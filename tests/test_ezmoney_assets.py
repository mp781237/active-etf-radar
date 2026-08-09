import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from active_etf_radar.changes import compare_holdings
from active_etf_radar.sources.ezmoney import _normalize_asset_rows


class EzMoneyAssetTests(unittest.TestCase):
    def test_normalize_asset_rows_keeps_futures_separate_from_stocks(self) -> None:
        assets = [
        {
            "AssetCode": "GD",
            "EditDate": "2026-08-07T17:16:50",
            "Details": [
                {
                    "AssetCode": "GD",
                    "TranDate": "2026-08-07T00:00:00",
                    "DetailCode": "TX",
                    "DetailName": "台指期貨(B)",
                    "Position": "B",
                    "MTH": "2026/08",
                    "MoneyType": "NTD",
                    "Share": 1940,
                    "Amount": 17187236000,
                    "NavRate": 5.75,
                }
            ],
        },
        {
            "AssetCode": "ST",
            "EditDate": "2026-08-07T17:16:50",
            "Details": [
                {
                    "AssetCode": "ST",
                    "TranDate": "2026-08-07T00:00:00",
                    "DetailCode": "2330",
                    "DetailName": "台積電",
                    "MoneyType": "NTD",
                    "Share": 100,
                    "Amount": 100000,
                    "NavRate": 9.63,
                }
            ],
        },
        ]

        rows = _normalize_asset_rows(
            assets=assets,
            fund={"sStockNo": "00981A"},
            fund_code="49YTW",
            etf_code="00981A",
            source_url="https://example.test",
            fetched_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        )

        self.assertEqual([row["asset_code"] for row in rows], ["ST", "GD"])
        future = rows[1]
        self.assertEqual(future["stock_code"], "TX")
        self.assertEqual(future["position"], "B")
        self.assertEqual(future["contract_month"], "2026/08")
        self.assertEqual(future["weight_pct"], 5.75)

    def test_compare_holdings_ignores_non_stock_assets(self) -> None:
        fieldnames = [
            "asset_code",
            "stock_code",
            "stock_name",
            "shares",
            "market_value",
            "weight_pct",
            "as_of_datetime",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            old_path = Path(tmpdir) / "old.csv"
            new_path = Path(tmpdir) / "new.csv"
            for path, future_shares in ((old_path, 100), (new_path, 200)):
                with path.open("w", encoding="utf-8-sig", newline="") as file:
                    writer = csv.DictWriter(file, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerow(
                        {
                            "asset_code": "ST",
                            "stock_code": "2330",
                            "stock_name": "台積電",
                            "shares": 100,
                            "market_value": 100000,
                            "weight_pct": 10,
                            "as_of_datetime": "2026-08-07",
                        }
                    )
                    writer.writerow(
                        {
                            "asset_code": "GD",
                            "stock_code": "TX",
                            "stock_name": "台指期貨(B)",
                            "shares": future_shares,
                            "market_value": 100000,
                            "weight_pct": 5,
                            "as_of_datetime": "2026-08-07",
                        }
                    )

            changes = compare_holdings(old_path, new_path)

        self.assertEqual([row["stock_code"] for row in changes], ["2330"])


if __name__ == "__main__":
    unittest.main()
