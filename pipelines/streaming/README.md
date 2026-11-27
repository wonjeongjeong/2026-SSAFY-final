# Kafka 위치 스트림

현재 구현: `SQLite positions → 별도 Python 전송기 → Kafka → 확인용 소비자`.
수집기는 기존처럼 위치 15초, 도착정보 보관 120초로 실행합니다. 전송기는 DB에
커밋된 새 위치 행을 최대 500개씩 읽으며 대기 중에는 1초마다 확인합니다.
API를 추가 호출하거나 위치 원문을 별도 outbox에 복제하지 않습니다.

Python 소비자의 도착·지연 계산, Redis 및 추천 API 연결은 [stream-serving.md](../../docs/stream-serving.md)에 구현되어 있습니다.
Flink 전환은 후속 범위이며 기존 Python `derive.py` 매칭 규칙을 재사용합니다.

## 로컬 실행 (저장소 루트)

Docker Desktop을 실행하고 다음 명령을 사용합니다. Kafka는 로컬 PC의
`localhost:9092`에서만 접근 가능합니다. EC2에서 이 PC의 localhost에 연결할 수는 없습니다.

```powershell
python -m venv .venv-kafka
.\.venv-kafka\Scripts\python.exe -m pip install -r requirements-kafka.txt
docker compose -f infra/kafka.compose.yml up -d --wait
.\.venv-kafka\Scripts\python.exe kafka_pipeline.py init
```

수집 DB가 있는 프로젝트 폴더를 `--root`로 지정합니다. 기본값은 이 저장소입니다.
최초 실행 시 시작점을 반드시 선택합니다. **`latest`는 기존 이력을 건너뛰고 이후
커밋된 데이터부터**, `beginning`은 기존 위치 이력을 처음부터 보냅니다.
과거 데이터가 많으면 `beginning`은 실시간까지 따라잡는 시간이 필요합니다.

```powershell
.\.venv-kafka\Scripts\python.exe kafka_pipeline.py publish --start latest
```

다른 터미널에서 수신과 잔여 건수를 확인합니다. `consume`은 확인용으로 10건을
표준 출력에 표시한 뒤 오프셋을 커밋합니다. 실제 업무 처리 소비자는 별도로 구현해야 합니다.

```powershell
.\.venv-kafka\Scripts\python.exe kafka_pipeline.py consume --count 10 --wait-seconds 60
.\.venv-kafka\Scripts\python.exe kafka_pipeline.py status
```

재실행은 `publish`만 쓰면 됩니다. `--start`를 다시 전달해도 기존 체크포인트를
초기화하지 않습니다. 재생용 별도 체크포인트는 `--stream-id replay-20261126 --start beginning`처럼
새 이름을 지정합니다. 같은 데이터 폴더에서 전송기는 한 개만 실행할 수 있습니다.
Ctrl+C로 종료해도 체크포인트가 남습니다.

`init`은 토픽 `subway.positions.raw.v1`을 3개 파티션, 복제 수 1로 만듭니다.
이미 존재하는 토픽은 수정하지 않습니다. 개발 기본 보관 기간은 24시간,
파티션당 256 MiB로 둘 중 먼저 도달한 제한을 적용합니다. 세그먼트 단위 비동기 삭제이므로
정확한 디스크 상한은 아니며 메타데이터와 내부 토픽 공간도 필요합니다.
운영에서는 처리 지연·재처리 기간에 맞게 보관 정책과 복제 수를 별도로 정합니다.
단일 브로커 구성은 고가용성을 제공하지 않습니다.

중지: `docker compose -f infra/kafka.compose.yml stop` (볼륨과 메시지 유지).
Kafka 볼륨을 삭제하면 SQLite 체크포인트는 남으므로 이전 메시지가 자동 복원되지 않습니다.
새 클러스터에 재전송할 때는 새 `--stream-id`와 `--start beginning`을 사용합니다.

## EC2에서 팀 Kafka로 전송

이 파일 추가만으로 EC2 전송기가 자동 시작되지는 않습니다. EC2에 코드를 반영하고,
**EC2에서 접속 가능한 팀 Kafka 주소**를 설정해야 합니다. 개발 Compose는 외부 접속용이
아닙니다. 팀 브로커의 advertised listener도 EC2에서 접근 가능해야 합니다.

```bash
cd ~/2026-SSAFY-final
python3 -m venv .venv-kafka
.venv-kafka/bin/python -m pip install -r requirements-kafka.txt
.venv-kafka/bin/python kafka_pipeline.py init --bootstrap-servers <팀-Kafka-주소:포트>
.venv-kafka/bin/python kafka_pipeline.py publish --bootstrap-servers <팀-Kafka-주소:포트> --start latest --once
```

첫 실행은 latest 체크포인트를 저장합니다. 이후 수집되는 위치가 전송 대상입니다.
원격 연결은 팀의 사설 네트워크 또는 인증/TLS 구성을 사용하세요. TLS/SASL이 필요하면
git에서 제외한 `kafka-client.local.json`에 librdkafka 설정을 넣고 각 명령에
`--client-config kafka-client.local.json`을 추가합니다. 예: `security.protocol`,
`sasl.mechanism`, `sasl.username`, `sasl.password`, `ssl.ca.location`.
API 키는 Kafka 연결에 필요하지 않습니다.

자동 재시작용 `infra/subway-kafka-publisher.service` 템플릿도 제공합니다.
`config/kafka-publisher.env`에 `KAFKA_BOOTSTRAP_SERVERS=주소:포트`를 기록하고,
인증 환경이라면 unit의 ExecStart에 위 client-config 옵션을 추가하세요.
수동 전송 검증과 최초 체크포인트 생성 후 설치합니다.

```bash
sudo cp infra/subway-kafka-publisher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now subway-kafka-publisher
systemctl status subway-kafka-publisher --no-pager
journalctl -u subway-kafka-publisher -n 30 --no-pager
```

전송기만 추가하므로 기존 `subway-collector` 서비스 재시작은 필요 없습니다.
Kafka 전송 실패 시 전송기는 실패 종료하고 systemd가 10초 뒤 재시작합니다.
수집기는 독립적으로 계속 DB에 저장합니다. `status`의 `pending_positions`로 적체를 확인하세요.

## 전달 계약과 복구

- 공통 필드: `schema_version=1`, `event_id`, `source`, `measurement_kind`,
  `source_timestamp`, `collected_at`, `date_kst`, `dow`, `payload`, `position`.
- `payload`는 API 행, `position`은 정규화된 위치 필드입니다. API 키나 요청 URL은 포함하지 않습니다.
- 파티션 키: `[운행일, 노선, 방향, 열차번호]`의 JSON UTF-8 바이트.
- 원천 시각은 `source_timestamp`, 수집 시각은 `collected_at`으로 분리합니다.
  오래되거나 시각이 없는 관측도 `position.quality`와 함께 보내며 최신으로 간주하지 않습니다.
- DB 삽입 순서로 전송합니다. 원천 시각 순서와 다를 수 있으므로 후속 소비자는
  중복·역순·지연 관측을 처리해야 합니다. 같은 열차의 키는 고정하고 운영 중 파티션 수를
  변경할 때는 순서에 미치는 영향을 검토하세요.
- **At least once**: 배치 전체의 브로커 ACK를 받은 뒤 SQLite 체크포인트를 저장합니다.
  부분 실패·시간 초과는 체크포인트를 진행하지 않습니다. ACK 직후 프로세스가 죽으면
  재시작 시 중복 전송될 수 있으므로 소비자는 `event_id`로 중복 제거해야 합니다.
  Producer idempotence는 활성화하지만 DB부터 소비자까지 exactly-once를 주장하지 않습니다.
- 커서는 기존 immutable `positions`의 SQLite rowid를 사용합니다. 미전송 행을 삭제하지 말고,
  DB 교체·VACUUM·보존 정책을 적용하기 전 전송기를 정지하고 커서를 함께 검토하세요.
  마지막 전송 행의 ID가 바뀌거나 사라졌으면 전송기를 실패 종료시켜 조용한 누락을 막습니다.
  SQLite와 원본 보관 용량, Kafka 보관 용량은 별도로 관리해야 합니다.

## 검증

```powershell
python -m unittest tests.test_kafka_stream -v
$env:KAFKA_TEST_BOOTSTRAP = 'localhost:9092'
.\.venv-kafka\Scripts\python.exe -m unittest tests.test_kafka_stream.KafkaIntegrationTests -v
Remove-Item Env:KAFKA_TEST_BOOTSTRAP
```

통합 테스트는 임시 SQLite와 고유한 테스트 토픽을 만들고 실제 브로커에서 전송·수신·재시작을
검증한 뒤 해당 테스트 토픽만 삭제합니다. EC2 데이터나 운영 토픽을 사용하지 않습니다.

공식 참고: [Apache Kafka](https://kafka.apache.org/quickstart/),
[Confluent Python client](https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html).
