from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KST = timezone(timedelta(hours=9))
DAYS = ('MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN')
KEY_NAMES = ('SEOUL_SUBWAY_API_KEY', 'SEOUL_OPEN_API_KEY', 'DATA_GO_KR_API_KEY')
# 서울 열린데이터광장 실시간 지하철 API의 subwayId. 서비스 범위인 서울교통공사 1~8호선만 둔다.
LINE_IDS = {f'{n}호선': str(1000 + n) for n in range(1, 9)}
# 위치 API updnLine 코드: 0 = 상행/내선, 1 = 하행/외선 (통용 명세. 2호선 외선 열차가 '1'로 오는 실측 사례와 일치).
# 시간표의 upbdnbSe 문자열을 같은 코드로 바꿔 비교한다. 어긋나면 match_status='direction_mismatch'로 드러난다.
DIRECTION_CODES = {'상행': '0', '내선': '0', '하행': '1', '외선': '1'}
# 위치 API 역 이름 → 시간표 역 이름. 2026-11-11 실데이터(scripts/find_mappings.py)로 확인한 것만 둔다.
# 괄호 부기(예: '천호(풍납토성)')는 규칙으로 처리하므로 여기 없다. 키는 (노선, 이름) 또는 이름만(모든 노선).
# 적용 전에 그 날 시간표에 실제로 있는 이름인지 다시 확인하므로, 맞지 않는 대응은 무시된다.
STATION_ALIASES = {
    '서울': '서울역',
    '춘의역': '춘의',
    '지제': '평택지제',
    ('7호선', '총신대입구(이수)'): '이수',
    '성수종착': '성수',
    '성수지선': '성수',
    '신도림지선': '신도림',
    '뚝섬유원지': '자양',
    '응암순환': '응암',  # 6호선 위치 종착역 '응암순환(상선)' (2026-11-11 check_match_quality)
}


def now():
    return datetime.now(KST)


def load_keys(root=ROOT):
    values = {}
    path = root / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if not line.strip() or line.lstrip().startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('\"\'')
    return {key: os.environ.get(key, values.get(key, '')).strip() for key in KEY_NAMES}


@dataclass
class Config:
    root: Path
    values: dict

    @classmethod
    def load(cls, path=None, root=ROOT):
        path = Path(path) if path else root / 'config' / 'collector.json'
        config = cls(root, json.loads(path.read_text(encoding='utf-8-sig')))
        config.validate()
        return config

    def validate(self):
        v = self.values
        if not v['lines'] or len(set(v['lines'])) != len(v['lines']):
            raise ValueError('lines must contain unique line names')
        if v['position_interval_seconds'] < 10:
            raise ValueError('position_interval_seconds must be >= 10')
        if not v['weekdays'] or any(d not in range(7) for d in v['weekdays']):
            raise ValueError('weekdays must be 0..6 (Monday..Sunday)')
        windows = sorted((minutes(a), minutes(b)) for a, b in v['windows'])
        if not windows or any(a >= b for a, b in windows):
            raise ValueError('windows must be same-day start < end')
        if any(windows[i][1] > windows[i+1][0] for i in range(len(windows)-1)):
            raise ValueError('windows must not overlap')
        if not 1 <= v['max_attempts'] <= 5 or not 1 <= v['request_timeout_seconds'] <= 30:
            raise ValueError('invalid retry/timeout configuration')
        if any(not isinstance(n, int) or n < 1 for n in v['daily_budgets'].values()):
            raise ValueError('budgets must be positive integers')
        if any(line not in LINE_IDS for line in v['lines']):
            raise ValueError('lines must be Seoul Metro 1호선..8호선 (proposal ch.9)')
        if v.get('arrival_sample_seconds', 0) and v['arrival_sample_seconds'] < 60:
            raise ValueError('arrival_sample_seconds must be 0 (off) or >= 60')
        if not 0 <= v.get('boarding_volume_months', 0) <= 12:
            raise ValueError('boarding_volume_months must be 0..12')
        if v.get('boarding_volume_months', 0) and 'seoul_open' not in v['daily_budgets']:
            raise ValueError('daily_budgets.seoul_open is required when boarding_volume_months > 0')
        if v.get('export_interval_seconds', 300) < 60:
            raise ValueError('export_interval_seconds must be >= 60')

    def active(self, current):
        current = current.astimezone(KST)
        m = current.hour * 60 + current.minute + current.second / 60
        return current.weekday() in self.values['weekdays'] and any(
            minutes(a) <= m < minutes(b) for a, b in self.values['windows'])

    def session(self, current):
        return 'morning' if current.astimezone(KST).hour < 12 else 'evening'

    def next_start(self, current):
        current = current.astimezone(KST)
        for offset in range(8):
            day = (current + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
            if day.weekday() not in self.values['weekdays']:
                continue
            for start, _ in sorted(self.values['windows']):
                target = day + timedelta(minutes=minutes(start))
                if target > current:
                    return target
        raise ValueError('no collection day')


def minutes(value):
    h, m = map(int, value.split(':'))
    if not 0 <= h < 24 or not 0 <= m < 60:
        raise ValueError('invalid HH:MM')
    return h * 60 + m


def source_time(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.replace(tzinfo=KST) if result.tzinfo is None else result.astimezone(KST)
    except ValueError:
        return None
