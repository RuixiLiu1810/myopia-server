from __future__ import annotations

import base64
import unittest

from myopia_backend.routers.inference import (
    _decode_data_url_to_bytes,
    _safe_ext,
    _validate_inline_payload_size,
    _validate_visits_count,
)


class ApiGuardTests(unittest.TestCase):
    def test_safe_ext_defaults_and_normalizes(self) -> None:
        self.assertEqual(_safe_ext(None), ".jpg")
        self.assertEqual(_safe_ext("PNG"), ".png")
        self.assertEqual(_safe_ext(".jpeg"), ".jpeg")

    def test_safe_ext_rejects_invalid(self) -> None:
        with self.assertRaises(ValueError):
            _safe_ext(".exe")

    def test_decode_data_url_to_bytes_supports_plain_base64(self) -> None:
        payload = base64.b64encode(b"hello").decode("ascii")
        self.assertEqual(_decode_data_url_to_bytes(payload), b"hello")

    def test_decode_data_url_to_bytes_supports_data_url(self) -> None:
        payload = "data:image/png;base64," + base64.b64encode(b"abc").decode("ascii")
        self.assertEqual(_decode_data_url_to_bytes(payload), b"abc")

    def test_decode_data_url_to_bytes_rejects_invalid(self) -> None:
        with self.assertRaises(ValueError):
            _decode_data_url_to_bytes("not_base64***")

    def test_validate_visits_count(self) -> None:
        _validate_visits_count(visits_count=3, max_visits=5)
        with self.assertRaises(ValueError):
            _validate_visits_count(visits_count=0, max_visits=5)
        with self.assertRaises(ValueError):
            _validate_visits_count(visits_count=6, max_visits=5)

    def test_validate_inline_payload_size(self) -> None:
        new_total = _validate_inline_payload_size(
            image_bytes=b"a" * 100,
            current_total=200,
            max_image_bytes=1024,
            max_total_bytes=1024,
        )
        self.assertEqual(new_total, 300)

        with self.assertRaises(ValueError):
            _validate_inline_payload_size(
                image_bytes=b"a" * 2048,
                current_total=0,
                max_image_bytes=1024,
                max_total_bytes=4096,
            )

        with self.assertRaises(ValueError):
            _validate_inline_payload_size(
                image_bytes=b"a" * 800,
                current_total=400,
                max_image_bytes=1024,
                max_total_bytes=1000,
            )


if __name__ == "__main__":
    unittest.main()
