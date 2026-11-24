"""Read a small collector snapshot over the user's existing SSH alias.

No AWS/API credentials reach the browser; no collector files are modified.
Latest raw responses avoid expensive scans of the positions table.
"""
import json
import os
from pathlib import Path
import subprocess
import threading
import time

SCRIPT = r'''
import gzip,json,sqlite3,subprocess
from pathlib import Path
from datetime import datetime,timedelta,timezone
r=Path('/home/ubuntu/2026-SSAFY-final')
s=json.loads((r/'data/status.json').read_text())
c=sqlite3.connect((r/'data/subway.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=5)
c.set_progress_handler(lambda: 1 if __import__('time').monotonic()>deadline else 0,10000)
deadline=__import__('time').monotonic()+8
latest={}
for target,stamp,raw in c.execute("SELECT target,started_at,raw_path FROM requests WHERE id>(SELECT coalesce(max(id),0)-500 FROM requests WHERE dataset='positions') AND dataset='positions' AND outcome='success' ORDER BY id DESC"):
 line=target.rsplit(':',1)[0]
 if line in latest or not target.endswith(':1') or not raw: continue
 path=(r/raw).resolve()
 if not path.is_relative_to(r/'data'/'raw'): continue
 with gzip.open(path,'rt',encoding='utf-8') as f: envelope=json.load(f)
 body=envelope['body']
 body=json.loads(body) if isinstance(body,str) else body
 rows=body.get('realtimePositionList',[])
 latest[line]={'line':line,'collected_at':envelope.get('collected_at',stamp),'trains':[{'train_no':str(row.get('trainNo','')),'station_name':row.get('statnNm',''),'direction':row.get('updnLine'),'status':row.get('trainSttus'),'express_code':str(row.get('directAt','')),'observed_at':row.get('recptnDt'),'destination':row.get('statnTnm','')} for row in rows]}
now=datetime.now(timezone(timedelta(hours=9)))
sessions=[]
for day,opening,closing,label in [(now.date()-timedelta(days=1),'17:30','20:30','previous_evening'),(now.date(),'06:30','09:30','today_morning')]:
 a=f'{day}T{opening}:00+09:00';b=f'{day}T{closing}:00+09:00'
 rows=c.execute("SELECT target,sum(row_count),min(started_at),max(started_at) FROM requests WHERE date_kst=? AND dataset='positions' AND outcome='success' AND started_at>=? AND started_at<? GROUP BY target",(str(day),a,b)).fetchall()
 sessions.append({'label':label,'date':str(day),'lines':[{'line':target.rsplit(':',1)[0],'received_rows':count,'first_at':first,'last_at':last} for target,count,first,last in rows]})
print(json.dumps({'source':'ec2','fetched_at':now.isoformat(),'service':subprocess.check_output(['systemctl','is-active','subway-collector.service'],text=True).strip(),'active_window':s.get('active_window'),'status_at':s['time_kst'],'today_rows':s.get('rows',{}),'lines':list(latest.values()),'sessions':sessions},ensure_ascii=True))
'''

_lock=threading.Lock()
_value=None
_expires=0


def snapshot():
    global _value,_expires
    with _lock:
        if time.monotonic()<_expires:
            return _value
        ssh=str(Path(os.environ.get('WINDIR','C:/Windows'))/'System32/OpenSSH/ssh.exe') if os.name=='nt' else 'ssh'
        try:
            result=subprocess.run([ssh,'-T','-o','BatchMode=yes','-o','ConnectTimeout=5',
                '-o','ServerAliveInterval=5','-o','ServerAliveCountMax=1',
                'subway-collector','python3 -'],input=SCRIPT,text=True,
                encoding='utf-8',capture_output=True,timeout=20)
            if result.returncode:
                raise ValueError('SSH failed')
            _value=json.loads(result.stdout)
            _value['available']=True
        except (OSError,ValueError,subprocess.TimeoutExpired):
            # Do not expose remote stderr, paths, account or credential details.
            _value={'available':False,'error':'EC2 수집기에 연결하지 못했습니다. SSH 연결 상태를 확인해 주세요.'}
        _expires=time.monotonic()+30
        return _value
