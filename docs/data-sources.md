# 데이터 출처와 확보 상태

확인일: **2026-11-10**, 범위 변경 **2026-11-11**. 실시간·과거 통계·미확보 항목을 구분합니다. API 키가 없는 상태의 단위 테스트 통과는 인증된 실서비스 호출 성공을 뜻하지 않습니다.

| 출처 | 용도 | 인증·현재 상태 |
|---|---|---|
| [서울시 실시간 열차 위치](https://data.seoul.go.kr/dataList/OA-12601/A/1/datasetView.do) | 위치·열차번호·방향·원천 시각 | 서울시 키 필요. 공식 `sample` 5건 응답으로 HTTP 연결·필드 확인. 전체 운영 수집 검증은 본인 키 필요 |
| [서울교통공사 열차시간표](https://www.data.go.kr/data/15143847/openapi.do) | 역별 지연 기준 | `B553766/schedule/getTrainSch`, 해당 API 활용승인 필요 |
| [서울시 실시간 도착정보(일괄)](https://data.seoul.go.kr/dataList/OA-15799/A/1/datasetView.do) | 위치 기반 도착 시각 검증 샘플 | 실시간 지하철 키. `realtimeStationArrival/ALL`, 2분 간격. 형식은 첫 응답으로 확인 |
| [서울시 역별 시간대별 승하차 인원](https://data.seoul.go.kr/dataList/OA-12252/S/1/datasetView.do) | 지연 모델 특성 | 일반 인증키. `CardSubwayTime/{start}/{end}/{YYYYMM}`, 매월 5일 전월분 |
| [서울교통공사 지하철알림정보](https://www.data.go.kr/data/15144070/openapi.do) | 지연·사고·무정차 공지 | **2026-11-11 수집 중단**: 기획서 계산에 쓰이지 않음 |
| [SK 역 메타데이터](https://openapi.sk.com/products/detail?linkMenuSeq=415) | 혼잡도 지원 역 확인 | **2026-11-11 수집 코드 제거** (유료, 지하철혼잡도정보로 대체). `puzzle/subway/meta/stations` |
| [SK 역 진입 열차 통계](https://openapi.sk.com/products/detail?linkMenuSeq=309) | 요일별 열차 혼잡도 | **코드 제거**. `puzzle/subway/congestion/stat/train/stations/{stationCode}` |
| [SK 역 진입 칸 통계](https://openapi.sk.com/products/detail?linkMenuSeq=310) | 요일별 칸별 혼잡도 | **코드 제거**. `puzzle/subway/congestion/stat/car/stations/{stationCode}` |
| [SK 실시간 열차/칸](https://openapi.sk.com/products/detail?linkMenuSeq=312)·[역 기준 실시간](https://openapi.sk.com/products/detail?linkMenuSeq=574) | 현재 차내 혼잡 | 상세 문서에 **2025년 11월 서비스 종료** 명시. 수집기에 호출 코드를 넣지 않음 |
| 실시간 승강장 대기 인원·대기열 | 몇 대를 보내야 하는지 추정 | 현재 검증된 공개 API 미확보. 미수집 |

SK 공식 페이지는 JavaScript로 상세 본문을 불러옵니다. 위 메뉴의 본문을 제공하는 공식 `/products/api/getContents` 응답에서도 종료 안내와 통계 API 명세를 확인했습니다. 검색 결과에 구형 실시간 엔드포인트가 남아 있어도 운영 중으로 판단하지 않습니다.

## 실시간 위치

공식 샘플 경로는 `http://swopenAPI.seoul.go.kr/api/subway/{key}/json/realtimePosition/0/999/2호선`입니다. 이 환경에서는 HTTP 샘플 요청이 성공했고 HTTPS 443 연결은 타임아웃이었습니다. 기본 URL은 공식 HTTP 형식을 사용하며 요청 URL이나 키를 로그로 출력하지 않습니다. 원천의 `total`을 보고 추가 페이지를 처리하며 첫 5건 샘플은 운영 수집 데이터에 섞지 않습니다.

`recptnDt`는 원천 수신 시각이고 `collected_at`은 PC 수집 시각입니다. 전자는 물리적 도착을 센서로 직접 측정한 타임스탬프와 같다고 보장할 수 없습니다. 실제 샘플에서도 열차별 시차가 달랐으므로 신선도와 관측 간격을 함께 보존합니다. 과거 위치 이력의 소급 제공은 공식 FAQ상 불가능합니다.

## 시간표

공식 Swagger의 필수 인자는 `tmprTmtblYn`, `upbdnbSe`, `wkndSe`, `lineNm`, `serviceKey`입니다. 예전 `SearchSTNTimeTableByIDService` 이름이나 임의 필드로 대체하지 않습니다. 당일 요일 유형과 `상시`, 일반·임시 시간표를 모두 조회하고 `trainno`, `trainArvlTm`, `trainDptreTm`, `vldBgngDt`, `vldEndDt` 등 전체 행을 보존합니다.

시간표 `upbdnbSe`는 위치 API `updnLine`과 같은 코드(0 = 상행·내선, 1 = 하행·외선)로 바꿔 저장합니다. 2026-11-11 이전 버전은 2호선 내선·외선을 반대로 저장했고, 수집기 시작 시 원문 기준으로 자동 보정합니다. 방향만 어긋나 매칭되지 않으면 `direction_mismatch`로 남겨 대응표 오류를 드러냅니다.

열차번호 앞자리·접두어를 임의로 없애면 서로 다른 열차를 연결할 수 있으므로 정확한 문자열로 시작합니다. 역 코드 체계가 서로 다르므로 현재는 동일 역명과 방향을 함께 대조하며, 매칭 실패를 NULL로 남깁니다. 매칭률을 본 뒤 검증된 코드 대응표를 추가하는 것이 1주차 작업입니다.

## 혼잡도

2026-11-11부터 열차 혼잡도는 서울교통공사 지하철혼잡도정보(무료, 분기 갱신, 1~8호선)를 씁니다. 아래는 코드 제거 전 SK 연동 기록입니다.

2026-11-11 실제 키 연동 확인: 공통 역 목록과 강남역의 열차·칸 통계는 정상 응답했습니다. 역 목록에서 문서에 기재된 선택 인자 `type=congestionTrain` 또는 `type=congestionCar`를 지정하면 400/9000 오류가 발생해 선택 인자를 생략했습니다. 공통 역 목록에 있다고 두 통계가 모두 제공된다고 가정하지 않고 개별 응답을 확인합니다. HTTP 오류 본문도 인증키를 가린 뒤 원본 파일에 보존합니다.

SK의 `dow=TUE&hh=18`은 과거 통계의 화요일 18시 패턴입니다. 2026-11-10에 요청했다는 이유로 그날 18시 실측이 되지 않습니다. 집계 시작·종료일을 반드시 함께 저장합니다. API가 통계값 0을 반환하는 미운행 조합도 있으므로 0으로 탑승 가능을 판단하지 않습니다. 열차·칸 통계를 역 승강장 혼잡도로 부르지 않습니다.

## 직접 내려받아 적재하는 정적 파일 (`--load-reference`)

공공데이터포털 파일데이터는 내려받기 주소가 고정돼 있지 않아 자동 수집하지 않습니다. `data/reference/`에 저장한 뒤 적재하며, 컬럼은 원본 그대로 `reference_rows`에 보존합니다.

| 데이터셋 이름 | 파일 | 용도 |
|---|---|---|
| `segment_times` | [서울교통공사_역간거리 및 소요시간](https://data.seoul.go.kr/dataList/OA-12034/S/1/datasetView.do) (1~8호선) | 그래프 엣지 가중치 |
| `transfer_times` | [서울교통공사_환승역거리 소요시간 정보](https://www.data.go.kr/data/15044419/fileData.do) (환승역 145개, 2025-12-31 기준) | 환승 엣지, 환승 성공 확률 입력 |
| `station_coords` | [서울교통공사_1_8호선 역사 좌표(위경도) 정보](https://www.data.go.kr/data/15099316/fileData.do) (276개 역) | 그래프 노드, 노선도, 역명 검증 |
| `train_congestion` | [서울교통공사_지하철혼잡도정보](https://www.data.go.kr/data/15071311/fileData.do) (30분, 상하선, 평일·토·일, 분기) | 탑승 실패 대기 규칙(가정 기반 시나리오) |

승하차 인원 API가 응답하지 않으면 [서울교통공사_역별 일별 시간대별 승하차인원 정보](https://www.data.go.kr/data/15048032/fileData.do) CSV로 대체합니다.

## 참고만 하는 파일

- [4호선 열차 칸별 혼잡도](https://www.data.go.kr/data/15167486/fileData.do): 공식 메타데이터상 2026-06-01~07-31 신조 전동차 대상 과거 로그입니다. 편성번호·호차·측정시각·현재역·다음역 등을 제공합니다. 2호선 오늘 데이터의 대체재로 사용하지 않습니다. 대용량 분할 ZIP이므로 현재 자동 다운로드 범위에 넣지 않았습니다.

공휴일 캘린더는 `day_type_overrides`로 관리합니다. 강수량·날씨는 출처가 없고 4주 이력으로 표본이 부족해 사용하지 않습니다(2026-11-11 결정).
