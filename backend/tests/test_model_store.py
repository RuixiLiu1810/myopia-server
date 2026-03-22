from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from myopia_backend.model_store import (
    _parse_model_asset_key_from_name,
    _parse_model_key_from_name,
    list_available_model_assets,
    list_available_models,
)


class ModelStoreCoreTests(unittest.TestCase):
    def test_parse_model_asset_key_from_name(self) -> None:
        self.assertEqual(_parse_model_asset_key_from_name("Xu11b.pth"), ("xu", 1, 1))
        self.assertEqual(_parse_model_asset_key_from_name("Fen24b_state_dict.pt"), ("fen", 2, 4))
        self.assertEqual(_parse_model_asset_key_from_name("FenG33b.pth"), ("feng", 3, 3))
        self.assertIsNone(_parse_model_asset_key_from_name("bad_name.pt"))

    def test_parse_model_key_from_name(self) -> None:
        self.assertEqual(_parse_model_key_from_name("Xu11b.pth"), (1, 1))
        self.assertEqual(_parse_model_key_from_name("Xu24b_state_dict.pt"), (2, 4))
        self.assertIsNone(_parse_model_key_from_name("Fen11b_state_dict.pt"))
        self.assertIsNone(_parse_model_key_from_name("Xu66b.pth"))
        self.assertIsNone(_parse_model_key_from_name("not_a_model.pt"))

    def test_list_available_models_prioritizes_state_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Xu11b.pth").touch()
            (root / "Xu11b_state_dict.pt").touch()
            (root / "Xu12b.pth").touch()

            models = list_available_models(root)
            self.assertEqual(models[(1, 1)].name, "Xu11b_state_dict.pt")
            self.assertEqual(models[(1, 2)].name, "Xu12b.pth")

    def test_list_available_model_assets_for_families(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Xu11b_state_dict.pt").touch()
            (root / "Fen11b.pth").touch()
            (root / "FenG11b_state_dict.pt").touch()

            models = list_available_model_assets(root)
            self.assertIn(("xu", 1, 1), models)
            self.assertIn(("fen", 1, 1), models)
            self.assertIn(("feng", 1, 1), models)

    def test_list_available_models_missing_dir_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            list_available_models("/tmp/path/not/exist/myopia_models")


if __name__ == "__main__":
    unittest.main()
