# 개발 환경 구현 위치

현 수집기는 Python 표준 라이브러리와 로컬 SQLite로 실행합니다. 데이터를 즉시 모으는 데 Docker가 필요하지 않습니다.

다음 단계의 Docker Compose 대상은 Kafka, Flink, Redis, HDFS, Spark, Airflow, PostgreSQL, FastAPI, Grafana입니다. 버전·메모리 예산과 스트리밍/배치 계약을 정한 뒤 추가합니다. 빈 컨테이너 정의를 실제 서비스 구현으로 표시하지 않습니다.
