"""코레일 XLSX 고정 시간표 적재와 지연 짝짓기. 합성 XLSX만 쓰고 실제 data/에는 쓰지 않는다."""
from datetime import timedelta
from pathlib import Path
from xml.sax.saxutils import escape
import zipfile

from src.subway_collect.derive import match_delays, save_positions
from src.subway_collect.korail import load_korail_timetable, station_name
from src.subway_collect.storage import dumps
from tests.test_collection import Fixture, TUESDAY


def column(n):
    letters = ''
    while n:
        n, r = divmod(n - 1, 26)
        letters = chr(65 + r) + letters
    return letters


def make_xlsx(path, sheets):
    """sheets = {시트 이름: [[A열 값, B열 값, ...], ...]}. 숫자는 float, 글자는 인라인 문자열, None은 빈칸."""
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('xl/workbook.xml',
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + ''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(sheets, 1))
                   + '</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels',
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + ''.join(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
                   + '</Relationships>')
        for i, rows in enumerate(sheets.values(), 1):
            body = []
            for r, row in enumerate(rows, 1):
                cells = []
                for c, value in enumerate(row, 1):
                    ref = f'{column(c)}{r}'
                    if value is None:
                        continue
                    if isinstance(value, (int, float)):
                        cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                    else:
                        cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
                body.append(f'<row r="{r}">{"".join(cells)}</row>')
            z.writestr(f'xl/worksheets/sheet{i}.xml',
                       '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                       + ''.join(body) + '</sheetData></worksheet>')


def t(h, m, s=0):
    return (h * 3600 + m * 60 + s) / 86400


# 하행 2개 열차. 역 이름 행 = 도착, 바로 아래 행 = 출발. '수색'은 K5003이 통과(공백).
DOWN = [
    ['개정안'],
    ['시발역', '문산  ', '문산  '],
    ['종착역', '용문  ', '용문  '],
    ['열차번호', 'K5001', 'K5003'],
    ['문산  ', '        ', '        '],
    [None, t(17, 50), t(23, 50)],
    ['수색  ', t(18, 0, 30), '        '],
    [None, t(18, 1), '        '],
    ['홍대입', t(18, 5), t(23, 59)],
    [None, t(18, 5, 30), t(0, 0, 30)],
    ['용문  ', t(19, 30), t(1, 10)],
]


class KorailTimetableTests(Fixture):
    def test_incremental_worker_rechecks_after_static_import(self):
        from src.subway_collect.maintenance import IncrementalDelays
        self.assertEqual(self.observe()[0], 'unmatched_timetable')
        worker = IncrementalDelays(self.store, '2026-11-10', self.config)
        worker.step(0)
        self.load({'경의중앙선 하행': DOWN}, day_type='평일')
        worker.step(31)
        row = self.store.db.execute('SELECT match_status, delay_seconds FROM delays').fetchone()
        self.assertEqual(tuple(row), ('matched_observation_interval', 0.0))
        newer = [list(r) for r in DOWN]
        newer[6][1] = t(18, 0)
        self.load({'경의중앙선 하행': newer}, valid_from='2026-10-01', day_type='평일')
        worker.step(62)
        self.assertEqual(self.store.db.execute('SELECT delay_seconds FROM delays').fetchone()[0], 30.0)

    def test_explicit_holiday_override_without_api_timetable(self):
        from src.subway_collect.maintenance import IncrementalDelays
        self.load({'경의중앙선 하행': DOWN}, day_type='휴일')
        self.assertEqual(self.observe()[0], 'unmatched_timetable')
        self.config.values['day_type_overrides']['2026-11-10'] = '공휴일'
        worker = IncrementalDelays(self.store, '2026-11-10', self.config)
        worker.step(0)
        self.assertEqual(self.store.db.execute('SELECT match_status FROM delays').fetchone()[0], 'matched_observation_interval')

    def test_identical_workbook_can_have_new_effective_date(self):
        self.load({'경의중앙선 하행': DOWN}, valid_from='2026-03-01', day_type='평일')
        self.load({'경의중앙선 하행': DOWN}, valid_from='2026-09-01', day_type='평일')
        versions = self.store.db.execute('SELECT count(DISTINCT valid_from) FROM static_timetable').fetchone()[0]
        self.assertEqual(versions, 2)
        self.assertEqual(self.observe(), ('matched_observation_interval', 'korail_k_number', 0.0))

    def test_invalid_calendar_date_rejected_before_writes(self):
        with self.assertRaises(ValueError):
            self.load({'경의중앙선 하행': DOWN}, valid_from='2026-02-30', day_type='평일')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM static_timetable').fetchone()[0], 0)

    def load(self, sheets, valid_from='2026-09-01', **kwargs):
        path = self.root / 'korail.xlsx'
        make_xlsx(path, sheets)
        return load_korail_timetable(self.store, path, valid_from, **kwargs)

    def stops(self, train):
        return [tuple(r) for r in self.store.db.execute(
            "SELECT station_name, arrival_time, departure_time FROM static_timetable WHERE train_no=? "
            "ORDER BY json_extract(payload_json,'$.order')", (train,))]

    def test_arrival_departure_rows_and_station_names(self):
        result = self.load({'경의중앙선 하행': DOWN}, day_type='평일')
        self.assertEqual(result['sheets'][0]['status'], 'loaded')
        self.assertEqual(self.stops('K5001'), [('문산', None, '17:50:00'), ('수색', '18:00:30', '18:01:00'),
                                               ('홍대입구', '18:05:00', '18:05:30'), ('용문', '19:30:00', '19:30:00')])
        row = self.store.db.execute("SELECT line, direction, day_type, valid_from FROM static_timetable LIMIT 1").fetchone()
        self.assertEqual(tuple(row), ('경의중앙선', '1', '평일', '2026-09-01'))

    def test_passing_station_is_not_a_stop_and_midnight_rolls_over(self):
        self.load({'경의중앙선 하행': DOWN}, day_type='평일')
        self.assertEqual(self.stops('K5003'), [('문산', None, '23:50:00'), ('홍대입구', '23:59:00', '24:00:30'),
                                               ('용문', '25:10:00', '25:10:00')])

    def test_itx_sheet_skipped_and_reload_adds_nothing(self):
        sheets = {'ITX청춘 하행': DOWN, '경의중앙선 하행': DOWN}
        first = self.load(sheets, day_type='평일')
        self.assertEqual(first['sheets'][0]['status'], 'skipped_not_commuter_train')
        again = self.load(sheets, day_type='평일')
        self.assertEqual(sum(s.get('inserted', 0) for s in again['sheets']), 0)

    def test_sheet_without_line_or_day_needs_arguments(self):
        result = self.load({'평일하행': DOWN})
        self.assertTrue(result['sheets'][0]['status'].startswith('skipped'))
        result = self.load({'평일하행': DOWN}, line='수인분당선')
        self.assertEqual((result['sheets'][0]['line'], result['sheets'][0]['day_type']), ('수인분당선', '평일'))
        with self.assertRaises(ValueError):
            self.load({'평일하행': DOWN}, valid_from='9월1일')

    def test_station_name_map_is_line_specific(self):
        self.assertEqual(station_name('경강선', '경광주 '), '경기광주')
        self.assertEqual(station_name('수인분당선', '신수원'), '수원')
        self.assertEqual(station_name('경의중앙선', '신수원'), '신수원')

    def observe(self, train='5001', station='수색', terminal='용문', line='경의중앙선', start=TUESDAY):
        for sec, status in ((0, '0'), (30, '1')):
            row = dict(trainNo=train, statnId='x', statnNm=station, updnLine='1', trainSttus=status, statnTnm=terminal,
                       recptnDt=(start + timedelta(seconds=sec)).strftime('%Y-%m-%d %H:%M:%S'))
            save_positions(self.store, self.config, [row], start + timedelta(seconds=sec + 1), line)
        match_delays(self.store, f'{start:%Y-%m-%d}')
        return tuple(self.store.db.execute(
            'SELECT match_status, match_method, delay_seconds FROM delays WHERE line=?', (line,)).fetchone())

    def test_realtime_number_without_k_matches_static_timetable(self):
        self.load({'경의중앙선 하행': DOWN}, day_type='평일')
        # 관측 18:00:30 도착, 시간표 18:00:30 → 지연 0초. 위치 API 번호 5001 = 시간표 K5001.
        self.assertEqual(self.observe(), ('matched_observation_interval', 'korail_k_number', 0.0))

    def test_holiday_table_not_used_on_weekday(self):
        self.load({'경의중앙선 하행': DOWN}, day_type='휴일')
        self.assertEqual(self.observe()[0], 'unmatched_timetable')

    def test_future_timetable_version_not_used(self):
        self.load({'경의중앙선 하행': DOWN}, valid_from='2026-12-01', day_type='평일')
        self.assertEqual(self.observe()[0], 'unmatched_timetable')

    def test_latest_valid_version_wins(self):
        self.load({'경의중앙선 하행': DOWN}, valid_from='2026-03-01', day_type='평일')
        newer = [list(r) for r in DOWN]
        newer[6][1] = t(18, 0, 0)   # 수색 도착 18:00:00 로 개정
        self.load({'경의중앙선 하행': newer}, valid_from='2026-09-01', day_type='평일')
        self.assertEqual(self.observe(), ('matched_observation_interval', 'korail_k_number', 30.0))

    def test_api_timetable_line_ignores_static_rows(self):
        self.load({'경의중앙선 하행': DOWN}, day_type='평일')
        with self.store.db:
            self.store.insert('timetable', dict(event_id='api-1', fetched_date='2026-11-10', line='경의중앙선', train_no='5001',
                station_name='수색', direction='1', day_type='평일', arrival_time='17:59:00', payload_json=dumps({'arvlStnNm': '용문'})))
        self.assertEqual(self.observe(), ('matched_observation_interval', 'exact', 90.0))
