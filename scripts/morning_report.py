"""
아침 준비 리포트 (KRX Open API, 전 영업일 기준)

하는 일
- 가장 최근 영업일 데이터를 KRX Open API 에서 받아 morning.html 을 만든다.
  (KRX Open API 는 전날 데이터를 다음 영업일 오전 8시에 제공)
- 내용: 지수 요약, 업종지수 강약, 상승률 상위, 상한가, 거래대금 상위, 하락률 상위
- 약관(제3자 제공 금지)을 고려해 전 종목 원자료는 저장하지 않고,
  상위 목록 요약과 업종지수만 data/krx/ 에 남긴다.

규칙
- 인증키(KRX_API_KEY)는 환경변수에서만 읽고 출력하지 않는다.
- 받지 못한 항목은 지어내지 않고 "데이터 없음"으로 표시한다.
"""

import html
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
BASE = "https://data-dbg.krx.co.kr/svc/apis"

STOCK_APIS = [("유가증권", "sto/stk_bydd_trd"), ("코스닥", "sto/ksq_bydd_trd")]
INDEX_APIS = [("KOSPI", "idx/kospi_dd_trd"), ("KOSDAQ", "idx/kosdaq_dd_trd")]

# 업종 강약에서 뺄 지수(규모·대표지수 등 업종이 아닌 것)
NON_SECTOR_KEYWORDS = [
    "200", "100", "150", "50", "대형", "중형", "소형", "우량", "배당", "고배당",
    "레버리지", "인버스", "선물", "변동성", "ESG", "TOP", "Top", "가치", "성장",
    "모멘텀", "로우볼", "커버드", "리츠", "외국인", "기관", "벤처", "글로벌",
    "중소", "KRX", "프리미어", "동일가중", "코스피", "코스닥", "중견",
]

MIN_TRADE_VALUE = 1_000_000_000  # 상승률·하락률 순위에 넣을 최소 거래대금(10억 원)
TOP_N = 20


def num(value):
    """'1,234' / '-' / '' 같은 문자열을 숫자로. 실패하면 None."""
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def call(path, bas_dd, key):
    url = f"{BASE}/{path}?basDd={bas_dd}"
    req = urllib.request.Request(url, headers={"AUTH_KEY": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        print(f"[호출 실패] {path} {bas_dd}: {type(e).__name__}")
        return None
    rows = data.get("OutBlock_1")
    return rows if isinstance(rows, list) and rows else None


def find_latest_day(key):
    """오늘부터 거꾸로 영업일을 찾아 유가증권 데이터가 있는 가장 최근 날짜를 돌려준다."""
    today = datetime.now(KST).date()
    for back in range(0, 10):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        bas_dd = d.strftime("%Y%m%d")
        rows = call(STOCK_APIS[0][1], bas_dd, key)
        if rows:
            return bas_dd, rows
    return None, None


def is_spac(name):
    return "스팩" in name


def is_preferred(name):
    return name.endswith("우") or name.endswith("우B") or name.endswith("우C") or "우(전환)" in name


def load_stocks(bas_dd, first_rows, key):
    stocks = []
    for i, (market, path) in enumerate(STOCK_APIS):
        rows = first_rows if i == 0 else call(path, bas_dd, key)
        if not rows:
            print(f"[경고] {market} 종목 데이터 없음")
            continue
        for r in rows:
            name = str(r.get("ISU_NM", "")).strip()
            close = num(r.get("TDD_CLSPRC"))
            rate = num(r.get("FLUC_RT"))
            value = num(r.get("ACC_TRDVAL"))
            if not name or close is None or rate is None:
                continue
            stocks.append({
                "market": market,
                "name": name,
                "code": str(r.get("ISU_CD", "")).strip(),
                "close": close,
                "change": num(r.get("CMPPREVDD_PRC")),
                "rate": rate,
                "value": value or 0.0,
                "mktcap": num(r.get("MKTCAP")),
                "spac": is_spac(name),
                "pref": is_preferred(name),
            })
    return stocks


def load_indices(bas_dd, key):
    out = {"main": [], "sectors": []}
    for market, path in INDEX_APIS:
        rows = call(path, bas_dd, key)
        if not rows:
            print(f"[경고] {market} 지수 데이터 없음")
            continue
        for r in rows:
            name = str(r.get("IDX_NM", "")).strip()
            close = num(r.get("CLSPRC_IDX"))
            rate = num(r.get("FLUC_RT"))
            if not name or close is None or rate is None:
                continue
            item = {
                "market": market,
                "name": name,
                "close": close,
                "change": num(r.get("CMPPREVDD_IDX")),
                "rate": rate,
                "high": num(r.get("HGPRC_IDX")),
                "low": num(r.get("LWPRC_IDX")),
                "value": num(r.get("ACC_TRDVAL")),
            }
            if name in ("코스피", "코스닥"):
                out["main"].append(item)
            elif not any(k in name for k in NON_SECTOR_KEYWORDS):
                out["sectors"].append(item)
    return out


# ---------- HTML ----------

def fmt_price(v):
    return "—" if v is None else f"{v:,.0f}"


def fmt_idx(v):
    return "—" if v is None else f"{v:,.2f}"


def fmt_rate(v):
    if v is None:
        return '<td class="num">—</td>'
    cls = "up" if v > 0 else "down" if v < 0 else ""
    sign = "+" if v > 0 else ""
    return f'<td class="num {cls}">{sign}{v:.2f}%</td>'


def fmt_eok(v):
    """원 → 억 원"""
    return "—" if not v else f"{v / 1e8:,.0f}억"


def esc(s):
    return html.escape(str(s))


def tag(stock):
    t = ""
    if stock["pref"]:
        t += ' <span class="tag">우선주</span>'
    return t


def stock_table(rows, show_value=True):
    if not rows:
        return '<p class="muted">데이터 없음</p>'
    head = "<tr><th>#</th><th>종목</th><th>시장</th><th class=\"num\">종가</th><th class=\"num\">등락률</th>"
    head += "<th class=\"num\">거래대금</th></tr>" if show_value else "</tr>"
    body = []
    for i, s in enumerate(rows, 1):
        line = (f"<tr><td>{i}</td><td>{esc(s['name'])}{tag(s)}</td><td>{esc(s['market'])}</td>"
                f"<td class=\"num\">{fmt_price(s['close'])}</td>{fmt_rate(s['rate'])}")
        if show_value:
            line += f"<td class=\"num\">{fmt_eok(s['value'])}</td>"
        body.append(line + "</tr>")
    return f"<table>{head}{''.join(body)}</table>"


def index_table(rows):
    if not rows:
        return '<p class="muted">데이터 없음</p>'
    head = "<tr><th>지수</th><th class=\"num\">종가</th><th class=\"num\">등락률</th></tr>"
    body = "".join(
        f"<tr><td>{esc(r['name'])} <span class=\"tag\">{esc(r['market'])}</span></td>"
        f"<td class=\"num\">{fmt_idx(r['close'])}</td>{fmt_rate(r['rate'])}</tr>"
        for r in rows
    )
    return f"<table>{head}{body}</table>"


STYLE = """
:root { --bg:#f6f7f9; --card:#ffffff; --text:#1a1d21; --muted:#6b7280; --line:#e5e7eb;
  --up:#d92d20; --down:#1d64d8; --warn-bg:#fff7e6; --warn:#8a5a00; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1115; --card:#181b21; --text:#e8eaed; --muted:#9aa0a6; --line:#2a2e36;
    --up:#ff6b5e; --down:#5b9bff; --warn-bg:#2a2212; --warn:#f2c46d; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); line-height:1.6;
  font-family:-apple-system,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif; }
main { max-width:760px; margin:0 auto; padding:20px 16px 48px; }
h1 { font-size:1.5rem; margin:0 0 4px; }
h2 { font-size:1.1rem; margin:0 0 10px; }
.stamp { color:var(--muted); font-size:.9rem; margin-bottom:16px; }
.notice { background:var(--warn-bg); color:var(--warn); border-radius:10px; padding:10px 14px;
  font-size:.9rem; margin-bottom:16px; }
section { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px;
  margin-bottom:14px; overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:.92rem; }
th, td { text-align:left; padding:6px 4px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:.82rem; }
td.num, th.num { text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }
.up { color:var(--up); font-weight:600; } .down { color:var(--down); font-weight:600; }
.tag { display:inline-block; font-size:.72rem; color:var(--muted); border:1px solid var(--line);
  border-radius:999px; padding:0 7px; margin-left:3px; }
.muted { color:var(--muted); font-size:.9rem; }
.grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
@media (max-width:640px) { .grid { grid-template-columns:1fr; } }
.grid section { margin-bottom:0; }
footer { color:var(--muted); font-size:.8rem; text-align:center; margin-top:20px; }
footer a { color:inherit; }
"""


def build_html(bas_dd, idx, stocks, made_at, warnings):
    d = datetime.strptime(bas_dd, "%Y%m%d")
    weekday = "월화수목금토일"[d.weekday()]
    day_label = f"{d.year}년 {d.month}월 {d.day}일({weekday})"

    pool = [s for s in stocks if not s["spac"]]
    liquid = [s for s in pool if s["value"] >= MIN_TRADE_VALUE]

    gainers = sorted(liquid, key=lambda s: s["rate"], reverse=True)[:TOP_N]
    losers = sorted(liquid, key=lambda s: s["rate"])[:10]
    by_value = sorted(pool, key=lambda s: s["value"], reverse=True)[:TOP_N]
    limit_up = sorted([s for s in pool if s["rate"] >= 29.5], key=lambda s: s["value"], reverse=True)

    up_count = sum(1 for s in pool if s["rate"] > 0)
    down_count = sum(1 for s in pool if s["rate"] < 0)

    sectors = sorted(idx["sectors"], key=lambda r: r["rate"], reverse=True)
    strong = sectors[:7]
    weak = list(reversed(sectors[-7:])) if len(sectors) > 7 else []

    main_rows = ""
    for r in idx["main"]:
        main_rows += (f"<tr><td>{esc(r['name'])}</td><td class=\"num\">{fmt_idx(r['close'])}</td>"
                      f"{fmt_rate(r['rate'])}<td class=\"num\">{fmt_idx(r['low'])} ~ {fmt_idx(r['high'])}</td>"
                      f"<td class=\"num\">{fmt_eok(r['value'])}</td></tr>")
    main_table = (f"<table><tr><th>지수</th><th class=\"num\">종가</th><th class=\"num\">등락률</th>"
                  f"<th class=\"num\">저가 ~ 고가</th><th class=\"num\">거래대금</th></tr>{main_rows}</table>"
                  if main_rows else '<p class="muted">데이터 없음</p>')

    notice = "전 영업일 거래소 확정 데이터입니다. 매수·매도 추천이 아닙니다."
    if warnings:
        notice += " · 일부 데이터 누락: " + esc(", ".join(warnings))

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>아침 준비 리포트</title>
<style>{STYLE}</style>
</head>
<body>
<main>
  <h1>아침 준비 리포트</h1>
  <div class="stamp">{day_label} 종가 기준 · 생성 {esc(made_at)}</div>
  <div class="notice">{notice}</div>

  <section>
    <h2>① 지수</h2>
    {main_table}
    <p class="muted" style="margin:10px 0 0">상승 {up_count:,}종목 · 하락 {down_count:,}종목 (코스피+코스닥, 스팩 제외)</p>
  </section>

  <div class="grid">
    <section>
      <h2>② 강한 업종</h2>
      {index_table(strong)}
    </section>
    <section>
      <h2>③ 약한 업종</h2>
      {index_table(weak)}
    </section>
  </div>
  <div style="height:14px"></div>

  <section>
    <h2>④ 상한가 ({len(limit_up)}종목)</h2>
    {stock_table(limit_up) if limit_up else '<p class="muted">상한가 종목 없음</p>'}
  </section>

  <section>
    <h2>⑤ 상승률 상위 {TOP_N}</h2>
    <p class="muted" style="margin:0 0 8px">거래대금 10억 원 이상, 스팩 제외</p>
    {stock_table(gainers)}
  </section>

  <section>
    <h2>⑥ 거래대금 상위 {TOP_N}</h2>
    <p class="muted" style="margin:0 0 8px">돈이 가장 많이 몰린 종목 — 시장 관심의 중심</p>
    {stock_table(by_value)}
  </section>

  <section>
    <h2>⑦ 하락률 상위 10</h2>
    {stock_table(losers)}
  </section>

  <section>
    <h2>보는 법</h2>
    <ul style="margin:0;padding-left:20px">
      <li><b>상승률 상위와 거래대금 상위에 동시에 있는 종목</b>이 전날 시장의 진짜 주인공입니다.</li>
      <li>같은 업종 종목이 상위에 여러 개면 <b>테마 확산</b>, 한 종목만 튀었으면 <b>개별 이슈</b>일 가능성이 큽니다.</li>
      <li>전날 급등주는 다음 날 변동성이 큽니다. 시초가 흐름을 먼저 보고 판단하세요.</li>
    </ul>
  </section>

  <footer>
    <a href="index.html">장마감 시황</a> · <a href="archive.html">지난 리포트</a> · 출처: KRX Open API · 투자 판단과 책임은 본인에게 있습니다
  </footer>
</main>
</body>
</html>
"""


def main():
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        print("[실패] KRX_API_KEY 없음")
        sys.exit(1)

    bas_dd, first_rows = find_latest_day(key)
    if not bas_dd:
        print("[실패] 최근 10일 안에 데이터가 있는 영업일을 찾지 못함")
        sys.exit(1)
    print(f"기준일: {bas_dd}")

    warnings = []
    stocks = load_stocks(bas_dd, first_rows, key)
    if not any(s["market"] == "코스닥" for s in stocks):
        warnings.append("코스닥 종목")
    idx = load_indices(bas_dd, key)
    if not idx["main"]:
        warnings.append("지수")
    print(f"종목 {len(stocks)}개, 업종지수 {len(idx['sectors'])}개")

    made_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    page = build_html(bas_dd, idx, stocks, made_at, warnings)
    with open("morning.html", "w", encoding="utf-8") as f:
        f.write(page)

    # 요약만 저장 (전 종목 원자료는 저장하지 않음)
    pool = [s for s in stocks if not s["spac"]]
    liquid = [s for s in pool if s["value"] >= MIN_TRADE_VALUE]
    pick = lambda rows: [{k: s[k] for k in ("market", "name", "code", "close", "rate", "value")} for s in rows]
    summary = {
        "bas_dd": bas_dd,
        "generated_at_kst": made_at,
        "indices_main": idx["main"],
        "sectors": idx["sectors"],
        "top_gainers": pick(sorted(liquid, key=lambda s: s["rate"], reverse=True)[:TOP_N]),
        "top_value": pick(sorted(pool, key=lambda s: s["value"], reverse=True)[:TOP_N]),
        "limit_up": pick([s for s in pool if s["rate"] >= 29.5]),
    }
    os.makedirs(os.path.join("data", "krx"), exist_ok=True)
    with open(os.path.join("data", "krx", f"{bas_dd}.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("morning.html, data/krx 요약 저장 완료")


if __name__ == "__main__":
    main()
