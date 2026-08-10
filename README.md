# 台股股票期貨 保證金 / 現價查詢

## 這是什麼

查詢每檔股票期貨(含小型)目前的**原始保證金金額**(買一口最少要準備多少錢),
同時抓取上市(TWSE)、上櫃(TPEx)股票的每日收盤價,作為之後「股期排行」(現貨標的、
期現價差)功能的資料基礎。

所有資料都來自交易所官方開放資料端點,不是爬一般網頁,不會卡防爬蟲。每個交易日
下午自動更新兩次,免費、不需要自己開電腦。

線上網址:`https://<你的 GitHub 帳號>.github.io/<repo 名稱>/`

## 檔案結構

```
fetch_margin_data.py    # 抓股票期貨保證金比例 + 收盤價,算出保證金金額
fetch_stock_prices.py   # 抓 TWSE 上市 + TPEx 上櫃股票收盤價(獨立腳本,可搬去別的專案用)
index.html              # 查詢介面,讀 margin_latest.json 顯示結果

margin_latest.json          # 保證金資料「最新一筆」,網頁實際讀這份
margin_YYYY-MM-DD.json      # 每個交易日的歷史快照,不會被覆蓋
stock_prices_latest.json    # 股票現價「最新一筆」
stock_prices_YYYY-MM-DD.json # 股票現價的歷史快照

.github/workflows/update-margin.yml  # GitHub Actions 排程設定
```

## 資料來源

- 股票期貨保證金比例:期交所政府資料開放平台
  `taifex_open_data.asp?data_name=SingleStockFuturesMargining`
- 期貨每日收盤價 / 未沖銷契約數:
  `taifex_open_data.asp?data_name=DailyMarketReportFut`
- 上市股票收盤價:TWSE OpenAPI `openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`
- 上櫃股票收盤價:TPEx 政府資料開放平台
  `tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/...`

都是免費、公開、供程式化存取用的官方端點,授權為「政府資料開放授權條款第1版」。

## 自動更新怎麼運作

GitHub Actions 在週一到週五,台灣時間下午 3:10 與晚上 7:10 各執行一次
(對應 UTC 07:10 / 11:10,設定在 `.github/workflows/update-margin.yml`):

1. 執行 `fetch_margin_data.py` → 產生當天的 `margin_YYYY-MM-DD.json` + 更新 `margin_latest.json`
2. 執行 `fetch_stock_prices.py` → 產生當天的 `stock_prices_YYYY-MM-DD.json` + 更新 `stock_prices_latest.json`
3. 有變動才 commit,推回 `main` 分支
4. GitHub Pages 直接把 repo 內容當網站服務,`index.html` 打開就會讀到最新資料

也可以到 repo 的 **Actions** 分頁手動按 **Run workflow** 立即執行,不用等排程時間。

## 本機手動執行(除錯用)

```bash
python fetch_margin_data.py
python fetch_stock_prices.py
python -m http.server 8000
```

再開 `http://localhost:8000/`。

## 保證金金額怎麼算

```
原始保證金金額 = 收盤價 × 契約乘數 × 原始保證金適用比例
契約乘數:一般股票期貨 2000 股/口;小型股票期貨 100 股/口(依名稱是否含「小型」判斷)
```

## 目前已知的限制

1. **金額是估算值**。取整方式(無條件進位到元)不一定跟期交所官方公告完全一致,
   正式交易請以期貨商/期交所公告的保證金為準,不要直接拿這個下單。
2. **保證金比例表沒有比對「更新日期」欄位是否變動**,現在是每次都重抓,之後可以
   改成只在期交所調整級距時才更新這部分。
3. **股票現價(TWSE/TPEx)還沒接進 `index.html`**,目前是獨立的資料檔,之後做
   「股期排行」(現貨標的、期現價差)才會用上。
4. **20 日均量還沒有歷史資料可用**,需要等每天的快照累積到一定天數,或去挖
   期交所的批次歷史下載一次補齊。
5. 如果哪天 GitHub Actions 顯示執行失敗,通常是期交所/證交所網站格式又改了,
   把錯誤訊息貼給我,照實際欄位調整即可。

## 授權

抓取的資料本身依「政府資料開放授權條款第1版」開放使用,可自由使用、需標示來源。
