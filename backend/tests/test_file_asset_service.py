from __future__ import annotations

import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from myopia_backend.db.base import Base
from myopia_backend.db.models import FileAsset
from myopia_backend.services.file_asset_service import create_file_asset, resolve_asset_local_path


class FileAssetServiceTests(unittest.TestCase):
    def test_create_file_asset_writes_file_and_row(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

        with tempfile.TemporaryDirectory() as tmp:
            with Session() as session:
                asset = create_file_asset(
                    session=session,
                    storage_backend="local",
                    local_storage_dir=tmp,
                    content=b"hello",
                    ext=".jpg",
                    original_filename="a.jpg",
                    content_type="image/jpeg",
                    metadata_json={"source": "unit-test"},
                )
                session.commit()

                self.assertIsNotNone(asset.id)
                self.assertEqual(asset.size_bytes, 5)
                self.assertEqual(asset.content_type, "image/jpeg")

                fetched = session.get(FileAsset, int(asset.id))
                self.assertIsNotNone(fetched)
                assert fetched is not None
                path = resolve_asset_local_path(storage_dir=tmp, asset=fetched)
                self.assertTrue(path.exists())
                self.assertEqual(path.read_bytes(), b"hello")


if __name__ == "__main__":
    unittest.main()

