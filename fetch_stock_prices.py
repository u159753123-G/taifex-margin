#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股(上市 TWSE + 上櫃 TPEx)每日收盤價抓取腳本
===========================================

用途:
  抓取「現貨」股票的官方每日收盤資料,給股期排行表的「現貨標的、現貨價格、期現價差」用,
  同時輸出格式刻意設計成跟你另一個 Portfolio Web App 專案要的 market-data.json 一致,
  之後那個專案可以直接拿這支腳本(或它的輸出格式)過去用,不用重寫一次。

⚠️ 這支獨立於 fetch_margin_data.py,兩者互不依賴,可以單獨複製到別的專案使用。

資料來源(已驗證,官方開放資料,不是爬蟲):
  1. TWSE 上市 OpenAPI:
     https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
     已實際打通,回傳全部上市股票 JSON,欄位:
     Date, Code, Name, TradeVolume, TradeValue, OpeningPrice,
     HighestPrice, LowestPrice, ClosingPrice, Change, Transaction

  2. TPEx 上櫃(政府資料開放平台指定端點):
     https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php?l=zh-tw&se=EW&o=data
     已確認能連上(狀態 200,CSV,UTF-8),但實際欄位名稱我還沒親眼看過真實內容,
     用關鍵字模糊比對,第一次執行請把終端機印出的「偵測到的欄位」貼給我確認。

輸出格式(跟 Portfolio 專案的 market-data.json 規格一致):
{
  "generatedAt": "ISO-8601 timestamp",
  "tradeDate": "YYYY-MM-DD",
  "symbols": {
    "2330": {
      "symbol": "2330", "name": "台積電", "market": "TWSE",
      "close": 0, "open": 0, "high": 0, "low": 0, "volume": 0, "value": 0,
      "tradeDate": "YYYY-MM-DD", "source": "TWSE", "status": "fresh"
    }
  },
  "errors": []
}

保留歷史:
  每次執行會另外存一份帶日期的快照 stock_prices_YYYY-MM-DD.json,
  不會覆蓋掉之前幾天的資料,同時更新 stock_prices_latest.json 給程式讀「最新一筆」用。

容錯設計(照 Portfolio 專案規格做的):
  - TWSE 和 TPEx 分開記錄成功或失敗,其中一邊掛掉不會讓另一邊也抓不到。
  - 如果某個市場這次抓失敗,會盡量沿用「上一份 latest.json」裡同市場的舊資料,
    並把該筆資料標成 status = "stale",而不是整個消失或變 0。
  - 任一市場這次抓到的筆數如果異常過少(判斷為抓取失敗/格式跑掉),
    一樣觸發保留舊資料的邏輯,不會用少量錯誤資料覆蓋掉正確的舊資料。
"""

import csv
import glob
import io
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone

TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_URL = "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php?l=zh-tw&se=EW&o=data"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-stock-app/1.0)"}

# 少於這個數字就當作抓取失敗,不覆蓋舊資料(上市目前約 1000 檔以上,上櫃約 800 檔以上,抓打折預留空間)
MIN_TWSE_RECORDS = 500
MIN_TPEX_RECORDS = 300


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def to_number(s):
    if s is None:
        return None
    s = str(s).replace(",", "").replace("%", "").strip()
    if s in ("", "-", "--", "X", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def roc_to_iso(s):
    """把民國日期(不管是 1150807 還是 115/08/07)轉成 2026-08-07 這種格式。"""
    digits = re.sub(r"\D", "", str(s or ""))
    if len(digits) < 5:
        return None
    mmdd, year_roc = digits[-4:], digits[:-4]
    try:
        y = int(year_roc) + 1911
        m, d = int(mmdd[:2]), int(mmdd[2:])
        return f"{y:04d}-{m:02d}-{d:02d}"
    except ValueError:
        return None


def find_col(fieldnames, keywords):
    for name in fieldnames:
        for kw in keywords:
            if kw in name:
                return name
    return None


def fetch_twse(errors):
    """回傳 (symbols_dict, trade_date) 或在失敗時回傳 (None, None)。"""
    try:
        raw = fetch_bytes(TWSE_URL)
        data = json.loads(decode_bytes(raw))
    except Exception as e:
        errors.append({"market": "TWSE", "message": f"抓取或解析失敗: {e}"})
        return None, None

    if not isinstance(data, list) or len(data) < MIN_TWSE_RECORDS:
        errors.append({"market": "TWSE", "message": f"筆數異常過少({len(data) if isinstance(data, list) else 'N/A'} 筆),視為失敗"})
        return None, None

    symbols = {}
    trade_date = None
    for row in data:
        symbol = str(row.get("Code") or "").strip()
        if not symbol:
            continue
        td = roc_to_iso(row.get("Date"))
        trade_date = trade_date or td
        symbols[symbol] = {
            "symbol": symbol,
            "name": (row.get("Name") or "").strip(),
            "market": "TWSE",
            "close": to_number(row.get("ClosingPrice")),
            "open": to_number(row.get("OpeningPrice")),
            "high": to_number(row.get("HighestPrice")),
            "low": to_number(row.get("LowestPrice")),
            "volume": to_number(row.get("TradeVolume")),
            "value": to_number(row.get("TradeValue")),
            "tradeDate": td,
            "source": "TWSE",
            "status": "fresh",
        }
    return symbols, trade_date


def fetch_tpex(errors):
    try:
        raw = fetch_bytes(TPEX_URL)
        text = decode_bytes(raw)
    except Exception as e:
        errors.append({"market": "TPEx", "message": f"抓取失敗: {e}"})
        return None, None

    lines = [ln for ln in text.splitlines() if ln.strip()]
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    rows = list(reader)
    fields = reader.fieldnames or []
    print("[TPEx] 偵測到的欄位:", fields)

    col_symbol = find_col(fields, ["代號", "股票代號", "證券代號"])
    col_name = find_col(fields, ["名稱", "證券名稱"])
    col_close = find_col(fields, ["收盤"])
    col_open = find_col(fields, ["開盤"])
    col_high = find_col(fields, ["最高"])
    col_low = find_col(fields, ["最低"])
    col_volume = find_col(fields, ["成交股數", "成交量"])
    col_date = find_col(fields, ["資料日期", "日期"])

    if not (col_symbol and col_close):
        errors.append({"market": "TPEx", "message": f"關鍵欄位比對失敗,偵測到的欄位: {fields}"})
        return None, None

    if len(rows) < MIN_TPEX_RECORDS:
        errors.append({"market": "TPEx", "message": f"筆數異常過少({len(rows)} 筆),視為失敗"})
        return None, None

    symbols = {}
    trade_date = None
    for row in rows:
        symbol = (row.get(col_symbol) or "").strip()
        if not symbol:
            continue
        td = roc_to_iso(row.get(col_date)) if col_date else None
        trade_date = trade_date or td
        symbols[symbol] = {
            "symbol": symbol,
            "name": (row.get(col_name) or "").strip() if col_name else "",
            "market": "TPEx",
            "close": to_number(row.get(col_close)),
            "open": to_number(row.get(col_open)) if col_open else None,
            "high": to_number(row.get(col_high)) if col_high else None,
            "low": to_number(row.get(col_low)) if col_low else None,
            "volume": to_number(row.get(col_volume)) if col_volume else None,
            "tradeDate": td,
            "source": "TPEx",
            "status": "fresh",
        }
    return symbols, trade_date


def load_previous_latest():
    try:
        with open("stock_prices_latest.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def build_trade_dates_index():
    """掃描目前資料夾裡有的 stock_prices_YYYY-MM-DD.json 快照,
    產生 trade_dates.json(日期由新到舊排序),
    給網頁端知道「有哪幾天的快照可以抓」,不用網頁自己列資料夾(靜態網頁做不到這件事)。"""
    dates = set()
    for filename in glob.glob("stock_prices_????-??-??.json"):
        m = re.search(r"stock_prices_(\d{4}-\d{2}-\d{2})\.json$", filename)
        if m:
            dates.add(m.group(1))

    sorted_dates = sorted(dates, reverse=True)
    with open("trade_dates.json", "w", encoding="utf-8") as f:
        json.dump({"dates": sorted_dates}, f, ensure_ascii=False, indent=2)

    return sorted_dates


def main():
    errors = []

    print("== 抓取 TWSE 上市股票 ==")
    twse_symbols, twse_date = fetch_twse(errors)
    print(f"TWSE: {'成功, ' + str(len(twse_symbols)) + ' 檔' if twse_symbols else '失敗'}")

    print("\n== 抓取 TPEx 上櫃股票 ==")
    tpex_symbols, tpex_date = fetch_tpex(errors)
    print(f"TPEx: {'成功, ' + str(len(tpex_symbols)) + ' 檔' if tpex_symbols else '失敗'}")

    previous = load_previous_latest()
    prev_symbols = (previous or {}).get("symbols", {})

    combined = {}

    if twse_symbols:
        combined.update(twse_symbols)
    else:
        # TWSE 這次失敗,沿用舊資料裡屬於 TWSE 的部分,標成 stale
        for sym, row in prev_symbols.items():
            if row.get("market") == "TWSE":
                row = dict(row)
                row["status"] = "stale"
                combined[sym] = row

    if tpex_symbols:
        combined.update(tpex_symbols)
    else:
        for sym, row in prev_symbols.items():
            if row.get("market") == "TPEx":
                row = dict(row)
                row["status"] = "stale"
                combined[sym] = row

    trade_date = twse_date or tpex_date or (previous or {}).get("tradeDate")

    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tradeDate": trade_date,
        "symbols": combined,
        "errors": errors,
    }

    if not combined:
        print("\n!! TWSE 和 TPEx 這次都失敗,而且沒有舊資料可以沿用,不寫檔,避免產生空結果。")
        sys.exit(1)

    date_tag = trade_date or datetime.now().strftime("%Y-%m-%d")
    dated_filename = f"stock_prices_{date_tag}.json"

    for filename in (dated_filename, "stock_prices_latest.json"):
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

    fresh_count = sum(1 for r in combined.values() if r.get("status") == "fresh")
    stale_count = sum(1 for r in combined.values() if r.get("status") == "stale")
    print(f"\n完成!共 {len(combined)} 檔股票(新鮮 {fresh_count} 檔、沿用舊資料 {stale_count} 檔)")
    print(f"已輸出 {dated_filename} 與 stock_prices_latest.json")

    trade_dates = build_trade_dates_index()
    print(f"已更新 trade_dates.json,目前累積 {len(trade_dates)} 個交易日的快照")

    if errors:
        print("\n本次錯誤紀錄:")
        for e in errors:
            print(f"  - [{e['market']}] {e['message']}")


if __name__ == "__main__":
    main()
