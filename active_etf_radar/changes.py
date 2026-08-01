from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from active_etf_radar.streaks import find_latest_snapshot_csvs


def find_latest_two_csvs(project_root: Path, etf_code: str | None = None) -> tuple[Path, Path]:
    files = find_latest_snapshot_csvs(project_root, limit=2, etf_code=etf_code)
    if len(files) < 2:
        raise ValueError("至少需要兩份不同時間的持股 CSV 才能計算增持/減持。")
    return files[0], files[1]


def compare_holdings(old_csv: Path, new_csv: Path) -> list[dict[str, Any]]:
    old_rows = _read_by_stock(old_csv)
    new_rows = _read_by_stock(new_csv)
    stock_codes = sorted(set(old_rows) | set(new_rows))

    changes: list[dict[str, Any]] = []
    for stock_code in stock_codes:
        old = old_rows.get(stock_code)
        new = new_rows.get(stock_code)
        old_weight = _num(old, "weight_pct")
        new_weight = _num(new, "weight_pct")
        old_shares = _num(old, "shares")
        new_shares = _num(new, "shares")
        old_value = _num(old, "market_value")
        new_value = _num(new, "market_value")

        share_status = _status_from_values(old, new, old_shares, new_shares, "股數增加", "股數減少", "股數不變")
        weight_status = _status_from_values(old, new, old_weight, new_weight, "權重增加", "權重降低", "權重持平")

        changes.append(
            {
                "stock_code": stock_code,
                "stock_name": (new or old or {}).get("stock_name", ""),
                "status": share_status,
                "share_status": share_status,
                "weight_status": weight_status,
                "old_weight_pct": old_weight,
                "new_weight_pct": new_weight,
                "weight_change_pct": _round_float(new_weight - old_weight),
                "old_shares": old_shares,
                "new_shares": new_shares,
                "share_change": _round_float(new_shares - old_shares),
                "old_market_value": old_value,
                "new_market_value": new_value,
                "market_value_change": _round_float(new_value - old_value),
                "old_as_of_datetime": (old or {}).get("as_of_datetime", ""),
                "new_as_of_datetime": (new or {}).get("as_of_datetime", ""),
            }
        )

    changes.sort(key=lambda row: abs(float(row["weight_change_pct"])), reverse=True)
    return changes


def write_changes_csv(changes: list[dict[str, Any]], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "stock_code",
        "stock_name",
        "status",
        "share_status",
        "weight_status",
        "old_weight_pct",
        "new_weight_pct",
        "weight_change_pct",
        "old_shares",
        "new_shares",
        "share_change",
        "old_market_value",
        "new_market_value",
        "market_value_change",
        "old_as_of_datetime",
        "new_as_of_datetime",
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(changes)
    return output_path


def _read_by_stock(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    return {row["stock_code"]: row for row in rows}


def _num(row: dict[str, str] | None, key: str) -> float:
    if not row:
        return 0.0
    value = row.get(key, "")
    return float(str(value).replace(",", "")) if value not in ("", None) else 0.0


def _status_from_values(
    old_row: dict[str, str] | None,
    new_row: dict[str, str] | None,
    old_value: float,
    new_value: float,
    increase_label: str,
    decrease_label: str,
    flat_label: str,
) -> str:
    if old_row is None:
        return "新增"
    if new_row is None:
        return "移除"
    if new_value > old_value:
        return increase_label
    if new_value < old_value:
        return decrease_label
    return flat_label


def _round_float(value: float) -> float:
    return round(value, 6)
