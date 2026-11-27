"""Executed over SSH stdin in EC2; all credentials remain in EC2 memory."""
import gzip
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

root=Path('/home/ubuntu/2026-SSAFY-final')
sys.path.insert(0,str(root/'src'))
from subway_collect.config import Config,load_keys,source_time
from subway_collect.http import Client
from subway_collect.storage import Store
from subway_collect.live_cache import LiveFeedCache
from subway_collect.providers import arrival_url,arrival_response,position_url,position_response


class ExistingStore(Store):
    def __init__(self):
        # The collector owns schema/migrations. Do not run them on web requests.
        self.root=root
        self.data=root/'data'
        self.db=sqlite3.connect(self.data/'subway.sqlite3',timeout=5)


def recent_collector_arrivals(store, current):
    record=store.db.execute("SELECT started_at,raw_path FROM requests WHERE dataset='arrival_samples' AND outcome='success' AND raw_path IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    if record and 0 <= (current-datetime.fromisoformat(record[0])).total_seconds() < 15:
        try:
            with gzip.open(store.root/record[1],'rt',encoding='utf-8') as f: envelope=json.load(f)
            stamp=envelope.get('collected_at',record[0])
            received=source_time(stamp)
            if received is None or not 0 <= (current-received).total_seconds() < 15:
                return None
            rows,total=arrival_response(json.loads(envelope['body']))
            return dict(rows=rows,total=total,collected_at=stamp,checked_at=stamp)
        except (OSError,ValueError,KeyError,TypeError,EOFError):
            pass
    return None


def collect_snapshot(store, config, client, clock):
    cache=LiveFeedCache(store,clock=clock)
    # User requests share only the latest response, not a permanent raw archive.
    entry,cached=cache.get('arrivals',lambda:client.fetch(
        'seoul_subway','web_arrivals','ALL',arrival_url(config,client.keys['SEOUL_SUBWAY_API_KEY']),
        arrival_response,attempts=1,archive=False),
        archived=lambda current:recent_collector_arrivals(store,current))
    rows=entry['rows'] or []
    collected=entry['collected_at']
    total=entry['total']
    now=clock()
    # Do not normalize ETA estimates as observed physical train arrivals.
    positions=[];seen=set()
    for target,stamp,raw in store.db.execute("SELECT target,started_at,raw_path FROM requests WHERE id>(SELECT coalesce(max(id),0)-600 FROM requests WHERE dataset='positions') AND dataset='positions' AND outcome='success' AND raw_path IS NOT NULL ORDER BY id DESC"):
        line=target.rsplit(':',1)[0]
        if line in seen or not target.endswith(':1'): continue
        seen.add(line)
        if not 0 <= (now-datetime.fromisoformat(stamp)).total_seconds() <= 90: continue
        with gzip.open(store.root/raw,'rt',encoding='utf-8') as f: body=json.loads(json.load(f)['body'])
        for row in body.get('realtimePositionList',[]):
            positions.append({'line':line,**row})
    gtx='GTX-A'
    if not any(row['line']==gtx and -5<=(now-source_time(row['recptnDt'])).total_seconds()<45 for row in positions if source_time(row.get('recptnDt')) is not None):
        position_entry,_=cache.get('gtx_positions',lambda:client.fetch(
            'seoul_subway','web_positions',gtx+':1',position_url(config,client.keys['SEOUL_SUBWAY_API_KEY'],gtx),
            position_response,attempts=1,archive=False))
        if position_entry['rows'] is not None:
            positions=[row for row in positions if row['line']!=gtx]+[{'line':gtx,**row} for row in position_entry['rows']]
    return {'available':collected is not None,'fetched_at':clock().isoformat(),'collected_at':collected,
            'cached':cached,'arrivals':rows,'positions':positions,'received_rows':len(rows),'source_total':total,
            'error':None if collected else 'live_api_unavailable_or_budget_exhausted'}


def run():
    # Stream state is served locally on EC2; no API key or Redis port crosses SSH.
    try:
        from urllib.request import urlopen
        with urlopen('http://127.0.0.1:8787/snapshot',timeout=2) as response:
            feed=json.load(response)
        print(json.dumps(feed,ensure_ascii=True))
        return
    except (OSError,ValueError):
        pass
    now=datetime.now(timezone(timedelta(hours=9)))
    store=ExistingStore()
    config=Config.load(root=root)
    config.values['request_timeout_seconds']=6
    client=Client(config,store,lambda _:True)
    client.keys=load_keys(root)
    try:
        print(json.dumps(collect_snapshot(store,config,client,lambda:datetime.now(now.tzinfo)),ensure_ascii=True))
    finally:
        store.close()


if __name__=='__main__':
    try:
        run()
    except Exception:
        # No exception URLs or stderr containing credentials may cross SSH.
        print(json.dumps({'available':False,'arrivals':[],'positions':[],'error':'remote_live_unavailable'}))
