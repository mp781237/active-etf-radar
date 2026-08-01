from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

from active_etf_radar.changes import compare_holdings
from active_etf_radar.streaks import Snapshot, compute_holding_streaks, load_snapshots


FUND_NAMES = {
    "00403A": "主動統一升級50",
    "00407A": "主動凱基台灣",
    "00981A": "主動統一台股增長",
    "00988A": "主動統一全球創新",
    "00991A": "主動復華未來50",
    "00992A": "主動群益台灣科技創新",
    "00994A": "主動第一金台股優",
    "00997A": "主動群益美國增長",
}


PENDING_FUNDS = [
    {
        "etf_code": "00407A",
        "fund_name": FUND_NAMES["00407A"],
        "manager": "凱基投信",
        "status": "預計 2026-06-04 至 2026-06-10 募集，尚無正式公開持股快照。",
        "source_url": "https://www.kgifund.com.tw/Upload/Activity/KGI00407A/",
    }
]


UNUSUAL_WEIGHT_DELTA_THRESHOLD = 0.30
UNUSUAL_WEIGHT_RATIO_THRESHOLD = 1.50


def build_multi_fund_view(project_root: Path) -> dict[str, Any]:
    snapshots = load_snapshots(project_root)
    snapshots_by_etf: dict[str, list[Snapshot]] = defaultdict(list)
    for snapshot in snapshots:
        snapshots_by_etf[snapshot.etf_code].append(snapshot)

    latest_snapshots = [items[-1] for _, items in sorted(snapshots_by_etf.items()) if items]
    changes_by_etf: dict[str, dict[str, dict[str, Any]]] = {}
    streaks_by_etf: dict[str, dict[str, dict[str, Any]]] = {}

    for etf_code, items in snapshots_by_etf.items():
        if len(items) >= 2:
            changes_by_etf[etf_code] = {
                row["stock_code"]: row for row in compare_holdings(items[-2].path, items[-1].path)
            }
        else:
            changes_by_etf[etf_code] = {}
        streaks, _ = compute_holding_streaks(project_root, etf_code=etf_code)
        streaks_by_etf[etf_code] = {row["stock_code"]: row for row in streaks}

    fund_cards = [_build_fund_card(snapshot, changes_by_etf.get(snapshot.etf_code, {})) for snapshot in latest_snapshots]
    previous_snapshots = [items[-2] for _, items in sorted(snapshots_by_etf.items()) if len(items) >= 2]
    previous_consensus_codes = _consensus_stock_codes(previous_snapshots)
    consensus_rows = _build_consensus_rows(
        latest_snapshots,
        changes_by_etf,
        streaks_by_etf,
        previous_consensus_codes,
    )
    latest_as_of_date = max((snapshot.as_of_date for snapshot in latest_snapshots), default=None)
    new_holding_rows = _build_new_holding_rows(changes_by_etf, latest_as_of_date.isoformat() if latest_as_of_date else "")
    unusual_increase_rows = _build_unusual_increase_rows(changes_by_etf)
    write_multi_fund_csvs(project_root, fund_cards, consensus_rows, new_holding_rows, unusual_increase_rows)

    return {
        "fund_cards": fund_cards,
        "fund_codes": [snapshot.etf_code for snapshot in latest_snapshots],
        "consensus_rows": consensus_rows,
        "new_holding_rows": new_holding_rows,
        "unusual_increase_rows": unusual_increase_rows,
        "pending_funds": [fund for fund in PENDING_FUNDS if fund["etf_code"] not in snapshots_by_etf],
        "source_dates": sorted({snapshot.as_of_date for snapshot in latest_snapshots}),
    }


def write_multi_fund_csvs(
    project_root: Path,
    fund_cards: list[dict[str, Any]],
    consensus_rows: list[dict[str, Any]],
    new_holding_rows: list[dict[str, Any]],
    unusual_increase_rows: list[dict[str, Any]],
) -> None:
    reports_dir = project_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    fund_fields = [
        "etf_code",
        "fund_code",
        "fund_name",
        "as_of_date",
        "holding_count",
        "total_weight",
        "top10_weight",
        "largest_holding",
        "largest_weight",
        "share_increase_count",
        "share_decrease_count",
    ]
    with (reports_dir / "multi_fund_overview.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fund_fields)
        writer.writeheader()
        writer.writerows(fund_cards)

    consensus_fields = [
        "stock_code",
        "stock_name",
        "holding_fund_count",
        "holding_funds",
        "average_weight",
        "total_weight",
        "max_weight",
        "same_share_increase_count",
        "same_share_decrease_count",
        "active_increase_streak_fund_count",
        "is_new_consensus",
    ]
    with (reports_dir / "multi_fund_consensus.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=consensus_fields)
        writer.writeheader()
        for row in consensus_rows:
            writer.writerow({field: row[field] for field in consensus_fields})

    new_holding_fields = [
        "etf_code",
        "fund_name",
        "stock_code",
        "stock_name",
        "new_weight_pct",
        "new_shares",
        "new_market_value",
        "new_as_of_datetime",
    ]
    with (reports_dir / "multi_fund_new_holdings.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=new_holding_fields)
        writer.writeheader()
        for row in new_holding_rows:
            writer.writerow({field: row[field] for field in new_holding_fields})

    unusual_increase_fields = [
        "etf_code",
        "fund_name",
        "stock_code",
        "stock_name",
        "share_status",
        "old_weight_pct",
        "new_weight_pct",
        "weight_change_pct",
        "weight_ratio",
        "share_change",
        "new_as_of_datetime",
        "reason",
    ]
    with (reports_dir / "multi_fund_unusual_increases.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=unusual_increase_fields)
        writer.writeheader()
        for row in unusual_increase_rows:
            writer.writerow({field: row[field] for field in unusual_increase_fields})


def _build_fund_card(snapshot: Snapshot, changes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = list(snapshot.rows_by_stock.values())
    rows.sort(key=lambda row: _num(row, "weight_pct"), reverse=True)
    total_weight = sum(_num(row, "weight_pct") for row in rows)
    top10_weight = sum(_num(row, "weight_pct") for row in rows[:10])
    largest = rows[0] if rows else {}
    return {
        "etf_code": snapshot.etf_code,
        "fund_code": snapshot.fund_code,
        "fund_name": FUND_NAMES.get(snapshot.etf_code, snapshot.etf_code),
        "as_of_date": snapshot.as_of_date.isoformat(),
        "holding_count": len(rows),
        "total_weight": round(total_weight, 2),
        "top10_weight": round(top10_weight, 2),
        "largest_holding": largest.get("stock_name", ""),
        "largest_weight": _num(largest, "weight_pct"),
        "share_increase_count": sum(1 for row in changes.values() if row.get("share_status") == "股數增加"),
        "share_decrease_count": sum(1 for row in changes.values() if row.get("share_status") == "股數減少"),
    }


def _build_consensus_rows(
    latest_snapshots: list[Snapshot],
    changes_by_etf: dict[str, dict[str, dict[str, Any]]],
    streaks_by_etf: dict[str, dict[str, dict[str, Any]]],
    previous_consensus_codes: set[str],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for snapshot in latest_snapshots:
        for stock_code, holding in snapshot.rows_by_stock.items():
            item = grouped.setdefault(
                stock_code,
                {
                    "stock_code": stock_code,
                    "stock_name": holding.get("stock_name", ""),
                    "fund_weights": {},
                    "fund_as_of_dates": {},
                    "fund_share_status": {},
                    "fund_weight_changes": {},
                    "fund_share_changes": {},
                    "fund_streaks": {},
                },
            )
            item["fund_weights"][snapshot.etf_code] = _num(holding, "weight_pct")
            item["fund_as_of_dates"][snapshot.etf_code] = snapshot.as_of_date.isoformat()
            change = changes_by_etf.get(snapshot.etf_code, {}).get(stock_code, {})
            streak = streaks_by_etf.get(snapshot.etf_code, {}).get(stock_code, {})
            item["fund_share_status"][snapshot.etf_code] = change.get("share_status", "")
            item["fund_weight_changes"][snapshot.etf_code] = _num(change, "weight_change_pct")
            item["fund_share_changes"][snapshot.etf_code] = _num(change, "share_change")
            item["fund_streaks"][snapshot.etf_code] = int(streak.get("current_increase_streak", 0) or 0)

    rows: list[dict[str, Any]] = []
    for item in grouped.values():
        weights = list(item["fund_weights"].values())
        holding_funds = sorted(item["fund_weights"])
        if len(holding_funds) < 2:
            continue
        share_statuses = item["fund_share_status"]
        fund_streaks = item["fund_streaks"]
        rows.append(
            {
                **item,
                "holding_fund_count": len(holding_funds),
                "holding_funds": ", ".join(holding_funds),
                "average_weight": round(sum(weights) / len(weights), 4) if weights else 0.0,
                "total_weight": round(sum(weights), 4),
                "max_weight": round(max(weights), 4) if weights else 0.0,
                "same_share_increase_count": sum(1 for status in share_statuses.values() if status == "股數增加"),
                "same_share_decrease_count": sum(1 for status in share_statuses.values() if status == "股數減少"),
                "active_increase_streak_fund_count": sum(1 for streak in fund_streaks.values() if streak > 0),
                "is_new_consensus": bool(previous_consensus_codes) and str(item["stock_code"]) not in previous_consensus_codes,
            }
        )

    rows.sort(
        key=lambda row: (
            int(row["holding_fund_count"]),
            int(row["same_share_increase_count"]),
            int(row["active_increase_streak_fund_count"]),
            float(row["average_weight"]),
        ),
        reverse=True,
    )
    return rows


def _build_new_holding_rows(
    changes_by_etf: dict[str, dict[str, dict[str, Any]]],
    latest_as_of_date: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for etf_code, changes in changes_by_etf.items():
        for row in changes.values():
            if row.get("share_status") != "新增":
                continue
            if _date_part(row.get("new_as_of_datetime", "")) != latest_as_of_date:
                continue
            rows.append(
                {
                    "etf_code": etf_code,
                    "fund_name": FUND_NAMES.get(etf_code, etf_code),
                    "stock_code": row.get("stock_code", ""),
                    "stock_name": row.get("stock_name", ""),
                    "new_weight_pct": _num(row, "new_weight_pct"),
                    "new_shares": _num(row, "new_shares"),
                    "new_market_value": _num(row, "new_market_value"),
                    "new_as_of_datetime": row.get("new_as_of_datetime", ""),
                }
            )

    rows.sort(key=lambda row: (str(row["etf_code"]), -float(row["new_weight_pct"]), str(row["stock_code"])))
    return rows


def _build_unusual_increase_rows(
    changes_by_etf: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for etf_code, changes in changes_by_etf.items():
        for row in changes.values():
            share_status = str(row.get("share_status", ""))
            if share_status not in ("新增", "股數增加"):
                continue

            old_weight = _num(row, "old_weight_pct")
            new_weight = _num(row, "new_weight_pct")
            weight_change = _num(row, "weight_change_pct")
            if weight_change <= 0:
                continue

            weight_ratio = new_weight / old_weight if old_weight > 0 else 0.0
            reasons = []
            if weight_change >= UNUSUAL_WEIGHT_DELTA_THRESHOLD:
                reasons.append(f"權重增加 >= {UNUSUAL_WEIGHT_DELTA_THRESHOLD:.2f}pp")
            if old_weight > 0 and weight_ratio >= UNUSUAL_WEIGHT_RATIO_THRESHOLD:
                reasons.append(f"權重放大 >= {UNUSUAL_WEIGHT_RATIO_THRESHOLD:.1f}x")
            if not reasons:
                continue

            rows.append(
                {
                    "etf_code": etf_code,
                    "fund_name": FUND_NAMES.get(etf_code, etf_code),
                    "stock_code": row.get("stock_code", ""),
                    "stock_name": row.get("stock_name", ""),
                    "share_status": share_status,
                    "old_weight_pct": old_weight,
                    "new_weight_pct": new_weight,
                    "weight_change_pct": weight_change,
                    "weight_ratio": round(weight_ratio, 4) if old_weight > 0 else "",
                    "share_change": _num(row, "share_change"),
                    "new_as_of_datetime": row.get("new_as_of_datetime", ""),
                    "reason": "；".join(reasons),
                }
            )

    rows.sort(
        key=lambda row: (
            -float(row["weight_change_pct"]),
            -float(row["new_weight_pct"]),
            str(row["etf_code"]),
            str(row["stock_code"]),
        )
    )
    return rows


def _date_part(value: object) -> str:
    return str(value or "").replace("/", "-")[:10]


def _consensus_stock_codes(snapshots: list[Snapshot]) -> set[str]:
    holder_count: dict[str, int] = defaultdict(int)
    for snapshot in snapshots:
        for stock_code in snapshot.rows_by_stock:
            holder_count[stock_code] += 1
    return {stock_code for stock_code, count in holder_count.items() if count >= 2}


def _num(row: dict[str, Any] | None, key: str) -> float:
    if not row:
        return 0.0
    value = row.get(key, "")
    return float(str(value).replace(",", "")) if value not in ("", None) else 0.0
