# Kafka → 지연 계산 → Redis → 출발 시간 계산

`stream_pipeline.py consume`이 `subway.positions.raw.v1`의 관측을 처리합니다.
API를 다시 호출하지 않으며 기존 DB에는 쓰지 않습니다. Redis에서 이전 관측을 읽고
새 역 도착을 찾은 뒤 기존 `DelayMatcher` 규칙으로 SQLite의 공식 시간표와 짝짓습니다.
이때 방향·열차번호·역·운행일을 확인합니다. KRIC 시간표는 방향 검증이 끝나지 않아
여전히 지연 계산에서 제외됩니다.

- 원천 관측이 90초보다 오래됐거나 5초 넘게 미래이면 최신 상태로 쓰지 않습니다.
- 동일 열차에서 같거나 이전 시각은 무시합니다. Redis Lua로 최종 갱신을 원자적으로 처리합니다.
- 첫 도착이나 90초 초과 관측 공백은 확정 지연으로 제공하지 않습니다.
- 최신 위치와 검증된 지연은 Redis에 180초 TTL로 저장하며 API에서 원천 시각을 다시 검사합니다.
- Redis 기록 성공 뒤 Kafka 오프셋을 커밋합니다. 실패 시 커밋하지 않고 재시작합니다.
- Redis AOF everysec는 장애 시 최근 약 1초의 상태를 잃을 수 있습니다. Redis는 단기 제공 캐시이며,
  복구 직후 첫 도착은 보수적으로 미확정 처리하고 이후 관측으로 다시 만듭니다.
  원본 영구 보관과 end-to-end exactly-once 보장을 대신하지 않습니다.
- 파티션별 처리는 순차적입니다. 정상 재시작 중복은 최신 시각 검사로 제거합니다.
  형식이 잘못된 이벤트는 오프셋을 진행하지 않고 실패 종료하므로 운영자가 원인을 확인해야 합니다.
- 시간표 인덱스는 최대 30초마다 갱신합니다. 시간표 보강 시 과거 Redis 도착을 전량 재처리하지는 않습니다.
- Redis 저장 제한은 48MB/noeviction, 컨테이너 제한은 96MB입니다. 한도 도달 시 기록을 실패시켜
  소비자가 조용히 데이터를 버리지 않도록 했습니다.

## 실행

```bash
.venv-kafka/bin/python -m pip install -r requirements-kafka.txt
sudo docker compose -f infra/kafka.compose.yml up -d redis
.venv-kafka/bin/python -B stream_pipeline.py consume
# 다른 터미널
.venv-kafka/bin/python -B stream_pipeline.py serve
curl http://127.0.0.1:8787/snapshot
```

`/snapshot`은 최신 `positions`, 검증된 `delays`, 원천·수집 시각과 `available`을 제공합니다.
관측이 없거나 오래됐으면 `available:false`이고 웹은 시간표 기준으로 계산합니다.
서버 내부 주소에만 바인딩하며 Redis·Kafka·스냅샷·웹 포트를 외부에 공개하지 않습니다.

```bash
sudo cp infra/subway-stream-consumer.service infra/subway-stream-api.service infra/subway-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now subway-stream-consumer subway-stream-api subway-web
systemctl is-active subway-kafka-publisher subway-stream-consumer subway-stream-api subway-web subway-collector
```

웹은 `LIVE_SNAPSHOT_URL=http://127.0.0.1:8787/snapshot` 환경변수로 연결합니다.
변수를 생략한 기존 SSH 게이트웨이도 EC2의 이 API를 우선 읽습니다. 스트림 API가 가동 중인데
데이터가 오래된 경우 원본 API 호출로 신선해 보이게 대체하지 않습니다. API 자체 연결에 실패한
기존 SSH 게이트웨이만 이전 실시간 API 조회 방식으로 복구합니다.

웹 예측은 Kafka 경로일 때 검증된 지연만 사용합니다. 같은 운행의 시간표 도착 시각까지
일치하는 경우 관측 지연을 후속 구간에 투영하고 역산 탐색을 다시 수행합니다.
90초가 지나면 영향이 사라집니다. 실제 향후 지연이 유지된다는 보장은 없는 초기 추정입니다.
계산 엔진에는 그 운행일의 웹 시간표 데이터도 있어야 합니다. 수집 DB의 시간표 적재와
웹 `services/web/data` 동기화는 별도입니다. 기존 `scripts/sync_web_timetable.py`를 사용하세요.

EC2의 loopback 웹을 학교 PC에서 보려면 기존에 인증된 SSH 접속에서
`ssh -L 5173:127.0.0.1:5173 ubuntu@서버주소`로 포워딩하거나 팀의 인증된 웹 배포에 연결합니다.
이 작업은 접속 키를 만들거나 보안 그룹을 자동 변경하지 않습니다.

테스트는 `python -B -m unittest tests.test_stream_state`입니다.
`REDIS_TEST_URL`, `KAFKA_TEST_BOOTSTRAP`을 지정하면 고유 테스트 키와 토픽으로 실제
Kafka → Redis 지연 계산도 검증합니다. 운영 키를 초기화하지 않습니다.
