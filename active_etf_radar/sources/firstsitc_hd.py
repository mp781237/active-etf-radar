from __future__ import annotations

import csv
import json
import ssl
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


BASE_URL = "https://www.fsitc.com.tw"
HOLDINGS_URL = f"{BASE_URL}/WebAPI.aspx/Get_hd"
FUND_DETAIL_URL_TEMPLATE = f"{BASE_URL}/FundDetail.aspx?ID={{fund_id}}"


@dataclass(frozen=True)
class FirstSitcHdFetchResult:
    fund_code: str
    etf_code: str
    query_date: str
    as_of_date: str
    row_count: int
    weight_sum: float
    raw_json_path: Path
    csv_path: Path


def fetch_firstsitc_hd(
    fund_id: str,
    etf_code: str,
    output_root: Path,
    query_date: date | None = None,
    allow_insecure_tls: bool = False,
) -> FirstSitcHdFetchResult:
    fetched_at = datetime.now(timezone.utc).astimezone()
    request_date = query_date.strftime("%Y/%m/%d") if query_date else ""
    recorded_query_date = query_date or date.today()
    payload = _post_holdings(
        fund_id=fund_id,
        request_date=request_date,
        allow_insecure_tls=allow_insecure_tls,
    )
    raw_path = _write_raw_json(output_root, fund_id, recorded_query_date, fetched_at, payload)
    rows = _normalize_rows(
        payload=payload,
        fund_id=fund_id,
        etf_code=etf_code,
        query_date=recorded_query_date,
        fetched_at=fetched_at,
    )
    csv_path = _write_csv(output_root, etf_code, fund_id, recorded_query_date, fetched_at, rows)
    as_of_date = rows[0]["as_of_datetime"] if rows else ""

    return FirstSitcHdFetchResult(
        fund_code=fund_id,
        etf_code=etf_code,
        query_date=recorded_query_date.isoformat(),
        as_of_date=as_of_date,
        row_count=len(rows),
        weight_sum=sum(float(row["weight_pct"]) for row in rows),
        raw_json_path=raw_path,
        csv_path=csv_path,
    )


def _post_holdings(fund_id: str, request_date: str, allow_insecure_tls: bool) -> dict[str, Any]:
    body = json.dumps({"pStrFundID": fund_id, "pStrDate": request_date}, ensure_ascii=False).encode("utf-8")
    headers = _headers()
    headers.update(
        {
            "Content-Type": "application/json; charset=utf-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": FUND_DETAIL_URL_TEMPLATE.format(fund_id=fund_id),
        }
    )
    request = urllib.request.Request(HOLDINGS_URL, data=body, headers=headers, method="POST")
    context = ssl._create_unverified_context() if allow_insecure_tls else None
    with urllib.request.urlopen(request, timeout=30, context=context) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        text = response.read().decode(charset, errors="replace")
    return json.loads(text)


def _normalize_rows(
    payload: dict[str, Any],
    fund_id: str,
    etf_code: str,
    query_date: date,
    fetched_at: datetime,
) -> list[dict[str, Any]]:
    details = json.loads(payload.get("d") or "[]")
    holdings = [item for item in details if str(item.get("group", "")).strip() == "1"]
    if not holdings:
        raise ValueError("第一金 Get_hd 沒有股票持股明細")

    as_of_date = str(holdings[0].get("sdate", "")).strip()
    if not as_of_date:
        raise ValueError("第一金 Get_hd 缺少資料日期")

    rows: list[dict[str, Any]] = []
    for holding in holdings:
        rows.append(
            {
                "fetched_at": fetched_at.isoformat(timespec="seconds"),
                "source": "firstsitc_hd",
                "source_url": HOLDINGS_URL,
                "fund_code": fund_id,
                "etf_code": etf_code,
                "query_date": query_date.isoformat(),
                "as_of_datetime": as_of_date,
                "edit_datetime": "",
                "asset_code": "ST",
                "stock_code": str(holding.get("A", "")).strip(),
                "stock_name": str(holding.get("B", "")).strip(),
                "currency": "TWD",
                "shares": _clean_number(holding.get("D", "")),
                "market_value": "",
                "weight_pct": _clean_percent(holding.get("C", "")),
            }
        )

    rows = [row for row in rows if row["stock_code"] and row["stock_name"]]
    rows.sort(key=lambda row: float(row["weight_pct"] or 0), reverse=True)
    return rows


def _write_raw_json(
    output_root: Path,
    fund_id: str,
    query_date: date,
    fetched_at: datetime,
    payload: dict[str, Any],
) -> Path:
    raw_dir = output_root / "data" / "raw" / "firstsitc_hd"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = fetched_at.strftime("%Y%m%d_%H%M%S")
    path = raw_dir / f"{stamp}_{fund_id}_{query_date.isoformat()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="")
    return path


def _write_csv(
    output_root: Path,
    etf_code: str,
    fund_id: str,
    query_date: date,
    fetched_at: datetime,
    rows: list[dict[str, Any]],
) -> Path:
    processed_dir = output_root / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    stamp = fetched_at.strftime("%Y%m%d_%H%M%S")
    path = processed_dir / f"holdings_{etf_code}_{fund_id}_firstsitc_hd_{query_date.isoformat()}_{stamp}.csv"
    fieldnames = [
        "fetched_at",
        "source",
        "source_url",
        "fund_code",
        "etf_code",
        "query_date",
        "as_of_datetime",
        "edit_datetime",
        "asset_code",
        "stock_code",
        "stock_name",
        "currency",
        "shares",
        "market_value",
        "weight_pct",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131 Safari/537.36"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }


def _clean_number(value: object) -> str:
    return str(value or "").replace(",", "").strip()


def _clean_percent(value: object) -> str:
    return _clean_number(value).replace("%", "")
