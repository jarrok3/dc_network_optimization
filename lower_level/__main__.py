"""Run a saved planning day offline: python -m lower_level --help."""
import argparse
import json
from pathlib import Path

import pandas as pd

from project import CACHE_DIR, DATA_DIR, DEMAND_DIR, ROOT
from .api import DailyRoutingSolver, weighted_fleet_size


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline daily routing for the nationwide generated instance")
    parser.add_argument("--site", default="SITE_00")
    parser.add_argument("--day-set", choices=("D_dev", "D7", "D28", "H14"), default="D_dev")
    parser.add_argument("--day-index", type=int, default=0)
    parser.add_argument("--all-days", action="store_true")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--stores", nargs="+", help="explicit assigned store IDs")
    selection.add_argument("--assignment", help="CSV with store_id,site_id, or 'historical'")
    selection.add_argument("--nearest-stores", type=int, help="diagnostic subset of nearest eligible stores; default 100")
    parser.add_argument("--owned", type=int, default=0)
    parser.add_argument("--spot-cap", type=int)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--planning-dir", type=Path, default=DEMAND_DIR / "planning")
    parser.add_argument("--output", type=Path, default=ROOT / "code" / "results" / "daily_routing.json")
    args = parser.parse_args(argv)
    router = DailyRoutingSolver.from_project(args.data_dir, args.cache_dir)
    days = router.load_days(args.day_set, args.planning_dir)
    if args.stores is not None:
        ids, mode = args.stores, "explicit assigned stores"
    elif args.assignment == "historical":
        stores = pd.read_csv(args.data_dir / "stores.csv", dtype=str)
        dcs = pd.read_csv(args.data_dir / "dcs.csv", dtype=str)
        current = set(dcs.loc[dcs.site_id == args.site, "dc_id"])
        if not current:
            parser.error(f"{args.site} is not a historical DC; use --stores, --assignment CSV, or --nearest-stores")
        ids = stores.loc[stores.current_dc_id.isin(current) & stores.store_id.isin(days.store_ids), "store_id"].tolist()
        mode = "historical assignment, active planning population (capacity bounds exempt)"
    elif args.assignment:
        assigned = pd.read_csv(args.assignment, dtype=str)
        if not {"store_id", "site_id"} <= set(assigned) or assigned.store_id.isna().any() or assigned.site_id.isna().any() or assigned.store_id.duplicated().any():
            parser.error("assignment CSV requires unique store_id and nonmissing site_id columns")
        if not assigned.site_id.isin(router.distances.sites.index).all() or not assigned.store_id.isin(days.store_ids).all():
            parser.error("assignment CSV contains unknown sites or stores outside the active planning population")
        ids = assigned.loc[assigned.site_id == args.site, "store_id"].tolist()
        mode = f"provided assignment: {args.assignment}"
    else:
        n = 100 if args.nearest_stores is None else args.nearest_stores
        if n < 1:
            parser.error("--nearest-stores must be positive")
        eligible = router.eligibility(args.site, days.store_ids)
        ranked = sorted((router.distances.lookup(args.site, s)[0], s) for s in days.store_ids if eligible[s])
        if len(ranked) < n:
            parser.error(f"only {len(ranked)} stores are individually eligible for {args.site}; requested {n}")
        ids = [s for _, s in ranked[:n]]
        mode = "nearest eligible diagnostic subset; not a full network assignment"
    if args.all_days:
        results = router.solve_days(args.site, ids, days, owned=args.owned, spot_cap=args.spot_cap)
    else:
        results = [router.solve_day(args.site, ids, days, args.day_index, owned=args.owned, spot_cap=args.spot_cap)]
    report = {"selection": mode, "day_set": args.day_set, "site_id": args.site, "results": results}
    if args.all_days and args.owned == 0 and args.spot_cap is None:
        fleet = weighted_fleet_size([r["vehicles_needed"] for r in results], days.weights)
        report["suggested_owned_fleet"] = fleet
        report["weighted_daily_cost_at_suggested_fleet"] = sum(float(w) * router.reprice(r, fleet)["costs"]["total"] for w, r in zip(days.weights, results))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    summary = [{k: r[k] for k in ("day_id", "n_stores", "vehicles_needed", "vehicles_run", "spot", "km", "service_level_met")} | {"unserved": len(r["unserved"]), "total_krw": r["costs"]["total"], "solve_seconds": r["timing"]["solve_seconds"]} for r in results]
    print(json.dumps({"selection": mode, "output": str(args.output), "summary": summary}, indent=2))
    return report


if __name__ == "__main__":
    main()
