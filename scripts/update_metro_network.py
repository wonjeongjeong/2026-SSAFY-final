"""Refresh the bundled factual 19-line network from Seoul Metro, without JS execution.

Run from any folder: python scripts/update_metro_network.py
The local app never needs network access to load the resulting snapshot.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from urllib.request import urlopen

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services'/'web'))
from line_catalog import EXTRA_LINES

URL='https://www.seoulmetro.co.kr/kr/getLineData.do'
OUTPUT=Path(__file__).resolve().parents[1]/'services'/'web'/'metro_source.json'
FIELDS={'station-cd','station-nm','data-uid','data-coords','data-moveTo','data-labelPos'}


def main():
    text=urlopen(URL,timeout=20).read().decode('utf-8')
    # The endpoint is a data-only JS variable with trailing commas. Parse its
    # object as JSON; never eval downloaded JavaScript.
    match=re.search(r'var\s+lines\s*=\s*',text)
    if not match:
        raise ValueError('Unexpected source format; current snapshot preserved')
    body=text[match.end():text.rfind('}')+1]
    data=json.loads(re.sub(r',\s*([}\]])',r'\1',body))
    snapshot={}
    keys=[f'{number+1}-{number}' for number in range(1,10)]+[row[0] for row in EXTRA_LINES.values()]
    for key in keys:
        rows=data[key]['stations']
        if not any(row.get('station-nm') for row in rows):
            raise ValueError(f'Line {key} has no stations; snapshot preserved')
        snapshot[key]={'stations':[{k:v for k,v in row.items() if k in FIELDS} for row in rows]}
    temporary=OUTPUT.with_suffix('.tmp')
    temporary.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(OUTPUT)
    print(OUTPUT)
    print('Restart the local web server and run its network/planner tests.')


if __name__=='__main__':
    main()
