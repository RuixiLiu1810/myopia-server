from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..db.models import FileAsset
from ..db.session import session_scope
from ..dependencies.rbac import require_roles
from ..inference_service import predict_future
from ..schemas import PredictByAssetRequest, UploadInlineFileRequest
from ..services.file_asset_service import create_file_asset, resolve_asset_local_path
from .inference import (
    _decode_data_url_to_bytes,
    _resolve_device,
    _resolve_model_dir,
    _safe_ext,
    _validate_inline_payload_size,
    _validate_visits_count,
)


def build_assets_router(
    settings,
    *,
    prefix: str = "/v1",
    required_roles: tuple[str, ...] | None = None,
) -> APIRouter:
    dependencies = [Depends(require_roles(*required_roles))] if required_roles else None
    router = APIRouter(prefix=prefix, tags=["assets"], dependencies=dependencies)

    @router.post("/files/upload-inline")
    def upload_inline_file(req: UploadInlineFileRequest):
        try:
            ext = _safe_ext(req.image_ext)
            content = _decode_data_url_to_bytes(req.image_b64)
            _validate_inline_payload_size(
                image_bytes=content,
                current_total=0,
                max_image_bytes=settings.max_inline_image_bytes,
                max_total_bytes=settings.max_inline_total_bytes,
            )
            with session_scope() as session:
                asset = create_file_asset(
                    session=session,
                    storage_backend=settings.storage_backend,
                    local_storage_dir=settings.local_storage_dir,
                    content=content,
                    ext=ext,
                    original_filename=req.original_filename,
                    content_type=req.content_type,
                    metadata_json=req.metadata,
                )
                return {
                    "file_asset_id": int(asset.id),
                    "storage_backend": asset.storage_backend,
                    "object_key": asset.object_key,
                    "size_bytes": asset.size_bytes,
                    "sha256": asset.sha256,
                }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"upload failed: {exc}") from exc

    @router.get("/files/{file_asset_id}")
    def get_file_asset(file_asset_id: int):
        try:
            with session_scope() as session:
                asset = session.get(FileAsset, int(file_asset_id))
                if asset is None:
                    raise HTTPException(status_code=404, detail=f"file_asset_id not found: {file_asset_id}")
                return {
                    "id": int(asset.id),
                    "storage_backend": asset.storage_backend,
                    "object_key": asset.object_key,
                    "original_filename": asset.original_filename,
                    "content_type": asset.content_type,
                    "size_bytes": asset.size_bytes,
                    "sha256": asset.sha256,
                    "uploaded_at": asset.uploaded_at.isoformat(),
                    "metadata": asset.metadata_json,
                }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"query asset failed: {exc}") from exc

    @router.get("/files/{file_asset_id}/content")
    def get_file_asset_content(file_asset_id: int):
        try:
            with session_scope() as session:
                asset = session.get(FileAsset, int(file_asset_id))
                if asset is None:
                    raise HTTPException(status_code=404, detail=f"file_asset_id not found: {file_asset_id}")
                path = resolve_asset_local_path(storage_dir=settings.local_storage_dir, asset=asset)
                if not path.exists():
                    raise HTTPException(status_code=404, detail=f"asset file not found on storage: {path}")
                media_type = (
                    (asset.content_type or "").strip()
                    or mimetypes.guess_type(asset.original_filename or path.name)[0]
                    or "application/octet-stream"
                )
                filename = (asset.original_filename or path.name).strip() or path.name
                return FileResponse(
                    str(path),
                    media_type=media_type,
                    filename=filename,
                    content_disposition_type="inline",
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"read asset content failed: {exc}") from exc

    # Compatibility endpoint:
    # Keep this API for external tools and transition period scripts.
    # Doctor UI should use patient/encounter-based workflow:
    # `POST /v1/clinical/predictions/by-encounters`.
    @router.post("/predict-assets")
    def predict_from_assets(req: PredictByAssetRequest, model_dir: str | None = None):
        try:
            _validate_visits_count(len(req.visits), settings.max_visits)
            used_model_dir = _resolve_model_dir(settings.model_dir, model_dir)
            prepared_visits: list[dict] = []
            visit_asset_ids: list[int] = []

            with session_scope() as session:
                for visit in req.visits:
                    file_asset_id = int(visit.file_asset_id)
                    asset = session.get(FileAsset, file_asset_id)
                    if asset is None:
                        raise ValueError(f"file_asset_id not found: {file_asset_id}")
                    path = resolve_asset_local_path(storage_dir=settings.local_storage_dir, asset=asset)
                    if not path.exists():
                        raise FileNotFoundError(f"asset file not found on storage: {path}")
                    prepared_visits.append({"image_path": str(path), "se": float(visit.se)})
                    visit_asset_ids.append(file_asset_id)

            result = predict_future(
                visits=prepared_visits,
                model_dir=used_model_dir,
                horizons=req.horizons,
                device=_resolve_device(settings.default_device, req.device),
                model_families=req.model_families,
                risk_threshold=float(req.risk_threshold if req.risk_threshold is not None else 0.5),
            )
            result["file_asset_ids"] = visit_asset_ids
            return result
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"predict-from-assets failed: {exc}") from exc

    return router
