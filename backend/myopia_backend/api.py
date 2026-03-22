from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .install_state import get_setup_status
from .model_store import list_available_model_assets
from .routers.auth import build_auth_router
from .routers.assets import build_assets_router
from .routers.clinical import build_clinical_router
from .routers.inference import build_inference_router
from .routers.ops import build_ops_router
from .routers.setup import build_setup_router
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
        if settings.setup_enabled and get_setup_status(settings).setup_required:
            # During first-time setup, allow backend startup even if models are not ready yet.
            return
        models = list_available_model_assets(settings.model_dir)
        if not models:
            raise RuntimeError(
                f"No models found in default model dir: {settings.model_dir}. "
                "Set MYOPIA_MODEL_DIR to a valid model directory."
            )

    @app.middleware("http")
    async def setup_gate(request: Request, call_next):
        if not settings.setup_enabled or not settings.setup_enforce_lock:
            return await call_next(request)

        path = request.url.path
        if request.method.upper() == "OPTIONS":
            return await call_next(request)
        if path.startswith("/healthz") or path.startswith("/v1/setup"):
            return await call_next(request)
        if path == "/" or path.startswith("/setup"):
            return await call_next(request)
        if path.startswith("/docs") or path.startswith("/redoc") or path.startswith("/openapi.json"):
            return await call_next(request)

        status = get_setup_status(settings)
        if status.setup_required:
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "server setup required; open /setup and initialize admin account",
                    "setup": status.to_dict(),
                },
            )
        return await call_next(request)

    app.include_router(build_setup_router(settings))
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
