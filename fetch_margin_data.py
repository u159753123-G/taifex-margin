#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台灣股票期貨「即時最低保證金」資料抓取腳本
===========================================

用途:
  抓取期交所「政府資料開放平台」官方端點(taifex_open_data.asp),
  這組端點是特地給程式化存取用的開放資料,不是一般網頁,
  不需要 User-Agent 偽裝、不會被防爬蟲機制擋(截至撰寫時測試通過)。

抓兩份資料:
  1. SingleStockFuturesMargining — 每檔個股期貨的保證金"適用比例"
     (結算/維持/原始保證金 %),更新頻率低(期交所調整級距才會變)。
  2. DailyMarketReportFut       — 期貨每日交易行情(收盤價/結算價),
     這是每個交易日都會變的部分。

計算:
  原始保證金金額(= 買進一口最少要準備的保證金)
    = 收盤價(或結算價) × 契約乘數 × 原始保證金適用比例
  契約乘數: 一般股票期貨 = 2000 股/口;小型股票期貨 = 100 股/口
           (依中文簡稱是否含「小型」判斷)

輸出:
  margin_YYYY-MM-DD.json — 當天的歷史快照,不會被覆蓋,以後做漲跌/OI增減會需要
  margin_latest.json     — 給 index.html 讀取用的「最新一筆」資料檔

⚠️ 重要提醒(請先讀完再執行):
  本腳本必須在「能連上 taifex.com.tw 的一般網路環境」下執行,
  例如你自己的電腦、或 GitHub Actions 這類雲端 CI。
  我(Claude)目前所在的沙盒環境對外網域有白名單限制,
  沒辦法在這裡直接跑一次驗證欄位名稱與編碼是否完全正確,
  所以下面的欄位比對用「關鍵字模糊比對」而非寫死欄名,
  盡量在期交所小改版時仍然堪用。

  第一次執行時建議先看終端機印出的「欄位偵測結果」,
  如果比對錯誤,把印出的實際欄名回報給我,我再幫你調整。
"""

import csv
import io
import json
import math
import re
import sys
import urllib.request
from datetime import datetime

MARGIN_URL = "https://www.taifex.com.tw/data_gov/taifex_open_data.asp?data_name=SingleStockFuturesMargining"
DAILY_URL = "https://www.taifex.com.tw/data_gov/taifex_open_data.asp?data_name=DailyMarketReportFut"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; personal-margin-app/1.0)"
}


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def decode_csv_bytes(raw: bytes) -> str:
    """期交所這組舊式 ASP 端點常見是 Big5/CP950,也可能已經是 UTF-8,兩種都試。"""
    for enc in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # 都失敗的話,用 utf-8 忽略錯誤字元,至少讓程式能繼續跑
    return raw.decode("utf-8", errors="ignore")


def sniff_and_read_csv(text: str):
    text = text.strip("﻿ \n")
    # 有些期交所 CSV 開頭會有標題列以外的說明列,過濾掉空行
    lines = [ln for ln in text.splitlines() if ln.strip()]
    cleaned = "\n".join(lines)
    reader = csv.DictReader(io.StringIO(cleaned))
    rows = [row for row in reader]
    return reader.fieldnames or [], rows


def find_col(fieldnames, keywords):
    """用關鍵字模糊比對欄位名稱,keywords 任一命中即可。"""
    for name in fieldnames:
        for kw in keywords:
            if kw in name:
                return name
    return None


def to_float(s):
    if s is None:
        return None
    s = str(s).replace(",", "").replace("%", "").replace("＄", "").replace("$", "").strip()
    if s in ("", "-", "--", "N/A", "NA"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_code(s):
    """清掉常見的隱藏字元:引號、= 開頭(Excel 強制文字格式)、全形空白、大小寫差異。"""
    if s is None:
        return ""
    s = str(s).strip()
    if s.startswith("=") and '"' in s:
        s = s.split('"')[1] if len(s.split('"')) > 1 else s.lstrip("=")
    s = s.strip('"').strip("'")
    s = s.replace("　", "").replace("\xa0", "")
    return s.strip().upper()


def normalize_date(s):
    """把 2026/08/07 這種格式轉成 2026-08-07,期交所這邊本來就是西元年,不用轉民國。"""
    if not s:
        return None
    s = str(s).strip().replace("/", "-")
    parts = s.split("-")
    if len(parts) == 3:
        try:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            return f"{y:04d}-{m:02d}-{d:02d}"
        except ValueError:
            return s
    return s


def main():
    print("== 步驟 1: 下載保證金適用比例表 ==")
    margin_raw = fetch_bytes(MARGIN_URL)
    margin_text = decode_csv_bytes(margin_raw)
    margin_fields, margin_rows = sniff_and_read_csv(margin_text)
    print("偵測到的欄位:", margin_fields)
    if not margin_rows:
        print("!! 沒有抓到任何資料列,請檢查 margin_text 前 500 字:")
        print(margin_text[:500])
        sys.exit(1)

    col_code = find_col(margin_fields, ["股票期貨英文代碼", "英文代碼"])
    col_stock_no = find_col(margin_fields, ["標的證券代號", "證券代號"])
    col_name = find_col(margin_fields, ["中文簡稱"])
    col_underlying = find_col(margin_fields, ["標的證券"])
    col_tier = find_col(margin_fields, ["所屬級距", "級距"])
    col_settle_pct = find_col(margin_fields, ["結算保證金"])
    col_maint_pct = find_col(margin_fields, ["維持保證金"])
    col_init_pct = find_col(margin_fields, ["原始保證金"])

    required = [col_code, col_stock_no, col_name, col_init_pct]
    if not all(required):
        print("!! 關鍵欄位比對失敗,請把上面印出的『偵測到的欄位』回報給我調整。")
        sys.exit(1)

    print("\n== 步驟 2: 下載期貨每日行情(收盤價) ==")
    daily_raw = fetch_bytes(DAILY_URL)
    daily_text = decode_csv_bytes(daily_raw)
    daily_fields, daily_rows = sniff_and_read_csv(daily_text)
    print("偵測到的欄位:", daily_fields)

    d_col_code = find_col(daily_fields, ["交易系統代碼", "契約", "商品代號"])
    d_col_close = find_col(daily_fields, ["收盤價", "結算價"])
    d_col_expiry = find_col(daily_fields, ["到期月份", "契約月份", "到期月份(週別)"])
    d_col_date = find_col(daily_fields, ["交易日期", "日期"])
    d_col_oi = find_col(daily_fields, ["未沖銷契約數", "未沖銷"])
    d_col_volume = find_col(daily_fields, ["成交量"])

    trade_date = None
    price_by_code = {}
    if d_col_code and d_col_close:
        for row in daily_rows:
            code = normalize_code(row.get(d_col_code))
            close = to_float(row.get(d_col_close))
            if not code or close is None:
                continue
            if trade_date is None and d_col_date:
                trade_date = normalize_date(row.get(d_col_date))
            expiry = (row.get(d_col_expiry) or "").strip()
            # 同一檔股票期貨可能有近月/遠月多筆,取「到期月份字串排序最小」當近月
            prev = price_by_code.get(code)
            if prev is None or (expiry and expiry < prev["expiry"]):
                price_by_code[code] = {
                    "close": close,
                    "expiry": expiry,
                    "open_interest": to_float(row.get(d_col_oi)) if d_col_oi else None,
                    "volume": to_float(row.get(d_col_volume)) if d_col_volume else None,
                }
    else:
        print("!! 每日行情欄位比對失敗,margin_latest.json 會先只輸出保證金比例,沒有金額。")

    print(f"[診斷] 這次抓到的交易日期(trade_date): {trade_date}")

    print(f"\n共取得 {len(price_by_code)} 檔契約的收盤價對照。")

    # --- debug: 印出兩邊的代碼樣本,方便對不上的時候排查 ---
    margin_codes_sample = [normalize_code(r.get(col_code)) for r in margin_rows[:8]]
    price_codes_sample = list(price_by_code.keys())[:8]
    print("保證金表代碼樣本(正規化後):", margin_codes_sample)
    print("每日行情代碼樣本(正規化後):", price_codes_sample)
    print("保證金表代碼樣本(原始 repr):", [repr(r.get(col_code)) for r in margin_rows[:3]])
    if d_col_code:
        print("每日行情代碼樣本(原始 repr):", [repr(r.get(d_col_code)) for r in daily_rows[:3]])

    # --- debug 2: 這份每日行情原始資料到底有多少筆、是否真的涵蓋股票期貨 ---
    margin_code_set = {normalize_code(r.get(col_code)) for r in margin_rows if r.get(col_code)}
    all_daily_codes_raw = [normalize_code(r.get(d_col_code)) for r in daily_rows] if d_col_code else []
    daily_code_set_raw = set(all_daily_codes_raw)
    overlap = margin_code_set & daily_code_set_raw
    print(f"\n[診斷] 每日行情『未篩選近月』前總筆數: {len(daily_rows)}")
    print(f"[診斷] 每日行情『未篩選近月』前不重複代碼數: {len(daily_code_set_raw)}")
    print(f"[診斷] 保證金表股票期貨代碼數: {len(margin_code_set)}")
    print(f"[診斷] 兩邊代碼交集數(未篩選近月): {len(overlap)}")
    print(f"[診斷] 交集範例: {list(overlap)[:10]}")
    print(f"[診斷] 每日行情前 20 個不重複代碼: {sorted(daily_code_set_raw)[:20]}")
    print(f"[診斷] 保證金表前 20 個代碼: {sorted(margin_code_set)[:20]}")

    print("\n== 步驟 3: 合併計算最低保證金金額 ==")
    # --- debug 3: 看原始保證金比例欄位的原始字串長什麼樣,以及解析後的值 ---
    sample_row = margin_rows[0] if margin_rows else {}
    print("[診斷] 原始保證金比例欄位原始值 repr:", repr(sample_row.get(col_init_pct)))
    print("[診斷] 解析後 init_pct:", to_float(sample_row.get(col_init_pct)))
    if col_maint_pct:
        print("[診斷] 維持保證金比例欄位原始值 repr:", repr(sample_row.get(col_maint_pct)))

    result = []
    for row in margin_rows:
        code_raw = (row.get(col_code) or "").strip()
        code = normalize_code(code_raw)
        name = (row.get(col_name) or "").strip()
        if not code:
            continue

        multiplier = 100 if "小型" in name else 2000

        init_pct = to_float(row.get(col_init_pct))
        maint_pct = to_float(row.get(col_maint_pct)) if col_maint_pct else None
        settle_pct = to_float(row.get(col_settle_pct)) if col_settle_pct else None

        price_info = price_by_code.get(code)
        close_price = price_info["close"] if price_info else None

        init_amount = None
        if close_price is not None and init_pct is not None:
            # 期交所實務上會取整到新台幣元,這裡先用無條件進位示範,
            # 正式的捨入規則請以期交所公告為準
            init_amount = math.ceil(close_price * multiplier * (init_pct / 100))

        result.append({
            "code": code,
            "stock_no": (row.get(col_stock_no) or "").strip(),
            "name": name,
            "underlying": (row.get(col_underlying) or "").strip() if col_underlying else "",
            "tier": (row.get(col_tier) or "").strip() if col_tier else "",
            "multiplier": multiplier,
            "settle_pct": settle_pct,
            "maint_pct": maint_pct,
            "init_pct": init_pct,
            "close_price": close_price,
            "init_margin_amount": init_amount,
            "open_interest": price_info["open_interest"] if price_info else None,
            "volume": price_info["volume"] if price_info else None,
        })

    date_tag = trade_date or datetime.now().strftime("%Y-%m-%d")

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "trade_date": trade_date,
        "source": {
            "margin_ratio": MARGIN_URL,
            "daily_price": DAILY_URL,
        },
        "contracts": result,
    }

    dated_filename = f"margin_{date_tag}.json"
    for filename in (dated_filename, "margin_latest.json"):
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

    have_price = sum(1 for r in result if r["init_margin_amount"] is not None)
    print(f"\n完成!共 {len(result)} 檔股票期貨,其中 {have_price} 檔有算出金額。")
    print(f"已輸出 {dated_filename} 與 margin_latest.json,可搭配 index.html 使用。")


if __name__ == "__main__":
    main()
