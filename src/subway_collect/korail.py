"""코레일 공식 전동열차 시각표(XLSX)를 고정 시간표(static_timetable)로 적재한다.

공공데이터포털 열차시간표 API는 서울교통공사 1~8호선만 준다. 경의중앙선·경춘선·서해선·
수인분당선·경강선은 코레일 홈페이지 시각표 게시판의 XLSX가 열차번호가 붙은 유일한 공식 원천이다.

시트 구조 (2026-11 확인, korail_25745·25746·25702·23962):
  시발역 / 종착역 / 열차번호 행: 2열부터 열차마다 한 열
  역 이름이 있는 행 = 그 역 도착 시각, 바로 아래 이름 없는 행 = 출발 시각
  시각은 엑셀 날짜 비율(0.25 = 06:00). 빈칸·공백·글자는 정차하지 않음
  한 시트 안에 '시발역' 행이 다시 나오면 새 묶음이다.
매크로·수식은 실행하지 않고 저장된 값만 읽는다. 표준 라이브러리만 쓴다.
"""
from __future__ import annotations

from datetime import date as Date
import hashlib
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from .config import now
from .storage import dumps, identity

MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS = {'m': MAIN}
HEADERS = {'시발역': 'origin', '종착역': 'destination', '열차번호': 'train', '연계열번': 'linked'}
# 시트 이름 → 수집 노선 이름. 위에서부터 먼저 맞는 것을 쓴다. None은 적재하지 않음(ITX는 전동열차가 아님).
SHEET_LINES = (
    ('ITX', None),
    ('경의중앙', '경의중앙선'), ('경의선', '경의중앙선'),
    ('망우직결', '경춘선'), ('광운대', '경춘선'), ('경춘', '경춘선'),
    ('서해', '서해선'), ('수인분당', '수인분당선'), ('경강', '경강선'),
)
DAY_TYPES = ('평일', '휴일')
# 코레일 시각표는 역 이름을 세 글자 안팎으로 줄여 쓴다. 정식 역 이름(실시간 API·노선도 표기)으로 바꾼다.
# 2026-11-24 korail_25745·25746·25702·23962의 역 이름을 services/web 노선도 역 목록과 대조해 정했다.
# 목록에 없는 이름은 그대로 두며, 짝짓기에서 unmatched_timetable로 드러난다.
STATION_NAMES = {
    '경의중앙선': {'1양원': '양원', '1양정': '양정', '디엠시': '디지털미디어시티', '서울': '서울역',
                   '항공대': '한국항공대', '홍대입': '홍대입구', '효창공': '효창공원앞'},
    '경춘선': {'평내호': '평내호평'},
    '서해선': {'부천종': '부천종합운동장', '시흥능': '시흥능곡', '시흥대': '시흥대야', '시흥청': '시흥시청',
               '신김포': '김포공항', '신소사': '소사', '신신천': '신천', '신신현': '신현', '신초지': '초지'},
    '수인분당선': {'강남구': '강남구청', '구룡역': '구룡', '남동인': '남동인더스파크', '대모산': '대모산입구',
                   '로데오': '압구정로데오', '매탄권': '매탄권선', '소래포': '소래포구', '수원시': '수원시청',
                   '신길온': '신길온천', '신수원': '수원', '신인천': '인천', '인천논': '인천논현'},
    '경강선': {'경광주': '경기광주', '도예촌': '신둔도예촌', '세종릉': '세종대왕릉', '신이매': '이매', '신판교': '판교'},
}


def station_name(line, name):
    name = re.sub(r'\s+', '', name or '')
    return STATION_NAMES.get(line, {}).get(name, name) if name else name


def read_sheets(path):
    """(시트 이름, [(행 번호, {열 번호: 값})]). 숫자는 float, 글자는 str."""
    with ZipFile(path) as archive:
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(node.itertext()) for node in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        relations = {node.attrib['Id']: node.attrib['Target'] for node in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        for sheet in workbook.find('m:sheets', NS):
            target = relations[sheet.attrib['{' + REL + '}id']]
            target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            if '..' in PurePosixPath(target).parts:
                raise ValueError('unexpected workbook relationship')
            content = ET.fromstring(archive.read(target))
            rows = []
            for row in content.findall('m:sheetData/m:row', NS):
                values = {}
                for cell in row.findall('m:c', NS):
                    letters = re.fullmatch(r'([A-Z]+)\d+', cell.attrib['r'])[1]
                    column = 0
                    for letter in letters:
                        column = column * 26 + ord(letter) - 64
                    kind = cell.attrib.get('t')
                    node = cell.find('m:v', NS)
                    value = node.text if node is not None else None
                    if kind == 's' and value is not None:
                        value = strings[int(value)]
                    elif kind == 'inlineStr':
                        value = ''.join(cell.itertext())
                    elif kind not in ('s', 'str', 'inlineStr', 'e') and value is not None:
                        try:
                            value = float(value)
                        except ValueError:
                            pass
                    if value is not None:
                        values[column] = value
                if values:
                    rows.append((int(row.attrib['r']), values))
            yield sheet.attrib['name'], rows


def sheet_line(sheet, default=None):
    for hint, line in SHEET_LINES:
        if hint in sheet:
            return line, True
    return default, False


def clock(seconds):
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f'{h:02d}:{m:02d}:{s:02d}' if h <= 29 else None


def parse_sheet(rows):
    """한 시트 → 열차별 정차 목록 [{'train', 'origin', 'destination', 'linked', 'stops': [(역, 도착초, 출발초)]}]."""
    runs = []
    header, pending, by_column = {}, None, {}

    def numbers(cells):
        return {c: round(v * 86400) for c, v in cells.items() if c > 1 and isinstance(v, float)}

    def flush():
        nonlocal pending
        if pending:
            name, arrive, depart = pending
            depart = arrive if depart is None else depart
            for column in by_column:
                a, d = arrive.get(column), depart.get(column)
                if a is not None or d is not None:
                    by_column[column]['stops'].append((name, a, d))
        pending = None

    def close_block():
        flush()
        for run in by_column.values():
            if run['stops']:
                runs.append(run)

    for _, cells in rows:
        label = cells.get(1)
        label = label.strip() if isinstance(label, str) else ''
        if label in HEADERS:
            key = HEADERS[label]
            if key == 'origin':
                close_block()
                header, by_column = {}, {}
            header[key] = {c: str(v).strip() for c, v in cells.items() if c > 1 and str(v).strip()}
            if key == 'train':
                by_column = {c: dict(train=t, origin=header.get('origin', {}).get(c),
                                     destination=header.get('destination', {}).get(c), linked=None, stops=[])
                             for c, t in header['train'].items()}
            elif key == 'linked':
                flush()
                for c, value in header['linked'].items():
                    if c in by_column:
                        by_column[c]['linked'] = value
        elif label and by_column:
            flush()
            pending = (label, numbers(cells), None)
        elif not label and pending and pending[2] is None:
            pending = (pending[0], pending[1], numbers(cells))
            flush()
    close_block()

    # 자정을 넘는 열차: 앞 역보다 6시간 넘게 이르면 다음 날로 본다.
    for run in runs:
        last, fixed = None, []
        for name, a, d in run['stops']:
            times = []
            for t in (a, d):
                if t is not None and last is not None and t < last - 6 * 3600:
                    t += 86400
                if t is not None:
                    last = t
                times.append(t)
            fixed.append((name, *times))
        run['stops'] = fixed
    return runs


def load_korail_timetable(store, path, valid_from, line=None, day_type=None):
    """XLSX 하나를 적재한다. 같은 파일을 다시 넣으면 같은 행은 건너뛴다(event_id 고정)."""
    if day_type and day_type not in DAY_TYPES:
        raise ValueError(f'day_type must be one of {DAY_TYPES}')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', valid_from or ''):
        raise ValueError('valid_from must be YYYY-MM-DD (시각표 제목의 "…부터" 날짜)')
    Date.fromisoformat(valid_from)
    path = Path(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    loaded_at = now().isoformat()
    report = []
    with store.db:
        for sheet, rows in read_sheets(path):
            target, named = sheet_line(sheet, line)
            direction = '0' if '상행' in sheet else '1' if '하행' in sheet else None
            kind = next((k for k in DAY_TYPES if k in sheet), day_type)
            if named and target is None:
                report.append(dict(sheet=sheet, status='skipped_not_commuter_train'))
                continue
            if not target or direction is None or not kind:
                report.append(dict(sheet=sheet, status='skipped_unknown_line_direction_or_day',
                                   line=target, direction=direction, day_type=kind))
                continue
            runs = parse_sheet(rows)
            inserted = 0
            for run in runs:
                run['origin'] = station_name(target, run['origin'])
                run['destination'] = station_name(target, run['destination'])
                for order, (raw_station, a, d) in enumerate(run['stops']):
                    station = station_name(target, raw_station)
                    payload = dict(source=path.name, sha256=sha, sheet=sheet, trainno=run['train'], stnNm=station,
                                   stnNmOriginal=raw_station,
                                   upbdnbSe='상행' if direction == '0' else '하행', dptreStnNm=run['origin'],
                                   arvlStnNm=run['destination'], lnkgTrainno=run['linked'], order=order)
                    inserted += store.insert('static_timetable', dict(
                        event_id=identity(['korail', sha, valid_from, kind, target, sheet, run['train'], order, station, a, d]),
                        source=path.name, source_sha256=sha, valid_from=valid_from, day_type=kind, line=target,
                        train_no=run['train'], station_name=station, direction=direction,
                        arrival_time=clock(a) if a is not None else None,
                        departure_time=clock(d) if d is not None else None,
                        payload_json=dumps(payload), loaded_at=loaded_at))
            report.append(dict(sheet=sheet, status='loaded', line=target, direction=direction, day_type=kind,
                               trains=len(runs), stops=sum(len(r['stops']) for r in runs), inserted=inserted))
    return dict(file=path.name, sha256=sha, valid_from=valid_from, sheets=report)
