"""Official shortest-transfer walking times plus explicit user margins."""
import csv
from pathlib import Path
import re
from functools import lru_cache

from timetable_data import BY_LINE_NAME,STATIONS,station_id

SOURCE='https://www.data.go.kr/data/15044419/fileData.do'
_table={}
_file=Path(__file__).parent/'import_sources'/'transfer_20260831.csv'
if _file.exists():
    with _file.open(encoding='cp949',newline='') as f:
        for row in csv.DictReader(f):
            a=BY_LINE_NAME.get(row['호선']+'호선');b=BY_LINE_NAME.get(row['환승노선'])
            sid=station_id(a,row['환승역명'])
            value=row['환승소요시간'].strip()
            if a and b and sid and re.fullmatch(r'\d+:\d{2}',value):
                m,s=map(int,value.split(':'));_table[(sid,a,b)]=m*60+s


class TransferPolicy:
    def __init__(self,extra_seconds=120,overrides=None,boarding_seconds=30):
        self.extra_seconds=extra_seconds
        self.overrides=overrides or {}
        self.boarding_seconds=boarding_seconds

    @lru_cache(maxsize=4096)
    def get(self,station,from_line,to_line,from_direction='',to_direction=''):
        exact=f'{station}:{from_line}:{to_line}:{from_direction}:{to_direction}'
        pair=f'{station}:{from_line}:{to_line}'
        if exact in self.overrides or pair in self.overrides:
            walk=self.overrides[exact] if exact in self.overrides else self.overrides[pair]
            basis='user';source=None;inferred=False
        elif (station,from_line,to_line) in _table:
            walk=_table[(station,from_line,to_line)];basis='official_shortest';source=SOURCE;inferred=False
        elif (station,to_line,from_line) in _table:
            walk=_table[(station,to_line,from_line)];basis='official_reverse_assumption';source=SOURCE;inferred=True
        else:
            walk=180 if from_line==to_line else 300
            basis='unverified_assumption';source=None;inferred=True
        return {'station':station,'from_line':from_line,'to_line':to_line,
            'from_direction':from_direction,'to_direction':to_direction,
            'walk_seconds':walk,'extra_seconds':self.extra_seconds,
            'boarding_seconds':self.boarding_seconds,
            'total_seconds':walk+self.extra_seconds+self.boarding_seconds,
            'basis':basis,'source':source,'inferred':inferred}
