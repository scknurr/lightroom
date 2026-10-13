"""Local helper for the review pages (127.0.0.1 only).

POST /api/private-cleared {"ids": [...]}  -> adds ids to private_overrides.json, then rebuilds the pages
GET  /api/status                          -> {"building": bool, "last": "...", "cleared": N}
"""
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from config import NOT_MINE, PRIVATE_OVERRIDES, WORK, cleared_private, not_mine

HERE = Path(__file__).resolve().parent
PY = sys.executable
STATE = {'building': False, 'last': '', 'error': ''}
LOCK = threading.Lock()


def rebuild(steps=None):
    steps = steps or [['private_page.py'], ['library_page.py'], ['ranked_page.py', str(WORK / 'rank' / 'finals.json')]]
    with LOCK:
        STATE.update(building=True, error='')
        try:
            for args in steps:
                r = subprocess.run([PY, *args], cwd=HERE, capture_output=True, text=True)
                if r.returncode:
                    raise RuntimeError(f'{args[0]}: {r.stderr[-400:]}')
            STATE['last'] = time.strftime('%H:%M:%S')
        except Exception as e:
            STATE['error'] = str(e)
        finally:
            STATE['building'] = False


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()

    def do_GET(self):
        if self.path == '/api/status':
            return self._send(200, {**STATE, 'cleared': len(cleared_private())})
        self._send(404, {'error': 'not found'})

    def do_POST(self):
        if self.path == '/api/not-mine':
            try:
                n = int(self.headers.get('Content-Length', 0))
                ids = {int(i) for i in json.loads(self.rfile.read(min(n, 5_000_000)))['ids']}
            except Exception as e:
                return self._send(400, {'error': f'bad request: {e}'})
            merged = sorted(not_mine() | ids)
            NOT_MINE.write_text(json.dumps({'ids': merged, 'updated': time.strftime('%Y-%m-%d %H:%M:%S'), 'source': 'owner, authorship review'}))
            threading.Thread(target=rebuild, args=([['portfolio_refine.py'], ['authorship_page.py']],), daemon=True).start()
            return self._send(200, {'ok': True, 'total': len(merged)})
        if self.path != '/api/private-cleared':
            return self._send(404, {'error': 'not found'})
        try:
            n = int(self.headers.get('Content-Length', 0))
            ids = json.loads(self.rfile.read(min(n, 5_000_000)))['ids']
            ids = {int(i) for i in ids}
        except Exception as e:
            return self._send(400, {'error': f'bad request: {e}'})
        before = cleared_private()
        merged = sorted(before | ids)
        PRIVATE_OVERRIDES.write_text(json.dumps({'cleared': merged, 'updated': time.strftime('%Y-%m-%d %H:%M:%S')}))
        threading.Thread(target=rebuild, daemon=True).start()
        self._send(200, {'ok': True, 'added': len(set(merged) - before), 'cleared': len(merged)})

    def log_message(self, fmt, *a):
        sys.stderr.write('%s %s\n' % (time.strftime('%H:%M:%S'), fmt % a))


if __name__ == '__main__':
    print('rescue helper on http://127.0.0.1:8765', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8765), H).serve_forever()
