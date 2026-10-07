"""Loopback-only review UI. No external resources, model calls or publication."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import threading
from urllib.parse import parse_qs, urlsplit

from tas_notes_core import NotesError


ASSETS = Path(__file__).resolve().parent.parent / 'assets'


def make_server(store, token=None):
    token = token or secrets.token_urlsafe(32)
    from tas_notes_launcher import runtime_metadata
    runtime = runtime_metadata(store)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # URLs and record contents must not enter access logs.

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def send(self, status, body, mime='application/json; charset=utf-8'):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return  # Closing a tab during a response is not a store failure.
            if self.command == 'POST' and not getattr(self, 'body_consumed', False):
                self.drain_rejected_body()

        def drain_rejected_body(self):
            # A close with unread incoming bytes can discard the 401/403 on Windows.
            # Send the rejection first; discard only a small, correctly framed body.
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                return
            try:
                remaining = int(self.headers['Content-Length'])
                if not 0 < remaining <= 65536:
                    return
                deadline = time.monotonic() + 0.2
                while remaining and time.monotonic() < deadline:
                    self.connection.settimeout(max(0.001, deadline - time.monotonic()))
                    chunk = self.rfile.read1(min(8192, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except (OSError, ValueError):
                pass

        def check(self, authorized=False):
            expected = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != expected:
                self.send(403, {'error': 'invalid_host'})
                return False
            origin = self.headers.get('Origin')
            if origin is not None and origin != f'http://{expected}':
                self.send(403, {'error': 'invalid_origin'})
                return False
            if authorized and not secrets.compare_digest(self.headers.get('Authorization', '').encode('utf-8'), f'Bearer {token}'.encode('utf-8')):
                self.send(401, {'error': 'review_token_required'})
                return False
            return True

        def do_GET(self):
            request = urlsplit(self.path)
            if not self.check(request.path.startswith('/api/')):
                return
            static = {'/': ('anomalies.html', 'text/html; charset=utf-8'),
                      '/history': ('review.html', 'text/html; charset=utf-8'),
                      '/anomalies.js': ('anomalies.js', 'text/javascript; charset=utf-8'),
                      '/review.js': ('review.js', 'text/javascript; charset=utf-8'),
                      '/review.css': ('review.css', 'text/css; charset=utf-8')}
            try:
                if request.path in static:
                    name, mime = static[request.path]
                    self.send(200, (ASSETS / name).read_bytes(), mime)
                elif request.path == '/api/health':
                    self.send(200, runtime)
                elif request.path == '/favicon.ico':
                    self.send(204, b'', 'image/x-icon')
                elif request.path == '/api/records':
                    query = parse_qs(request.query).get('q', [''])[0]
                    self.send(200, store.listing(query))
                elif request.path == '/api/status':
                    value = store.status()
                    value['application'] = dict(runtime, state_directory=runtime['state_dir'])
                    self.send(200, value)
                elif request.path == '/api/anomalies':
                    from tas_notes_anomalies import listing
                    query = parse_qs(request.query)
                    self.send(200, listing(store, **{key: query.get(key, [''])[0]
                        for key in ('project', 'tool', 'start', 'end', 'before', 'through')}))
                elif request.path.startswith('/api/anomaly/') or request.path.startswith('/api/anomaly-export/'):
                    from tas_notes_anomalies import detail, RULE_VERSION
                    query = parse_qs(request.query)
                    if query.get('rule', [RULE_VERSION])[0] != RULE_VERSION:
                        raise NotesError('rule changed; reopen record')
                    result = detail(store, request.path.rsplit('/', 1)[1], query.get('through', [None])[0])
                    if request.path.startswith('/api/anomaly-export/'):
                        self.send(200, result['markdown'].encode('utf-8'), 'text/markdown; charset=utf-8')
                    else:
                        self.send(200, result)
                elif request.path.startswith('/api/record/'):
                    self.send(200, store.detail(request.path.removeprefix('/api/record/')))
                elif request.path.startswith('/api/export/'):
                    body = store.export_revision(request.path.removeprefix('/api/export/'))
                    self.send(200, body.encode('utf-8'), 'text/markdown; charset=utf-8')
                else:
                    self.send(404, {'error': 'not_found'})
            except NotesError:
                self.send(400, {'error': 'invalid_request'})
            except (sqlite3.Error, OSError):
                self.send(503, {'error': 'local_store_unavailable'})

        def do_POST(self):
            if not self.check(True):
                return
            if self.path == '/api/revisions':
                self.send(410, {'error': 'history_is_read_only'})
                return
            if self.path != '/api/shutdown':
                self.send(404, {'error': 'not_found'})
                return
            if (self.headers.get('Transfer-Encoding')
                    or len(self.headers.get_all('Content-Length', [])) > 1
                    or self.headers.get('Content-Length', '0') != '0'):
                self.send(400, {'error': 'shutdown_requires_empty_body'})
                return
            self.body_consumed = True
            self.send(200, {'stopping': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    return server, token


def serve(store, ready_file=None):
    server, token = make_server(store)
    url = f'http://127.0.0.1:{server.server_port}/#token={token}'
    if ready_file is not None:
        Path(ready_file).write_text(json.dumps({'url': url, 'pid': os.getpid()}), encoding='utf-8')
    else:
        print(f'Local review: {url}', flush=True)
        print('Keep this process running. Ctrl+C stops the local reviewer.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
