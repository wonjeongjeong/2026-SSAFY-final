"""수동으로 내려받은 공공데이터포털 CSV(정적 기준 데이터)를 원본 그대로 적재한다.

공공데이터포털 파일데이터는 내려받기 주소가 고정돼 있지 않아 자동 수집하지 않는다.
컬럼 이름을 추측해 바꾸지 않고, 한 행을 원래 컬럼 그대로 JSON으로 보존한다.
같은 파일(sha256)을 다시 넣으면 건너뛰고, 새 버전 파일은 별도 버전으로 남는다.
"""
from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

from .config import now
from .storage import dumps

# 데이터셋 이름 → 기획서에서 쓰이는 곳
DATASETS = {
    'segment_times': '역간 소요 시간 (서울교통공사_역간거리 및 소요시간): 그래프 엣지 가중치',
    'transfer_times': '환승 소요 시간 (서울교통공사_환승역거리 소요시간 정보): 환승 엣지·환승 성공 확률 입력',
    'station_coords': '역 목록·좌표 (서울교통공사_1_8호선 역사 좌표(위경도) 정보): 그래프 노드·노선도·역명 검증',
    'train_congestion': '열차 혼잡도 (서울교통공사_지하철혼잡도정보): 탑승 실패 대기 규칙',
    # 열린데이터광장 CardSubwayTime(8088 포트)이 학교망에서 막혀 파일로 대체 (2026-11-11)
    'boarding_volume_file': '승하차 인원 (서울교통공사_역별 일별 시간대별 승하차인원 정보): 지연 모델 특성',
}
ENCODINGS = ('utf-8-sig', 'cp949')


def decode(raw):
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError('CSV is neither UTF-8 nor CP949')


def load_reference(store, dataset, path):
    if dataset not in DATASETS:
        raise ValueError(f'unknown dataset {dataset}; choose one of {sorted(DATASETS)}')
    path = Path(path)
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if store.db.execute('SELECT 1 FROM reference_files WHERE sha256=?', (sha,)).fetchone():
        return dict(dataset=dataset, file=path.name, sha256=sha, status='already_loaded')
    text, encoding = decode(raw)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or any(not (name or '').strip() for name in reader.fieldnames):
        raise ValueError('CSV header is missing or has empty column names')
    rows = [{k.strip(): (v or '').strip() for k, v in row.items() if k is not None} for row in reader]
    if not rows:
        raise ValueError('CSV has no data rows')
    with store.db:
        store.db.execute('INSERT INTO reference_files VALUES (?,?,?,?,?,?,?)',
                         (sha, dataset, path.name, encoding, now().isoformat(), len(rows),
                          dumps([c.strip() for c in reader.fieldnames])))
        store.db.executemany('INSERT INTO reference_rows VALUES (?,?,?,?)',
                             [(sha, dataset, i, dumps(row)) for i, row in enumerate(rows, 1)])
    return dict(dataset=dataset, file=path.name, sha256=sha, encoding=encoding, rows=len(rows), status='loaded')
