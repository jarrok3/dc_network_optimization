"""
Serve the existing browser illustration, or run the current offline solvers.

  python run_demo.py --build     evaluate the prepared nationwide instance; write a JSON report
  python run_demo.py             serve the existing browser illustration
  python run_demo.py --routing   run the nationwide offline daily-routing example
  python run_demo.py --network   run the nationwide strategic solver (JSON report)
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import config as cfg


def build(log=print):
    from upper_level.search import run_upper
    report = run_upper(log=log)
    log("Strategic solver status:", report["status"])
    log("JSON report:", Path(report["output_dir"]) / "report.json")
    return report


def serve(port: int, open_browser: bool):
    if not cfg.TOY_JS.exists():
        raise FileNotFoundError("legacy browser data missing; run --network for the current nationwide JSON solver")
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
    if "--network" in sys.argv[1:]:
        from higher_level.__main__ import main as network_main
        network_main([arg for arg in sys.argv[1:] if arg != "--network"])
        return
    # Keep the nationwide routing entry point independent of incomplete legacy imports.
    # Remaining arguments are the same as `python -m lower_level`.
    if "--routing" in sys.argv[1:]:
        from lower_level.__main__ import main as routing_main
        routing_main([arg for arg in sys.argv[1:] if arg != "--routing"])
        return
    p = argparse.ArgumentParser(description="Toy DC-network demo")
    p.epilog = "Nationwide solvers: --routing --help or --network --help"
    p.add_argument("--build", action="store_true", help="evaluate the prepared nationwide instance and write a strategic JSON report")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    if args.build:
        build()
        return
    serve(args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
