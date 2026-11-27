import copy
from datetime import timedelta
import json
import os
import unittest
from unittest.mock import Mock

from tests.test_collection import Fixture, TUESDAY
from src.subway_collect.derive import DelayMatcher
from src.subway_collect.kafka_stream import position_event
from src.subway_collect.stream_state import Processor, snapshot, consume


class MemoryCache:
    def __init__(self):
        self.rows, self.index = {}, {}

    def get(self, key):
        return self.rows.get(key)

    def eval(self, script, count, key, index, epoch, value, cutoff):
        previous = self.get(key)
        if previous and json.loads(previous)['observed_epoch'] >= epoch:
            return 0
        self.rows[key] = value
        self.index[key] = epoch
        self.index = {k:v for k,v in self.index.items() if v > cutoff}
        return 1

    def zrangebyscore(self, index, low, high):
        return [k for k,v in self.index.items() if low <= v <= high]

    def mget(self, keys):
        return [self.get(k) for k in keys]


class StreamTests(Fixture):
    def setUp(self):
        super().setUp()
        self.cache = MemoryCache()
        self.current = TUESDAY+timedelta(seconds=31)
        self.factory = lambda date: DelayMatcher(self.store, date, self.config)
        self.processor = Processor(self.cache, self.factory, lambda: self.current)
        with self.store.db:
            self.store.insert('timetable', dict(event_id='schedule', fetched_date='2026-11-10',
                line='2호선', train_no='2001', station_name='강남', direction='1',
                arrival_time='175930', departure_time='175950', payload_json='{}'))

    def event(self, seconds, status='0'):
        self.save(seconds, status)
        row = self.store.db.execute('SELECT rowid AS relay_rowid,* FROM positions ORDER BY rowid DESC LIMIT 1').fetchone()
        return json.loads(position_event(row)[1])

    def test_bounded_arrival_matches_existing_rules_and_reaches_snapshot(self):
        self.assertEqual(self.processor.process(self.event(0)), 'stored')
        self.assertEqual(self.processor.process(self.event(30, '1')), 'stored')
        feed = snapshot(self.cache, self.current)
        self.assertEqual(feed['source'], 'kafka_redis')
        self.assertEqual(feed['delays'][0]['delay_seconds'], 60)
        self.assertEqual(feed['delays'][0]['lower_delay_seconds'], 30)
        self.assertEqual(feed['positions'][0]['trainNo'], '2001')

    def test_restart_duplicate_and_reversal_do_not_change_state(self):
        earlier, arrival = self.event(0), self.event(30, '1')
        self.processor.process(earlier)
        self.processor.process(arrival)
        saved = copy.deepcopy(self.cache.rows)
        restarted = Processor(self.cache, self.factory, lambda: self.current)
        self.assertEqual(restarted.process(arrival), 'duplicate_or_reversed')
        self.assertEqual(restarted.process(earlier), 'duplicate_or_reversed')
        self.assertEqual(self.cache.rows, saved)

    def test_stale_future_missing_time_never_looks_live(self):
        event = self.event(0)
        self.current = TUESDAY+timedelta(seconds=91)
        self.assertEqual(self.processor.process(event), 'stale')
        self.current = TUESDAY-timedelta(seconds=6)
        self.assertEqual(self.processor.process(event), 'stale')
        event['source_timestamp'] = None
        self.assertEqual(self.processor.process(event), 'stale')
        self.assertFalse(snapshot(self.cache, self.current)['available'])

    def test_snapshot_expires_without_any_new_messages(self):
        self.processor.process(self.event(30, '1'))
        self.assertTrue(snapshot(self.cache, self.current)['available'])
        self.assertFalse(snapshot(self.cache, TUESDAY+timedelta(seconds=121))['available'])

    def test_first_arrival_is_not_a_verified_delay(self):
        self.processor.process(self.event(30, '1'))
        self.assertEqual(snapshot(self.cache, self.current)['delays'], [])

    def test_unmatched_timetable_does_not_invent_zero_delay(self):
        with self.store.db:
            self.store.db.execute('DELETE FROM timetable')
        self.processor.process(self.event(0))
        self.processor.process(self.event(30, '1'))
        self.assertEqual(snapshot(self.cache, self.current)['delays'], [])

    def test_implausibly_early_match_is_not_served(self):
        with self.store.db:
            self.store.db.execute("UPDATE timetable SET arrival_time='181730'")
        self.processor.process(self.event(0))
        self.processor.process(self.event(30,'1'))
        self.assertEqual(snapshot(self.cache,self.current)['delays'],[])

    def test_redis_failure_prevents_kafka_commit(self):
        message = Mock()
        message.error.return_value = None
        message.value.return_value = json.dumps(self.event(30)).encode()
        consumer = Mock()
        consumer.consume.return_value = [message]
        processor = Mock()
        processor.process.side_effect = OSError('Redis unavailable')
        with self.assertRaises(OSError):
            consume(consumer, processor)
        consumer.commit.assert_not_called()

    def test_unchanged_timetable_reuses_index_but_new_rows_refresh_it(self):
        from src.subway_collect.stream_state import MatcherCache
        factory=MatcherCache(self.store,self.config)
        first=factory('2026-11-10')
        self.assertIs(factory('2026-11-10'),first)
        with self.store.db:
            self.store.db.execute("UPDATE timetable SET event_id='new-id'")
            # Existing API pages append rows; a new identity simulates that.
            self.store.db.execute("INSERT INTO timetable SELECT 'second',fetched_date,line,train_no,station_id,station_name,direction,day_type,arrival_time,departure_time,payload_json FROM timetable LIMIT 1")
        self.assertIsNot(factory('2026-11-10'),first)

    @unittest.skipUnless(os.environ.get('REDIS_TEST_URL'), 'opt-in Redis integration')
    def test_real_redis_lua_monotonicity_and_snapshot(self):
        from src.subway_collect.stream_state import redis_client
        from unittest.mock import patch
        import uuid
        prefix = 'subway:test:' + uuid.uuid4().hex + ':'
        cache = redis_client(os.environ['REDIS_TEST_URL'])
        with patch('src.subway_collect.stream_state.PREFIX', prefix):
            try:
                processor = Processor(cache, self.factory, lambda: self.current)
                first, arrival = self.event(0), self.event(30, '1')
                self.assertEqual(processor.process(first), 'stored')
                self.assertEqual(processor.process(arrival), 'stored')
                self.assertEqual(processor.process(first), 'duplicate_or_reversed')
                self.assertEqual(snapshot(cache, self.current)['delays'][0]['delay_seconds'], 60)
                self.assertFalse(snapshot(cache, TUESDAY+timedelta(seconds=121))['available'])
            finally:
                keys=list(cache.scan_iter(prefix+'*'))
                if keys: cache.delete(*keys)

    @unittest.skipUnless(os.environ.get('REDIS_TEST_URL') and os.environ.get('KAFKA_TEST_BOOTSTRAP'), 'opt-in Kafka + Redis integration')
    def test_real_kafka_to_redis_verified_delay(self):
        from confluent_kafka import Consumer
        from confluent_kafka.admin import AdminClient
        from src.subway_collect.kafka_stream import create_topic, producer_for
        from src.subway_collect.stream_state import redis_client
        from unittest.mock import patch
        import time
        import uuid
        topic = 'subway.test.state.' + uuid.uuid4().hex
        cfg = {'bootstrap.servers':os.environ['KAFKA_TEST_BOOTSTRAP']}
        admin = AdminClient(cfg)
        cache = redis_client(os.environ['REDIS_TEST_URL'])
        create_topic(cfg, topic)
        consumer = Consumer({**cfg, 'group.id':topic, 'auto.offset.reset':'earliest', 'enable.auto.commit':False})
        prefix = topic + ':'
        try:
            producer = producer_for(cfg)
            for event in [self.event(0), self.event(30,'1')]:
                producer.produce(topic, key=b'same-train', value=json.dumps(event).encode())
            self.assertEqual(producer.flush(10),0)
            consumer.subscribe([topic])
            processor = Processor(cache, self.factory, lambda: self.current)
            received = 0
            deadline = time.monotonic()+30
            with patch('src.subway_collect.stream_state.PREFIX',prefix):
                while received < 2 and time.monotonic() < deadline:
                    message = consumer.poll(1)
                    if message is None: continue
                    self.assertIsNone(message.error())
                    processor.process(json.loads(message.value()))
                    consumer.commit(message=message, asynchronous=False)
                    received += 1
                self.assertEqual(received,2)
                self.assertEqual(snapshot(cache,self.current)['delays'][0]['delay_seconds'],60)
        finally:
            consumer.close()
            keys=list(cache.scan_iter(prefix+'*'))
            if keys: cache.delete(*keys)
            admin.delete_topics([topic])[topic].result(timeout=20)
