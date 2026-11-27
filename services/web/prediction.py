"""Attach fresh train-specific ETA anchors to actual scheduled train runs."""
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
import re

from timetable_data import API_IDS,BY_LINE_NAME,KST,normalized,station_id


def parse_time(value):
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return (dt.replace(tzinfo=KST) if dt.tzinfo is None else dt).timestamp()
    except (ValueError,TypeError):
        return None


def number_keys(number):
    value=str(number or '').upper()
    result={value}
    # Only recognized prefix variants; identity still requires line, direction,
    # destination and a unique service occurrence in a limited time window.
    if re.fullmatch(r'K?\d+K?',value):
        digits=value.strip('K').lstrip('0') or '0'
        result.update({digits,'K'+digits})
    if re.fullmatch(r'C\d+',value): result.add(value[1:])
    if re.fullmatch(r'X\d+',value): result.add(value[1:].lstrip('0') or '0')
    return result


def apply_live(runs,feed,now):
    now_ts=now.timestamp();index=defaultdict(list)
    copies=[]
    for run in runs:
        copy={**run,'stops':[{**s,'scheduled_arrival':s['arrival'],'scheduled_departure':s['departure'],'basis':'timetable','delay_seconds':0} for s in run['stops']], 'live_anchors':0}
        copies.append(copy)
        for key in number_keys(run['number']): index[(run['line'],key)].append(copy)
    anchors=defaultdict(dict)
    stats={'received':len(feed.get('arrivals',[])),'used_anchors':0,'matched_trains':0,'stale':0,'ambiguous':0,'unmatched':0,'past_eta':0}
    observations=list(feed.get('arrivals',[])) if feed.get('available') else []
    for row in feed.get('delays',[]) if feed.get('available') else []:
        if row.get('match_status')!='matched_observation_interval': continue
        try:
            if not -120 <= float(row['delay_seconds']) <= 1200: continue
        except (KeyError,TypeError,ValueError): continue
        line=BY_LINE_NAME.get(row.get('line'))
        observations.append({'subwayId':next((k for k,v in API_IDS.items() if v==line),''),
            'statnNm':row.get('station_name'),'recptnDt':row.get('observed_at'),
            'updnLine':{'0':'상행','1':'하행'}.get(row.get('direction')),
            'btrainNo':row.get('train_no'),'bstatnNm':row.get('destination'),
            '_position':True,'_stream_delay':True,'_scheduled_at':row.get('scheduled_at')})
    for row in feed.get('positions',[]) if feed.get('available') else []:
        if feed.get('source')=='kafka_redis': continue  # use bounded, timetable-verified anchors
        status=str(row.get('trainSttus'))
        if status not in ('1','2'): continue
        line=BY_LINE_NAME.get(row.get('line'))
        observations.append({'subwayId':next((k for k,v in API_IDS.items() if v==line),''),
            'statnNm':row.get('statnNm'),'recptnDt':row.get('recptnDt'),
            'updnLine':{'0':'상행','1':'하행'}.get(str(row.get('updnLine'))),
            'btrainNo':row.get('trainNo'),'bstatnNm':row.get('statnTnm'),
            '_position':True,'_departure':status=='2'})
    for row in observations:
        line=API_IDS.get(str(row.get('subwayId')))
        sid=station_id(line,row.get('statnNm'))
        observed=parse_time(row.get('recptnDt'))
        if observed is None or not -5<=now_ts-observed<=(90 if row.get('_stream_delay') else 180):
            stats['stale']+=1;continue
        try: eta=0 if row.get('_position') else int(row.get('barvlDt'))
        except (ValueError,TypeError): continue
        # Zero is often 'no estimate'; never turn that into a guaranteed arrival.
        if eta<=0 and not row.get('_position'): continue
        predicted=observed+eta
        if predicted<now_ts-15 and not row.get('_stream_delay'):
            stats['past_eta']+=1;continue
        direction={'상행':'0','내선':'0','하행':'1','외선':'1'}.get(row.get('updnLine'))
        candidates={}
        numbers=number_keys(row.get('btrainNo'))
        number=str(row.get('btrainNo') or '')
        if row.get('_stream_delay') and line=='2' and len(number)==4 and number.isdigit() and number[0]!='2':
            numbers.add('2'+number[1:])
        for key in numbers:
            for run in index.get((line,key),[]): candidates[run['id']]=run
        matches=[]
        for run in candidates.values():
            if direction is not None and run['direction']!=direction: continue
            destination=normalized(row.get('bstatnNm'))
            if destination and normalized(run['destination'])!=destination: continue
            express=row.get('btrainSttus')
            if express in ('급행','특급') and not run['express']: continue
            for i,stop in enumerate(run['stops']):
                schedule=stop['scheduled_departure'] if row.get('_departure') else stop['scheduled_arrival']
                if row.get('_stream_delay'):
                    verified=parse_time(row.get('_scheduled_at'))
                    if verified is None or abs(verified-schedule)>1: continue
                if stop['station']==sid and abs(predicted-schedule)<=1200:
                    matches.append((run,i,stop))
        if len(matches)!=1:
            stats['ambiguous' if matches else 'unmatched']+=1;continue
        run,i,stop=matches[0]
        previous=anchors[run['id']].get(i)
        if previous is None or observed>previous['observed']:
            schedule=stop['scheduled_departure'] if row.get('_departure') else stop['scheduled_arrival']
            delta=predicted-schedule
            anchors[run['id']][i]={'delta':delta,'observed':observed,'predicted':stop['scheduled_arrival']+delta,'position':bool(row.get('_position'))}
    for run in copies:
        values=anchors.get(run['id'])
        if not values: continue
        positions=sorted(values);run['live_anchors']=len(values)
        stats['matched_trains']+=1;stats['used_anchors']+=len(values)
        previous_departure=None
        for i,stop in enumerate(run['stops']):
            anchor_index=positions[max(0,bisect_right(positions,i)-1)]
            anchor=values[anchor_index]
            arrival=stop['scheduled_arrival']+anchor['delta']
            departure=stop['scheduled_departure']+anchor['delta']
            if previous_departure is not None:
                # Preserve official running durations when inconsistent source
                # anchors would otherwise make the train run backwards in time.
                minimum=max(1,stop['scheduled_arrival']-run['stops'][i-1]['scheduled_departure'])
                arrival=max(arrival,previous_departure+minimum)
            departure=max(departure,arrival+(stop['scheduled_departure']-stop['scheduled_arrival']))
            stop.update(arrival=arrival,departure=departure,
                basis=('live_position' if values[i].get('position') else 'live_eta') if i in values and abs(arrival-values[i]['predicted'])<1 else 'live_projection',
                delay_seconds=round(arrival-stop['scheduled_arrival']),observed_at=datetime.fromtimestamp(anchor['observed'],KST).isoformat())
            previous_departure=departure
    return copies,stats
