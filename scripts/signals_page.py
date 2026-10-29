"""
신호 페이지 만들기 — data/signals/ 의 가장 최근 JSON 으로 signals.html 을 만든다.

- collect_signals.py 가 만든 파일을 읽기만 하고, 그 파일은 바꾸지 않는다.
- PC 가로형 배치(넓은 화면에서 2~3단), 휴대폰에서는 1단으로 자동 전환.
- 매수·매도 추천이 아니라 조건에 걸린 종목 목록이다.
"""

import glob
import html
import json
import os
import sys
from datetime import datetime

SIGNAL_DIR = os.path.join("data", "signals")
OUT = "signals.html"


def esc(s):
    return html.escape(str(s if s is not None else ""))


def won(v):
    return "—" if v is None else f"{v:,.0f}"


def eok(v):
    return "—" if not v else f"{v / 1e8:,.0f}억"


def rate_td(v):
    if v is None:
        return '<td class="num">—</td>'
    cls = "up" if v > 0 else "down" if v < 0 else ""
    sign = "+" if v > 0 else ""
    return f'<td class="num {cls}">{sign}{v:.2f}%</td>'


def num_td(v, suffix=""):
    return f'<td class="num">{"—" if v is None else f"{v:,.2f}{suffix}"}</td>'


def market_tag(m):
    return f'<span class="tag">{"코스피" if m == "KOSPI" else "코스닥" if m == "KOSDAQ" else esc(m)}</span>'


def base_cells(s):
    return (f"<td>{esc(s['name'] or s['code'])}{market_tag(s['market'])}</td>"
            f"<td class=\"num\">{won(s['close'])}</td>{rate_td(s['rate'])}<td class=\"num\">{eok(s['value'])}</td>")


def table(rows, extra_heads, extra_fn):
    if not rows:
        return '<p class="muted">해당 종목 없음</p>'
    head = ("<tr><th>종목</th><th class=\"num\">종가</th><th class=\"num\">등락률</th><th class=\"num\">거래대금</th>"
            + "".join(f"<th class=\"num\">{esc(h)}</th>" for h in extra_heads) + "</tr>")
    body = "".join(f"<tr>{base_cells(s)}{extra_fn(s)}</tr>" for s in rows)
    return f"<table>{head}{body}</table>"


def card(title, desc, count, content, wide=False):
    cls = "card wide" if wide else "card"
    return (f'<section class="{cls}"><div class="card-head"><h2>{esc(title)}</h2>'
            f'<span class="count">{count}</span></div><p class="desc">{desc}</p>{content}</section>')


STYLE = """
:root { --bg:#f4f5f7; --card:#ffffff; --text:#1a1d21; --muted:#6b7280; --line:#e5e7eb;
  --up:#d92d20; --down:#1d64d8; --warn-bg:#fff7e6; --warn:#8a5a00; --accent:#111827; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1115; --card:#181b21; --text:#e8eaed; --muted:#9aa0a6; --line:#2a2e36;
    --up:#ff6b5e; --down:#5b9bff; --warn-bg:#2a2212; --warn:#f2c46d; --accent:#e8eaed; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); line-height:1.55;
  font-family:-apple-system,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif; }
main { max-width:1280px; margin:0 auto; padding:24px 24px 56px; }
header { display:flex; flex-wrap:wrap; align-items:baseline; gap:8px 16px; margin-bottom:14px; }
h1 { font-size:1.6rem; margin:0; }
.stamp { color:var(--muted); font-size:.92rem; }
.notice { background:var(--warn-bg); color:var(--warn); border-radius:10px; padding:10px 14px;
  font-size:.9rem; margin-bottom:16px; }
.stats { display:grid; grid-template-columns:repeat(7, 1fr); gap:10px; margin-bottom:16px; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px; }
.stat b { display:block; font-size:1.5rem; line-height:1.2; }
.stat span { color:var(--muted); font-size:.8rem; }
.grid { display:grid; grid-template-columns:repeat(2, 1fr); gap:14px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; overflow-x:auto; }
.card.wide { grid-column:1 / -1; }
.card-head { display:flex; align-items:center; justify-content:space-between; gap:8px; }
h2 { font-size:1.05rem; margin:0; }
.count { font-weight:700; font-size:.9rem; background:var(--bg); border:1px solid var(--line);
  border-radius:999px; padding:1px 10px; }
.desc { color:var(--muted); font-size:.82rem; margin:4px 0 10px; }
table { width:100%; border-collapse:collapse; font-size:.9rem; }
th, td { text-align:left; padding:6px 5px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:.78rem; white-space:nowrap; }
td.num, th.num { text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }
.up { color:var(--up); font-weight:600; } .down { color:var(--down); font-weight:600; }
.tag { display:inline-block; font-size:.7rem; color:var(--muted); border:1px solid var(--line);
  border-radius:999px; padding:0 6px; margin-left:4px; white-space:nowrap; }
.muted { color:var(--muted); font-size:.88rem; }
.chips span { display:inline-block; font-size:.78rem; border:1px solid var(--line); border-radius:6px;
  padding:1px 6px; margin:0 3px 3px 0; }
ul.err { margin:0; padding-left:18px; font-size:.85rem; color:var(--muted); }
footer { color:var(--muted); font-size:.8rem; text-align:center; margin-top:22px; }
footer a { color:inherit; }
@media (max-width:1000px) { .stats { grid-template-columns:repeat(4, 1fr); } }
@media (max-width:720px) { main { padding:16px 12px 40px; } .grid { grid-template-columns:1fr; }
  .stats { grid-template-columns:repeat(2, 1fr); } }
"""


def build(d):
    sig = [s for s in d.get("signals", []) if "error" not in s]
    sig_err = [s for s in d.get("signals", []) if "error" in s]
    by_value = lambda rows: sorted(rows, key=lambda s: s.get("value") or 0, reverse=True)

    box = by_value([s for s in sig if (s.get("box_breakout") or {}).get("hit")])
    gc = by_value([s for s in sig if (s.get("golden_cross") or {}).get("hit")])
    bb = by_value([s for s in sig if (s.get("bollinger_break") or {}).get("hit")])
    lr_break = by_value([s for s in sig if (s.get("long_resistance") or {}).get("status") == "돌파"])
    lr_try = by_value([s for s in sig if (s.get("long_resistance") or {}).get("status") == "시도"])
    lu_first = by_value([s for s in sig if (s.get("limit_up") or {}).get("first")])
    lu_repeat = by_value([s for s in sig if (s.get("limit_up") or {}).get("today")
                          and not (s.get("limit_up") or {}).get("first")])

    # 여러 신호가 겹친 종목
    def hits(s):
        h = []
        if (s.get("box_breakout") or {}).get("hit"): h.append("박스 돌파")
        if (s.get("golden_cross") or {}).get("hit"): h.append("골든크로스")
        if (s.get("bollinger_break") or {}).get("hit"): h.append("볼린저 돌파")
        st = (s.get("long_resistance") or {}).get("status")
        if st: h.append(f"장기저항 {st}")
        lu = s.get("limit_up") or {}
        if lu.get("first"): h.append("첫 상한가")
        elif lu.get("today"): h.append("재상한가")
        return h
    multi = by_value([s for s in sig if len(hits(s)) >= 2])

    day = d.get("date", "")
    try:
        dt = datetime.strptime(day, "%Y-%m-%d")
        day_label = f"{dt.year}년 {dt.month}월 {dt.day}일({'월화수목금토일'[dt.weekday()]})"
    except ValueError:
        day_label = day

    stats = [("후보 종목", len(d.get("signals", []))), ("박스 돌파", len(box)), ("골든크로스", len(gc)),
             ("볼린저 돌파", len(bb)), ("장기저항 돌파", len(lr_break)), ("장기저항 시도", len(lr_try)),
             ("첫 상한가", len(lu_first))]
    stats_html = "".join(f'<div class="stat"><b>{n}</b><span>{esc(t)}</span></div>' for t, n in stats)

    cards = []
    cards.append(card("겹친 신호", "신호가 2개 이상 동시에 걸린 종목 — 가장 먼저 볼 후보", len(multi),
                      table(multi, ["신호"], lambda s: '<td class="num chips">' + "".join(
                          f"<span>{esc(h)}</span>" for h in hits(s)) + "</td>"), wide=True))
    cards.append(card("첫 상한가", "당일 상한가 + 직전 20거래일 안에 상한가 없음", len(lu_first),
                      table(lu_first, [], lambda s: "")))
    cards.append(card("재상한가", "당일 상한가 + 직전 20거래일 안에도 상한가 있음", len(lu_repeat),
                      table(lu_repeat, ["직전 상한가"], lambda s: f'<td class="num">{esc(", ".join((s.get("limit_up") or {}).get("prior20_dates", [])))}</td>')))
    cards.append(card("장기 저항선 돌파", "당일 종가 ≥ 직전 250일 최고가", len(lr_break),
                      table(lr_break, ["250일 고점", "고가/고점"], lambda s: f'<td class="num">{won((s.get("long_resistance") or {}).get("high250"))}</td>' + num_td((s.get("long_resistance") or {}).get("high_vs_high250_pct"), "%"))))
    cards.append(card("장기 저항선 시도", "당일 고가가 250일 최고가의 97% 이상, 종가는 아직 아래", len(lr_try),
                      table(lr_try, ["250일 고점", "고가/고점"], lambda s: f'<td class="num">{won((s.get("long_resistance") or {}).get("high250"))}</td>' + num_td((s.get("long_resistance") or {}).get("high_vs_high250_pct"), "%"))))
    cards.append(card("박스권 돌파", "직전 60일 고저폭 25% 이내 + 60일 최고가 돌파 + 거래량 20일 평균 2배 이상", len(box),
                      table(box, ["박스폭", "거래량 배수"], lambda s: num_td((s.get("box_breakout") or {}).get("range60_pct"), "%") + num_td((s.get("box_breakout") or {}).get("vol_ratio20"), "배"))))
    cards.append(card("골든크로스(하방경직)", "5일선이 20일선을 위로 교차 + 당일 저가 ≥ 20일선", len(gc),
                      table(gc, ["5일선", "20일선"], lambda s: num_td((s.get("golden_cross") or {}).get("ma5")) + num_td((s.get("golden_cross") or {}).get("ma20")))))
    cards.append(card("볼린저 상단 돌파", "전일 종가는 밴드(20일·2σ) 안 → 당일 종가가 상단 돌파", len(bb),
                      table(bb, ["상단선"], lambda s: num_td((s.get("bollinger_break") or {}).get("upper")))))

    errors = d.get("errors", [])
    notice = ("조건에 걸린 종목 목록일 뿐 매수·매도 추천이 아닙니다. 대상: 거래대금 상위 50 + 등락률 ±10% 종목(정규장 기준 선정). "
              "가격·신호는 통합(KRX+NXT) 일봉 기준, NXT 마감 후 계산.")
    inv_fail = [e for e in errors if "순매수" in e.get("where", "")]
    if inv_fail:
        notice += " · 투자자별 수급은 수집 실패(KRX 회원 로그인 필요)로 빠져 있습니다."
    short = [s for s in sig if (s.get("long_resistance") or {}).get("status") is None
             and "reason" in (s.get("long_resistance") or {})]

    err_items = "".join(f"<li>{esc(e.get('where'))}: {esc(e.get('error'))[:160]}</li>" for e in errors[:20])
    err_card = ""
    if errors or sig_err or short:
        extra = ""
        if short:
            extra = f"<p class=\"muted\">상장 기간이 짧아 장기 저항선을 계산하지 못한 종목 {len(short)}개: {esc(', '.join((s['name'] or s['code']) for s in short[:20]))}</p>"
        err_card = card("수집 메모", "실패하거나 계산하지 못한 항목", len(errors),
                        (f"<ul class=\"err\">{err_items}</ul>" if err_items else "") + extra, wide=True)

    src = d.get("sources", {})
    src_text = f"시세: {esc(src.get('market_today', '—'))} · 일봉: {esc(', '.join(f'{k} {v}개' for k, v in (src.get('daily') or {}).items()) or '—')}"

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>장마감 신호</title>
<style>{STYLE}</style>
</head>
<body>
<main>
  <header>
    <h1>장마감 신호</h1>
    <span class="stamp">{esc(day_label)} 종가 기준 · 생성 {esc(d.get('generated_at_kst', ''))}</span>
  </header>
  <div class="notice">{notice}</div>
  <div class="stats">{stats_html}</div>
  <div class="grid">
    {''.join(cards)}
    {err_card}
  </div>
  <footer>
    <a href="index.html">장마감 시황</a> · <a href="morning.html">아침 준비 리포트</a> · <a href="archive.html">지난 리포트</a>
    · {src_text} · 투자 판단과 책임은 본인에게 있습니다
  </footer>
</main>
</body>
</html>
"""


def main():
    files = sorted(glob.glob(os.path.join(SIGNAL_DIR, "????-??-??.json")))
    if not files:
        print("신호 파일 없음 — 페이지를 만들지 않음")
        return
    with open(files[-1], encoding="utf-8") as f:
        d = json.load(f)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(build(d))
    print(f"{OUT} 생성 ({os.path.basename(files[-1])})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"::error title=신호 페이지::{type(e).__name__}: {e}")
        sys.exit(1)
