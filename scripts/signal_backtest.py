"""
신호 성적표 (과거 검증)

방법
1) KRX Open API 로 과거 N 영업일(기본 60일) 각각의 전 종목 일별매매정보를 받아,
   그날의 후보(거래대금 상위 50 + 등락률 ±10%)를 장마감 신호 수집과 같은 규칙으로 다시 뽑는다.
2) 각 후보 종목의 일봉을 받아 "그날까지의" 데이터만으로 신호를 재계산한다.
   (신호 계산은 scripts/collect_signals.py 의 calc_signals 를 그대로 가져다 써서 규칙이 똑같다)
3) 그 뒤 실제로 어떻게 됐는지 잰다.
   - 종가 진입(종가베팅): 다음날 시가 갭, 다음날 종가, 5일 뒤 종가, 5일 안 최고·최저
   - 다음날 시가 진입: 5일 뒤 종가
4) 신호별 성적을 "후보 전체 평균(기준선)"과 비교해 data/backtest/ 와 backtest.html 로 저장한다.

주의
- 수수료·세금·슬리피지 미반영. 상한가 종목은 종가에 실제로 살 수 없는 경우가 많다.
- 일봉은 수정주가 기준이다. 표본이 30건 미만인 신호는 우연일 가능성이 크다.
- 전 종목 원자료는 저장하지 않고 집계와 신호 발생 목록만 저장한다.
"""

import html
import json
import os
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_signals import calc_signals, TOP_VALUE_N, MOVER_PCT, HISTORY_DAYS  # noqa: E402

KST = timezone(timedelta(hours=9))
DAYS = int(os.environ.get("BACKTEST_DAYS", "60") or 60)
FWD = 5
PAUSE = 0.12
OUT_DIR = os.path.join("data", "backtest")


def num(v):
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def krx_rows(path, bas_dd, key):
    url = f"https://data-dbg.krx.co.kr/svc/apis/{path}?basDd={bas_dd}"
    req = urllib.request.Request(url, headers={"AUTH_KEY": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")).get("OutBlock_1") or []
    except Exception:
        return []


def universe_for(bas_dd, key):
    """그날의 후보: 거래대금 상위 50 + 등락률 ±10% (collect_signals 와 같은 규칙)."""
    rows = []
    for path, market in (("sto/stk_bydd_trd", "KOSPI"), ("sto/ksq_bydd_trd", "KOSDAQ")):
        for r in krx_rows(path, bas_dd, key):
            rate, value, close = num(r.get("FLUC_RT")), num(r.get("ACC_TRDVAL")), num(r.get("TDD_CLSPRC"))
            if rate is None or close is None:
                continue
            rows.append({"code": str(r.get("ISU_CD", "")).strip(), "name": str(r.get("ISU_NM", "")).strip(),
                         "market": market, "rate": rate, "value": value or 0.0, "close": close})
    if not rows:
        return []
    by_value = sorted(rows, key=lambda x: x["value"], reverse=True)[:TOP_VALUE_N]
    movers = [x for x in rows if abs(x["rate"]) >= MOVER_PCT]
    seen, out = set(), []
    for x in by_value + movers:
        if x["code"] and x["code"] not in seen:
            seen.add(x["code"])
            x["top_value"] = any(y["code"] == x["code"] for y in by_value)
            out.append(x)
    return out


def trading_days(key, n):
    """어제부터 거꾸로 KRX 데이터가 있는 영업일 n 개 (오래된 순)."""
    days = []
    d = datetime.now(KST).date()
    for _ in range(n * 2 + 20):
        d -= timedelta(days=1)
        if d.weekday() >= 5:
            continue
        dd = d.strftime("%Y%m%d")
        u = universe_for(dd, key)
        if u:
            days.append((dd, u))
            if len(days) >= n:
                break
    return list(reversed(days))


def load_daily(code, start, end):
    last = None
    try:
        from pykrx import stock
        df = stock.get_market_ohlcv(start, end, code)
        if df is not None and not df.empty:
            df = df.rename(columns={"시가": "Open", "고가": "High", "저가": "Low", "종가": "Close", "거래량": "Volume"})
            return clean(df)
    except Exception as e:
        last = e
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader(code, start, end)
        if df is not None and not df.empty:
            return clean(df)
    except Exception as e:
        last = e
    raise RuntimeError(f"일봉 실패: {type(last).__name__ if last else '빈 데이터'}")


def clean(df):
    df = df[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"])
    for c in ("Open", "High", "Low"):
        df.loc[df[c] <= 0, c] = df.loc[df[c] <= 0, "Close"]
    df.index = pd.to_datetime(df.index)
    return df


def pct(a, b):
    return None if a is None or b is None or b == 0 else (a / b - 1) * 100


def forward(df, pos):
    """pos 행(신호일) 이후 성과."""
    c0 = df["Close"].iloc[pos]
    nxt = df.iloc[pos + 1: pos + 1 + FWD]
    if nxt.empty:
        return None
    o1, c1 = nxt["Open"].iloc[0], nxt["Close"].iloc[0]
    r = {"gap": pct(o1, c0), "d1": pct(c1, c0)}
    if len(nxt) >= FWD:
        c5 = nxt["Close"].iloc[FWD - 1]
        r.update({"d5": pct(c5, c0), "open_d5": pct(c5, o1),
                  "max5": pct(nxt["High"].max(), c0), "min5": pct(nxt["Low"].min(), c0)})
    return r


SIGNALS = [
    ("box_breakout", "박스권 돌파", lambda s: (s.get("box_breakout") or {}).get("hit")),
    ("golden_cross", "골든크로스(하방경직)", lambda s: (s.get("golden_cross") or {}).get("hit")),
    ("bollinger_break", "볼린저 상단 돌파", lambda s: (s.get("bollinger_break") or {}).get("hit")),
    ("lr_break", "장기 저항선 돌파", lambda s: (s.get("long_resistance") or {}).get("status") == "돌파"),
    ("lr_try", "장기 저항선 시도", lambda s: (s.get("long_resistance") or {}).get("status") == "시도"),
    ("first_lu", "첫 상한가", lambda s: (s.get("limit_up") or {}).get("first")),
    ("repeat_lu", "재상한가", lambda s: (s.get("limit_up") or {}).get("today") and not (s.get("limit_up") or {}).get("first")),
]
METRICS = [("gap", "다음날 시가갭"), ("d1", "다음날 종가"), ("d5", "5일 뒤 종가"),
           ("open_d5", "시가진입→5일"), ("max5", "5일 내 최고"), ("min5", "5일 내 최저")]


def stats(values):
    v = [x for x in values if x is not None]
    if not v:
        return {"n": 0}
    return {"n": len(v), "mean": round(statistics.fmean(v), 2), "median": round(statistics.median(v), 2),
            "win": round(sum(1 for x in v if x > 0) / len(v) * 100, 1), "worst": round(min(v), 2), "best": round(max(v), 2)}


def group_stats(events):
    return {k: stats([e["fwd"].get(k) for e in events]) for k, _ in METRICS}


def main():
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        print("::error::KRX_API_KEY 없음")
        sys.exit(1)

    days = trading_days(key, DAYS)
    if not days:
        print("::error::KRX 영업일 데이터를 찾지 못함")
        sys.exit(1)
    first, last = days[0][0], days[-1][0]
    print(f"검증 기간 {first} ~ {last} ({len(days)}영업일)")

    codes = {}
    for dd, u in days:
        for x in u:
            codes.setdefault(x["code"], x["name"])
    print(f"후보 종목(중복 제외) {len(codes)}개 — 일봉 받는 중")

    start = (datetime.strptime(first, "%Y%m%d") - timedelta(days=470)).strftime("%Y%m%d")
    end = datetime.now(KST).strftime("%Y%m%d")
    daily, fail = {}, 0
    for i, code in enumerate(codes):
        try:
            daily[code] = load_daily(code, start, end)
        except Exception:
            fail += 1
        time.sleep(PAUSE)
        if (i + 1) % 200 == 0:
            print(f"  {i + 1}/{len(codes)}")
    print(f"일봉 성공 {len(daily)}, 실패 {fail}")

    events, skipped = [], 0
    for dd, u in days:
        ts = pd.Timestamp(datetime.strptime(dd, "%Y%m%d"))
        for x in u:
            df = daily.get(x["code"])
            if df is None or ts not in df.index:
                skipped += 1
                continue
            pos = df.index.get_loc(ts)
            sub = df.iloc[max(0, pos + 1 - HISTORY_DAYS): pos + 1]
            try:
                sig = calc_signals(sub)
            except Exception:
                skipped += 1
                continue
            fwd = forward(df, pos)
            if fwd is None:
                continue
            flags = [k for k, _, f in SIGNALS if f(sig)]
            events.append({"date": dd, "code": x["code"], "name": x["name"], "market": x["market"],
                           "rate": round(x["rate"], 2), "top_value": x["top_value"],
                           "flags": flags, "fwd": {k: (round(v, 2) if v is not None else None) for k, v in fwd.items()}})
    print(f"평가 건수 {len(events)} (건너뜀 {skipped})")

    groups = [("all", "기준선: 후보 전체", events),
              ("all_up10", "기준선: 당일 +10% 이상 전체", [e for e in events if e["rate"] >= 10]),
              ("all_topvalue", "기준선: 거래대금 상위 50 전체", [e for e in events if e["top_value"]])]
    for k, label, _ in SIGNALS:
        groups.append((k, label, [e for e in events if k in e["flags"]]))
    groups.append(("multi", "겹친 신호(2개 이상)", [e for e in events if len(e["flags"]) >= 2]))

    result = {
        "period": {"from": first, "to": last, "days": len(days)},
        "generated_at_kst": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "events_total": len(events),
        "notes": ["가격은 통합(KRX+NXT) 일봉 기준: 종가 = NXT 마감가, 시가 = 통합 시가", "후보 선정은 KRX 정규장 기준(KRX Open API)",
                  "수수료·세금·슬리피지 미반영", "종가 진입 기준(다음날 시가갭·종가·5일 뒤 종가), 시가진입→5일은 다음날 시가 매수 기준",
                  "수정주가 일봉", "표본 30건 미만은 신뢰도 낮음"],
        "groups": [{"key": k, "label": label, "stats": group_stats(ev)} for k, label, ev in groups],
        "recent_events": {k: [{kk: e[kk] for kk in ("date", "name", "market", "rate", "flags", "fwd")}
                              for e in sorted([e for e in events if k in e["flags"]], key=lambda e: e["date"], reverse=True)[:30]]
                          for k, _, _ in SIGNALS},
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "signal_stats.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    with open("backtest.html", "w", encoding="utf-8") as f:
        f.write(build_html(result))

    for g in result["groups"]:
        s = g["stats"]["d5"]
        print(f"::notice title={g['label']}::n={s.get('n')} 5일평균={s.get('mean')} 승률={s.get('win')}")


# ---------------------------------------------------------------- HTML

def esc(s):
    return html.escape(str(s if s is not None else ""))


def cell(v, base=None, pct_sign=True):
    if v is None:
        return '<td class="num">—</td>'
    cls = "up" if v > 0 else "down" if v < 0 else ""
    edge = ""
    if base is not None:
        diff = v - base
        if abs(diff) >= 0.5:
            edge = f' <small class="{"up" if diff > 0 else "down"}">({diff:+.1f})</small>'
    return f'<td class="num {cls}">{v:+.2f}%{edge}</td>' if pct_sign else f'<td class="num">{v:.1f}%{edge}</td>'


def build_html(r):
    groups = r["groups"]
    base = next(g for g in groups if g["key"] == "all")["stats"]
    p = r["period"]
    f = lambda d: f"{d[:4]}.{d[4:6]}.{d[6:]}"

    def row(g, is_base=False):
        s = g["stats"]
        n = s["d1"].get("n", 0)
        low = n < 30
        b = None if is_base else base
        name = esc(g["label"]) + (' <span class="tag warn">표본 적음</span>' if low and n else "")
        cells = (cell(s["gap"].get("mean"), b and b["gap"].get("mean"))
                 + cell(s["d1"].get("mean"), b and b["d1"].get("mean"))
                 + cell(s["d1"].get("win"), b and b["d1"].get("win"), pct_sign=False)
                 + cell(s["d5"].get("mean"), b and b["d5"].get("mean"))
                 + cell(s["d5"].get("median"), b and b["d5"].get("median"))
                 + cell(s["d5"].get("win"), b and b["d5"].get("win"), pct_sign=False)
                 + cell(s["open_d5"].get("mean"), b and b["open_d5"].get("mean"))
                 + cell(s["max5"].get("mean")) + cell(s["min5"].get("mean")) + cell(s["d5"].get("worst")))
        cls = ' class="base"' if is_base else ""
        return f"<tr{cls}><td>{name}</td><td class=\"num\">{n:,}</td>{cells}</tr>"

    head = ("<tr><th>신호</th><th class=\"num\">건수</th><th class=\"num\">다음날 시가갭</th><th class=\"num\">다음날 종가</th>"
            "<th class=\"num\">다음날 승률</th><th class=\"num\">5일 평균</th><th class=\"num\">5일 중앙값</th>"
            "<th class=\"num\">5일 승률</th><th class=\"num\">시가진입→5일</th><th class=\"num\">5일 내 최고</th>"
            "<th class=\"num\">5일 내 최저</th><th class=\"num\">5일 최악</th></tr>")
    base_rows = "".join(row(g, True) for g in groups if g["key"].startswith("all"))
    sig_rows = "".join(row(g) for g in groups if not g["key"].startswith("all"))

    recent = []
    labels = {k: l for k, l, _ in SIGNALS}
    for k, evs in r["recent_events"].items():
        if not evs:
            continue
        body = "".join(
            f"<tr><td>{f(e['date'])}</td><td>{esc(e['name'])}</td><td class=\"num\">{e['rate']:+.2f}%</td>"
            + cell(e["fwd"].get("gap")) + cell(e["fwd"].get("d1")) + cell(e["fwd"].get("d5")) + "</tr>"
            for e in evs[:12])
        recent.append(f'<section class="card"><h2>{esc(labels[k])} — 최근 사례</h2><table><tr><th>날짜</th><th>종목</th>'
                      f'<th class="num">당일</th><th class="num">익일 시가갭</th><th class="num">익일 종가</th>'
                      f'<th class="num">5일 뒤</th></tr>{body}</table></section>')

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>신호 성적표</title>
<style>
:root {{ --bg:#f4f5f7; --card:#fff; --text:#1a1d21; --muted:#6b7280; --line:#e5e7eb; --up:#d92d20; --down:#1d64d8;
  --warn-bg:#fff7e6; --warn:#8a5a00; --base:#f9fafb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#181b21; --text:#e8eaed; --muted:#9aa0a6; --line:#2a2e36;
  --up:#ff6b5e; --down:#5b9bff; --warn-bg:#2a2212; --warn:#f2c46d; --base:#1f232b; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); line-height:1.55;
  font-family:-apple-system,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif; }}
main {{ max-width:1280px; margin:0 auto; padding:24px 24px 56px; }}
h1 {{ font-size:1.6rem; margin:0 0 4px; }} h2 {{ font-size:1.05rem; margin:0 0 10px; }}
.stamp {{ color:var(--muted); font-size:.92rem; margin-bottom:14px; }}
.notice {{ background:var(--warn-bg); color:var(--warn); border-radius:10px; padding:10px 14px; font-size:.9rem; margin-bottom:16px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; overflow-x:auto; margin-bottom:14px; }}
.grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:14px; }} .grid .card {{ margin-bottom:0; }}
table {{ width:100%; border-collapse:collapse; font-size:.88rem; }}
th, td {{ text-align:left; padding:6px 5px; border-bottom:1px solid var(--line); white-space:nowrap; }}
th {{ color:var(--muted); font-weight:600; font-size:.76rem; }}
td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
tr.base td {{ background:var(--base); }}
.up {{ color:var(--up); font-weight:600; }} .down {{ color:var(--down); font-weight:600; }}
small {{ font-weight:500; }}
.tag {{ display:inline-block; font-size:.7rem; border:1px solid var(--line); border-radius:999px; padding:0 6px; margin-left:4px; color:var(--muted); }}
.tag.warn {{ color:var(--warn); border-color:var(--warn); }}
ul {{ margin:0; padding-left:18px; font-size:.88rem; }} li {{ margin-bottom:4px; }}
footer {{ color:var(--muted); font-size:.8rem; text-align:center; margin-top:22px; }} footer a {{ color:inherit; }}
@media (max-width:720px) {{ main {{ padding:16px 12px 40px; }} .grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<main>
  <h1>신호 성적표</h1>
  <div class="stamp">검증 기간 {f(p['from'])} ~ {f(p['to'])} ({p['days']}영업일) · 평가 {r['events_total']:,}건 · 생성 {esc(r['generated_at_kst'])}</div>
  <div class="notice">과거 성적이 미래를 보장하지 않습니다. 가격은 <b>통합(KRX+NXT) 일봉</b> 기준이라 "종가"는 NXT 마감(20:00) 가격, "시가"는 통합 시가입니다. 수수료·세금·슬리피지 미반영, 상한가 종목은 종가에 못 사는 경우가 많습니다. 괄호 안 숫자는 <b>후보 전체 기준선 대비 차이(%p)</b>입니다.</div>

  <section class="card">
    <h2>신호별 성적 (종가 진입 기준)</h2>
    <table>{head}{base_rows}{sig_rows}</table>
  </section>

  <section class="card">
    <h2>보는 법</h2>
    <ul>
      <li><b>기준선과 비교하세요.</b> 신호 성적이 좋아 보여도 "후보 전체"와 차이가 없으면 그 신호는 효과가 없는 것입니다.</li>
      <li><b>5일 평균보다 중앙값·승률</b>을 같이 보세요. 평균은 한두 종목 대박에 끌려 올라갑니다.</li>
      <li><b>다음날 시가갭</b>은 종가베팅의 핵심입니다. 플러스가 크면 종가 매수 후 시초가 매도 전략에 유리합니다.</li>
      <li><b>5일 내 최저</b>는 버텨야 하는 평균 흔들림입니다. 손절 폭을 이보다 좁게 잡으면 자주 털립니다.</li>
      <li>건수 30건 미만(<span class="tag warn">표본 적음</span>)은 우연일 수 있어 판단을 보류하세요. 매주 토요일 기간이 갱신됩니다.</li>
    </ul>
  </section>

  <div class="grid">{''.join(recent)}</div>

  <footer><a href="signals.html">장마감 신호</a> · <a href="morning.html">아침 준비 리포트</a> · <a href="index.html">장마감 시황</a> · 출처: KRX Open API, pykrx</footer>
</main>
</body>
</html>
"""


if __name__ == "__main__":
    main()
