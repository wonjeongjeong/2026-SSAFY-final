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
from subway_collect.providers import arrival_url,arrival_response,position_url,position_response


class ExistingStore(Store):
    def __init__(self):
        # The collector owns schema/migrations. Do not run them on web requests.
        self.root=root
        self.data=root/'data'
        self.db=sqlite3.connect(self.data/'subway.sqlite3',timeout=5)


def run():
    now=datetime.now(timezone(timedelta(hours=9)))
    store=ExistingStore()
    config=Config.load(root=root)
    config.values['request_timeout_seconds']=6
    client=Client(config,store,lambda _:True)
    client.keys=load_keys(root)
    # Reuse recent collector or web responses across all local browser clients.
    record=store.db.execute("SELECT started_at,raw_path FROM requests WHERE dataset IN ('arrival_samples','web_arrivals') AND outcome='success' ORDER BY id DESC LIMIT 1").fetchone()
    rows=None;collected=None;cached=False
    if record and (now-datetime.fromisoformat(record[0])).total_seconds()<25:
        with gzip.open(root/record[1],'rt',encoding='utf-8') as f: envelope=json.load(f)
        rows,_=arrival_response(json.loads(envelope['body']))
        collected=envelope.get('collected_at',record[0]);cached=True
    if rows is None:
        result=client.fetch('seoul_subway','web_arrivals','ALL',arrival_url(config,client.keys['SEOUL_SUBWAY_API_KEY']),arrival_response,attempts=1)
        if result is not None:
            rows,total=result
            collected=datetime.now(now.tzinfo).isoformat()
        else:
            rows=[];total=0
    else:
        total=len(rows)
    # Do not normalize ETA estimates as observed physical train arrivals.
    # Fresh archived positions supply independent movement/status evidence.
    positions=[];seen=set()
    for target,stamp,raw in store.db.execute("SELECT target,started_at,raw_path FROM requests WHERE id>(SELECT coalesce(max(id),0)-600 FROM requests WHERE dataset IN ('positions','web_positions')) AND dataset IN ('positions','web_positions') AND outcome='success' ORDER BY id DESC"):
        line=target.rsplit(':',1)[0]
        if line in seen or not target.endswith(':1'): continue
        seen.add(line)
        if (now-datetime.fromisoformat(stamp)).total_seconds()>90: continue
        with gzip.open(root/raw,'rt',encoding='utf-8') as f: body=json.loads(json.load(f)['body'])
        for row in body.get('realtimePositionList',[]):
            positions.append({'line':line,**row})
    # GTX-A often has no numeric station ETA. Its fresh station/status anchors
    # are needed independently; one cached line request avoids 19-line polling.
    gtx='GTX-A'
    if not any(row['line']==gtx and -5<=(now-source_time(row['recptnDt'])).total_seconds()<45 for row in positions if source_time(row.get('recptnDt')) is not None):
        result=client.fetch('seoul_subway','web_positions',gtx+':1',position_url(config,client.keys['SEOUL_SUBWAY_API_KEY'],gtx),position_response,attempts=1)
        if result is not None:
            positions=[row for row in positions if row['line']!=gtx]+[{'line':gtx,**row} for row in result[0]]
    print(json.dumps({'available':collected is not None,'fetched_at':datetime.now(now.tzinfo).isoformat(),'collected_at':collected,'cached':cached,'arrivals':rows,'positions':positions,'received_rows':len(rows),'source_total':total,'error':None if collected else 'live_api_unavailable_or_budget_exhausted'},ensure_ascii=True))
    store.close()


if __name__=='__main__':
    try:
        run()
    except Exception:
        # No exception URLs or stderr containing credentials may cross SSH.
        print(json.dumps({'available':False,'arrivals':[],'positions':[],'error':'remote_live_unavailable'}))
