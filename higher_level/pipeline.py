"""One reported D7 search, D28 selection, then H14 with D28 fleets fixed."""
from dataclasses import dataclass
from numbers import Integral
import math
from pathlib import Path

import numpy as np

from demand_model import PlanningDays
from project import seed_sequence
from .evaluation import NetworkEvaluator, bootstrap_interval, comparison_intervals
from .io import write_json
from .pool import add_vehicle_proxy_term, build_pool
from .search import run_development, run_search


@dataclass(frozen=True)
class SearchSettings:
    budget: int = 40
    pool_size: int = 200
    seed: int = 437
    mip_time_limit: float = 20
    quantile: float = 2 / 3
    bootstrap_replicates: int = 1000
    run_control: bool = True
    development_replays: int = 0

    def __post_init__(self):
        for name, minimum in (("budget", 1), ("pool_size", 1), ("bootstrap_replicates", 1), ("development_replays", 0), ("seed", 0)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if not math.isfinite(self.mip_time_limit) or self.mip_time_limit <= 0 or not math.isfinite(self.quantile) or not 0 <= self.quantile <= 1:
            raise ValueError("invalid upper-level search settings")


def run_pipeline(context, output_dir, settings=None, *, k_values=None, log=print):
    settings = SearchSettings() if settings is None else settings
    ctx, output = context, Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    feasibility = ctx.preflight()
    write_json(output / "feasibility.json", feasibility)
    report = {"scope": ctx.scope, "settings": dict(settings.__dict__), "provenance": ctx.provenance,
              "preflight": feasibility, "recommended": None, "optimality_proven": False,
              "heldout_accessed": False, "output_dir": str(output)}
    if feasibility["status"] != "ok":
        report.update(status="infeasible_input", stage="preflight", reason=feasibility["reason"])
        write_json(output / "recommended.json", None)
        write_json(output / "report.json", report)
        return report
    ks = ctx.k_values() if k_values is None else sorted(set(k_values))
    if not ks or settings.budget < len(ks) or settings.pool_size < len(ks):
        raise ValueError("budget and pool must cover one seed per requested k")
    if any(k not in ctx.k_values() for k in ks):
        raise ValueError("requested k outside capacity-based range")
    report["k_coverage"] = {"requested": ks, "all_capacity_possible": ctx.k_values(), "restricted": ks != ctx.k_values()}
    evaluator = NetworkEvaluator(ctx)
    baseline = evaluator.evaluate(ctx.baseline_sites, "D28", quantile=settings.quantile, mode="historical")
    reassigned = evaluator.evaluate(ctx.baseline_sites, "D28", quantile=settings.quantile, mode="reassigned_baseline")
    write_json(output / "baseline_D28.json", baseline)
    write_json(output / "baseline_reassigned_D28.json", reassigned)
    if baseline["status"] != "ok" or baseline["assignment_proxy"] <= 0 or baseline["operating"] <= 0:
        raise ValueError("baseline does not provide a positive operating-cost/proxy ratio for lambda calibration")
    multiplier = baseline["operating"] / baseline["assignment_proxy"]
    report["lambda"] = multiplier
    report["lambda_definition"] = "baseline monthly fuel + spot + failure, divided by baseline cage-km/day; excludes DC and vehicle fixed cost"
    report["baseline_final"], report["baseline_reassigned_final"] = baseline, reassigned
    log(f"Calibrated lambda={multiplier:.3f}; building pool", flush=True)
    pool = build_pool(ctx, multiplier, size=settings.pool_size, k_values=ks, seed=settings.seed,
                      mip_time_limit=settings.mip_time_limit, quantile=settings.quantile, log=log)
    write_json(output / "pool.json", {**pool, "candidates": [c.to_dict() for c in pool["candidates"]]})
    if not pool["candidates"]:
        report.update(status="no_candidate_found", stage="pool", pool_diagnostics=pool["rejected_k"])
        write_json(output / "recommended.json", None)
        write_json(output / "report.json", report)
        return report
    if settings.development_replays:
        development = run_development(evaluator, pool, budget=settings.budget, replays=settings.development_replays,
                                      seed=settings.seed, log=log)
        write_json(output / "development.json", development)
        report["development_quality"] = development["quality"]
        if development["quality"]["vehicle_term_remedy_triggered"]:
            write_json(output / "pool_before_remedy.json", {**pool, "candidates": [c.to_dict() for c in pool["candidates"]]})
            pool = add_vehicle_proxy_term(ctx, pool)
            corrected = run_development(evaluator, pool, budget=settings.budget, replays=settings.development_replays,
                                         seed=settings.seed, log=log, cached_evaluations=development["evaluations"])
            write_json(output / "development_corrected.json", corrected)
            report["development_remedy"] = {"applied": True, "quality_after": corrected["quality"],
                                             "note": "vehicle term added once; same D_dev route evaluations reused; reported D7 evaluations remain fresh"}
            write_json(output / "pool.json", {**pool, "candidates": [c.to_dict() for c in pool["candidates"]]})
    else:
        report["development"] = "not run; use --development-replays for whole-pool D_dev evaluations and replays"
    # Freeze the actual (possibly development-corrected) seeds before reported D7 evaluation.
    write_json(output / "logs" / "seeds.json", {"seed": settings.seed, "day_set": "D7", "seed_ids": pool["seeds"],
                                                  "certificates": pool["certificates"],
                                                  "certificate_objective": pool.get("certificate_objective", "F + lambda*P")})
    guided = run_search(evaluator, pool, budget=settings.budget, seed=settings.seed, rule="guided", output_dir=output / "logs", log=log)
    report["guided"] = guided
    if settings.run_control:
        report["random_control"] = run_search(evaluator, pool, budget=settings.budget, seed=settings.seed,
                                               rule="random", output_dir=output / "logs", log=log)
    else:
        report["random_control"] = {"status": "not run"}
    by_id = {c.id: c for c in pool["candidates"]}
    computed = [o for o in guided["observations"] if o["result"]["status"] == "ok"]
    short_ids = set()
    for k in ks:
        rows = [o for o in computed if o["result"]["k"] == k]
        admissible = [o for o in rows if o["result"]["feasible"]]
        if rows:
            short_ids.add(min(admissible or rows, key=lambda o: (o["result"]["total"], o["candidate_id"]))["candidate_id"])
    short_ids.update(o["candidate_id"] for o in sorted(computed, key=lambda o: (o["result"]["total"], o["candidate_id"]))[:5])
    finals = {}
    for identifier in sorted(short_ids, key=lambda t: (len(by_id[t].sites), by_id[t].sites)):
        candidate = by_id[identifier]
        final = evaluator.evaluate(candidate.sites, "D28", quantile=candidate.quantile)
        finals[identifier] = final
        write_json(output / "final" / f"{len(finals):03d}.json", {"candidate_id": identifier, **final})
        log(f"Final k={final['k']}: {final.get('total', 0) / 1e6:.3f} M, service_feasible={final['feasible']}", flush=True)
    report["final_comparison"] = finals
    sweep = []
    for k in ks:
        choices = [(identifier, row) for identifier, row in finals.items() if row["k"] == k and row["feasible"]]
        winner = min(choices, key=lambda pair: (pair[1]["total"], pair[0])) if choices else None
        sweep.append({"k": k, "best_candidate_id": winner[0] if winner else None,
                      "best_E": winner[1]["total"] if winner else None,
                      "dc_fixed": winner[1]["dc_fixed"] if winner else None,
                      "operating": winner[1]["operating"] if winner else None,
                      "status": "best found among evaluated candidates" if winner else "no service-feasible candidate found",
                      "seed_certificate": pool["certificates"].get(k)})
    report["sweep"] = sweep
    write_json(output / "sweep.json", sweep)
    report["G_versus_E"] = [{"candidate_id": o["candidate_id"], "G": o["G"], "E": o["result"].get("total"),
                              "feasible": o["result"]["feasible"]} for o in guided["observations"]]
    feasible = [(identifier, row) for identifier, row in finals.items() if row["feasible"]]
    if not feasible:
        report.update(status="no_service_feasible_candidate", stage="D28")
        write_json(output / "recommended.json", None)
        write_json(output / "report.json", report)
        return report
    identifier, recommended = min(feasible, key=lambda pair: (pair[1]["total"], pair[1]["k"], pair[0]))
    report["recommended"] = {"candidate_id": identifier, **recommended}
    write_json(output / "recommended.json", report["recommended"])
    # First H14 access occurs after D28 selection. Every fleet is fixed to its D28 value.
    holdout = evaluator.evaluate(recommended["sites"], "H14", fleet=recommended["fleet"], quantile=settings.quantile)
    base_holdout = evaluator.evaluate(ctx.baseline_sites, "H14", fleet=baseline["fleet"], mode="historical")
    reassigned_holdout = (evaluator.evaluate(ctx.baseline_sites, "H14", fleet=reassigned["fleet"], mode="reassigned_baseline")
                          if reassigned["status"] == "ok" else {"status": "infeasible", "reason": reassigned["reason"]})
    report["heldout_accessed"] = True
    report["holdout"] = {"recommended": holdout, "baseline": base_holdout, "baseline_reassigned": reassigned_holdout}
    rng = np.random.default_rng(seed_sequence("search", settings.seed).spawn(4)[3])
    report["saving_D28"] = comparison_intervals(recommended, baseline, rng, settings.bootstrap_replicates)
    report["saving_H14"] = comparison_intervals(holdout, base_holdout, rng, settings.bootstrap_replicates)
    report["saving_H14_vs_reassigned"] = comparison_intervals(holdout, reassigned_holdout, rng, settings.bootstrap_replicates)
    report["cost_intervals"] = {}
    for label, row in (("D28", recommended), ("H14", holdout), ("baseline_D28", baseline),
                       ("baseline_H14", base_holdout), ("reassigned_D28", reassigned), ("reassigned_H14", reassigned_holdout)):
        if row["status"] != "ok":
            continue
        report["cost_intervals"][label] = {part: bootstrap_interval(values, row["weights"], rng, settings.bootstrap_replicates)
                                          for part, values in row["per_day_components"].items()}
        report["cost_intervals"][label]["total"] = bootstrap_interval(row["per_day_total"], row["weights"], rng, settings.bootstrap_replicates)
    average = PlanningDays("average-day", [ctx.days("D28").dates[0]], np.array([1.0]), ctx.store_ids, ctx.volumes[None, :])
    average_result = evaluator.evaluate(recommended["sites"], average, quantile=settings.quantile)
    report["average_day"] = {"evaluation": average_result,
                              "sampled_minus_average_cost": recommended["total"] - average_result["total"]}
    report.update(status="complete" if holdout["feasible"] else "holdout_service_failure", stage="finished",
                  improves_on_historical_baseline=recommended["total"] < baseline["total"],
                  recommendation_scope="best service-feasible design found in the evaluated pool; no global optimality certificate")
    write_json(output / "holdout.json", report["holdout"])
    write_json(output / "report.json", report)
    return report
