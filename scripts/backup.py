"""Consistent SQLite backup plus archived raw data/config; may contain private keys."""
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / 'backups'
    out.mkdir(exist_ok=True)
    target = out / f'subway-private-{datetime.now():%Y%m%d-%H%M%S}.zip'
    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp) / 'subway.sqlite3'
        source = ROOT / 'data' / 'subway.sqlite3'
        if source.exists():
            with sqlite3.connect(f'{source.as_uri()}?mode=ro', uri=True) as src, sqlite3.connect(snapshot) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('SQLite integrity check failed')
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            if snapshot.exists():
                archive.write(snapshot, 'data/subway.sqlite3')
            for name in ('.env', 'config/collector.json'):
                path = ROOT / name
                if path.exists():
                    archive.write(path, name)
            for folder in ('data/raw', 'data/exports', 'data/reference'):
                path = ROOT / folder
                if path.exists():
                    for file in path.rglob('*'):
                        if file.is_file():
                            archive.write(file, file.relative_to(ROOT))
    print(target)
    print('Private backup may contain API keys. Copy it off this PC before resetting Windows.')


if __name__ == '__main__':
    main()
