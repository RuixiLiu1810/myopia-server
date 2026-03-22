from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from myopia_backend.db.base import Base
from myopia_backend.db.models import User
from myopia_backend.db.session import create_engine_from_url, session_scope
from myopia_backend.install_state import get_setup_status, write_install_marker
from myopia_backend.security.auth import hash_password


class _Settings:
    def __init__(self, *, database_url: str, install_marker_file: str) -> None:
        self.database_url = database_url
        self.install_marker_file = install_marker_file


class SetupStateTests(unittest.TestCase):
    def test_setup_required_when_no_admin_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "setup_no_admin.db"
            marker = Path(tmp) / ".installed.json"
            database_url = f"sqlite:///{db_path}"

            engine = create_engine_from_url(database_url=database_url)
            try:
                Base.metadata.create_all(bind=engine)
                status = get_setup_status(
                    _Settings(database_url=database_url, install_marker_file=str(marker))
                )
            finally:
                engine.dispose()

            self.assertTrue(status.db_ready)
            self.assertTrue(status.setup_required)
            self.assertEqual(status.admin_user_count, 0)
            self.assertIn("no_admin_user", status.reasons)

    def test_setup_not_required_after_admin_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "setup_has_admin.db"
            marker = Path(tmp) / ".installed.json"
            database_url = f"sqlite:///{db_path}"

            engine = create_engine_from_url(database_url=database_url)
            try:
                Base.metadata.create_all(bind=engine)

                with session_scope(database_url=database_url) as session:
                    session.add(
                        User(
                            username="admin",
                            display_name="System Admin",
                            role="admin",
                            is_active=True,
                            password_hash=hash_password("AdminPass123"),
                        )
                    )

                status = get_setup_status(
                    _Settings(database_url=database_url, install_marker_file=str(marker))
                )
            finally:
                engine.dispose()

            self.assertTrue(status.db_ready)
            self.assertFalse(status.setup_required)
            self.assertEqual(status.admin_user_count, 1)

    def test_write_install_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "nested" / ".myopia_installed"
            settings = _Settings(database_url="sqlite:////tmp/unused.db", install_marker_file=str(marker))
            out = write_install_marker(settings, admin_username="admin")
            self.assertEqual(out, marker.resolve())
            self.assertTrue(out.exists())
            self.assertIn("admin", out.read_text(encoding="utf-8"))

    def test_setup_required_when_db_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / ".installed.json"
            database_url = "postgresql+psycopg://invalid:invalid@127.0.0.1:9/invalid"
            status = get_setup_status(
                _Settings(database_url=database_url, install_marker_file=str(marker))
            )
            self.assertFalse(status.db_ready)
            self.assertTrue(status.setup_required)
            self.assertIn("database_unavailable_or_migrations_pending", status.reasons)


if __name__ == "__main__":
    unittest.main()
