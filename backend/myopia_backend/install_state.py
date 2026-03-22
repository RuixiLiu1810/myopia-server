from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from .db.models import User
from .db.session import session_scope


@dataclass(frozen=True)
class SetupStatus:
    setup_required: bool
    db_ready: bool
    admin_user_count: int
    marker_exists: bool
    marker_file: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_required": bool(self.setup_required),
            "db_ready": bool(self.db_ready),
            "admin_user_count": int(self.admin_user_count),
            "marker_exists": bool(self.marker_exists),
            "marker_file": self.marker_file,
            "reasons": list(self.reasons),
        }


def _resolve_marker_path(raw_path: str) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        raise ValueError("install_marker_file cannot be empty")
    return Path(value).expanduser().resolve()


def get_setup_status(settings) -> SetupStatus:
    marker_path = _resolve_marker_path(settings.install_marker_file)
    marker_exists = marker_path.exists()

    try:
        with session_scope(database_url=settings.database_url) as session:
            admin_user_count = int(
                session.execute(
                    select(func.count())
                    .select_from(User)
                    .where(User.role == "admin", User.is_active.is_(True))
                ).scalar_one()
            )
    except Exception as exc:
        return SetupStatus(
            setup_required=True,
            db_ready=False,
            admin_user_count=0,
            marker_exists=marker_exists,
            marker_file=str(marker_path),
            reasons=("database_unavailable_or_migrations_pending", exc.__class__.__name__),
        )

    reasons: list[str] = []
    if admin_user_count <= 0:
        reasons.append("no_admin_user")

    return SetupStatus(
        setup_required=admin_user_count <= 0,
        db_ready=True,
        admin_user_count=admin_user_count,
        marker_exists=marker_exists,
        marker_file=str(marker_path),
        reasons=tuple(reasons),
    )


def write_install_marker(settings, *, admin_username: str) -> Path:
    marker_path = _resolve_marker_path(settings.install_marker_file)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "installed_at": datetime.now(tz=timezone.utc).isoformat(),
        "admin_username": str(admin_username).strip().lower(),
        "app": "myopia-server",
        "version": "1.0.0",
    }
    marker_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return marker_path
