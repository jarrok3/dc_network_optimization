"""CLI: python -m higher_level [run|check|evaluate] --help."""
import argparse
import json
from pathlib import Path
import sys

from project import CACHE_DIR, DATA_DIR, DEMAND_DIR, ROOT
from .context import NetworkContext
from .evaluation import NetworkEvaluator
from .io import write_json
from .pipeline import SearchSettings, run_pipeline


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0].startswith("--") and argv[0] not in ("--help", "-h")):
        argv.insert(0, "run")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", type=Path, default=DATA_DIR)
    common.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    common.add_argument("--model-dir", type=Path, default=DEMAND_DIR)
    common.add_argument("--planning-dir", type=Path, default=DEMAND_DIR / "planning")
    common.add_argument("--output-dir", type=Path)
    common.add_argument("--diagnostic-size", type=int, help="explicit nearest-eligible development subset, not a national recommendation")
    common.add_argument("--diagnostic-site", default="SITE_00")
    common.add_argument("--min-stores", type=int)
    common.add_argument("--max-stores", type=int)
    parser = argparse.ArgumentParser(description="Nationwide strategic network design using saved common days and deterministic routing")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", parents=[common], help="check all-site eligibility and capacity before searching")
    run = sub.add_parser("run", parents=[common], help="D7 guided/random search, D28 comparison, H14 validation")
    run.add_argument("--budget", type=int, default=40)
    run.add_argument("--pool-size", type=int, default=200)
    run.add_argument("--seed", type=int, default=437)
    run.add_argument("--mip-time-limit", type=float, default=20)
    run.add_argument("--quantile", type=float, default=2 / 3)
    run.add_argument("--k-values", type=int, nargs="+")
    run.add_argument("--bootstrap-replicates", type=int, default=1000)
    run.add_argument("--no-control", action="store_true")
    run.add_argument("--development-replays", type=int, default=0,
                     help="optional whole-pool D_dev benchmark and guided/random replays (expensive)")
    evaluate = sub.add_parser("evaluate", parents=[common], help="evaluate one specified network without searching")
    evaluate.add_argument("--sites", nargs="+", required=True)
    evaluate.add_argument("--day-set", choices=("D_dev", "D7", "D28", "H14"), default="D7")
    evaluate.add_argument("--quantile", type=float, default=2 / 3)
    evaluate.add_argument("--assignment-mode", choices=("optimized", "historical", "reassigned_baseline"), default="optimized")
    evaluate.add_argument("--fleet-json", type=Path, help="saved D28 evaluation or site -> fleet dictionary; required for H14")
    evaluate.add_argument("--routes", action="store_true")
    args = parser.parse_args(argv)
    ctx = NetworkContext.from_project(args.data_dir, args.cache_dir, args.model_dir, args.planning_dir,
                                      args.diagnostic_size, args.diagnostic_site, args.min_stores, args.max_stores)
    directory = args.output_dir or ROOT / "code" / "results" / ("higher_level_diagnostic" if args.diagnostic_size else "higher_level")
    if args.command == "check":
        result = ctx.preflight()
        write_json(directory / "feasibility.json", result)
    elif args.command == "evaluate":
        fleet = None
        if args.fleet_json:
            loaded = json.loads(args.fleet_json.read_text(encoding="utf-8"))
            fleet = loaded.get("fleet", loaded)
        result = NetworkEvaluator(ctx).evaluate(args.sites, args.day_set, fleet=fleet, mode=args.assignment_mode,
                                                 quantile=args.quantile, retain_routes=args.routes)
        write_json(directory / f"evaluation_{args.day_set}.json", result)
    else:
        settings = SearchSettings(args.budget, args.pool_size, args.seed, args.mip_time_limit, args.quantile,
                                  args.bootstrap_replicates, not args.no_control, args.development_replays)
        result = run_pipeline(ctx, directory, settings, k_values=args.k_values)
    summary = {key: result[key] for key in ("status", "scope", "reason", "k", "total", "feasible") if key in result}
    summary["output_dir"] = str(directory)
    preflight = result.get("preflight", result)
    if "uncovered_stores" in preflight:
        summary["uncovered_stores"] = preflight["uncovered_stores"]
    if result.get("recommended"):
        summary["recommended_sites"] = result["recommended"]["sites"]
        summary["D28_monthly_cost"] = result["recommended"]["total"]
        summary["H14_min_service"] = result["holdout"]["recommended"]["service_min_day"]
    print(json.dumps(summary, indent=2))
    return result


if __name__ == "__main__":
    main()
