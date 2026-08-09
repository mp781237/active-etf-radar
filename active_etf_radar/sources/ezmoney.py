from __future__ import annotations

import csv
import html
import http.cookiejar
import json
import re
import ssl
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EZMONEY_URL = "https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode={fund_code}"


@dataclass(frozen=True)
class FetchResult:
    etf_code: str
    fund_code: str
    row_count: int
    weight_sum: float
    raw_html_path: Path
    csv_path: Path


def fetch_ezmoney_holdings(
    fund_code: str,
    etf_code: str,
    output_root: Path,
    allow_insecure_tls: bool = False,
) -> FetchResult:
    fetched_at = datetime.now(timezone.utc).astimezone()
    url = EZMONEY_URL.format(fund_code=fund_code)
    html_text = _download_html(url, allow_insecure_tls=allow_insecure_tls)

    raw_path = _write_raw_html(output_root, fund_code, fetched_at, html_text)
    assets = _extract_json_data(html_text, "DataAsset")
    fund = _extract_fund_metadata(html_text)
    rows = _normalize_asset_rows(
        assets=assets,
        fund=fund,
        fund_code=fund_code,
        etf_code=etf_code,
        source_url=url,
        fetched_at=fetched_at,
    )
    csv_path = _write_csv(output_root, etf_code, fund_code, fetched_at, rows)

    return FetchResult(
        etf_code=etf_code,
        fund_code=fund_code,
        row_count=sum(1 for row in rows if row["asset_code"] == "ST"),
        weight_sum=sum(float(row["weight_pct"]) for row in rows if row["asset_code"] == "ST"),
        raw_html_path=raw_path,
        csv_path=csv_path,
    )


def _download_html(url: str, allow_insecure_tls: bool = False) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131 Safari/537.36"
            ),
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        },
    )
    context = ssl._create_unverified_context() if allow_insecure_tls else None
    cookie_jar = http.cookiejar.CookieJar()
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.HTTPCookieProcessor(cookie_jar),
    ]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers)
    with opener.open(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _extract_json_data(html_text: str, element_id: str) -> Any:
    raw_data = _extract_data_content(html_text, element_id)
    return json.loads(html.unescape(raw_data))


def _extract_data_content(html_text: str, element_id: str) -> str:
    pattern = (
        rf'<div\s+id="{re.escape(element_id)}"\s+'
        rf'data-content="(?P<json>[\s\S]*?)"\s+style='
    )
    match = re.search(pattern, html_text, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"找不到 EZMoney 內嵌資料：{element_id}")
    return match.group("json")


def _extract_fund_metadata(html_text: str) -> dict[str, str]:
    raw_data = _extract_data_content(html_text, "DataFund")
    decoded = raw_data
    for _ in range(3):
        next_decoded = html.unescape(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    return {
        "sStockNo": _extract_json_like_string(decoded, "sStockNo"),
        "sFundShortName": _extract_json_like_string(decoded, "sFundShortName"),
        "sFundCode": _extract_json_like_string(decoded, "sFundCode"),
    }


def _extract_json_like_string(text: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"(?P<value>[^"]*)"', text)
    return match.group("value") if match else ""


def _normalize_asset_rows(
    assets: list[dict[str, Any]],
    fund: dict[str, Any],
    fund_code: str,
    etf_code: str,
    source_url: str,
    fetched_at: datetime,
) -> list[dict[str, Any]]:
    stock_asset = next((asset for asset in assets if asset.get("AssetCode") == "ST"), None)
    if not stock_asset:
        raise ValueError("EZMoney DataAsset 裡沒有股票持股 AssetCode=ST")

    expected_etf_code = str(fund.get("sStockNo", "")).strip()
    if expected_etf_code and expected_etf_code != etf_code:
        raise ValueError(
            f"FundCode={fund_code} 對應 {expected_etf_code}，不是指定的 {etf_code}"
        )

    edit_datetime = str(stock_asset.get("EditDate", "")).strip()
    rows: list[dict[str, Any]] = []
    for asset in assets:
        asset_edit_datetime = str(asset.get("EditDate", "")).strip() or edit_datetime
        for detail in asset.get("Details") or []:
            asset_code = str(detail.get("AssetCode") or asset.get("AssetCode") or "").strip()
            rows.append(
                {
                    "fetched_at": fetched_at.isoformat(timespec="seconds"),
                    "source": "ezmoney",
                    "source_url": source_url,
                    "fund_code": fund_code,
                    "etf_code": etf_code,
                    "as_of_datetime": str(detail.get("TranDate") or asset.get("EndDate") or asset_edit_datetime)[:10],
                    "edit_datetime": str(detail.get("EditTime") or asset_edit_datetime),
                    "asset_code": asset_code,
                    "stock_code": detail.get("DetailCode", ""),
                    "stock_name": detail.get("DetailName", ""),
                    "currency": detail.get("MoneyType", ""),
                    "shares": detail.get("Share", ""),
                    "market_value": detail.get("Amount", ""),
                    "weight_pct": detail.get("NavRate", ""),
                    "position": str(detail.get("Position", "")).strip(),
                    "contract_month": detail.get("MTH", ""),
                }
            )

    rows.sort(key=lambda row: float(row["weight_pct"] or 0), reverse=True)
    return rows


def _write_raw_html(output_root: Path, fund_code: str, fetched_at: datetime, html_text: str) -> Path:
    raw_dir = output_root / "data" / "raw" / "ezmoney"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = fetched_at.strftime("%Y%m%d_%H%M%S")
    path = raw_dir / f"{stamp}_{fund_code}.html"
    path.write_text(html_text, encoding="utf-8", newline="")
    return path


def _write_csv(
    output_root: Path,
    etf_code: str,
    fund_code: str,
    fetched_at: datetime,
    rows: list[dict[str, Any]],
) -> Path:
    processed_dir = output_root / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    stamp = fetched_at.strftime("%Y%m%d_%H%M%S")
    path = processed_dir / f"holdings_{etf_code}_{fund_code}_{stamp}.csv"
    fieldnames = [
        "fetched_at",
        "source",
        "source_url",
        "fund_code",
        "etf_code",
        "as_of_datetime",
        "edit_datetime",
        "asset_code",
        "stock_code",
        "stock_name",
        "currency",
        "shares",
        "market_value",
        "weight_pct",
        "position",
        "contract_month",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path
