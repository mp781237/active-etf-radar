from __future__ import annotations

import unittest

from active_etf_radar.sources.capital_buyback import _decode_json_response


class CapitalBuybackTests(unittest.TestCase):
    def test_decode_json_response_accepts_utf8_bom(self) -> None:
        payload = _decode_json_response(b"\xef\xbb\xbf{\"code\":200}", "utf-8")
        self.assertEqual(payload, {"code": 200})

    def test_decode_json_response_accepts_plain_utf8(self) -> None:
        payload = _decode_json_response(b"{\"code\":200}", "utf-8")
        self.assertEqual(payload, {"code": 200})

    def test_decode_json_response_reports_maintenance_page(self) -> None:
        with self.assertRaisesRegex(ValueError, "網站維護頁"):
            _decode_json_response(b"\xef\xbb\xbf<!doctype html><title>maintenance</title>", "utf-8")


if __name__ == "__main__":
    unittest.main()
