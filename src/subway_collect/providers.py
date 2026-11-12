"""Parsers for the official source contracts (Seoul Open Data Plaza, data.go.kr)."""
from __future__ import annotations

from urllib.parse import quote, unquote, urlencode

from .http import SourceError

PUBLIC_BASE = 'https://apis.data.go.kr/B553766'
SEOUL_OPEN_BASE = 'http://openapi.seoul.go.kr:8088'


def as_list(value):
    if value is None or value == '':
        return []
    return value if isinstance(value, list) else [value]


def position_response(doc):
    error = doc.get('errorMessage') or doc.get('RESULT') or doc
    code = str(error.get('code', error.get('CODE', '')))
    if code and code not in {'INFO-000', 'INFO-200', '0', '00'}:
        raise SourceError(code)
    if code == 'INFO-200':
        return [], 0
    if 'realtimePositionList' not in doc:
        raise ValueError('missing realtimePositionList')
    rows = as_list(doc['realtimePositionList'])
    total = int(error.get('total', len(rows)))
    for row in rows:
        if not row.get('trainNo') or not row.get('statnId'):
            raise ValueError('missing position identity')
    return rows, total


def arrival_response(doc):
    """실시간 도착정보(일괄). 위치 API와 같은 오류 봉투(errorMessage)를 쓴다."""
    error = doc.get('errorMessage') or doc.get('RESULT') or doc
    code = str(error.get('code', error.get('CODE', '')))
    if code and code not in {'INFO-000', 'INFO-200', '0', '00'}:
        raise SourceError(code)
    if code == 'INFO-200':
        return [], 0
    if 'realtimeArrivalList' not in doc:
        raise ValueError('missing realtimeArrivalList')
    rows = as_list(doc['realtimeArrivalList'])
    for row in rows:
        if not row.get('subwayId') or not row.get('statnId'):
            raise ValueError('missing arrival identity')
    return rows, int(error.get('total', len(rows)))


def seoul_open_response(service):
    """열린데이터광장 일반 Open API: {SERVICE: {list_total_count, RESULT, row}}."""
    def parse(doc):
        body = doc.get(service)
        result = (body or doc).get('RESULT', {})
        code = str(result.get('CODE', ''))
        if code == 'INFO-200':
            return [], 0
        if code != 'INFO-000' or body is None:
            raise SourceError(code or 'missing_result_code')
        return as_list(body.get('row')), int(body['list_total_count'])
    return parse


def public_response(doc):
    envelope = doc.get('response', doc)
    header = envelope.get('header', {})
    code = str(header.get('resultCode', ''))
    if code not in {'00', '0', '03'}:
        raise SourceError(code or 'missing_result_code')
    body = envelope.get('body', {})
    if code == '03':
        return [], 0
    if 'totalCount' not in body:
        raise ValueError('missing totalCount')
    items = body.get('items') or {}
    rows = as_list(items.get('item') if isinstance(items, dict) else items)
    return rows, int(body['totalCount'])


def position_url(config, key, line, start=1, end=1000):
    return f"{config.values['seoul_subway_base'].rstrip('/')}/{quote(key, safe='')}/json/realtimePosition/{start}/{end}/{quote(line, safe='')}"


def arrival_url(config, key):
    # 공식 '실시간 도착정보(일괄)' 형식. 1주차 프로브로 응답 형식과 total을 먼저 확인한다.
    return f"{config.values['seoul_subway_base'].rstrip('/')}/{quote(key, safe='')}/json/realtimeStationArrival/ALL"


def seoul_open_url(key, service, start, end, *args):
    path = '/'.join(quote(str(a), safe='') for a in args)
    return f"{SEOUL_OPEN_BASE}/{quote(key, safe='')}/json/{service}/{start}/{end}/{path}"


def public_url(service, operation, key, params):
    return f'{PUBLIC_BASE}/{service}/{operation}?' + urlencode({'serviceKey': unquote(key), 'dataType': 'JSON', **params})
