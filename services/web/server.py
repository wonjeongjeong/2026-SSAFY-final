"""Loopback-only local app: python services/web/server.py [--port 5173]."""
from __future__ import annotations

import argparse
from datetime import datetime
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit

from network import NETWORK
from planner import KST,recommend
from remote_snapshot import snapshot

PUBLIC = Path(__file__).parent/'public'


class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(PUBLIC),**kwargs)

    def send_json(self,value,status=200):
        body=json.dumps(value,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/api/network':
            return self.send_json(NETWORK)
        if path=='/api/health':
            return self.send_json(dict(status='ok',mode='realtime_timetable',time_kst=datetime.now(KST).isoformat()))
        if path=='/api/collector':
            return self.send_json(snapshot())
        if path.startswith('/api/'):
            return self.send_json({'error':'요청한 API가 없습니다.'},404)
        return super().do_GET()

    def do_POST(self):
        if urlsplit(self.path).path!='/api/recommendations':
            return self.send_json({'error':'요청한 API가 없습니다.'},404)
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=16384:
                return self.send_json({'error':'요청 크기를 확인해 주세요.'},400)
            payload=json.loads(self.rfile.read(size))
            if not isinstance(payload,dict):
                raise ValueError('입력 형식을 확인해 주세요.')
            result=recommend(payload)
        except (ValueError,TypeError,OverflowError) as error:
            message=str(error) if isinstance(error,ValueError) and not isinstance(error,json.JSONDecodeError) else '입력 형식을 확인해 주세요.'
            return self.send_json({'error':message},400)
        return self.send_json(result)


def main():
    parser=argparse.ArgumentParser(description='여유: 로컬 지하철 출발 추천')
    parser.add_argument('--port',type=int,default=5173)
    args=parser.parse_args()
    with ThreadingHTTPServer(('127.0.0.1',args.port),Handler) as server:
        print(f'여유 local: http://127.0.0.1:{args.port}',flush=True)
        print('실제 시간표 + 실시간 ETA · 승강장 기준 · 종료: Ctrl+C',flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__=='__main__':
    main()
