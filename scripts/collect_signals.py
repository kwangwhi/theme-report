"""
장마감 신호 수집 (평일 15:50 KST, GitHub Actions)

1) 당일 전 종목 시세 → 등락률 ±10% 이상 종목, 거래대금 상위 50 종목 저장
   코스피·코스닥 투자자별 순매수, 외국인·기관 순매수 상위 10 종목 저장
2) 위 후보 종목의 최근 300거래일 일봉으로 신호 계산
   - 박스권 돌파, 골든크로스(하방경직), 볼린저 상단 돌파, 장기 저항선(돌파/시도), 첫 상한가 여부
3) data/signals/YYYY-MM-DD.json 저장. 실패한 항목은 errors 에 이유를 남기고 나머지는 저장한다.

데이터 경로(앞에서부터 시도, 실패하면 다음)
- 전 종목 당일 시세 : pykrx → FinanceDataReader StockListing
- 투자자별 순매수   : pykrx (KRX 회원 로그인 필요할 수 있음)
- 종목 일봉         : pykrx(네이버) → FinanceDataReader
- 종목명 보조       : KRX Open API 일별매매정보(전 영업일) — KRX_API_KEY 가 있을 때

비밀값(KRX_ID, KRX_PW, KRX_API_KEY)은 환경변수에서만 읽고 출력하지 않는다.
"""

import json
import os
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd

KST = timezone(timedelta(hours=9))
MARKETS = ["KOSPI", "KOSDAQ"]
TOP_VALUE_N = 50
MOVER_PCT = 10.0
NET_TOP_N = 10
HISTORY_DAYS = 300
LIMIT_UP_PCT = 29.5          # 상한가 판정(가격제한폭 30%, 호가 단위 반올림 감안)
REQUEST_PAUSE = 0.15         # 종목별 일봉 요청 사이 쉬는 시간(초)

errors = []


def err(where, e):
    msg = e if isinstance(e, str) else f"{type(e).__name__}: {str(e)[:300]}"
    errors.append({"where": where, "error": msg})
    print(f"::warning title={where}::{msg}")


def f2(v, nd=2):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return round(x, nd)


def i0(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return int(round(x))


# ---------------------------------------------------------------- 종목명 보조

def names_from_krx_openapi():
    """KRX Open API 일별매매정보(전 영업일)로 코드→(이름, 시장) 사전을 만든다."""
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        return {}
    out = {}
    today = datetime.now(KST).date()
    for path, market in (("sto/stk_bydd_trd", "KOSPI"), ("sto/ksq_bydd_trd", "KOSDAQ")):
        for back in range(1, 10):
            d = today - timedelta(days=back)
            if d.weekday() >= 5:
                continue
            url = f"https://data-dbg.krx.co.kr/svc/apis/{path}?basDd={d.strftime('%Y%m%d')}"
            try:
                req = urllib.request.Request(url, headers={"AUTH_KEY": key})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    rows = json.loads(resp.read().decode("utf-8")).get("OutBlock_1") or []
            except Exception:
                rows = []
            if rows:
                for r in rows:
                    code = str(r.get("ISU_CD", "")).strip()
                    if code:
                        out[code] = (str(r.get("ISU_NM", "")).strip(), market)
                break
    return out


# ---------------------------------------------------------------- 1) 당일 전 종목

def market_today_pykrx(d):
    from pykrx import stock
    frames = []
    for m in MARKETS:
        df = stock.get_market_ohlcv(d, market=m)
        if df is None or df.empty:
            raise RuntimeError(f"{m} 빈 데이터")
        df = df.copy()
        df["market"] = m
        frames.append(df)
    df = pd.concat(frames)
    need = {"종가", "거래량", "거래대금", "등락률"}
    if not need.issubset(df.columns):
        raise RuntimeError(f"열 부족: {list(df.columns)}")
    out = pd.DataFrame({
        "code": df.index.astype(str),
        "market": df["market"].values,
        "close": df["종가"].values,
        "rate": df["등락률"].values,
        "volume": df["거래량"].values,
        "value": df["거래대금"].values,
    })
    return out, "pykrx"


def market_today_fdr():
    import FinanceDataReader as fdr
    frames = []
    for m in MARKETS:
        df = fdr.StockListing(m)
        if df is None or df.empty:
            raise RuntimeError(f"{m} 빈 데이터")
        frames.append(pd.DataFrame({
            "code": df["Code"].astype(str).values,
            "market": m,
            "name": df["Name"].astype(str).values,
            "close": pd.to_numeric(df["Close"], errors="coerce").values,
            "rate": pd.to_numeric(df["ChagesRatio"], errors="coerce").values,
            "volume": pd.to_numeric(df["Volume"], errors="coerce").values,
            "value": pd.to_numeric(df["Amount"], errors="coerce").values,
        }))
    return pd.concat(frames, ignore_index=True), "FinanceDataReader"


def load_market_today(d):
    for fn, label in ((lambda: market_today_pykrx(d), "pykrx"), (market_today_fdr, "FinanceDataReader")):
        try:
            df, src = fn()
            df = df.dropna(subset=["close", "rate"])
            if df["value"].fillna(0).sum() <= 0:
                raise RuntimeError("거래대금 합계 0 — 휴장일이거나 아직 집계 전")
            return df, src
        except Exception as e:
            err(f"전 종목 당일 시세({label})", e)
    return None, None


def attach_names(df, d):
    if "name" in df.columns and df["name"].notna().all():
        return df
    names = {}
    try:
        from pykrx import stock
        for code in df["code"]:
            try:
                n = stock.get_market_ticker_name(code)
                if isinstance(n, str) and n:
                    names[code] = n
            except Exception:
                pass
    except Exception:
        pass
    missing = [c for c in df["code"] if c not in names]
    if missing:
        backup = names_from_krx_openapi()
        for c in missing:
            if c in backup:
                names[c] = backup[c][0]
    df = df.copy()
    df["name"] = [names.get(c, "") for c in df["code"]]
    unnamed = int((df["name"] == "").sum())
    if unnamed:
        err("종목명", f"{unnamed}개 종목 이름을 찾지 못함 (코드만 저장)")
    return df


def rows_of(df):
    return [{
        "code": r.code, "name": r.name, "market": r.market,
        "close": i0(r.close), "rate": f2(r.rate), "value": i0(r.value),
    } for r in df.itertuples(index=False)]


# ---------------------------------------------------------------- 1) 수급

def load_investors(d):
    from pykrx import stock
    result = {}
    for m in MARKETS:
        try:
            df = stock.get_market_trading_value_by_date(d, d, m)
            if df is None or df.empty:
                raise RuntimeError("빈 데이터 (KRX 로그인 필요 가능)")
            row = df.iloc[-1]
            result[m] = {str(k): i0(v) for k, v in row.items()}
        except Exception as e:
            err(f"투자자별 순매수({m})", e)
    return result


def load_net_buy_top(d):
    from pykrx import stock
    result = {}
    for m in MARKETS:
        result[m] = {}
        for investor, label in (("외국인", "외국인"), ("기관합계", "기관")):
            try:
                df = stock.get_market_net_purchases_of_equities(d, d, m, investor)
                if df is None or df.empty:
                    raise RuntimeError("빈 데이터 (KRX 로그인 필요 가능)")
                col = "순매수거래대금" if "순매수거래대금" in df.columns else df.columns[-1]
                df = df.sort_values(col, ascending=False).head(NET_TOP_N)
                result[m][label] = [{
                    "code": str(code),
                    "name": str(r.get("종목명", "")),
                    "net_value": i0(r.get(col)),
                } for code, r in df.iterrows()]
            except Exception as e:
                err(f"순매수 상위({m} {label})", e)
    return result


# ---------------------------------------------------------------- 2) 일봉과 신호

def daily_pykrx(code, start, end):
    from pykrx import stock
    df = stock.get_market_ohlcv(start, end, code)
    if df is None or df.empty:
        raise RuntimeError("빈 일봉")
    df = df.rename(columns={"시가": "Open", "고가": "High", "저가": "Low", "종가": "Close", "거래량": "Volume"})
    return df[["Open", "High", "Low", "Close", "Volume"]]


def daily_fdr(code, start, end):
    import FinanceDataReader as fdr
    df = fdr.DataReader(code, start, end)
    if df is None or df.empty:
        raise RuntimeError("빈 일봉")
    return df[["Open", "High", "Low", "Close", "Volume"]]


def load_daily(code, d):
    end_dt = datetime.strptime(d, "%Y%m%d")
    start = (end_dt - timedelta(days=500)).strftime("%Y%m%d")
    last_e = None
    for fn, label in ((daily_pykrx, "pykrx"), (daily_fdr, "FDR")):
        try:
            df = fn(code, start, d)
            df = df.apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"])
            # 거래 정지 등으로 시가·고가·저가가 0인 날은 종가로 채움
            for c in ("Open", "High", "Low"):
                df.loc[df[c] <= 0, c] = df.loc[df[c] <= 0, "Close"]
            df.index = pd.to_datetime(df.index)
            if df.index[-1].strftime("%Y%m%d") != d:
                raise RuntimeError(f"일봉 마지막 날짜가 {df.index[-1].date()} (오늘 데이터 없음)")
            return df.tail(HISTORY_DAYS), label
        except Exception as e:
            last_e = f"{label}: {type(e).__name__}: {str(e)[:150]}"
    raise RuntimeError(last_e)


def calc_signals(df):
    """df: 오늘이 마지막 행인 일봉(최대 300행). 각 신호를 dict 로 돌려준다."""
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    n = len(df)
    out = {"bars": n}
    today_close, today_high, today_low, today_vol = c.iloc[-1], h.iloc[-1], l.iloc[-1], v.iloc[-1]

    # 박스권 돌파: 직전 60일(오늘 제외) 고저폭 25% 이내 + 종가 > 60일 최고가 + 거래량 ≥ 직전 20일 평균 × 2
    if n >= 61:
        h60 = h.iloc[-61:-1].max()
        l60 = l.iloc[-61:-1].min()
        range_pct = (h60 - l60) / l60 * 100 if l60 > 0 else None
        vol20 = v.iloc[-21:-1].mean()
        vol_ratio = today_vol / vol20 if vol20 > 0 else None
        hit = (range_pct is not None and range_pct <= 25.0 and today_close > h60
               and vol_ratio is not None and vol_ratio >= 2.0)
        out["box_breakout"] = {"hit": bool(hit), "range60_pct": f2(range_pct), "high60": i0(h60),
                               "low60": i0(l60), "vol_ratio20": f2(vol_ratio)}
    else:
        out["box_breakout"] = {"hit": None, "reason": f"일봉 {n}개 (61개 필요)"}

    # 골든크로스(하방경직): 전일 MA5 ≤ MA20 → 당일 MA5 > MA20, 당일 저가 ≥ 당일 MA20
    if n >= 21:
        ma5 = c.rolling(5).mean()
        ma20 = c.rolling(20).mean()
        hit = (ma5.iloc[-2] <= ma20.iloc[-2] and ma5.iloc[-1] > ma20.iloc[-1] and today_low >= ma20.iloc[-1])
        out["golden_cross"] = {"hit": bool(hit), "ma5": f2(ma5.iloc[-1]), "ma20": f2(ma20.iloc[-1]),
                               "prev_ma5": f2(ma5.iloc[-2]), "prev_ma20": f2(ma20.iloc[-2]),
                               "low_above_ma20": bool(today_low >= ma20.iloc[-1])}
    else:
        out["golden_cross"] = {"hit": None, "reason": f"일봉 {n}개 (21개 필요)"}

    # 볼린저 상단 돌파(20일, 2σ, 모표준편차): 전일 종가는 밴드 안, 당일 종가 > 상단
    if n >= 21:
        mid = c.rolling(20).mean()
        sd = c.rolling(20).std(ddof=0)
        upper, lower = mid + 2 * sd, mid - 2 * sd
        prev_inside = lower.iloc[-2] <= c.iloc[-2] <= upper.iloc[-2]
        hit = bool(prev_inside and today_close > upper.iloc[-1])
        out["bollinger_break"] = {"hit": hit, "upper": f2(upper.iloc[-1]), "prev_upper": f2(upper.iloc[-2]),
                                  "prev_close_inside": bool(prev_inside)}
    else:
        out["bollinger_break"] = {"hit": None, "reason": f"일봉 {n}개 (21개 필요)"}

    # 장기 저항선: 당일 고가 ≥ 직전 250일(오늘 제외) 최고가 × 0.97 → 종가 ≥ 최고가면 '돌파', 아니면 '시도'
    if n >= 251:
        h250 = h.iloc[-251:-1].max()
        ratio = today_high / h250 * 100 if h250 > 0 else None
        status = None
        if ratio is not None and today_high >= h250 * 0.97:
            status = "돌파" if today_close >= h250 else "시도"
        out["long_resistance"] = {"status": status, "high250": i0(h250), "high_vs_high250_pct": f2(ratio)}
    else:
        out["long_resistance"] = {"status": None, "reason": f"일봉 {n}개 (251개 필요, 상장 기간 부족 가능)"}

    # 첫 상한가: 당일 상한가 여부 + 직전 20거래일(오늘 제외) 안의 상한가 횟수
    pct = c.pct_change() * 100
    today_up = bool(pct.iloc[-1] >= LIMIT_UP_PCT) if n >= 2 else None
    prior = pct.iloc[-21:-1] if n >= 21 else pct.iloc[1:-1]
    prior_count = int((prior >= LIMIT_UP_PCT).sum())
    prior_dates = [idx.strftime("%Y-%m-%d") for idx, val in prior.items() if val >= LIMIT_UP_PCT]
    out["limit_up"] = {"today": today_up, "prior20_count": prior_count, "prior20_dates": prior_dates,
                       "first": bool(today_up and prior_count == 0) if today_up is not None else None,
                       "checked_days": int(len(prior))}
    return out


# ---------------------------------------------------------------- main

def main():
    now = datetime.now(KST)
    d = os.environ.get("SIGNAL_DATE", "").strip() or now.strftime("%Y%m%d")
    day_iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    print(f"기준일 {day_iso}, KRX 로그인 정보: {'있음' if os.environ.get('KRX_ID') else '없음'}")

    result = {
        "date": day_iso,
        "generated_at_kst": now.strftime("%Y-%m-%d %H:%M"),
        "rules": {
            "movers": f"등락률 ±{MOVER_PCT}% 이상",
            "top_value": f"거래대금 상위 {TOP_VALUE_N}",
            "box_breakout": "직전 60일 (고가최고-저가최저)/저가최저 ≤ 25% + 종가 > 직전 60일 최고가 + 거래량 ≥ 직전 20일 평균×2",
            "golden_cross": "전일 MA5 ≤ MA20 → 당일 MA5 > MA20, 당일 저가 ≥ 당일 MA20",
            "bollinger_break": "20일·2σ(모표준편차). 전일 종가 밴드 안 → 당일 종가 > 상단",
            "long_resistance": "당일 고가 ≥ 직전 250일 최고가×0.97. 종가 ≥ 최고가 '돌파', 아니면 '시도'",
            "limit_up": f"일간 상승률 ≥ {LIMIT_UP_PCT}% 를 상한가로 판정, 직전 20거래일 상한가 횟수",
        },
        "sources": {},
        "movers": [],
        "top_value_50": [],
        "investors": {},
        "net_buy_top10": {},
        "signals": [],
        "summary": {},
        "errors": errors,
    }

    # 1) 당일 전 종목
    mkt, src = load_market_today(d)
    if mkt is None:
        # 휴장일 판단: 두 경로 모두 '거래대금 0'이면 파일을 만들지 않는다.
        if errors and all("거래대금 합계 0" in e["error"] for e in errors):
            print("휴장일 또는 아직 집계 전으로 판단 — 파일을 만들지 않음")
            return
    else:
        result["sources"]["market_today"] = src
        mkt = attach_names(mkt, d)
        movers = mkt[(mkt["rate"] >= MOVER_PCT) | (mkt["rate"] <= -MOVER_PCT)].sort_values("rate", ascending=False)
        top_value = mkt.sort_values("value", ascending=False).head(TOP_VALUE_N)
        result["movers"] = rows_of(movers)
        result["top_value_50"] = rows_of(top_value)
        print(f"전 종목 {len(mkt)}개 ({src}) → ±{MOVER_PCT}% {len(movers)}개, 거래대금 상위 {len(top_value)}개")

    # 1) 수급
    try:
        result["investors"] = load_investors(d)
        result["net_buy_top10"] = load_net_buy_top(d)
        result["sources"]["investors"] = "pykrx"
    except Exception as e:
        err("수급(pykrx 불러오기)", e)

    # 2) 신호
    if mkt is not None:
        top_codes = set(top_value["code"])
        mover_codes = set(movers["code"])
        cand = mkt[mkt["code"].isin(top_codes | mover_codes)]
        daily_src_count = {}
        for r in cand.itertuples(index=False):
            item = {"code": r.code, "name": r.name, "market": r.market, "close": i0(r.close),
                    "rate": f2(r.rate), "value": i0(r.value),
                    "selected_by": [x for x, ok in (("top_value_50", r.code in top_codes),
                                                    ("movers_10pct", r.code in mover_codes)) if ok]}
            try:
                df, dsrc = load_daily(r.code, d)
                daily_src_count[dsrc] = daily_src_count.get(dsrc, 0) + 1
                item.update(calc_signals(df))
            except Exception as e:
                item["error"] = f"{type(e).__name__}: {str(e)[:200]}"
                err(f"일봉·신호({r.code} {r.name})", e)
            result["signals"].append(item)
            time.sleep(REQUEST_PAUSE)
        result["sources"]["daily"] = daily_src_count

        sig = result["signals"]
        result["summary"] = {
            "candidates": len(sig),
            "box_breakout": [s["name"] or s["code"] for s in sig if s.get("box_breakout", {}).get("hit")],
            "golden_cross": [s["name"] or s["code"] for s in sig if s.get("golden_cross", {}).get("hit")],
            "bollinger_break": [s["name"] or s["code"] for s in sig if s.get("bollinger_break", {}).get("hit")],
            "long_resistance_break": [s["name"] or s["code"] for s in sig if s.get("long_resistance", {}).get("status") == "돌파"],
            "long_resistance_try": [s["name"] or s["code"] for s in sig if s.get("long_resistance", {}).get("status") == "시도"],
            "first_limit_up": [s["name"] or s["code"] for s in sig if s.get("limit_up", {}).get("first")],
            "repeat_limit_up": [s["name"] or s["code"] for s in sig
                                if s.get("limit_up", {}).get("today") and not s.get("limit_up", {}).get("first")],
            "signal_errors": sum(1 for s in sig if "error" in s),
        }

    os.makedirs(os.path.join("data", "signals"), exist_ok=True)
    path = os.path.join("data", "signals", f"{day_iso}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    s = result["summary"]
    print(f"::notice title=저장::{path} — 후보 {s.get('candidates', 0)}개, 오류 {len(errors)}건")
    for k in ("box_breakout", "golden_cross", "bollinger_break", "long_resistance_break",
              "long_resistance_try", "first_limit_up"):
        if k in s:
            print(f"::notice title={k}::{len(s[k])}개 {', '.join(s[k][:15])}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        print(f"::error title=중단::{type(e).__name__}: {e}")
        sys.exit(1)
