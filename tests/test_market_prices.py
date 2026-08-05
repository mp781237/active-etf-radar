from __future__ import annotations

import http.client
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from active_etf_radar.market_prices import (
    MarketPriceUnavailable,
    _load_market_json_or_empty,
    _load_or_fetch_json,
    _parse_tpex_prices,
    _parse_twse_prices,
    _parse_yahoo_price,
    _yahoo_symbol,
)


class MarketPriceTests(unittest.TestCase):
    def test_load_json_retries_incomplete_response(self) -> None:
        class Response:
            def __init__(self, payload: bytes = b"", error: Exception | None = None) -> None:
                self.payload = payload
                self.error = error

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                if self.error:
                    raise self.error
                return self.payload

        responses = [
            Response(error=http.client.IncompleteRead(b"partial")),
            Response(payload=b'{"ok": true}'),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "price.json"
            with (
                patch("active_etf_radar.market_prices.urllib.request.urlopen", side_effect=responses) as urlopen,
                patch("active_etf_radar.market_prices.time_module.sleep"),
            ):
                data = _load_or_fetch_json(output, "https://example.test/prices")

        self.assertEqual(data, {"ok": True})
        self.assertEqual(urlopen.call_count, 2)

    def test_unavailable_market_price_returns_empty_data(self) -> None:
        with patch(
            "active_etf_radar.market_prices._load_or_fetch_json",
            side_effect=MarketPriceUnavailable("暫時失敗"),
        ):
            data = _load_market_json_or_empty(Path("unused.json"), "https://example.test/prices")
        self.assertEqual(data, {})

    def test_parse_twse_signed_change(self) -> None:
        data = {
            "tables": [
                {
                    "fields": ["證券代號", "收盤價", "漲跌(+/-)", "漲跌價差"],
                    "data": [["2330", "2,350.00", "<p>-</p>", "55.00"]],
                }
            ]
        }
        rows = _parse_twse_prices(data, "2026-07-24", {"2330"}, "url", "now")
        self.assertEqual(rows[0]["change"], -55.0)
        self.assertAlmostEqual(rows[0]["change_pct"], -2.287, places=3)

    def test_parse_tpex_change_percentage(self) -> None:
        data = {
            "tables": [
                {
                    "title": "上櫃股票行情",
                    "fields": ["代號", "收盤", "漲跌"],
                    "data": [["4979", "426.50", "+19.00"]],
                }
            ]
        }
        rows = _parse_tpex_prices(data, "2026-07-24", {"4979"}, "url", "now")
        self.assertEqual(rows[0]["change"], 19.0)
        self.assertAlmostEqual(rows[0]["change_pct"], 4.6626, places=4)

    def test_yahoo_symbol_mapping(self) -> None:
        self.assertEqual(_yahoo_symbol("LITE US"), ("LITE", "US"))
        self.assertEqual(_yahoo_symbol("285A JP"), ("285A.T", "JP"))
        self.assertEqual(_yahoo_symbol("009150 KS"), ("009150.KS", "KR"))
        self.assertEqual(_yahoo_symbol("300408 CH"), ("300408.SZ", "CN"))

    def test_parse_yahoo_uses_event_date_and_previous_close(self) -> None:
        data = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1784764800, 1784851200],
                        "meta": {"currency": "USD"},
                        "indicators": {"quote": [{"close": [100.0, 110.0]}]},
                    }
                ]
            }
        }
        row = _parse_yahoo_price(data, "TEST US", "2026-07-24", "US", "url", "now")
        self.assertIsNotNone(row)
        self.assertEqual(row["close"], 110.0)
        self.assertEqual(row["change_pct"], 10.0)


if __name__ == "__main__":
    unittest.main()
