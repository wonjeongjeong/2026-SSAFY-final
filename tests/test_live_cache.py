"""Web refreshes stay fresh without accumulating response archives or samples."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import gzip
import importlib.util
import io
import sys
import threading
from urllib.error import HTTPError

from tests.test_collection import Fixture, FakeResponse, TUESDAY
from src.subway_collect.config import ROOT
from src.subway_collect.storage import Store
from src.subway_collect.runner import Runner

sys.path.insert(0, str(ROOT / 'src'))
spec = importlib.util.spec_from_file_location('cache_test_gateway', ROOT / 'services/web/live_gateway_remote.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class LiveCacheTests(Fixture):
    def test_collector_archives_every_120_seconds_while_web_can_refresh(self):
        current = [TUESDAY]
        runner = Runner(self.config, self.store, clock=lambda: current[0])
        runner.keys = runner.client.keys = {'SEOUL_SUBWAY_API_KEY': 'test'}
        runner.client.opener = self.response
        self.assertTrue(runner.arrival_step())
        for seconds in (15, 60, 119):
            current[0] = TUESDAY + timedelta(seconds=seconds)
            self.assertFalse(runner.arrival_step())
            self.assertTrue(self.snapshot(current[0])['available'])
        current[0] = TUESDAY + timedelta(seconds=120)
        self.assertTrue(runner.arrival_step())
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM requests WHERE dataset='arrival_samples'").fetchone()[0], 2)
        self.assertEqual(len(list(self.store.data.glob('raw/**/*.json.gz'))), 2)

    def client(self, at, opener, store=None):
        client = gateway.Client(self.config, store or self.store, lambda _: True,
                                clock=lambda: at, opener=opener)
        client.keys = {'SEOUL_SUBWAY_API_KEY': 'secretABC'}
        return client

    def response(self, request, **kwargs):
        if 'realtimePosition' in request.full_url:
            return FakeResponse({'realtimePositionList': []})
        return FakeResponse({'realtimeArrivalList': [dict(subwayId='1002', statnId='222', statnNm='secretABC')]})

    def snapshot(self, at=TUESDAY, opener=None, store=None):
        return gateway.collect_snapshot(store or self.store, self.config,
                                        self.client(at, opener or self.response, store), lambda: at)

    def test_refresh_replaces_two_files_and_keeps_request_accounting(self):
        for seconds in (0, 16, 32):
            result = self.snapshot(TUESDAY + timedelta(seconds=seconds))
            self.assertTrue(result['available'])
        self.assertEqual(len(list((self.store.data / 'live_cache').glob('*.json.gz'))), 2)
        self.assertEqual(list(self.store.data.glob('raw/**/*.json.gz')), [])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM requests').fetchone()[0], 6)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM requests WHERE raw_path IS NOT NULL').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM arrival_samples').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM positions').fetchone()[0], 0)
        for p in (self.store.data / 'live_cache').glob('*.json.gz'):
            with gzip.open(p, 'rt', encoding='utf-8') as f:
                self.assertNotIn('secretABC', f.read())

    def test_new_gateway_connection_reuses_cache_then_refreshes_at_expiry(self):
        first = self.snapshot()
        other = Store(self.root, initialize=False)
        try:
            def unexpected(*args, **kwargs):
                self.fail('fresh cache triggered HTTP')
            again = self.snapshot(TUESDAY + timedelta(seconds=14), unexpected, other)
            self.assertTrue(again['cached'])
            self.assertEqual(first['collected_at'], again['collected_at'])
            after = self.snapshot(TUESDAY + timedelta(seconds=15), store=other)
            self.assertFalse(after['cached'])
            self.assertNotEqual(first['collected_at'], after['collected_at'])
        finally:
            other.close()

    def test_concurrent_connections_only_fetch_once_per_feed(self):
        barrier = threading.Barrier(6)
        def read(_):
            store = Store(self.root, initialize=False)
            try:
                barrier.wait(timeout=5)
                return self.snapshot(store=store)
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(read, range(6)))
        self.assertTrue(all(r['available'] for r in results))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM requests').fetchone()[0], 2)

    def test_collector_archive_reused_without_extra_arrival_request(self):
        client = self.client(TUESDAY, self.response)
        client.fetch('seoul_subway', 'arrival_samples', 'ALL', 'https://example.test', gateway.arrival_response)
        result = self.snapshot(TUESDAY + timedelta(seconds=5))
        self.assertTrue(result['cached'])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM requests WHERE dataset='web_arrivals'").fetchone()[0], 0)
        self.assertEqual(len(list(self.store.data.glob('raw/**/*.json.gz'))), 1)
        self.snapshot(TUESDAY + timedelta(seconds=16))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM requests WHERE dataset='web_arrivals'").fetchone()[0], 1)
        self.assertEqual(len(list(self.store.data.glob('raw/**/*.json.gz'))), 1)

    def test_failed_refresh_is_not_stale_success_and_is_shared(self):
        self.snapshot()
        def fail(*args, **kwargs):
            raise HTTPError('https://example.test', 503, 'unavailable', {}, io.BytesIO(b'secretABC'))
        failed = self.snapshot(TUESDAY + timedelta(seconds=16), fail)
        self.assertFalse(failed['available'])
        self.assertIsNone(failed['collected_at'])
        self.assertEqual(failed['arrivals'], [])
        self.snapshot(TUESDAY + timedelta(seconds=20), fail)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM requests').fetchone()[0], 4)
        self.assertEqual(list(self.store.data.glob('raw/**/*.json.gz')), [])

    def test_web_calls_obey_shared_budget(self):
        self.config.values['daily_budgets']['seoul_subway'] = 1
        self.snapshot()
        result = self.snapshot(TUESDAY + timedelta(seconds=16))
        self.assertFalse(result['available'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM requests').fetchone()[0], 1)

    def test_corrupt_cache_is_replaced(self):
        self.snapshot()
        path = self.store.data / 'live_cache' / 'arrivals.json.gz'
        path.write_bytes(b'broken')
        self.assertTrue(self.snapshot()['available'])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM requests WHERE dataset='web_arrivals'").fetchone()[0], 2)
