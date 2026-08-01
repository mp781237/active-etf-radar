from __future__ import annotations

import unittest

from active_etf_radar.market_prices import (
    _parse_tpex_prices,
    _parse_twse_prices,
    _parse_yahoo_price,
    _yahoo_symbol,
)


class MarketPriceTests(unittest.TestCase):
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
