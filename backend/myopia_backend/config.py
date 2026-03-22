"""Runtime config loader for standalone backend."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PACKAGE_DIR.parent
APP_DIR = BACKEND_DIR.parent
LEGACY_ROOT_DIR = APP_DIR.parent


def _env_optional(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env_optional(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    value = _env_optional(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be int, got: {value}") from exc
    if parsed < 1:
        raise ValueError(f"Environment variable {name} must be >=1, got: {parsed}")
    return parsed


def _parse_allowed_origins(value: str | None) -> list[str]:
    if value is None:
        return ["*"]
    parts = [x.strip() for x in value.split(",") if x.strip()]
    return parts or ["*"]


def _default_model_dir() -> str:
    """Pick a sensible default model directory.

    Strategy:
    1) prefer a directory that actually contains Xu/Fen assets
    2) otherwise pick first existing candidate
    3) otherwise return app-local models path for explicit setup
    """
    candidates = [
        APP_DIR / "models",
        LEGACY_ROOT_DIR / "artifacts" / "models",
    ]

    def has_assets(path: Path) -> bool:
        return (
            any(path.glob("Xu*b_state_dict.pt"))
            or any(path.glob("Xu*b.pth"))
            or any(path.glob("Fen*b_state_dict.pt"))
            or any(path.glob("Fen*b.pth"))
            or any(path.glob("FenG*b_state_dict.pt"))
            or any(path.glob("FenG*b.pth"))
        )

    for path in candidates:
        if path.exists() and has_assets(path):
            return str(path)

    for path in candidates:
        if path.exists():
            return str(path)
    return str(candidates[0])


def _default_local_storage_dir() -> str:
    return str((APP_DIR / "storage").resolve())


def _default_install_marker_file() -> str:
    return str((APP_DIR / ".myopia_installed").resolve())


@dataclass(frozen=True)
class Settings:
    """Immutable backend settings resolved from environment."""

    database_url: str
    storage_backend: str
    local_storage_dir: str
    model_dir: str
    default_device: str | None
    skip_startup_check: bool
    allowed_origins: list[str]
    max_visits: int
    max_inline_image_bytes: int
    max_inline_total_bytes: int
    auth_secret: str
    auth_token_ttl_minutes: int
    enable_legacy_public_clinical_routes: bool
    setup_enabled: bool
    setup_enforce_lock: bool
    install_marker_file: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    storage_backend = (_env_optional("MYOPIA_STORAGE_BACKEND") or "local").strip().lower()
    if storage_backend not in {"local", "minio"}:
        raise ValueError(
            f"Environment variable MYOPIA_STORAGE_BACKEND must be one of "
            f"['local', 'minio'], got: {storage_backend}"
        )

    return Settings(
        database_url=_env_optional("MYOPIA_DATABASE_URL")
        or "postgresql+psycopg://myopia:myopia@127.0.0.1:5432/myopia",
        storage_backend=storage_backend,
        local_storage_dir=_env_optional("MYOPIA_LOCAL_STORAGE_DIR") or _default_local_storage_dir(),
        model_dir=_env_optional("MYOPIA_MODEL_DIR") or _default_model_dir(),
        default_device=_env_optional("MYOPIA_DEFAULT_DEVICE"),
        skip_startup_check=_env_bool("MYOPIA_SKIP_STARTUP_CHECK", default=False),
        allowed_origins=_parse_allowed_origins(_env_optional("MYOPIA_ALLOWED_ORIGINS")),
        max_visits=_env_int("MYOPIA_MAX_VISITS", default=5),
        max_inline_image_bytes=_env_int("MYOPIA_MAX_INLINE_IMAGE_BYTES", default=8 * 1024 * 1024),
        max_inline_total_bytes=_env_int("MYOPIA_MAX_INLINE_TOTAL_BYTES", default=32 * 1024 * 1024),
        auth_secret=_env_optional("MYOPIA_AUTH_SECRET") or "change-me-in-production",
        auth_token_ttl_minutes=_env_int("MYOPIA_AUTH_TOKEN_TTL_MINUTES", default=480),
        enable_legacy_public_clinical_routes=_env_bool(
            "MYOPIA_ENABLE_LEGACY_PUBLIC_CLINICAL_ROUTES", default=False
        ),
        setup_enabled=_env_bool("MYOPIA_SETUP_ENABLED", default=True),
        setup_enforce_lock=_env_bool("MYOPIA_SETUP_ENFORCE_LOCK", default=True),
        install_marker_file=_env_optional("MYOPIA_INSTALL_MARKER_FILE")
        or _default_install_marker_file(),
    )
