"""위치 호출 간격(30초)을 줄일 가치가 있는지 지금 바로 확인한다.

1부: 이미 받은 원문(data/raw/.../positions/*.json.gz)으로, 30초 호출 사이에 놓치는 것이 있는지 본다.
2부: 실제 API를 5초 간격으로 3분 동안 불러(36건), 원천이 몇 초마다 새 값을 주는지 직접 잰다.
DB와 수집 중인 데이터는 건드리지 않는다. 인증키는 화면에 출력하지 않는다.

실행 (프로젝트 폴더에서):  python scripts/check_interval.py
2부 건너뛰기:              python scripts/check_interval.py --no-probe
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import gzip
import json
from pathlib import Path
import statistics
import sys
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.subway_collect.config import load_keys  # noqa: E402

ARRIVED = '1'


def parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', ''))
    except (TypeError, ValueError):
        return None


def rows_of(body):
    doc = json.loads(body)
    return doc.get('realtimePositionList') or []


def pct(part, whole):
    return f'{100 * part / whole:.0f}%' if whole else '-'


def analyze_raw(day=None, line_filter=None):
    folders = sorted((ROOT / 'data' / 'raw').glob('date=*'))
    if day:
        folders = [f for f in folders if f.name == f'date={day}']
    if not folders:
        print('1부: data/raw 아래에 위치 원문이 없습니다. 2부 결과만 보세요.')
        return None
    folder = folders[-1]
    polls = []  # (collected_at, target, rows)
    for path in sorted(folder.glob('dow=*/positions/*.json.gz')):
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            payload = json.load(f)
        if payload.get('http_status') != 200:
            continue
        try:
            rows = rows_of(payload['body'])
        except (ValueError, KeyError):
            continue
        line = str(payload.get('target', '')).split(':')[0]
        if line_filter and line != line_filter:
            continue
        polls.append((parse_time(payload.get('collected_at')), line, rows))
    polls = [p for p in polls if p[0]]
    polls.sort(key=lambda p: p[0])
    print(f'1부: {folder.name} 원문 {len(polls)}회 호출분 분석')
    if len(polls) < 10:
        print('  호출 수가 너무 적어 판단하기 어렵습니다.')
        return None

    # 열차별로 호출마다 본 값을 시간순으로 모은다
    seen = defaultdict(list)  # (line, train, direction) -> [(collected, recptnDt, station, status)]
    for collected, line, rows in polls:
        for r in rows:
            key = (line, str(r.get('trainNo')), str(r.get('updnLine')))
            seen[key].append((collected, r.get('recptnDt'), str(r.get('statnId')), str(r.get('trainSttus'))))

    pairs = changed = 0
    visits = visits_with_arrival = 0
    for obs in seen.values():
        for (t0, rec0, _, _), (t1, rec1, _, _) in zip(obs, obs[1:]):
            if 0 < (t1 - t0).total_seconds() <= 45:  # 연속된 두 호출
                pairs += 1
                changed += rec0 != rec1
        # 역 방문 = 같은 역이 연속으로 보인 묶음. 그 묶음에서 '도착(1)'을 한 번이라도 봤는지
        current, statuses = None, set()
        for _, _, station, status in obs + [(None, None, None, None)]:
            if station != current:
                if current is not None:
                    visits += 1
                    visits_with_arrival += ARRIVED in statuses
                current, statuses = station, set()
            statuses.add(status)

    print(f'  연속 두 호출 사이에 원천 시각(recptnDt)이 바뀐 비율: {pct(changed, pairs)} ({changed}/{pairs})')
    print(f'  역 방문 중 "도착" 상태를 실제로 본 비율: {pct(visits_with_arrival, visits)} ({visits_with_arrival}/{visits})')
    return dict(change_rate=changed / pairs if pairs else None,
                arrival_seen=visits_with_arrival / visits if visits else None)


def probe(seconds=180, every=5, line='2호선'):
    keys = load_keys(ROOT)
    key = keys['SEOUL_SUBWAY_API_KEY']
    if not key:
        print('2부: .env에 SEOUL_SUBWAY_API_KEY가 없어 건너뜁니다.')
        return None
    base = json.loads((ROOT / 'config' / 'collector.json').read_text(encoding='utf-8-sig'))['seoul_subway_base'].rstrip('/')
    url = f"{base}/{quote(key, safe='')}/json/realtimePosition/0/999/{quote(line, safe='')}"
    calls = seconds // every
    print(f'2부: {line}을 {every}초 간격으로 {seconds}초 동안 호출합니다 ({calls}건). 잠시 기다려주세요...')
    last = {}       # train -> recptnDt
    changes = defaultdict(list)  # train -> [poll time when recptnDt changed]
    ok = 0
    for i in range(calls):
        started = time.monotonic()
        try:
            req = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'SeoulSubwayCollector/1.0'})
            with urlopen(req, timeout=10) as res:
                rows = rows_of(res.read().decode('utf-8-sig'))
            ok += 1
            now = time.monotonic()
            for r in rows:
                train = (str(r.get('trainNo')), str(r.get('updnLine')))
                rec = r.get('recptnDt')
                if train in last and last[train] != rec:
                    changes[train].append(now)
                last[train] = rec
        except Exception as exc:  # 네트워크 오류 등. 키가 들어간 URL은 출력하지 않는다
            print(f'  {i + 1}번째 호출 실패: {type(exc).__name__}')
        time.sleep(max(0, every - (time.monotonic() - started)))
    gaps = [b - a for ts in changes.values() for a, b in zip(ts, ts[1:])]
    print(f'  성공 {ok}/{calls}건, 관측 열차 {len(last)}대, 원천 값이 바뀐 횟수 {sum(map(len, changes.values()))}')
    if not gaps:
        print('  변화 간격을 잴 만큼 바뀐 값이 없었습니다. 운행이 적은 시간이면 퇴근 시간에 다시 실행해보세요.')
        return None
    under30 = sum(g < 30 for g in gaps)
    print(f'  같은 열차의 값이 바뀌는 간격: 중앙값 {statistics.median(gaps):.0f}초, '
          f'30초보다 짧은 경우 {pct(under30, len(gaps))} ({under30}/{len(gaps)})')
    return dict(median_gap=statistics.median(gaps), under30=under30 / len(gaps))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-probe', action='store_true', help='API를 부르지 않고 원문만 분석')
    parser.add_argument('--date', help='분석할 날짜 YYYY-MM-DD (기본: 가장 최근)')
    args = parser.parse_args()

    raw = analyze_raw(args.date)
    live = None if args.no_probe else probe()

    print('\n판단')
    reasons = []
    if live and live['under30'] >= 0.3:
        reasons.append(f"원천 값의 {live['under30']:.0%}가 30초보다 짧은 간격으로 바뀜")
    if raw and raw['arrival_seen'] is not None and raw['arrival_seen'] < 0.8:
        reasons.append(f"30초 호출로는 역 방문의 {1 - raw['arrival_seen']:.0%}에서 '도착' 상태를 놓침")
    if reasons:
        print('  → 15초로 줄이는 것을 권장합니다: ' + '; '.join(reasons))
        print('  config/collector.json: "position_interval_seconds": 15, "daily_budgets"의 "seoul_subway": 14000')
        print('  (8개 노선 × 6시간 × 240회 = 11,520건 + 도착정보 180건 + 재시도 여유)')
    elif live or raw:
        print('  → 30초 유지를 권장합니다. 더 자주 불러도 새로 얻는 정보가 적습니다.')
    else:
        print('  → 판단할 데이터가 부족합니다. 위 메시지를 확인하세요.')


if __name__ == '__main__':
    main()
