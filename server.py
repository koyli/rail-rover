#!/usr/bin/env python3
"""
Local web app for rail-explorer.

Serves the static frontend (index.html, data/*.json) and a JSON reachability
API:

  GET /api/reachable?ticket=<id>&station=<name>&date=YYYY-MM-DD&time=HH:MM

Run: python3 server.py [port]
"""

import json
import sqlite3
import sys
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import reachability

ROOT = Path(__file__).parent


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/reachable":
            self.handle_reachable(urllib.parse.parse_qs(parsed.query))
        else:
            super().do_GET()

    def handle_reachable(self, params):
        try:
            ticket = params["ticket"][0]
            station = params["station"][0]
            date = params["date"][0]
            time_str = params["time"][0]
        except (KeyError, IndexError):
            self.send_error(400, "Missing required query parameters: ticket, station, date, time")
            return

        try:
            conn = sqlite3.connect(reachability.DB_FILE)
            try:
                result = reachability.reachable(ticket, station, date, time_str, conn=conn)
            finally:
                conn.close()
        except ValueError as e:
            self.send_error(400, str(e))
            return

        body = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
