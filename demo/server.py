#!/usr/bin/env python3
"""Loopback-only server for the isolated fictional dashboard demo."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILES = {'/': ('index.html', 'text/html; charset=utf-8'), '/index.html': ('index.html', 'text/html; charset=utf-8'), '/demo.css': ('demo.css', 'text/css; charset=utf-8'), '/demo.js': ('demo.js', 'text/javascript; charset=utf-8')}
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        host = self.headers.get('Host', '').split(':', 1)[0].lower()
        if host not in {'127.0.0.1', 'localhost'}:
            self.send_error(403); return
        path = self.path.split('?', 1)[0]
        if path == '/__demo_session':
            body = json.dumps({'demo': True, 'version': 1}).encode()
            content_type = 'application/json'
        elif path in FILES:
            name, content_type = FILES[path]
            body = (ROOT / name).read_bytes()
        else:
            self.send_error(404); return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *_args): pass

def main():
    parser = argparse.ArgumentParser(description='Start the isolated demo on loopback only')
    parser.add_argument('--demo', action='store_true', required=True, help='Explicitly enable fictional local demo')
    parser.add_argument('--port', type=int, default=8770)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535: parser.error('port must be between 1024 and 65535')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Demo: http://127.0.0.1:{args.port}/', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__ == '__main__': main()
