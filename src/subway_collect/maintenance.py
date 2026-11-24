"""Incremental observed delays and idle-window CSV exports in a separate process."""
import logging
from logging.handlers import RotatingFileHandler
import multiprocessing
import os
import signal
import time

from .config import now
from .derive import DelayMatcher, arrival_batch, match_delays, static_day_type
from .storage import Store

LOG = logging.getLogger('subway.processing')


class IncrementalDelays:
    def __init__(self, store, date, config=None):
        self.store, self.date, self.config = store, date, config
        self.key = 'delay_processing:' + date
        self.state = store.get(self.key) or {'cursor': 0, 'signature': None, 'recheck': None, 'through': 0}
        self.matcher = None
        self.last_refresh = -float('inf')

    def step(self, tick, limit=250):
        if self.matcher is None or tick - self.last_refresh >= 30:
            signature = list(self.store.db.execute(
                'SELECT count(*),coalesce(max(rowid),0) FROM timetable WHERE fetched_date=?', (self.date,)).fetchone())
            signature += list(self.store.db.execute(
                'SELECT count(*),coalesce(max(rowid),0),max(loaded_at) FROM static_timetable').fetchone())
            signature.append(static_day_type(self.store, self.date, self.config))
            if signature != self.state['signature'] or self.matcher is None:
                self.matcher = DelayMatcher(self.store, self.date, self.config)
            if signature != self.state['signature']:
                # Revisit old matches when more timetable pages arrive, but always
                # process new observations first. Persist replay progress on restart.
                self.state.update(signature=signature, recheck=0, through=self.state['cursor'])
            self.last_refresh = tick
        fresh = arrival_batch(self.store, self.date, self.state['cursor'], limit)
        self.matcher.write(self.store, fresh)
        if fresh:
            self.state['cursor'] = fresh[-1]['sequence']
        replay = []
        if self.state['recheck'] is not None:
            replay = arrival_batch(self.store, self.date, self.state['recheck'], limit, self.state['through'])
            self.matcher.write(self.store, replay)
            self.state['recheck'] = replay[-1]['sequence'] if len(replay) == limit else None
        # Committing outputs before the cursor is intentional: a crash may replay
        # an idempotent upsert, but cannot skip an uncommitted observation.
        self.store.put(self.key, self.state)
        return len(fresh) + len(replay)


def worker_main(config, stop):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    store = Store(config.root, initialize=False)
    handler = RotatingFileHandler(store.data / 'processing.log', maxBytes=2_000_000, backupCount=2, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=[handler])
    day = f'{now():%Y-%m-%d}'
    processor = IncrementalDelays(store, day, config)
    next_export = 0
    was_active = config.active(now())
    try:
        from .cli import lock_file
        with lock_file(store.data, name='processing.lock'):
            LOG.info('Processing started pid=%s; exports run outside collection windows', os.getpid())
            while not stop.is_set():
                current, tick = now(), time.monotonic()
                if f'{current:%Y-%m-%d}' != day:
                    match_delays(store, day, config)
                    store.export(day)
                    day = f'{current:%Y-%m-%d}'
                    processor = IncrementalDelays(store, day, config)
                    next_export = 0
                count = processor.step(tick)
                active = config.active(current)
                if was_active and not active:
                    next_export = 0
                was_active = active
                # Never start a large CSV scan during collection. The collector
                # remains independent even if an export spans the next window.
                if not active and tick >= next_export and count == 0:
                    started = time.monotonic()
                    store.export(day)
                    LOG.info('Export date=%s duration_seconds=%.2f', day, time.monotonic() - started)
                    next_export = time.monotonic() + config.values.get('export_interval_seconds', 3600)
                store.put('processing_status', {'pid': os.getpid(), 'date': day,
                          'updated_at': now().isoformat(), 'last_arrival_sequence': processor.state['cursor'],
                          'rechecking_timetable': processor.state['recheck'] is not None})
                stop.wait(1 if count else 3)
    except Exception as exc:
        LOG.error('Processing stopped unexpectedly: %s', type(exc).__name__)
        raise
    finally:
        store.close()


class MaintenanceProcess:
    def __init__(self, config):
        self.config = config
        self.context = multiprocessing.get_context('spawn')
        self.stop_event = self.context.Event()
        self.process = None
        self.last_start = -float('inf')

    def ensure_running(self, tick):
        if self.process is not None and self.process.is_alive():
            return
        if tick - self.last_start < 10:
            return
        if self.process is not None:
            self.process.join()
            LOG.warning('Restarting processing worker; previous exit=%s', self.process.exitcode)
        self.process = self.context.Process(target=worker_main, args=(self.config, self.stop_event),
                                            name='subway-processing', daemon=True)
        self.process.start()
        self.last_start = tick

    def close(self):
        self.stop_event.set()
        if self.process is not None:
            self.process.join(timeout=10)
            if self.process.is_alive():
                # Source observations are already durable; partial CSVs remain
                # .tmp and the last completed CSV is retained. Resume next run.
                LOG.warning('Stopping busy processing worker; pending work resumes on restart')
                self.process.terminate()
                self.process.join(timeout=5)
