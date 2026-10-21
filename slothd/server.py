"""slothd — HTTP surface for the dashboard plus a tiny CLI.

Nothing here talks to the network. The only socket the worker opens is
127.0.0.1:11434, where the local model lives.
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .runner import Runner

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The dashboard ships inside the package so an installed copy still has it.
UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "index.html")

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    runner: Runner  # set in main()

    def log_message(self, fmt, *args):  # keep the console calm
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            try:
                with open(UI, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                return self._send(500, {"error": "ui missing"})
        if p == "/api/state":
            return self._send(200, self.runner.snapshot())
        if p == "/api/ledger":
            return self._send(200, self.runner.store.entries(limit=200)[::-1])
        if p == "/healthz":
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/pause":
            self.runner.pause()
            return self._send(200, {"paused": True})
        if p == "/api/resume":
            self.runner.resume()
            return self._send(200, {"paused": False})
        if p == "/api/undo":
            n = self.runner.store.undo_last_run(everything="all=1" in self.path)
            self.runner.queue = []
            return self._send(200, {"restored": n})
        return self._send(404, {"error": "not found"})


def main(argv=None):
    ap = argparse.ArgumentParser(prog="slothd")
    ap.add_argument("--inbox", default=os.path.expanduser("~/Downloads"),
                    help="folder of things you keep putting off")
    ap.add_argument("--var", default=os.path.join(HERE, "var"),
                    help="where the ledger, state and sorted/ live")
    ap.add_argument("--model", default="gemma3:4b")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--digest", action="store_true", help="print the digest and exit")
    ap.add_argument("--undo", action="store_true", help="restore the last run's files")
    ap.add_argument("--undo-all", action="store_true", help="restore everything in the ledger")
    ap.add_argument("--once", action="store_true",
                    help="file everything waiting right now and exit (for cron)")
    args = ap.parse_args(argv)

    os.makedirs(args.var, exist_ok=True)
    Handler.runner = Runner(args.inbox, args.var, model=args.model)

    if args.digest:
        print("\n".join(Handler.runner.store.digest()))
        return 0
    if args.once:
        print(f"filed {Handler.runner.drain()} files")
        return 0
    if args.undo or args.undo_all:
        n = Handler.runner.store.undo_last_run(everything=args.undo_all)
        print(f"restored {n} files")
        return 0

    Handler.runner.start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"slothd · inbox={Handler.runner.inbox} · outbox={Handler.runner.outbox} · "
          f"model={args.model} · http://127.0.0.1:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        Handler.runner.stop()
        print("\nok, back to sleep")
    return 0


if __name__ == "__main__":
    sys.exit(main())
