import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from src.subway_collect.kafka_stream import PositionRelay, create_topic, position_event, producer_for
from src.subway_collect.storage import Store


def insert_position(store, number):
    row = dict(event_id=f'position-{number}', date_kst='2026-11-26', dow='THU',
               session='morning', collected_at=f'2026-11-26T08:00:{number:02d}+09:00',
               observed_at=f'2026-11-26T08:00:{number:02d}+09:00', line='2호선',
               train_no='2001', station_id='1002000201', station_name='시청', direction='0',
               train_status='1', quality='fresh', payload_json=json.dumps({'trainNo': '2001', 'sample': number}))
    with store.db:
        store.insert('positions', row)


class FakeProducer:
    def __init__(self, fail_at=None, pending=False):
        self.messages = []
        self.fail_at, self.pending = fail_at, pending

    def produce(self, topic, **kwargs):
        self.messages.append((topic, kwargs))

    def poll(self, timeout):
        pass

    def flush(self, timeout):
        if self.pending:
            return len(self.messages)
        for i, (_, kwargs) in enumerate(self.messages):
            callback = kwargs.pop('on_delivery', None)
            if callback:
                callback('broker unavailable' if i == self.fail_at else None, None)
        return 0


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.folder.name))
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(lambda: self.store.close())

    def test_explicit_initial_offset_required(self):
        with self.assertRaisesRegex(ValueError, 'First run'):
            PositionRelay(self.store, FakeProducer())

    def test_envelope_preserves_identity_source_time_and_partition_key(self):
        insert_position(self.store, 1)
        row = self.store.db.execute('SELECT rowid AS relay_rowid,* FROM positions').fetchone()
        key, value = position_event(row)
        event = json.loads(value)
        self.assertEqual(json.loads(key), ['2026-11-26', '2호선', '0', '2001'])
        self.assertEqual(event['event_id'], 'position-1')
        self.assertEqual(event['source_timestamp'], row['observed_at'])
        self.assertEqual(event['payload']['sample'], 1)
        self.assertNotIn('relay_rowid', event['position'])
        self.assertEqual(event['position']['quality'], 'fresh')

    def test_acknowledged_batch_resumes_after_process_restart(self):
        for i in range(1, 4):
            insert_position(self.store, i)
        relay = PositionRelay(self.store, FakeProducer(), start='beginning', batch_size=2)
        self.assertEqual(relay.step(), 2)
        self.store.close()
        self.store = Store(Path(self.folder.name), initialize=False)
        producer = FakeProducer()
        restarted = PositionRelay(self.store, producer, start='beginning')
        self.assertEqual(restarted.step(), 1)
        self.assertEqual(json.loads(producer.messages[0][1]['value'])['event_id'], 'position-3')
        self.assertEqual(restarted.step(), 0)

    def test_partial_failure_replays_entire_batch_with_stable_ids(self):
        for i in range(1, 4):
            insert_position(self.store, i)
        producer = FakeProducer(fail_at=1)
        relay = PositionRelay(self.store, producer, start='beginning')
        with self.assertRaises(RuntimeError):
            relay.step()
        self.assertEqual(self.store.get(relay.checkpoint_key)['rowid'], 0)
        retry = FakeProducer()
        self.assertEqual(PositionRelay(self.store, retry).step(), 3)
        self.assertEqual([m[1]['value'] for m in producer.messages], [m[1]['value'] for m in retry.messages])

    def test_delivery_timeout_never_advances_cursor(self):
        insert_position(self.store, 1)
        relay = PositionRelay(self.store, FakeProducer(pending=True), start='beginning')
        with self.assertRaises(RuntimeError):
            relay.step()
        self.assertEqual(self.store.get(relay.checkpoint_key)['rowid'], 0)

    def test_crash_after_ack_before_checkpoint_is_replayable(self):
        insert_position(self.store, 1)
        relay = PositionRelay(self.store, FakeProducer(), start='beginning')
        with patch.object(self.store, 'put', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                relay.step()
        self.assertEqual(PositionRelay(self.store, FakeProducer()).step(), 1)

    def test_latest_skips_history_only_once_then_catches_up(self):
        insert_position(self.store, 1)
        relay = PositionRelay(self.store, FakeProducer(), start='latest')
        insert_position(self.store, 2)
        restarted = PositionRelay(self.store, FakeProducer(), start='latest')
        self.assertEqual(restarted.step(), 1)
        self.assertEqual(restarted.state['event_id'], 'position-2')

    def test_collection_can_commit_while_kafka_waits(self):
        insert_position(self.store, 1)
        producer = FakeProducer()
        original_flush = producer.flush
        def concurrent_flush(timeout):
            other = Store(Path(self.folder.name), initialize=False)
            try:
                insert_position(other, 2)
            finally:
                other.close()
            return original_flush(timeout)
        producer.flush = concurrent_flush
        relay = PositionRelay(self.store, producer, start='beginning')
        self.assertEqual(relay.step(), 1)
        self.assertEqual(relay.step(), 1)

    def test_replacement_or_retention_fails_closed(self):
        insert_position(self.store, 1)
        relay = PositionRelay(self.store, FakeProducer(), start='beginning')
        relay.step()
        with self.store.db:
            self.store.db.execute('DELETE FROM positions')
        insert_position(self.store, 2)
        with self.assertRaisesRegex(ValueError, 'Source cursor changed'):
            relay.step()

    def test_destination_change_requires_new_stream(self):
        PositionRelay(self.store, FakeProducer(), start='beginning')
        with self.assertRaisesRegex(ValueError, 'Destination changed'):
            PositionRelay(self.store, FakeProducer(), destination='other:9092')

    def test_produce_error_keeps_checkpoint(self):
        insert_position(self.store, 1)
        producer = FakeProducer()
        producer.produce = lambda *args, **kwargs: (_ for _ in ()).throw(BufferError())
        relay = PositionRelay(self.store, producer, start='beginning')
        with self.assertRaises(BufferError):
            relay.step()
        self.assertEqual(self.store.get(relay.checkpoint_key)['rowid'], 0)


@unittest.skipUnless(os.environ.get('KAFKA_TEST_BOOTSTRAP'), 'opt-in local Kafka integration')
class KafkaIntegrationTests(unittest.TestCase):
    def test_real_broker_publish_consume_restart(self):
        from confluent_kafka import Consumer
        from confluent_kafka.admin import AdminClient
        bootstrap = os.environ['KAFKA_TEST_BOOTSTRAP']
        config = {'bootstrap.servers': bootstrap}
        topic = 'subway.test.' + uuid.uuid4().hex
        create_topic(config, topic)
        admin = AdminClient(config)
        self.addCleanup(lambda: admin.delete_topics([topic])[topic].result(timeout=20))
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            consumer = Consumer({**config, 'group.id': topic, 'auto.offset.reset': 'earliest', 'enable.auto.commit': False})
            try:
                insert_position(store, 1)
                relay = PositionRelay(store, producer_for(config), topic=topic, destination=bootstrap, start='beginning')
                self.assertEqual(relay.step(), 1)
                insert_position(store, 2)
                restarted = PositionRelay(store, producer_for(config), topic=topic, destination=bootstrap)
                self.assertEqual(restarted.step(), 1)
                self.assertEqual(restarted.step(), 0)
                consumer.subscribe([topic])
                events, keys = [], []
                deadline = time.monotonic() + 30
                while len(events) < 2 and time.monotonic() < deadline:
                    message = consumer.poll(1)
                    if message is not None:
                        self.assertIsNone(message.error())
                        events.append(json.loads(message.value()))
                        keys.append(message.key())
                self.assertEqual([e['event_id'] for e in events], ['position-1', 'position-2'])
                self.assertEqual(keys[0], keys[1])
            finally:
                consumer.close()
                store.close()
