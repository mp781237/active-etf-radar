# active-etf-radar 專案筆記

> 2026-06-12 從 000_Agent/memory/MEMORY.md 分流，內容逐字保留。涵蓋專案沿革、資料來源接入、UI/產品方向偏好、踩坑。**新進展直接寫這裡**，MEMORY.md 只保留現況摘要。

### active-etf-radar — 主動式 ETF 持股研究工具(2026-05-25 建立)

- **位置**:`100_Todo/projects/2026-05-25_active-etf-radar/`
- **目的**:追蹤台灣主動式 ETF 公開持股資料,先以 `00981A 主動統一台股增長` 為標的;保留 raw HTML,標準化持股 CSV,後續再寫 SQLite、持股變化、產業鏈配置與每日研究報告。
- **安全邊界**:非交易系統;不產生買賣/停損/停利訊號;不接券商 API;不自動下單;只用公開網頁/API/下載檔或手動 CSV。
- **2026-05-25 第一版**:已建立 Python 標準庫 adapter `active_etf_radar.sources.ezmoney`,可抓 EZMoney 公開頁 `FundCode=49YTW` 並輸出 raw HTML + 標準化 CSV。指令:`py -3.13 -m active_etf_radar fetch-ezmoney --fund-code 49YTW --etf-code 00981A --allow-insecure-tls`
- **已驗證輸出**:抓到 52 檔股票持股,權重合計 96.31%;前幾大為台積電、台光電、聯發科、智邦、台達電。原始 HTML 在 `data/raw/ezmoney/`,CSV 在 `data/processed/`。
- **2026-05-25 可視化**:已新增 `active_etf_radar.dashboard` 與 CLI `build-dashboard`,可從最新 CSV 產生 self-contained `reports/dashboard.html`。頁面包含總覽 KPI、前十五大持股權重長條圖、權重區間分布、幣別分布、完整持股表搜尋、資料查核區;明確標示非交易系統。
- **2026-05-25 PCF 歷史查詢**:已新增 `active_etf_radar.sources.ezmoney_pcf` 與 CLI `fetch-pcf`,使用 EZMoney 官方公開 PCF 頁 `/ETF/Transaction/PCF` 先建立公開 session,再 POST `/ETF/Transaction/GetPCF` 查詢指定日期。可回查,例如 `--date 2026-05-25` 回傳資料日 `2026-05-22`, `--date 2026-05-26` 回傳資料日 `2026-05-25`;raw JSON 存 `data/raw/ezmoney_pcf/`,標準化 CSV 存 `data/processed/`。
- **2026-05-25 持股變化**:已用 PCF 兩份快照跑 `compare`,輸出 `reports/holding_changes.csv`。比較結果拆成 `share_status`(股數增加/減少/不變/新增/移除)與 `weight_status`(權重增加/降低/持平/新增/移除),避免把股價造成的權重漂移誤判成實際增減持。Dashboard 已加入持股變化區塊。
- **2026-05-26 連續增持**:已新增 `active_etf_radar.streaks`。Dashboard 會用多日快照計算「連續增加持股」並輸出 `reports/holding_streaks.csv`;同一資料日只採最新快照,以 `shares` 股數判斷,不用權重。已補抓 PCF 查詢日 2026-05-20~2026-05-26,有效資料日為 2026-05-19、05-20、05-21、05-22、05-25;目前最新資料日只有金像電、漢唐、奇鋐為連續 1 次增加,沒有連續 >=2 次。
- **2026-05-26 多基金雷達**:已加入 `00988A 主動統一全球創新`(`FundCode=61YTW`) PCF 快照,有效資料日 2026-05-18、05-19、05-20、05-21、05-22。新增 `active_etf_radar.multi_fund`,Dashboard 若有兩檔以上 ETF 會顯示多基金總覽、共識持股與同向股數變化;輸出 `reports/multi_fund_overview.csv`、`reports/multi_fund_consensus.csv`。設計原則:不直接混合各基金權重。共識持股至少要 2 檔基金共同持有,以「持有基金數」+「各基金權重標籤」呈現;同向股數變化至少要 2 檔基金同時加股數。2026-05-26 Ray 覺得「基金 × 股票矩陣」沒意義,已移除。
- **2026-05-26 Dashboard 切分**:Ray 覺得下半段 00981A/00988A 有混在一起,已改成「上半部多基金總覽,下半部單基金分頁」。`00981A` 與 `00988A` 的 KPI、前十五大、連續增持、持股變化、完整持股表、資料查核各自隔離;搜尋只作用於目前分頁的 ETF。
- **EZMoney 對照踩坑**:`49YTW` = `00981A 主動統一台股增長`;Ray 一開始貼的 `61YTW` = `00988A 主動統一全球創新`,不是 00981A。
- **技術踩坑**:EZMoney 會先 302 到同 URL 並發 `__nxquid` cookie;Python `urllib` 必須用 `CookieJar` 否則 redirect loop。Python 3.13/OpenSSL 對該站憑證會報 `Missing Subject Key Identifier`,目前 adapter 需明確加 `--allow-insecure-tls` 才抓公開頁。PCF 頁目前沒有 `__RequestVerificationToken`,不要硬解析 token。

## 2026-05-26 補充：active-etf-radar 00991A

- `00991A` 是復華投信 `主動復華未來50`，不是統一 EZMoney 來源。
- 官方公開頁：`https://www.fhtrust.com.tw/ETF/etf_detail/ETF23`
- 官方完整持股 Excel 端點：`https://www.fhtrust.com.tw/api/assetsExcel/ETF23/YYYYMMDD`
- 專案已新增 `fetch-fhtrust`，保留原始 XLSX 到 `data/raw/fhtrust_assets/`，並標準化成 `data/processed/holdings_00991A_ETF23_fhtrust_assets_*.csv`。
- 區間查詢會略過週末或無資料日；2026-05-18 到 2026-05-25 已抓到 05/18、05/19、05/20、05/21、05/22、05/25。
- Browser 外掛目前會因 URL 安全政策拒絕 `file://` dashboard 驗證；不要用改網址或替代瀏覽器繞過，改用本機檔案/CSV 靜態驗證，或請 Ray 手動刷新 in-app browser。

## 2026-05-26 補充：active-etf-radar 00403A / 00407A

- `00403A` 是統一投信 `主動統一升級50`，EZMoney FundCode 為 `63YTW`，可沿用 `fetch-pcf`。
- 00403A 已抓 2026-05-20 到 2026-05-26 查詢區間；有效資料日為 2026-05-19、05-20、05-21、05-22、05-25。最新股票權重合計約 81.39%。
- `00407A` 是凱基投信 `主動凱基台灣`，截至 2026-05-26 尚未開始募集；公開資訊顯示預計 2026-06-04 至 2026-06-10 募集，尚無正式公開持股快照。
- Dashboard 已讓 00407A 只出現在「待資料基金」提示，不納入共識持股與同向變化計算。

## 2026-06-01 active-etf-radar 顯示偏好：新增與加股要分開

- Ray 明確糾正：`今日新增持股` 只要真正新的持股，也就是 `share_status = 新增`；不要把 `股數增加` 混進去。
- 正確做法：把 `新增` 與 `股數增加` 拆成不同研究區塊。新增持股用「今日新增持股」；加股或權重放大用獨立的「異常增持」或其他名稱。
- 原因：Ray 想看「第一次出現在持股裡」與「既有部位大幅加碼」兩種完全不同的訊號，混在一起會讓共識持股與新增持股重複、判讀混亂。
- UI 偏好：研究摘要卡不要把基金代號、權重、變化幅度、狀態全塞進一個超長膠囊標籤；應拆成有層次的小資訊片，讓股票名稱與數值分區清楚。

## 2026-06-02 active-etf-radar 產品方向：事件雷達優先

- Ray 明確要求：簡單好用最重要，要一眼發現「新買進的共識」或「突然被加進持股的標的」。
- Dashboard 首頁應先放事件雷達，不要先放大表格。優先順序：新買進且進入共識、突然納入持股、異常加碼；共識持股總表與單基金明細往下放，作為查詢與回查。
- 「新買進共識」的含義：最新資料日有基金第一次買進，且該股票目前已由至少 2 檔基金共同持有。事件卡 badge 顯示標的數，不是基金數；股票旁要列出目前全部共持基金與權重，只有本輪新買進基金加上「新」標記，避免看起來像只抓到一檔基金。
- Ray 指出 `首次共識持股` 與 `新買進共識` 在畫面上容易重複、干擾判讀；首頁不應再獨立放 `首次共識持股` 卡片。若仍需保留，可只作為共識表內的小標籤或查詢欄位。
- 外部主動 ETF 日報的「買進/調節幾億」口徑應用 `股數變化 × 最新持股價格` 估算，最新持股價格可由 `new_market_value / new_shares` 取得；不要直接用 `market_value_change` 當買賣金額，因為它會混入持股價格變化。台股日報比對要先過濾 4 碼台股代號，避免 00988A 的外股干擾排行。
- `同向股數變化` 也屬於事件雷達，應放在首頁事件卡群一起看；不要放在共識持股總表旁邊，避免表格被右側輔助卡壓縮。

## 2026-06-02 active-etf-radar 新增資料來源：群益與第一金

- 已接入 `00992A 主動群益台灣科技創新`：群益官方申購買回清單，product id `500`，CLI：`fetch-capital --product-id 500 --etf-code 00992A`。
- 已接入 `00997A 主動群益美國增長`：群益官方申購買回清單，product id `502`，CLI：`fetch-capital --product-id 502 --etf-code 00997A`。
- 群益來源保留 `data/raw/capital_buyback/*.html`，標準化輸出 `holdings_*_capital_buyback_*.csv`；股票資料日要取股票表前的括號日期，例如匯率日，不要取頁面 date picker 或付款日的最大日期。
- 已接入 `00994A 主動第一金台股優`：第一金官方公開 AJAX `https://www.fsitc.com.tw/WebAPI.aspx/Get_hd`，fund id `182`，CLI：`fetch-firstsitc --fund-id 182 --etf-code 00994A --allow-insecure-tls`。
- 第一金來源保留 `data/raw/firstsitc_hd/*.json`，標準化輸出 `holdings_*_firstsitc_hd_*.csv`；Python 3.13 可能因網站憑證缺 `Subject Key Identifier` 需要 `--allow-insecure-tls`。
- 事件卡與共識表必須使用同一個 canonical 股票名稱；若新買進來源名稱不同，例如 `富世達股` vs `富世達`，`新買進共識` 事件卡應採共識表名稱，並在共識表同列標示 `新買進共識` 與新買進基金權重 `新`。
# 2026-06-02 active-etf-radar EZMoney 持股流程決策

- EZMoney ETF 最新持股不再靠手動記 FundCode；使用 `active_etf_radar/funds.py` registry 管理 `00403A=63YTW`、`00981A=49YTW`、`00988A=61YTW`。
- 每日刷新優先跑 `python -B -m active_etf_radar refresh-ezmoney --allow-insecure-tls`，它會保留 raw HTML、輸出標準化 CSV、寫 `reports/ezmoney_latest_manifest.csv/json`，並重建 dashboard。
- Microsoft Playwright MCP 驗證過：`FundCode=61YTW` 實際是 `00988A 主動統一全球創新`，不是 `00981A`；後續不可把 61YTW 當 00981A。

## 2026-06-12 active-etf-radar 群益來源改 JS 渲染（已修）

- 2026-06 群益官網 `https://www.capitalfund.com.tw/etf/product/detail/{id}/buyback` 改成前端 JS 渲染，`urllib` 抓到的只是空殼頁（title「群益投信」、有 `noscript`、無申購/買回/持股關鍵字），導致 `fetch-capital` 報 `群益申購買回清單缺少資料日期`。
- 用 Playwright MCP 攔截 network 找到真正資料端點：`POST https://www.capitalfund.com.tw/CFWeb/api/etf/buyback`，body `{"fundId":"<product_id>","date":null}`，回傳 `{code,data:{pcf:{date1,date2,exchangeDesc},stocks:[{stocNo,stocName,weight,weightRound,share}]},message}`。
- 已改寫 `active_etf_radar/sources/capital_buyback.py`：改打 JSON API、raw 改存 `data/raw/capital_buyback/*.json`、dataclass 欄位 `raw_html_path` 改名 `raw_path`（cli.py 同步），資料日沿用舊規則取 `exchangeDesc` 括號匯率日（= `date2`）。
- 重要：00997A（群益美國增長）、00988A 等含美股/日股/韓股（`MU US`、`4062 JP`、`009150 KS`），股票明細**不可只留台股 4 碼代號**，否則持股會被砍到只剩台股。canonical 過濾條件＝有代號+名稱+權重就收；台股 4 碼過濾只用在台股日報排行那一層。
- 副作用：舊 HTML parser 對 00997A 美股結構抓不全（舊 CSV 只 10 檔、權重合計 40.9%），新 API 拿到完整 53 檔（93.16%）。換來源那一輪 00997A 的持股變化會出現一次性假新增，下一輪自動修正。
- 2026-06-12 全 7 檔已刷新到當日：00403A/00981A/00988A（EZMoney，資料更新 6-11）、00991A 6-11、00992A 6-11、00994A 6-11、00997A 6-10。dashboard 已重建。
- 2026-06-22 全 7 檔再刷新（資料日 6/17~6/18）：00403A/00981A/00988A（EZMoney，ETF 公告更新 6-18）、00991A 6-18、00992A 6-18、00994A 6-18、00997A 6-17。dashboard + 全部 multi_fund/streaks/holding_changes CSV 已重建，無 iCloud 殘骸。亮點：記憶體題材明顯（華邦電 2344 在 00403A/00994A 雙加、南亞科 2408、美光/SanDisk/WDC/KIOXIA 群增）；7/7 全共識＝聯發科 2454、台光電 2383、台積電 2330、台達電 2308（但台積電/台達電有 3 檔基金減股數）；同向加碼最強＝旺矽 6223、國巨* 2327、聯電 2303；單檔最大跳升＝大立光 3008 在 00992A +3.03pp（1.17→4.2）。研究標籤非交易訊號。
- Playwright MCP 會在 cwd 對應的 iCloud 根目錄留 `.playwright-mcp/`（console log + page yml 快照），屬暫存 churn，用完要清。

## 2026-06-12 active-etf-radar dashboard 表達模式升級（趨勢線＋今日結論）

- Ray 問 dashboard 表達模式能否更好。判斷：最大缺口是已有 15 個資料日快照，但畫面只表達「最新 vs 前一日」單點，0 條趨勢線。Ray 選「趨勢線＋今日結論」優先。
- 已改 `active_etf_radar/dashboard.py`（單一來源，重建保留）：① 共識持股總表新增「近期股數趨勢」欄，每列一條 inline SVG sparkline，資料為該股票在所有持有基金的總股數沿聯合資料日 forward-fill 取最近 12 個資料日，漲綠跌紅；② 頁面頂端（事件雷達上方）新增深色「今日結論」橫幅，白話彙總新買進共識/首次納入/異常加碼/同向加股檔數。新增函式 `_build_cross_fund_share_series`、`_render_sparkline`、`_build_today_summary`。
- `_render_legacy_html` 是未被呼叫的死碼；改 CSS 時兩處 f-string 區塊用 replace_all 一起改，實際只有 `_render_html` 會渲染。
- 2026-06-12 當天因 00997A 來源遷移，今日結論「異常加碼」灌水成 83 檔（多 ~43 假新增），下一輪快照自動修正；功能本身正確。
- 2026-06-12 Ray 接著說「都做」，已完成三項追加：① 事件卡強度排序（同向股數變化補上 same_share_increase_count→streak→max_weight 排序，其餘卡本來就強度排序）＋ CSS `.event-list .insight-row:first-child` 把每張卡最強第一筆加左側強調條與淡底；② 第一屏深色 NASA hero：`header` 改深藍 radial 漸層＋象牙白字＋琥珀 chip，今日結論橫幅 border-radius 接在 header 下緣形成一整塊深色 hero；③ 異常加碼卡與單基金完整持股表也加 sparkline（小尺寸 60x18，`_render_sparkline(small=True)`，單基金表用 `_build_per_fund_share_series` 各基金自己的股數序列）。已用本機 http.server + Playwright 截圖驗證視覺正常（file:// 仍被擋，要用 http server 繞）。
- 2026-06-12 Ray 要求事件雷達不要拆成四張卡，改成單一大區塊，以標籤頁切換 `新買進共識`、`突然納入持股`、`異常加碼`、`同向股數變化`。預設開啟第一個有資料的分頁，讓列表區更大，第一屏不要被多張小卡切碎。
- 2026-06-12 Ray 要求 `同向股數變化` 的細節呈現跟 `異常加碼` 一樣，不要只顯示「幾檔加」。已把每檔加股基金的目前權重、權重變化 pp、股數增加量、連續加股數帶進 chip。
- 密集表格維持淺色底，只有 hero/今日結論用深色，符合 Ray 深色 NASA 編輯風偏好但不犧牲查表可讀性。

## 2026-06-22 active-etf-radar 事件雷達統整

- Ray 同意把事件清單從「四個事件類型」升級成「股票焦點統整」。Dashboard 事件 tabs 改為 `今日焦點`、`新進持股`、`集體加碼`、`單基金異常`。
- `今日焦點` 以股票為單位合併四種訊號：新買進共識、首次納入、集體加股、單基金異常。排序優先同時踩到多個訊號的標的，再看新買進共識、同向加股檔數、共持基金數與最大權重增加。
- `新進持股` 內若該股票也進入共識，直接標示 `新買進共識`；`集體加碼` 改列完整同向加股清單，不再只截前 10 檔。
## 2026-06-29 EZMoney 資料日修正

- EZMoney 的 `EndDate` 會反映頁面查詢時間，週末抓取時可能晚於實際持股公告日。
- 標準化 CSV 的 `as_of_datetime` 改以 `EditDate` 日期為準，完整 `EditDate` 仍保留在 `edit_datetime`。
- 這可避免週末更新時，統一三檔被誤列為週末新資料，造成跨基金新進持股與事件雷達錯位。

## 2026-07-26 事件日股價反應

- 四個事件分頁新增「事件資料日收盤價＋當日漲跌幅」；台股採 TWSE/TPEX 官方日行情，海外股票採 Yahoo Finance 歷史行情。
- 行情依各基金自己的持股資料日配對，00997A 若落後一天，不會誤套其他基金的最新日期。
- 原始行情保留在 `data/raw/market_prices/`，標準化快取寫入 `reports/event_market_prices.csv`。
- 畫面明示這是同日價格反應，只能觀察相關性，不能直接推論基金持股變化造成股價漲跌。

## 2026-08-01 群益來源維護

- 00403A、00981A、00988A、00991A、00994A 已更新至 2026-07-31。
- 群益申購買回 API 當日回傳「系統維護中」HTML，00992A、00997A 保留既有最新快照，不誤標更新日期。
- `capital_buyback` 解碼器已支援 UTF-8 BOM，並會把維護頁明確回報為來源維護，不再顯示模糊的 JSON 解析錯誤。

## 2026-08-09 00981A 台指期貨部位

- EZMoney `DataAsset` 的台指期貨位於 `AssetCode=GD`，舊解析器只讀 `ST`，因此原始 HTML 有資料但標準化 CSV 與 dashboard 遺漏。
- 解析器現會保留所有有明細的資產，並新增 `position`、`contract_month` 欄位；單基金頁將期貨獨立列示，不混入股票持股數、共識持股、加減碼與連續增持。
- 2026-08-07 的 00981A 公開部位：`TX 台指期貨(B)`、1,940 口、2026/08、名目金額 17,187,236,000 元、淨值占比 5.75%。GitHub Actions 全流程與 Pages 部署通過（commit `48740f9`，資料更新 `4975c46`）。
