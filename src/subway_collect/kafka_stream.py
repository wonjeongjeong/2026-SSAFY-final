"""Relay committed, immutable position rows without blocking API collection.

Delivery is at least once. Consumers must deduplicate by event_id. A batch is
checkpointed only after every broker acknowledgement; a crash can replay it.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import time

from .storage import Store, dumps

TOPIC = 'subway.positions.raw.v1'
LOG = logging.getLogger('subway.kafka')


def position_event(row):
    normalized = dict(row)
    normalized.pop('relay_rowid', None)
    payload = json.loads(normalized.pop('payload_json'))
    key = dumps([row['date_kst'], row['line'], row['direction'], row['train_no']])
    event = dict(schema_version=1, event_id=row['event_id'], source='seoul_subway',
                 measurement_kind='train_position', source_timestamp=row['observed_at'],
                 collected_at=row['collected_at'], date_kst=row['date_kst'], dow=row['dow'],
                 payload=payload, position=normalized)
    return key.encode('utf-8'), dumps(event).encode('utf-8')


class PositionRelay:
    def __init__(self, store, producer, *, topic=TOPIC, stream_id='default',
                 destination='localhost:9092', start=None, batch_size=500, timeout=35):
        if not 1 <= batch_size <= 5000:
            raise ValueError('batch_size must be between 1 and 5000')
        self.store, self.producer, self.topic = store, producer, topic
        self.batch_size, self.timeout = batch_size, timeout
        self.checkpoint_key = 'kafka:positions:v1:' + stream_id
        self.destination = dict(bootstrap=destination, topic=topic)
        self.state = store.get(self.checkpoint_key)
        if self.state is None:
            if start not in ('beginning', 'latest'):
                raise ValueError('First run requires --start beginning or --start latest')
            row = store.db.execute('SELECT rowid,event_id FROM positions ORDER BY rowid DESC LIMIT 1').fetchone() if start == 'latest' else None
            self.state = dict(rowid=row[0] if row else 0, event_id=row[1] if row else None,
                              destination=self.destination, initial_start=start)
            store.put(self.checkpoint_key, self.state)
        if self.state['destination'] != self.destination:
            raise ValueError('Destination changed: use a new --stream-id for a new destination')

    def step(self):
        cursor = self.state['rowid']
        # Refuse silent loss if retention/VACUUM/replacement changed source rowids.
        if cursor:
            previous = self.store.db.execute('SELECT event_id FROM positions WHERE rowid=?', (cursor,)).fetchone()
            if not previous or previous[0] != self.state['event_id']:
                raise ValueError('Source cursor changed; stop retention/VACUUM and reconcile before replay')
        rows = self.store.db.execute('SELECT rowid AS relay_rowid,* FROM positions WHERE rowid>? ORDER BY rowid LIMIT ?',
                                     (cursor, self.batch_size)).fetchall()
        if not rows:
            return 0
        errors, delivered = [], []

        def acknowledged(error, message):
            if error is not None:
                errors.append(str(error))
            else:
                delivered.append(True)

        # No database transaction is held while waiting for the network.
        for row in rows:
            key, value = position_event(row)
            self.producer.produce(self.topic, key=key, value=value, on_delivery=acknowledged)
            self.producer.poll(0)
        remaining = self.producer.flush(self.timeout)
        if remaining or errors or len(delivered) != len(rows):
            # Caller exits on failure. Do not reuse a producer with pending messages.
            raise RuntimeError('Kafka batch not fully acknowledged; checkpoint unchanged; restart to retry')
        state = {**self.state, 'rowid': rows[-1]['relay_rowid'], 'event_id': rows[-1]['event_id']}
        self.store.put(self.checkpoint_key, state)
        self.state = state
        return len(rows)


def client_config(args):
    config = {}
    if args.client_config:
        config = json.loads(Path(args.client_config).read_text(encoding='utf-8-sig'))
        if not isinstance(config, dict):
            raise ValueError('client-config must be a JSON object')
    config['bootstrap.servers'] = args.bootstrap_servers
    return config


def producer_for(config):
    from confluent_kafka import Producer
    return Producer({**config, 'enable.idempotence': True, 'acks': 'all',
                     'compression.type': 'gzip', 'linger.ms': 20,
                     'delivery.timeout.ms': 30000, 'allow.auto.create.topics': False})


def create_topic(config, topic, partitions=3, replication_factor=1):
    from confluent_kafka import KafkaError, KafkaException
    from confluent_kafka.admin import AdminClient, NewTopic
    # Bounded development retention: 24h or roughly 256 MiB per partition.
    admin = AdminClient(config)
    future = admin.create_topics([NewTopic(topic, num_partitions=partitions,
        replication_factor=replication_factor, config={'retention.ms': '86400000',
        'retention.bytes': '268435456', 'segment.bytes': '16777216', 'segment.ms': '3600000'})])[topic]
    try:
        future.result(timeout=30)
    except KafkaException as error:
        if error.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='SQLite positions -> Kafka (separate process)')
    parser.add_argument('command', choices=('init', 'publish', 'consume', 'status'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--bootstrap-servers', default='localhost:9092')
    parser.add_argument('--client-config', help='Ignored JSON file for TLS/SASL client settings')
    parser.add_argument('--topic', default=TOPIC)
    parser.add_argument('--stream-id', default='default')
    parser.add_argument('--start', choices=('beginning', 'latest'), help='First run only; never resets a saved cursor')
    parser.add_argument('--batch-size', type=int, default=500)
    parser.add_argument('--once', action='store_true', help='Publish at most one batch, then exit')
    parser.add_argument('--partitions', type=int, default=3)
    parser.add_argument('--replication-factor', type=int, default=1)
    parser.add_argument('--group', default='subway-position-inspector-v1')
    parser.add_argument('--count', type=int, default=10)
    parser.add_argument('--wait-seconds', type=int, default=30)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.command == 'init':
        create_topic(client_config(args), args.topic, args.partitions, args.replication_factor)
        print('Topic ready:', args.topic)
        return 0
    if args.command == 'consume':
        from confluent_kafka import Consumer
        consumer = Consumer({**client_config(args), 'group.id': args.group,
                             'auto.offset.reset': 'earliest', 'enable.auto.commit': False,
                             'enable.auto.offset.store': False, 'allow.auto.create.topics': False})
        received = 0
        deadline = time.monotonic() + args.wait_seconds
        try:
            consumer.subscribe([args.topic])
            while received < args.count and time.monotonic() < deadline:
                message = consumer.poll(1)
                if message is None:
                    continue
                if message.error():
                    raise RuntimeError(str(message.error()))
                event = json.loads(message.value())
                print(dumps(event), flush=True)
                consumer.commit(message=message, asynchronous=False)
                received += 1
        finally:
            consumer.close()
        return 0 if received == args.count else 1
    if not (args.root / 'data' / 'subway.sqlite3').is_file():
        parser.error('Collector database missing under --root; collect data first')
    store = Store(args.root, initialize=False)
    try:
        if args.command == 'status':
            state = store.get('kafka:positions:v1:' + args.stream_id)
            pending = store.db.execute('SELECT count(*) FROM positions WHERE rowid>?',
                                      (state['rowid'] if state else 0,)).fetchone()[0]
            print(dumps(dict(checkpoint=state, pending_positions=pending)))
            return 0
        from .cli import lock_file
        with lock_file(store.data, 'kafka-positions.lock'):
            relay = PositionRelay(store, producer_for(client_config(args)), topic=args.topic,
                stream_id=args.stream_id, destination=args.bootstrap_servers, start=args.start, batch_size=args.batch_size)
            while True:
                count = relay.step()
                if count:
                    LOG.info('published=%d checkpoint=%d', count, relay.state['rowid'])
                if args.once:
                    return 0
                if not count:
                    time.sleep(1)
    finally:
        store.close()


def run():
    try:
        return main()
    except KeyboardInterrupt:
        return 0
    except ImportError:
        LOG.error('Install Kafka dependencies: python -m pip install -r requirements-kafka.txt')
        return 1
    except Exception as error:
        # Client config can contain credentials; do not print it or a traceback.
        LOG.error('Kafka worker stopped (%s). Check broker/configuration; cursor is retained.', type(error).__name__)
        return 1
