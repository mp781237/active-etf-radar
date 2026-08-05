from __future__ import annotations

import csv
import http.client
import json
import re
import ssl
import time as time_module
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any


TWSE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
DOWNLOAD_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1
PRICE_FIELDS = [
    "stock_code",
    "event_date",
    "market",
    "currency",
    "close",
    "change",
    "change_pct",
    "source",
    "source_url",
    "fetched_at",
]


class MarketPriceUnavailable(RuntimeError):
    pass


def refresh_event_market_prices(
    project_root: Path,
    event_dates_by_stock: dict[str, set[str]],
) -> dict[tuple[str, str], dict[str, Any]]:
    output_path = project_root / "reports" / "event_market_prices.csv"
    cached = _read_price_cache(output_path)
    requested = {
        (stock_code.strip(), event_date)
        for stock_code, event_dates in event_dates_by_stock.items()
        for event_date in event_dates
        if stock_code.strip() and _is_iso_date(event_date)
    }
    missing = requested - set(cached)
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")

    taiwan_dates = sorted({event_date for stock_code, event_date in missing if _is_taiwan_stock(stock_code)})
    for event_date in taiwan_dates:
        target_codes = {stock_code for stock_code, item_date in missing if item_date == event_date}
        for row in _fetch_taiwan_prices(project_root, event_date, target_codes, fetched_at):
            cached[(row["stock_code"], row["event_date"])] = row

    for stock_code, event_date in sorted(missing):
        if _is_taiwan_stock(stock_code) or (stock_code, event_date) in cached:
            continue
        row = _fetch_yahoo_price(project_root, stock_code, event_date, fetched_at)
        if row:
            cached[(stock_code, event_date)] = row

    _write_price_cache(output_path, cached)
    return {key: row for key, row in cached.items() if key in requested}


def _fetch_taiwan_prices(
    project_root: Path,
    event_date: str,
    target_codes: set[str],
    fetched_at: str,
) -> list[dict[str, Any]]:
    raw_dir = project_root / "data" / "raw" / "market_prices"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []

    twse_url = f"{TWSE_URL}?{urllib.parse.urlencode({'date': event_date.replace('-', ''), 'type': 'ALLBUT0999', 'response': 'json'})}"
    twse_data = _load_market_json_or_empty(raw_dir / f"twse_{event_date}.json", twse_url)
    rows.extend(_parse_twse_prices(twse_data, event_date, target_codes, twse_url, fetched_at))

    remaining = target_codes - {str(row["stock_code"]) for row in rows}
    if remaining:
        tpex_url = f"{TPEX_URL}?{urllib.parse.urlencode({'date': event_date.replace('-', '/'), 'id': '', 'response': 'json'})}"
        tpex_data = _load_market_json_or_empty(raw_dir / f"tpex_{event_date}.json", tpex_url)
        rows.extend(_parse_tpex_prices(tpex_data, event_date, remaining, tpex_url, fetched_at))
    return rows


def _fetch_yahoo_price(
    project_root: Path,
    stock_code: str,
    event_date: str,
    fetched_at: str,
) -> dict[str, Any] | None:
    yahoo_symbol, market = _yahoo_symbol(stock_code)
    if not yahoo_symbol:
        return None
    target_date = date.fromisoformat(event_date)
    start = int(datetime.combine(target_date - timedelta(days=8), time.min, tzinfo=timezone.utc).timestamp())
    end = int(datetime.combine(target_date + timedelta(days=3), time.min, tzinfo=timezone.utc).timestamp())
    params = urllib.parse.urlencode(
        {"period1": start, "period2": end, "interval": "1d", "events": "history"}
    )
    url = f"{YAHOO_URL}/{urllib.parse.quote(yahoo_symbol)}?{params}"
    raw_name = re.sub(r"[^A-Za-z0-9._-]+", "_", yahoo_symbol)
    raw_path = project_root / "data" / "raw" / "market_prices" / f"yahoo_{raw_name}_{event_date}.json"
    data = _load_market_json_or_empty(raw_path, url)
    return _parse_yahoo_price(data, stock_code, event_date, market, url, fetched_at)


def _parse_twse_prices(
    data: dict[str, Any],
    event_date: str,
    target_codes: set[str],
    source_url: str,
    fetched_at: str,
) -> list[dict[str, Any]]:
    table = next(
        (table for table in data.get("tables", []) if table.get("fields", [None])[0] == "證券代號"),
        None,
    )
    if not table:
        return []
    fields = table["fields"]
    index = {field: position for position, field in enumerate(fields)}
    rows = []
    for values in table.get("data", []):
        stock_code = str(values[index["證券代號"]]).strip()
        if stock_code not in target_codes:
            continue
        close = _number(values[index["收盤價"]])
        change_abs = _number(values[index["漲跌價差"]])
        sign_text = str(values[index["漲跌(+/-)"]])
        change = -change_abs if "-" in sign_text else change_abs
        row = _price_row(stock_code, event_date, "TWSE", "TWD", close, change, "TWSE", source_url, fetched_at)
        if row:
            rows.append(row)
    return rows


def _parse_tpex_prices(
    data: dict[str, Any],
    event_date: str,
    target_codes: set[str],
    source_url: str,
    fetched_at: str,
) -> list[dict[str, Any]]:
    table = next((table for table in data.get("tables", []) if table.get("title") == "上櫃股票行情"), None)
    if not table:
        return []
    fields = table["fields"]
    index = {field: position for position, field in enumerate(fields)}
    rows = []
    for values in table.get("data", []):
        stock_code = str(values[index["代號"]]).strip()
        if stock_code not in target_codes:
            continue
        close = _number(values[index["收盤"]])
        change = _number(values[index["漲跌"]])
        row = _price_row(stock_code, event_date, "TPEX", "TWD", close, change, "TPEX", source_url, fetched_at)
        if row:
            rows.append(row)
    return rows


def _parse_yahoo_price(
    data: dict[str, Any],
    stock_code: str,
    event_date: str,
    market: str,
    source_url: str,
    fetched_at: str,
) -> dict[str, Any] | None:
    results = data.get("chart", {}).get("result") or []
    if not results:
        return None
    result = results[0]
    timestamps = result.get("timestamp") or []
    closes = ((result.get("indicators", {}).get("quote") or [{}])[0].get("close") or [])
    points = [
        (datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat(), float(close))
        for timestamp, close in zip(timestamps, closes)
        if close is not None
    ]
    target_index = next((index for index, point in enumerate(points) if point[0] == event_date), None)
    if target_index is None or target_index == 0:
        return None
    close = points[target_index][1]
    previous_close = points[target_index - 1][1]
    currency = str(result.get("meta", {}).get("currency", ""))
    change = close - previous_close
    return _price_row(
        stock_code,
        event_date,
        market,
        currency,
        close,
        change,
        "Yahoo Finance",
        source_url,
        fetched_at,
    )


def _price_row(
    stock_code: str,
    event_date: str,
    market: str,
    currency: str,
    close: float,
    change: float,
    source: str,
    source_url: str,
    fetched_at: str,
) -> dict[str, Any] | None:
    previous_close = close - change
    if close <= 0 or previous_close <= 0:
        return None
    return {
        "stock_code": stock_code,
        "event_date": event_date,
        "market": market,
        "currency": currency,
        "close": round(close, 4),
        "change": round(change, 4),
        "change_pct": round(change / previous_close * 100, 4),
        "source": source,
        "source_url": source_url,
        "fetched_at": fetched_at,
    }


def _yahoo_symbol(stock_code: str) -> tuple[str, str]:
    parts = stock_code.strip().split()
    if len(parts) != 2:
        return "", ""
    symbol, suffix = parts
    suffixes = {
        "US": ("", "US"),
        "JP": (".T", "JP"),
        "KS": (".KS", "KR"),
        "KQ": (".KQ", "KR"),
        "CH": (".SZ" if symbol.startswith(("0", "3")) else ".SS", "CN"),
    }
    yahoo_suffix, market = suffixes.get(suffix.upper(), ("", ""))
    return (f"{symbol}{yahoo_suffix}", market) if market else ("", "")


def _load_or_fetch_json(path: Path, url: str) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 active-etf-radar/0.1"})
    last_error: Exception | None = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            try:
                response = urllib.request.urlopen(request, timeout=30)
            except urllib.error.URLError as exc:
                if not isinstance(exc.reason, ssl.SSLCertVerificationError):
                    raise
                response = urllib.request.urlopen(request, timeout=30, context=ssl._create_unverified_context())
            with response:
                data = json.loads(response.read().decode("utf-8"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8", newline="")
            return data
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
            if attempt + 1 < DOWNLOAD_ATTEMPTS:
                time_module.sleep(RETRY_DELAY_SECONDS)
    raise MarketPriceUnavailable(f"股價資料下載失敗（重試 {DOWNLOAD_ATTEMPTS} 次）：{url}") from last_error


def _load_market_json_or_empty(path: Path, url: str) -> dict[str, Any]:
    try:
        return _load_or_fetch_json(path, url)
    except MarketPriceUnavailable as exc:
        print(f"警告：{exc}")
        return {}


def _read_price_cache(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return {
            (str(row["stock_code"]), str(row["event_date"])): row
            for row in csv.DictReader(file)
            if row.get("stock_code") and row.get("event_date")
        }


def _write_price_cache(path: Path, rows: dict[tuple[str, str], dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=PRICE_FIELDS)
        writer.writeheader()
        for key in sorted(rows):
            writer.writerow({field: rows[key].get(field, "") for field in PRICE_FIELDS})


def _is_taiwan_stock(stock_code: str) -> bool:
    return bool(re.fullmatch(r"\d{4}", stock_code.strip()))


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _number(value: object) -> float:
    text = re.sub(r"<[^>]+>", "", str(value or "")).replace(",", "").strip()
    if text in ("", "--", "---"):
        return 0.0
    return float(text)
