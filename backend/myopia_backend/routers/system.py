from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from ..inference_service import routing_rules
from ..install_state import get_setup_status
from ..model_store import list_available_model_assets, list_available_models


def _resolve_model_dir(settings_model_dir: str, model_dir_override: str | None) -> str:
    value = (model_dir_override or settings_model_dir).strip()
    if not value:
        raise ValueError("model_dir cannot be empty")
    return value


def build_system_router(settings) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    def healthz(model_dir: Optional[str] = None):
        setup = get_setup_status(settings).to_dict()
        used_model_dir = _resolve_model_dir(settings.model_dir, model_dir)

        try:
            model_assets = list_available_model_assets(used_model_dir)
        except Exception as exc:
            if setup.get("setup_required"):
                # During installation mode, allow health checks even if model assets are not ready yet.
                return {
                    "status": "setup_required",
                    "model_dir": used_model_dir,
                    "model_count": 0,
                    "model_error": str(exc),
                    "default_model_dir": settings.model_dir,
                    "default_device": settings.default_device,
                    "storage_backend": settings.storage_backend,
                    "local_storage_dir": settings.local_storage_dir,
                    "allowed_origins": settings.allowed_origins,
                    "setup": setup,
                    "limits": {
                        "max_visits": settings.max_visits,
                        "max_inline_image_bytes": settings.max_inline_image_bytes,
                        "max_inline_total_bytes": settings.max_inline_total_bytes,
                    },
                }
            raise HTTPException(status_code=503, detail=f"health check failed: {exc}") from exc

        return {
            "status": "ok",
            "model_dir": used_model_dir,
            "model_count": len(model_assets),
            "default_model_dir": settings.model_dir,
            "default_device": settings.default_device,
            "storage_backend": settings.storage_backend,
            "local_storage_dir": settings.local_storage_dir,
            "allowed_origins": settings.allowed_origins,
            "setup": setup,
            "limits": {
                "max_visits": settings.max_visits,
                "max_inline_image_bytes": settings.max_inline_image_bytes,
                "max_inline_total_bytes": settings.max_inline_total_bytes,
            },
        }

    @router.get("/model-info")
    def model_info(model_dir: Optional[str] = None):
        try:
            used_model_dir = _resolve_model_dir(settings.model_dir, model_dir)
            models = list_available_models(used_model_dir)
            assets = list_available_model_assets(used_model_dir)
            grouped: dict[str, list[dict]] = {}
            for (seq_len, horizon), path in sorted(models.items()):
                grouped.setdefault(str(seq_len), []).append(
                    {"horizon": horizon, "file": path.name}
                )
            family_groups: dict[str, dict[str, list[dict]]] = {}
            for (family, seq_len, horizon), path in sorted(assets.items()):
                family_groups.setdefault(family, {}).setdefault(str(seq_len), []).append(
                    {"horizon": int(horizon), "file": path.name}
                )
            return {
                "model_dir": used_model_dir,
                "groups": grouped,
                "family_groups": family_groups,
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to read model info: {exc}") from exc

    @router.get("/routing-rules")
    def get_routing_rules():
        """Return deterministic route rules for explainability."""
        return {
            "description": "seq_len -> supported horizons",
            "rules": routing_rules(max_seq_len=5),
        }

    return router
