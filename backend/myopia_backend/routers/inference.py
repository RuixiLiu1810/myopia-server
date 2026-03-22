from __future__ import annotations

import base64
import binascii
from pathlib import Path
import tempfile
import time
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..inference_service import predict_future
from ..schemas import PredictInlineRequest, PredictRequest, VisitIn


ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def _visit_to_dict(v: VisitIn) -> dict:
    if hasattr(v, "model_dump"):
        return v.model_dump()
    return v.dict()


def _resolve_model_dir(settings_model_dir: str, model_dir_override: str | None) -> str:
    value = (model_dir_override or settings_model_dir).strip()
    if not value:
        raise ValueError("model_dir cannot be empty")
    return value


def _resolve_device(settings_device: str | None, request_device: str | None) -> str | None:
    return request_device or settings_device


def _safe_ext(ext: str | None) -> str:
    if not ext:
        return ".jpg"
    value = ext.strip().lower()
    if not value.startswith("."):
        value = "." + value
    if len(value) > 10:
        raise ValueError("image_ext is too long.")
    if value not in ALLOWED_IMAGE_EXTS:
        raise ValueError(
            f"Unsupported image_ext={value}. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTS))}"
        )
    return value


def _decode_data_url_to_bytes(data: str) -> bytes:
    payload = data
    if "," in data and data.strip().lower().startswith("data:"):
        payload = data.split(",", 1)[1]
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64 image payload.") from exc


def _cleanup_files(paths: list[Path]) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass


def _validate_visits_count(visits_count: int, max_visits: int) -> None:
    if visits_count < 1:
        raise ValueError("At least one visit is required.")
    if visits_count > max_visits:
        raise ValueError(
            f"Too many visits: {visits_count}. Maximum supported visits is {max_visits}."
        )


def _validate_inline_payload_size(
    image_bytes: bytes,
    current_total: int,
    max_image_bytes: int,
    max_total_bytes: int,
) -> int:
    size = len(image_bytes)
    if size > max_image_bytes:
        raise ValueError(
            f"Inline image payload too large: {size} bytes. Max per image: {max_image_bytes} bytes."
        )
    new_total = current_total + size
    if new_total > max_total_bytes:
        raise ValueError(
            f"Total inline payload too large: {new_total} bytes. Max total: {max_total_bytes} bytes."
        )
    return new_total


def build_inference_router(settings) -> APIRouter:
    router = APIRouter()

    @router.get("/limits")
    def get_limits():
        """Return request guardrail settings for operational transparency."""
        return {
            "max_visits": settings.max_visits,
            "max_inline_image_bytes": settings.max_inline_image_bytes,
            "max_inline_total_bytes": settings.max_inline_total_bytes,
            "allowed_image_exts": sorted(ALLOWED_IMAGE_EXTS),
        }

    @router.post("/predict")
    def predict(req: PredictRequest, model_dir: Optional[str] = None):
        started = time.perf_counter()
        try:
            _validate_visits_count(len(req.visits), settings.max_visits)

            used_model_dir = _resolve_model_dir(settings.model_dir, model_dir)
            result = predict_future(
                visits=[_visit_to_dict(v) for v in req.visits],
                model_dir=used_model_dir,
                horizons=req.horizons,
                device=_resolve_device(settings.default_device, req.device),
                model_families=req.model_families,
                risk_threshold=float(req.risk_threshold if req.risk_threshold is not None else 0.5),
            )
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"unexpected inference error: {exc}") from exc

        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result

    @router.post("/predict-inline")
    def predict_inline(req: PredictInlineRequest, model_dir: Optional[str] = None):
        started = time.perf_counter()
        temp_paths: list[Path] = []
        total_inline_size = 0

        try:
            _validate_visits_count(len(req.visits), settings.max_visits)

            used_model_dir = _resolve_model_dir(settings.model_dir, model_dir)
            visits = []
            for visit in req.visits:
                suffix = _safe_ext(visit.image_ext)
                content = _decode_data_url_to_bytes(visit.image_b64)
                total_inline_size = _validate_inline_payload_size(
                    image_bytes=content,
                    current_total=total_inline_size,
                    max_image_bytes=settings.max_inline_image_bytes,
                    max_total_bytes=settings.max_inline_total_bytes,
                )
                with tempfile.NamedTemporaryFile(
                    prefix="myopia_visit_", suffix=suffix, delete=False
                ) as tmp:
                    tmp.write(content)
                    tmp_path = Path(tmp.name)

                temp_paths.append(tmp_path)
                visits.append({"image_path": str(tmp_path), "se": float(visit.se)})

            result = predict_future(
                visits=visits,
                model_dir=used_model_dir,
                horizons=req.horizons,
                device=_resolve_device(settings.default_device, req.device),
                model_families=req.model_families,
                risk_threshold=float(req.risk_threshold if req.risk_threshold is not None else 0.5),
            )
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"unexpected inference error: {exc}") from exc
        finally:
            _cleanup_files(temp_paths)

        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result

    return router
