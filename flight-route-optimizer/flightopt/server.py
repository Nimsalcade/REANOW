"""Local web app and JSON API (standard library HTTP server, binds to localhost by default)."""
from __future__ import annotations

import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlsplit

from .planner import Engine, SearchRequest

STATIC = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8')}


def _one(q, k, default=None):
    v = q.get(k)
    return v[0] if v and v[0] != '' else default


def make_handler(engine: Engine):
    web = resources.files('flightopt') / 'web'

    class Handler(BaseHTTPRequestHandler):
        server_version = 'flightopt'

        def log_message(self, fmt, *args):  # keep the console quiet except for errors
            pass

        def _send(self, code, body: bytes, ctype='application/json'):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, default=str).encode())

        def do_GET(self):
            u = urlsplit(self.path)
            if u.path in STATIC:
                name, ctype = STATIC[u.path]
                return self._send(200, (web / name).read_bytes(), ctype)
            q = parse_qs(u.query)
            try:
                with engine.lock:
                    if u.path == '/api/status':
                        return self._json(engine.status())
                    if u.path == '/api/airports':
                        served = None
                        if _one(q, 'date'):
                            import datetime as dt
                            served = engine.timetable(dt.date.fromisoformat(_one(q, 'date'))).served
                        return self._json(engine.airports.search(_one(q, 'q', ''), int(_one(q, 'limit', 12)), served))
                    if u.path == '/api/served':
                        import datetime as dt
                        tt = engine.timetable(dt.date.fromisoformat(_one(q, 'date')))
                        out = []
                        for c in sorted(tt.served):
                            a = engine.airports.get(c)
                            if a:
                                out.append([c, a.lat, a.lon, a.name, a.city, a.state])
                        return self._json(out)
                    if u.path == '/api/search':
                        stops = _one(q, 'stops')
                        req = SearchRequest(
                            origin=_one(q, 'origin', ''), destination=_one(q, 'destination', ''), date=_one(q, 'date', ''),
                            depart_after=_one(q, 'after', '00:00'), depart_before=_one(q, 'before', '23:59'),
                            arrive_by=_one(q, 'by'), max_stops=None if stops in (None, 'auto') else int(stops),
                            profile=_one(q, 'profile', 'balanced'), value_of_time=float(_one(q, 'vot', 50)),
                            min_connection=int(_one(q, 'min_connection')) if _one(q, 'min_connection') else None,
                            nearby_miles=float(_one(q, 'nearby', 0)), allow_separate_tickets=_one(q, 'separate') == '1',
                            avoid_carriers=[c for c in _one(q, 'avoid', '').replace(',', ' ').split() if c],
                            carriers=[c for c in _one(q, 'carriers', '').replace(',', ' ').split() if c],
                            results=min(int(_one(q, 'results', 8)), 25), samples=min(int(_one(q, 'samples', 2000)), 10000))
                        return self._json(engine.search(req))
                    if u.path == '/api/route':
                        return self._json(engine.route_report(_one(q, 'origin', ''), _one(q, 'destination', ''),
                                                              _one(q, 'date')))
                    if u.path == '/api/hubs':
                        return self._json(engine.hubs(_one(q, 'date'), int(_one(q, 'limit', 20))))
                    if u.path == '/api/opportunities':
                        return self._json(engine.opportunities(_one(q, 'date'), int(_one(q, 'limit', 25))))
            except (ValueError, KeyError, TypeError) as e:
                return self._json(dict(error=str(e)), 400)
            except Exception as e:  # report, do not crash the server
                traceback.print_exc()
                return self._json(dict(error=f'internal error: {e}'), 500)
            self._json(dict(error='not found'), 404)

    return Handler


def serve(engine: Engine, host: str = '127.0.0.1', port: int = 8765):
    httpd = ThreadingHTTPServer((host, port), make_handler(engine))
    print(f'flightopt web app on http://{host}:{port}  (Ctrl+C to stop)', flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
