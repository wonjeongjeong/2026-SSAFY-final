"""Copy a compact, secret-free timetable snapshot from the existing EC2 DB."""
import base64
import gzip
import json
import os
from pathlib import Path
import subprocess
import argparse
from contextlib import redirect_stdout
import io

ROOT=Path(__file__).resolve().parents[1]
REMOTE=r'''
import base64,gzip,json,sqlite3
from pathlib import Path
r=Path('/home/ubuntu/2026-SSAFY-final')
s=json.loads((r/'data/status.json').read_text())
c=sqlite3.connect((r/'data/subway.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=10)
date=s['date']
columns=['line','number','station','direction','arrival','departure','day_type','branch','origin','destination','express','valid_from','valid_until','temporary']
rows=c.execute("""select line,train_no,station_name,direction,arrival_time,departure_time,day_type,
json_extract(payload_json,'$.brlnNm'),json_extract(payload_json,'$.dptreStnNm'),json_extract(payload_json,'$.arvlStnNm'),
json_extract(payload_json,'$.etrnYn'),json_extract(payload_json,'$.vldBgngDt'),json_extract(payload_json,'$.vldEndDt'),json_extract(payload_json,'$.tmprTmtblYn')
from timetable where fetched_date=?""",(date,)).fetchall()
doc={'service_date':date,'source':'https://www.data.go.kr/data/15143847/openapi.do','columns':columns,'rows':rows}
print(base64.b64encode(gzip.compress(json.dumps(doc,ensure_ascii=False,separators=(',',':')).encode())).decode())
'''

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--local',action='store_true',help='Run on EC2 itself without SSH')
    args=parser.parse_args()
    ssh=str(Path(os.environ.get('WINDIR','C:/Windows'))/'System32/OpenSSH/ssh.exe') if os.name=='nt' else 'ssh'
    if args.local:
        output=io.StringIO()
        with redirect_stdout(output):
            exec(compile(REMOTE,'<local-timetable-export>','exec'),{})
        encoded=output.getvalue()
    else:
        p=subprocess.run([ssh,'-T','-o','BatchMode=yes','-o','ConnectTimeout=8','subway-collector','python3 -'],input=REMOTE,text=True,encoding='utf-8',capture_output=True,timeout=90)
        if p.returncode:
            raise SystemExit('Could not read the EC2 timetable; check the SSH connection.')
        encoded=p.stdout
    content=base64.b64decode(encoded.strip(),validate=True)
    doc=json.loads(gzip.decompress(content))
    if not doc['rows']:
        raise SystemExit('No timetable rows returned; existing cache preserved.')
    folder=ROOT/'services'/'web'/'data';folder.mkdir(parents=True,exist_ok=True)
    out=folder/f"timetable-{doc['service_date']}.json.gz"
    out.with_suffix('.tmp').write_bytes(content)
    out.with_suffix('.tmp').replace(out)
    print(json.dumps({'date':doc['service_date'],'rows':len(doc['rows']),'bytes':len(content),'path':str(out)},ensure_ascii=True))

if __name__=='__main__':
    main()
