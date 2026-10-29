"""
Serve the saved optimization results offline, or explicitly run a solver.

  python run_demo.py --build     evaluate the prepared nationwide instance; write a JSON report
  python run_demo.py             view existing results (does not run a solver)
  python run_demo.py --check-results  validate the presentation inputs, read-only
  python run_demo.py --routing   run the nationwide offline daily-routing example
  python run_demo.py --network   run the nationwide strategic solver (JSON report)
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from ui.presentation import ResultBundle

UI_DIR = ROOT / "ui"
RESULTS_DIR = ROOT / "code" / "results" / "higher_level"


def build(log=print):
    from upper_level.search import run_upper
    report = run_upper(log=log)
    log("Strategic solver status:", report["status"])
    log("JSON report:", Path(report["output_dir"]) / "report.json")
    return report


def make_handler(bundle):
    """Expose only UI assets and read-only views of the selected result bundle."""
    class UIHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(UI_DIR), **kwargs)

        def do_GET(self):
            request = urlsplit(self.path)
            query = parse_qs(request.query)
            try:
                if request.path == "/api/dashboard":
                    self.send_json(bundle.dashboard())
                elif request.path == "/api/network":
                    self.send_json(bundle.network(query.get("id", ["recommended"])[0]))
                elif request.path == "/api/result":
                    path = bundle.result_file(query.get("file", ["report.json"])[0])
                    body = path.read_bytes()
                    self.send_response(200)
                    content_type = "application/json" if path.suffix == ".json" else "application/x-ndjson"
                    self.send_header("Content-Type", content_type + "; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    if query.get("download") == ["1"]:
                        self.send_header("Content-Disposition", 'attachment; filename="' + path.name + '"')
                    self.end_headers()
                    self.wfile.write(body)
                elif request.path.startswith("/api/"):
                    self.send_json({"error": "Unknown endpoint"}, 404)
                elif request.path in ("/", "/demo.html", "/app.js", "/demo.css"):
                    self.path = "/demo.html" if request.path == "/" else request.path
                    super().do_GET()
                else:
                    self.send_error(404)
            except (FileNotFoundError, KeyError) as exc:
                self.send_json({"error": str(exc)}, 404)
            except (ValueError, OSError) as exc:
                self.send_json({"error": str(exc)}, 422)

        def send_json(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            super().end_headers()

        def log_message(self, fmt, *args):
            sys.stderr.write("[demo] " + (fmt % args) + "\n")

    return UIHandler


def serve(port: int, open_browser: bool, results_dir=RESULTS_DIR):
    bundle = ResultBundle(results_dir, ROOT / "data")
    bundle.report()  # Fail clearly before opening the browser if the source is missing.
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(bundle))
    url = "http://127.0.0.1:{}/demo.html".format(httpd.server_port)
    print("Saved-result viewer:", url, flush=True)
    print("Reading:", bundle.directory, flush=True)
    print("Offline, read-only. Ctrl+C to stop. Open via localhost, not file://.", flush=True)
    try:
        if open_browser:
            webbrowser.open(url)
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
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
    p = argparse.ArgumentParser(description="Offline viewer for saved higher-level optimization results")
    p.epilog = "Nationwide solvers: --routing --help or --network --help"
    p.add_argument("--build", action="store_true", help="evaluate the prepared nationwide instance and write a strategic JSON report")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--results-dir", type=Path, default=RESULTS_DIR, help="saved result directory (default: code/results/higher_level)")
    p.add_argument("--check-results", action="store_true", help="validate saved presentation inputs without running optimization")
    args = p.parse_args()
    if args.check_results:
        payload = ResultBundle(args.results_dir, ROOT / "data").dashboard()
        print(json.dumps({"status": payload["report"].get("status"), "networks": len(payload["networks"]),
                          "active_stores": payload["report"].get("scope", {}).get("n_stores"),
                          "store_coordinates": len(payload["geography"]["stores"]), "sites": len(payload["geography"]["sites"]),
                          "source_files": len(payload["files"])}, indent=2))
        return
    if args.build:
        build()
        return
    serve(args.port, open_browser=not args.no_browser, results_dir=args.results_dir)


if __name__ == "__main__":
    main()
