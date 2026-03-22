from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from ..db.models import FileAsset
from ..storage.local_store import build_object_key, resolve_local_object_path, write_local_object


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def create_file_asset(
    *,
    session: Session,
    storage_backend: str,
    local_storage_dir: str,
    content: bytes,
    ext: str,
    original_filename: str | None = None,
    content_type: str | None = None,
    metadata_json: dict | None = None,
) -> FileAsset:
    if storage_backend != "local":
        raise ValueError(f"Unsupported storage backend for now: {storage_backend}")

    object_key = build_object_key(ext)
    write_local_object(storage_dir=local_storage_dir, object_key=object_key, content=content)

    asset = FileAsset(
        storage_backend=storage_backend,
        object_key=object_key,
        original_filename=original_filename,
        content_type=content_type,
        size_bytes=len(content),
        sha256=sha256_hex(content),
        metadata_json=metadata_json,
    )
    session.add(asset)
    session.flush()
    return asset


def resolve_asset_local_path(*, storage_dir: str, asset: FileAsset) -> Path:
    if asset.storage_backend != "local":
        raise ValueError(f"Unsupported asset storage backend for inference: {asset.storage_backend}")
    return resolve_local_object_path(storage_dir=storage_dir, object_key=asset.object_key)

