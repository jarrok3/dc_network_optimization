"""Read-only adapters for the result viewer; no optimization/model imports."""
import csv
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=4)
def _read_report(path, modified, size):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


class ResultBundle:
    def __init__(self, directory, data_dir):
        self.directory = Path(directory).resolve()
        self.data_dir = Path(data_dir).resolve()

    def report(self):
        path = self.directory / "report.json"
        stat = path.stat()
        value = _read_report(str(path), stat.st_mtime_ns, stat.st_size)
        if not isinstance(value, dict):
            raise ValueError("report.json must contain a report object")
        return value

    def _networks(self):
        report = self.report()
        # report.json is authoritative: old standalone files may survive an aborted run.
        rows = []
        def add(key, label, result):
            if isinstance(result, dict):
                rows.append((key, label, result))
        add("recommended", "Recommended · D28", report.get("recommended"))
        add("baseline", "Historical baseline · D28", report.get("baseline_final"))
        add("reassigned", "Reassigned baseline · D28", report.get("baseline_reassigned_final"))
        for key, label in (("recommended", "Recommended"), ("baseline", "Historical baseline"), ("baseline_reassigned", "Reassigned baseline")):
            add("holdout:" + key, label + " · H14", report.get("holdout", {}).get(key))
        for identifier, result in report.get("final_comparison", {}).items():
            add("final:" + identifier, f"Final candidate · k={result.get('k', '?')} · D28", result)
        for rule, field in (("guided", "guided"), ("random", "random_control")):
            for i, row in enumerate(report.get(field, {}).get("observations", []), 1):
                add(f"{rule}:{i}", f"{rule.title()} #{i} · D7", row.get("result"))
        add("average", "Recommended sites · mean-demand day", report.get("average_day", {}).get("evaluation"))
        return rows

    def network(self, key):
        for identifier, label, result in self._networks():
            if identifier == key:
                return {"id": identifier, "label": label, "evaluation": result}
        raise KeyError("Network not found in the current report: " + key)

    def result_file(self, name):
        path = (self.directory / name).resolve()
        if not path.is_relative_to(self.directory) or path.suffix not in (".json", ".jsonl") or not path.is_file():
            raise FileNotFoundError("Result file not found")
        return path

    def geography(self):
        def read(name):
            with (self.data_dir / name).open(encoding="utf-8-sig", newline="") as stream:
                return list(csv.DictReader(stream))
        dc_sites = {row["dc_id"]: row["site_id"] for row in read("dcs.csv")}
        stores = [{"id": row["store_id"], "lat": float(row["lat"]), "lon": float(row["lon"]),
                   "region": row.get("region", ""), "type": row.get("store_type", ""),
                   "historical_site": dc_sites.get(row.get("current_dc_id"))} for row in read("stores.csv")]
        costs = {row["name"]: float(row["value"]) for row in read("cost_parameters.csv")}
        sites = [{"id": row["site_id"], "lat": float(row["lat"]), "lon": float(row["lon"]),
                  "name": row.get("name", row["site_id"]), "fixed_cost": float(row["monthly_fixed_cost"]),
                  "minimum": float(row.get("min_stores") or costs.get("min_stores", 0)),
                  "maximum": float(row["max_stores"]), "current": row["site_id"] in dc_sites.values()}
                 for row in read("candidate_sites.csv")]
        return {"stores": stores, "sites": sites}

    def dashboard(self):
        source = self.report()
        keys = ("status", "stage", "reason", "scope", "settings", "preflight", "recommendation_scope",
                "optimality_proven", "saving_D28", "saving_H14", "cost_intervals", "lambda", "lambda_definition",
                "development", "k_coverage", "sweep", "G_versus_E")
        report = {key: source[key] for key in keys if key in source}
        report["average_day_gap"] = source.get("average_day", {}).get("sampled_minus_average_cost")
        networks = []
        summary_keys = ("status", "feasible", "reason", "k", "sites", "total", "dc_fixed", "vehicle_fixed", "fuel",
                        "spot", "failure", "operating", "day_set", "fleet", "loads", "service_before_noon", "service_min_day",
                        "lower_level_solves", "seconds", "quantile", "fleet_source", "candidate_id")
        for identifier, label, value in self._networks():
            networks.append({"id": identifier, "label": label, **{k: value[k] for k in summary_keys if k in value}})
        searches = {}
        for rule, field in (("guided", "guided"), ("random", "random_control")):
            searches[rule] = [{"id": f"{rule}:{i}", "iteration": i, "candidate_id": row["candidate_id"],
                               "G": row.get("G"), "phase": row.get("phase"),
                               **{k: row["result"].get(k) for k in ("k", "total", "feasible", "status", "reason", "seconds", "lower_level_solves", "service_min_day")}}
                              for i, row in enumerate(source.get(field, {}).get("observations", []), 1)]
        files = [{"name": p.relative_to(self.directory).as_posix(), "bytes": p.stat().st_size}
                 for p in sorted(self.directory.rglob("*")) if p.is_file() and p.suffix in (".json", ".jsonl")]
        return {"report": report, "networks": networks, "searches": searches, "geography": self.geography(), "files": files,
                "source": "code/results/higher_level/report.json", "modified": (self.directory / "report.json").stat().st_mtime}
