from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .model_store import list_available_model_assets
from .routers.auth import build_auth_router
from .routers.assets import build_assets_router
from .routers.clinical import build_clinical_router
from .routers.inference import build_inference_router
from .routers.ops import build_ops_router
from .routers.system import build_system_router


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title="Myopia Backend API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def startup_check() -> None:
        if settings.skip_startup_check:
            return
        models = list_available_model_assets(settings.model_dir)
        if not models:
            raise RuntimeError(
                f"No models found in default model dir: {settings.model_dir}. "
                "Set MYOPIA_MODEL_DIR to a valid model directory."
            )

    app.include_router(build_system_router(settings))
    app.include_router(build_inference_router(settings))
    app.include_router(build_auth_router(settings))
    app.include_router(build_ops_router(settings))

    # Legacy compatibility routes (public /v1 clinical & assets) are disabled by default.
    # Enable only for local transition with:
    # MYOPIA_ENABLE_LEGACY_PUBLIC_CLINICAL_ROUTES=1
    if settings.enable_legacy_public_clinical_routes:
        app.include_router(build_assets_router(settings))
        app.include_router(build_clinical_router(settings))
    app.include_router(
        build_assets_router(
            settings,
            prefix="/v1/clinical",
            required_roles=("doctor", "operator", "admin"),
        )
    )
    app.include_router(
        build_clinical_router(
            settings,
            prefix="/v1/clinical",
            required_roles=("doctor", "operator", "admin"),
        )
    )
    return app


app = create_app()
