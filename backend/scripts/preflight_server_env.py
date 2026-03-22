#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from urllib.parse import urlparse


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"env file not found: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def validate_env(cfg: dict[str, str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    required = [
        "MYOPIA_DATABASE_URL",
        "MYOPIA_MODEL_DIR",
        "MYOPIA_ALLOWED_ORIGINS",
        "MYOPIA_AUTH_SECRET",
    ]
    for key in required:
        if not str(cfg.get(key, "")).strip():
            errors.append(f"missing required key: {key}")

    db_url = str(cfg.get("MYOPIA_DATABASE_URL", "")).strip()
    if db_url:
        parsed = urlparse(db_url)
        if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
            errors.append(
                "MYOPIA_DATABASE_URL must be postgresql://... or postgresql+psycopg://..."
            )
        if not parsed.hostname:
            errors.append("MYOPIA_DATABASE_URL missing hostname")
        if not parsed.path or parsed.path == "/":
            errors.append("MYOPIA_DATABASE_URL missing database name")

    model_dir = str(cfg.get("MYOPIA_MODEL_DIR", "")).strip()
    if model_dir and not model_dir.startswith("/"):
        warnings.append("MYOPIA_MODEL_DIR is not absolute path (recommended absolute in production)")

    allowed_origins = str(cfg.get("MYOPIA_ALLOWED_ORIGINS", "")).strip()
    if allowed_origins:
        origins = [x.strip() for x in allowed_origins.split(",") if x.strip()]
        if not origins:
            errors.append("MYOPIA_ALLOWED_ORIGINS is empty")
        if "*" in origins:
            errors.append("MYOPIA_ALLOWED_ORIGINS must not contain '*' in production")
        for origin in origins:
            if not re.match(r"^https?://", origin):
                warnings.append(f"origin may be invalid: {origin}")

    secret = str(cfg.get("MYOPIA_AUTH_SECRET", "")).strip()
    if secret:
        lowered = secret.lower()
        if len(secret) < 24:
            errors.append("MYOPIA_AUTH_SECRET too short (<24)")
        if "change-me" in lowered or "replace" in lowered or "default" in lowered:
            errors.append("MYOPIA_AUTH_SECRET looks like placeholder")

    if is_truthy(cfg.get("MYOPIA_ENABLE_LEGACY_PUBLIC_CLINICAL_ROUTES", "0")):
        errors.append("MYOPIA_ENABLE_LEGACY_PUBLIC_CLINICAL_ROUTES must be 0 in production")

    if not is_truthy(cfg.get("MYOPIA_SETUP_ENABLED", "1")):
        warnings.append("MYOPIA_SETUP_ENABLED=0 (setup wizard disabled)")

    if not is_truthy(cfg.get("MYOPIA_SETUP_ENFORCE_LOCK", "1")):
        warnings.append("MYOPIA_SETUP_ENFORCE_LOCK=0 (setup lock disabled)")

    return errors, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight validator for myopia-server production env.")
    parser.add_argument(
        "--env-file",
        default="/etc/myopia/server.env",
        help="Path to server env file (default: /etc/myopia/server.env)",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file).expanduser().resolve()
    try:
        cfg = parse_env_file(env_path)
    except Exception as exc:
        print(f"[fail] unable to read env file: {exc}")
        sys.exit(2)

    errors, warnings = validate_env(cfg)

    print(f"[info] env_file={env_path}")
    print(f"[info] keys={len(cfg)}")

    if warnings:
        for w in warnings:
            print(f"[warn] {w}")

    if errors:
        for e in errors:
            print(f"[error] {e}")
        print(f"[fail] preflight failed: {len(errors)} error(s)")
        sys.exit(1)

    print("[ok] preflight passed")


if __name__ == "__main__":
    main()
