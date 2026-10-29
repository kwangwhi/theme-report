# theme-report

휘휘의 장마감 시황 · 테마 리포트 저장소입니다.

**장마감 시황:** https://kwangwhi.github.io/theme-report/

**아침 준비 리포트:** https://kwangwhi.github.io/theme-report/morning.html

**장마감 신호:** https://kwangwhi.github.io/theme-report/signals.html

## 매일 자동으로 일어나는 일 (평일)

| 시각 | 누가 | 하는 일 |
|---|---|---|
| 08:30 | GitHub Actions | KRX Open API로 전 영업일 확정 데이터 → **아침 준비 리포트**(`morning.html`) |
| 15:45 | GitHub Actions | 지수·환율·해외지수 숫자를 거래소 데이터로 받아 `data/`에 저장 |
| 16:21 | Claude 예약 작업 | `data/`의 숫자 + 뉴스(수급·테마·일정)로 리포트 작성 후 올림 |
| 20:20 | GitHub Actions | NXT 마감 후 통합 일봉으로 당일 신호(박스·골든크로스·볼린저·장기저항·첫 상한가) → `data/signals/`, **장마감 신호**(`signals.html`) |

## 폴더 설명

| 이름 | 설명 |
|---|---|
| `index.html` | 최신 리포트 (위 주소로 열리는 페이지) |
| `archive.html`, `archive/` | 지난 리포트 보관함 |
| `morning.html` | 아침 준비 리포트 (전 영업일 상승률·거래대금 상위, 업종 강약) |
| `data/` | 매일 자동 수집한 숫자 (`latest.json` = 가장 최근), `data/krx/` = KRX 요약 |
| `signals.html`, `data/signals/` | 장마감 신호 페이지와 원본 결과 (통합 KRX+NXT 기준) |
| `backtest.html`, `data/backtest/` | 신호 성적표 (매주 토요일 갱신) |
| `scripts/collect_market.py` | 숫자를 받아오는 프로그램 |
| `.github/workflows/collect-market.yml` | 15:45 자동 실행 설정 |

## 확인하는 법

- **숫자 수집이 됐나:** 위쪽 **Actions** 탭 → "장마감 데이터 수집"에 초록 체크 ✓
- **수동으로 다시 받기:** Actions → "장마감 데이터 수집" → **Run workflow**

## 주의

- 매수·매도 추천이 아닙니다. 매매 전 HTS 시세로 다시 확인하세요.
- API 키, 계좌번호, 비밀번호는 이 저장소에 절대 올리지 않습니다. (공개 저장소)
