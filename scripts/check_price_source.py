"""
일봉 출처 점검 (진단용, 수동 실행)

pykrx 종목 일봉(네이버)과 FinanceDataReader 일봉의 최근 종가를
KRX 정규장 종가(KRX Open API 확정치 / 오늘 장마감 시세)와 비교한다.
결과는 GitHub 실행 요약(notice)으로만 출력하고 파일은 만들지 않는다.
"""

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# (이름, 코드, KRX 10/28 종가, KRX 10/29 종가)  — 10/28: KRX Open API, 10/29: 장마감 시세
CHECKS = [
    ("케이아이엔엑스", "093320", 156600, 179100),
    ("한울앤제주", "276730", 757, 940),
    ("대호특수강", "021040", None, 980),
    ("삼성전자", "005930", None, 231000),
    ("서울반도체", "046890", None, 11200),
]


def show(label, df):
    if df is None or df.empty:
        return f"{label}: 빈 데이터"
    tail = df.tail(3)
    parts = [f"{i.strftime('%m/%d')} 종가 {int(r['Close']):,}" for i, r in tail.iterrows()]
    return f"{label}: " + " | ".join(parts)


def main():
    end = datetime.now(KST).strftime("%Y%m%d")
    start = (datetime.now(KST) - timedelta(days=15)).strftime("%Y%m%d")
    from pykrx import stock
    import FinanceDataReader as fdr

    for name, code, k28, k29 in CHECKS:
        ref = f"KRX 기준 10/28 {k28:,}" if k28 else "KRX 기준 10/28 —"
        ref += f", 10/29 {k29:,}"
        lines = [ref]
        for label, fn in (
            ("pykrx 수정", lambda: stock.get_market_ohlcv(start, end, code)),
            ("pykrx 원주가", lambda: stock.get_market_ohlcv(start, end, code, adjusted=False)),
            ("FDR 기본", lambda: fdr.DataReader(code, start, end)),
            ("FDR KRX", lambda: fdr.DataReader(f"KRX:{code}", start, end)),
            ("FDR NAVER", lambda: fdr.DataReader(f"NAVER:{code}", start, end)),
        ):
            try:
                df = fn()
                if df is not None and "종가" in df.columns:
                    df = df.rename(columns={"종가": "Close"})
                lines.append(show(label, df))
            except Exception as e:
                lines.append(f"{label}: 오류 {type(e).__name__}: {str(e)[:80]}")
        print(f"::notice title={name}::" + " // ".join(lines))


if __name__ == "__main__":
    main()
