import sys
from src.subway_collect.stream_state import run

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(run())
