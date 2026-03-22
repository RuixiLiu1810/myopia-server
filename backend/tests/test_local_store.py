from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from myopia_backend.storage.local_store import (
    build_object_key,
    resolve_local_object_path,
    write_local_object,
)


class LocalStoreTests(unittest.TestCase):
    def test_build_object_key_keeps_extension(self) -> None:
        key = build_object_key(".jpg")
        self.assertTrue(key.endswith(".jpg"))
        self.assertIn("/", key)

    def test_write_and_resolve_local_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key = "2026/03/19/sample.png"
            path = write_local_object(storage_dir=tmp, object_key=key, content=b"abc")
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), b"abc")

            resolved = resolve_local_object_path(storage_dir=tmp, object_key=key)
            self.assertEqual(resolved, path)

    def test_resolve_local_object_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                resolve_local_object_path(storage_dir=tmp, object_key="../escape.txt")


if __name__ == "__main__":
    unittest.main()

