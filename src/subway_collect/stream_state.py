"""Source-time ordered serving state; Redis writes precede Kafka commits."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import time

from .config import Config, now, source_time
from .derive import DelayMatcher
from .storage import Store, dumps, identity

PREFIX = 'subway:live:v1:'
FRESH = 90
SAVE = """
local old = redis.call('GET', KEYS[1])
if old and tonumber(cjson.decode(old).observed_epoch) >= tonumber(ARGV[1]) then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', 180)
redis.call('ZADD', KEYS[2], ARGV[1], KEYS[1])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[3])
return 1
"""


def redis_client(url):
    from redis import Redis
    return Redis.from_url(url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3)


class Processor:
    def __init__(self, cache, matcher_factory, clock=now):
        self.cache, self.matcher_factory, self.clock = cache, matcher_factory, clock
        self.matcher, self.date, self.refresh_at = None, None, 0

    def process(self, event):
        if event.get('schema_version') != 1 or event.get('measurement_kind') != 'train_position':
            raise ValueError('Unsupported event contract')
        p, raw = event['position'], event['payload']
        for field in ('date_kst', 'line', 'direction', 'train_no', 'station_id', 'train_status'):
            if not isinstance(p.get(field), str):
                raise ValueError('Invalid position identity')
        if p['event_id'] != event['event_id'] or raw.get('trainNo') is None:
            raise ValueError('Invalid event identity')
        observed, current = source_time(event.get('source_timestamp')), self.clock()
        if observed is None or not -5 <= (current-observed).total_seconds() <= FRESH:
            return 'stale'
        if p.get('quality') != 'fresh' or p.get('observed_at') != event['source_timestamp']:
            return 'stale'
        key = PREFIX + identity([p['date_kst'], p['line'], p['direction'], p['train_no']])
        previous = self.cache.get(key)
        previous = json.loads(previous) if previous else None
        epoch = observed.timestamp()
        if previous and previous['observed_epoch'] >= epoch:
            return 'duplicate_or_reversed'
        delay = previous.get('delay') if previous else None
        if delay and (observed-source_time(delay['observed_at'])).total_seconds() > FRESH:
            delay = None
        old = previous['position'] if previous else None
        new_arrival = p['train_status'] == '1' and not (
            old and old['station_id'] == p['station_id'] and old['train_status'] in ('1', '2'))
        if new_arrival:
            gap = epoch-previous['observed_epoch'] if previous else None
            bounded = gap is not None and 0 < gap <= FRESH
            arrival = dict(event_id=identity(['arrival', event['event_id']]), date_kst=p['date_kst'],
                dow=p['dow'], line=p['line'], train_no=p['train_no'], direction=p['direction'],
                station_name=p.get('station_name'), observed_at=observed.isoformat(),
                lower_bound_at=old['observed_at'] if bounded else None,
                quality='bounded_observation' if bounded else 'left_censored', position_payload=dumps(raw))
            tick = time.monotonic()
            if self.matcher is None or self.date != p['date_kst'] or tick >= self.refresh_at:
                self.matcher = self.matcher_factory(p['date_kst'])
                self.date, self.refresh_at = p['date_kst'], tick+30
            calculated = self.matcher.values(arrival)
            # Very early matches can be another circulation of the same train.
            # Keep historical matcher outputs separate from serving confidence.
            usable = (calculated['match_status'] == 'matched_observation_interval'
                      and -120 <= calculated['delay_seconds'] <= 1200)
            delay = {**calculated, 'direction':p['direction'], 'destination':raw.get('statnTnm')} if usable else None
        value = dict(event_id=event['event_id'], observed_epoch=epoch, position=p,
                     payload=raw, delay=delay, processed_at=current.isoformat())
        saved = self.cache.eval(SAVE, 2, key, PREFIX+'index', epoch, dumps(value), current.timestamp()-180)
        return 'stored' if saved else 'duplicate_or_reversed'


def snapshot(cache, current=None):
    current = current or now()
    keys = cache.zrangebyscore(PREFIX+'index', current.timestamp()-FRESH, current.timestamp()+5)
    entries = [json.loads(v) for v in cache.mget(keys) if v] if keys else []
    entries = [e for e in entries if -5 <= current.timestamp()-e['observed_epoch'] <= FRESH]
    delays = [e['delay'] for e in entries if e.get('delay') and
              -5 <= (current-source_time(e['delay']['observed_at'])).total_seconds() <= FRESH]
    return dict(available=bool(entries), source='kafka_redis', fetched_at=current.isoformat(),
        collected_at=max((e['position']['collected_at'] for e in entries), default=None),
        arrivals=[], positions=[{**e['payload'], 'line':e['position']['line']} for e in entries],
        delays=delays, received_rows=len(entries), source_total=len(entries),
        error=None if entries else 'no_fresh_stream_positions')


def serve(cache, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/snapshot':
                self.send_error(404)
                return
            try:
                value, status = snapshot(cache), 200
            except Exception:
                value, status = dict(available=False, positions=[], arrivals=[], delays=[], error='stream_state_unavailable'), 503
            body = dumps(value).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


def consume(consumer, processor):
    while True:
        messages = consumer.consume(num_messages=250, timeout=1)
        if not messages:
            continue
        from confluent_kafka import TopicPartition
        offsets = {}
        for message in messages:
            if message.error():
                raise RuntimeError('Kafka consume failed')
            # Poison events fail closed without committing this batch.
            processor.process(json.loads(message.value()))
            offsets[(message.topic(),message.partition())] = message.offset()+1
        consumer.commit(offsets=[TopicPartition(topic,partition,offset)
            for (topic,partition),offset in offsets.items()], asynchronous=False)


class MatcherCache:
    """Refresh only when source timetables change, not on every 30s tick."""
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.signature, self.matcher = None, None

    def __call__(self, date):
        api = tuple(self.store.db.execute('SELECT count(*),max(rowid) FROM timetable WHERE fetched_date=?',(date,)).fetchone())
        static = tuple(self.store.db.execute('SELECT count(*),max(rowid),max(loaded_at) FROM static_timetable').fetchone())
        from .derive import static_day_type
        signature = (date,api,static,static_day_type(self.store,date,self.config))
        if signature != self.signature:
            self.matcher = DelayMatcher(self.store,date,self.config)
            self.signature = signature
        return self.matcher


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('consume', 'serve', 'status'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--redis-url', default='redis://localhost:6379/0')
    parser.add_argument('--bootstrap-servers', default='localhost:9092')
    parser.add_argument('--group', default='subway-state-v1')
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    cache = redis_client(args.redis_url)
    if args.command == 'serve':
        return serve(cache, args.port)
    if args.command == 'status':
        print(dumps(snapshot(cache)))
        return
    from confluent_kafka import Consumer
    store = Store(args.root, initialize=False)
    config = Config.load(root=args.root)
    consumer = Consumer({'bootstrap.servers':args.bootstrap_servers, 'group.id':args.group,
        'enable.auto.commit':False, 'enable.auto.offset.store':False,
        'auto.offset.reset':'earliest', 'allow.auto.create.topics':False,
        'session.timeout.ms':120000, 'heartbeat.interval.ms':3000})
    try:
        consumer.subscribe(['subway.positions.raw.v1'])
        consume(consumer, Processor(cache, MatcherCache(store,config)))
    finally:
        consumer.close()
        store.close()


def run():
    try:
        main()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        logging.error('Stream worker stopped (%s); offset not advanced for failed event', type(error).__name__)
        return 1
