"""
Build the toy instance (if needed) and serve ui/ on http://127.0.0.1:8000

  python run_demo.py --build     generate tables, distances, evaluations, ui/toy_data.js
  python run_demo.py             serve the demo (builds first if toy_data.js is missing)
  python run_demo.py --routing   run the nationwide offline daily-routing example
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


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o).__name__)


def build(log=print):
    from generator.generate import generate_tables
    from upper_level.search import run_upper
    log("1/3 generating dummy tables → generator/data/")
    # Jeśli wysyłasz w zipie gotowe data/*.csv to ten krok też możesz opcjonalnie pominąć
    info = generate_tables()
    log("   ", info)
    
    log("2/3 checking distance cache → cache/road_distance.parquet")
    if not (cfg.CACHE_DIR / "road_distance.parquet").exists():
         log("BŁĄD: Brak pliku road_distance.parquet. Skrypt oceniający wymaga wygenerowanego bufora.")
         sys.exit(1)
    log("    Plik cache obecny. Pobieranie odległości w trybie OFFLINE.")
    
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
    # Keep the nationwide routing entry point independent of incomplete legacy imports.
    # Remaining arguments are the same as `python -m lower_level`.
    if "--routing" in sys.argv[1:]:
        from lower_level.__main__ import main as routing_main
        routing_main([arg for arg in sys.argv[1:] if arg != "--routing"])
        return
    p = argparse.ArgumentParser(description="Toy DC-network demo")
    p.epilog = "Nationwide daily routing: python run_demo.py --routing --help"
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
