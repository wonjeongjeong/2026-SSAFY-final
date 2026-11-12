from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import shutil
from pathlib import Path
import time

from .config import Config, DAYS, ROOT, load_keys, now
from .derive import match_delays
from .runner import Runner
from .storage import Store, dumps

LOG = logging.getLogger('subway')


@contextmanager
def lock_file(data):
    path = data / 'collector.lock'
    handle = path.open('a+b')
    try:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('Collector already running for this data directory') from None
    try:
        yield
    finally:
        if os.name == 'nt':
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def health(config, store):
    current = now()
    date = f'{current:%Y-%m-%d}'
    keys = load_keys(config.root)
    counts = {}
    for table in ('positions', 'arrivals', 'delays', 'arrival_samples', 'notices', 'timetable'):
        col = 'date_kst' if table in ('positions', 'arrivals', 'delays', 'arrival_samples') else 'fetched_date'
        counts[table] = store.db.execute(f'SELECT count(*) FROM {table} WHERE {col}=?', (date,)).fetchone()[0]
    boarding = [dict(row) for row in store.db.execute('SELECT use_month,count(*) AS rows FROM boarding_volume GROUP BY use_month ORDER BY use_month')]
    reference = [dict(row) for row in store.db.execute('SELECT dataset,file_name,row_count,loaded_at FROM reference_files ORDER BY dataset,loaded_at')]
    quota = store.db.execute("""SELECT sum(outcome='success') AS ok, sum(error_code IN ('ERROR-337','http_429')) AS limited
        FROM requests WHERE date_kst=? AND provider='seoul_subway'""", (date,)).fetchone()
    requests = [dict(row) for row in store.db.execute('SELECT provider,outcome,count(*) AS count FROM requests WHERE date_kst=? GROUP BY provider,outcome', (date,))]
    matches = [dict(row) for row in store.db.execute('SELECT line,match_status,count(*) AS count FROM delays WHERE date_kst=? GROUP BY line,match_status ORDER BY line', (date,))]
    remaining = {provider: max(0, budget-sum(r['count'] for r in requests if r['provider']==provider))
                 for provider, budget in config.values['daily_budgets'].items()}
    return dict(time_kst=current.isoformat(), date=date, dow=DAYS[current.weekday()],
        active_window=config.active(current), next_window=config.next_start(current).isoformat(),
        keys_configured={k: bool(v) for k, v in keys.items()}, rows=counts, requests=requests,
        boarding_volume_loaded=boarding, reference_files=reference,
        seoul_subway_quota=dict(successful_calls_today=quota['ok'] or 0, limit_errors_today=quota['limited'] or 0,
            verdict='limit_hit' if quota['limited'] else 'lifted_confirmed' if (quota['ok'] or 0) > 1000 else 'not_yet_verified'),
        disk_free_gb=round(shutil.disk_usage(store.data).free / 1e9, 1),
        local_budget_remaining=remaining,
        delay_matching=matches, last_position={line: store.get('last_position:' + line) for line in config.values['lines']},
        train_congestion='seoul_metro_quarterly_statistic_via_load_reference_not_realtime',
        realtime_platform_crowding='unavailable_no_verified_public_source')


def write_health(config, store):
    payload = health(config, store)
    path = store.data / 'status.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description='서울 지하철 출퇴근 데이터 수집기 (KST, 매일 요일별 저장)')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--once', action='store_true', help='운영 시간 내 위치 1회 + 보조 데이터 1요청')
    modes.add_argument('--status', action='store_true', help='키 값 없이 수집 상태 출력')
    modes.add_argument('--doctor', action='store_true', help='인증키·호출 예산·제공 한계 확인; 네트워크 요청 없음')
    modes.add_argument('--export', metavar='YYYY-MM-DD', help='날짜 데이터를 요일별 CSV로 내보내기')
    modes.add_argument('--load-reference', nargs=2, metavar=('DATASET', 'CSV'),
                       help='내려받은 정적 기준 CSV 적재: segment_times|transfer_times|station_coords|train_congestion|boarding_volume_file')
    parser.add_argument('--config', type=Path)
    args = parser.parse_args(argv)
    config = Config.load(args.config)
    store = Store(config.root)
    try:
        if args.status or args.doctor:
            result = health(config, store)
            if args.doctor:
                from .config import minutes
                seconds = sum((minutes(b)-minutes(a))*60 for a, b in config.values['windows'])
                estimated = len(config.values['lines']) * seconds / config.values['position_interval_seconds']
                arrivals = seconds / v if (v := config.values.get('arrival_sample_seconds')) else 0
                result['estimated_position_calls_per_day_without_retries'] = estimated
                result['estimated_arrival_sample_calls_per_day'] = arrivals
                result['daily_local_budgets'] = config.values['daily_budgets']
                result['position_budget_ok'] = estimated + arrivals <= config.values['daily_budgets']['seoul_subway']
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.load_reference:
            from .reference import load_reference
            print(json.dumps(load_reference(store, *args.load_reference), ensure_ascii=False, indent=2))
            return 0
        if args.export:
            datetime.strptime(args.export, '%Y-%m-%d')
            match_delays(store, args.export)
            print(store.export(args.export))
            return 0
        handlers = [logging.StreamHandler(), RotatingFileHandler(store.data / 'collector.log', maxBytes=5_000_000, backupCount=3, encoding='utf-8')]
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=handlers)
        with lock_file(store.data):
            stop_path = store.data / 'stop.request'
            stop_path.unlink(missing_ok=True)
            runner = Runner(config, store)
            lines = config.values['lines']
            interval = config.values['position_interval_seconds']
            start_tick = time.monotonic()
            # 노선을 주기 안에 고르게 나눠 호출한다. 한 노선이 느려도 나머지가 밀리지 않는다.
            next_due = {line: start_tick + i * interval / len(lines) for i, line in enumerate(lines)}
            export_every = config.values.get('export_interval_seconds', 300)
            was_active = False
            last_health, last_export = 0.0, 0.0
            last_date = f'{now():%Y-%m-%d}'
            LOG.info('Started: lines=%s windows=%s; missing keys wait for .env', config.values['lines'], config.values['windows'])
            try:
                while not stop_path.exists():
                    current = now()
                    tick = time.monotonic()
                    active = config.active(current)
                    if active and not was_active:
                        # 수집 시간이 시작될 때 시차를 다시 맞춰, 첫 회차에 8개 노선이 한꺼번에 몰리지 않게 한다.
                        next_due = {line: tick + i * interval / len(lines) for i, line in enumerate(lines)}
                    was_active = active
                    if active:
                        due = list(lines) if args.once else [line for line in lines if tick >= next_due[line]]
                        for line in due:
                            # 늦어졌으면 밀린 회차를 몰아서 부르지 않고 다음 정해진 시각으로 넘어간다.
                            next_due[line] += interval * (int((tick - next_due[line]) // interval) + 1)
                            runner.positions([line])
                        runner.auxiliary_step()
                    elif args.once:
                        LOG.info('Outside collection window; no HTTP requests sent')
                    if f'{current:%Y-%m-%d}' != last_date:
                        match_delays(store, last_date)
                        store.export(last_date)
                        last_date = f'{current:%Y-%m-%d}'
                    if tick-last_health >= 30 or args.once:
                        write_health(config, store)
                        last_health = tick
                    if tick-last_export >= export_every or args.once:
                        # 주기 내보내기에서는 가장 큰 positions를 빼고, 자정·중지 때만 전체를 쓴다.
                        match_delays(store, last_date)
                        store.export(last_date, include_positions=False)
                        last_export = tick
                    if args.once:
                        break
                    time.sleep(0.5)
            except KeyboardInterrupt:
                LOG.info('Keyboard stop requested')
            finally:
                match_delays(store, last_date)
                store.export(last_date)
                write_health(config, store)
                LOG.info('Stopped; data preserved')
        return 0
    except RuntimeError as exc:
        print(str(exc))
        return 2
    finally:
        store.close()
