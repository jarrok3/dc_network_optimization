"""
Build the toy instance (if needed) and serve ui/ on http://127.0.0.1:8000

  python run_demo.py --build     generate tables, distances, evaluations, ui/toy_data.js
  python run_demo.py             serve the demo (builds first if toy_data.js is missing)
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

import config as cfg
from distances.compute import build_distance_cache
from generator.generate import generate_tables
from upper_level.search import run_upper


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o).__name__)


def build(log=print):
    log("1/3 generating dummy tables → generator/data/")
    info = generate_tables()
    log("   ", info)
    log("2/3 building distance cache → distances/cache/")
    dist = build_distance_cache()
    log("   ", dist.meta["n_points"], "points")
    log("3/3 running upper-level search (this is the slow step)")
    payload = run_upper(log=log)
    cfg.UI_DIR.mkdir(parents=True, exist_ok=True)
    js = "const TOY = " + json.dumps(payload, default=_json_default) + ";\n"
    cfg.TOY_JS.write_text(js, encoding="utf-8")
    log("wrote", cfg.TOY_JS, "({:.1f} KB)".format(cfg.TOY_JS.stat().st_size / 1024))
    log("open", cfg.DEMO_HTML, "or run: python run_demo.py")


def serve(port: int, open_browser: bool):
    if not cfg.TOY_JS.exists():
        print("toy_data.js missing — building first")
        build()
    handler = SimpleHTTPRequestHandler
    class UIHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(cfg.UI_DIR), **kwargs)

        def log_message(self, fmt, *args):
            sys.stderr.write("[demo] " + (fmt % args) + "\n")

    httpd = ThreadingHTTPServer(("127.0.0.1", port), UIHandler)
    url = "http://127.0.0.1:{}/demo.html".format(port)
    print("serving", cfg.UI_DIR, "at", url)
    print("Ctrl+C to stop. You can also open ui/demo.html as a file.")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
        httpd.server_close()


def main():
    p = argparse.ArgumentParser(description="Toy DC-network demo")
    p.add_argument("--build", action="store_true", help="regenerate data and evaluations")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    if args.build:
        build()
        return
    serve(args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
