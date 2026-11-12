"""Arrival observation intervals, then conservative exact-identity timetable matching."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import json
import re

from .config import DAYS, KST, LINE_IDS, STATION_ALIASES, source_time
from .storage import dumps, identity


def save_positions(store, config, rows, current, line):
    inserted = 0
    with store.db:
        for row in rows:
            observed = source_time(row.get('recptnDt'))
            age = (current - observed).total_seconds() if observed else None
            quality = 'fresh' if age is not None and -5 <= age <= config.values['max_observation_gap_seconds'] else 'stale_or_missing_time'
            train = str(row['trainNo'])
            direction = str(row.get('updnLine', ''))
            event_id = identity([current.date().isoformat(), line, row])
            values = dict(event_id=event_id, date_kst=f'{current:%Y-%m-%d}', dow=DAYS[current.weekday()],
                          session=config.session(current), collected_at=current.isoformat(),
                          observed_at=observed.isoformat() if observed else None, line=line, train_no=train,
                          station_id=str(row['statnId']), station_name=row.get('statnNm'), direction=direction,
                          train_status=str(row.get('trainSttus', '')), quality=quality, payload_json=dumps(row))
            added = store.insert('positions', values)
            inserted += added
            if not added or quality != 'fresh':
                continue
            state_key = 'trajectory:' + identity([f'{current:%Y-%m-%d}', line, train, direction])
            previous = store.get(state_key)
            prev_time = source_time(previous['observed_at']) if previous else None
            if prev_time and observed <= prev_time:
                continue
            # State 1 means arrived. Repeated state 1 is one observation, not a new arrival.
            is_new_arrival = values['train_status'] == '1' and not (
                previous and previous['station_id'] == values['station_id'] and previous['train_status'] in {'1', '2'})
            if is_new_arrival:
                gap = (observed - prev_time).total_seconds() if prev_time else None
                bounded = gap is not None and 0 < gap <= config.values['max_observation_gap_seconds']
                arrival = dict(event_id=identity(['arrival', event_id]), date_kst=values['date_kst'],
                               dow=values['dow'], line=line, train_no=train, station_id=values['station_id'],
                               station_name=values['station_name'], direction=direction, observed_at=observed.isoformat(),
                               lower_bound_at=prev_time.isoformat() if bounded else None,
                               uncertainty_seconds=gap if bounded else None,
                               quality='bounded_observation' if bounded else 'left_censored', position_event_id=event_id)
                store.insert('arrivals', arrival)
            # Use same transaction as rows, so a crash cannot advance the trajectory alone.
            store.db.execute('INSERT INTO checkpoints VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                             (state_key, dumps(values)))
    return inserted


def save_arrival_samples(store, config, rows, current):
    """도착정보(일괄)는 위치 기반 도착 시각을 검증하는 샘플이다. 지연 정답으로 쓰지 않는다."""
    names = {code: name for name, code in LINE_IDS.items() if name in config.values['lines']}
    inserted = 0
    with store.db:
        for row in rows:
            line = names.get(str(row.get('subwayId')))
            if not line:
                continue  # 9호선·코레일 등 서비스 범위 밖 노선은 원문(raw)에만 남긴다
            observed = source_time(row.get('recptnDt'))
            inserted += store.insert('arrival_samples', dict(
                event_id=identity(['arrival_sample', f'{current:%Y-%m-%d}', row]),
                date_kst=f'{current:%Y-%m-%d}', dow=DAYS[current.weekday()], session=config.session(current),
                collected_at=current.isoformat(), observed_at=observed.isoformat() if observed else None,
                line=line, train_no=str(row.get('btrainNo') or ''), station_id=str(row.get('statnId')),
                station_name=row.get('statnNm'), direction=str(row.get('updnLine', '')),
                arrival_code=str(row.get('arvlCd', '')), payload_json=dumps(row)))
    return inserted


def schedule_at(date, value):
    if not value:
        return None
    clean = str(value).replace(':', '')
    if not re.fullmatch(r'\d{6}', clean):
        return None
    h, m, s = int(clean[:2]), int(clean[2:4]), int(clean[4:])
    if h > 29 or m > 59 or s > 59:
        return None
    return datetime.fromisoformat(date).replace(tzinfo=KST) + timedelta(hours=h, minutes=m, seconds=s)


def strip_note(name):
    """'천호(풍납토성)' → '천호'. 역 이름 뒤의 괄호 부기만 뗀다."""
    return re.sub(r'\s*\(.*?\)\s*$', '', name or '').strip()


def station_resolver(store, date):
    """위치 API 역 이름을 그 날 시간표의 역 이름으로 바꾸는 함수를 만든다.

    후보(원래 이름 → 확인된 별칭 → 괄호 부기 제거)를 차례로 보고, 그 날 그 노선 시간표에
    실제로 있는 이름만 받아들인다. 시간표 쪽 이름에 괄호 부기가 붙어 있어도 찾는다.
    끝내 없으면 원래 이름을 그대로 돌려줘서 unmatched_timetable로 남게 한다.
    """
    names = {}
    for line, name in store.db.execute('SELECT DISTINCT line, station_name FROM timetable WHERE fetched_date=?', (date,)):
        if name:
            names.setdefault(line, {})[name] = name
            names[line].setdefault(strip_note(name), name)
    cache = {}

    def resolve(line, name):
        key = (line, name)
        if key not in cache:
            known = names.get(line, {})
            alias = STATION_ALIASES.get(key) or STATION_ALIASES.get(name) or STATION_ALIASES.get(strip_note(name))
            found = None
            for candidate in (name, alias, strip_note(name), strip_note(alias) if alias else None):
                if candidate and candidate in known:
                    found = known[candidate]
                    break
            cache[key] = found or name
        return cache[key]
    return resolve


INFERRED_MAX_SECONDS = 1200


def number_variants(train, line):
    """위치 API 열차번호와 같은 열차일 수 있는 시간표 표기 → 방법 이름. 2026-11-11 실데이터에서 본 것만 둔다.

    - 코레일 운영 열차는 시간표에서 'K'가 붙는다: 3호선 '3257K', 4호선 'K4626', 1호선 '0473' → 'K473'.
    - 2호선은 위치 번호의 앞자리만 다르다: '3308' → '2308', '8359' → '2359' (뒤 세 자리와 종착역이 같음).
    번호를 바꾸는 규칙이므로 혼자서는 쓰지 않고, 종착역이 같고 지연이 20분 이내일 때만 인정한다(match_delays).
    """
    out = {train + 'K': 'korail_k_number', 'K' + train: 'korail_k_number'}
    if train.startswith('0'):
        out['K' + train.lstrip('0')] = 'korail_k_number'
    if line == '2호선' and len(train) == 4 and train.isdigit() and train[0] != '2':
        out['2' + train[1:]] = 'line2_first_digit'
    return out


def canonical_station(line, name):
    """종착역 비교용 이름: 확인된 별칭을 적용하고 괄호 부기를 뗀다. 시간표 역 목록과 무관하게 비교한다."""
    alias = STATION_ALIASES.get((line, name)) or STATION_ALIASES.get(name) or STATION_ALIASES.get(strip_note(name))
    return strip_note(alias or name)


def same_station(line, a, b):
    return bool(a) and bool(b) and canonical_station(line, a) == canonical_station(line, b)


def match_delays(store, date):
    """도착 관측을 그 날 시간표의 같은 열차·역·방향과 짝짓는다.

    1) 정확한 열차번호. 종착역을 알면 종착역도 같아야 한다(다르면 destination_mismatch).
    2) 1)이 없을 때만, 같은 열차일 수 있는 다른 번호를 본다: 코레일 'K' 표기(number_variants),
       그리고 종점 회차 뒤 새로 붙는 연계 열차번호(시간표 lnkgTrainno). 이때는 종착역이 반드시 같아야 한다.
    시각이 가깝다는 이유만으로는 짝짓지 않는다. 출퇴근 배차가 2~3분이라 뒤차와 짝지어지면 지연이 틀어진다.
    어떤 방법으로 짝지었는지 match_method에 남겨, 분석에서 따로 보거나 뺄 수 있게 한다.
    """
    resolve = station_resolver(store, date)
    stops = defaultdict(list)       # (line, train, station, direction) -> [(row, scheduled, destination)]
    trains = defaultdict(set)       # line -> 시간표 열차번호
    linked = defaultdict(set)       # (line, train) -> 연계 열차번호
    for row in store.db.execute('SELECT * FROM timetable WHERE fetched_date=?', (date,)):
        payload = json.loads(row['payload_json'] or '{}')
        trains[row['line']].add(row['train_no'])
        if payload.get('lnkgTrainno'):
            linked[(row['line'], row['train_no'])].add(str(payload['lnkgTrainno']))
        stops[(row['line'], row['train_no'], row['station_name'], row['direction'])].append(
            (row, schedule_at(date, row['arrival_time']), payload.get('arvlStnNm')))
    arrivals = store.db.execute('SELECT a.*, p.payload_json AS position_payload FROM arrivals a '
                                'LEFT JOIN positions p ON p.event_id = a.position_event_id WHERE a.date_kst=?', (date,)).fetchall()
    with store.db:
        for arrival in arrivals:
            line, train, direction = arrival['line'], arrival['train_no'], arrival['direction']
            station = resolve(line, arrival['station_name'])
            observed = source_time(arrival['observed_at'])
            terminal = json.loads(arrival['position_payload'] or '{}').get('statnTnm')
            destination = terminal or None

            def near(number, need_destination, limit=3600):
                found = []
                for row, scheduled, dest in stops.get((line, number, station, direction), []):
                    if scheduled is None or abs((observed - scheduled).total_seconds()) > limit:
                        continue
                    if destination and dest and not same_station(line, destination, dest):
                        continue
                    if need_destination and not (destination and dest):
                        continue
                    found.append((row, scheduled))
                return found

            status, method, candidates = None, None, []
            if arrival['quality'] != 'bounded_observation':
                status = 'unbounded_arrival'
            else:
                exact_rows = [(r, s) for r, s, _ in stops.get((line, train, station, direction), [])
                              if s is not None and abs((observed - s).total_seconds()) <= 3600]
                candidates, method = near(train, False), 'exact'
                if not candidates:
                    variants = {v: m for v, m in number_variants(train, line).items() if v in trains[line]}
                    family = [train] + list(variants)
                    others = list(variants) + sorted({n for v in family for n in linked.get((line, v), ())} - set(family))
                    far = False
                    for number in others:
                        # 번호를 바꿔 찾은 짝은 같은 번호·종착역의 다른 회차일 수 있다(예: 1호선 급행은 매시간).
                        # 20분 안이면 인정하고, 그보다 멀면 지연을 확정하지 않고 표시만 한다.
                        hits = near(number, True, INFERRED_MAX_SECONDS)
                        far = far or bool(near(number, True))
                        if hits:
                            candidates += hits
                            method = variants.get(number, 'linked_trainno')
                    if not candidates and far:
                        status = 'unverified_large_delay'
                    if not candidates and exact_rows and status is None:
                        # 같은 번호가 이 역·방향·시각에 있지만 종착역이 다르다: 다른 회차와 잘못 짝지어질 뻔한 경우
                        status = 'destination_mismatch'
                if status is None:
                    if not candidates:
                        # 방향만 다르고 나머지가 맞는 시간표가 있으면 방향 코드 대응이 틀렸다는 신호다. 지연은 확정하지 않는다.
                        other = store.db.execute('SELECT 1 FROM timetable WHERE fetched_date=? AND line=? AND train_no=? AND station_name=? AND direction IS NOT ?',
                                                 (date, line, train, station, direction)).fetchone()
                        status = 'direction_mismatch' if other else 'unmatched_timetable'
                    elif len(candidates) != 1:
                        status = 'ambiguous_timetable'
                    else:
                        status = 'matched_observation_interval'
            matched, scheduled = candidates[0] if status == 'matched_observation_interval' else (None, None)
            values = dict(arrival_id=arrival['event_id'], date_kst=date, dow=arrival['dow'],
                          line=arrival['line'], train_no=arrival['train_no'], station_name=station,
                          observed_at=arrival['observed_at'], scheduled_at=scheduled.isoformat() if scheduled else None,
                          delay_seconds=(observed-scheduled).total_seconds() if scheduled else None,
                          lower_delay_seconds=(source_time(arrival['lower_bound_at'])-scheduled).total_seconds() if scheduled else None,
                          match_status=status, timetable_event_id=matched['event_id'] if matched else None,
                          match_method=method if matched else None)
            columns = ','.join(values)
            marks = ','.join('?' for _ in values)
            store.db.execute(f'INSERT OR REPLACE INTO delays ({columns}) VALUES ({marks})', tuple(values.values()))
