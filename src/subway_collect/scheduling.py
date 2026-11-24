"""Bounded polling lanes; one slow line/reference request cannot stall all feeds."""
from concurrent.futures import ThreadPoolExecutor
import logging
import threading

from .runner import Runner
from .storage import Store

LOG = logging.getLogger('subway')


class CollectionScheduler:
    def __init__(self, config, task=None):
        self.config = config
        self.workers = config.values.get('position_workers', 4)
        self.pools = {kind: ThreadPoolExecutor(max_workers=count, thread_name_prefix=kind)
                      for kind, count in (('positions', self.workers), ('arrivals', 1), ('reference', 1))}
        self.futures = {}
        self.due = {}
        self.active = False
        self.stopping = threading.Event()
        self.retry = {'arrivals': {}, 'reference': {}}
        self.task = task or self.collect

    def collect(self, kind, line):
        # Connections never cross threads, and only the parent initializes schema.
        store = Store(self.config.root, initialize=False)
        try:
            runner = Runner(self.config, store)
            allowed = runner.client.allowed
            runner.client.allowed = lambda t: not self.stopping.is_set() and allowed(t)
            if kind == 'positions':
                runner.positions([line])
            else:
                runner.retry_at = self.retry[kind]
                if kind == 'arrivals':
                    runner.refresh_keys()
                    if runner.keys['SEOUL_SUBWAY_API_KEY']:
                        runner.arrival_step()
                else:
                    runner.auxiliary_step(include_arrivals=False)
        finally:
            store.close()

    def step(self, current, tick):
        for key, future in list(self.futures.items()):
            if future.done():
                try:
                    future.result()
                except Exception as exc:
                    LOG.error('Collection task %s failed: %s', key, type(exc).__name__)
                del self.futures[key]
        active = self.config.active(current)
        if active and not self.active:
            interval = self.config.values['position_interval_seconds']
            lines = self.config.values['lines']
            self.due = {('positions', line): tick + i * interval / len(lines) for i, line in enumerate(lines)}
            self.due.update({('arrivals', None): tick, ('reference', None): tick})
        self.active = active
        if not active or self.stopping.is_set():
            return
        for key, scheduled in sorted(self.due.items(), key=lambda item: item[1]):
            kind, line = key
            if scheduled > tick or key in self.futures:
                continue
            interval = (self.config.values['position_interval_seconds'] if kind == 'positions'
                        else int(bool(self.config.values.get('arrival_sample_seconds', 0))) if kind == 'arrivals' else 1)
            # arrival_step owns its 15s freshness gate. Recheck every second so
            # small thread-start jitter cannot accidentally skip a whole cycle.
            if not interval:
                continue
            capacity = self.workers if kind == 'positions' else 1
            if sum(k[0] == kind for k in self.futures) >= capacity:
                continue
            self.futures[key] = self.pools[kind].submit(self.task, kind, line)
            # Skip missed slots instead of issuing a burst of catch-up requests.
            self.due[key] += interval * (int((tick - scheduled) // interval) + 1)

    def close(self):
        self.stopping.set()
        for pool in self.pools.values():
            pool.shutdown(wait=True, cancel_futures=True)
