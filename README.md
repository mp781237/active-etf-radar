# active-etf-radar

## GitHub 自動更新

專案包含兩個 GitHub Actions 工作流程：

- `.github/workflows/manual-source-test.yml`：手動測試所有公開資料來源，不提交測試結果。
- `.github/workflows/daily-update.yml`：台北時間平日 `19:17` 排程更新，避開 GitHub Actions 整點壅塞；完成後重建儀表板、提交 `data/` 與 `reports/`，並發布 GitHub Pages。

每個投信來源獨立執行。單一來源暫時無法連線時，其他來源仍可更新，儀表板會沿用該基金既有資料，Actions 工作摘要則會標示失敗來源。自動化只操作這個 repository 內的公開資料與報告。

主動式 ETF 公開持股研究工具。

第一個追蹤標的是 `00981A 主動統一台股增長`。工具目標是保留每日公開持股原始資料，標準化成可查核格式，後續再分析持股權重、產業鏈配置與影子股池變化。

## 限制

- 不是交易系統。
- 不產生買進、賣出、停損、停利訊號。
- 不接券商 API。
- 不自動下單。
- 不抓取需要登入、授權、破解驗證或繞過限制的資料。
- 只使用公開網頁、公開 API、公開下載檔或使用者手動匯入的 CSV。
- 原始資料一律保留，方便日後查核。
- 報告與註解使用繁體中文。

## 目前狀態

第一版已支援從 EZMoney 公開頁抓取 ETF 內嵌資產資料：

```powershell
py -3.13 -m active_etf_radar fetch-ezmoney --fund-code 49YTW --etf-code 00981A
```

如果 Python OpenSSL 因 EZMoney 憑證格式回報 `Missing Subject Key Identifier`，可改用：

```powershell
py -3.13 -m active_etf_radar fetch-ezmoney --fund-code 49YTW --etf-code 00981A --allow-insecure-tls
```

會輸出：

- `data/raw/ezmoney/*.html`：原始公開頁 HTML
- `data/processed/holdings_*.csv`：標準化持股 CSV

官方 PCF 公開查詢可指定日期，適合建立歷史快照：

```powershell
py -3.13 -m active_etf_radar fetch-pcf --fund-code 49YTW --etf-code 00981A --date 2026-05-25 --allow-insecure-tls
```

也可抓一段區間：

```powershell
py -3.13 -m active_etf_radar fetch-pcf --start-date 2026-05-21 --end-date 2026-05-25 --allow-insecure-tls
```

會輸出：

- `data/raw/ezmoney_pcf/*.json`：官方 PCF 公開查詢原始 JSON
- `data/processed/holdings_*_pcf_*.csv`：標準化持股 CSV

加入第二檔主動式 ETF 範例：

```powershell
py -3.13 -m active_etf_radar fetch-pcf --fund-code 61YTW --etf-code 00988A --start-date 2026-05-20 --end-date 2026-05-26 --allow-insecure-tls
```

加入 `00403A 主動統一升級50`：

```powershell
py -3.13 -m active_etf_radar fetch-pcf --fund-code 63YTW --etf-code 00403A --start-date 2026-05-20 --end-date 2026-05-26 --allow-insecure-tls
```

加入復華 `00991A 主動復華未來50` 範例：

```powershell
py -3.13 -m active_etf_radar fetch-fhtrust --fund-code ETF23 --etf-code 00991A --start-date 2026-05-18 --end-date 2026-05-25
```

會輸出：

- `data/raw/fhtrust_assets/*.xlsx`：復華官方基金資產 Excel 原始檔
- `data/processed/holdings_*_fhtrust_assets_*.csv`：標準化持股 CSV

加入群益 `00992A 主動群益台灣科技創新` 與 `00997A 主動群益美國增長` 範例：

```powershell
py -3.13 -m active_etf_radar fetch-capital --product-id 500 --etf-code 00992A
py -3.13 -m active_etf_radar fetch-capital --product-id 502 --etf-code 00997A
```

會輸出：

- `data/raw/capital_buyback/*.html`：群益官方申購買回清單原始 HTML
- `data/processed/holdings_*_capital_buyback_*.csv`：標準化持股 CSV

加入第一金 `00994A 主動第一金台股優` 範例：

```powershell
py -3.13 -m active_etf_radar fetch-firstsitc --fund-id 182 --etf-code 00994A --allow-insecure-tls
```

會輸出：

- `data/raw/firstsitc_hd/*.json`：第一金官方持股公開 AJAX 原始 JSON
- `data/processed/holdings_*_firstsitc_hd_*.csv`：標準化持股 CSV

產生可視化儀表板：

```powershell
py -3.13 -m active_etf_radar build-dashboard
```

輸出：

- `reports/dashboard.html`
- `reports/holding_streaks.csv`：依多日快照計算的連續股數增加明細
- `reports/multi_fund_overview.csv`：多基金總覽
- `reports/multi_fund_consensus.csv`：共識持股明細

比較兩份持股 CSV：

```powershell
py -3.13 -m active_etf_radar compare --old data\processed\holdings_old.csv --new data\processed\holdings_new.csv
```

若省略 `--old` / `--new`，會自動使用 `data/processed/` 裡最新兩份 CSV。至少要有兩個不同日期的快照，才有研究意義。

比較結果會同時輸出：

- `share_status`：股數增加、股數減少、股數不變、新增、移除。
- `weight_status`：權重增加、權重降低、權重持平、新增、移除。

研究解讀時要優先分辨「股數真的變了」與「只有權重因價格或淨值變動而漂移」。

儀表板會另外顯示「連續增加持股」。這個區塊只用股數計算，並且同一資料日只取最新快照；如果一檔股票在最新資料日股數沒有增加，連續次數會歸零。

如果資料夾裡有兩檔以上 ETF 快照，儀表板會自動顯示「多基金總覽」「共識持股」「同向股數變化」「異常增持」。共識持股只列至少 2 檔基金共同持有的股票，並用基金權重標籤顯示各基金比重；同向股數變化只列至少 2 檔基金同時加股數。單基金細節會用分頁切開，例如 `00403A`、`00981A`、`00988A`、`00991A`、`00992A`、`00994A` 與 `00997A` 各自顯示持股、變化、連續增加與資料查核。

共識持股會標示 `首次共識`：定義是目前最新快照達到至少 2 檔基金共同持有，但上一輪各基金快照組成的共識清單尚未出現。這是研究標籤，不代表交易訊號。

系統仍會輸出 `reports/multi_fund_new_holdings.csv`：只列全體目前最新資料日第一次出現在該基金持股裡的股票。這同樣只是研究資料，不代表交易訊號；目前 dashboard 不顯示這張卡片。

多基金總覽右側的 `異常增持` 會比較各基金自己的最新兩次公開資料，列出 `share_status` 為 `新增` 或 `股數增加`，且符合「權重增加 >= 0.30pp」或「既有持股權重放大 >= 1.5 倍」的股票，並輸出 `reports/multi_fund_unusual_increases.csv`。這是研究篩選門檻，不代表買賣訊號。

`00407A 主動凱基台灣` 截至 2026-05-26 仍是預計 2026-06-04 至 2026-06-10 募集，尚無正式公開持股快照；儀表板會先列在待資料基金，不納入共識或同向變化計算。

## 標準化欄位

CSV 欄位：

- `fetched_at`
- `source`
- `source_url`
- `fund_code`
- `etf_code`
- `query_date`（PCF 來源才有）
- `as_of_datetime`
- `edit_datetime`
- `asset_code`
- `stock_code`
- `stock_name`
- `currency`
- `shares`
- `market_value`
- `weight_pct`

## 下一步

1. 寫入 SQLite。
2. 將 `compare` 擴充成 3 日、5 日持股變化摘要。
3. 加入 `mappings/industry_map.csv` 與 `mappings/shadow_pool.csv`。
4. 產生 Markdown 每日研究報告。
# 最新 EZMoney 刷新流程

用 Microsoft Playwright MCP 驗證後，EZMoney 最新持股快照統一走 registry：

```powershell
py -3.13 -B -m active_etf_radar refresh-ezmoney --allow-insecure-tls
```

這會刷新 `00403A`、`00981A`、`00988A`，保留 raw HTML，輸出標準化 CSV，寫入 `reports/ezmoney_latest_manifest.csv` / `.json`，並重建 `reports/dashboard.html`。流程細節見 `docs/ezmoney_mcp_workflow.md`。
