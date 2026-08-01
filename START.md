# active-etf-radar 啟動手冊

## 專案定位

追蹤台灣主動式 ETF 的公開持股資料，保留原始資料並輸出標準化研究資料。這不是交易系統，不產生買賣、停損、停利訊號，也不接券商 API。

## iCloud 規則

本專案位於 iCloud 同步資料夾內，只放 source、設定、文件與可查核資料。不要在專案內建立 `.venv`、`node_modules`、`.pytest_cache`、`__pycache__` 等大量小檔。

如果未來需要虛擬環境，請放在：

```powershell
C:\venvs\active-etf-radar
```

## 日常使用

目前第一版只使用 Python 標準函式庫，不需要安裝依賴。

抓取 EZMoney 公開頁 00981A 持股：

```powershell
py -3.13 -m active_etf_radar fetch-ezmoney --fund-code 49YTW --etf-code 00981A
```

若 Python 3.13 回報 EZMoney 憑證相容性錯誤 `Missing Subject Key Identifier`，可只針對這個公開頁加：

```powershell
py -3.13 -m active_etf_radar fetch-ezmoney --fund-code 49YTW --etf-code 00981A --allow-insecure-tls
```

輸出位置：

- 原始 HTML：`data/raw/ezmoney/`
- 標準化 CSV：`data/processed/`

查詢官方 PCF 歷史資料：

```powershell
py -3.13 -m active_etf_radar fetch-pcf --fund-code 49YTW --etf-code 00981A --date 2026-05-25 --allow-insecure-tls
```

區間抓取：

```powershell
py -3.13 -m active_etf_radar fetch-pcf --start-date 2026-05-21 --end-date 2026-05-25 --allow-insecure-tls
```

加入 `00988A 主動統一全球創新`：

```powershell
py -3.13 -m active_etf_radar fetch-pcf --fund-code 61YTW --etf-code 00988A --start-date 2026-05-20 --end-date 2026-05-26 --allow-insecure-tls
```

加入 `00403A 主動統一升級50`：

```powershell
py -3.13 -m active_etf_radar fetch-pcf --fund-code 63YTW --etf-code 00403A --start-date 2026-05-20 --end-date 2026-05-26 --allow-insecure-tls
```

加入 `00991A 主動復華未來50`：

```powershell
py -3.13 -m active_etf_radar fetch-fhtrust --fund-code ETF23 --etf-code 00991A --start-date 2026-05-18 --end-date 2026-05-25
```

加入群益 `00992A 主動群益台灣科技創新` 與 `00997A 主動群益美國增長`：

```powershell
py -3.13 -m active_etf_radar fetch-capital --product-id 500 --etf-code 00992A
py -3.13 -m active_etf_radar fetch-capital --product-id 502 --etf-code 00997A
```

加入第一金 `00994A 主動第一金台股優`：

```powershell
py -3.13 -m active_etf_radar fetch-firstsitc --fund-id 182 --etf-code 00994A --allow-insecure-tls
```

PCF 輸出位置：

- 原始 JSON：`data/raw/ezmoney_pcf/`
- 標準化 CSV：`data/processed/`

復華官方基金資產 Excel 輸出位置：

- 原始 XLSX：`data/raw/fhtrust_assets/`
- 標準化 CSV：`data/processed/`

群益官方申購買回清單輸出位置：

- 原始 JSON：`data/raw/capital_buyback/`
- 標準化 CSV：`data/processed/`

> 2026-06 起群益官網改為前端 JS 渲染，舊的 HTML 頁只回傳空殼。`fetch-capital` 已改打官方 JSON API `POST https://www.capitalfund.com.tw/CFWeb/api/etf/buyback`（body `{"fundId":"<product_id>","date":null}`），raw 改存 `.json`。資料日沿用 `exchangeDesc` 括號內的匯率日。00997A / 00988A 等含美股、日股、韓股，明細不會只留台股 4 碼代號。

第一金官方持股公開 AJAX 輸出位置：

- 原始 JSON：`data/raw/firstsitc_hd/`
- 標準化 CSV：`data/processed/`

產生可視化網頁：

```powershell
py -3.13 -m active_etf_radar build-dashboard
```

開啟：

```powershell
Start-Process .\reports\dashboard.html
```

Dashboard 會自動輸出 `reports/holding_streaks.csv`，並在頁面顯示「連續增加持股」。判斷基準是公開快照裡的股數，不是權重；同一資料日重抓多次時只採最新快照。

若 `data/processed/` 裡有兩檔以上 ETF，Dashboard 會額外輸出 `reports/multi_fund_overview.csv`、`reports/multi_fund_consensus.csv` 與 `reports/multi_fund_unusual_increases.csv`，並顯示多基金總覽、共識持股、同向股數變化、異常增持。共識持股至少要 2 檔基金共同持有，並用權重標籤呈現各基金比重；同向股數變化至少要 2 檔基金同時加股數。

共識持股的 `首次共識` 標籤代表：目前最新快照達到至少 2 檔基金共同持有，但上一輪各基金快照組成的共識清單尚未出現。這只是研究標籤，不是交易訊號。

系統仍會輸出 `reports/multi_fund_new_holdings.csv`，代表全體目前最新資料日第一次出現在該基金持股裡的股票；目前 dashboard 不顯示這張卡片。

多基金總覽右側的 `異常增持` 代表：比較各基金自己的最新兩次公開資料，列出 `新增` 或 `股數增加` 且符合「權重增加 >= 0.30pp」或「既有持股權重放大 >= 1.5 倍」的股票。這是研究篩選，不是交易訊號。

單基金細節會用分頁切開，`00403A`、`00981A`、`00988A`、`00991A`、`00992A`、`00994A` 與 `00997A` 不會混在同一條長頁裡；切換分頁後，完整持股表搜尋也只作用於該 ETF。

`00407A 主動凱基台灣` 截至 2026-05-26 尚未開始募集，預計 2026-06-04 至 2026-06-10 募集；在正式公開持股快照出現前，只列在待資料基金，不納入共識計算。

比較兩份持股快照：

```powershell
py -3.13 -m active_etf_radar compare --old <較早CSV> --new <較新CSV>
```

注意：EZMoney 公開投資組合頁只提供當前持股快照；官方 PCF 公開查詢可以指定日期，適合回補歷史資料。要看增持/減持，需要至少兩個不同資料日期的 CSV。比較結果會拆成股數狀態與權重狀態，避免把價格造成的權重漂移誤判成實際買賣。

## 資料來源備註

EZMoney FundCode 對照：

- `49YTW` = `00981A 主動統一台股增長`
- `61YTW` = `00988A 主動統一全球創新`
- `63YTW` = `00403A 主動統一升級50`

不要把 `61YTW` 當成 00981A。

復華官方基金代碼對照：

- `ETF23` = `00991A 主動復華未來50`

群益官方 product id 對照：

- `500` = `00992A 主動群益台灣科技創新`
- `502` = `00997A 主動群益美國增長`

第一金官方 fund id 對照：

- `182` = `00994A 主動第一金台股優`
# 回來第一步

刷新 EZMoney 三檔統一投信主動 ETF 最新持股，保留 raw HTML、寫 manifest，並重建 dashboard：

```powershell
py -3.13 -B -m active_etf_radar refresh-ezmoney --allow-insecure-tls
```

流程細節：`docs/ezmoney_mcp_workflow.md`
