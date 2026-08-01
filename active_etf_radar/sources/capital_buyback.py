from __future__ import annotations

import csv
import json
import re
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


BASE_URL = "https://www.capitalfund.com.tw"
# 2026-06：群益官網改為前端 JS 渲染，原本的 /etf/product/detail/{id}/buyback 只回傳空殼頁。
# 持股改由此 JSON API 提供，POST body 為 {"fundId": <product_id>, "date": null}。
BUYBACK_API_URL = f"{BASE_URL}/CFWeb/api/etf/buyback"
BUYBACK_PAGE_URL_TEMPLATE = f"{BASE_URL}/etf/product/detail/{{product_id}}/buyback"


@dataclass(frozen=True)
class CapitalBuybackFetchResult:
    fund_code: str
    etf_code: str
    query_date: str
    as_of_date: str
    row_count: int
    weight_sum: float
    raw_path: Path
    csv_path: Path


def fetch_capital_buyback(
    product_id: str,
    etf_code: str,
    output_root: Path,
    query_date: date | None = None,
) -> CapitalBuybackFetchResult:
    query_date = query_date or date.today()
    fetched_at = datetime.now(timezone.utc).astimezone()
    payload = _download_buyback_json(product_id)
    raw_path = _write_raw_json(output_root, product_id, query_date, fetched_at, payload)
    rows = _normalize_rows(
        payload=payload,
        source_url=BUYBACK_API_URL,
        fund_code=product_id,
        etf_code=etf_code,
        query_date=query_date,
        fetched_at=fetched_at,
    )
    csv_path = _write_csv(output_root, etf_code, product_id, query_date, fetched_at, rows)
    as_of_date = rows[0]["as_of_datetime"] if rows else ""

    return CapitalBuybackFetchResult(
        fund_code=product_id,
        etf_code=etf_code,
        query_date=query_date.isoformat(),
        as_of_date=as_of_date,
        row_count=len(rows),
        weight_sum=sum(float(row["weight_pct"]) for row in rows),
        raw_path=raw_path,
        csv_path=csv_path,
    )


def _download_buyback_json(product_id: str) -> dict[str, Any]:
    body = json.dumps({"fundId": str(product_id), "date": None}).encode("utf-8")
    request = urllib.request.Request(
        BUYBACK_API_URL,
        data=body,
        headers=_headers(product_id),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        payload = _decode_json_response(response.read(), charset)
    if not isinstance(payload, dict) or payload.get("code") != 200:
        code = payload.get("code") if isinstance(payload, dict) else "unknown"
        message = payload.get("message") if isinstance(payload, dict) else ""
        raise ValueError(f"群益申購買回 API 回應異常 code={code} message={message}")
    return payload


def _decode_json_response(raw_bytes: bytes, charset: str) -> dict[str, Any]:
    encoding = "utf-8-sig" if raw_bytes.startswith(b"\xef\xbb\xbf") else charset
    text = raw_bytes.decode(encoding, errors="replace").lstrip()
    if text.startswith(("<!doctype html", "<html")):
        raise ValueError("群益申購買回 API 目前回傳網站維護頁，未提供 JSON 持股資料")
    return json.loads(text)


def _normalize_rows(
    payload: dict[str, Any],
    source_url: str,
    fund_code: str,
    etf_code: str,
    query_date: date,
    fetched_at: datetime,
) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    as_of_date = _find_as_of_date(data).isoformat()
    holdings = _extract_stock_rows(data)
    if not holdings:
        raise ValueError("群益申購買回清單沒有股票明細")

    rows: list[dict[str, Any]] = []
    for holding in holdings:
        rows.append(
            {
                "fetched_at": fetched_at.isoformat(timespec="seconds"),
                "source": "capital_buyback",
                "source_url": source_url,
                "fund_code": fund_code,
                "etf_code": etf_code,
                "query_date": query_date.isoformat(),
                "as_of_datetime": as_of_date,
                "edit_datetime": "",
                "asset_code": "ST",
                "stock_code": holding["stock_code"],
                "stock_name": holding["stock_name"],
                "currency": "",
                "shares": holding["shares"],
                "market_value": "",
                "weight_pct": holding["weight_pct"],
            }
        )

    rows.sort(key=lambda row: float(row["weight_pct"] or 0), reverse=True)
    return rows


def _extract_stock_rows(data: dict[str, Any]) -> list[dict[str, str]]:
    stocks = data.get("stocks") or []
    rows: list[dict[str, str]] = []
    for stock in stocks:
        stock_code = str(stock.get("stocNo") or "").strip()
        stock_name = str(stock.get("stocName") or "").strip()
        # 沿用舊版：有代號 + 名稱 + 權重就收。00997A / 00988A 等含美股、日股、韓股
        # （例如 "MU US"、"4062 JP"、"009150 KS"），不可只留台股 4 碼代號。
        if not stock_code or not stock_name:
            continue
        weight_value = stock.get("weightRound")
        if weight_value is None:
            weight_value = stock.get("weight")
        weight_pct = "" if weight_value is None else f"{float(weight_value):.2f}"
        if not weight_pct:
            continue
        share = stock.get("share")
        shares = "" if share is None else str(int(float(share)))
        rows.append(
            {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "weight_pct": weight_pct,
                "shares": shares,
            }
        )
    return rows


def _find_as_of_date(data: dict[str, Any]) -> date:
    pcf = data.get("pcf") or {}
    # 沿用舊規則：資料日取股票表前括號內的匯率日，等同 API 的 date2。
    exchange_desc = str(pcf.get("exchangeDesc") or "")
    parenthesized = re.findall(r"\((20\d{2}/\d{2}/\d{2})\)", exchange_desc)
    if parenthesized:
        return _parse_date_text(parenthesized[-1])
    for key in ("date2", "date1"):
        value = pcf.get(key)
        if value:
            try:
                return _parse_date_text(str(value))
            except ValueError:
                continue
    raise ValueError("群益申購買回清單缺少資料日期")


def _parse_date_text(value: str) -> date:
    normalized = value.replace("/", "-")
    return date.fromisoformat(normalized)


def _write_raw_json(
    output_root: Path,
    product_id: str,
    query_date: date,
    fetched_at: datetime,
    payload: dict[str, Any],
) -> Path:
    raw_dir = output_root / "data" / "raw" / "capital_buyback"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = fetched_at.strftime("%Y%m%d_%H%M%S")
    path = raw_dir / f"{stamp}_{product_id}_{query_date.isoformat()}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="",
    )
    return path


def _write_csv(
    output_root: Path,
    etf_code: str,
    product_id: str,
    query_date: date,
    fetched_at: datetime,
    rows: list[dict[str, Any]],
) -> Path:
    processed_dir = output_root / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    stamp = fetched_at.strftime("%Y%m%d_%H%M%S")
    path = processed_dir / f"holdings_{etf_code}_{product_id}_capital_buyback_{query_date.isoformat()}_{stamp}.csv"
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


def _headers(product_id: str) -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": BASE_URL,
        "Referer": BUYBACK_PAGE_URL_TEMPLATE.format(product_id=product_id),
    }
