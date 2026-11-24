"""Shared, bounded on-demand real-time feed through the configured SSH alias."""
import json
import os
from pathlib import Path
import subprocess
import threading
import time

_lock=threading.Lock()
_cached=None
_expires=0


def live_snapshot():
    global _cached,_expires
    with _lock:
        if _cached is not None and time.monotonic()<_expires:
            return _cached
        ssh=str(Path(os.environ.get('WINDIR','C:/Windows'))/'System32/OpenSSH/ssh.exe') if os.name=='nt' else 'ssh'
        try:
            p=subprocess.run([ssh,'-T','-o','BatchMode=yes','-o','ConnectTimeout=5','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=1','subway-collector','python3 -'],input=Path(__file__).with_name('live_gateway_remote.py').read_text(encoding='utf-8'),text=True,encoding='utf-8',capture_output=True,timeout=20)
            if p.returncode: raise ValueError('SSH failed')
            _cached=json.loads(p.stdout)
        except (OSError,ValueError,subprocess.TimeoutExpired):
            _cached={'available':False,'arrivals':[],'positions':[],'error':'실시간 연결에 실패했습니다. 시간표 기준 결과를 표시합니다.'}
        _expires=time.monotonic()+(5 if _cached.get('available') else 10)
        return _cached
