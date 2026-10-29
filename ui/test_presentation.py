"""Read-only viewer integration checks; no optimizer invocation or model writes."""
from collections import Counter
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_demo import make_handler
from ui.presentation import ResultBundle


class PresentationChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = ResultBundle(ROOT / "code/results/higher_level", ROOT / "data")
        cls.report = cls.bundle.report()
        class QuietHandler(make_handler(cls.bundle)):
            def log_message(self, fmt, *args):
                pass
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_saved_metrics_and_assignments_survive_presentation_adapter(self):
        source = self.report["recommended"]
        with urlopen(self.url + "/api/network?id=recommended") as response:
            served = json.load(response)["evaluation"]
        self.assertEqual(served, source)
        self.assertEqual(dict(Counter(served["store_to_site"].values())), served["loads"])
        self.assertEqual(sum(served["loads"].values()), self.report["scope"]["n_stores"])
        self.assertAlmostEqual(sum(v * w for v, w in zip(served["per_day_total"], served["weights"])), served["total"])
        self.assertAlmostEqual(sum(served[k] for k in ("dc_fixed", "vehicle_fixed", "fuel", "spot", "failure")), served["total"])

    def test_every_network_is_served_and_mapped_by_id_not_toy_position(self):
        dashboard = self.bundle.dashboard()
        sites = {s["id"] for s in dashboard["geography"]["sites"]}
        stores = {s["id"] for s in dashboard["geography"]["stores"]}
        for summary in dashboard["networks"]:
            with self.subTest(network=summary["id"]):
                with urlopen(self.url + "/api/network?id=" + quote(summary["id"], safe="")) as response:
                    value = json.load(response)["evaluation"]
                self.assertEqual(value.get("total"), summary.get("total"))
                self.assertTrue(set(value.get("store_to_site", {})) <= stores)
                self.assertTrue(set(value.get("sites", [])) <= sites)
        self.assertEqual(self.bundle.network("holdout:recommended")["evaluation"]["fleet"], self.report["recommended"]["fleet"])

    def test_original_downloads_are_byte_identical(self):
        for name in ("recommended.json", "final/001.json", "logs/search_437.jsonl"):
            with self.subTest(file=name):
                with urlopen(self.url + "/api/result?file=" + quote(name) + "&download=1") as response:
                    self.assertEqual(response.read(), self.bundle.result_file(name).read_bytes())
                    self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_result_boundary_and_unknown_network(self):
        for path in ("/api/result?file=../../config.py", "/api/result?file=../report.json", "/api/network?id=does-not-exist", "/presentation.py", "/../config.py"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                urlopen(self.url + path)
            self.assertEqual(caught.exception.code, 404)
            caught.exception.close()

    def test_aborted_report_never_resurrects_stale_recommendation(self):
        with tempfile.TemporaryDirectory(prefix="dc-viewer-test-") as directory:
            path = Path(directory)
            (path / "report.json").write_text(json.dumps({"status": "infeasible_input", "recommended": None}), encoding="utf-8")
            (path / "recommended.json").write_text(json.dumps(self.report["recommended"]), encoding="utf-8")
            bundle = ResultBundle(path, ROOT / "data")
            self.assertEqual(bundle.dashboard()["networks"], [])
            with self.assertRaises(KeyError):
                bundle.network("recommended")


if __name__ == "__main__":
    unittest.main()
