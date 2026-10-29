"""Development-only timing and service checks on 100/200/280-store subsets."""
import argparse
import json
from pathlib import Path

import numpy as np

from project import ROOT
from .api import DailyRoutingSolver, weighted_fleet_size


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", default="SITE_00")
    parser.add_argument("--output", type=Path, default=ROOT / "code" / "results" / "lower_level_benchmark.json")
    args = parser.parse_args(argv)
    router = DailyRoutingSolver.from_project()
    days = router.load_days("D_dev")
    eligibility = router.eligibility(args.site, days.store_ids)
    ranked = sorted((router.distances.lookup(args.site, s)[0], s) for s in days.store_ids if eligibility[s])
    if len(ranked) < 280:
        parser.error(f"{args.site} has only {len(ranked)} eligible stores; choose another site")
    rows = []
    for n in (100, 200, 280):
        assigned = [s for _, s in ranked[:n]]
        results = router.solve_days(args.site, assigned, days)
        seconds = [r["timing"]["solve_seconds"] for r in results]
        fleet = weighted_fleet_size([r["vehicles_needed"] for r in results], days.weights)
        priced = [router.reprice(r, fleet) for r in results]
        row = {"n_stores": n, "n_days": len(results), "solve_seconds_median": float(np.median(seconds)),
               "solve_seconds_p95": float(np.quantile(seconds, .95)),
               "matrix_seconds_first_day": results[0]["timing"]["matrix_seconds"],
               "matrix_cache_hits": sum(r["timing"]["matrix_cache_hit"] for r in results),
               "vehicles_by_day": [r["vehicles_needed"] for r in results], "owned_fleet_quantile_2_3": fleet,
               "unserved_by_day": [len(r["unserved"]) for r in results],
               "minimum_service_before_noon": min(r["service_before_noon"] for r in results),
               "all_days_meet_service": all(r["service_level_met"] for r in results),
               "weighted_daily_fuel_spot_failure_krw": float(sum(w * r["costs"]["total"] for w, r in zip(days.weights, priced)))}
        rows.append(row)
        print(json.dumps(row), flush=True)
    report = {"site_id": args.site, "day_set": "D_dev", "scope": "nearest eligible subsets; development evidence, not a whole-network evaluation or optimality study",
              "timing_scope": "single solver process; solve times exclude initial distance loading and matrix preparation",
              "results": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    main()
