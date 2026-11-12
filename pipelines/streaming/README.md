# 스트리밍 구현 위치

다음 단계에서 수집 원본을 Kafka로 보내고 Flink에서 열차별 궤적·역 도착·지연을 계산합니다. 현재 Python 수집기의 `derive.py`와 데이터 계약을 검증 기준으로 사용합니다.

- 토픽 후보: `subway.positions.raw.v1`, `subway.arrivals.v1`, `subway.delays.v1`.
- 파티션 키: 운행일 + 호선 + 방향 + 열차번호.
- 이벤트 시각: 원천 `recptnDt`. 지연 수신·중복·순서 역전을 처리하고 watermark를 설정합니다.
- 출력: Redis 현재 상태, HDFS/Parquet 과거 도착 이벤트.
- 미구현: Kafka 프로듀서/컨슈머·Flink job·체크포인트 복구 테스트.
