from __future__ import annotations

from datetime import timedelta
import logging
import time

from .config import DAYS, DIRECTION_CODES, load_keys, now, source_time
from .derive import save_arrival_samples, save_positions, match_delays
from .http import Client
from .providers import (arrival_response, arrival_url, position_response, position_url, public_response,
                        public_url, seoul_open_response, seoul_open_url)
from .storage import dumps, identity

LOG = logging.getLogger('subway')


class Runner:
    def __init__(self, config, store, clock=now):
        self.config, self.store, self.clock = config, store, clock
        self.client = Client(config, store, lambda t: config.active(t) and not (store.data / 'stop.request').exists(), clock=clock)
        self.retry_at = {}

    def refresh_keys(self):
        self.keys = load_keys(self.config.root)
        self.client.keys = self.keys

    def positions(self, lines=None):
        """주어진 노선만 호출한다. CLI가 노선별로 시차를 두고 부른다(기본: 전체)."""
        self.refresh_keys()
        key = self.keys['SEOUL_SUBWAY_API_KEY']
        if not key:
            return
        for line in (lines or self.config.values['lines']):
            start = 1
            while self.config.active(self.clock()):
                result = self.client.fetch('seoul_subway', 'positions', f'{line}:{start}',
                                           position_url(self.config, key, line, start, start+999), position_response)
                if result is None:
                    break
                rows, total = result
                current = self.clock()
                save_positions(self.store, self.config, rows, current, line)
                last_key = 'last_position:' + line
                last = self.store.get(last_key)
                previous = source_time(last) if last else None
                if start == 1 and (not previous or (current-previous).total_seconds() > self.config.values['max_observation_gap_seconds']):
                    if not previous:
                        from .config import minutes
                        candidates = [current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=minutes(a))
                                      for a, _ in self.config.values['windows']]
                        last = max((t for t in candidates if t <= current), default=current).isoformat()
                    self.store.gap('positions', line, current, 'collection_started' if not previous else 'observation_gap_including_off_hours', last)
                if rows:
                    self.store.put(last_key, current.isoformat())
                LOG.info('positions line=%s rows=%d total=%d', line, len(rows), total)
                start += 1000
                if start > total:
                    break
                if not rows:
                    self.store.gap('positions', line, current, 'incomplete_pagination')
                    break

    def available(self, task):
        return time.monotonic() >= self.retry_at.get(task, 0)

    def retry_later(self, task):
        self.retry_at[task] = time.monotonic() + 120

    def auxiliary_step(self):
        """One supplemental request per tick, preserving position polling priority."""
        self.refresh_keys()
        if not self.config.active(self.clock()):
            return
        if self.keys['SEOUL_SUBWAY_API_KEY'] and self.config.values.get('arrival_sample_seconds') and self.arrival_step():
            return
        if self.keys['SEOUL_OPEN_API_KEY'] and self.config.values.get('boarding_volume_months') and self.boarding_volume_step():
            return
        if self.keys['DATA_GO_KR_API_KEY']:
            if self.config.values['alerts'] and self.alert_step():
                return
            if self.config.values['timetable'] and self.timetable_step():
                return

    def arrival_step(self):
        current = self.clock()
        task = 'arrival_sample:last_success'
        last = source_time(self.store.get(task))
        if last and (current-last).total_seconds() < self.config.values['arrival_sample_seconds']:
            return False
        if not self.available(task):
            return False
        result = self.client.fetch('seoul_subway', 'arrival_samples', 'ALL',
                                   arrival_url(self.config, self.keys['SEOUL_SUBWAY_API_KEY']), arrival_response, attempts=1)
        if result is None:
            self.retry_later(task)
            return True
        rows, total = result
        saved = save_arrival_samples(self.store, self.config, rows, self.clock())
        if total > len(rows):
            # 일괄 응답이 잘려 오면 기록만 남긴다. 페이지 방식은 1주차 프로브 결과를 보고 정한다.
            self.store.gap('arrival_samples', 'ALL', current, f'truncated_{len(rows)}_of_{total}')
        self.store.put(task, current.isoformat())
        LOG.info('arrival_samples rows=%d saved=%d total=%d', len(rows), saved, total)
        return True

    def boarding_months(self, current):
        """매월 5일에 전월분이 올라오므로, 5일 전에는 전전월까지만 완성된 달로 본다."""
        year, month = current.year, current.month - (1 if current.day >= 5 else 2)
        months = []
        for _ in range(self.config.values['boarding_volume_months']):
            while month < 1:
                year, month = year - 1, month + 12
            months.append(f'{year:04d}{month:02d}')
            month -= 1
        return months

    def boarding_volume_step(self):
        """역별 시간대별 승하차 인원(월 통계). 달마다 한 번 끝까지 받고 체크포인트로 다시 받지 않는다."""
        current = self.clock()
        service = 'CardSubwayTime'
        for use_month in self.boarding_months(current):
            task = f'boarding:{use_month}'
            empty_today = f'boarding_empty:{use_month}:{current:%Y-%m-%d}'
            state = self.store.get(task) or {'start': 1}
            if state.get('done') or self.store.get(empty_today) or not self.available(task):
                continue
            start = state['start']
            result = self.client.fetch('seoul_open', 'boarding_volume', f'{use_month}:{start}',
                seoul_open_url(self.keys['SEOUL_OPEN_API_KEY'], service, start, start + 999, use_month),
                seoul_open_response(service), attempts=1)
            if result is None:
                self.retry_later(task)
                return True
            rows, total = result
            if total == 0:
                # 아직 공개 전인 달: 완료로 표시하지 않고 내일 다시 확인한다.
                self.store.put(empty_today, True)
                return True
            with self.store.db:
                for row in rows:
                    self.store.insert('boarding_volume', dict(event_id=identity(['boarding', use_month, row]),
                        use_month=use_month, fetched_date=f'{current:%Y-%m-%d}', payload_json=dumps(row)))
            if start + 999 >= total:
                state = {'done': True, 'total': total}
            elif not rows:
                self.retry_later(task)
                self.store.gap('boarding_volume', use_month, current, 'incomplete_pagination')
                return True
            else:
                state['start'] = start + 1000
            self.store.put(task, state)
            return True
        return False

    def alert_step(self):
        current = self.clock()
        task = f'notices:{current:%Y-%m-%d}'
        state = self.store.get(task) or {'page': 1, 'last_success': None}
        last = source_time(state['last_success'])
        if last and (current-last).total_seconds() < self.config.values['alert_interval_seconds']:
            return False
        if not self.available(task):
            return False
        params = dict(pageNo=state['page'], numOfRows=1000,
                      srchStartNoftOcrnYmd=f'{current-timedelta(days=1):%Y%m%d}', srchEndNoftOcrnYmd=f'{current:%Y%m%d}')
        result = self.client.fetch('data_go_kr', 'notices', str(state['page']),
                                   public_url('ntce', 'getNtceList', self.keys['DATA_GO_KR_API_KEY'], params), public_response, attempts=1)
        if result is None:
            self.retry_later(task)
            return True
        rows, total = result
        with self.store.db:
            for row in rows:
                self.store.insert('notices', dict(event_id=identity([f'{current:%Y-%m-%d}', row]),
                    fetched_date=f'{current:%Y-%m-%d}', dow=DAYS[current.weekday()], collected_at=current.isoformat(),
                    occurred_at=row.get('noftOcrnDt'), lines=row.get('lineNmLst'), stations=row.get('stnSctnCdLst'),
                    title=row.get('noftTtl'), content=row.get('noftCn'), payload_json=dumps(row)))
        if state['page'] * 1000 < total:
            if not rows:
                self.retry_later(task)
                self.store.gap('notices', task, current, 'incomplete_pagination')
                return True
            state['page'] += 1
        else:
            state = {'page': 1, 'last_success': current.isoformat()}
        self.store.put(task, state)
        return True

    def day_type(self, current):
        overrides = self.config.values.get('day_type_overrides', {})
        return overrides.get(f'{current:%Y-%m-%d}', '공휴일' if current.weekday() == 6 else '주말' if current.weekday() == 5 else '평일')

    def timetable_step(self):
        current = self.clock()
        day_type = self.day_type(current)
        for line in self.config.values['lines']:
            directions = ('내선', '외선') if line == '2호선' else ('상행', '하행')
            for direction in directions:
                for dtype in (day_type, '상시'):
                    for temporary in ('N', 'Y'):
                        task = 'timetable:' + identity([f'{current:%Y-%m-%d}', line, direction, dtype, temporary])
                        state = self.store.get(task) or {'page': 1}
                        if state.get('done') or not self.available(task):
                            continue
                        params = dict(pageNo=state['page'], numOfRows=1000, tmprTmtblYn=temporary,
                                      upbdnbSe=direction, wkndSe=dtype, lineNm=line,
                                      searchDt=current.strftime('%Y-%m-%dT%H:%M:%S'))
                        result = self.client.fetch('data_go_kr', 'timetable', f'{line}:{direction}:{dtype}:{temporary}:{state["page"]}',
                            public_url('schedule', 'getTrainSch', self.keys['DATA_GO_KR_API_KEY'], params), public_response, attempts=1)
                        if result is None:
                            self.retry_later(task)
                            return True
                        rows, total = result
                        with self.store.db:
                            for row in rows:
                                self.store.insert('timetable', dict(event_id=identity([f'{current:%Y-%m-%d}', row]),
                                    fetched_date=f'{current:%Y-%m-%d}', line=row.get('lineNm', line), train_no=str(row.get('trainno', '')),
                                    station_id=row.get('stnCd'), station_name=row.get('stnNm'),
                                    direction=DIRECTION_CODES.get(row.get('upbdnbSe', direction), row.get('upbdnbSe')),
                                    day_type=row.get('wkndSe', dtype), arrival_time=row.get('trainArvlTm'),
                                    departure_time=row.get('trainDptreTm'), payload_json=dumps(row)))
                        if state['page'] * 1000 >= total:
                            state['done'] = True
                        elif not rows:
                            self.retry_later(task)
                            self.store.gap('timetable', task, current, 'incomplete_pagination')
                            return True
                        else:
                            state['page'] += 1
                        self.store.put(task, state)
                        return True
        return False
