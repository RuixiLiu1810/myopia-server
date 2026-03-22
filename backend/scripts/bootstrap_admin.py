#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from sqlalchemy import select


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from myopia_backend.db.models import AuditLog, User  # noqa: E402
from myopia_backend.db.session import session_scope  # noqa: E402
from myopia_backend.security.auth import hash_password  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap initial admin user.")
    parser.add_argument("--username", required=True, help="Admin username")
    parser.add_argument("--password", required=True, help="Admin password (>=8 chars)")
    parser.add_argument("--display-name", default="System Admin", help="Optional admin display name")
    parser.add_argument(
        "--database-url",
        default=None,
        help='Optional DB URL override, e.g. "postgresql+psycopg://user:pass@host:5432/dbname".',
    )
    parser.add_argument(
        "--reset-password-if-exists",
        action="store_true",
        help="Reset password if user already exists.",
    )
    parser.add_argument(
        "--activate-if-exists",
        action="store_true",
        help="Activate user if already exists and inactive.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    username = str(args.username or "").strip().lower()
    if not username:
        raise ValueError("username cannot be empty")

    password_hash = hash_password(args.password)
    display_name = (args.display_name or "").strip() or None

    with session_scope(database_url=args.database_url) as session:
        existing = session.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if existing is None:
            user = User(
                username=username,
                display_name=display_name,
                role="admin",
                is_active=True,
                password_hash=password_hash,
            )
            session.add(user)
            session.flush()
            session.add(
                AuditLog(
                    action="bootstrap.admin.create",
                    actor="bootstrap-script",
                    target_type="user",
                    target_id=str(user.id),
                    detail_json={"username": user.username, "role": user.role},
                )
            )
            print(f"[ok] created admin user: username={user.username} id={user.id}")
            return

        before = {
            "role": existing.role,
            "is_active": bool(existing.is_active),
            "display_name": existing.display_name,
        }
        changed = False
        existing.role = "admin"
        changed = changed or before["role"] != "admin"

        if display_name is not None and existing.display_name != display_name:
            existing.display_name = display_name
            changed = True
        if args.activate_if_exists and not bool(existing.is_active):
            existing.is_active = True
            changed = True
        if args.reset_password_if_exists:
            existing.password_hash = password_hash
            changed = True

        if changed:
            session.flush()
            session.add(
                AuditLog(
                    action="bootstrap.admin.update",
                    actor="bootstrap-script",
                    target_type="user",
                    target_id=str(existing.id),
                    detail_json={
                        "username": existing.username,
                        "before": before,
                        "after": {
                            "role": existing.role,
                            "is_active": bool(existing.is_active),
                            "display_name": existing.display_name,
                            "password_reset": bool(args.reset_password_if_exists),
                        },
                    },
                )
            )
            print(f"[ok] updated existing user as admin: username={existing.username} id={existing.id}")
            return

        print(f"[ok] admin user already ready: username={existing.username} id={existing.id}")


if __name__ == "__main__":
    main()
