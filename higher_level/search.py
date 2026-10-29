"""Budgeted common-day guided search, random control and development replay."""
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

from project import seed_sequence
from .io import append_log, write_json
from .surrogate import GapGaussianProcess, expected_improvement


def choose_next(candidates, observations, used, rng, rule="guided"):
    remaining = [c for c in candidates if c.id not in used]
    if not remaining:
        raise ValueError("candidate pool exhausted")
    if rule == "random":
        return remaining[int(rng.integers(len(remaining)))], {}
    if rule != "guided":
        raise ValueError("selection rule must be guided or random")
    by_id = {c.id: c for c in candidates}
    train = [(by_id[o["candidate_id"]], o["result"]) for o in observations
             if o["result"]["status"] == "ok" and o["result"]["total"] > 0]
    if not train:
        return min(remaining, key=lambda c: (c.G, len(c.sites), c.sites)), {}
    all_x = np.array([c.features for c in candidates])
    center, scale = all_x.mean(axis=0), all_x.std(axis=0)
    scale[scale < 1e-10] = 1
    x = (np.array([c.features for c, _ in train]) - center) / scale
    target = np.log([r["total"] / c.G for c, r in train])
    gp = GapGaussianProcess().fit(x, target)
    mean, std = gp.predict((np.array([c.features for c in remaining]) - center) / scale)
    feasible = [r["total"] for _, r in train if r["feasible"]]
    incumbent = min(feasible or [r["total"] for _, r in train])
    acquisition = expected_improvement(np.array([c.G for c in remaining]), mean, std, incumbent)
    # Stable ties: lower k, then lexicographic site IDs.
    best = max(float(acquisition.max()), 0)
    tied = np.flatnonzero(np.isclose(acquisition, best, rtol=1e-12, atol=1e-8))
    index = min(tied, key=lambda i: (len(remaining[i].sites), remaining[i].sites))
    return remaining[index], {"surrogate_mean": float(mean[index]), "surrogate_std": float(std[index]),
                              "predicted_cost_median": float(remaining[index].G * np.exp(mean[index])),
                              "acquisition": float(acquisition[index]), "incumbent_is_service_feasible": bool(feasible)}


def run_search(evaluator, pool, *, budget=40, seed=437, rule="guided", output_dir=None, log=print):
    candidates, seeds = pool["candidates"], pool["seeds"]
    if not isinstance(budget, int) or budget < len(seeds):
        raise ValueError(f"budget must cover the {len(seeds)} mandatory G seeds")
    if not candidates:
        return {"rule": rule, "observations": [], "effective_budget": 0, "reason": "empty feasible pool"}
    effective = min(budget, len(candidates))
    rng = np.random.default_rng(seed_sequence("search" if rule == "guided" else "control", seed).spawn(2)[1])
    by_id = {c.id: c for c in candidates}
    observations, used = [], set()
    path = None
    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{'search' if rule == 'guided' else 'control'}_{seed}.jsonl"
        path.write_text("", encoding="utf-8")
    for iteration in range(effective):
        phase = "seed" if iteration < len(seeds) else ("guided" if rule == "guided" else "random")
        if phase == "seed":
            candidate, prediction = by_id[seeds[iteration]], {}
        else:
            candidate, prediction = choose_next(candidates, observations, used, rng, rule)
        result = evaluator.evaluate(candidate.sites, "D7", quantile=candidate.quantile)
        used.add(candidate.id)
        observations.append({"candidate_id": candidate.id, "G": candidate.G, "phase": phase, "result": result})
        record = {"eval_id": iteration + 1, "phase": phase, "selection_rule": rule, "candidate_id": candidate.id,
                  "sites": list(candidate.sites), "k": len(candidate.sites), "q": candidate.quantile, "G": candidate.G,
                  "E": result.get("total"), "status": result["status"], "feasible": result["feasible"],
                  "seed": seed, "days_used": result.get("day_labels", []), "weights": result.get("weights", []),
                  "number_of_solves": result["lower_level_solves"], "seconds": result["seconds"],
                  "dc_fixed": result.get("dc_fixed"), "vehicle_fixed": result.get("vehicle_fixed"),
                  "fuel": result.get("fuel"), "spot": result.get("spot"), "failure": result.get("failure"),
                  "surrogate_mean": prediction.get("surrogate_mean"), "surrogate_std": prediction.get("surrogate_std"),
                  "acquisition": prediction.get("acquisition"), **prediction}
        if path is not None:
            append_log(path, record)
            write_json(output_dir / f"{rule}_evaluations" / f"{iteration + 1:03d}.json", result)
        text = f"{result['total'] / 1e6:.3f} M KRW" if result["status"] == "ok" else result.get("reason", "infeasible")
        log(f"{rule} {iteration + 1}/{effective}: k={len(candidate.sites)}, E={text}, service_feasible={result['feasible']}", flush=True)
    valid = [o for o in observations if o["result"]["feasible"]]
    best = min(valid, key=lambda o: (o["result"]["total"], o["result"]["k"], o["candidate_id"])) if valid else None
    return {"rule": rule, "requested_budget": budget, "effective_budget": effective,
            "pool_exhausted": len(candidates) < budget, "best_candidate_id": None if best is None else best["candidate_id"],
            "best_cost": None if best is None else best["result"]["total"], "observations": observations}


def run_development(evaluator, pool, *, budget=40, replays=100, seed=437, log=print, cached_evaluations=None):
    """Evaluate every pool candidate on D_dev, then replay without routing again."""
    if budget < len(pool["seeds"]) or replays < 1:
        raise ValueError("development budget must cover seeds and replays must be positive")
    candidates = pool["candidates"]
    values = {}
    for i, c in enumerate(candidates):
        values[c.id] = (cached_evaluations[c.id] if cached_evaluations is not None
                        else evaluator.evaluate(c.sites, "D_dev", quantile=c.quantile))
        log(f"development {i + 1}/{len(candidates)}", flush=True)
    valid = [c for c in candidates if values[c.id]["feasible"]]
    finite = [c for c in candidates if values[c.id]["status"] == "ok"]
    quality = {"rank_correlation": None, "g_best_loss_percent": None, "proxy_mape_percent": None}
    if len(finite) >= 2:
        corr = spearmanr([c.G for c in finite], [values[c.id]["total"] for c in finite]).statistic
        quality["rank_correlation"] = float(corr) if np.isfinite(corr) else None
        quality["proxy_mape_percent"] = float(np.mean([abs(c.G - values[c.id]["total"]) / values[c.id]["total"] for c in finite]) * 100)
    optimum = min((values[c.id]["total"] for c in valid), default=None)
    if valid:
        g_best = min(valid, key=lambda c: (c.G, c.sites))
        quality["g_best_loss_percent"] = 100 * (values[g_best.id]["total"] / optimum - 1)
    quality["vehicle_term_remedy_triggered"] = bool((quality["rank_correlation"] is not None and quality["rank_correlation"] < .8)
                                                     or (quality["g_best_loss_percent"] is not None and quality["g_best_loss_percent"] > 2))
    rows = []
    streams = seed_sequence("control", seed).spawn(replays)
    for repeat, stream in enumerate(streams):
        rng = np.random.default_rng(stream)
        shuffled_seeds = list(rng.permutation(pool["seeds"]))
        for rule in ("guided", "random"):
            observed, used = [], set()
            for step in range(min(budget, len(candidates))):
                if step < len(shuffled_seeds):
                    chosen = next(c for c in candidates if c.id == shuffled_seeds[step])
                else:
                    chosen, _ = choose_next(candidates, observed, used, rng, rule)
                used.add(chosen.id)
                observed.append({"candidate_id": chosen.id, "result": values[chosen.id]})
            best = min((o["result"]["total"] for o in observed if o["result"]["feasible"]), default=None)
            rows.append({"replay": repeat, "rule": rule, "best_cost": best,
                         "regret": best - optimum if best is not None and optimum is not None else None})
    return {"day_set": "D_dev", "quality": quality, "pool_best_cost": optimum, "replays": rows,
            "evaluations": values, "development_E_reused": cached_evaluations is not None,
            "remedy_status": "threshold exceeded" if quality["vehicle_term_remedy_triggered"] else "gap model sufficient under the declared thresholds"}
