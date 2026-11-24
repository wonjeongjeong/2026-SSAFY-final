"""Official 19-line drawing topology; actual train services live in timetables.

Display geometry and service patterns are separate. A flattened drawing list
must never connect one branch terminus to the next branch's first station.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from itertools import combinations
from line_catalog import EXTRA_LINES

SOURCE_URL='https://www.seoulmetro.co.kr/kr/getLineData.do'
SOURCE=json.loads(Path(__file__).with_name('metro_source.json').read_text(encoding='utf-8'))
COLORS={'1':'#1552a0','2':'#16a675','3':'#e47b24','4':'#258ed1',
        '5':'#9664c7','6':'#b48237','7':'#727e35','8':'#df5b85'}
SOURCE_KEYS={str(n):f'{n+1}-{n}' for n in range(1,10)}
SOURCE_KEYS.update({identifier:row[0] for identifier,row in EXTRA_LINES.items()})
COLORS['9']='#b39b70'
COLORS.update({identifier:row[2] for identifier,row in EXTRA_LINES.items()})


def station_uid(value):
    # The map uses a separate platform ID for the Gyeongui Seoul terminus.
    return 'st'+({'1251':'0150'}.get(value,value))


def point(value):
    x,y=map(float,value.split(','))
    return [round(x*10+80,2),round(y*10+80,2)]


def make_network():
    stations={}
    source_lists={}
    by_name={}
    for number,key in SOURCE_KEYS.items():
        names=[]
        for row in SOURCE[key]['stations']:
            if not row.get('station-nm'):
                continue
            uid=station_uid(row['data-uid'])
            name=''.join(row['station-nm'].split())
            if name=='총신대입구':
                name='총신대입구(이수)'
            if uid not in stations:
                x,y=point(row['data-coords'])
                stations[uid]=dict(id=uid,name=name,x=x,y=y,lines=[],
                                   label_position=row.get('data-labelPos','E').upper(),aliases=[])
            station=stations[uid]
            if number not in station['lines']:
                station['lines'].append(number)
            if name!=station['name'] and name not in station['aliases']:
                station['aliases'].append(name)
            by_name[name]=uid
            if not names or names[-1]!=uid:
                names.append(uid)
        source_lists[number]=names
    by_name['이수']=by_name['총신대입구(이수)']
    stations[by_name['이수']]['aliases'].extend(['이수','총신대입구'])
    if '불암산' in by_name:
        stations[by_name['불암산']]['aliases'].append('당고개')

    def section(ids,start,end):
        a,b=ids.index(by_name[start]),ids.index(by_name[end])
        return ids[a:b+1] if a<=b else list(reversed(ids[b:a+1]))

    patterns=[]
    def pattern(number,suffix,ids,loop=False,one_way=False):
        patterns.append(dict(id=f'{number}-{suffix}',line_id=number,stations=ids,
                             loop=loop,one_way=one_way))

    one=source_lists['1']
    north=section(one,'연천','구로')
    south=list(dict.fromkeys(uid for uid in section(one,'가산디지털단지','신창') if uid!=by_name['광명']))
    pattern('1','incheon',section(one,'연천','인천'))
    pattern('1','sinchang',north+south)
    pattern('1','gwangmyeong',section(one,'영등포','구로')+section(one,'가산디지털단지','금천구청')+[by_name['광명']])
    pattern('1','seodongtan',north+section(south,'가산디지털단지','병점')+[by_name['서동탄']])
    two=source_lists['2']
    pattern('2','circle',section(two,'신도림','대림'),loop=True)
    pattern('2','sinjeong',section(two,'까치산','신도림'))
    pattern('2','seongsu',[by_name[x] for x in ('성수','용답','신답','용두','신설동')])
    pattern('3','main',list(dict.fromkeys(source_lists['3'])))
    pattern('4','main',list(dict.fromkeys(source_lists['4'])))
    five=source_lists['5']
    pattern('5','hanam',section(five,'방화','하남검단산'))
    pattern('5','macheon',section(five,'방화','강동')+section(five,'둔촌동','마천'))
    six=source_lists['6']
    main6=section(six,'신내','응암')
    # The Eungam loop is one-way: Eungam -> Yeokchon -> ... -> Gusan -> Eungam.
    pattern('6','eungam',main6+section(six,'역촌','구산')+[by_name['응암']]+list(reversed(main6[:-1])),one_way=True)
    pattern('7','main',list(dict.fromkeys(source_lists['7'])))
    pattern('8','main',list(dict.fromkeys(source_lists['8'])))

    edges=[]
    drawings=defaultdict(list)
    for number,key in SOURCE_KEYS.items():
        code_to_uid={r['station-cd']:station_uid(r['data-uid']) for r in SOURCE[key]['stations']
                     if r.get('station-cd') and r.get('data-uid')}
        previous=None
        segment=[]
        drawing=[]
        for row in SOURCE[key]['stations']:
            if 'data-moveTo' in row:
                if len(drawing)>1:
                    drawings[number].append(drawing)
                drawing=[point(row['data-moveTo'])]
                previous=station_uid(row['data-uid']) if row.get('data-uid') else None
                segment=drawing.copy()
                continue
            if 'data-coords' not in row:
                continue
            coordinate=point(row['data-coords'])
            drawing.append(coordinate)
            segment.append(coordinate)
            uid=station_uid(row['data-uid']) if row.get('data-uid') else code_to_uid.get(row.get('station-cd'))
            if uid in stations:
                if previous and previous!=uid:
                    edges.append(dict(line_id=number,origin=previous,destination=uid,points=segment))
                if previous!=uid:
                    previous=uid
                    segment=[coordinate]
        if len(drawing)>1:
            drawings[number].append(drawing)
    # Build example service paths from actual adjacent stations. Branches and
    # disconnected GTX-A sections must never become a single flattened ride.
    for number in list(SOURCE_KEYS)[8:]:
        adjacent=defaultdict(set)
        for edge in edges:
            if edge['line_id']==number:
                adjacent[edge['origin']].add(edge['destination'])
                adjacent[edge['destination']].add(edge['origin'])
        terminals=[uid for uid,neighbors in adjacent.items() if len(neighbors)==1]
        for start,end in combinations(terminals,2):
            todo=[(start,[start])]
            while todo:
                current,path=todo.pop()
                if current==end:
                    pattern(number,str(len(patterns)),path)
                    break
                todo.extend((neighbor,path+[neighbor]) for neighbor in adjacent[current] if neighbor not in path)
    lines=[]
    for number in COLORS:
        ids=list(dict.fromkeys(source_lists[number]))
        lines.append(dict(id=number,name=f'{number}호선' if number.isdigit() else EXTRA_LINES[number][1],color=COLORS[number],stations=ids,
                          loop=number=='2',paths=drawings[number],
                          patterns=[p['id'] for p in patterns if p['line_id']==number],station_count=len(ids)))
    return dict(stations=list(stations.values()),lines=lines,patterns=patterns,edges=edges,
                map=dict(width=3100,height=2100),mode='realtime_timetable',scope='수도권 지하철 19개 노선 · 지선 포함',
                source=dict(name='서울교통공사 사이버스테이션',url=SOURCE_URL,checked_date='2026-11-17'),
                assumptions=dict(max_transfers=3),
                notice='지도는 공식 노선 연결입니다. 경로는 실제 시간표와 안전하게 매칭한 실시간 정보로 계산하며, 환승 여유와 혼잡 대기 가정은 별도로 표시합니다.')


NETWORK=make_network()
