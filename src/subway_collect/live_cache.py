"""Bounded latest-response cache shared by independent web/SSH processes."""
from contextlib import contextmanager, ExitStack
import gzip
import json
import time

from .cli import lock_file
from .config import now, source_time

TTL_SECONDS = 15


@contextmanager
def refresh_lock(folder, name, timeout=15):
    deadline = time.monotonic() + timeout
    with ExitStack() as stack:
        while True:
            try:
                stack.enter_context(lock_file(folder, name=name + '.lock'))
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('live cache refresh busy') from None
                time.sleep(.05)
        yield


class LiveFeedCache:
    def __init__(self, store, clock=now):
        self.folder = store.data / 'live_cache'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.clock = clock

    def get(self, name, fetch, archived=None):
        """Return (entry, reused). Failures are also shared for 15s, never stale ETAs.

        fetch must use Client.fetch(..., archive=False), retaining quota/accounting.
        archived may return a fresh collector entry without making an API request.
        """
        if name not in {'arrivals', 'gtx_positions'}:
            raise ValueError('unknown live cache feed')
        path = self.folder / (name + '.json.gz')
        with refresh_lock(self.folder, name):
            current = self.clock()
            try:
                with gzip.open(path, 'rt', encoding='utf-8') as stream:
                    entry = json.load(stream)
                stamp = source_time(entry['checked_at'])
                rows = entry['rows']
                if (stamp and 0 <= (current - stamp).total_seconds() < TTL_SECONDS
                        and (rows is None or (isinstance(rows, list) and source_time(entry['collected_at'])))
                        and isinstance(entry['total'], int)):
                    return entry, True
            except (OSError, ValueError, KeyError, TypeError, EOFError):
                pass
            if archived is not None:
                entry = archived(current)
                if entry is not None:
                    return entry, True
            result = fetch()
            stamp = self.clock().isoformat()
            rows, total = result if result is not None else (None, 0)
            entry = dict(checked_at=stamp, collected_at=stamp if result is not None else None,
                         rows=rows, total=total)
            # One fixed file per feed; lock spans read/check/fetch/replace. No DB
            # write transaction stays open during HTTP. A crash releases the lock.
            temp = path.with_suffix('.tmp')
            with gzip.open(temp, 'wt', encoding='utf-8') as stream:
                json.dump(entry, stream, ensure_ascii=False)
            temp.replace(path)
            return entry, False
