"""KRIC snapshots. Direction is absent in the API: never invent matching labels."""
from urllib.parse import urlencode
from datetime import timedelta

from .config import load_keys, now
from .http import Client, SourceError
from .storage import dumps, identity

BASE = 'https://data.kric.go.kr/openapi/trainUseInfo/'
# Codes confirmed against subwayRouteInfo, not Seoul realtime station IDs.
ROUTES = {'9호선': '9', '수인분당선': 'K1', '경춘선': 'K2',
          '경의중앙선': 'K4', '공항철도': 'A1', '신분당선': 'D1', '우이신설선': 'UI'}


def response(doc):
    header = doc['header']
    code = str(header['resultCode'])
    if code == '03':
        return [], 0
    if code != '00':
        raise SourceError('kric_' + code)
    rows = doc.get('body', [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Invalid KRIC body')
    total = int(header['resultCnt'])
    if len(rows) != total:
        raise ValueError('Incomplete KRIC response')
    return rows, total


def step(config, store, clock=now):
    from .cli import lock_file
    with lock_file(store.data, 'kric-request.lock'):
        return _step(config, store, clock)


def _step(config, store, clock=now):
    keys = load_keys(config.root)
    if not keys.get('KRIC_API_KEY'):
        return False
    current = clock()
    retry = store.get('kric:retry_after')
    if retry and current.isoformat() < retry:
        return False
    date = current.date().isoformat()
    client = Client(config, store, lambda _: True, clock=clock)
    client.keys = keys
    config.values['daily_budgets'].setdefault('kric', 2000)

    def fetch(endpoint, params, target):
        url = BASE + endpoint + '?' + urlencode(dict(serviceKey=keys['KRIC_API_KEY'], format='json', **params))
        result = client.fetch('kric', 'kric_' + endpoint, target, url, response, attempts=1)
        if result is None:
            store.put('kric:retry_after', (current + timedelta(minutes=10)).isoformat())
        return result

    for line, code in ROUTES.items():
        if line not in config.values['lines']:
            continue
        route_key = 'kric:route:' + code
        route = store.get(route_key)
        if not route or route['date'] != date:
            result = fetch('subwayRouteInfo', dict(mreaWideCd='01', lnCd=code), line)
            if result is not None and result[0]:
                store.put(route_key, dict(date=date, rows=result[0]))
            return True
        for station in route['rows']:
            params = {key: station[key] for key in ('railOprIsttCd', 'lnCd', 'stinCd')}
            for day in ('8', '7', '9'):
                for endpoint in ('subwayTimetable', 'subwayTimetableExp'):
                    task = 'kric:done:' + identity([date, params, day, endpoint])
                    if store.get(task):
                        continue
                    result = fetch(endpoint, dict(**params, dayCd=day),
                                   ':'.join([line, params['stinCd'], day, endpoint]))
                    if result is None:
                        return True
                    rows, _ = result
                    with store.db:
                        for row in rows:
                            store.insert('kric_timetable', dict(event_id=identity([date, endpoint, params, day, row]),
                                fetched_date=date, line=line, operator_code=params['railOprIsttCd'],
                                line_code=params['lnCd'], station_code=params['stinCd'],
                                station_name=station['stinNm'], station_order=station.get('stinConsOrdr'),
                                day_code=day, train_no=str(row['trnNo']), arrival_time=row.get('arvTm'),
                                departure_time=row.get('dptTm'), express_code=row.get('exptCd'),
                                endpoint=endpoint, payload_json=dumps(row)))
                        store.db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?)', (task, dumps(True)))
                    return True
    return False
