from __future__ import annotations

import csv
import html
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from active_etf_radar.changes import compare_holdings, find_latest_two_csvs, write_changes_csv
from active_etf_radar.market_prices import refresh_event_market_prices
from active_etf_radar.multi_fund import build_multi_fund_view
from active_etf_radar.streaks import Snapshot, compute_holding_streaks, load_snapshots, write_streaks_csv


def build_dashboard(project_root: Path, csv_path: Path | None, output_path: Path) -> Path:
    source_csv = csv_path or _latest_csv(project_root)
    rows = _read_rows(source_csv)
    if not rows:
        raise ValueError(f"持股 CSV 沒有資料：{source_csv}")

    etf_code = str(rows[0].get("etf_code", "")).strip()
    multi_fund_view = build_multi_fund_view(project_root)
    multi_fund_view["share_series"] = _build_cross_fund_share_series(project_root)
    try:
        multi_fund_view["market_prices"] = refresh_event_market_prices(
            project_root,
            _build_event_date_requests(multi_fund_view),
        )
    except (OSError, ValueError):
        multi_fund_view["market_prices"] = {}
    fund_detail_views = _build_fund_detail_views(project_root, active_etf_code=etf_code)
    per_fund_series = _build_per_fund_share_series(project_root)
    for view in fund_detail_views:
        view_etf_code = str(view["rows"][0].get("etf_code", "")) if view["rows"] else ""
        view["share_series"] = per_fund_series.get(view_etf_code, {})
    render_active_etf_code = etf_code
    if csv_path is None and fund_detail_views:
        render_active_etf_code = str(fund_detail_views[0]["rows"][0].get("etf_code", "")) or etf_code
    if render_active_etf_code != etf_code:
        for view in fund_detail_views:
            if str(view["rows"][0].get("etf_code", "")) == render_active_etf_code:
                write_streaks_csv(view["streaks"], project_root / "reports" / "holding_streaks.csv")
                break

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _render_html(fund_detail_views, active_etf_code=render_active_etf_code, multi_fund_view=multi_fund_view),
        encoding="utf-8",
        newline="",
    )
    return output_path


def _latest_csv(project_root: Path) -> Path:
    processed_dir = project_root / "data" / "processed"
    files = sorted(processed_dir.glob("holdings_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"找不到持股 CSV：{processed_dir}")
    return files[0]


def _read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    for row in rows:
        row["shares_num"] = _to_float(row.get("shares"))
        row["market_value_num"] = _to_float(row.get("market_value"))
        row["weight_num"] = _to_float(row.get("weight_pct"))
    rows.sort(key=lambda row: row["weight_num"], reverse=True)
    return rows


def _read_changes(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    for row in rows:
        row["weight_change_num"] = _to_float(row.get("weight_change_pct"))
        row["share_change_num"] = _to_float(row.get("share_change"))
        row["share_status"] = row.get("share_status") or row.get("status", "")
        row["weight_status"] = row.get("weight_status", "")
    rows.sort(key=lambda row: abs(row["weight_change_num"]), reverse=True)
    return rows


def _build_latest_changes(project_root: Path, etf_code: str, output_path: Path) -> list[dict[str, Any]]:
    try:
        old_csv, new_csv = find_latest_two_csvs(project_root, etf_code=etf_code)
    except ValueError:
        return []
    changes = compare_holdings(old_csv=old_csv, new_csv=new_csv)
    write_changes_csv(changes=changes, output_path=output_path)
    return _read_changes(output_path)


def _build_fund_detail_views(project_root: Path, active_etf_code: str) -> list[dict[str, Any]]:
    latest_by_etf = {}
    for snapshot in load_snapshots(project_root):
        latest_by_etf[snapshot.etf_code] = snapshot

    views: list[dict[str, Any]] = []
    for etf_code, snapshot in sorted(latest_by_etf.items()):
        rows = _read_rows(snapshot.path)
        if not rows:
            continue

        changes_path = project_root / "reports" / f"holding_changes_{etf_code}.csv"
        changes = _build_latest_changes(project_root, etf_code, changes_path)
        streaks, snapshot_dates = compute_holding_streaks(project_root, etf_code=etf_code)
        streaks_path = project_root / "reports" / f"holding_streaks_{etf_code}.csv"
        write_streaks_csv(streaks, streaks_path)
        if etf_code == active_etf_code:
            write_streaks_csv(streaks, project_root / "reports" / "holding_streaks.csv")

        views.append(
            {
                "rows": rows,
                "source_csv": snapshot.path.relative_to(project_root),
                "changes_path": changes_path.relative_to(project_root),
                "changes": changes,
                "streaks_path": streaks_path.relative_to(project_root),
                "streaks": streaks,
                "snapshot_dates": snapshot_dates,
            }
        )
    return views


def _to_float(value: object) -> float:
    if value in (None, ""):
        return 0.0
    return float(str(value).replace(",", ""))


def _build_event_date_requests(view: dict[str, Any]) -> dict[str, set[str]]:
    requests: dict[str, set[str]] = {}

    def add(stock_code: object, raw_date: object) -> None:
        code = str(stock_code or "").strip()
        event_date = str(raw_date or "").replace("/", "-")[:10]
        if code and event_date:
            requests.setdefault(code, set()).add(event_date)

    for key in ("new_holding_rows", "unusual_increase_rows"):
        for row in view.get(key, []):
            add(row.get("stock_code"), row.get("new_as_of_datetime"))

    for row in view.get("consensus_rows", []):
        if int(row.get("same_share_increase_count", 0) or 0) < 2:
            continue
        for fund_code, status in row.get("fund_share_status", {}).items():
            if status == "股數增加":
                add(row.get("stock_code"), row.get("fund_as_of_dates", {}).get(fund_code))
    return requests


def _build_cross_fund_share_series(project_root: Path, max_points: int = 12) -> dict[str, list[float]]:
    """每檔股票在所有持有基金的總股數，沿聯合資料日 forward-fill，取最近 max_points 個資料日。

    給共識持股總表的「近期股數趨勢」sparkline 用。同一股票代號在不同基金是同一檔，
    股數單位一致可相加；不同股票各自正規化，量級不同不影響趨勢判讀。
    """
    snapshots = load_snapshots(project_root)
    if not snapshots:
        return {}

    by_fund: dict[str, list[Snapshot]] = {}
    for snap in snapshots:
        by_fund.setdefault(snap.etf_code, []).append(snap)
    for slist in by_fund.values():
        slist.sort(key=lambda snap: snap.as_of_date)

    union_dates = sorted({snap.as_of_date for snap in snapshots})[-max_points:]
    all_codes = {code for snap in snapshots for code in snap.rows_by_stock}

    series: dict[str, list[float]] = {}
    for code in all_codes:
        seq: list[float] = []
        for day in union_dates:
            total = 0.0
            for slist in by_fund.values():
                last_snap: Snapshot | None = None
                for snap in slist:
                    if snap.as_of_date <= day:
                        last_snap = snap
                    else:
                        break
                if last_snap is not None:
                    row = last_snap.rows_by_stock.get(code)
                    if row:
                        total += _to_float(row.get("shares"))
            seq.append(total)
        series[code] = seq
    return series


def _build_per_fund_share_series(project_root: Path, max_points: int = 12) -> dict[str, dict[str, list[float]]]:
    """每檔基金各自的每股股數時間序列（取該基金最近 max_points 個資料日），供單基金完整持股表趨勢線使用。"""
    snapshots = load_snapshots(project_root)
    by_fund: dict[str, list[Snapshot]] = {}
    for snap in snapshots:
        by_fund.setdefault(snap.etf_code, []).append(snap)

    result: dict[str, dict[str, list[float]]] = {}
    for etf_code, slist in by_fund.items():
        slist.sort(key=lambda snap: snap.as_of_date)
        window = slist[-max_points:]
        codes = {code for snap in window for code in snap.rows_by_stock}
        series: dict[str, list[float]] = {}
        for code in codes:
            series[code] = [
                _to_float((snap.rows_by_stock.get(code) or {}).get("shares")) for snap in window
            ]
        result[etf_code] = series
    return result


def _render_sparkline(values: list[float], small: bool = False) -> str:
    if not values or len([v for v in values if v]) < 2:
        return '<span class="spark-empty" title="資料日不足">—</span>'

    width, height, pad = (60.0, 18.0, 2.5) if small else (88.0, 24.0, 3.0)
    count = len(values)
    low, high = min(values), max(values)
    span = high - low

    def _x(index: int) -> float:
        return pad + index / (count - 1) * (width - 2 * pad)

    if span == 0:
        mid = height / 2
        points = " ".join(f"{_x(i):.1f},{mid:.1f}" for i in range(count))
        color = "var(--muted)"
    else:
        points = " ".join(
            f"{_x(i):.1f},{height - pad - (v - low) / span * (height - 2 * pad):.1f}"
            for i, v in enumerate(values)
        )
        color = "#107c41" if values[-1] >= values[0] else "#b42318"

    last_x, last_y = points.split(" ")[-1].split(",")
    label = f"近 {count} 個資料日總股數趨勢"
    return (
        f'<svg class="spark" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" role="img" aria-label="{label}">'
        f"<title>{label}</title>"
        f'<polyline fill="none" stroke="{color}" stroke-width="1.6" '
        f'stroke-linejoin="round" stroke-linecap="round" points="{points}"/>'
        f'<circle cx="{last_x}" cy="{last_y}" r="2.1" fill="{color}"/></svg>'
    )


def _build_today_summary(
    new_buy_items: list[dict[str, Any]],
    new_holding_rows: list[dict[str, Any]],
    unusual_rows: list[dict[str, Any]],
    same_rows: list[dict[str, Any]],
) -> str:
    new_buy = len(new_buy_items)
    new_hold = len({str(row.get("stock_code", "")) for row in new_holding_rows})
    unusual = len({str(row.get("stock_code", "")) for row in unusual_rows})
    same = len(same_rows)

    parts: list[str] = []
    if new_buy:
        lead = _esc(str(new_buy_items[0]["stock_name"]))
        parts.append(f"<b>{new_buy}</b> 檔新買進並進入共識（如 {lead}）")
    if new_hold:
        parts.append(f"<b>{new_hold}</b> 檔首次被納入持股")
    if unusual:
        parts.append(f"<b>{unusual}</b> 檔異常加碼")
    if same:
        parts.append(f"<b>{same}</b> 檔被多檔基金同向加股")

    if parts:
        body = "今天 " + "、".join(parts) + "。"
    else:
        body = "今天各基金最新公開資料沒有明顯的共識變化或異常加碼。"

    return (
        '<div class="today-summary">'
        '<span class="today-tag">今日結論</span>'
        f'<span class="today-body">{body}</span>'
        "</div>"
    )


def _render_legacy_html(
    rows: list[dict[str, Any]],
    source_csv: Path,
    changes_path: Path,
    changes: list[dict[str, Any]],
    streaks_path: Path,
    streaks: list[dict[str, Any]],
    snapshot_dates: list[date],
    multi_fund_view: dict[str, Any],
) -> str:
    first = rows[0]
    etf_code = str(first.get("etf_code", ""))
    fund_code = str(first.get("fund_code", ""))
    fetched_at = str(first.get("fetched_at", ""))
    as_of_datetime = str(first.get("as_of_datetime", ""))
    edit_datetime = str(first.get("edit_datetime", ""))
    source_url = str(first.get("source_url", ""))
    total_weight = sum(row["weight_num"] for row in rows)
    top_10_weight = sum(row["weight_num"] for row in rows[:10])
    max_weight = rows[0]["weight_num"]
    total_market_value = sum(row["market_value_num"] for row in rows)
    bucket_counts = _bucket_counts(rows)
    currency_counts = Counter(str(row.get("currency", "")) for row in rows)
    generated_at = datetime.now().isoformat(timespec="seconds")
    has_multi_fund = len(multi_fund_view["fund_cards"]) > 1
    page_title = "主動式 ETF 多基金雷達" if has_multi_fund else f"{etf_code} 主動式 ETF 持股雷達"

    payload_rows = [
        {
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "shares": row["shares_num"],
            "market_value": row["market_value_num"],
            "weight_pct": row["weight_num"],
            "currency": row["currency"],
        }
        for row in rows
    ]
    payload = {
        "rows": payload_rows,
        "topRows": payload_rows[:15],
        "buckets": bucket_counts,
        "currencyCounts": dict(currency_counts),
    }

    top_bars = "\n".join(_render_bar(row, index + 1, max_weight) for index, row in enumerate(rows[:15]))
    bucket_bars = "\n".join(_render_bucket_bar(label, count, len(rows)) for label, count in bucket_counts)
    currency_pills = "\n".join(
        f'<span class="pill">{_esc(currency or "未標示")} · {count}</span>'
        for currency, count in sorted(currency_counts.items())
    )

    table_rows = "\n".join(_render_table_row(row, index + 1) for index, row in enumerate(rows))
    multi_fund_section = _render_multi_fund_section(multi_fund_view)
    changes_section = _render_changes_section(changes, changes_path)
    streaks_section = _render_streaks_section(streaks, streaks_path, snapshot_dates)

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(page_title)}</title>
  <style>
    :root {{
      --bg: #f3f6fa;
      --surface: #ffffff;
      --surface-2: #f1f5f9;
      --surface-3: #f8fafc;
      --text: #111827;
      --muted: #667085;
      --line: #d8e1ec;
      --accent: #0f7b72;
      --accent-2: #1f5eff;
      --warn: #b56a19;
      --good: #107c41;
      --shadow: 0 1px 2px rgba(16, 24, 40, 0.05), 0 10px 30px rgba(16, 24, 40, 0.06);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "Noto Sans TC", "Microsoft JhengHei", Arial, sans-serif;
      line-height: 1.55;
    }}
    .shell {{ max-width: 1500px; margin: 0 auto; padding: 24px 24px 48px; }}
    header {{
      display: grid;
      grid-template-columns: 1.5fr 1fr;
      gap: 24px;
      align-items: end;
      padding: 18px 0 16px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{ margin: 0; font-size: 32px; line-height: 1.15; letter-spacing: 0; }}
    h2 {{ margin: 0 0 16px; font-size: 20px; letter-spacing: 0; }}
    .subtitle {{ margin: 12px 0 0; color: var(--muted); font-size: 15px; max-width: 720px; }}
    .audit {{
      display: grid;
      gap: 8px;
      justify-self: end;
      color: var(--muted);
      font-size: 13px;
      text-align: right;
    }}
    .audit code {{ color: var(--text); background: var(--surface-2); padding: 2px 6px; border-radius: 6px; }}
    .grid {{ display: grid; gap: 18px; }}
    .kpis {{ grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 24px 0 18px; }}
    .fund-grid {{ grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); margin: 24px 0 18px; }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 18px;
    }}
    .kpi-label {{ color: var(--muted); font-size: 13px; }}
    .kpi-value {{ margin-top: 6px; font-size: 28px; font-weight: 750; line-height: 1.15; }}
    .kpi-note {{ margin-top: 8px; color: var(--muted); font-size: 12px; }}
    .main {{ grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.75fr); align-items: start; }}
    .multi-layout {{ grid-template-columns: minmax(720px, 1fr) minmax(390px, 0.58fr); gap: 24px; align-items: stretch; }}
    .consensus-card {{ display: flex; flex-direction: column; min-height: 100%; }}
    .consensus-card .toolbar {{ flex: 0 0 auto; }}
    .consensus-table-wrap {{ flex: 1 1 auto; min-height: 0; max-height: none; }}
    .bar-list {{ display: grid; gap: 10px; }}
    .bar-row {{
      display: grid;
      grid-template-columns: 40px minmax(140px, 220px) minmax(180px, 1fr) 70px;
      gap: 12px;
      align-items: center;
      font-size: 14px;
    }}
    .rank {{ color: var(--muted); font-variant-numeric: tabular-nums; }}
    .name strong {{ display: block; font-size: 14px; }}
    .name span {{ color: var(--muted); font-size: 12px; }}
    .track {{ height: 12px; background: var(--surface-2); border-radius: 999px; overflow: hidden; }}
    .fill {{ height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-2)); border-radius: inherit; }}
    .value {{ text-align: right; font-variant-numeric: tabular-nums; font-weight: 700; }}
    .side-stack {{ display: grid; gap: 16px; align-content: start; }}
    .side-stack .card {{ padding: 20px 22px; }}
    .side-stack h2 {{ margin-bottom: 14px; }}
    .bucket {{ display: grid; grid-template-columns: 86px 1fr 42px; gap: 10px; align-items: center; margin: 10px 0; font-size: 14px; }}
    .bucket .track {{ height: 10px; }}
    .pill-row {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .pill {{ border: 1px solid var(--line); background: var(--surface-2); border-radius: 999px; padding: 6px 10px; font-size: 13px; color: var(--muted); }}
    .weight-tags {{ display: grid; gap: 6px; justify-items: start; min-width: 180px; }}
    .insight-row .weight-tags {{ justify-items: end; min-width: 0; }}
    .weight-tag {{ display: inline-flex; align-items: center; gap: 4px; border: 1px solid var(--line); border-radius: 999px; background: var(--surface-2); padding: 4px 8px; font-size: 12px; white-space: nowrap; }}
    .weight-tag strong {{ color: var(--text); font-variant-numeric: tabular-nums; }}
    .tag-note {{ color: var(--good); font-size: 11px; font-style: normal; font-weight: 800; }}
    .insight-row .weight-tags {{ justify-items: end; min-width: 0; }}
    .notice {{ border-left: 4px solid var(--warn); background: #fff8ef; color: #5c3b14; }}
    .fund-chip {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 8px; background: var(--surface-2); color: var(--muted); font-size: 12px; }}
    .section-title {{ display: flex; justify-content: space-between; gap: 12px; align-items: end; margin-bottom: 14px; }}
    .change-grid {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(300px, 0.45fr); gap: 18px; align-items: start; }}
    .change-summary {{ display: grid; gap: 10px; }}
    .change-item {{ display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); padding: 8px 0; font-size: 14px; }}
    .change-item strong {{ font-variant-numeric: tabular-nums; }}
    .streak-badge {{ display: inline-flex; align-items: center; justify-content: center; min-width: 44px; padding: 4px 8px; border-radius: 999px; background: #eaf7f0; color: var(--good); font-weight: 750; }}
    .date-range {{ color: var(--muted); font-size: 12px; }}
    .positive {{ color: var(--good); }}
    .negative {{ color: #b42318; }}
    .today-summary {{ display: flex; align-items: center; gap: 12px; margin: 0 0 22px; padding: 15px 30px 17px; background: linear-gradient(180deg, #07131f, #0a1f33); color: #f4f8fc; border-top: 1px solid rgba(255, 255, 255, 0.08); border-radius: 0 0 14px 14px; box-shadow: var(--shadow); }}
    .today-tag {{ flex: 0 0 auto; display: inline-flex; align-items: center; border-radius: 999px; padding: 4px 11px; background: rgba(255, 255, 255, 0.14); color: #ffd9a0; font-size: 12px; font-weight: 800; letter-spacing: 0.04em; }}
    .today-body {{ font-size: 15.5px; line-height: 1.5; }}
    .today-body b {{ color: #ffd9a0; font-variant-numeric: tabular-nums; font-weight: 800; }}
    .spark {{ display: block; }}
    .spark-cell {{ width: 96px; }}
    .spark-empty {{ color: var(--muted); }}
    .insight-stock .spark {{ margin-top: 6px; opacity: 0.92; }}
    .event-list .insight-row:first-child {{ box-shadow: inset 3px 0 0 var(--event-accent); background: color-mix(in srgb, var(--event-accent) 7%, var(--surface)); border-radius: 8px; padding-left: 9px; }}
    .toolbar {{ display: flex; gap: 10px; align-items: center; justify-content: space-between; margin-bottom: 12px; }}
    .toolbar input {{
      width: min(340px, 100%);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      font: inherit;
      color: var(--text);
      background: var(--surface);
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    #consensusTable {{ min-width: 1020px; }}
    th, td {{ padding: 11px 10px; border-bottom: 1px solid var(--line); text-align: left; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 700; background: #f9fbfd; position: sticky; top: 0; z-index: 1; }}
    td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .table-wrap {{ max-height: 640px; overflow: auto; border: 1px solid var(--line); border-radius: 8px; }}
    .table-wrap.consensus-table-wrap {{ flex: 1 1 0; min-height: 0; max-height: none; }}
    .table-code {{ color: var(--muted); font-size: 12px; }}
    .data-json {{ display: none; }}
    footer {{ margin-top: 24px; color: var(--muted); font-size: 12px; }}
    a {{ color: var(--accent-2); }}
    @media (max-width: 900px) {{
      .shell {{ padding: 18px 14px 34px; }}
      header, .main, .change-grid {{ grid-template-columns: 1fr; }}
      .audit {{ justify-self: start; text-align: left; }}
      .kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .bar-row {{ grid-template-columns: 32px minmax(110px, 1fr) 96px 58px; gap: 8px; }}
      .track {{ height: 10px; }}
    }}
    @media (max-width: 560px) {{
      h1 {{ font-size: 26px; }}
      .kpis {{ grid-template-columns: 1fr; }}
      .bar-row {{ grid-template-columns: 28px 1fr 54px; }}
      .bar-row .track {{ grid-column: 2 / 4; }}
      .toolbar {{ align-items: stretch; flex-direction: column; }}
      th, td {{ padding: 9px 8px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <h1>{_esc(page_title)}</h1>
        <p class="subtitle">以公開頁面資料建立的研究儀表板。多基金區塊顯示共識、分歧與同向變化；單基金拆解仍保留各基金自己的持股結構，不產生買賣、停損、停利或下單訊號。</p>
      </div>
      <div class="audit">
        <div>追蹤基金 <code>{len(multi_fund_view["fund_cards"])}</code></div>
        <div>FundCode <code>{_esc(fund_code)}</code></div>
        <div>資料更新 <code>{_esc(edit_datetime)}</code></div>
        <div>產生時間 <code>{_esc(generated_at)}</code></div>
      </div>
    </header>

    {multi_fund_section}

    <section class="grid kpis">
      {_kpi("持股檔數", f"{len(rows)}", "股票資產明細筆數")}
      {_kpi("股票權重合計", f"{total_weight:.2f}%", "DataAsset / ST 明細加總")}
      {_kpi("前十大權重", f"{top_10_weight:.2f}%", "觀察集中度，不代表交易建議")}
      {_kpi("最大單一持股", f"{max_weight:.2f}%", _esc(str(rows[0].get("stock_name", ""))))}
    </section>

    <section class="grid main">
      <div class="card">
        <h2>前十五大持股權重</h2>
        <div class="bar-list">
          {top_bars}
        </div>
      </div>
      <div class="side-stack">
        <div class="card consensus-card">
          <h2>權重區間分布</h2>
          {bucket_bars}
        </div>
        <div class="card consensus-card">
          <h2>幣別分布</h2>
          <div class="pill-row">{currency_pills}</div>
        </div>
        <div class="card notice">
          <h2>研究邊界</h2>
          <p>本頁只做公開持股資料整理。產業鏈分類、影子股池、3 日與 5 日變化需要累積多日快照後再啟用。</p>
        </div>
      </div>
    </section>

    {streaks_section}

    {changes_section}

    <section class="card" style="margin-top:18px;">
      <div class="toolbar">
        <h2 style="margin:0;">完整持股表</h2>
        <input id="searchInput" type="search" placeholder="搜尋股票代號或名稱">
      </div>
      <div class="table-wrap">
        <table id="holdingsTable">
          <thead>
            <tr>
              <th>排名</th>
              <th>股票</th>
              <th class="num">股數</th>
              <th class="num">市值</th>
              <th class="num">權重</th>
              <th>幣別</th>
            </tr>
          </thead>
          <tbody>
            {table_rows}
          </tbody>
        </table>
      </div>
    </section>

    <section class="card" style="margin-top:18px;">
      <h2>資料查核</h2>
      <p>來源：<a href="{_esc(source_url)}">{_esc(source_url)}</a></p>
      <p>CSV：<code>{_esc(str(source_csv))}</code></p>
      <p>抓取時間：<code>{_esc(fetched_at)}</code>；頁面資產時間：<code>{_esc(as_of_datetime)}</code></p>
      <p>股票明細市值合計：約 <code>{_format_number(total_market_value)}</code>。數字來自公開頁內嵌 DataAsset JSON，日後報告應保留 raw HTML 以便回查。</p>
    </section>

    <footer>
      active-etf-radar · 公開資料研究工具 · 非交易系統
    </footer>
  </div>
  <script id="dashboardData" type="application/json">{html.escape(json.dumps(payload, ensure_ascii=False))}</script>
  <script>
    const input = document.getElementById('searchInput');
    const table = document.getElementById('holdingsTable');
    input.addEventListener('input', () => {{
      const q = input.value.trim().toLowerCase();
      for (const row of table.tBodies[0].rows) {{
        const text = row.textContent.toLowerCase();
        row.style.display = text.includes(q) ? '' : 'none';
      }}
    }});
  </script>
</body>
</html>
"""


def _render_html(
    fund_detail_views: list[dict[str, Any]],
    active_etf_code: str,
    multi_fund_view: dict[str, Any],
) -> str:
    if not fund_detail_views:
        raise ValueError("沒有可顯示的 ETF 持股快照")

    available_codes = [str(view["rows"][0].get("etf_code", "")) for view in fund_detail_views if view["rows"]]
    active_code = active_etf_code if active_etf_code in available_codes else available_codes[0]
    generated_at = datetime.now().isoformat(timespec="seconds")
    page_title = "主動式 ETF 多基金雷達" if len(fund_detail_views) > 1 else f"{active_code} 主動式 ETF 持股雷達"
    multi_fund_section = _render_multi_fund_section(multi_fund_view)
    tabs = "\n".join(_render_fund_tab(view, active_code) for view in fund_detail_views)
    panels = "\n".join(_render_fund_detail_panel(view, active_code) for view in fund_detail_views)
    payload = {"funds": available_codes, "activeFund": active_code, "generatedAt": generated_at}

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(page_title)}</title>
  <style>
    :root {{
      --bg: #f6f8fb;
      --surface: #ffffff;
      --surface-2: #eef3f8;
      --text: #17212f;
      --muted: #657386;
      --line: #dce4ee;
      --accent: #0f7b72;
      --accent-2: #1f5eff;
      --warn: #b56a19;
      --good: #107c41;
      --shadow: 0 18px 48px rgba(23, 33, 47, 0.10);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "Noto Sans TC", "Microsoft JhengHei", Arial, sans-serif;
      line-height: 1.55;
    }}
    .shell {{ max-width: 1480px; margin: 0 auto; padding: 28px 24px 48px; }}
    header {{
      display: grid;
      grid-template-columns: 1.5fr 1fr;
      gap: 24px;
      align-items: end;
      margin-top: 18px;
      padding: 30px 30px 26px;
      background: radial-gradient(135% 170% at 0% 0%, #15406a 0%, #0d2742 46%, #07131f 100%);
      color: #eef4fb;
      border-radius: 14px 14px 0 0;
      box-shadow: var(--shadow);
    }}
    h1 {{ margin: 0; font-size: 34px; line-height: 1.15; letter-spacing: 0; }}
    h2 {{ margin: 0 0 16px; font-size: 20px; letter-spacing: 0; }}
    .subtitle {{ margin: 12px 0 0; color: var(--muted); font-size: 15px; max-width: 760px; }}
    header .subtitle {{ color: rgba(238, 244, 251, 0.78); }}
    .audit {{ display: grid; gap: 8px; justify-self: end; color: var(--muted); font-size: 13px; text-align: right; }}
    header .audit {{ color: rgba(238, 244, 251, 0.72); }}
    .audit code {{ color: var(--text); background: var(--surface-2); padding: 2px 6px; border-radius: 6px; }}
    header .audit code {{ color: #ffd9a0; background: rgba(255, 255, 255, 0.12); }}
    .grid {{ display: grid; gap: 18px; }}
    .kpis {{ grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 18px 0; }}
    .fund-grid {{ grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); margin: 18px 0; }}
    .event-grid {{ grid-template-columns: repeat(auto-fit, minmax(270px, 1fr)); align-items: stretch; gap: 14px; margin: 14px 0 18px; }}
    .event-radar-card {{ margin: 14px 0 18px; padding: 0; overflow: hidden; }}
    .event-radar-top {{ display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; padding: 20px 22px 14px; }}
    .event-radar-top h2 {{ margin-bottom: 0; }}
    .event-tabs {{ display: flex; gap: 8px; flex-wrap: wrap; padding: 0 22px 14px; border-bottom: 1px solid var(--line); }}
    .event-tab {{ display: inline-flex; align-items: center; gap: 8px; border: 1px solid var(--line); background: var(--surface); color: var(--muted); border-radius: 999px; padding: 9px 12px; font: inherit; font-size: 13px; font-weight: 800; cursor: pointer; }}
    .event-tab.active {{ background: var(--text); border-color: var(--text); color: #ffffff; }}
    .event-tab-count {{ display: inline-flex; align-items: center; justify-content: center; min-width: 28px; border-radius: 999px; padding: 2px 7px; background: var(--surface-2); color: var(--text); font-size: 12px; font-variant-numeric: tabular-nums; }}
    .event-tab.active .event-tab-count {{ background: rgba(255, 255, 255, 0.16); color: #ffd9a0; }}
    .event-panel {{ --event-accent: var(--accent); display: none; padding: 18px 22px 22px; }}
    .event-panel.active {{ display: block; }}
    .event-panel-note {{ margin: 0 0 12px; color: var(--muted); font-size: 12px; }}
    .event-radar-card .event-list {{ max-height: 520px; overflow: auto; padding-right: 4px; }}
    .event-radar-card .insight-row {{ grid-template-columns: minmax(0, 1fr) minmax(220px, auto); }}
    .focus-tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }}
    .focus-tag {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 8px; background: var(--surface-2); color: var(--muted); font-size: 12px; font-weight: 750; }}
    .focus-tag.hot {{ background: #fff7e8; color: #7a4c06; }}
    .focus-tag.new {{ background: #eef8f1; color: var(--good); }}
    .focus-tag.same {{ background: #ecfdf3; color: #067647; }}
    .focus-tag.unusual {{ background: #eff6ff; color: #175cd3; }}
    .focus-score {{ display: grid; gap: 6px; justify-items: end; min-width: 220px; }}
    .market-reaction {{ display: inline-flex; align-items: baseline; justify-content: flex-end; gap: 7px; min-width: 190px; border: 1px solid var(--line); border-radius: 7px; background: var(--surface); padding: 6px 9px; font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .market-reaction strong {{ font-size: 15px; }}
    .market-reaction.positive strong {{ color: var(--good); }}
    .market-reaction.negative strong {{ color: var(--bad); }}
    .market-reaction.flat strong, .market-reaction.unavailable strong {{ color: var(--muted); }}
    .market-label, .market-close {{ color: var(--muted); font-size: 11px; }}
    .market-reaction.unavailable {{ border-style: dashed; }}
    .event-card {{ --event-accent: var(--accent); display: flex; flex-direction: column; height: 248px; border-top: 4px solid var(--event-accent); box-shadow: var(--shadow); }}
    .event-card h3 {{ margin: 0; font-size: 19px; line-height: 1.25; letter-spacing: 0; }}
    .event-head {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 10px; }}
    .event-label {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 4px 9px; background: #eef8f1; background: color-mix(in srgb, var(--event-accent) 11%, #ffffff); color: var(--event-accent); font-size: 12px; font-weight: 800; white-space: nowrap; }}
    .event-label.neutral {{ background: var(--surface-2); color: var(--muted); }}
    .event-list {{ flex: 1 1 auto; min-height: 0; overflow: auto; padding-right: 4px; }}
    .event-empty {{ margin: 0; color: var(--muted); font-size: 14px; }}
    .event-new-buy {{ --event-accent: #0f7b72; }}
    .event-new-holding {{ --event-accent: #2563eb; }}
    .event-unusual {{ --event-accent: #b56a19; }}
    .event-same {{ --event-accent: #107c41; }}
    .card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow); padding: 18px; }}
    .kpi-label {{ color: var(--muted); font-size: 13px; }}
    .kpi-value {{ margin-top: 6px; font-size: 28px; font-weight: 750; line-height: 1.15; }}
    .kpi-note {{ margin-top: 8px; color: var(--muted); font-size: 12px; }}
    .main {{ grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.75fr); align-items: start; }}
    .multi-layout {{ grid-template-columns: minmax(720px, 1fr) minmax(390px, 0.58fr); gap: 24px; align-items: stretch; }}
    .consensus-card {{ display: flex; flex-direction: column; min-height: 100%; }}
    .consensus-card .toolbar {{ flex: 0 0 auto; }}
    .consensus-table-wrap {{ flex: 1 1 auto; min-height: 0; max-height: none; }}
    .bar-list {{ display: grid; gap: 10px; }}
    .bar-row {{ display: grid; grid-template-columns: 40px minmax(140px, 220px) minmax(180px, 1fr) 70px; gap: 12px; align-items: center; font-size: 14px; }}
    .rank {{ color: var(--muted); font-variant-numeric: tabular-nums; }}
    .name strong {{ display: block; font-size: 14px; }}
    .name span {{ color: var(--muted); font-size: 12px; }}
    .track {{ height: 12px; background: var(--surface-2); border-radius: 999px; overflow: hidden; }}
    .fill {{ height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-2)); border-radius: inherit; }}
    .value {{ text-align: right; font-variant-numeric: tabular-nums; font-weight: 700; }}
    .side-stack {{ display: grid; gap: 16px; align-content: start; }}
    .side-stack .card {{ padding: 20px 22px; }}
    .side-stack h2 {{ margin-bottom: 14px; }}
    .bucket {{ display: grid; grid-template-columns: 86px 1fr 42px; gap: 10px; align-items: center; margin: 10px 0; font-size: 14px; }}
    .bucket .track {{ height: 10px; }}
    .pill-row {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .pill {{ border: 1px solid var(--line); background: var(--surface-2); border-radius: 999px; padding: 6px 10px; font-size: 13px; color: var(--muted); }}
    .weight-tags {{ display: grid; gap: 6px; justify-items: start; min-width: 180px; }}
    .insight-row .weight-tags {{ justify-items: end; min-width: 0; }}
    .insight-row .market-reaction {{ margin-bottom: 2px; }}
    .weight-tag {{ display: inline-flex; align-items: center; gap: 4px; border: 1px solid var(--line); border-radius: 999px; background: var(--surface-2); padding: 4px 8px; font-size: 12px; white-space: nowrap; }}
    .weight-tag strong {{ color: var(--text); font-variant-numeric: tabular-nums; }}
    .tag-note {{ color: var(--good); font-size: 11px; font-style: normal; font-weight: 800; }}
    .notice {{ border-left: 4px solid var(--warn); background: #fff8ef; color: #5c3b14; }}
    .fund-chip {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 8px; background: var(--surface-2); color: var(--muted); font-size: 12px; }}
    .status-tag {{ display: inline-flex; align-items: center; margin-left: 8px; border-radius: 999px; padding: 2px 7px; background: #eaf7f0; color: var(--good); font-size: 12px; font-weight: 750; white-space: nowrap; }}
    .count-main {{ display: block; font-weight: 750; }}
    .count-detail {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 4px; margin-top: 5px; color: var(--muted); font-size: 11px; }}
    .count-detail span {{ display: inline-flex; border-radius: 999px; padding: 1px 5px; background: var(--surface-2); }}
    .pending-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; margin: -6px 0 18px; }}
    .pending-card {{ background: var(--surface); border: 1px dashed var(--line); border-radius: 8px; padding: 14px 16px; }}
    .pending-card strong {{ display: block; margin-bottom: 4px; }}
    .section-title {{ display: flex; justify-content: space-between; gap: 12px; align-items: end; margin: 22px 0 12px; }}
    .change-grid {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(300px, 0.45fr); gap: 18px; align-items: start; }}
    .change-summary {{ display: grid; gap: 10px; }}
    .change-item {{ display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); padding: 8px 0; font-size: 14px; }}
    .change-item strong {{ font-variant-numeric: tabular-nums; }}
    .insight-list {{ display: grid; gap: 8px; }}
    .insight-row {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: center; border: 1px solid transparent; border-radius: 8px; background: var(--surface-3); padding: 10px 11px; font-size: 14px; }}
    .insight-row:first-child {{ padding-top: 10px; }}
    .insight-row:last-child {{ border-bottom: 1px solid transparent; padding-bottom: 10px; }}
    .insight-stock {{ min-width: 0; }}
    .insight-stock strong {{ display: block; font-size: 15px; line-height: 1.35; }}
    .insight-value {{ color: var(--good); font-weight: 800; font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .unusual-card {{ display: flex; flex-direction: column; }}
    .unusual-scroll {{ max-height: 360px; overflow: auto; padding-right: 4px; }}
    .unusual-row {{ align-items: start; padding: 13px 0; }}
    .unusual-metrics {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; max-width: 280px; }}
    .metric-chip {{ display: inline-flex; align-items: center; gap: 4px; border: 1px solid var(--line); border-radius: 999px; background: var(--surface-2); padding: 3px 8px; font-size: 12px; white-space: nowrap; }}
    .metric-chip strong {{ color: var(--text); font-variant-numeric: tabular-nums; }}
    .metric-chip.delta {{ color: var(--good); background: #eef8f1; border-color: #cfe8d8; }}
    .metric-chip.ratio {{ color: #7a4c06; background: #fff7e8; border-color: #f1d7a8; }}
    .metric-chip.status {{ color: var(--muted); }}
    .streak-badge {{ display: inline-flex; align-items: center; justify-content: center; min-width: 44px; padding: 4px 8px; border-radius: 999px; background: #eaf7f0; color: var(--good); font-weight: 750; }}
    .date-range {{ color: var(--muted); font-size: 12px; }}
    .positive {{ color: var(--good); }}
    .negative {{ color: #b42318; }}
    .today-summary {{ display: flex; align-items: center; gap: 12px; margin: 0 0 22px; padding: 15px 30px 17px; background: linear-gradient(180deg, #07131f, #0a1f33); color: #f4f8fc; border-top: 1px solid rgba(255, 255, 255, 0.08); border-radius: 0 0 14px 14px; box-shadow: var(--shadow); }}
    .today-tag {{ flex: 0 0 auto; display: inline-flex; align-items: center; border-radius: 999px; padding: 4px 11px; background: rgba(255, 255, 255, 0.14); color: #ffd9a0; font-size: 12px; font-weight: 800; letter-spacing: 0.04em; }}
    .today-body {{ font-size: 15.5px; line-height: 1.5; }}
    .today-body b {{ color: #ffd9a0; font-variant-numeric: tabular-nums; font-weight: 800; }}
    .spark {{ display: block; }}
    .spark-cell {{ width: 96px; }}
    .spark-empty {{ color: var(--muted); }}
    .insight-stock .spark {{ margin-top: 6px; opacity: 0.92; }}
    .event-list .insight-row:first-child {{ box-shadow: inset 3px 0 0 var(--event-accent); background: color-mix(in srgb, var(--event-accent) 7%, var(--surface)); border-radius: 8px; padding-left: 9px; }}
    .toolbar {{ display: flex; gap: 10px; align-items: center; justify-content: space-between; margin-bottom: 12px; }}
    .toolbar input {{ width: min(360px, 100%); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; font: inherit; color: var(--text); background: var(--surface); outline: none; }}
    .toolbar input:focus-visible, .tab-button:focus-visible, .event-tab:focus-visible {{ border-color: var(--accent-2); box-shadow: 0 0 0 3px rgba(31, 94, 255, 0.14); outline: none; }}
    .fund-tabs {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0 18px; padding: 6px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); }}
    .tab-button {{ border: 1px solid transparent; background: transparent; color: var(--muted); border-radius: 6px; padding: 10px 14px; font: inherit; font-weight: 700; cursor: pointer; }}
    .tab-button.active {{ background: var(--text); color: #fff; }}
    .fund-panel {{ display: none; }}
    .fund-panel.active {{ display: block; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    #consensusTable {{ min-width: 1020px; }}
    th, td {{ padding: 11px 10px; border-bottom: 1px solid var(--line); text-align: left; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 700; background: #f9fbfd; position: sticky; top: 0; z-index: 1; }}
    td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .table-wrap {{ max-height: 640px; overflow: auto; border: 1px solid var(--line); border-radius: 8px; }}
    .table-wrap.consensus-table-wrap {{ flex: 1 1 auto; min-height: 0; max-height: 640px; }}
    .table-code {{ color: var(--muted); font-size: 12px; }}
    footer {{ margin-top: 24px; color: var(--muted); font-size: 12px; }}
    a {{ color: var(--accent-2); }}
    @media (max-width: 900px) {{
      .shell {{ padding: 18px 14px 34px; }}
      header, .main, .multi-layout, .change-grid, .event-grid {{ grid-template-columns: 1fr; }}
      .audit {{ justify-self: start; text-align: left; }}
      .kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .bar-row {{ grid-template-columns: 32px minmax(110px, 1fr) 96px 58px; gap: 8px; }}
      .track {{ height: 10px; }}
      .consensus-card {{ min-height: auto; }}
      .consensus-table-wrap {{ max-height: 520px; }}
      .event-radar-top {{ flex-direction: column; padding: 18px 18px 12px; }}
      .event-tabs {{ padding: 0 18px 12px; }}
      .event-panel {{ padding: 16px 18px 18px; }}
      .event-radar-card .event-list {{ max-height: 380px; }}
      .focus-score {{ justify-items: start; min-width: 0; }}
      .event-card {{ height: auto; max-height: none; }}
      .event-list {{ max-height: 220px; }}
      .unusual-metrics {{ justify-content: flex-start; max-width: none; }}
    }}
    @media (max-width: 560px) {{
      h1 {{ font-size: 26px; }}
      .kpis {{ grid-template-columns: 1fr; }}
      .bar-row {{ grid-template-columns: 28px 1fr 54px; }}
      .bar-row .track {{ grid-column: 2 / 4; }}
      .toolbar {{ align-items: stretch; flex-direction: column; }}
      .tab-button {{ flex: 1 1 140px; }}
      .event-tab {{ flex: 1 1 150px; justify-content: space-between; }}
      .event-radar-card .insight-row {{ grid-template-columns: 1fr; gap: 8px; }}
      .insight-row, .unusual-row {{ grid-template-columns: 1fr; gap: 7px; }}
      th, td {{ padding: 9px 8px; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      * {{ scroll-behavior: auto !important; transition: none !important; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <h1>{_esc(page_title)}</h1>
        <p class="subtitle">首頁先看事件雷達：今日焦點會把新進持股、集體加碼與單基金異常合併成股票清單；共識總表與單基金明細往下放，當作查詢與回查用。</p>
      </div>
      <div class="audit">
        <div>追蹤基金 <code>{len(fund_detail_views)}</code></div>
        <div>作用分頁 <code>{_esc(active_code)}</code></div>
        <div>產生時間 <code>{_esc(generated_at)}</code></div>
      </div>
    </header>

    {multi_fund_section}

    <section>
      <div class="section-title">
        <div>
          <h2 style="margin:0;">單基金拆解</h2>
          <div class="kpi-note">每個分頁只顯示該 ETF 自己的持股、變化、連續增加與資料查核。</div>
        </div>
      </div>
      <div class="fund-tabs" role="tablist" aria-label="單基金拆解分頁">
        {tabs}
      </div>
      {panels}
    </section>

    <footer>
      active-etf-radar · 公開資料研究工具 · 非交易系統
    </footer>
  </div>
  <script id="dashboardData" type="application/json">{html.escape(json.dumps(payload, ensure_ascii=False))}</script>
  <script>
    const eventButtons = document.querySelectorAll('[data-event-tab]');
    const eventPanels = document.querySelectorAll('[data-event-panel]');
    eventButtons.forEach((button) => {{
      button.addEventListener('click', () => {{
        const target = button.dataset.eventTab;
        eventButtons.forEach((item) => {{
          const active = item.dataset.eventTab === target;
          item.classList.toggle('active', active);
          item.setAttribute('aria-selected', active ? 'true' : 'false');
        }});
        eventPanels.forEach((panel) => {{
          const active = panel.dataset.eventPanel === target;
          panel.classList.toggle('active', active);
          panel.hidden = !active;
        }});
      }});
    }});

    const tabButtons = document.querySelectorAll('[data-fund-tab]');
    const fundPanels = document.querySelectorAll('[data-fund-panel]');
    tabButtons.forEach((button) => {{
      button.addEventListener('click', () => {{
        const target = button.dataset.fundTab;
        tabButtons.forEach((item) => {{
          const active = item.dataset.fundTab === target;
          item.classList.toggle('active', active);
          item.setAttribute('aria-selected', active ? 'true' : 'false');
        }});
        fundPanels.forEach((panel) => {{
          const active = panel.dataset.fundPanel === target;
          panel.classList.toggle('active', active);
          panel.hidden = !active;
        }});
      }});
    }});

    document.querySelectorAll('[data-search-table]').forEach((input) => {{
      const table = document.getElementById(input.dataset.searchTable);
      input.addEventListener('input', () => {{
        const q = input.value.trim().toLowerCase();
        for (const row of table.tBodies[0].rows) {{
          const text = row.textContent.toLowerCase();
          row.style.display = text.includes(q) ? '' : 'none';
        }}
      }});
    }});
  </script>
</body>
</html>
"""


def _bucket_counts(rows: list[dict[str, Any]]) -> list[tuple[str, int]]:
    buckets = [(">5%", 0), ("3-5%", 0), ("1-3%", 0), ("<1%", 0)]
    counts = dict(buckets)
    for row in rows:
        weight = row["weight_num"]
        if weight > 5:
            counts[">5%"] += 1
        elif weight >= 3:
            counts["3-5%"] += 1
        elif weight >= 1:
            counts["1-3%"] += 1
        else:
            counts["<1%"] += 1
    return [(label, counts[label]) for label, _ in buckets]


def _render_bar(row: dict[str, Any], rank: int, max_weight: float) -> str:
    width = 0 if max_weight == 0 else row["weight_num"] / max_weight * 100
    return f"""
      <div class="bar-row">
        <div class="rank">{rank:02d}</div>
        <div class="name"><strong>{_esc(str(row["stock_name"]))}</strong><span>{_esc(str(row["stock_code"]))}</span></div>
        <div class="track"><div class="fill" style="width:{width:.2f}%"></div></div>
        <div class="value">{row["weight_num"]:.2f}%</div>
      </div>
    """


def _render_bucket_bar(label: str, count: int, total: int) -> str:
    width = 0 if total == 0 else count / total * 100
    return f"""
      <div class="bucket">
        <div>{_esc(label)}</div>
        <div class="track"><div class="fill" style="width:{width:.2f}%"></div></div>
        <div class="value">{count}</div>
      </div>
    """


def _render_table_row(row: dict[str, Any], rank: int, series: list[float] | None = None) -> str:
    sparkline = _render_sparkline(series or [], small=True)
    return f"""
      <tr>
        <td class="table-code">{rank:02d}</td>
        <td><strong>{_esc(str(row["stock_name"]))}</strong><div class="table-code">{_esc(str(row["stock_code"]))}</div></td>
        <td class="num">{_format_number(row["shares_num"])}</td>
        <td class="num">{_format_number(row["market_value_num"])}</td>
        <td class="num">{row["weight_num"]:.2f}%</td>
        <td>{_esc(str(row["currency"]))}</td>
        <td class="spark-cell">{sparkline}</td>
      </tr>
    """


def _render_fund_tab(view: dict[str, Any], active_etf_code: str) -> str:
    first = view["rows"][0]
    etf_code = str(first.get("etf_code", ""))
    active = etf_code == active_etf_code
    active_class = " active" if active else ""
    selected = "true" if active else "false"
    return (
        f'<button class="tab-button{active_class}" type="button" role="tab" '
        f'aria-selected="{selected}" data-fund-tab="{_esc(etf_code)}">'
        f'{_esc(etf_code)}</button>'
    )


def _render_fund_detail_panel(view: dict[str, Any], active_etf_code: str) -> str:
    rows = view["rows"]
    first = rows[0]
    etf_code = str(first.get("etf_code", ""))
    fund_code = str(first.get("fund_code", ""))
    fetched_at = str(first.get("fetched_at", ""))
    as_of_datetime = str(first.get("as_of_datetime", ""))
    edit_datetime = str(first.get("edit_datetime", ""))
    source_url = str(first.get("source_url", ""))
    source_csv = view["source_csv"]
    changes_path = view["changes_path"]
    changes = view["changes"]
    streaks_path = view["streaks_path"]
    streaks = view["streaks"]
    snapshot_dates = view["snapshot_dates"]
    fund_series = view.get("share_series", {})

    total_weight = sum(row["weight_num"] for row in rows)
    top_10_weight = sum(row["weight_num"] for row in rows[:10])
    max_weight = rows[0]["weight_num"]
    total_market_value = sum(row["market_value_num"] for row in rows)
    bucket_counts = _bucket_counts(rows)
    currency_counts = Counter(str(row.get("currency", "")) for row in rows)
    top_bars = "\n".join(_render_bar(row, index + 1, max_weight) for index, row in enumerate(rows[:15]))
    bucket_bars = "\n".join(_render_bucket_bar(label, count, len(rows)) for label, count in bucket_counts)
    currency_pills = "\n".join(
        f'<span class="pill">{_esc(currency or "未標示")} · {count}</span>'
        for currency, count in sorted(currency_counts.items())
    )
    table_rows = "\n".join(
        _render_table_row(row, index + 1, fund_series.get(str(row.get("stock_code", ""))))
        for index, row in enumerate(rows)
    )
    streaks_section = _render_streaks_section(streaks, streaks_path, snapshot_dates)
    changes_section = _render_changes_section(changes, changes_path)
    panel_class = "fund-panel active" if etf_code == active_etf_code else "fund-panel"
    hidden = "" if etf_code == active_etf_code else " hidden"
    table_id = f"holdingsTable-{etf_code}"

    return f"""
      <section class="{panel_class}" data-fund-panel="{_esc(etf_code)}"{hidden}>
        <div class="section-title">
          <div>
            <h2 style="margin:0;">{_esc(etf_code)} 單基金拆解</h2>
            <div class="kpi-note">FundCode {fund_code} · 資料日 {as_of_datetime}</div>
          </div>
        </div>

        <section class="grid kpis">
          {_kpi("持股檔數", f"{len(rows)}", "股票資產明細筆數")}
          {_kpi("股票權重合計", f"{total_weight:.2f}%", "DataAsset / ST 明細加總")}
          {_kpi("前十大權重", f"{top_10_weight:.2f}%", "觀察集中度，不代表交易建議")}
          {_kpi("最大單一持股", f"{max_weight:.2f}%", _esc(str(rows[0].get("stock_name", ""))))}
        </section>

        <section class="grid main">
          <div class="card">
            <h2>前十五大持股權重</h2>
            <div class="bar-list">{top_bars}</div>
          </div>
          <div class="side-stack">
            <div class="card">
              <h2>權重區間分布</h2>
              {bucket_bars}
            </div>
            <div class="card">
              <h2>幣別分布</h2>
              <div class="pill-row">{currency_pills}</div>
            </div>
            <div class="card notice">
              <h2>研究邊界</h2>
              <p>本頁只做公開持股資料整理。產業鏈分類、影子股池、3 日與 5 日變化需要累積多日快照後再啟用。</p>
            </div>
          </div>
        </section>

        {streaks_section}
        {changes_section}

        <section class="card" style="margin-top:18px;">
          <div class="toolbar">
            <h2 style="margin:0;">完整持股表</h2>
            <input type="search" placeholder="搜尋股票代號或名稱" data-search-table="{_esc(table_id)}">
          </div>
          <div class="table-wrap">
            <table id="{_esc(table_id)}">
              <thead>
                <tr>
                  <th>排名</th>
                  <th>股票</th>
                  <th class="num">股數</th>
                  <th class="num">市值</th>
                  <th class="num">權重</th>
                  <th>幣別</th>
                  <th>近期股數趨勢</th>
                </tr>
              </thead>
              <tbody>{table_rows}</tbody>
            </table>
          </div>
        </section>

        <section class="card" style="margin-top:18px;">
          <h2>資料查核</h2>
          <p>來源：<a href="{_esc(source_url)}">{_esc(source_url)}</a></p>
          <p>CSV：<code>{_esc(str(source_csv))}</code></p>
          <p>抓取時間：<code>{_esc(fetched_at)}</code>；頁面資產時間：<code>{_esc(as_of_datetime)}</code>；更新時間：<code>{_esc(edit_datetime)}</code></p>
          <p>股票明細市值合計：約 <code>{_format_number(total_market_value)}</code>。數字來自公開頁資料，日後報告應保留 raw JSON/HTML 以便回查。</p>
        </section>
      </section>
    """


def _render_multi_fund_section(view: dict[str, Any]) -> str:
    fund_cards = view["fund_cards"]
    if len(fund_cards) < 2:
        return ""

    consensus_rows = view["consensus_rows"]
    share_series = view.get("share_series", {})
    market_prices = view.get("market_prices", {})
    card_html = "\n".join(_render_fund_card(card) for card in fund_cards)
    pending_html = "\n".join(_render_pending_fund_card(card) for card in view.get("pending_funds", []))
    pending_section = f'<div class="pending-grid">{pending_html}</div>' if pending_html else ""
    source_date_note = "；".join(
        f'{_esc(str(card["etf_code"]))} {_esc(str(card["as_of_date"]))}' for card in fund_cards
    )
    new_holding_rows = view.get("new_holding_rows", [])
    new_buy_consensus_items = _build_new_buy_consensus_items(new_holding_rows, consensus_rows)
    new_buy_consensus_codes = {str(item["stock_code"]) for item in new_buy_consensus_items}
    consensus_table = "\n".join(
        _render_consensus_row(
            row,
            is_new_buy_consensus=str(row.get("stock_code", "")) in new_buy_consensus_codes,
            series=share_series.get(str(row.get("stock_code", ""))),
        )
        for row in consensus_rows
    )
    if not consensus_table:
        consensus_table = '<tr><td colspan="8">目前沒有至少 2 檔基金共同持有的股票。</td></tr>'
    same_increase_rows = sorted(
        (row for row in consensus_rows if int(row["same_share_increase_count"]) >= 2),
        key=lambda row: (
            int(row["same_share_increase_count"]),
            int(row.get("active_increase_streak_fund_count", 0) or 0),
            float(row.get("max_weight", 0) or 0),
        ),
        reverse=True,
    )
    focus_items = _build_today_focus_items(
        new_buy_consensus_items,
        new_holding_rows,
        view.get("unusual_increase_rows", []),
        same_increase_rows,
        consensus_rows,
    )
    today_focus_list = _render_today_focus_list(focus_items, share_series, market_prices)
    new_holding_list = _render_new_holding_list(new_holding_rows, new_buy_consensus_codes, market_prices)
    increase_list = "\n".join(
        _render_same_direction_item(
            row,
            share_series.get(str(row.get("stock_code", ""))),
            market_prices,
        )
        for row in same_increase_rows
    )
    if not increase_list:
        increase_list = '<p class="kpi-note">最新兩個資料日沒有多基金同向股數增加。</p>'
    unusual_increase_list = _render_unusual_increase_list(
        view.get("unusual_increase_rows", []),
        share_series,
        market_prices,
    )
    new_holding_stock_count = len({str(row.get("stock_code", "")) for row in new_holding_rows})
    unusual_stock_count = len({str(row.get("stock_code", "")) for row in view.get("unusual_increase_rows", [])})
    event_tabs = [
        {
            "id": "focus",
            "title": "今日焦點",
            "count": len(focus_items),
            "unit": "檔",
            "css": "event-new-buy",
            "note": "把四種事件合併成股票焦點；行情採事件資料日收盤，僅供觀察價格反應，不代表持股變化造成漲跌。",
            "body": today_focus_list,
        },
        {
            "id": "new-holding",
            "title": "新進持股",
            "count": new_holding_stock_count,
            "unit": "檔",
            "css": "event-new-holding",
            "note": "只列全體最新資料日第一次出現在基金持股裡的股票；右側顯示該資料日收盤與漲跌幅。",
            "body": new_holding_list,
        },
        {
            "id": "same",
            "title": "集體加碼",
            "count": len(same_increase_rows),
            "unit": "檔",
            "css": "event-same",
            "note": "最新兩個資料日，至少 2 檔基金同時增加股數的股票；行情依實際事件資料日對照。",
            "body": increase_list,
        },
        {
            "id": "unusual",
            "title": "單基金異常",
            "count": unusual_stock_count,
            "unit": "檔",
            "css": "event-unusual",
            "note": "單一基金權重增加 >= 0.30pp，或既有持股權重放大 >= 1.5 倍；右側同步顯示當日價格反應。",
            "body": unusual_increase_list,
        },
    ]
    active_event_id = next((tab["id"] for tab in event_tabs if int(tab["count"]) > 0), "focus")
    event_tab_buttons = "\n".join(
        (
            f'<button class="event-tab{" active" if tab["id"] == active_event_id else ""}" type="button" '
            f'aria-selected="{"true" if tab["id"] == active_event_id else "false"}" '
            f'aria-controls="event-panel-{_esc(str(tab["id"]))}" data-event-tab="{_esc(str(tab["id"]))}">'
            f'<span>{_esc(str(tab["title"]))}</span>'
            f'<span class="event-tab-count">{int(tab["count"])} {_esc(str(tab["unit"]))}</span>'
            "</button>"
        )
        for tab in event_tabs
    )
    event_panels = "\n".join(
        (
            f'<div class="event-panel {tab["css"]}{" active" if tab["id"] == active_event_id else ""}" '
            f'id="event-panel-{_esc(str(tab["id"]))}" data-event-panel="{_esc(str(tab["id"]))}" '
            f'{"hidden" if tab["id"] != active_event_id else ""}>'
            f'<p class="event-panel-note">{_esc(str(tab["note"]))}</p>'
            f'<div class="event-list insight-list">{tab["body"]}</div>'
            "</div>"
        )
        for tab in event_tabs
    )
    today_summary = _build_today_summary(
        new_buy_consensus_items,
        new_holding_rows,
        view.get("unusual_increase_rows", []),
        same_increase_rows,
    )

    return f"""
    {today_summary}

    <section>
      <div class="section-title">
        <div>
          <h2 style="margin:0;">事件雷達</h2>
          <div class="kpi-note">先看今日焦點，再切到新進持股、集體加碼與單基金異常。各基金採各自最新公開資料日：{source_date_note}。</div>
        </div>
      </div>
      <div class="card event-radar-card">
        <div class="event-radar-top">
          <div>
            <h3 style="margin:0;">事件清單</h3>
            <div class="kpi-note">同一檔股票跨事件時集中到今日焦點；漲跌幅是持股資料日的收盤變化，只能用來比對相關性，不能直接推論因果。</div>
          </div>
        </div>
        <div class="event-tabs" role="tablist" aria-label="事件雷達細項">
          {event_tab_buttons}
        </div>
        {event_panels}
      </div>
    </section>

    <section>
      <div class="section-title">
        <div>
          <h2 style="margin:0;">基金資料日</h2>
          <div class="kpi-note">每張卡是該基金自己的最新公開快照，資料日不同時仍分開標示。</div>
        </div>
      </div>
      <div class="grid fund-grid">{card_html}</div>
      {pending_section}
    </section>

    <section class="card consensus-card" style="margin-top:18px;">
      <div class="toolbar">
        <div>
          <h2 style="margin:0;">共識持股總表</h2>
          <div class="kpi-note">顯示全部 {len(consensus_rows)} 筆共同持有股票，可搜尋代號、名稱或基金代號；事件卡標的會標示「新買進共識」，新進入共識者另標示「首次共識」。</div>
        </div>
        <input type="search" placeholder="搜尋股票或基金" data-search-table="consensusTable">
      </div>
      <div class="table-wrap consensus-table-wrap">
        <table id="consensusTable">
          <thead>
            <tr>
              <th>股票</th>
              <th class="num">持有基金數</th>
              <th>各基金權重</th>
              <th class="num">平均權重</th>
              <th class="num">最高權重</th>
              <th class="num">最新加股基金數</th>
              <th class="num">連續加股基金數</th>
              <th>近期股數趨勢</th>
            </tr>
          </thead>
          <tbody>
            {consensus_table}
          </tbody>
        </table>
      </div>
    </section>
    """


def _render_pending_fund_card(card: dict[str, Any]) -> str:
    return f"""
      <div class="pending-card">
        <strong>{_esc(str(card["etf_code"]))} {_esc(str(card["fund_name"]))}</strong>
        <div class="kpi-note">{_esc(str(card["status"]))}</div>
        <div class="kpi-note">來源：<a href="{_esc(str(card["source_url"]))}">{_esc(str(card["manager"]))}</a></div>
      </div>
    """


def _render_fund_card(card: dict[str, Any]) -> str:
    return f"""
      <div class="card">
        <div class="kpi-label">{_esc(str(card["fund_name"]))}</div>
        <div class="kpi-value">{_esc(str(card["etf_code"]))}</div>
        <div class="kpi-note">資料日 {card["as_of_date"]} · 持股 {card["holding_count"]} 檔</div>
        <div class="change-item"><span>股票權重</span><strong>{float(card["total_weight"]):.2f}%</strong></div>
        <div class="change-item"><span>前十大</span><strong>{float(card["top10_weight"]):.2f}%</strong></div>
        <div class="change-item"><span>最新加/減</span><strong>{card["share_increase_count"]} / {card["share_decrease_count"]}</strong></div>
      </div>
    """


def _render_consensus_row(
    row: dict[str, Any],
    is_new_buy_consensus: bool = False,
    series: list[float] | None = None,
) -> str:
    weight_tags = _render_fund_weight_tags(row)
    increase_funds = _fund_codes_by_status(row, "股數增加")
    streak_funds = _fund_codes_with_streak(row)
    new_tag = '<span class="status-tag">首次共識</span>' if _is_new_consensus(row) else ""
    new_buy_tag = '<span class="status-tag">新買進共識</span>' if is_new_buy_consensus else ""
    sparkline = _render_sparkline(series or [])
    return f"""
      <tr>
        <td><strong>{_esc(str(row["stock_name"]))}</strong>{new_buy_tag}{new_tag}<div class="table-code">{_esc(str(row["stock_code"]))}</div></td>
        <td class="num"><span class="fund-chip">{row["holding_fund_count"]} 檔</span></td>
        <td><div class="weight-tags">{weight_tags}</div></td>
        <td class="num">{float(row["average_weight"]):.2f}%</td>
        <td class="num">{float(row["max_weight"]):.2f}%</td>
        <td class="num positive">{_render_fund_count_detail(int(row["same_share_increase_count"]), increase_funds)}</td>
        <td class="num positive">{_render_fund_count_detail(int(row["active_increase_streak_fund_count"]), streak_funds)}</td>
        <td class="spark-cell">{sparkline}</td>
      </tr>
    """


def _is_new_consensus(row: dict[str, Any]) -> bool:
    return row.get("is_new_consensus") in (True, "True", "true", "1", 1)


def _fund_codes_by_status(row: dict[str, Any], status: str) -> list[str]:
    return sorted(
        str(fund_code)
        for fund_code, fund_status in row.get("fund_share_status", {}).items()
        if fund_status == status
    )


def _fund_codes_with_streak(row: dict[str, Any]) -> list[str]:
    return sorted(
        str(fund_code)
        for fund_code, streak in row.get("fund_streaks", {}).items()
        if int(streak or 0) > 0
    )


def _render_fund_count_detail(count: int, fund_codes: list[str]) -> str:
    if count <= 0:
        return "0"
    fund_list = "".join(f"<span>{_esc(fund_code)}</span>" for fund_code in fund_codes)
    return f'<span class="count-main">{count} 檔</span><div class="count-detail">{fund_list}</div>'


def _render_fund_weight_tags(row: dict[str, Any]) -> str:
    tags = []
    for fund_code, weight in sorted(row["fund_weights"].items()):
        status = row["fund_share_status"].get(fund_code, "")
        marker = ""
        css_class = ""
        if status == "股數增加":
            marker = " ↑"
            css_class = " positive"
        elif status == "新增":
            marker = " 新"
            css_class = " positive"
        elif status == "股數減少":
            marker = " ↓"
            css_class = " negative"
        tags.append(
            f'<span class="weight-tag{css_class}">{_esc(str(fund_code))} <strong>{_format_pct(float(weight))}</strong>{marker}</span>'
        )
    return "".join(tags)


def _format_pct(value: float) -> str:
    if 0 < abs(value) < 0.01:
        return f"{value:.4f}%"
    return f"{value:.2f}%"


def _build_today_focus_items(
    new_buy_items: list[dict[str, Any]],
    new_holding_rows: list[dict[str, Any]],
    unusual_rows: list[dict[str, Any]],
    same_rows: list[dict[str, Any]],
    consensus_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    consensus_by_code = {str(row.get("stock_code", "")): row for row in consensus_rows}
    items: dict[str, dict[str, Any]] = {}

    def get_item(stock_code: str, stock_name: str = "") -> dict[str, Any]:
        consensus = consensus_by_code.get(stock_code, {})
        item = items.setdefault(
            stock_code,
            {
                "stock_code": stock_code,
                "stock_name": consensus.get("stock_name", "") or stock_name,
                "signals": set(),
                "new_buy_count": 0,
                "new_holding_count": 0,
                "unusual_count": 0,
                "same_count": 0,
                "holding_fund_count": int(consensus.get("holding_fund_count", 0) or 0),
                "average_weight": float(consensus.get("average_weight", 0) or 0),
                "max_delta": 0.0,
                "score": 0.0,
                "event_dates": set(),
            },
        )
        if not item["stock_name"] and stock_name:
            item["stock_name"] = stock_name
        return item

    for row in new_buy_items:
        code = str(row.get("stock_code", ""))
        item = get_item(code, str(row.get("stock_name", "")))
        count = len(row.get("new_buy_fund_codes", set())) or len(row.get("funds", []))
        item["signals"].add("new_buy")
        item["new_buy_count"] = max(int(item["new_buy_count"]), count)
        item["score"] += 14 + count * 2

    grouped_new: dict[str, set[str]] = {}
    for row in new_holding_rows:
        code = str(row.get("stock_code", ""))
        item = get_item(code, str(row.get("stock_name", "")))
        grouped_new.setdefault(code, set()).add(str(row.get("etf_code", "")))
        item["signals"].add("new_holding")
        item["event_dates"].add(str(row.get("new_as_of_datetime", "")).replace("/", "-")[:10])
        item["score"] += 6 + float(row.get("new_weight_pct", 0) or 0)
    for code, funds in grouped_new.items():
        items[code]["new_holding_count"] = len([fund for fund in funds if fund])

    grouped_unusual: dict[str, list[dict[str, Any]]] = {}
    for row in unusual_rows:
        code = str(row.get("stock_code", ""))
        item = get_item(code, str(row.get("stock_name", "")))
        grouped_unusual.setdefault(code, []).append(row)
        item["signals"].add("unusual")
        item["event_dates"].add(str(row.get("new_as_of_datetime", "")).replace("/", "-")[:10])
        delta = float(row.get("weight_change_pct", 0) or 0)
        item["max_delta"] = max(float(item["max_delta"]), delta)
        item["score"] += 2 + delta
    for code, rows in grouped_unusual.items():
        items[code]["unusual_count"] = len(rows)

    for row in same_rows:
        code = str(row.get("stock_code", ""))
        item = get_item(code, str(row.get("stock_name", "")))
        same_count = int(row.get("same_share_increase_count", 0) or 0)
        item["signals"].add("same")
        item["same_count"] = same_count
        for fund_code, status in row.get("fund_share_status", {}).items():
            if status == "股數增加":
                event_date = str(row.get("fund_as_of_dates", {}).get(fund_code, ""))
                if event_date:
                    item["event_dates"].add(event_date)
        item["holding_fund_count"] = int(row.get("holding_fund_count", item["holding_fund_count"]) or 0)
        item["average_weight"] = float(row.get("average_weight", item["average_weight"]) or 0)
        item["score"] += 8 + same_count * 2

    focus_items = [
        item
        for item in items.values()
        if (
            len(item["signals"]) >= 2
            or "new_buy" in item["signals"]
            or "same" in item["signals"]
            or "new_holding" in item["signals"]
        )
    ]
    if len(focus_items) < 8:
        selected_codes = {str(item["stock_code"]) for item in focus_items}
        unusual_only = [item for item in items.values() if str(item["stock_code"]) not in selected_codes]
        unusual_only.sort(key=lambda item: (float(item["max_delta"]), float(item["score"])), reverse=True)
        focus_items.extend(unusual_only[: 8 - len(focus_items)])

    focus_items.sort(
        key=lambda item: (
            len(item["signals"]),
            float(item["score"]),
            int(item["same_count"]),
            int(item["holding_fund_count"]),
            float(item["max_delta"]),
        ),
        reverse=True,
    )
    return focus_items[:18]


def _render_today_focus_list(
    items: list[dict[str, Any]],
    series: dict[str, list[float]] | None = None,
    market_prices: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> str:
    if not items:
        return '<p class="event-empty">今天沒有需要合併判讀的焦點標的。</p>'
    series = series or {}
    market_prices = market_prices or {}
    return "\n".join(
        _render_today_focus_item(item, series.get(str(item["stock_code"])), market_prices) for item in items
    )


def _render_today_focus_item(
    item: dict[str, Any],
    series: list[float] | None = None,
    market_prices: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> str:
    signals = item.get("signals", set())
    signal_tags: list[str] = []
    if "new_buy" in signals:
        signal_tags.append('<span class="focus-tag hot">新買進共識</span>')
    if "new_holding" in signals:
        count = int(item.get("new_holding_count", 0) or 0)
        label = f"首次納入 {count} 檔" if count > 1 else "首次納入"
        signal_tags.append(f'<span class="focus-tag new">{_esc(label)}</span>')
    if "same" in signals:
        signal_tags.append(f'<span class="focus-tag same">{int(item.get("same_count", 0) or 0)} 檔同向加股</span>')
    if "unusual" in signals:
        signal_tags.append('<span class="focus-tag unusual">異常加碼</span>')

    metrics: list[str] = []
    holding_count = int(item.get("holding_fund_count", 0) or 0)
    if holding_count:
        metrics.append(f'<span class="metric-chip">共持 <strong>{holding_count} 檔</strong></span>')
    average_weight = float(item.get("average_weight", 0) or 0)
    if average_weight:
        metrics.append(f'<span class="metric-chip">平均 <strong>{average_weight:.2f}%</strong></span>')
    max_delta = float(item.get("max_delta", 0) or 0)
    if max_delta:
        metrics.append(f'<span class="metric-chip delta">最高 +{max_delta:.2f}pp</span>')
    unusual_count = int(item.get("unusual_count", 0) or 0)
    if unusual_count > 1:
        metrics.append(f'<span class="metric-chip status">異常 {unusual_count} 筆</span>')
    metrics.append(
        _render_market_reaction(
            str(item["stock_code"]),
            item.get("event_dates", set()),
            market_prices or {},
        )
    )

    sparkline = _render_sparkline(series or [], small=True)
    return f"""
      <div class="insight-row unusual-row">
        <span class="insight-stock">
          <strong>{_esc(str(item["stock_name"]))}</strong>
          <span class="table-code">{_esc(str(item["stock_code"]))}</span>
          <span class="focus-tags">{"".join(signal_tags)}</span>
          {sparkline}
        </span>
        <span class="focus-score">{"".join(metrics)}</span>
      </div>
    """


def _build_new_buy_consensus_items(
    new_holding_rows: list[dict[str, Any]],
    consensus_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    consensus_by_code = {str(row.get("stock_code", "")): row for row in consensus_rows}
    grouped: dict[str, dict[str, Any]] = {}
    for row in new_holding_rows:
        stock_code = str(row.get("stock_code", ""))
        consensus = consensus_by_code.get(stock_code)
        if not consensus:
            continue
        item = grouped.setdefault(
            stock_code,
            {
                "stock_code": stock_code,
                "stock_name": consensus.get("stock_name", "") or row.get("stock_name", ""),
                "funds": [],
                "holding_fund_count": int(consensus.get("holding_fund_count", 0) or 0),
                "average_weight": float(consensus.get("average_weight", 0) or 0),
                "max_weight": float(consensus.get("max_weight", 0) or 0),
                "max_new_weight": 0.0,
                "all_funds": dict(consensus.get("fund_weights", {})),
                "new_buy_fund_codes": set(),
            },
        )
        item["funds"].append(row)
        fund_code = str(row.get("etf_code", "")).strip()
        if fund_code:
            item["new_buy_fund_codes"].add(fund_code)
            item["all_funds"].setdefault(fund_code, float(row.get("new_weight_pct", 0) or 0))
        item["max_new_weight"] = max(float(item["max_new_weight"]), float(row.get("new_weight_pct", 0) or 0))

    items = list(grouped.values())
    items.sort(
        key=lambda item: (
            len(item["funds"]),
            int(item["holding_fund_count"]),
            float(item["max_new_weight"]),
            float(item["average_weight"]),
        ),
        reverse=True,
    )
    return items


def _render_new_buy_consensus_list(items: list[dict[str, Any]]) -> str:
    if not items:
        return '<p class="event-empty">這一輪沒有「新買進且已成共識」的股票。</p>'
    return "\n".join(_render_new_buy_consensus_item(item) for item in items)


def _render_new_buy_consensus_item(item: dict[str, Any]) -> str:
    new_buy_fund_codes = {str(fund_code) for fund_code in item.get("new_buy_fund_codes", set())}
    fund_weights = item.get("all_funds", {})
    tags = "".join(
        _render_consensus_fund_tag(
            str(fund_code),
            float(weight or 0),
            is_new_buy=str(fund_code) in new_buy_fund_codes,
        )
        for fund_code, weight in sorted(fund_weights.items())
    )
    return f"""
      <div class="insight-row">
        <span class="insight-stock">
          <strong>{_esc(str(item["stock_name"]))}</strong>
          <span class="table-code">{_esc(str(item["stock_code"]))} · {int(item["holding_fund_count"])} 檔共持 · 平均 {float(item["average_weight"]):.2f}%</span>
        </span>
        <span class="weight-tags">{tags}</span>
      </div>
    """


def _render_consensus_fund_tag(fund_code: str, weight: float, is_new_buy: bool = False) -> str:
    css_class = " positive" if is_new_buy else ""
    marker = '<em class="tag-note">新</em>' if is_new_buy else ""
    return (
        f'<span class="weight-tag{css_class}">{_esc(fund_code)} '
        f'<strong>{_format_pct(weight)}</strong>{marker}</span>'
    )


def _render_same_direction_item(
    row: dict[str, Any],
    series: list[float] | None = None,
    market_prices: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> str:
    increase_funds = _fund_codes_by_status(row, "股數增加")
    tags = "".join(_render_same_direction_fund_tag(row, fund_code) for fund_code in increase_funds)
    event_dates = {
        str(row.get("fund_as_of_dates", {}).get(fund_code, ""))
        for fund_code in increase_funds
        if row.get("fund_as_of_dates", {}).get(fund_code)
    }
    market_reaction = _render_market_reaction(
        str(row["stock_code"]),
        event_dates,
        market_prices or {},
    )
    sparkline = _render_sparkline(series or [], small=True)
    return f"""
      <div class="insight-row unusual-row">
        <span class="insight-stock">
          <strong>{_esc(str(row["stock_name"]))}</strong>
          <span class="table-code">{_esc(str(row["stock_code"]))} · {row["same_share_increase_count"]} 檔加股</span>
          {sparkline}
        </span>
        <span class="unusual-metrics">{market_reaction}{tags}</span>
      </div>
    """


def _render_same_direction_fund_tag(row: dict[str, Any], fund_code: str) -> str:
    weight = float(row.get("fund_weights", {}).get(fund_code, 0) or 0)
    weight_change = float(row.get("fund_weight_changes", {}).get(fund_code, 0) or 0)
    share_change = float(row.get("fund_share_changes", {}).get(fund_code, 0) or 0)
    streak = int(row.get("fund_streaks", {}).get(fund_code, 0) or 0)
    delta = f"+{weight_change:.2f}pp" if weight_change > 0 else "加股"
    share_text = _format_number(share_change) if share_change else ""
    share_chip = f'<span class="metric-chip status">+{share_text} 股</span>' if share_text else ""
    streak_chip = f'<span class="metric-chip ratio">連{streak}</span>' if streak > 1 else ""
    return (
        f'<span class="metric-chip">{_esc(fund_code)} '
        f'<strong>{_format_pct(weight)}</strong></span>'
        f'<span class="metric-chip delta">{_esc(delta)}</span>'
        f'{share_chip}'
        f'{streak_chip}'
    )


def _render_new_holding_list(
    rows: list[dict[str, Any]],
    new_buy_consensus_codes: set[str] | None = None,
    market_prices: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> str:
    if not rows:
        return '<p class="kpi-note">全體最新資料日沒有第一次新增持股。</p>'
    new_buy_consensus_codes = new_buy_consensus_codes or set()

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        stock_code = str(row.get("stock_code", ""))
        item = grouped.setdefault(
            stock_code,
            {
                "stock_code": stock_code,
                "stock_name": row.get("stock_name", ""),
                "funds": [],
                "max_weight": 0.0,
                "is_new_buy_consensus": stock_code in new_buy_consensus_codes,
                "event_dates": set(),
            },
        )
        item["funds"].append(row)
        item["event_dates"].add(str(row.get("new_as_of_datetime", "")).replace("/", "-")[:10])
        item["max_weight"] = max(float(item["max_weight"]), float(row.get("new_weight_pct", 0) or 0))

    stock_items = sorted(
        grouped.values(),
        key=lambda item: (len(item["funds"]), float(item["max_weight"]), str(item["stock_code"])),
        reverse=True,
    )
    return "\n".join(_render_new_holding_item(item, market_prices or {}) for item in stock_items)


def _render_new_holding_item(
    item: dict[str, Any],
    market_prices: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> str:
    tags = "".join(
        _render_new_holding_fund_tag(row)
        for row in sorted(item["funds"], key=lambda row: (str(row.get("etf_code", ""))))
    )
    new_buy_tag = '<span class="status-tag">新買進共識</span>' if item.get("is_new_buy_consensus") else ""
    market_reaction = _render_market_reaction(
        str(item["stock_code"]),
        item.get("event_dates", set()),
        market_prices or {},
    )
    return f"""
      <div class="insight-row">
        <span class="insight-stock"><strong>{_esc(str(item["stock_name"]))}</strong>{new_buy_tag}<span class="table-code">{_esc(str(item["stock_code"]))}</span></span>
        <span class="weight-tags">{market_reaction}{tags}</span>
      </div>
    """


def _render_new_holding_fund_tag(row: dict[str, Any]) -> str:
    return (
        f'<span class="weight-tag positive">{_esc(str(row["etf_code"]))} '
        f'<strong>{_format_pct(float(row["new_weight_pct"]))}</strong></span>'
    )


def _render_unusual_increase_list(
    rows: list[dict[str, Any]],
    series: dict[str, list[float]] | None = None,
    market_prices: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> str:
    if not rows:
        return '<p class="kpi-note">目前沒有達到異常增持門檻的持股。</p>'
    series = series or {}

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        stock_code = str(row.get("stock_code", ""))
        item = grouped.setdefault(
            stock_code,
            {
                "stock_code": stock_code,
                "stock_name": row.get("stock_name", ""),
                "funds": [],
                "max_delta": 0.0,
                "event_dates": set(),
            },
        )
        item["funds"].append(row)
        item["event_dates"].add(str(row.get("new_as_of_datetime", "")).replace("/", "-")[:10])
        item["max_delta"] = max(float(item["max_delta"]), float(row.get("weight_change_pct", 0) or 0))

    stock_items = sorted(
        grouped.values(),
        key=lambda item: (float(item["max_delta"]), len(item["funds"]), str(item["stock_code"])),
        reverse=True,
    )
    return "\n".join(
        _render_unusual_increase_item(item, series.get(str(item["stock_code"])), market_prices or {})
        for item in stock_items[:12]
    )


def _render_unusual_increase_item(
    item: dict[str, Any],
    series: list[float] | None = None,
    market_prices: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> str:
    tags = "".join(
        _render_unusual_increase_fund_tag(row)
        for row in sorted(
            item["funds"],
            key=lambda row: (-float(row.get("weight_change_pct", 0) or 0), str(row.get("etf_code", ""))),
        )
    )
    market_reaction = _render_market_reaction(
        str(item["stock_code"]),
        item.get("event_dates", set()),
        market_prices or {},
    )
    sparkline = _render_sparkline(series or [], small=True)
    return f"""
      <div class="insight-row unusual-row">
        <span class="insight-stock"><strong>{_esc(str(item["stock_name"]))}</strong><span class="table-code">{_esc(str(item["stock_code"]))}</span>{sparkline}</span>
        <span class="unusual-metrics">{market_reaction}{tags}</span>
      </div>
    """


def _render_unusual_increase_fund_tag(row: dict[str, Any]) -> str:
    status = "新進" if row.get("share_status") == "新增" else "加股"
    ratio = row.get("weight_ratio", "")
    ratio_text = ""
    if ratio not in ("", None):
        ratio_value = float(ratio)
        ratio_text = f'<span class="metric-chip ratio">×{ratio_value:.2f}</span>' if ratio_value >= 1.5 else ""
    return (
        f'<span class="metric-chip">{_esc(str(row["etf_code"]))} '
        f'<strong>{_format_pct(float(row["new_weight_pct"]))}</strong></span>'
        f'<span class="metric-chip delta">+{float(row["weight_change_pct"]):.2f}pp</span>'
        f'{ratio_text}'
        f'<span class="metric-chip status">{_esc(status)}</span>'
    )


def _render_market_reaction(
    stock_code: str,
    event_dates: set[str] | list[str],
    market_prices: dict[tuple[str, str], dict[str, Any]],
) -> str:
    dates = sorted({str(value) for value in event_dates if str(value)})
    if not dates:
        return ""
    event_date = dates[-1]
    date_label = event_date[5:].replace("-", "/") if len(event_date) >= 10 else event_date
    row = market_prices.get((stock_code, event_date))
    if not row:
        return (
            f'<span class="market-reaction unavailable" title="找不到該事件資料日的公開收盤行情">'
            f'<span class="market-label">{_esc(date_label)} 收盤</span><strong>尚無行情</strong></span>'
        )

    change_pct = float(row.get("change_pct", 0) or 0)
    css_class = "positive" if change_pct > 0 else "negative" if change_pct < 0 else "flat"
    pct_text = f"{change_pct:+.2f}%"
    close_text = _format_market_price(float(row.get("close", 0) or 0), str(row.get("currency", "")))
    title = f'{event_date}；{row.get("market", "")}；來源：{row.get("source", "")}'
    return (
        f'<span class="market-reaction {css_class}" title="{_esc(title)}">'
        f'<span class="market-label">{_esc(date_label)} 收盤</span>'
        f'<strong>{_esc(pct_text)}</strong>'
        f'<span class="market-close">{_esc(close_text)}</span></span>'
    )


def _format_market_price(value: float, currency: str) -> str:
    prefixes = {"TWD": "NT$", "USD": "US$", "JPY": "¥", "KRW": "₩", "CNY": "CN¥"}
    prefix = prefixes.get(currency.upper(), f"{currency.upper()} " if currency else "")
    decimals = 0 if value >= 1000 else 2
    return f"{prefix}{value:,.{decimals}f}"


def _render_changes_section(changes: list[dict[str, Any]], changes_path: Path) -> str:
    if not changes:
        return """
    <section class="card notice" style="margin-top:18px;">
      <h2>持股變化</h2>
      <p>尚未找到變化比較檔。請先用 compare 指令產生 reports/holding_changes.csv。</p>
    </section>
"""

    share_changed = [row for row in changes if row.get("share_status") != "股數不變"]
    share_increase = sum(1 for row in changes if row.get("share_status") == "股數增加")
    share_decrease = sum(1 for row in changes if row.get("share_status") == "股數減少")
    removed = sum(1 for row in changes if row.get("share_status") == "移除")
    added = sum(1 for row in changes if row.get("share_status") == "新增")
    changed_rows = share_changed[:12] or changes[:12]
    change_table = "\n".join(_render_change_row(row) for row in changed_rows)
    top_weight_rows = "\n".join(_render_weight_change_item(row) for row in changes[:8])

    return f"""
    <section class="card" style="margin-top:18px;">
      <div class="toolbar">
        <div>
          <h2 style="margin:0;">持股變化</h2>
          <div class="kpi-note">股數狀態與權重狀態分開呈現，避免把價格造成的權重漂移誤判成實際增減持。</div>
        </div>
      </div>
      <div class="change-grid">
        <div class="table-wrap" style="max-height:420px;">
          <table>
            <thead>
              <tr>
                <th>股票</th>
                <th>股數狀態</th>
                <th>權重狀態</th>
                <th class="num">股數變化</th>
                <th class="num">權重變化</th>
              </tr>
            </thead>
            <tbody>
              {change_table}
            </tbody>
          </table>
        </div>
        <div class="change-summary">
          <div class="pill-row">
            <span class="pill">股數增加 · {share_increase}</span>
            <span class="pill">股數減少 · {share_decrease}</span>
            <span class="pill">新增 · {added}</span>
            <span class="pill">移除 · {removed}</span>
          </div>
          <div>
            <h2 style="font-size:16px; margin:12px 0 8px;">權重變化前八名</h2>
            {top_weight_rows}
          </div>
          <p class="kpi-note">比較檔：<code>{_esc(str(changes_path))}</code></p>
        </div>
      </div>
    </section>
"""


def _render_streaks_section(
    streaks: list[dict[str, Any]],
    streaks_path: Path,
    snapshot_dates: list[date],
) -> str:
    if len(snapshot_dates) < 2:
        return """
    <section class="card notice" style="margin-top:18px;">
      <h2>連續增加持股</h2>
      <p>至少需要兩個不同資料日的持股快照，才能計算股數是否連續增加。</p>
    </section>
"""

    active_streaks = [row for row in streaks if int(row["current_increase_streak"]) > 0]
    repeated_streaks = [row for row in streaks if int(row["current_increase_streak"]) >= 2]
    max_streak = max((int(row["current_increase_streak"]) for row in streaks), default=0)
    date_label = f"{snapshot_dates[0].isoformat()} → {snapshot_dates[-1].isoformat()}"

    if not active_streaks:
        table_body = """
              <tr>
                <td colspan="6">最新資料日沒有任何股數增加的持股。</td>
              </tr>
"""
    else:
        table_body = "\n".join(_render_streak_row(row) for row in active_streaks[:12])

    leaders = "\n".join(_render_streak_leader(row) for row in active_streaks[:8])
    if not leaders:
        leaders = '<p class="kpi-note">目前沒有連續增加名單。</p>'

    return f"""
    <section class="card" style="margin-top:18px;">
      <div class="toolbar">
        <div>
          <h2 style="margin:0;">連續增加持股</h2>
          <div class="kpi-note">以公開快照的股數欄位判斷；權重增加不列入連續增持，避免股價波動造成誤判。</div>
        </div>
      </div>
      <div class="change-grid">
        <div class="table-wrap" style="max-height:420px;">
          <table>
            <thead>
              <tr>
                <th>股票</th>
                <th>連續增持</th>
                <th class="num">最新股數變化</th>
                <th class="num">連續區間增量</th>
                <th class="num">最新權重</th>
                <th>區間</th>
              </tr>
            </thead>
            <tbody>
              {table_body}
            </tbody>
          </table>
        </div>
        <div class="change-summary">
          <div class="pill-row">
            <span class="pill">資料日 · {len(snapshot_dates)}</span>
            <span class="pill">最高連續 · {max_streak}</span>
            <span class="pill">連續 >= 2 · {len(repeated_streaks)}</span>
          </div>
          <div class="date-range">追蹤區間：{_esc(date_label)}</div>
          <div>
            <h2 style="font-size:16px; margin:12px 0 8px;">連續增加觀察名單</h2>
            {leaders}
          </div>
          <p class="kpi-note">明細檔：<code>{_esc(str(streaks_path))}</code></p>
        </div>
      </div>
    </section>
"""


def _render_streak_row(row: dict[str, Any]) -> str:
    streak = int(row["current_increase_streak"])
    date_range = _streak_date_range(row)
    return f"""
      <tr>
        <td><strong>{_esc(str(row.get("stock_name", "")))}</strong><div class="table-code">{_esc(str(row.get("stock_code", "")))}</div></td>
        <td><span class="streak-badge">{streak} 次</span></td>
        <td class="num positive">{_format_number(float(row["latest_share_change"]))}</td>
        <td class="num positive">{_format_number(float(row["streak_total_share_change"]))}</td>
        <td class="num">{float(row["latest_weight_pct"]):.2f}%</td>
        <td class="date-range">{_esc(date_range)}</td>
      </tr>
    """


def _render_streak_leader(row: dict[str, Any]) -> str:
    streak = int(row["current_increase_streak"])
    return f"""
      <div class="change-item">
        <span>{_esc(str(row.get("stock_name", "")))} <span class="table-code">{_esc(str(row.get("stock_code", "")))}</span></span>
        <strong class="positive">{streak} 次</strong>
      </div>
    """


def _streak_date_range(row: dict[str, Any]) -> str:
    start = str(row.get("streak_start_date", ""))
    end = str(row.get("latest_as_of_date", ""))
    if start and end:
        return f"{start} → {end}"
    return end


def _render_change_row(row: dict[str, Any]) -> str:
    weight_class = _change_class(row["weight_change_num"])
    share_class = _change_class(row["share_change_num"])
    return f"""
      <tr>
        <td><strong>{_esc(str(row.get("stock_name", "")))}</strong><div class="table-code">{_esc(str(row.get("stock_code", "")))}</div></td>
        <td>{_esc(str(row.get("share_status", "")))}</td>
        <td>{_esc(str(row.get("weight_status", "")))}</td>
        <td class="num {share_class}">{_format_number(row["share_change_num"])}</td>
        <td class="num {weight_class}">{row["weight_change_num"]:+.2f}%</td>
      </tr>
    """


def _render_weight_change_item(row: dict[str, Any]) -> str:
    css_class = _change_class(row["weight_change_num"])
    return f"""
      <div class="change-item">
        <span>{_esc(str(row.get("stock_name", "")))} <span class="table-code">{_esc(str(row.get("stock_code", "")))}</span></span>
        <strong class="{css_class}">{row["weight_change_num"]:+.2f}%</strong>
      </div>
    """


def _change_class(value: float) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return ""


def _kpi(label: str, value: str, note: str) -> str:
    return f"""
      <div class="card">
        <div class="kpi-label">{_esc(label)}</div>
        <div class="kpi-value">{_esc(value)}</div>
        <div class="kpi-note">{note}</div>
      </div>
    """


def _format_number(value: float) -> str:
    if abs(value) >= 1:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _esc(value: str) -> str:
    return html.escape(value, quote=True)
