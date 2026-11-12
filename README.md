# 서울 지하철 출발 시각 추천 서비스

열차 지연과 환승 대기, 혼잡으로 인한 추가 대기를 고려해 목표 도착 시각에 맞는 열차를 추천하는 프로젝트입니다. 현재 구현 범위는 **매일 출퇴근 데이터 수집과 지연 관측값 생성**입니다. 이전 전기차 충전소 코드는 교체했으며 Git 이력은 보존했습니다.

## 지금 수집하는 범위

| 데이터 | 구현 | 해석 |
|---|---|---|
| 열차 위치 | 서울시 실시간 위치 API, 1~8호선, 노선마다 15초 간격(노선별 시차 호출) | 열차번호·역·방향·원천 수신 시각 보존 |
| 역 도착·지연 | 위치 상태 전이 + 당일 유효 시간표 비교 | 관측 시간 구간으로 계산. 시간표 미매칭은 NULL |
| 도착정보 샘플 | 서울시 실시간 도착정보(일괄), 2분 간격 | 위치로 뽑은 도착 시각 검증용. 지연 정답으로 쓰지 않음 |
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

2026-11-10은 화요일(`TUE`)입니다. 수집 시간은 KST 기준 **매일 06:30~09:30, 17:30~20:30**, 주말 포함입니다. 화요일에 모은 실시간 관측은 화요일 날짜에 보관하고 수요일에는 자동으로 `WED`로 전환합니다. 과거 실시간 데이터를 소급해서 받을 수 없으므로 오늘 실행 이전의 구간은 비어 있습니다.

PC가 켜져 있고 절전 상태가 아니며 인터넷에 연결되어 있어야 합니다. 자동 시작 작업은 현재 사용자의 로그인 세션에서 실행됩니다. 로그아웃·전원 종료 중 수집은 불가능합니다. 스케줄러의 시작 시각은 Windows 현지 시간이며 실제 요청 시간대 판단은 Python의 KST를 사용합니다.

## 저장 위치

```text
data/
  subway.sqlite3                    # 정규화 데이터·호출 예산·체크포인트
  collector.log                     # 회전 로그
  status.json                       # 30초마다 최신 상태
  raw/date=2026-11-10/dow=TUE/       # 데이터셋별 응답 원문(JSON gzip)
  reference/                         # 직접 내려받은 정적 CSV
  exports/dow=TUE/date=2026-11-10/   # 1시간마다 갱신하는 CSV(positions.csv는 자정·중지 시)
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
python -m unittest discover -s tests -v        # 실제 키 없이 검증
python scripts/backup.py                      # SQLite 일관된 스냅샷 + 원본 + 키 백업
powershell -File scripts/register_schedule.ps1 -Remove
```

백업 ZIP에는 인증키가 들어갈 수 있습니다. PC 초기화 전 외장 저장소나 다른 PC로 복사해야 합니다. 같은 PC의 `backups/`만으로는 초기화에 대비할 수 없습니다.

## 범위·호출량 변경

`config/collector.json`을 수정한 뒤 수집기를 재시작합니다. 기본 위치 호출량은 `8개 노선 × 6시간 × 240회 = 11,520회/일`이며, 도착정보 샘플(하루 최대 180회)과 재시도를 포함한 로컬 상한은 14,000회입니다. 15초 간격은 2026-11-11 실측(같은 열차의 위치 값 변화 중 48%가 30초 안에 연달아 일어남)으로 정했으며 `scripts/check_interval.py`로 다시 확인할 수 있습니다. 2026-11-11 활용사례 등록으로 하루 1,000건 제한이 해제됐으며, `--status`의 `seoul_subway_quota.verdict`로 실제 해제를 확인합니다. 승하차 인원(`seoul_open`)과 공공데이터포털(`data_go_kr`)은 별도 예산으로 집계합니다. 실제 계정 한도는 제공기관에서 확인해야 하며 다른 프로그램의 사용량은 이 수집기가 알 수 없습니다.

열차 혼잡도는 서울교통공사 지하철혼잡도정보 CSV를 `--load-reference train_congestion`으로 적재해 씁니다. SK 혼잡도 수집 코드는 2026-11-11에 제거했습니다.

노선 수를 늘리면 위치 호출량이 노선 수에 비례해 늘어납니다. 위치 주기를 늘리거나 호출 예산을 함께 조정하세요. 예산 소진·한도 오류는 당일 해당 공급자 호출을 막고 다음 KST 날짜에 재개합니다. 인증 오류는 해당 데이터셋을 당일 중단하며 키를 바꾸면 재시도합니다.

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
