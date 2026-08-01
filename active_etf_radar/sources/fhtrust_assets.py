from __future__ import annotations

import csv
import re
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


BASE_URL = "https://www.fhtrust.com.tw"
ASSETS_EXCEL_URL = f"{BASE_URL}/api/assetsExcel"
XLSX_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@dataclass(frozen=True)
class FhTrustAssetsFetchResult:
    fund_code: str
    etf_code: str
    query_date: str
    as_of_date: str
    row_count: int
    weight_sum: float
    raw_xlsx_path: Path
    csv_path: Path


def fetch_fhtrust_assets(
    fund_code: str,
    etf_code: str,
    query_date: date,
    output_root: Path,
) -> FhTrustAssetsFetchResult:
    fetched_at = datetime.now(timezone.utc).astimezone()
    raw_bytes = _download_assets_excel(fund_code=fund_code, query_date=query_date)
    raw_path = _write_raw_xlsx(output_root, fund_code, query_date, fetched_at, raw_bytes)
    workbook = _parse_workbook(raw_path)

    rows = _normalize_rows(
        workbook=workbook,
        fund_code=fund_code,
        etf_code=etf_code,
        query_date=query_date,
        fetched_at=fetched_at,
    )
    csv_path = _write_csv(output_root, etf_code, fund_code, query_date, fetched_at, rows)

    return FhTrustAssetsFetchResult(
        fund_code=fund_code,
        etf_code=etf_code,
        query_date=query_date.isoformat(),
        as_of_date=workbook["as_of_date"].isoformat(),
        row_count=len(rows),
        weight_sum=sum(float(row["weight_pct"]) for row in rows),
        raw_xlsx_path=raw_path,
        csv_path=csv_path,
    )


def fetch_fhtrust_assets_range(
    fund_code: str,
    etf_code: str,
    start_date: date,
    end_date: date,
    output_root: Path,
) -> list[FhTrustAssetsFetchResult]:
    if end_date < start_date:
        raise ValueError("end-date 不能早於 start-date")

    results: list[FhTrustAssetsFetchResult] = []
    current = start_date
    while current <= end_date:
        try:
            result = fetch_fhtrust_assets(
                fund_code=fund_code,
                etf_code=etf_code,
                query_date=current,
                output_root=output_root,
            )
            results.append(result)
        except ValueError as exc:
            if "沒有持股明細" not in str(exc) and "沒有回傳 xlsx" not in str(exc):
                raise
        current += timedelta(days=1)
    return results


def _download_assets_excel(fund_code: str, query_date: date) -> bytes:
    url = _source_url(fund_code, query_date)
    request = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read()
    if not data.startswith(b"PK"):
        raise ValueError(f"復華 assetsExcel 沒有回傳 xlsx：{url}")
    return data


def _parse_workbook(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _read_shared_strings(archive)
        sheet_xml = archive.read("xl/worksheets/sheet1.xml")

    sheet = ElementTree.fromstring(sheet_xml)
    rows: list[list[str]] = []
    for row in sheet.findall(".//x:row", XLSX_NS):
        values: dict[int, str] = {}
        for cell in row.findall("x:c", XLSX_NS):
            index = _cell_index(cell.get("r", ""))
            values[index] = _cell_text(cell, shared_strings)
        if values:
            max_index = max(values)
            rows.append([values.get(index, "").strip() for index in range(max_index + 1)])

    as_of_date = _find_as_of_date(rows)
    holdings = _find_holding_rows(rows)
    return {"as_of_date": as_of_date, "holdings": holdings}


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    strings: list[str] = []
    for item in root.findall("x:si", XLSX_NS):
        strings.append("".join(text.text or "" for text in item.findall(".//x:t", XLSX_NS)))
    return strings


def _cell_text(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    value = cell.find("x:v", XLSX_NS)
    if value is None or value.text is None:
        inline_text = cell.find(".//x:t", XLSX_NS)
        return inline_text.text or "" if inline_text is not None else ""

    text = value.text
    if cell.get("t") == "s":
        return shared_strings[int(text)]
    return text


def _cell_index(reference: str) -> int:
    letters = "".join(char for char in reference if char.isalpha()).upper()
    total = 0
    for char in letters:
        total = total * 26 + (ord(char) - ord("A") + 1)
    return max(total - 1, 0)


def _find_as_of_date(rows: list[list[str]]) -> date:
    for row in rows:
        text = " ".join(row)
        match = re.search(r"(\d{4})/(\d{2})/(\d{2})", text)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return date(year, month, day)
    raise ValueError("復華 assetsExcel 缺少資料日期")


def _find_holding_rows(rows: list[list[str]]) -> list[dict[str, str]]:
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if row[:5] == ["證券代號", "證券名稱", "股數", "金額", "權重(%)"]
        ),
        None,
    )
    if header_index is None:
        raise ValueError("復華 assetsExcel 缺少持股表頭")

    holdings: list[dict[str, str]] = []
    for row in rows[header_index + 1 :]:
        if len(row) < 5 or not row[0]:
            continue
        if not re.fullmatch(r"\d{4}", row[0]):
            continue
        holdings.append(
            {
                "stock_code": row[0],
                "stock_name": row[1],
                "shares": _clean_number(row[2]),
                "market_value": _clean_number(row[3]),
                "weight_pct": _clean_percent(row[4]),
            }
        )

    if not holdings:
        raise ValueError("復華 assetsExcel 沒有持股明細")
    return holdings


def _normalize_rows(
    workbook: dict[str, Any],
    fund_code: str,
    etf_code: str,
    query_date: date,
    fetched_at: datetime,
) -> list[dict[str, Any]]:
    as_of_date = workbook["as_of_date"].isoformat()
    rows: list[dict[str, Any]] = []
    for holding in workbook["holdings"]:
        rows.append(
            {
                "fetched_at": fetched_at.isoformat(timespec="seconds"),
                "source": "fhtrust_assets",
                "source_url": _source_url(fund_code, query_date),
                "fund_code": fund_code,
                "etf_code": etf_code,
                "query_date": query_date.isoformat(),
                "as_of_datetime": as_of_date,
                "edit_datetime": "",
                "asset_code": "ST",
                "stock_code": holding["stock_code"],
                "stock_name": holding["stock_name"],
                "currency": "NTD",
                "shares": holding["shares"],
                "market_value": holding["market_value"],
                "weight_pct": holding["weight_pct"],
            }
        )

    rows.sort(key=lambda row: float(row["weight_pct"] or 0), reverse=True)
    return rows


def _write_raw_xlsx(
    output_root: Path,
    fund_code: str,
    query_date: date,
    fetched_at: datetime,
    raw_bytes: bytes,
) -> Path:
    raw_dir = output_root / "data" / "raw" / "fhtrust_assets"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = fetched_at.strftime("%Y%m%d_%H%M%S")
    path = raw_dir / f"{stamp}_{fund_code}_{query_date.isoformat()}.xlsx"
    path.write_bytes(raw_bytes)
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
    path = processed_dir / f"holdings_{etf_code}_{fund_code}_fhtrust_assets_{query_date.isoformat()}_{stamp}.csv"
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


def _source_url(fund_code: str, query_date: date) -> str:
    return f"{ASSETS_EXCEL_URL}/{fund_code}/{query_date:%Y%m%d}"


def _headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131 Safari/537.36"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }


def _clean_number(value: str) -> str:
    return value.replace(",", "").strip()


def _clean_percent(value: str) -> str:
    return _clean_number(value).replace("%", "")
