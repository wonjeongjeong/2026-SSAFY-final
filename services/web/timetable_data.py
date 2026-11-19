"""Normalize official dated timetables into distinct train runs, never headways."""
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
import re

from network import NETWORK

KST=timezone(timedelta(hours=9))
STATIONS={s['id']:s for s in NETWORK['stations']}
LINES={l['id']:l for l in NETWORK['lines']}
BY_LINE_NAME={l['name']:l['id'] for l in LINES.values()}
API_IDS={str(1000+i):str(i) for i in range(1,10)}
API_IDS.update({'1032':'GTX-A','1063':'GJ','1065':'AR','1067':'GC','1075':'SB','1077':'SD','1081':'GG','1092':'UI','1093':'SH','1094':'SL'})
FOLDER=Path(__file__).parent/'data'


def normalized(value):
    value=re.sub(r'\([^)]*\)','',str(value or ''))
    value=re.sub(r'[\s·]','',value)
    return {'서울':'서울역','당고개':'불암산','뚝섬유원지':'자양','지제':'평택지제','춘의역':'춘의','이수':'총신대입구','성수종착':'성수','응암순환':'응암'}.get(value,value)


_station_index=defaultdict(set)
for station in STATIONS.values():
    for line in station['lines']:
        for name in [station['name'],*station['aliases']]:
            _station_index[(line,normalized(name))].add(station['id'])


def station_id(line,name):
    matches=_station_index.get((line,normalized(name)),set())
    return next(iter(matches)) if len(matches)==1 else None


def seconds(value):
    if not value: return None
    digits=str(value).replace(':','')
    if not re.fullmatch(r'\d{6}',digits): return None
    h,m,s=int(digits[:2]),int(digits[2:4]),int(digits[4:])
    if h>29 or m>59 or s>59: return None
    return h*3600+m*60+s


def day_kind(date):
    # Explicit official calendar overrides are shared with the collector.
    config=Path(__file__).resolve().parents[2]/'config'/'collector.json'
    overrides=json.loads(config.read_text(encoding='utf-8-sig')).get('day_type_overrides',{})
    return overrides.get(date.isoformat(),'공휴일' if date.weekday()==6 else '주말' if date.weekday()==5 else '평일')


@lru_cache(maxsize=1)
def load_rows():
    files=sorted(FOLDER.glob('timetable-*.json.gz'))
    if not files: return [],{}
    # Each compact snapshot is a service-day template, with source validity.
    templates={}
    metadata={}
    for path in files[-8:]:
        with gzip.open(path,'rt',encoding='utf-8') as f: doc=json.load(f)
        metadata[doc['service_date']]=doc.get('source')
        for values in doc['rows']:
            row=dict(zip(doc['columns'],values));line=BY_LINE_NAME.get(row['line'])
            if not line: continue
            key=(line,row['day_type'],row['number'],row['station'],row['arrival'],row['departure'],row['branch'],row['origin'],row['destination'],row['temporary'])
            templates[key]={**row,'line':line,'source':doc['source'],'fetched_date':doc['service_date']}
    supplemental=FOLDER/'supplemental.json'
    if supplemental.exists():
        for i,row in enumerate(json.loads(supplemental.read_text(encoding='utf-8'))):
            templates[('supplemental',i)]=row
    return list(templates.values()),metadata


def build_runs(rows,date,kind=None):
    kind=kind or day_kind(date)
    midnight=datetime.combine(date,datetime.min.time(),KST).timestamp()
    groups=defaultdict(list);unmapped=0
    for row in rows:
        if row['day_type'] not in (kind,'상시'): continue
        start=(row.get('valid_from') or '')[:10];end=(row.get('valid_until') or '')[:10]
        if start and date.isoformat()<start: continue
        if end and date.isoformat()>end: continue
        line=row['line'];sid=station_id(line,row['station'])
        a,d=seconds(row.get('arrival')),seconds(row.get('departure'))
        if sid is None or (a is None and d is None):
            unmapped+=1;continue
        key=(line,str(row['number']),str(row['direction']),row.get('branch'),row.get('origin'),row.get('destination'),row.get('temporary','N'))
        groups[key].append({'station':sid,'arrival':midnight+(a if a is not None else d),'departure':midnight+(d if d is not None else a),'can_board':d is not None,'can_alight':a is not None,'source':row['source'],'express':row.get('express') in ('Y','1','급행','특급'),'valid_from':row.get('valid_from'),'temporary':row.get('temporary','N')})
    runs=[]
    # Temporary schedules override regular schedules for the same identity.
    temporary={key[:6] for key in groups if key[-1]=='Y'}
    for key,stops in groups.items():
        if key[-1]!='Y' and key[:6] in temporary: continue
        stops=list({(s['station'],s['arrival'],s['departure']):s for s in stops}.values())
        stops.sort(key=lambda s:(s['arrival'],s['departure']))
        chunks=[];chunk=[];seen=set()
        for stop in stops:
            # A genuine loop (e.g. Eungam) may visit a non-origin station twice.
            # Split reused numbers at a new origin, not at every repeated stop.
            origin_sid=station_id(key[0],key[4])
            destination_sid=station_id(key[0],key[5])
            repeat=stop['station']==origin_sid and origin_sid!=destination_sid and stop['station'] in seen and chunk and stop['can_board']
            gap=chunk and stop['arrival']-chunk[-1]['departure']>3600
            if repeat or gap:
                if len(chunk)>1: chunks.append(chunk)
                chunk=[];seen=set()
            chunk.append(stop);seen.add(stop['station'])
        if len(chunk)>1: chunks.append(chunk)
        for chunk in chunks:
            digest=hashlib.sha1(repr((date.isoformat(),key,chunk[0]['departure'])).encode()).hexdigest()[:18]
            runs.append({'id':digest,'line':key[0],'number':key[1],'direction':key[2],
                'destination':key[5] or STATIONS[chunk[-1]['station']]['name'],
                'origin_name':key[4],'stops':chunk,'source':chunk[0]['source'],
                'express':any(s['express'] for s in chunk),'service_date':date.isoformat()})
    return runs,{'unmapped_rows':unmapped,'day_type':kind,'run_count':len(runs)}


@lru_cache(maxsize=8)
def base_runs(date_text,kind=None):
    date=datetime.fromisoformat(date_text).date()
    rows,meta=load_rows()
    runs,quality=build_runs(rows,date,kind)
    quality['snapshots']=meta
    return runs,quality
