"""Optional Kafka CLI; ordinary collection needs no Kafka dependency."""
import sys

from src.subway_collect.kafka_stream import run

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(run())
