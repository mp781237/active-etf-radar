from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Snapshot:
    etf_code: str
    fund_code: str
    as_of_date: date
    path: Path
    rows_by_stock: dict[str, dict[str, str]]


def find_latest_snapshot_csvs(project_root: Path, limit: int | None = None, etf_code: str | None = None) -> list[Path]:
    snapshots = load_snapshots(project_root, etf_code=etf_code)
    paths = [snapshot.path for snapshot in snapshots]
    if limit is None:
        return paths
    return paths[-limit:]


def compute_holding_streaks(project_root: Path, etf_code: str | None = None) -> tuple[list[dict[str, Any]], list[date]]:
    snapshots = load_snapshots(project_root, etf_code=etf_code)
    if len(snapshots) < 2:
        return [], [snapshot.as_of_date for snapshot in snapshots]

    snapshot_dates = [snapshot.as_of_date for snapshot in snapshots]
    stock_codes = sorted({code for snapshot in snapshots for code in snapshot.rows_by_stock})
    streaks: list[dict[str, Any]] = []

    for stock_code in stock_codes:
        current_streak = 0
        latest_change = 0.0
        streak_start_date = ""
        streak_start_shares = 0.0
        previous_shares: float | None = None
        latest_known_row: dict[str, str] = {}

        for snapshot in snapshots:
            row = snapshot.rows_by_stock.get(stock_code)
            shares = _num(row, "shares")
            if row:
                latest_known_row = row

            if previous_shares is not None:
                change = shares - previous_shares
                if snapshot is snapshots[-1]:
                    latest_change = change
                if change > 0:
                    if current_streak == 0:
                        streak_start_date = snapshot.as_of_date.isoformat()
                        streak_start_shares = previous_shares
                    current_streak += 1
                else:
                    current_streak = 0
                    streak_start_date = ""
                    streak_start_shares = shares

            previous_shares = shares

        latest_snapshot_row = snapshots[-1].rows_by_stock.get(stock_code, latest_known_row)
        latest_shares = _num(latest_snapshot_row, "shares")
        streak_total_change = latest_shares - streak_start_shares if current_streak > 0 else 0.0

        streaks.append(
            {
                "stock_code": stock_code,
                "stock_name": latest_snapshot_row.get("stock_name", ""),
                "etf_code": snapshots[-1].etf_code,
                "fund_code": snapshots[-1].fund_code,
                "current_increase_streak": current_streak,
                "latest_share_change": _round_float(latest_change),
                "streak_total_share_change": _round_float(streak_total_change),
                "latest_shares": latest_shares,
                "latest_weight_pct": _num(latest_snapshot_row, "weight_pct"),
                "streak_start_date": streak_start_date,
                "latest_as_of_date": snapshots[-1].as_of_date.isoformat(),
                "snapshot_count": len(snapshots),
            }
        )

    streaks.sort(
        key=lambda row: (
            int(row["current_increase_streak"]),
            float(row["streak_total_share_change"]),
            float(row["latest_share_change"]),
        ),
        reverse=True,
    )
    return streaks, snapshot_dates


def load_snapshots(project_root: Path, etf_code: str | None = None) -> list[Snapshot]:
    processed_dir = project_root / "data" / "processed"
    latest_by_key: dict[tuple[str, date], Path] = {}

    for path in processed_dir.glob("holdings_*.csv"):
        rows = _read_rows(path)
        if not rows:
            continue
        row_etf_code = str(rows[0].get("etf_code", "")).strip()
        if etf_code and row_etf_code != etf_code:
            continue
        as_of_date = _parse_snapshot_date(rows[0])
        current = latest_by_key.get((row_etf_code, as_of_date))
        if current is None or path.stat().st_mtime > current.stat().st_mtime:
            latest_by_key[(row_etf_code, as_of_date)] = path

    snapshots: list[Snapshot] = []
    for (row_etf_code, as_of_date), path in sorted(latest_by_key.items(), key=lambda item: (item[0][0], item[0][1])):
        rows = _read_rows(path)
        snapshots.append(
            Snapshot(
                etf_code=row_etf_code,
                fund_code=str(rows[0].get("fund_code", "")).strip(),
                as_of_date=as_of_date,
                path=path,
                rows_by_stock={
                    row["stock_code"]: row
                    for row in rows
                    if row.get("stock_code") and (row.get("asset_code") or "ST") == "ST"
                },
            )
        )
    return snapshots


def write_streaks_csv(streaks: list[dict[str, Any]], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "stock_code",
        "stock_name",
        "etf_code",
        "fund_code",
        "current_increase_streak",
        "latest_share_change",
        "streak_total_share_change",
        "latest_shares",
        "latest_weight_pct",
        "streak_start_date",
        "latest_as_of_date",
        "snapshot_count",
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(streaks)
    return output_path


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _parse_snapshot_date(row: dict[str, str]) -> date:
    if row.get("source") == "ezmoney" and row.get("edit_datetime"):
        raw_value = row["edit_datetime"]
    else:
        raw_value = row.get("as_of_datetime") or row.get("query_date") or row.get("fetched_at")
    if not raw_value:
        raise ValueError("持股 CSV 缺少 as_of_datetime/query_date/fetched_at，無法建立時間序列")

    text = str(raw_value).strip()
    normalized = text.replace("/", "-")
    date_part = normalized[:10]
    try:
        return date.fromisoformat(date_part)
    except ValueError:
        return datetime.fromisoformat(normalized).date()


def _num(row: dict[str, str] | None, key: str) -> float:
    if not row:
        return 0.0
    value = row.get(key, "")
    return float(str(value).replace(",", "")) if value not in ("", None) else 0.0


def _round_float(value: float) -> float:
    return round(value, 6)
