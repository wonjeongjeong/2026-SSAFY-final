# 서울 지하철 출발 시각 추천 서비스

## 로컬 웹 실행

`start_web.cmd`를 실행하거나 `python services/web/server.py`를 실행한 뒤 **http://127.0.0.1:5173** 에 접속하세요. 외부 패키지 설치 없이 Python 3.10 이상으로 실행합니다.

19개 노선 지도와 역 검색에서 출발역·도착역·목표 시각을 설정하면 **실제 시간표와 현재 도착정보를 이용해 여러 경로를 비교**합니다. 빠른 도착·늦은 출발·여유 있는 출발·환승 적은 경로를 나눠 보여주고 15초마다 다시 계산합니다. 로컬 PC의 기존 `subway-collector` SSH 설정과 시간표 캐시가 필요합니다. **혼잡 대기는 사용자 시나리오이며, 9호선은 현재 일부 운행 시간표만 확보했습니다.** [실행 안내](services/web/README.md) · [계산 방식과 한계](docs/live-route-draft.md)

열차 지연과 환승 대기, 혼잡으로 인한 추가 대기를 고려해 목표 도착 시각에 맞는 열차를 추천하는 프로젝트입니다. 현재 구현 범위는 **출퇴근 데이터 수집·지연 관측값 생성과 로컬 실시간 경로 초안**입니다. 실시간 수집은 19개 노선, 15초 간격이며 일반·급행·특급 구분과 도착예정시간을 보존합니다. 수집기의 지연 매칭은 1~8호선과 공식 XLSX 적재 후 코레일 5개 노선을 지원합니다. 웹 계산용 시간표는 별도 보완 스크립트로 19개 노선 자료를 확보했습니다. [수신 확인과 설정](docs/realtime-coverage.md)

## 지금 수집하는 범위

| 데이터 | 구현 | 해석 |
|---|---|---|
| 열차 위치 | 서울시 실시간 위치 API, 19개 노선, 노선마다 15초 간격(노선별 시차 호출) | 열차번호·역·방향·일반/급행/특급·원천 수신 시각 보존 |
| 역 도착·지연 | 위치 상태 전이 + 당일 유효 시간표 비교 | 관측 시간 구간으로 계산. 시간표 미매칭은 NULL |
| 시간표 (1~8호선) | 공공데이터포털 열차시간표 API, 매일 자동 | 서울교통공사 운영 노선 |
| 시간표 (코레일 5개 노선) | 코레일 공식 XLSX를 `--load-timetable`로 한 번 적재 | 경의중앙선·경춘선·서해선·수인분당선·경강선. 평일/휴일·시행일별로 골라 씀 |
| 도착정보 샘플 | 서울시 실시간 도착정보(일괄), 15초 간격 | 위치로 뽑은 도착 시각 검증용. 지연 정답으로 쓰지 않음 |
| 역별 시간대별 승하차 인원 | 서울시 `CardSubwayTime`, 최근 3개월, 달마다 1회 | 지연 모델 특성 |
| 역간·환승 소요, 역 좌표, 열차 혼잡도 | 공공데이터포털 CSV를 내려받아 `--load-reference`로 적재 | 경로 그래프·노선도·탑승 실패 대기 규칙 |
| 운행 공지 | **2026-11-11부터 수집 중단** (`alerts: false`) | 기존 데이터·코드는 보존 |
| SK 혼잡도 | **2026-11-11 수집 코드 제거** (유료, 지하철혼잡도정보로 대체) | 이전에 받은 `congestion` 행만 DB에 남음 |
| 실시간 승강장 대기 인원 | 검증된 공개 수집원 미확보 | 0명으로 채우거나 다른 혼잡도를 대입하지 않음 |

**키 없이 실행하면 대기합니다. 실행 중이라는 사실과 실제 데이터 수신 성공은 다릅니다.** `--status`의 `rows`, `requests`, `last_position`을 확인하세요.

## 오늘 화요일부터 시작하기

Python **3.10 이상**이 필요하며 수집기 실행에는 외부 패키지가 필요 없습니다.

1. `.env.example`을 `.env`로 복사하고 본인의 인증키를 입력합니다. 이미 `.env`가 있다면 그 파일을 수정합니다.
2. `start_background.cmd`를 실행합니다. 현재 시간이 수집 시간 안이면 즉시 요청하며, 밖이면 다음 시간까지 대기합니다.
3. `register_daily.cmd`를 한 번 실행하면 로그인 시 및 매일 06:25·17:25 자동 시작을 등록합니다. 작업 이름은 `SSAFY-SeoulSubway-DailyCollector`입니다. PC 권한 때문에 작업 스케줄러 등록이 거부되면 **현재 사용자 시작프로그램 바로가기**로 자동 전환합니다. 이 경우 로그인 시 켜진 수집기가 매일 시간대를 판단하며, 수동 중지한 수집기의 자동 복구는 다음 로그인 때 이루어집니다.
4. `status_background.cmd`로 프로세스 상태와 실제 저장 건수를 확인합니다. `stop_background.cmd`로 중지합니다.

```dotenv
SEOUL_SUBWAY_API_KEY=서울_열린데이터광장_실시간지하철_인증키
SEOUL_OPEN_API_KEY=서울_열린데이터광장_일반_인증키
DATA_GO_KR_API_KEY=공공데이터포털_인증키
```

필요한 키부터 입력해도 됩니다. 서울시 키만 입력하면 위치 이력부터 쌓입니다. 키는 매 주기 다시 읽으므로 실행 중 `.env`에 추가할 수 있습니다. `SEOUL_OPEN_API_KEY`는 승하차 인원용 **일반 인증키**로 실시간 지하철 키와 다릅니다. 공공데이터포털 키는 **열차시간표 활용승인**이 필요합니다. 프로그램은 상품을 구매하거나 유료 요금제를 신청하지 않습니다.

2026-11-10은 화요일(`TUE`)입니다. 현재 수집 시간은 KST 기준 **평일 06:30~09:30, 17:30~20:30**입니다. 화요일에 모은 실시간 관측은 화요일 날짜에 보관하고 수요일에는 자동으로 `WED`로 전환합니다. 과거 실시간 데이터를 소급해서 받을 수 없으므로 오늘 실행 이전의 구간은 비어 있습니다.

PC가 켜져 있고 절전 상태가 아니며 인터넷에 연결되어 있어야 합니다. 자동 시작 작업은 현재 사용자의 로그인 세션에서 실행됩니다. 로그아웃·전원 종료 중 수집은 불가능합니다. 스케줄러의 시작 시각은 Windows 현지 시간이며 실제 요청 시간대 판단은 Python의 KST를 사용합니다.

## 저장 위치

```text
data/
  subway.sqlite3                    # 정규화 데이터·호출 예산·체크포인트
  collector.log                     # 회전 로그
  status.json                       # 15초마다 최신 상태
  raw/date=2026-11-10/dow=TUE/       # 데이터셋별 응답 원문(JSON gzip)
  reference/                         # 직접 내려받은 정적 CSV
  exports/dow=TUE/date=2026-11-10/   # 수집 시간 밖에서 갱신하는 CSV
    positions.csv
    arrivals.csv
    delays.csv
    arrival_samples.csv
    congestion.csv
    notices.csv
    timetable.csv
    requests.csv
```

원본과 SQLite, CSV는 Git에서 제외합니다. 원본에는 수집 시각·대상·응답 본문을 보존하며 인증키가 반사되면 가립니다. CSV는 UTF-8 BOM으로 Excel에서도 열 수 있습니다. 혼잡도의 `dow`는 원천 통계의 대상 요일이고 `fetched_date`는 내려받은 날짜입니다.

## 실행·확인 명령

```powershell
python collector.py --doctor                  # 설정·키 유무·예상 호출량; 키 값/네트워크 요청 없음
python collector.py                           # 매일 출퇴근 시간 수집, 나머지 시간 대기
python collector.py --once                    # 수집 시간 안에서 위치 1회 + 보조 요청 1개
python collector.py --status                  # 현재 저장 건수·시간표 매칭 상태
python collector.py --export 2026-11-10        # 해당 날짜 CSV 다시 생성
python collector.py --load-reference transfer_times data/reference/파일.csv  # 정적 CSV 적재
python collector.py --load-timetable 파일.xlsx --valid-from 2026-09-01 [--line 수인분당선] [--day-type 평일]  # 코레일 시각표 적재
python -m unittest discover -s tests -v        # 실제 키 없이 검증
python scripts/backup.py                      # SQLite 일관된 스냅샷 + 원본 + 키 백업
powershell -File scripts/register_schedule.ps1 -Remove
```

백업 ZIP에는 인증키가 들어갈 수 있습니다. PC 초기화 전 외장 저장소나 다른 PC로 복사해야 합니다. 같은 PC의 `backups/`만으로는 초기화에 대비할 수 없습니다.

## 범위·호출량 변경

`config/collector.json`을 수정한 뒤 수집기를 재시작합니다. 기본 위치 호출량은 `19개 노선 × 6시간 × 240회 = 27,360회/일`이며, 도착정보 1,440회를 합쳐 하루 기본 28,800회입니다. 재시도와 웹 조회가 공유하는 로컬 상한은 35,000회입니다. `scripts/check_interval.py`로 원천 갱신 품질을 점검할 수 있습니다. 2026-11-11 활용사례 등록으로 하루 1,000건 제한이 해제됐으며, `--status`의 `seoul_subway_quota.verdict`로 실제 해제를 확인합니다. 승하차 인원(`seoul_open`)과 공공데이터포털(`data_go_kr`)은 별도 예산으로 집계합니다. 실제 계정 한도는 제공기관에서 확인해야 하며 다른 프로그램의 사용량은 이 수집기가 알 수 없습니다.

2026-11-24부터 위치·도착정보의 목표 주기는 각각 15초입니다. 위치 요청은 최대 4개를 병행하며 같은 노선 요청은 겹치지 않습니다. 느린 요청의 밀린 회차는 건너뛰므로 실제 간격은 API 응답과 부하에 따라 길어질 수 있습니다. 도착정보와 시간표 수집은 별도 실행 경로를 사용합니다. 지연 계산은 별도 프로세스에서 새 도착 관측을 작은 묶음으로 처리하고 재시작 시 체크포인트부터 이어갑니다. CSV는 수집 종료 후 및 대기 중 1시간 간격으로 내보냅니다. `data/processing.log`와 `--status`의 `processing`에서 계산 진행 상태를 확인합니다. 원천 데이터 자체의 갱신을 15초로 보장하는 설정은 아닙니다.

열차 혼잡도는 서울교통공사 지하철혼잡도정보 CSV를 `--load-reference train_congestion`으로 적재해 씁니다. SK 혼잡도 수집 코드는 2026-11-11에 제거했습니다.

노선 수를 늘리면 위치 호출량이 노선 수에 비례해 늘어납니다. 위치 주기를 늘리거나 호출 예산을 함께 조정하세요. 예산 소진·한도 오류는 당일 해당 공급자 호출을 막고 다음 KST 날짜에 재개합니다. 인증 오류는 해당 데이터셋을 당일 중단하며 키를 바꾸면 재시도합니다.

### 코레일 노선 시간표 적재

공공데이터포털 열차시간표 API는 서울교통공사 1~8호선만 줍니다. 경의중앙선·경춘선·서해선·수인분당선·경강선은 코레일 홈페이지 시각표 게시판의 XLSX(`services/web/import_sources/`, 출처는 `korail_manifest.json`)를 `static_timetable` 표에 한 번 적재합니다. 매일 복사하지 않고, 지연을 계산할 때 그 날의 요일 유형(평일/휴일)과 시행일(`valid_from`)이 맞는 가장 최근 판을 노선별로 고릅니다. 그 날 API 시간표가 있는 노선은 고정 시간표를 쓰지 않습니다.

```bash
cd services/web/import_sources
python ../../../collector.py --load-timetable korail_25745.xlsx --valid-from 2026-09-01 --day-type 평일   # 경의중앙·경춘·서해 평일
python ../../../collector.py --load-timetable korail_25746.xlsx --valid-from 2026-09-01 --day-type 휴일   # 같은 노선 휴일
python ../../../collector.py --load-timetable korail_25702.xlsx --valid-from 2026-08-22 --line 수인분당선  # 시트에 평일/휴일 표시
python ../../../collector.py --load-timetable korail_23962.xlsx --valid-from 2026-03-23 --line 경강선
python ../../../collector.py --status    # static_timetables에 노선·요일·시행일별 열차 수
```

- 시트 이름으로 노선(경의중앙선·경의선→경의중앙선, 경춘선·망우직결선→경춘선, 서해선)과 방향(상행 `0`, 하행 `1`)을 정합니다. ITX-청춘 시트는 전동열차가 아니라 건너뜁니다. 노선 이름이 시트에 없으면 `--line`, 평일/휴일이 없으면 `--day-type`이 필요합니다.
- 역 이름 행은 도착, 바로 아래 행은 출발 시각입니다. 빈칸은 정차하지 않는 역입니다. 자정을 넘는 시각은 `24:10:00`처럼 이어 씁니다.
- 코레일 시각표의 줄임 역명(`홍대입`, `경광주`, `신수원` 등)은 `korail.STATION_NAMES`로 정식 역명으로 바꿉니다. 같은 파일을 다시 적재하면 같은 행은 건너뜁니다.
- 열차번호는 시각표 그대로(`K5001`) 저장합니다. 위치 API가 `5001`로 주면 기존 코레일 K 표기 규칙(`korail_k_number`: 종착역 일치 + 20분 이내)으로 짝짓습니다.
- 개정 시각표가 나오면 새 파일을 새 `--valid-from`으로 적재합니다. 이전 판은 남겨 두며 날짜에 따라 자동으로 골라 씁니다. 파업 등 임시 시각표(`korail_26603`)는 자동으로 섞지 않습니다.
- 적재 후 첫 수집일에 `python scripts/check_day.py 날짜`로 노선별 매칭률과 `direction_mismatch`를 확인합니다. 코레일 노선의 상행/하행이 위치 API의 `updnLine`과 반대로 나오면 `direction_mismatch`가 늘어납니다.

새 코드를 서버에 배포한 뒤에는 수집 서비스를 재시작해야 합니다. 이 버전의 계산 프로세스는 시간표 적재 변경을 최대 30초 간격으로 감지하고, 당일 기존 관측을 작은 묶음으로 다시 계산합니다. 재계산이 끝나는 시간은 관측량에 따라 달라집니다. 이전 날짜는 `python collector.py --export YYYY-MM-DD`로 다시 계산합니다. **관측 날짜가 시행일보다 이른 시간표는 사용하지 않습니다.** 공휴일 지정(`day_type_overrides`)은 API 시간표가 아직 없어도 반영됩니다. XLSX 적재와 과거 전체 재계산은 수집 시간 밖에 실행하세요.

공휴일 시간표는 `day_type_overrides`에 날짜별로 지정합니다. 예: `"2026-12-25": "공휴일"`. 기본값은 월~금 `평일`, 토요일 `주말`, 일요일 `공휴일`입니다. 임시·상시 시간표도 함께 보존하며 매칭이 여러 개면 지연 값을 확정하지 않습니다.

## 프로젝트 구성과 다음 단계

```text
src/subway_collect/   # 실제 동작: API·스케줄·저장·도착/지연 산출
config/              # 노선·시간대·호출 예산·공휴일 보정
sql/                 # SQLite 스키마
scripts/             # Windows 백그라운드·자동 실행·백업
tests/               # 중복·재시작·오류·시각·지연/통계 검증
docs/                # 기획서 사본·요구사항·출처·데이터 계약
pipelines/streaming/ # Kafka→Flink→Redis/HDFS 설계
pipelines/batch/     # Spark·Airflow 설계
ml/                  # P50/P95·환승/탑승 대기 모델 설계
services/            # FastAPI·웹 서비스 설계
infra/               # 향후 공통 개발 환경 구성 계획
```

Kafka·Flink·Spark·Airflow·ML·웹은 이후 구현 범위입니다. 현재 수집 데이터를 그대로 재처리할 수 있도록 원본·이벤트 식별자·시간 품질을 보존합니다. 상세 내용은 [요구사항](docs/requirements.md), [데이터 출처와 제약](docs/data-sources.md), [데이터 계약](docs/data-contract.md)을 참고하세요.
