from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import shutil
import signal
from pathlib import Path
import time

from .config import Config, DAYS, ROOT, TIMETABLE_LINES, load_keys, now
from .derive import match_delays
from .runner import Runner
from .storage import Store, dumps

LOG = logging.getLogger('subway')


@contextmanager
def lock_file(data, name='collector.lock'):
    path = data / name
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
        raise RuntimeError(f'{name}: another process already owns this data directory') from None
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
        collection_settings={key: config.values.get(key) for key in ('position_interval_seconds', 'arrival_sample_seconds', 'position_workers')},
        processing=store.get('processing_status'),
        realtime_lines=config.values['lines'],
        timetable_lines=[line for line in config.values['lines'] if line in TIMETABLE_LINES],
        static_timetables=[dict(row) for row in store.db.execute(
            'SELECT line, day_type, valid_from, source, count(DISTINCT train_no) AS trains, count(*) AS stops '
            'FROM static_timetable GROUP BY line, day_type, valid_from, source ORDER BY line, day_type, valid_from')],
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
    modes.add_argument('--load-timetable', metavar='XLSX',
                       help='코레일 공식 전동열차 시각표 XLSX를 고정 시간표로 적재 (--valid-from 필요)')
    parser.add_argument('--valid-from', metavar='YYYY-MM-DD', help='--load-timetable: 시각표 시행일 (제목의 "…부터")')
    parser.add_argument('--line', help='--load-timetable: 시트 이름에 노선이 없을 때 쓸 수집 노선 이름 (예: 수인분당선)')
    parser.add_argument('--day-type', choices=('평일', '휴일'), help='--load-timetable: 시트 이름에 평일/휴일이 없을 때')
    parser.add_argument('--config', type=Path)
    args = parser.parse_args(argv)
    if args.load_timetable and not args.valid_from:
        parser.error('--load-timetable needs --valid-from YYYY-MM-DD')
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
        if args.load_timetable:
            from .korail import load_korail_timetable
            result = load_korail_timetable(store, args.load_timetable, args.valid_from, args.line, args.day_type)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.export:
            datetime.strptime(args.export, '%Y-%m-%d')
            match_delays(store, args.export, config)
            print(store.export(args.export))
            return 0
        handlers = [logging.StreamHandler(), RotatingFileHandler(store.data / 'collector.log', maxBytes=5_000_000, backupCount=3, encoding='utf-8')]
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=handlers)
        with lock_file(store.data):
            stop_path = store.data / 'stop.request'
            stop_path.unlink(missing_ok=True)
            if args.once:
                runner = Runner(config, store)
                if config.active(now()):
                    runner.positions()
                    runner.auxiliary_step()
                else:
                    LOG.info('Outside collection window; no HTTP requests sent')
                match_delays(store, f'{now():%Y-%m-%d}', config)
                write_health(config, store)
                return 0
            from .scheduling import CollectionScheduler
            from .maintenance import MaintenanceProcess
            scheduler = CollectionScheduler(config)
            processing = MaintenanceProcess(config)
            last_health = -float('inf')

            def stop_signal(signum, frame):
                raise KeyboardInterrupt

            previous_term = signal.signal(signal.SIGTERM, stop_signal)
            LOG.info('Started: lines=%s windows=%s position_interval=%ss arrival_interval=%ss workers=%s; missing keys wait for .env',
                     config.values['lines'], config.values['windows'], config.values['position_interval_seconds'],
                     config.values.get('arrival_sample_seconds'), config.values.get('position_workers', 4))
            try:
                while not stop_path.exists():
                    current, tick = now(), time.monotonic()
                    scheduler.step(current, tick)
                    processing.ensure_running(tick)
                    if tick - last_health >= 15:
                        write_health(config, store)
                        last_health = tick
                    time.sleep(0.2)
            except KeyboardInterrupt:
                LOG.info('Stop requested')
            finally:
                scheduler.close()
                processing.close()
                write_health(config, store)
                signal.signal(signal.SIGTERM, previous_term)
                LOG.info('Stopped; observations preserved; CSV export can resume on next idle window')
        return 0
    except RuntimeError as exc:
        print(str(exc))
        return 2
    finally:
        store.close()
