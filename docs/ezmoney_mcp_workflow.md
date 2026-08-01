# EZMoney ETF 持股梳理流程

本流程用 Microsoft Playwright MCP 驗證網站行為，再用公開 HTML/API 做可重跑的資料抓取。MCP 負責「確認網站現在怎麼呈現」，正式資料管線仍保留 raw HTML/JSON 與標準化 CSV，方便日後查核。

## MCP 驗證結果

- 驗證日期：2026-06-02
- 驗證頁面：`https://www.ezmoney.com.tw/ETF/Fund/Info?FundCode=61YTW#asset`
- MCP 觀察：`FundCode=61YTW` 對應頁面顯示 `00988A 主動統一全球創新`，不是 `00981A`。
- 00981A 對應：`FundCode=49YTW`
- 00403A 對應：`FundCode=63YTW`
- 頁面可用公開 HTML 內嵌資料 `DataFund` / `DataAsset` 取得最新持股快照。

## 兩條資料路線

### 最新快照

使用頁面：

```text
https://www.ezmoney.com.tw/ETF/Fund/Info?FundCode={fund_code}#asset
```

用途：

- 每天更新最新公開持股。
- 讀取 `DataFund` 校驗 ETF 股票代號。
- 讀取 `DataAsset` 中 `AssetCode=ST` 的股票明細。
- 保留 raw HTML 到 `data/raw/ezmoney/`。
- 標準化 CSV 到 `data/processed/`。

### 歷史回補

使用公開 POST：

```text
https://www.ezmoney.com.tw/ETF/Transaction/GetPCF
```

用途：

- 回補指定日期或日期區間。
- request date 使用民國年格式，例如 `115/06/02`。
- 保留 raw JSON 到 `data/raw/ezmoney_pcf/`。
- 標準化 CSV 到 `data/processed/`。

## 新版刷新命令

刷新三檔 EZMoney 主動 ETF 最新快照，寫 manifest，並重建 dashboard：

```powershell
py -3.13 -B -m active_etf_radar refresh-ezmoney --allow-insecure-tls
```

只刷新單檔：

```powershell
py -3.13 -B -m active_etf_radar refresh-ezmoney --etf-code 00981A --allow-insecure-tls
```

只抓資料與 manifest，不重建 dashboard：

```powershell
py -3.13 -B -m active_etf_radar refresh-ezmoney --skip-dashboard --allow-insecure-tls
```

輸出：

- `reports/ezmoney_latest_manifest.csv`
- `reports/ezmoney_latest_manifest.json`
- `reports/dashboard.html`

## 2026-06-02 實跑結果

| ETF | FundCode | 名稱 | 筆數 | 權重合計 | 更新時間 |
|---|---|---|---:|---:|---|
| 00403A | 63YTW | 主動統一升級50 | 50 | 88.70% | 2026-06-02T16:39:47 |
| 00981A | 49YTW | 主動統一台股增長 | 51 | 97.16% | 2026-06-02T16:30:56 |
| 00988A | 61YTW | 主動統一全球創新 | 51 | 95.20% | 2026-06-02T16:32:55 |

## 查核規則

- 不信任使用者輸入的 ETF code 與 FundCode 配對，必須以 `DataFund.sStockNo` 反查。
- 每次抓取都保留 raw HTML/JSON。
- 每次 refresh 都寫 manifest，記錄 raw path、csv path、資料時間、更新時間、筆數、權重合計。
- dashboard 只吃標準化 CSV，不直接依賴網站 DOM。
- MCP snapshot 只作為流程查核證據，不作為每日資料來源。

