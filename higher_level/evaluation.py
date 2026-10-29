"""Full monthly E: exact assignment, N=0 routing, quantile fleet, frozen accounting."""
from numbers import Integral
from time import perf_counter

import numpy as np

from demand_model import PlanningDays
from lower_level import weighted_fleet_size


class NetworkEvaluator:
    def __init__(self, context):
        self.context = context
        self.evaluations = 0

    def evaluate(self, sites, days="D7", *, quantile=2 / 3, fleet=None, mode="optimized", retain_routes=False):
        started = perf_counter()
        ctx = self.context
        sites = ctx.canonical_sites(sites)
        if not np.isfinite(quantile) or not 0 <= quantile <= 1:
            raise ValueError("fleet quantile must lie in [0, 1]")
        if fleet is not None and (set(fleet) != set(sites) or any(isinstance(v, bool) or not isinstance(v, Integral) or v < 0 for v in fleet.values())):
            raise ValueError("fixed fleet must specify a nonnegative integer for every open site, and no others")
        assignment = ctx.assignment(sites, mode)
        base = {"sites": list(sites), "k": len(sites), "assignment_mode": mode, "quantile": quantile,
                "optimality_proven": False, "scope": ctx.scope}
        if assignment["status"] != "ok":
            return {**base, "status": "infeasible", "feasible": False, "reason": assignment["reason"],
                    "assignment_diagnostics": {k: v for k, v in assignment.items() if k != "assign"},
                    "lower_level_solves": 0, "seconds": perf_counter() - started}
        if isinstance(days, str) and days == "H14" and fleet is None:
            raise ValueError("H14 requires a fixed fleet sized on D28; holdout fleet sizing is prohibited")
        days = ctx.days(days) if isinstance(days, str) else days
        if not isinstance(days, PlanningDays) or set(days.store_ids) != set(ctx.store_ids):
            raise ValueError("evaluation planning population must match the context exactly")
        if days.label == "H14" and fleet is None:
            raise ValueError("H14 requires a fixed fleet sized on D28; holdout fleet sizing is prohibited")
        nd = len(days.dates)
        weights = np.asarray(days.weights, float)
        if not nd or weights.shape != (nd,) or not np.isfinite(weights).all() or (weights <= 0).any() or not np.isclose(weights.sum(), 1):
            raise ValueError("day weights must be finite, positive and sum to one")
        costs = {key: np.zeros(nd) for key in ("fuel", "spot", "late", "stockout")}
        n12, n14, delivered, unserved, km = (np.zeros(nd) for _ in range(5))
        per_dc_ok, needed, owned, by_dc, routes = {}, {}, {}, {}, {}
        calls = ctx.router.solve_calls
        for j, site in enumerate(sites):
            members = [s for s, chosen in assignment["store_to_site"].items() if chosen == site]
            # Recompute routes afresh even when another candidate or phase used this assignment.
            sizing = ctx.router.solve_days(site, members, days, owned=0, spot_cap=None)
            needed[site] = [r["vehicles_needed"] for r in sizing]
            owned[site] = int(fleet[site]) if fleet is not None else weighted_fleet_size(needed[site], weights, quantile)
            priced = [ctx.router.reprice(r, owned[site]) for r in sizing]
            per_dc_ok[site] = all(r["service_level_met"] for r in priced)
            by_dc[site] = {"n_stores": len(members), "fleet": owned[site], "vehicles_needed": needed[site],
                           "service_before_noon_by_day": [r["service_before_noon"] for r in priced],
                           "unserved_by_day": [list(r["unserved"]) for r in priced],
                           "spot_by_day": [r["spot"] for r in priced]}
            if retain_routes:
                routes[site] = priced
            for d, r in enumerate(priced):
                for output, source in (("fuel", "fuel"), ("spot", "spot_cost"), ("late", "late_cost"), ("stockout", "stockout_cost")):
                    costs[output][d] += r["costs"][source]
                n12[d] += r["n12"]
                n14[d] += r["n14"]
                delivered[d] += r["delivered"]
                unserved[d] += len(r["unserved"])
                km[d] += r["km"]
        ix = [ctx.site_index[s] for s in sites]
        F = float(ctx.fixed_costs[ix].sum())
        vehicle_fixed = float(ctx.vehicle_monthly * sum(owned.values()))
        monthly = {key: float(ctx.days_per_month * weights @ value) for key, value in costs.items()}
        operating = sum(monthly.values())
        daily_operating = sum(costs.values())
        per_day_total = F + vehicle_fixed + ctx.days_per_month * daily_operating
        service = (delivered - n12) / ctx.n  # Undelivered stores count as service failures.
        service_ok = bool(np.all(service >= .98 - 1e-9) and all(per_dc_ok.values()))
        daily_components = {"dc_fixed": [F] * nd, "vehicle_fixed": [vehicle_fixed] * nd,
                            "fuel": (ctx.days_per_month * costs["fuel"]).tolist(),
                            "spot": (ctx.days_per_month * costs["spot"]).tolist(),
                            "failure": (ctx.days_per_month * (costs["late"] + costs["stockout"])).tolist()}
        result = {**base, "status": "ok", "feasible": service_ok, "service_level_met": service_ok,
                  "reason": "" if service_ok else "routing output fails the per-DC or chain-wide 98% before-noon requirement",
                  "day_set": days.label, "day_labels": [d.isoformat() for d in days.dates], "weights": weights.tolist(),
                  "fleet": owned, "fleet_source": "fixed from D28" if fleet is not None else f"sized on {days.label}",
                  "store_to_site": assignment["store_to_site"], "loads": dict(zip(sites, assignment["loads"])),
                  "assignment_proxy": assignment["objective"], "assignment_optimality_proven": assignment["optimality_proven"],
                  "historical_ineligible_stores": assignment["ineligible_stores"],
                  "dc_fixed": F, "vehicle_fixed": vehicle_fixed, "fuel": monthly["fuel"], "spot": monthly["spot"],
                  "failure": monthly["late"] + monthly["stockout"], "late_cost": monthly["late"], "stockout_cost": monthly["stockout"],
                  "operating": operating, "total": F + vehicle_fixed + operating,
                  "per_day_total": per_day_total.tolist(), "per_day_components": daily_components,
                  "service_before_noon": float(weights @ service), "service_min_day": float(service.min()),
                  "service_before_noon_by_day": service.tolist(), "unserved_by_day": unserved.astype(int).tolist(),
                  "n12_by_day": n12.astype(int).tolist(), "n14_by_day": n14.astype(int).tolist(),
                  "km_by_day": km.tolist(), "dc_days": by_dc, "vehicles_needed": needed,
                  "lower_level_solves": ctx.router.solve_calls - calls, "seconds": perf_counter() - started}
        if retain_routes:
            result["routes"] = routes
        self.evaluations += 1
        return result


def bootstrap_interval(values, weights, rng, replicates=1000, confidence=.95):
    """Weighted empirical paired-day bootstrap; weights enter sampling once.

    Treats the saved days as an empirical distribution. It does not preserve
    stratified-sampling quotas or include parameter/route-model uncertainty.
    """
    values, weights = np.asarray(values, float), np.asarray(weights, float)
    if values.ndim != 1 or not len(values) or weights.shape != values.shape or not np.isfinite(values).all() or not np.isfinite(weights).all() or (weights <= 0).any():
        raise ValueError("bootstrap values/positive weights must be matching finite vectors")
    if not isinstance(replicates, int) or replicates < 1 or not 0 < confidence < 1:
        raise ValueError("invalid bootstrap replicates/confidence")
    weights = weights / weights.sum()
    indices = rng.choice(len(values), size=(replicates, len(values)), p=weights)
    samples = values[indices].mean(axis=1)
    tail = (1 - confidence) / 2
    return {"mean": float(weights @ values), "lower": float(np.quantile(samples, tail)),
            "upper": float(np.quantile(samples, 1 - tail)), "confidence": confidence,
            "method": "weighted empirical paired-day bootstrap; fixed fleet and fitted models"}


def comparison_intervals(candidate, baseline, rng, replicates=1000):
    if candidate["status"] != "ok" or baseline["status"] != "ok":
        return None
    if candidate["day_labels"] != baseline["day_labels"] or not np.allclose(candidate["weights"], baseline["weights"]):
        raise ValueError("paired comparison requires identical common days and weights")
    difference = np.asarray(baseline["per_day_total"]) - np.asarray(candidate["per_day_total"])
    result = bootstrap_interval(difference, candidate["weights"], rng, replicates)
    result["percent"] = 100 * result["mean"] / baseline["total"] if baseline["total"] else None
    return result
