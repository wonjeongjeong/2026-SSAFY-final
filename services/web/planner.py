"""Actual timetable + fresh real-time ETA + forward/backward route search."""
from datetime import datetime,timedelta
import math
import time

from timetable_data import KST,STATIONS,LINES,base_runs
from prediction import apply_live
from transfers import TransferPolicy
from journey_search import Search
from live_data import live_snapshot


def timestamp(value):
    dt=datetime.fromisoformat(value)
    return dt.replace(tzinfo=KST) if dt.tzinfo is None else dt.astimezone(KST)


def integer(value,low,high,name):
    try:
        n=float(value)
        if isinstance(value,bool) or not math.isfinite(n) or not n.is_integer() or not low<=n<=high: raise ValueError()
        return int(n)
    except (ValueError,TypeError): raise ValueError(f'{name}: {low}~{high} 사이 정수를 입력해 주세요.') from None


def recommend(payload,now=None,runs=None,feed=None):
    started=time.monotonic();now=now or datetime.now(KST)
    origin,destination=payload.get('origin'),payload.get('destination')
    if origin not in STATIONS or destination not in STATIONS: raise ValueError('출발역과 도착역을 목록에서 선택해 주세요.')
    if origin==destination: raise ValueError('출발역과 도착역을 다르게 선택해 주세요.')
    try: deadline=timestamp(payload.get('deadline',''))
    except (ValueError,TypeError): raise ValueError('목표 도착 시각을 확인해 주세요.') from None
    if not now<deadline<=now+timedelta(days=2): raise ValueError('목표 도착 시각은 현재 이후 48시간 이내로 설정해 주세요.')
    safety=integer(payload.get('safety_minutes',5),0,30,'도착 여유')
    extra=integer(payload.get('transfer_extra_minutes',2),0,20,'환승 추가 여유')
    boarding=integer(payload.get('boarding_seconds',30),0,120,'승차 여유(초)')
    skip=integer(payload.get('skip_trains',0),0,2,'탑승 시 보내는 열차 수')
    maximum=integer(payload.get('max_transfers',3),0,4,'최대 환승 횟수')
    allow=payload.get('allow_transfer',True)
    if not isinstance(allow,bool): raise ValueError('환승 허용 여부를 확인해 주세요.')
    if not allow: maximum=0
    raw=payload.get('transfer_overrides',{})
    if not isinstance(raw,dict) or len(raw)>100: raise ValueError('환승시간 입력을 확인해 주세요.')
    overrides={}
    for key,value in raw.items():
        parts=key.split(':')
        if len(parts) not in (3,5) or parts[0] not in STATIONS or parts[1] not in LINES or parts[2] not in LINES:
            raise ValueError('환승역과 노선을 확인해 주세요.')
        overrides[key]=integer(value,0,3600,'환승 이동시간(초)')
    quality=[]
    if runs is None:
        runs=[];day=now.date()-timedelta(days=1 if now.hour<6 else 0)
        while day<=deadline.date():
            values,meta=base_runs(day.isoformat());runs.extend(values);quality.append({'date':day.isoformat(),**meta})
            day+=timedelta(days=1)
    if feed is None: feed=live_snapshot()
    # A query gets one immutable real-time snapshot, so its routes are comparable.
    relevant=[r for r in runs if r['stops'][-1]['arrival']>=now.timestamp() and r['stops'][0]['departure']<=max(deadline,now+timedelta(hours=4)).timestamp()]
    predicted,stats=apply_live(relevant,feed,now)
    policy=TransferPolicy(extra*60,overrides,boarding)
    forward=Search(predicted,policy,now.timestamp(),(now+timedelta(hours=4)).timestamp(),maximum,skip)
    earliest=forward.search(origin,destination)
    reverse=Search(predicted,policy,now.timestamp(),deadline.timestamp(),maximum,skip)
    latest=reverse.search(origin,destination,backwards=True)
    safe=reverse.search(origin,destination,backwards=True,safety_seconds=safety*60) if safety else latest
    for result in earliest+latest+safe:
        result['slack_minutes']=math.floor((deadline.timestamp()-result['arrive_ts'])/60)
    low_transfer=min(earliest,key=lambda x:(x['transfers'],x['arrive_ts']),default=None)
    covered=sorted({r['line'] for r in runs})
    station_sets={line:{s['station'] for run in runs if run['line']==line for s in run['stops']} for line in covered}
    partial=[{'id':line,'name':LINES[line]['name'],'stations':len(ids),'map_stations':len(LINES[line]['stations'])} for line,ids in station_sets.items() if len(ids)<len(LINES[line]['stations'])]
    missing=[line['name'] for line in LINES.values() if line['id'] not in covered]
    warnings=[]
    if not feed.get('available'): warnings.append('실시간 조회 실패: 현재 결과는 실제 시간표 기준입니다.')
    elif not stats['used_anchors']: warnings.append('현재 시간표 열차에 안전하게 연결된 최신 도착예정 정보가 없어 시간표 기준으로 계산했습니다.')
    if missing: warnings.append('시간표 미확보 또는 해당 운행일 데이터 없음: '+', '.join(missing))
    for part in partial:
        if part['id'] in STATIONS[origin]['lines'] or part['id'] in STATIONS[destination]['lines']:
            warnings.append(f"{part['name']}은 지도 {part['map_stations']}개 역 중 {part['stations']}개 역의 유효한 시간표만 확보돼 일부 운행이 탐색에서 빠질 수 있습니다.")
    if skip: warnings.append(f'각 탑승에서 앞의 {skip}대를 보내는 사용자 시나리오입니다. 실시간 혼잡도 측정값이 아닙니다.')
    return {'mode':'realtime_timetable','as_of':now.isoformat(),'deadline':deadline.isoformat(),
        'origin':origin,'destination':destination,'status':'ok' if latest else 'unreachable' if earliest else 'no_data',
        'recommended':safe[0] if safe else None,'latest':latest[0] if latest else None,
        'earliest':earliest[0] if earliest else None,'fewer_transfers':low_transfer,
        'routes':{'fastest':earliest[:5],'latest':latest[:5],'recommended':safe[:5]},
        'alternatives':safe[1:4],'safety_minutes':safety,'warnings':warnings,
        'coverage':{'line_ids':covered,'missing_lines':missing,'partial_lines':partial,'quality':quality},
        'live':{'available':bool(feed.get('available')),'collected_at':feed.get('collected_at'),**stats},
        'calculation_ms':round((time.monotonic()-started)*1000),
        'assumptions':['실제 시간표의 운행 열차를 사용합니다.',
            '현재 도착예정 시각과 시간표 차이를 해당 열차 후속 구간에 유지하는 초기 추정입니다.',
            '환승은 공식 최단 이동시간 또는 명시한 가정에 추가 여유를 더합니다.',
            '실시간 승강장 대기열·탑승 실패 횟수는 미확보이며 혼잡 대기는 사용자 시나리오입니다.',
            '최대 환승 횟수와 상태별 대안 수를 제한한 탐색이며 전체 경로의 전역 최적해를 보장하지 않습니다.']}
