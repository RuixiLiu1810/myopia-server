from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def ensure_local_storage_dir(storage_dir: str | Path) -> Path:
    root = Path(storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_object_key(ext: str) -> str:
    now = datetime.now(timezone.utc)
    return f"{now:%Y/%m/%d}/{uuid4().hex}{ext}"


def resolve_local_object_path(storage_dir: str | Path, object_key: str) -> Path:
    root = ensure_local_storage_dir(storage_dir)
    path = (root / object_key).resolve()
    if not str(path).startswith(str(root)):
        raise ValueError("Invalid object_key path traversal.")
    return path


def write_local_object(storage_dir: str | Path, object_key: str, content: bytes) -> Path:
    path = resolve_local_object_path(storage_dir=storage_dir, object_key=object_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path

