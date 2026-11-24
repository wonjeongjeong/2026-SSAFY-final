"""Offline regressions for concurrent polling and independent delay processing."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading
import time
from unittest.mock import patch

from tests.test_collection import Fixture, TUESDAY
from src.subway_collect.derive import DelayMatcher, arrival_batch
from src.subway_collect.maintenance import IncrementalDelays, MaintenanceProcess
from src.subway_collect.scheduling import CollectionScheduler
from src.subway_collect.storage import Store


class ProcessingTests(Fixture):
    def add_timetable(self):
        with self.store.db:
            self.store.insert('timetable', dict(event_id='tt', fetched_date='2026-11-10', line='2호선',
                train_no='2001', station_name='강남', direction='1', arrival_time='18:00:20', payload_json='{}'))

    def test_restart_resumes_new_arrivals_and_late_timetable_rechecks_old_rows(self):
        self.save(0); self.save(15, '1')
        processor = IncrementalDelays(self.store, '2026-11-10')
        processor.step(0)
        self.assertEqual(self.store.db.execute('SELECT match_status FROM delays').fetchone()[0], 'unmatched_timetable')
        cursor = processor.state['cursor']
        processor = IncrementalDelays(self.store, '2026-11-10')
        self.assertEqual(processor.state['cursor'], cursor)
        self.assertEqual(processor.step(1), 0)
        self.add_timetable()
        self.save(30, '2'); self.save(45, '0'); self.save(60, '1')
        processor.step(32)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM delays').fetchone()[0], 2)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM delays WHERE match_status='matched_observation_interval'").fetchone()[0], 2)
        self.assertEqual(processor.step(33), 0)

    def test_matching_does_not_hold_write_lock_while_computing(self):
        self.save(0); self.save(15, '1'); self.add_timetable()
        matcher = DelayMatcher(self.store, '2026-11-10')
        other = Store(self.root, initialize=False)
        original = matcher.values
        def match(arrival):
            self.assertFalse(self.store.db.in_transaction)
            other.put('collector_can_write', True)
            return original(arrival)
        try:
            with patch.object(matcher, 'values', side_effect=match):
                matcher.write(self.store, arrival_batch(self.store, '2026-11-10'))
            self.assertTrue(self.store.get('collector_can_write'))
        finally:
            other.close()

    def test_budget_reservation_is_atomic_across_connections(self):
        gate = threading.Barrier(8)
        def reserve(_):
            store = Store(self.root, initialize=False)
            try:
                gate.wait(timeout=5)
                return store.begin_request('seoul_subway', 'positions', 'test', TUESDAY, 3)
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(8)))
        self.assertEqual(sum(value is not None for value in results), 3)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM requests').fetchone()[0], 3)

    def test_slow_position_and_reference_do_not_block_arrivals_or_other_line(self):
        self.config.values['lines'] = ['1호선', '2호선']
        release, arrival, healthy = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def task(kind, line):
            calls.append((kind, line))
            if kind == 'reference' or line == '1호선':
                release.wait(timeout=5)
            elif kind == 'arrivals':
                arrival.set()
            elif line == '2호선':
                healthy.set()
        scheduler = CollectionScheduler(self.config, task=task)
        try:
            scheduler.step(TUESDAY, 100)
            self.assertTrue(arrival.wait(timeout=2))
            scheduler.step(TUESDAY + timedelta(seconds=8), 108)
            self.assertTrue(healthy.wait(timeout=2))
            scheduler.step(TUESDAY + timedelta(seconds=31), 131)
            self.assertEqual(calls.count(('positions', '1호선')), 1)
            self.assertLessEqual(sum(k[0] == 'positions' for k in scheduler.futures), self.config.values['position_workers'])
        finally:
            release.set()
            scheduler.close()

    def test_outside_window_schedules_no_network_tasks(self):
        calls = []
        scheduler = CollectionScheduler(self.config, task=lambda *args: calls.append(args))
        try:
            scheduler.step(TUESDAY.replace(hour=12), 100)
        finally:
            scheduler.close()
        self.assertEqual(calls, [])

    def test_spawned_processing_worker_updates_state_and_stops(self):
        worker = MaintenanceProcess(self.config)
        try:
            worker.ensure_running(time.monotonic())
            deadline = time.monotonic() + 8
            while not self.store.get('processing_status') and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertIsNotNone(self.store.get('processing_status'))
            self.assertTrue(worker.process.is_alive())
        finally:
            worker.close()
        self.assertFalse(worker.process.is_alive())
