"""Loopback-only HTTP adapter for the team administration emulator prototype.

This is not a production endpoint. It requires both local Firebase emulators,
an exact loopback Origin, a valid emulator ID token, and trusted-service checks.
It never logs request headers or bodies.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import firebase_admin
from firebase_admin import auth, credentials, firestore
from google.auth.credentials import AnonymousCredentials

from functions import team_admin

MAX_BODY = 16 * 1024
UI_ROOT = Path(__file__).resolve().parent / 'team_ui'
ASSETS = {'/team': ('index.html','text/html; charset=utf-8'),
          '/team/app.js': ('app.js','text/javascript; charset=utf-8'),
          '/team/logic.js': ('logic.js','text/javascript; charset=utf-8'),
          '/team/style.css': ('style.css','text/css; charset=utf-8')}


def _check_origin(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'}
                or not parsed.port or parsed.path or parsed.query or parsed.fragment
                or parsed.username is not None or parsed.password is not None):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('An explicit HTTP loopback origin with a port is required.') from None
    return value


def make_server(port, origin, *, db, app):
    team_admin.require_emulators()
    allowed_origin = _check_origin(origin)
    auth_origin = _check_origin('http://' + os.environ['FIREBASE_AUTH_EMULATOR_HOST'])
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Invalid port.')
    if db.project != team_admin.PROJECT_ID or app.project_id != team_admin.PROJECT_ID:
        raise RuntimeError('Refusing a non-emulator project.')

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_args):
            pass  # Never record request paths, headers, tokens or bodies.

        def reply(self, status, payload=None):
            body = b'' if payload is None else json.dumps(payload, separators=(',', ':')).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if self.headers.get('Origin') in self.allowed_origins():
                self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
                self.send_header('Vary', 'Origin')
            self.end_headers()
            if body:
                self.wfile.write(body)

        def allowed_origins(self):
            return {allowed_origin, f'http://127.0.0.1:{self.server.server_port}'}

        def valid_host(self):
            if any(key.lower() == 'forwarded' or key.lower().startswith('x-forwarded-') for key in self.headers.keys()):
                self.reply(400, {'error':'Forwarded requests are not accepted.'}); return False
            expected_hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if len(self.headers.get_all('Host', [])) != 1 or self.headers.get('Host') not in expected_hosts:
                self.reply(403, {'error':'Invalid host.'}); return False
            return True

        def valid_request_boundary(self):
            if not self.valid_host(): return False
            if len(self.headers.get_all('Origin', [])) != 1 or self.headers.get('Origin') not in self.allowed_origins():
                self.reply(403, {'error':'Origin not allowed.'}); return False
            return True

        def do_GET(self):
            if not self.valid_host(): return
            if self.path == '/team/config':
                self.reply(200, {'authOrigin':auth_origin}); return
            asset = ASSETS.get(self.path)
            if asset is None:
                self.reply(404, {'error':'Not found.'}); return
            filename, content_type = asset
            try:
                body = (UI_ROOT / filename).read_bytes()
            except OSError:
                self.reply(503, {'error':'Emulator UI unavailable.'}); return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', f"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self' {auth_origin}; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(body)

        def do_OPTIONS(self):
            if not self.valid_request_boundary(): return
            if self.path != '/team/action' or self.headers.get('Access-Control-Request-Method') != 'POST':
                self.reply(404, {'error':'Not found.'}); return
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
            self.send_header('Access-Control-Allow-Methods', 'POST')
            self.send_header('Access-Control-Allow-Headers', 'authorization,content-type')
            self.send_header('Vary', 'Origin')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_POST(self):
            if not self.valid_request_boundary(): return
            if self.path != '/team/action': self.reply(404, {'error':'Not found.'}); return
            if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                self.reply(415, {'error':'JSON is required.'}); return
            values = self.headers.get_all('Content-Length', [])
            if len(values) != 1 or self.headers.get('Transfer-Encoding'):
                self.reply(400, {'error':'A bounded request body is required.'}); return
            try:
                length = int(values[0])
                if not 1 <= length <= MAX_BODY: raise ValueError
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
            except (ValueError, UnicodeError, socket.timeout):
                self.reply(400, {'error':'Invalid request.'}); return
            headers = self.headers.get_all('Authorization', [])
            if len(headers) != 1 or not headers[0].startswith('Bearer ') or not 1 <= len(headers[0][7:]) <= 12000:
                self.reply(401, {'error':'A valid identity token is required.'}); return
            try:
                result = team_admin.run_action(headers[0][7:], payload, db=db, app=app)
            except (auth.InvalidIdTokenError, auth.UserDisabledError):
                self.reply(401, {'error':'A valid identity token is required.'}); return
            except PermissionError:
                self.reply(403, {'error':'Permission denied.'}); return
            except (ValueError, auth.UserNotFoundError):
                self.reply(400, {'error':'Invalid request or conflict.'}); return
            except Exception:
                self.reply(503, {'error':'Emulator service unavailable.'}); return
            self.reply(200, result)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


class _EmulatorCredentials(credentials.Base):
    def get_credential(self):
        return AnonymousCredentials()


def main():
    parser = argparse.ArgumentParser(description='Start the loopback-only team emulator service')
    parser.add_argument('--emulator', action='store_true', required=True)
    parser.add_argument('--port', type=int, default=8788)
    parser.add_argument('--origin', default='http://127.0.0.1:8765')
    args = parser.parse_args()
    team_admin.require_emulators()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    app = firebase_admin.initialize_app(_EmulatorCredentials(), {'projectId': team_admin.PROJECT_ID}, name='team-http-emulator')
    server = make_server(args.port, args.origin, db=firestore.client(app), app=app)
    print(f'Team emulator service listening on 127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        firebase_admin.delete_app(app)


if __name__ == '__main__':
    main()
