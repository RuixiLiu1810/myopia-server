#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import sys

import uvicorn


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _env_optional(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _resolve_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    cwd_resolved = (Path.cwd() / path).resolve()
    if cwd_resolved.exists():
        return str(cwd_resolved)
    return str((BACKEND_DIR / path).resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run standalone myopia backend service.")
    parser.add_argument("--host", default=_env_optional("MYOPIA_API_HOST") or "0.0.0.0")
    parser.add_argument("--port", type=int, default=int(_env_optional("MYOPIA_API_PORT") or "8000"))
    parser.add_argument(
        "--model-dir",
        default=_env_optional("MYOPIA_MODEL_DIR") or "",
        help="Model directory (exported to MYOPIA_MODEL_DIR).",
    )
    parser.add_argument(
        "--database-url",
        default=_env_optional("MYOPIA_DATABASE_URL"),
        help='Database URL, e.g. "postgresql+psycopg://user:pass@host:5432/dbname".',
    )
    parser.add_argument(
        "--default-device",
        default=_env_optional("MYOPIA_DEFAULT_DEVICE"),
        help='Default inference device, e.g. "cpu".',
    )
    parser.add_argument(
        "--storage-backend",
        default=_env_optional("MYOPIA_STORAGE_BACKEND") or "local",
        help='Storage backend, e.g. "local" or "minio".',
    )
    parser.add_argument(
        "--local-storage-dir",
        default=_env_optional("MYOPIA_LOCAL_STORAGE_DIR") or "../storage",
        help="Storage directory for local backend.",
    )
    parser.add_argument(
        "--allowed-origins",
        default=_env_optional("MYOPIA_ALLOWED_ORIGINS") or "*",
        help='CORS origins separated by comma, e.g. "http://127.0.0.1:5173,http://localhost:5173"',
    )
    parser.add_argument(
        "--max-visits",
        type=int,
        default=int(_env_optional("MYOPIA_MAX_VISITS") or "5"),
        help="Maximum visits per request.",
    )
    parser.add_argument(
        "--max-inline-image-bytes",
        type=int,
        default=int(_env_optional("MYOPIA_MAX_INLINE_IMAGE_BYTES") or str(8 * 1024 * 1024)),
        help="Maximum inline payload bytes per image.",
    )
    parser.add_argument(
        "--max-inline-total-bytes",
        type=int,
        default=int(_env_optional("MYOPIA_MAX_INLINE_TOTAL_BYTES") or str(32 * 1024 * 1024)),
        help="Maximum inline payload bytes across one request.",
    )
    parser.add_argument(
        "--setup-enabled",
        choices=["0", "1"],
        default=_env_optional("MYOPIA_SETUP_ENABLED") or "1",
        help="Enable first-run setup endpoints and status checks.",
    )
    parser.add_argument(
        "--setup-enforce-lock",
        choices=["0", "1"],
        default=_env_optional("MYOPIA_SETUP_ENFORCE_LOCK") or "1",
        help="When setup is required, lock non-setup endpoints.",
    )
    parser.add_argument(
        "--install-marker-file",
        default=_env_optional("MYOPIA_INSTALL_MARKER_FILE") or "",
        help="Optional install marker file path.",
    )
    parser.add_argument("--skip-startup-check", action="store_true")
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    if args.model_dir:
        os.environ["MYOPIA_MODEL_DIR"] = _resolve_path(args.model_dir)
    else:
        os.environ.pop("MYOPIA_MODEL_DIR", None)

    if args.database_url:
        os.environ["MYOPIA_DATABASE_URL"] = args.database_url
    else:
        os.environ.pop("MYOPIA_DATABASE_URL", None)

    if args.default_device:
        os.environ["MYOPIA_DEFAULT_DEVICE"] = args.default_device
    else:
        os.environ.pop("MYOPIA_DEFAULT_DEVICE", None)

    os.environ["MYOPIA_STORAGE_BACKEND"] = args.storage_backend
    os.environ["MYOPIA_LOCAL_STORAGE_DIR"] = _resolve_path(args.local_storage_dir)
    os.environ["MYOPIA_ALLOWED_ORIGINS"] = args.allowed_origins
    os.environ["MYOPIA_SKIP_STARTUP_CHECK"] = "1" if args.skip_startup_check else "0"
    os.environ["MYOPIA_MAX_VISITS"] = str(args.max_visits)
    os.environ["MYOPIA_MAX_INLINE_IMAGE_BYTES"] = str(args.max_inline_image_bytes)
    os.environ["MYOPIA_MAX_INLINE_TOTAL_BYTES"] = str(args.max_inline_total_bytes)
    os.environ["MYOPIA_SETUP_ENABLED"] = args.setup_enabled
    os.environ["MYOPIA_SETUP_ENFORCE_LOCK"] = args.setup_enforce_lock

    if args.install_marker_file.strip():
        os.environ["MYOPIA_INSTALL_MARKER_FILE"] = _resolve_path(args.install_marker_file)
    else:
        os.environ.pop("MYOPIA_INSTALL_MARKER_FILE", None)

    try:
        importlib.import_module("myopia_backend.api")
    except Exception as exc:
        raise RuntimeError(
            f'Failed to import "myopia_backend.api" from BACKEND_DIR={BACKEND_DIR}'
        ) from exc

    print(f"[run] host={args.host} port={args.port}")
    print(f"[run] MYOPIA_MODEL_DIR={os.environ.get('MYOPIA_MODEL_DIR', '<default>')}")
    print(f"[run] MYOPIA_DATABASE_URL={os.environ.get('MYOPIA_DATABASE_URL', '<default>')}")
    print(f"[run] MYOPIA_DEFAULT_DEVICE={os.environ.get('MYOPIA_DEFAULT_DEVICE', '<none>')}")
    print(f"[run] MYOPIA_STORAGE_BACKEND={os.environ['MYOPIA_STORAGE_BACKEND']}")
    print(f"[run] MYOPIA_LOCAL_STORAGE_DIR={os.environ['MYOPIA_LOCAL_STORAGE_DIR']}")
    print(f"[run] MYOPIA_ALLOWED_ORIGINS={os.environ['MYOPIA_ALLOWED_ORIGINS']}")
    print(f"[run] MYOPIA_SKIP_STARTUP_CHECK={os.environ['MYOPIA_SKIP_STARTUP_CHECK']}")
    print(f"[run] MYOPIA_MAX_VISITS={os.environ['MYOPIA_MAX_VISITS']}")
    print(f"[run] MYOPIA_MAX_INLINE_IMAGE_BYTES={os.environ['MYOPIA_MAX_INLINE_IMAGE_BYTES']}")
    print(f"[run] MYOPIA_MAX_INLINE_TOTAL_BYTES={os.environ['MYOPIA_MAX_INLINE_TOTAL_BYTES']}")
    print(f"[run] MYOPIA_SETUP_ENABLED={os.environ['MYOPIA_SETUP_ENABLED']}")
    print(f"[run] MYOPIA_SETUP_ENFORCE_LOCK={os.environ['MYOPIA_SETUP_ENFORCE_LOCK']}")
    print(
        f"[run] MYOPIA_INSTALL_MARKER_FILE={os.environ.get('MYOPIA_INSTALL_MARKER_FILE', '<default>')}"
    )

    uvicorn.run(
        "myopia_backend.api:app",
        host=args.host,
        port=args.port,
        app_dir=str(BACKEND_DIR),
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
