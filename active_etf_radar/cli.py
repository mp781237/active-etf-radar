from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from active_etf_radar.changes import compare_holdings, find_latest_two_csvs, write_changes_csv
from active_etf_radar.dashboard import build_dashboard
from active_etf_radar.sources.capital_buyback import fetch_capital_buyback
from active_etf_radar.sources.ezmoney import fetch_ezmoney_holdings
from active_etf_radar.sources.ezmoney_pcf import fetch_ezmoney_pcf, fetch_ezmoney_pcf_range
from active_etf_radar.sources.fhtrust_assets import fetch_fhtrust_assets, fetch_fhtrust_assets_range
from active_etf_radar.sources.firstsitc_hd import fetch_firstsitc_hd
from active_etf_radar.workflow import refresh_ezmoney_latest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="active_etf_radar",
        description="主動式 ETF 公開持股研究工具",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser(
        "fetch-ezmoney",
        help="抓取 EZMoney 公開 ETF 持股頁並輸出標準化 CSV",
    )
    fetch.add_argument("--fund-code", default="49YTW", help="EZMoney FundCode，00981A 為 49YTW")
    fetch.add_argument("--etf-code", default="00981A", help="ETF 股票代號")
    fetch.add_argument("--output-root", type=Path, default=PROJECT_ROOT, help="輸出根目錄")
    fetch.add_argument(
        "--allow-insecure-tls",
        action="store_true",
        help="允許 EZMoney 公開頁在 Python OpenSSL 憑證檢查失敗時放寬 TLS 驗證",
    )

    pcf = subparsers.add_parser(
        "fetch-pcf",
        help="抓取 EZMoney 官方 PCF 公開查詢資料，支援指定日期或日期區間",
    )
    pcf.add_argument("--fund-code", default="49YTW", help="EZMoney FundCode，00981A 為 49YTW")
    pcf.add_argument("--etf-code", default="00981A", help="ETF 股票代號")
    pcf.add_argument("--date", type=_parse_date, help="單日查詢日期，格式 YYYY-MM-DD")
    pcf.add_argument("--start-date", type=_parse_date, help="區間開始日期，格式 YYYY-MM-DD")
    pcf.add_argument("--end-date", type=_parse_date, help="區間結束日期，格式 YYYY-MM-DD")
    pcf.add_argument("--output-root", type=Path, default=PROJECT_ROOT, help="輸出根目錄")
    pcf.add_argument(
        "--allow-insecure-tls",
        action="store_true",
        help="允許 EZMoney 公開頁在 Python OpenSSL 憑證檢查失敗時放寬 TLS 驗證",
    )

    fhtrust = subparsers.add_parser(
        "fetch-fhtrust",
        help="抓取復華投信官方基金資產 Excel 公開下載資料，支援指定日期或日期區間",
    )
    fhtrust.add_argument("--fund-code", default="ETF23", help="復華官方基金代碼，00991A 為 ETF23")
    fhtrust.add_argument("--etf-code", default="00991A", help="ETF 股票代號")
    fhtrust.add_argument("--date", type=_parse_date, help="單日查詢日期，格式 YYYY-MM-DD")
    fhtrust.add_argument("--start-date", type=_parse_date, help="區間開始日期，格式 YYYY-MM-DD")
    fhtrust.add_argument("--end-date", type=_parse_date, help="區間結束日期，格式 YYYY-MM-DD")
    fhtrust.add_argument("--output-root", type=Path, default=PROJECT_ROOT, help="輸出根目錄")

    capital = subparsers.add_parser(
        "fetch-capital",
        help="抓取群益投信官方 ETF 申購買回清單公開頁",
    )
    capital.add_argument("--product-id", default="500", help="群益 ETF 產品 ID，00992A 為 500，00997A 為 502")
    capital.add_argument("--etf-code", default="00992A", help="ETF 股票代號")
    capital.add_argument("--date", type=_parse_date, help="抓取日期註記，格式 YYYY-MM-DD；資料日期仍以官網頁面為準")
    capital.add_argument("--output-root", type=Path, default=PROJECT_ROOT, help="輸出根目錄")

    firstsitc = subparsers.add_parser(
        "fetch-firstsitc",
        help="抓取第一金投信官方 ETF 持股公開 AJAX 資料",
    )
    firstsitc.add_argument("--fund-id", default="182", help="第一金官網基金 ID，00994A 為 182")
    firstsitc.add_argument("--etf-code", default="00994A", help="ETF 股票代號")
    firstsitc.add_argument("--date", type=_parse_date, help="指定官網查詢日期，格式 YYYY-MM-DD；省略時抓最新")
    firstsitc.add_argument("--output-root", type=Path, default=PROJECT_ROOT, help="輸出根目錄")
    firstsitc.add_argument(
        "--allow-insecure-tls",
        action="store_true",
        help="允許第一金公開頁在 Python OpenSSL 憑證檢查失敗時放寬 TLS 驗證",
    )

    refresh_ezmoney = subparsers.add_parser(
        "refresh-ezmoney",
        help="用 EZMoney registry 重新抓取統一投信 ETF 最新持股、寫 manifest，並重建 dashboard",
    )
    refresh_ezmoney.add_argument(
        "--etf-code",
        action="append",
        help="只刷新指定 ETF，可重複提供；省略時刷新 00403A、00981A、00988A",
    )
    refresh_ezmoney.add_argument("--output-root", type=Path, default=PROJECT_ROOT, help="輸出根目錄")
    refresh_ezmoney.add_argument(
        "--allow-insecure-tls",
        action="store_true",
        help="EZMoney 在 Python 3.13 可能出現 Missing Subject Key Identifier 時使用",
    )
    refresh_ezmoney.add_argument(
        "--skip-dashboard",
        action="store_true",
        help="只抓資料與寫 manifest，不重建 dashboard",
    )

    dashboard = subparsers.add_parser(
        "build-dashboard",
        help="用最新或指定持股 CSV 產生 self-contained HTML 儀表板",
    )
    dashboard.add_argument("--csv", type=Path, help="指定持股 CSV；省略時使用 data/processed 最新檔")
    dashboard.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "dashboard.html",
        help="輸出 HTML 路徑",
    )

    compare = subparsers.add_parser(
        "compare",
        help="比較兩份持股 CSV，計算增持、減持、新增、移除",
    )
    compare.add_argument("--old", type=Path, help="較早日期持股 CSV；省略時使用倒數第二新檔")
    compare.add_argument("--new", type=Path, help="較新日期持股 CSV；省略時使用最新檔")
    compare.add_argument("--etf-code", help="省略 --old/--new 時，用指定 ETF 挑選最新兩個資料日")
    compare.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "holding_changes.csv",
        help="輸出變化 CSV 路徑",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "fetch-ezmoney":
        result = fetch_ezmoney_holdings(
            fund_code=args.fund_code,
            etf_code=args.etf_code,
            output_root=args.output_root,
            allow_insecure_tls=args.allow_insecure_tls,
        )
        print(f"ETF：{result.etf_code}")
        print(f"FundCode：{result.fund_code}")
        print(f"資料筆數：{result.row_count}")
        print(f"權重合計：{result.weight_sum:.2f}%")
        print(f"原始 HTML：{result.raw_html_path}")
        print(f"標準化 CSV：{result.csv_path}")
    elif args.command == "fetch-pcf":
        if args.start_date or args.end_date:
            if args.date:
                parser.error("fetch-pcf 請擇一使用 --date 或 --start-date/--end-date")
            if not args.start_date or not args.end_date:
                parser.error("fetch-pcf 區間查詢需同時提供 --start-date 與 --end-date")
            results = fetch_ezmoney_pcf_range(
                fund_code=args.fund_code,
                etf_code=args.etf_code,
                start_date=args.start_date,
                end_date=args.end_date,
                output_root=args.output_root,
                allow_insecure_tls=args.allow_insecure_tls,
            )
        else:
            result = fetch_ezmoney_pcf(
                fund_code=args.fund_code,
                etf_code=args.etf_code,
                query_date=args.date or date.today(),
                output_root=args.output_root,
                allow_insecure_tls=args.allow_insecure_tls,
            )
            results = [result]

        for result in results:
            print(f"ETF：{result.etf_code}")
            print(f"FundCode：{result.fund_code}")
            print(f"查詢日期：{result.query_date}")
            print(f"資料日期：{result.tran_date}")
            print(f"資料筆數：{result.row_count}")
            print(f"權重合計：{result.weight_sum:.2f}%")
            print(f"原始 JSON：{result.raw_json_path}")
            print(f"標準化 CSV：{result.csv_path}")
    elif args.command == "fetch-fhtrust":
        if args.start_date or args.end_date:
            if args.date:
                parser.error("fetch-fhtrust 請擇一使用 --date 或 --start-date/--end-date")
            if not args.start_date or not args.end_date:
                parser.error("fetch-fhtrust 區間查詢需同時提供 --start-date 與 --end-date")
            results = fetch_fhtrust_assets_range(
                fund_code=args.fund_code,
                etf_code=args.etf_code,
                start_date=args.start_date,
                end_date=args.end_date,
                output_root=args.output_root,
            )
        else:
            result = fetch_fhtrust_assets(
                fund_code=args.fund_code,
                etf_code=args.etf_code,
                query_date=args.date or date.today(),
                output_root=args.output_root,
            )
            results = [result]

        for result in results:
            print(f"ETF：{result.etf_code}")
            print(f"FundCode：{result.fund_code}")
            print(f"查詢日期：{result.query_date}")
            print(f"資料日期：{result.as_of_date}")
            print(f"資料筆數：{result.row_count}")
            print(f"權重合計：{result.weight_sum:.2f}%")
            print(f"原始 XLSX：{result.raw_xlsx_path}")
            print(f"標準化 CSV：{result.csv_path}")
    elif args.command == "fetch-capital":
        result = fetch_capital_buyback(
            product_id=args.product_id,
            etf_code=args.etf_code,
            query_date=args.date,
            output_root=args.output_root,
        )
        print(f"ETF：{result.etf_code}")
        print(f"ProductID：{result.fund_code}")
        print(f"查詢註記日期：{result.query_date}")
        print(f"資料日期：{result.as_of_date}")
        print(f"資料筆數：{result.row_count}")
        print(f"權重合計：{result.weight_sum:.2f}%")
        print(f"原始 JSON：{result.raw_path}")
        print(f"標準化 CSV：{result.csv_path}")
    elif args.command == "fetch-firstsitc":
        result = fetch_firstsitc_hd(
            fund_id=args.fund_id,
            etf_code=args.etf_code,
            query_date=args.date,
            output_root=args.output_root,
            allow_insecure_tls=args.allow_insecure_tls,
        )
        print(f"ETF：{result.etf_code}")
        print(f"FundID：{result.fund_code}")
        print(f"查詢日期：{result.query_date}")
        print(f"資料日期：{result.as_of_date}")
        print(f"資料筆數：{result.row_count}")
        print(f"權重合計：{result.weight_sum:.2f}%")
        print(f"原始 JSON：{result.raw_json_path}")
        print(f"標準化 CSV：{result.csv_path}")
    elif args.command == "refresh-ezmoney":
        summary = refresh_ezmoney_latest(
            project_root=args.output_root,
            etf_codes=args.etf_code,
            allow_insecure_tls=args.allow_insecure_tls,
            rebuild_dashboard=not args.skip_dashboard,
        )
        for record in summary.records:
            print(
                f"{record.etf_code} {record.fund_name}："
                f"{record.row_count} 筆，權重合計 {record.weight_sum:.2f}%，"
                f"資料時間 {record.as_of_datetime}，更新時間 {record.edit_datetime}"
            )
        print(f"Manifest CSV：{summary.manifest_csv_path}")
        print(f"Manifest JSON：{summary.manifest_json_path}")
        if summary.dashboard_path:
            print(f"Dashboard：{summary.dashboard_path}")
    elif args.command == "build-dashboard":
        result = build_dashboard(project_root=PROJECT_ROOT, csv_path=args.csv, output_path=args.output)
        print(f"儀表板：{result}")
    elif args.command == "compare":
        old_csv, new_csv = (args.old, args.new)
        if old_csv is None or new_csv is None:
            old_csv, new_csv = find_latest_two_csvs(PROJECT_ROOT, etf_code=args.etf_code)
        changes = compare_holdings(old_csv=old_csv, new_csv=new_csv)
        output = write_changes_csv(changes=changes, output_path=args.output)
        print(f"舊檔：{old_csv}")
        print(f"新檔：{new_csv}")
        print(f"變化筆數：{len(changes)}")
        print(f"輸出：{output}")


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期格式需為 YYYY-MM-DD") from exc


if __name__ == "__main__":
    main()
