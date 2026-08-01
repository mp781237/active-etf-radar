from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from active_etf_radar.dashboard import build_dashboard
from active_etf_radar.funds import select_ezmoney_funds
from active_etf_radar.sources.ezmoney import FetchResult, fetch_ezmoney_holdings


@dataclass(frozen=True)
class EzMoneyRefreshRecord:
    refreshed_at: str
    source_site: str
    source_route: str
    etf_code: str
    fund_code: str
    fund_name: str
    category: str
    info_url: str
    status: str
    row_count: int
    weight_sum: float
    as_of_datetime: str
    edit_datetime: str
    raw_html_path: str
    csv_path: str
    error: str = ""


@dataclass(frozen=True)
class EzMoneyRefreshSummary:
    manifest_csv_path: Path
    manifest_json_path: Path
    dashboard_path: Path | None
    records: list[EzMoneyRefreshRecord]


def refresh_ezmoney_latest(
    project_root: Path,
    etf_codes: list[str] | None = None,
    allow_insecure_tls: bool = False,
    rebuild_dashboard: bool = True,
) -> EzMoneyRefreshSummary:
    refreshed_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    records: list[EzMoneyRefreshRecord] = []

    for spec in select_ezmoney_funds(etf_codes):
        try:
            result = fetch_ezmoney_holdings(
                fund_code=spec.fund_code,
                etf_code=spec.etf_code,
                output_root=project_root,
                allow_insecure_tls=allow_insecure_tls,
            )
            first_row = _read_first_row(result.csv_path)
            records.append(
                _success_record(
                    refreshed_at=refreshed_at,
                    spec=spec,
                    result=result,
                    first_row=first_row,
                    project_root=project_root,
                )
            )
        except Exception as exc:
            records.append(
                EzMoneyRefreshRecord(
                    refreshed_at=refreshed_at,
                    source_site="EZMoney",
                    source_route="ETF/Fund/Info DataAsset",
                    etf_code=spec.etf_code,
                    fund_code=spec.fund_code,
                    fund_name=spec.fund_name,
                    category=spec.category,
                    info_url=spec.info_url,
                    status="error",
                    row_count=0,
                    weight_sum=0.0,
                    as_of_datetime="",
                    edit_datetime="",
                    raw_html_path="",
                    csv_path="",
                    error=str(exc),
                )
            )

    manifest_csv_path, manifest_json_path = write_ezmoney_manifest(project_root, records)
    failed = [record for record in records if record.status != "ok"]
    if failed:
        failed_text = "; ".join(f"{record.etf_code}: {record.error}" for record in failed)
        raise RuntimeError(f"EZMoney refresh 未全部完成：{failed_text}")

    dashboard_path = None
    if rebuild_dashboard:
        dashboard_path = build_dashboard(
            project_root=project_root,
            csv_path=None,
            output_path=project_root / "reports" / "dashboard.html",
        )

    return EzMoneyRefreshSummary(
        manifest_csv_path=manifest_csv_path,
        manifest_json_path=manifest_json_path,
        dashboard_path=dashboard_path,
        records=records,
    )


def write_ezmoney_manifest(
    project_root: Path,
    records: list[EzMoneyRefreshRecord],
) -> tuple[Path, Path]:
    reports_dir = project_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    csv_path = reports_dir / "ezmoney_latest_manifest.csv"
    json_path = reports_dir / "ezmoney_latest_manifest.json"

    fieldnames = list(asdict(records[0]).keys()) if records else list(EzMoneyRefreshRecord.__dataclass_fields__)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(asdict(record))

    json_path.write_text(
        json.dumps([asdict(record) for record in records], ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="",
    )
    return csv_path, json_path


def _success_record(
    refreshed_at: str,
    spec: Any,
    result: FetchResult,
    first_row: dict[str, str],
    project_root: Path,
) -> EzMoneyRefreshRecord:
    return EzMoneyRefreshRecord(
        refreshed_at=refreshed_at,
        source_site="EZMoney",
        source_route="ETF/Fund/Info DataAsset",
        etf_code=result.etf_code,
        fund_code=result.fund_code,
        fund_name=spec.fund_name,
        category=spec.category,
        info_url=spec.info_url,
        status="ok",
        row_count=result.row_count,
        weight_sum=round(result.weight_sum, 4),
        as_of_datetime=first_row.get("as_of_datetime", ""),
        edit_datetime=first_row.get("edit_datetime", ""),
        raw_html_path=result.raw_html_path.relative_to(project_root).as_posix(),
        csv_path=result.csv_path.relative_to(project_root).as_posix(),
    )


def _read_first_row(csv_path: Path) -> dict[str, str]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = csv.DictReader(file)
        return next(rows, {})
