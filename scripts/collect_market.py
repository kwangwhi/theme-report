"""
장마감 시황용 숫자 자동 수집 (A단계)

하는 일
- 코스피·코스닥 종가, 등락, 고저점, 거래대금
- 원/달러 환율
- 미국 3대 지수 + 필라델피아 반도체지수 (전날 밤 종가)
- 일본·중국·대만 지수
결과는 data/YYYY-MM-DD.json 과 data/latest.json 에 저장된다.

규칙
- 숫자를 지어내지 않는다. 받기에 실패한 항목은 errors 에 이유와 함께 남긴다.
- 각 항목에 '그 숫자가 어느 날짜 봉인지(bar_date)'를 같이 저장한다.
  리포트를 쓸 때 날짜가 오늘(또는 해외는 전날)과 다르면 오래된 숫자라는 뜻이다.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import FinanceDataReader as fdr

KST = timezone(timedelta(hours=9))

# (표시 이름, 구분, 시도할 심볼 목록) — 앞에서부터 시도해서 처음 성공한 것을 쓴다.
TARGETS = [
    ("코스피", "domestic", ["KS11"]),
    ("코스닥", "domestic", ["KQ11"]),
    ("원/달러", "fx", ["USD/KRW"]),
    ("다우", "us", ["DJI"]),
    ("S&P500", "us", ["US500"]),
    ("나스닥", "us", ["IXIC"]),
    ("필라델피아 반도체", "us", ["^SOX", "SOX"]),
    ("닛케이225", "asia", ["N225", "JP225", "^N225"]),
    ("상해종합", "asia", ["SSEC", "000001.SS"]),
    ("대만 가권", "asia", ["TWII", "^TWII"]),
]


def to_float(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def fetch_one(symbols, start):
    """심볼 후보를 차례로 시도해 마지막 봉과 그 직전 봉을 돌려준다."""
    last_error = None
    for symbol in symbols:
        try:
            df = fdr.DataReader(symbol, start)
        except Exception as e:  # 네트워크 차단, 심볼 오류 등
            last_error = f"{symbol}: {type(e).__name__}: {str(e)[:200]}"
            continue
        if df is None or df.empty or "Close" not in df.columns:
            last_error = f"{symbol}: 빈 데이터"
            continue
        df = df.dropna(subset=["Close"])
        if len(df) < 2:
            last_error = f"{symbol}: 봉이 2개 미만"
            continue
        return symbol, df, None
    return None, None, last_error


def build_item(name, group, symbol, df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    close = to_float(last["Close"])
    prev_close = to_float(prev["Close"])
    change = None
    change_pct = None
    if close is not None and prev_close not in (None, 0):
        change = round(close - prev_close, 4)
        change_pct = round((close - prev_close) / prev_close * 100, 2)

    item = {
        "name": name,
        "group": group,
        "symbol": symbol,
        "bar_date": df.index[-1].strftime("%Y-%m-%d"),
        "close": close,
        "prev_close": prev_close,
        "prev_bar_date": df.index[-2].strftime("%Y-%m-%d"),
        "change": change,
        "change_pct": change_pct,
        "open": to_float(last.get("Open")),
        "high": to_float(last.get("High")),
        "low": to_float(last.get("Low")),
    }
    # 거래대금(원)은 국내 지수에만 있다.
    amount = to_float(last.get("Amount")) if "Amount" in df.columns else None
    if amount is not None:
        item["amount_krw"] = amount
        item["amount_jo"] = round(amount / 1e12, 2)  # 조 원 단위
    return item


def main():
    now = datetime.now(KST)
    start = (now - timedelta(days=20)).strftime("%Y-%m-%d")

    result = {
        "generated_at_kst": now.strftime("%Y-%m-%d %H:%M"),
        "source": "FinanceDataReader",
        "note": "bar_date가 기대 날짜와 다르면 오래된 숫자다. change_pct는 직전 봉 종가 대비.",
        "items": [],
        "errors": [],
    }

    for name, group, symbols in TARGETS:
        symbol, df, err = fetch_one(symbols, start)
        if err:
            result["errors"].append({"name": name, "error": err})
            print(f"[실패] {name}: {err}")
            continue
        item = build_item(name, group, symbol, df)
        result["items"].append(item)
        print(f"[성공] {name} ({symbol}) {item['bar_date']} 종가 {item['close']} ({item['change_pct']}%)")

    os.makedirs("data", exist_ok=True)
    day_path = os.path.join("data", f"{now.strftime('%Y-%m-%d')}.json")
    for path in (day_path, os.path.join("data", "latest.json")):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n성공 {len(result['items'])}개 / 실패 {len(result['errors'])}개 → {day_path}")

    # 하나도 못 받았으면 실행 실패로 표시해 GitHub에서 빨간색으로 보이게 한다.
    if not result["items"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
