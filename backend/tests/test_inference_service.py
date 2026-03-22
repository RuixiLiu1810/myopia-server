from __future__ import annotations

import unittest

from myopia_backend.inference_service import (
    Visit,
    normalize_model_families,
    normalize_visits,
    resolve_horizons,
    routing_rules,
)


class InferenceServiceCoreTests(unittest.TestCase):
    def test_normalize_visits_accepts_mixed_inputs(self) -> None:
        visits = normalize_visits(
            [
                {"image_path": "a.jpg", "se": 0.25},
                Visit(image_path="b.jpg", se=-0.5),
            ]
        )
        self.assertEqual(len(visits), 2)
        self.assertEqual(visits[0].image_path, "a.jpg")
        self.assertAlmostEqual(visits[1].se, -0.5)

    def test_normalize_visits_rejects_missing_keys(self) -> None:
        with self.assertRaises(ValueError):
            normalize_visits([{"image_path": "a.jpg"}])  # type: ignore[list-item]

    def test_normalize_visits_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            normalize_visits([])

    def test_normalize_model_families_default_xu(self) -> None:
        self.assertEqual(normalize_model_families(None), ["xu"])

    def test_normalize_model_families_alias_and_dedup(self) -> None:
        self.assertEqual(
            normalize_model_families(["Xu", "myopia_risk", "fen_g", "xu"]),
            ["xu", "fen", "feng"],
        )

    def test_normalize_model_families_rejects_invalid(self) -> None:
        with self.assertRaises(ValueError):
            normalize_model_families(["abc"])

    def test_resolve_horizons_default_by_seq_len(self) -> None:
        self.assertEqual(resolve_horizons(seq_len=3), [1, 2, 3])

    def test_resolve_horizons_deduplicates_and_sorts(self) -> None:
        self.assertEqual(resolve_horizons(seq_len=3, requested_horizons=[3, 1, 3]), [1, 3])

    def test_resolve_horizons_rejects_invalid_horizon(self) -> None:
        with self.assertRaises(ValueError):
            resolve_horizons(seq_len=4, requested_horizons=[3])

    def test_routing_rules_default(self) -> None:
        self.assertEqual(
            routing_rules(),
            {
                1: [1, 2, 3, 4, 5],
                2: [1, 2, 3, 4],
                3: [1, 2, 3],
                4: [1, 2],
                5: [1],
            },
        )


if __name__ == "__main__":
    unittest.main()
