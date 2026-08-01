from __future__ import annotations

import csv
import http.cookiejar
import json
import re
import ssl
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


BASE_URL = "https://www.ezmoney.com.tw"
PCF_PAGE_URL = f"{BASE_URL}/ETF/Transaction/PCF"
GET_PCF_URL = f"{BASE_URL}/ETF/Transaction/GetPCF"


@dataclass(frozen=True)
class PcfFetchResult:
    fund_code: str
    etf_code: str
    query_date: str
    tran_date: str
    row_count: int
    weight_sum: float
    raw_json_path: Path
    csv_path: Path


def fetch_ezmoney_pcf(
    fund_code: str,
    etf_code: str,
    query_date: date,
    output_root: Path,
    allow_insecure_tls: bool = False,
) -> PcfFetchResult:
    fetched_at = datetime.now(timezone.utc).astimezone()
    opener = _build_opener(allow_insecure_tls=allow_insecure_tls)
    _warm_up_pcf_page(opener)
    payload = _post_pcf(opener=opener, fund_code=fund_code, query_date=query_date)

    raw_path = _write_raw_json(output_root, fund_code, query_date, fetched_at, payload)
    rows = _normalize_stock_rows(
        payload=payload,
        fund_code=fund_code,
        etf_code=etf_code,
        query_date=query_date,
        fetched_at=fetched_at,
    )
    csv_path = _write_csv(output_root, etf_code, fund_code, query_date, fetched_at, rows)
    tran_date = rows[0]["as_of_datetime"] if rows else ""

    return PcfFetchResult(
        fund_code=fund_code,
        etf_code=etf_code,
        query_date=query_date.isoformat(),
        tran_date=tran_date,
        row_count=len(rows),
        weight_sum=sum(float(row["weight_pct"]) for row in rows),
        raw_json_path=raw_path,
        csv_path=csv_path,
    )


def fetch_ezmoney_pcf_range(
    fund_code: str,
    etf_code: str,
    start_date: date,
    end_date: date,
    output_root: Path,
    allow_insecure_tls: bool = False,
) -> list[PcfFetchResult]:
    if end_date < start_date:
        raise ValueError("end-date 不能早於 start-date")

    results: list[PcfFetchResult] = []
    current = start_date
    while current <= end_date:
        try:
            result = fetch_ezmoney_pcf(
                fund_code=fund_code,
                etf_code=etf_code,
                query_date=current,
                output_root=output_root,
                allow_insecure_tls=allow_insecure_tls,
            )
            results.append(result)
        except ValueError as exc:
            if "沒有股票明細" not in str(exc):
                raise
        current += timedelta(days=1)
    return results


def _build_opener(allow_insecure_tls: bool) -> urllib.request.OpenerDirector:
    context = ssl._create_unverified_context() if allow_insecure_tls else None
    cookie_jar = http.cookiejar.CookieJar()
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.HTTPCookieProcessor(cookie_jar),
    ]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


def _warm_up_pcf_page(opener: urllib.request.OpenerDirector) -> None:
    request = urllib.request.Request(PCF_PAGE_URL, headers=_headers())
    with opener.open(request, timeout=30) as response:
        response.read()


def _post_pcf(
    opener: urllib.request.OpenerDirector,
    fund_code: str,
    query_date: date,
) -> dict[str, Any]:
    body = json.dumps(
        {
            "fundCode": fund_code,
            "date": _to_roc_date(query_date),
            "specificDate": True,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    headers = _headers()
    headers.update(
        {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json; charset=utf-8",
            "Referer": PCF_PAGE_URL,
        }
    )
    request = urllib.request.Request(GET_PCF_URL, data=body, headers=headers, method="POST")
    with opener.open(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        text = response.read().decode(charset, errors="replace")
    return json.loads(text)


def _normalize_stock_rows(
    payload: dict[str, Any],
    fund_code: str,
    etf_code: str,
    query_date: date,
    fetched_at: datetime,
) -> list[dict[str, Any]]:
    fund = payload.get("fund") or {}
    expected_etf_code = str(fund.get("sStockNo", "")).strip()
    if expected_etf_code and expected_etf_code != etf_code:
        raise ValueError(f"FundCode={fund_code} 對應 {expected_etf_code}，不是指定的 {etf_code}")

    assets = payload.get("asset") or []
    stock_asset = next((asset for asset in assets if asset.get("AssetCode") == "ST"), None)
    if not stock_asset:
        raise ValueError("EZMoney GetPCF 回傳裡沒有股票明細 AssetCode=ST")

    rows: list[dict[str, Any]] = []
    for detail in stock_asset.get("Details") or []:
        rows.append(
            {
                "fetched_at": fetched_at.isoformat(timespec="seconds"),
                "source": "ezmoney_pcf",
                "source_url": GET_PCF_URL,
                "fund_code": fund_code,
                "etf_code": etf_code,
                "query_date": query_date.isoformat(),
                "as_of_datetime": _normalize_date_value(detail.get("TranDate") or stock_asset.get("EndDate", "")),
                "edit_datetime": _normalize_date_value(detail.get("EditTime") or stock_asset.get("EditDate", "")),
                "asset_code": detail.get("AssetCode", ""),
                "stock_code": detail.get("DetailCode", ""),
                "stock_name": detail.get("DetailName", ""),
                "currency": detail.get("MoneyType", ""),
                "shares": detail.get("Share", ""),
                "market_value": detail.get("Amount", ""),
                "weight_pct": detail.get("NavRate", ""),
            }
        )

    rows.sort(key=lambda row: float(row["weight_pct"] or 0), reverse=True)
    return rows


def _write_raw_json(
    output_root: Path,
    fund_code: str,
    query_date: date,
    fetched_at: datetime,
    payload: dict[str, Any],
) -> Path:
    raw_dir = output_root / "data" / "raw" / "ezmoney_pcf"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = fetched_at.strftime("%Y%m%d_%H%M%S")
    path = raw_dir / f"{stamp}_{fund_code}_{query_date.isoformat()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="")
    return path


def _write_csv(
    output_root: Path,
    etf_code: str,
    fund_code: str,
    query_date: date,
    fetched_at: datetime,
    rows: list[dict[str, Any]],
) -> Path:
    processed_dir = output_root / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    stamp = fetched_at.strftime("%Y%m%d_%H%M%S")
    path = processed_dir / f"holdings_{etf_code}_{fund_code}_pcf_{query_date.isoformat()}_{stamp}.csv"
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


def _to_roc_date(value: date) -> str:
    return f"{value.year - 1911:03d}/{value.month:02d}/{value.day:02d}"


def _normalize_date_value(value: object) -> str:
    text = str(value or "")
    match = re.fullmatch(r"/Date\((?P<ms>-?\d+)\)/", text)
    if match:
        seconds = int(match.group("ms")) / 1000
        return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    return text
