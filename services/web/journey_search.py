"""Boarding-round timetable search over actual train runs, in both directions.

All trains in the requested time window are scanned. Three distinct route
signatures per (station, line, round) bound work while retaining alternatives;
this prototype does not claim an unrestricted globally optimal profile.
"""
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
import math

from timetable_data import KST,LINES,STATIONS
from network import NETWORK


def iso(value): return datetime.fromtimestamp(value,KST).isoformat()


@lru_cache(maxsize=8192)
def map_path(line,a,b):
    graph=defaultdict(list)
    for edge in NETWORK['edges']:
        if edge['line_id']==line:
            graph[edge['origin']].append(edge['destination']);graph[edge['destination']].append(edge['origin'])
    todo=[a];parents={a:None}
    for current in todo:
        if current==b: break
        for target in graph[current]:
            if target not in parents: parents[target]=current;todo.append(target)
    if b not in parents: return [a,b]
    path=[b]
    while path[-1]!=a: path.append(parents[path[-1]])
    return list(reversed(path))


def leg(run,start,end):
    a,b=run['stops'][start],run['stops'][end]
    return {'line_id':run['line'],'trip_id':run['id'],'direction':run['direction'],
        'origin':a['station'],'destination':b['station'],
        '_run':run,'_start':start,'_end':end,
        'board_ts':a['departure'],'arrive_ts':b['arrival']}


def signature(legs):
    return '|'.join(l['line_id'] for l in legs)


class Search:
    def __init__(self,runs,policy,now,deadline,max_transfers=3,skip=0):
        self.runs=[r for r in runs if r['stops'][-1]['arrival']>=now and r['stops'][0]['departure']<=deadline]
        self.policy=policy;self.now=now;self.deadline=deadline
        self.max_transfers=max_transfers;self.skip=skip
        self.departures=defaultdict(list);self.threshold_cache={}
        for r in self.runs:
            for s in r['stops']:
                if s['can_board']:
                    self.departures[(s['station'],r['line'],r['direction'])].append(s['departure'])
        for k,values in self.departures.items(): self.departures[k]=sorted(set(values))

    def can_board(self,run,stop,ready):
        key=(stop['station'],run['line'],run['direction'],ready,self.skip)
        if key not in self.threshold_cache:
            times=self.departures[(stop['station'],run['line'],run['direction'])]
            index=bisect_left(times,ready)+self.skip
            self.threshold_cache[key]=times[index] if index<len(times) else math.inf
        return stop['departure']>=self.threshold_cache[key]

    @staticmethod
    def redundant_change(previous,following):
        if previous['line_id']!=following['line_id']: return False
        old=previous['_run']['stops'][previous['_end']:]
        new=following['_run']['stops'][following['_start']:following['_end']+1]
        sequence=[s['station'] for s in old]
        try:
            indexes=[sequence.index(s['station']) for s in new]
        except ValueError:
            return False
        follows=indexes==sorted(set(indexes))
        same_service=previous['_run']['express']==following['_run']['express'] and previous['direction']==following['direction']
        return follows and (same_service or old[indexes[-1]]['arrival']<=following['arrive_ts'])

    @staticmethod
    def insert(bucket,label,backwards=False):
        key=(label['station'],label['line'])
        values=bucket[key]
        for previous in values:
            if previous['signature']==label['signature']:
                if (previous['time']>=label['time'] if backwards else previous['time']<=label['time']): return
                values.remove(previous);break
        values.append(label);values.sort(key=lambda x:x['time'],reverse=backwards)
        del values[3:]

    def search(self,origin,destination,backwards=False,safety_seconds=0):
        start=destination if backwards else origin
        target=origin if backwards else destination
        seed={'station':start,'line':None,'time':self.deadline-safety_seconds if backwards else self.now,
              'legs':[],'signature':'','direction':None}
        previous={(start,None):[seed]};results=[]
        for boarding in range(self.max_transfers+1):
            following=defaultdict(list)
            by_station=defaultdict(list)
            for (station,_),labels in previous.items(): by_station[station].extend(labels)
            for run in self.runs:
                onboard=[];onboard_signatures=set();stops=run['stops'];line=run['line']
                indexes=range(len(stops)-1,-1,-1) if backwards else range(len(stops))
                for i in indexes:
                    stop=stops[i];station=stop['station']
                    if backwards:
                        if stop['can_board'] and stop['departure']>=self.now+self.policy.boarding_seconds:
                            for end,label in onboard:
                                if end<=i: continue
                                segment=leg(run,i,end);legs=[segment]+label['legs']
                                if label['legs'] and self.redundant_change(segment,label['legs'][0]): continue
                                self.insert(following,{'station':station,'line':line,'direction':run['direction'],'time':stop['departure'],'legs':legs,'signature':signature(legs)},True)
                        if stop['can_alight']:
                            candidates=by_station.get(station,[])
                            for label in candidates:
                                if label['legs'] and label['legs'][0]['trip_id']==run['id']: continue
                                if label['line'] is None:
                                    feasible=stop['arrival']<=label['time']
                                else:
                                    tr=self.policy.get(station,line,label['line'],run['direction'],label['direction'])
                                    ready=stop['arrival']+tr['total_seconds']
                                    first=label['legs'][0]
                                    fake_run={'line':label['line'],'direction':label['direction']}
                                    feasible=ready<=label['time'] and self.can_board(fake_run,{'station':station,'departure':first['board_ts']},ready)
                                if feasible and label['signature'] not in onboard_signatures:
                                    onboard.append((i,label));onboard_signatures.add(label['signature'])
                    else:
                        if stop['can_alight'] and stop['arrival']<=self.deadline:
                            for begin,label in onboard:
                                if begin>=i: continue
                                segment=leg(run,begin,i);legs=label['legs']+[segment]
                                if label['legs'] and self.redundant_change(label['legs'][-1],segment): continue
                                self.insert(following,{'station':station,'line':line,'direction':run['direction'],'time':stop['arrival'],'legs':legs,'signature':signature(legs)})
                        if stop['can_board']:
                            candidates=by_station.get(station,[])
                            for label in candidates:
                                if label['legs'] and label['legs'][-1]['trip_id']==run['id']: continue
                                transfer=self.policy.get(station,label['line'],line,label['direction'],run['direction'])['total_seconds'] if label['line'] else self.policy.boarding_seconds
                                ready=label['time']+transfer
                                if self.can_board(run,stop,ready) and stop['departure']<=self.deadline:
                                    if label['signature'] not in onboard_signatures:
                                        onboard.append((i,label));onboard_signatures.add(label['signature'])
                    # Keep at most three viable boarding histories per route;
                    # every train is scanned, including express and short turns.
                    if len(onboard)>12:
                        onboard.sort(key=lambda x:(x[1]['time'] if backwards else -x[1]['time']),reverse=True)
                        onboard=onboard[:12]
            for key,labels in following.items():
                if key[0]==target:
                    for label in labels:
                        if backwards:
                            first=label['legs'][0]
                            if not self.can_board({'line':first['line_id'],'direction':first['direction']},{'station':origin,'departure':first['board_ts']},self.now+self.policy.boarding_seconds): continue
                        results.append(self.summarize(label['legs']))
            previous=following
            if not previous: break
        results.sort(key=(lambda r:r['board_ts']) if backwards else (lambda r:r['arrive_ts']),reverse=backwards)
        unique={}
        for result in results: unique.setdefault(result['route_key'],result)
        values=list(unique.values())
        def compressed(value):
            lines=[]
            for item in value['legs']:
                if not lines or lines[-1]!=item['line_id']: lines.append(item['line_id'])
            return tuple(lines)
        # Hide redundant train changes when the same line sequence has a
        # journey that leaves no earlier, arrives no later and changes less.
        values=[value for value in values if not any(
            other is not value and compressed(other)==compressed(value)
            and other['transfers']<value['transfers']
            and other['board_ts']>=value['board_ts'] and other['arrive_ts']<=value['arrive_ts']
            for other in values)]
        chosen=values[:8]
        simplest=min(values,key=lambda r:(r['transfers'],r['arrive_ts']),default=None)
        if simplest and simplest not in chosen: chosen.append(simplest)
        return chosen

    def summarize(self,legs):
        materialized=[]
        for segment in legs:
            clean={k:v for k,v in segment.items() if not k.startswith('_')}
            run=segment['_run'];stops=run['stops'][segment['_start']:segment['_end']+1]
            a,b=stops[0],stops[-1]
            clean.update(train_no=run['number'],train_label=run['number']+' · '+('급행' if run['express'] else '일반'),
                express=run['express'],direction_label=run['destination']+' 방면',destination_name=run['destination'],
                board_at=iso(a['departure']),arrive_at=iso(b['arrival']),scheduled_board_at=iso(a['scheduled_departure']),scheduled_arrive_at=iso(b['scheduled_arrival']),
                delay_seconds=b['delay_seconds'],basis='live_projection' if run.get('live_anchors') else 'timetable',board_basis=a['basis'],arrival_basis=b['basis'],source=run['source'],observed_at=a.get('observed_at') or b.get('observed_at'))
            stations=[stops[0]['station']]
            for a,b in zip(stops,stops[1:]): stations.extend(map_path(segment['line_id'],a['station'],b['station'])[1:])
            clean.update(stations=stations,stop_stations=[s['station'] for s in stops])
            materialized.append(clean)
        legs=materialized
        transfers=[]
        for a,b in zip(legs,legs[1:]):
            t=self.policy.get(a['destination'],a['line_id'],b['line_id'],a['direction'],b['direction']).copy()
            t.update(arrive_at=a['arrive_at'],ready_at=iso(a['arrive_ts']+t['total_seconds']),next_departure=b['board_at'],wait_seconds=round(b['board_ts']-a['arrive_ts']-t['total_seconds']))
            transfers.append(t)
        first,last=legs[0],legs[-1]
        return {'legs':legs,'transfers':len(legs)-1,'transfer_details':transfers,
            'board_at':first['board_at'],'leave_at':first['board_at'],'arrive_at':last['arrive_at'],
            'board_ts':first['board_ts'],'arrive_ts':last['arrive_ts'],
            'duration_minutes':math.ceil((last['arrive_ts']-first['board_ts'])/60),
            'from_now_minutes':math.ceil((last['arrive_ts']-self.now)/60),
            'slack_minutes':math.floor((self.deadline-last['arrive_ts'])/60),
            'station_count':sum(len(l['stop_stations'])-1 for l in legs),
            'live_legs':sum(l['basis']!='timetable' for l in legs),
            'route_key':signature(legs),'skip_trains':self.skip,
            'crowding_basis':'user_scenario','access_minutes':0}
