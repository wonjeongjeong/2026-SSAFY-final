# 개발 환경 구현 위치

현 수집기는 Python 표준 라이브러리와 로컬 SQLite로 실행합니다. 데이터를 즉시 모으는 데 Docker가 필요하지 않습니다.

`kafka.compose.yml`은 단일 Kafka 4.1.1 브로커의 로컬 개발 환경입니다. 볼륨을 유지하고 호스트의 127.0.0.1:9092만 공개합니다. `subway-kafka-publisher.service`는 EC2에서 팀 브로커로 위치를 전송할 때 사용할 systemd 템플릿입니다. [실행·검증·배포 방법](../pipelines/streaming/README.md)을 먼저 확인하세요.

Flink, Redis, HDFS, Spark, Airflow, PostgreSQL, FastAPI, Grafana는 후속 범위입니다. 버전·메모리 예산과 스트리밍/배치 계약을 정한 뒤 추가합니다.
