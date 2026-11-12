"""매칭 개선 뒤 남은 문제를 예시로 확인한다. DB를 읽기만 한다.

[1] 노선 × 짝지은 방법별 지연 분포: 잘못 짝지어진 방법이 있으면 분포가 튄다.
[2] 15분 넘게 늦거나 5분 넘게 이른 지연 예시: 다른 회차와 짝지어졌는지 본다.
[3] destination_mismatch 예시: 위치의 종착역과 시간표의 종착역이 어떻게 다르게 적히는지 본다.
[4] 2호선 미매칭 예시: 그 시각 그 역·방향의 시간표 열차번호와 종착역을 나란히 본다.

실행 (프로젝트 폴더에서):  python scripts/check_match_quality.py 2026-11-11
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data' / 'subway.sqlite3'


def q(values, p):
    return values[min(len(values) - 1, int(p * len(values)))]


def at(date, value):
    clean = str(value or '').replace(':', '')
    if len(clean) != 6 or not clean.isdigit():
        return None
    return datetime.fromisoformat(date) + timedelta(hours=int(clean[:2]), minutes=int(clean[2:4]), seconds=int(clean[4:]))


def main():
    db = sqlite3.connect(f'{DB.as_uri()}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    date = sys.argv[1] if len(sys.argv) > 1 else db.execute('SELECT max(date_kst) FROM delays').fetchone()[0]
    print(f'== {date} 매칭 품질 ==')

    print('\n[1] 노선 × 방법별 지연 분포 (초)')
    groups = {}
    for r in db.execute("SELECT line, match_method, delay_seconds FROM delays WHERE date_kst=? AND match_status='matched_observation_interval'", (date,)):
        groups.setdefault((r[0], r[1]), []).append(r[2])
    for (line, method), v in sorted(groups.items()):
        v.sort()
        far = sum(abs(x) > 900 for x in v)
        print(f'  {line} {method:16} {len(v):>5}건  P5 {q(v, .05):>6.0f}  P50 {q(v, .5):>6.0f}  P95 {q(v, .95):>6.0f}  |지연|>15분 {far}건')

    print('\n[2] 극단값 예시 (15분 넘게 늦음 또는 5분 넘게 이름)')
    for r in db.execute("SELECT d.line, d.match_method, d.train_no, d.station_name, d.observed_at, d.scheduled_at, d.delay_seconds, "
                        "t.train_no AS tt_train, t.payload_json AS tt, p.payload_json AS pos FROM delays d "
                        "JOIN timetable t ON t.event_id=d.timetable_event_id JOIN arrivals a ON a.event_id=d.arrival_id "
                        "LEFT JOIN positions p ON p.event_id=a.position_event_id "
                        "WHERE d.date_kst=? AND d.match_status='matched_observation_interval' AND (d.delay_seconds>900 OR d.delay_seconds<-300) "
                        "ORDER BY abs(d.delay_seconds) DESC LIMIT 8", (date,)):
        tt, pos = json.loads(r['tt'] or '{}'), json.loads(r['pos'] or '{}')
        print(f'  {r["line"]} {r["match_method"]}: 위치 {r["train_no"]}→시간표 {r["tt_train"]}, {r["station_name"]}, '
              f'관측 {r["observed_at"][11:19]} 예정 {r["scheduled_at"][11:19]} ({r["delay_seconds"]:.0f}초), '
              f'종착 위치 {pos.get("statnTnm")} / 시간표 {tt.get("arvlStnNm")}, 출발 {tt.get("dptreStnNm")}')

    print('\n[3] destination_mismatch: 위치 종착역 vs 같은 번호 시간표 종착역 (상위 조합)')
    pairs = Counter()
    for r in db.execute("SELECT d.line, d.train_no, d.station_name, a.direction, p.payload_json AS pos FROM delays d "
                        "JOIN arrivals a ON a.event_id=d.arrival_id LEFT JOIN positions p ON p.event_id=a.position_event_id "
                        "WHERE d.date_kst=? AND d.match_status='destination_mismatch'", (date,)):
        pos = json.loads(r['pos'] or '{}')
        tts = {json.loads(x[0] or '{}').get('arvlStnNm') for x in db.execute(
            'SELECT payload_json FROM timetable WHERE fetched_date=? AND line=? AND train_no=? AND direction=?',
            (date, r['line'], r['train_no'], r['direction']))}
        pairs[(r['line'], pos.get('statnTnm'), ' / '.join(sorted(x for x in tts if x)))] += 1
    for (line, real, tt), n in pairs.most_common(10):
        print(f'  {line}: 위치 종착 "{real}"  ↔  시간표 종착 "{tt}"   {n}건')

    print('\n[4] 2호선 미매칭 예시: 위치 열차 vs 그 역·방향에서 ±5분 안에 예정된 시간표 열차')
    rows = db.execute("SELECT d.train_no, d.station_name, d.observed_at, a.direction, p.payload_json AS pos FROM delays d "
                      "JOIN arrivals a ON a.event_id=d.arrival_id LEFT JOIN positions p ON p.event_id=a.position_event_id "
                      "WHERE d.date_kst=? AND d.line='2호선' AND d.match_status='unmatched_timetable' ORDER BY d.observed_at LIMIT 400", (date,)).fetchall()
    shown, seen = 0, set()
    for r in rows:
        if r['train_no'] in seen:
            continue
        seen.add(r['train_no'])
        observed = datetime.fromisoformat(r['observed_at']).replace(tzinfo=None)
        near = []
        for t in db.execute('SELECT train_no, arrival_time, departure_time, payload_json FROM timetable WHERE fetched_date=? AND line=? AND station_name=? AND direction=?',
                            (date, '2호선', r['station_name'], r['direction'])):
            when = at(date, t['arrival_time']) or at(date, t['departure_time'])
            if when and abs((when - observed).total_seconds()) <= 300:
                near.append(f'{t["train_no"]}({when:%H:%M}, 종착 {json.loads(t["payload_json"] or "{}").get("arvlStnNm")})')
        pos = json.loads(r['pos'] or '{}')
        print(f'  위치 {r["train_no"]} {r["station_name"]} 방향{r["direction"]} {observed:%H:%M:%S} 종착 {pos.get("statnTnm")}  →  {", ".join(near[:4]) or "없음"}')
        shown += 1
        if shown >= 10:
            break
    tt_numbers = sorted({r[0] for r in db.execute("SELECT train_no FROM timetable WHERE fetched_date=? AND line='2호선'", (date,))})
    real_numbers = sorted({r[0] for r in db.execute("SELECT train_no FROM delays WHERE date_kst=? AND line='2호선' AND match_status='unmatched_timetable'", (date,))})
    print(f'  2호선 시간표 번호 앞자리 분포: {dict(Counter(n[:1] for n in tt_numbers))}')
    print(f'  2호선 미매칭 위치 번호 앞자리 분포: {dict(Counter(n[:1] for n in real_numbers))}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
