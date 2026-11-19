"""Acquire missing line timetables using the EC2 key and existing request budget.

No secrets leave EC2. Successful source rows use the collector's existing
normalization contract, so the ordinary sync script can export them afterwards.
"""
from pathlib import Path
import os
import subprocess

ROOT=Path(__file__).resolve().parents[1]
BODY=r'''
from subway_collect.config import DIRECTION_CODES
from subway_collect.providers import public_url,public_response
from subway_collect.storage import identity,dumps
store=ExistingStore();config=Config.load(root=root);config.values['request_timeout_seconds']=10
client=Client(config,store,lambda _:True);client.keys=load_keys(root)
now=datetime.now(timezone(timedelta(hours=9)));date=now.date().isoformat()
done={r[0] for r in store.db.execute('select distinct line from timetable where fetched_date=?',(date,))}
dtype='공휴일' if now.weekday()==6 else '주말' if now.weekday()==5 else '평일'
queries=[(line,line) for line in config.values['lines']]
queries.extend([('경의중앙선','경의'),('경의중앙선','중앙'),('우이신설선','우이')])
for line,query in queries:
 if line in done: continue
 saved=0;source_count=0
 for direction in ('상행','하행'):
  for daytype in (dtype,'상시'):
   for temporary in ('N','Y'):
    page=1
    while True:
     params=dict(pageNo=page,numOfRows=1000,tmprTmtblYn=temporary,upbdnbSe=direction,wkndSe=daytype,lineNm=query,searchDt=now.strftime('%Y-%m-%dT%H:%M:%S'))
     out=client.fetch('data_go_kr','web_timetable',f'{line}:{direction}:{daytype}:{temporary}:{page}',public_url('schedule','getTrainSch',client.keys['DATA_GO_KR_API_KEY'],params),public_response,attempts=1)
     if out is None: break
     rows,total=out;source_count+=len(rows)
     with store.db:
      for row in rows:
       saved+=store.insert('timetable',dict(event_id=identity([date,row]),fetched_date=date,line=line,train_no=str(row.get('trainno','')),station_id=row.get('stnCd'),station_name=row.get('stnNm'),direction=DIRECTION_CODES.get(row.get('upbdnbSe',direction),row.get('upbdnbSe')),day_type=row.get('wkndSe',daytype),arrival_time=row.get('trainArvlTm'),departure_time=row.get('trainDptreTm'),payload_json=dumps(row)))
     if page*1000>=total or not rows: break
     page+=1
 print(json.dumps({'line':line,'source_rows':source_count,'inserted':saved},ensure_ascii=True),flush=True)
store.close()
'''

def main():
    prefix=(ROOT/'services/web/live_gateway_remote.py').read_text(encoding='utf-8').split("if __name__=='__main__':")[0]
    ssh=str(Path(os.environ.get('WINDIR','C:/Windows'))/'System32/OpenSSH/ssh.exe') if os.name=='nt' else 'ssh'
    p=subprocess.Popen([ssh,'-T','-o','BatchMode=yes','-o','ConnectTimeout=8','subway-collector','python3 -u -'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8')
    p.stdin.write(prefix+'\n'+BODY);p.stdin.close()
    for line in p.stdout:
        print(line.strip(),flush=True)
    return p.wait()

if __name__=='__main__':
    raise SystemExit(main())
