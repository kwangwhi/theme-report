"""
KRX Open API 연결 시험

- 인증키(KRX_API_KEY)는 GitHub Secrets 에서만 읽고, 화면에 절대 출력하지 않는다.
- 신청한 4개 API(지수 2 + 일별매매정보 2)를 최근 영업일 기준으로 한 번씩 불러
  성공 여부와 건수, 첫 행의 항목 이름만 보여준다. (실제 데이터 값은 출력하지 않음)
- KRX Open API 는 전날 데이터를 다음 영업일 오전 8시에 제공하므로
  오늘부터 거꾸로 최대 7일을 시도해 데이터가 있는 가장 최근 날짜를 찾는다.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
BASE = "https://data-dbg.krx.co.kr/svc/apis"

APIS = [
    ("KOSPI 시리즈 일별시세", "idx/kospi_dd_trd"),
    ("KOSDAQ 시리즈 일별시세", "idx/kosdaq_dd_trd"),
    ("유가증권 일별매매정보", "sto/stk_bydd_trd"),
    ("코스닥 일별매매정보", "sto/ksq_bydd_trd"),
]


def call(path, bas_dd, key):
    url = f"{BASE}/{path}?basDd={bas_dd}"
    req = urllib.request.Request(url, headers={"AUTH_KEY": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")[:300]
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def rows_of(body):
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    rows = data.get("OutBlock_1")
    return rows if isinstance(rows, list) else None


def main():
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        print("[실패] KRX_API_KEY 가 비어 있음 — GitHub Secrets 이름을 확인하세요.")
        sys.exit(1)
    print(f"인증키 확인: 있음 (길이 {len(key)}자, 값은 표시하지 않음)\n")

    today = datetime.now(KST).date()
    ok_count = 0
    for name, path in APIS:
        found = False
        last_msg = ""
        for back in range(0, 8):
            d = today - timedelta(days=back)
            if d.weekday() >= 5:
                continue
            bas_dd = d.strftime("%Y%m%d")
            status, body = call(path, bas_dd, key)
            rows = rows_of(body) if status == 200 else None
            if rows:
                print(f"[성공] {name}: {bas_dd} 기준 {len(rows)}건, 항목: {', '.join(list(rows[0].keys())[:8])} ...")
                ok_count += 1
                found = True
                break
            if status != 200:
                last_msg = f"HTTP {status} {body[:150]}"
                # 인증·권한 오류면 날짜를 바꿔도 소용없으니 바로 중단
                if status in (401, 403):
                    break
            else:
                last_msg = f"{bas_dd} 데이터 없음"
        if not found:
            print(f"[실패] {name}: {last_msg}")

    print(f"\n결과: {ok_count}/{len(APIS)} 성공")
    if ok_count == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
