from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from .config import DAYS, KST


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def identity(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.data = self.root / 'data'
        self.data.mkdir(exist_ok=True, parents=True)
        self.db = sqlite3.connect(self.data / 'subway.sqlite3', timeout=30)
        self.db.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[2] / 'sql' / 'schema.sql'
        self.db.executescript(schema.read_text(encoding='utf-8'))
        self.add_missing_columns()
        self.fix_timetable_directions()

    def add_missing_columns(self):
        """기존 DB에 새 컬럼을 더한다(멱등). CREATE TABLE IF NOT EXISTS는 이미 있는 표를 바꾸지 않는다."""
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(delays)')}
        if 'match_method' not in columns:
            with self.db:
                self.db.execute('ALTER TABLE delays ADD COLUMN match_method TEXT')

    def fix_timetable_directions(self):
        """2026-11-11 이전 버전은 2호선 내선/외선 코드를 위치 API와 반대로 저장했다. 원문 기준으로 바로잡는다(멱등)."""
        with self.db:
            for code, names in (('0', ('상행', '내선')), ('1', ('하행', '외선'))):
                self.db.execute("UPDATE timetable SET direction=? WHERE json_extract(payload_json,'$.upbdnbSe') IN (?,?) "
                                "AND direction IS NOT ?", (code, *names, code))

    def close(self):
        self.db.close()

    def get(self, key):
        row = self.db.execute('SELECT value FROM checkpoints WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        with self.db:
            self.db.execute('INSERT INTO checkpoints VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, dumps(value)))

    def insert(self, table, values):
        columns = ','.join(values)
        marks = ','.join('?' for _ in values)
        return self.db.execute(f'INSERT OR IGNORE INTO {table} ({columns}) VALUES ({marks})', tuple(values.values())).rowcount

    def begin_request(self, provider, dataset, target, current, budget):
        date = current.astimezone(KST).date().isoformat()
        with self.db:
            count = self.db.execute('SELECT count(*) FROM requests WHERE date_kst=? AND provider=?', (date, provider)).fetchone()[0]
            if count >= budget:
                return None
            cur = self.db.execute('INSERT INTO requests(started_at,date_kst,dow,provider,dataset,target) VALUES (?,?,?,?,?,?)',
                                  (current.isoformat(), date, DAYS[current.weekday()], provider, dataset, target))
            return cur.lastrowid

    def finish(self, request_id, outcome, status=None, rows=None, raw=None, error=None):
        with self.db:
            self.db.execute('UPDATE requests SET outcome=?,http_status=?,row_count=?,raw_path=?,error_code=? WHERE id=?',
                            (outcome, status, rows, raw, error, request_id))

    def raw(self, dataset, current, payload):
        folder = self.data / 'raw' / f'date={current:%Y-%m-%d}' / f'dow={DAYS[current.weekday()]}' / dataset
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{current:%H%M%S_%f}_{uuid.uuid4().hex[:8]}.json.gz'
        with gzip.open(path, 'wt', encoding='utf-8') as f:
            f.write(dumps(payload))
        return str(path.relative_to(self.root))

    def gap(self, dataset, target, end, reason, start=None):
        with self.db:
            self.db.execute('INSERT INTO gaps(dataset,target,start_at,end_at,reason) VALUES (?,?,?,?,?)',
                            (dataset, target, start, end.isoformat(), reason))

    def export(self, date, include_positions=True):
        from datetime import date as Date
        dow = DAYS[Date.fromisoformat(date).weekday()]
        folder = self.data / 'exports' / f'dow={dow}' / f'date={date}'
        folder.mkdir(parents=True, exist_ok=True)
        tables = ('positions', 'arrivals', 'delays', 'arrival_samples', 'notices', 'timetable', 'requests')
        for table in tables if include_positions else tables[1:]:
            column = 'date_kst' if table in ('positions', 'arrivals', 'delays', 'arrival_samples', 'requests') else 'fetched_date'
            cursor = self.db.execute(f'SELECT * FROM {table} WHERE {column}=?', (date,))
            path = folder / f'{table}.csv'
            temp = path.with_suffix('.tmp')
            with temp.open('w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([d[0] for d in cursor.description])
                writer.writerows(cursor)
            temp.replace(path)
        return folder
