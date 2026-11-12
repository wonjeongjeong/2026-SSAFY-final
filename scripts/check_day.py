"""하루치 수집 결과를 점검한다: 호출 제한으로 끊겼는지, 노선별로 빈틈 없이 모였는지, 지연이 계산되는지.

DB를 읽기만 하고 고치지 않는다. 수집기가 켜져 있어도 실행할 수 있다.

실행 (프로젝트 폴더에서):  python scripts/check_day.py 2026-11-11
날짜를 빼면 가장 최근에 위치를 모은 날을 본다.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data' / 'subway.sqlite3'
LIMIT_CODES = ('ERROR-337', 'http_429', '22')


def t(value):
    return datetime.fromisoformat(value)


def main():
    if not DB.exists():
        print('data/subway.sqlite3가 없습니다. 프로젝트 폴더에서 실행했는지 확인하세요.')
        return 1
    db = sqlite3.connect(f'{DB.as_uri()}?mode=ro', uri=True)
    date = sys.argv[1] if len(sys.argv) > 1 else (db.execute('SELECT max(date_kst) FROM positions').fetchone()[0])
    if not date:
        print('위치 데이터가 아직 없습니다.')
        return 1
    print(f'== {date} 수집 점검 ==\n')

    # 1. 호출 제한
    print('[1] 호출 결과 (공급자별)')
    rows = db.execute('SELECT provider, outcome, coalesce(error_code, \'-\'), count(*) FROM requests '
                      'WHERE date_kst=? GROUP BY 1,2,3 ORDER BY 1,2', (date,)).fetchall()
    for provider, outcome, code, n in rows:
        print(f'  {provider:13} {outcome:8} {code:14} {n:>6}건')
    limited = db.execute(f'SELECT count(*), min(started_at) FROM requests WHERE date_kst=? AND error_code IN ({",".join("?" * len(LIMIT_CODES))})',
                         (date, *LIMIT_CODES)).fetchone()
    budget = db.execute("SELECT count(*) FROM gaps WHERE end_at LIKE ? AND reason='local_daily_budget_exhausted'", (date + '%',)).fetchone()[0]
    ok = db.execute("SELECT count(*) FROM requests WHERE date_kst=? AND provider='seoul_subway' AND outcome='success'", (date,)).fetchone()[0]
    if limited[0]:
        print(f'  → 호출 제한 오류 {limited[0]}건, 처음 {limited[1][11:19]}. 이 시각 이후 위치 수집이 막혔을 수 있음')
    elif budget:
        print('  → 수집기 자체 하루 상한(daily_budgets)에 닿아 멈춤. 설정값을 올려야 함')
    else:
        print(f'  → 호출 제한 오류 없음. 위치 API 성공 {ok}건' + (' (하루 1,000건 초과 → 제한 해제 확인)' if ok > 1000 else ''))

    # 2. 수집 시간대와 끊김
    print('\n[2] 노선별 수집 구간과 끊김 (위치 호출 간격이 60초를 넘은 곳)')
    lines = [r[0] for r in db.execute('SELECT DISTINCT line FROM positions WHERE date_kst=? ORDER BY line', (date,))]
    for line in lines:
        times = [t(r[0]) for r in db.execute(
            "SELECT started_at FROM requests WHERE date_kst=? AND dataset='positions' AND outcome='success' "
            "AND target LIKE ? ORDER BY started_at", (date, line + ':%'))]
        if not times:
            continue
        gaps = [(a, b) for a, b in zip(times, times[1:]) if 60 < (b - a).total_seconds() < 6 * 3600]
        intervals = sorted((b - a).total_seconds() for a, b in zip(times, times[1:]) if (b - a).total_seconds() <= 60)
        typical = intervals[len(intervals) // 2] if intervals else 0
        print(f'  {line}: {times[0]:%H:%M}~{times[-1]:%H:%M}, 호출 {len(times)}회, 보통 간격 {typical:.0f}초, 끊김 {len(gaps)}곳')
        for a, b in gaps[:3]:
            print(f'      {a:%H:%M:%S} → {b:%H:%M:%S} ({(b - a).total_seconds() / 60:.1f}분 비어 있음)')
    last = db.execute("SELECT max(started_at) FROM requests WHERE date_kst=? AND dataset='positions'", (date,)).fetchone()[0]
    if last:
        end = t(last)
        expected_end = end.replace(hour=20, minute=30, second=0, microsecond=0) if end.hour >= 12 else end.replace(hour=9, minute=30, second=0, microsecond=0)
        missing = (expected_end - end).total_seconds() / 60
        if missing > 2:
            print(f'  → 마지막 호출 {end:%H:%M:%S}. 수집 시간 끝({expected_end:%H:%M})까지 {missing:.0f}분이 비어 있음 '
                  f'(로그가 정상 종료 없이 끝났다면 PC가 꺼진 것)')

    # 3. 데이터 품질
    print('\n[3] 위치 데이터 품질')
    total, fresh = db.execute("SELECT count(*), sum(quality='fresh') FROM positions WHERE date_kst=?", (date,)).fetchone()
    print(f'  위치 {total}건 중 신선한 관측(원천 시각 90초 이내) {fresh or 0}건 ({100 * (fresh or 0) / total:.0f}%)' if total else '  위치 0건')
    for line, n, trains in db.execute('SELECT line, count(*), count(DISTINCT train_no) FROM positions WHERE date_kst=? GROUP BY line ORDER BY line', (date,)):
        print(f'  {line}: 위치 {n}건, 열차 {trains}대')

    # 4. 도착과 지연
    print('\n[4] 역 도착과 지연 계산')
    a_total, a_bounded = db.execute("SELECT count(*), sum(quality='bounded_observation') FROM arrivals WHERE date_kst=?", (date,)).fetchone()
    print(f'  도착 이벤트 {a_total}건, 그중 도착 시각 구간이 확정된 것 {a_bounded or 0}건')
    print('  노선별 시간표 매칭 (matched = 지연 계산됨):')
    stats = {}
    for line, status, n in db.execute('SELECT line, match_status, count(*) FROM delays WHERE date_kst=? GROUP BY 1,2', (date,)):
        stats.setdefault(line, {})[status] = n
    if not stats:
        print('    delays가 비어 있습니다. 먼저 python collector.py --export 날짜 를 실행하세요.')
    for line, s in sorted(stats.items()):
        bounded = sum(n for k, n in s.items() if k != 'unbounded_arrival')
        matched = s.get('matched_observation_interval', 0)
        detail = ', '.join(f'{k} {n}' for k, n in sorted(s.items()))
        print(f'    {line}: 매칭률 {100 * matched / bounded:.0f}% ({matched}/{bounded})  [{detail}]' if bounded else f'    {line}: [{detail}]')
    if any(r[1] == 'match_method' for r in db.execute('PRAGMA table_info(delays)')):
        print('  짝지은 방법 (exact = 같은 번호, korail_k_number = 코레일 K 표기, line2_first_digit = 2호선 앞자리, linked_trainno = 회차 후 연계 번호):')
        for line, method, n in db.execute("SELECT line, match_method, count(*) FROM delays WHERE date_kst=? "
                                          "AND match_status='matched_observation_interval' GROUP BY 1,2 ORDER BY 1,2", (date,)):
            print(f'    {line} {method or "-":16} {n:>6}건')
    row = db.execute("SELECT count(*), round(avg(delay_seconds)), min(delay_seconds), max(delay_seconds) FROM delays "
                     "WHERE date_kst=? AND match_status='matched_observation_interval'", (date,)).fetchone()
    if row[0]:
        print(f'  계산된 지연 {row[0]}건: 평균 {row[1]:.0f}초, 최소 {row[2]:.0f}초, 최대 {row[3]:.0f}초')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
