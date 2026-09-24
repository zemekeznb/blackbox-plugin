"""Loopback-only review UI. No external resources, model calls or publication."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import parse_qs, urlsplit

from tas_notes_core import Conflict, NotesError


ASSETS = Path(__file__).resolve().parent.parent / 'assets'


def make_server(store, token=None):
    token = token or secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # URLs and record contents must not enter access logs.

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
            self.wfile.write(body)

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
            static = {'/': ('review.html', 'text/html; charset=utf-8'),
                      '/review.js': ('review.js', 'text/javascript; charset=utf-8'),
                      '/review.css': ('review.css', 'text/css; charset=utf-8')}
            try:
                if request.path in static:
                    name, mime = static[request.path]
                    self.send(200, (ASSETS / name).read_bytes(), mime)
                elif request.path == '/api/records':
                    query = parse_qs(request.query).get('q', [''])[0]
                    self.send(200, store.listing(query))
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
            if self.path != '/api/revisions':
                self.send(404, {'error': 'not_found'})
                return
            if self.headers.get('Content-Type') != 'application/json':
                self.send(415, {'error': 'json_required'})
                return
            try:
                if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                    raise NotesError('invalid framing')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 900_000:
                    raise NotesError('invalid length')
                self.connection.settimeout(5)
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or set(payload) != {'id', 'title', 'body', 'source_seq', 'expected_revision'}:
                    raise NotesError('invalid fields')
                result = store.save_revision(payload['id'], payload['title'], payload['body'],
                                             payload['source_seq'], payload['expected_revision'])
                self.send(200, result)
            except Conflict:
                self.send(409, {'error': 'changed_since_open'})
            except (NotesError, ValueError, TypeError, OSError):
                self.send(400, {'error': 'invalid_request'})
            except sqlite3.Error:
                self.send(503, {'error': 'local_store_unavailable'})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    return server, token


def serve(store):
    server, token = make_server(store)
    print(f'Local review: http://127.0.0.1:{server.server_port}/#token={token}', flush=True)
    print('Keep this process running. Ctrl+C stops the local reviewer.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
