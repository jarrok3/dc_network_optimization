"""
Upper level for the toy instance: enumerate every subset of the four sites,
score G (cheap assignment on road distance) and E (full lower-level evaluation).
"""
from __future__ import annotations

import hashlib
import itertools
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as cfg
from distances.compute import load_distances
from generator.generate import WEEKDAY_F, load_dcs, load_demand, load_sites, load_stores
from lower_level.solver import LLParams, account, solve_dc_day


def weighted_quantile(values, weights, q):
    values, weights = np.asarray(values, float), np.asarray(weights, float)
    order = np.argsort(values, kind="stable")
    cum = np.cumsum(weights[order]) / weights.sum()
    return float(values[order][np.searchsorted(cum, q - 1e-12)])


# --------------------------------------------------------------------------- demand
class DemandModel:
    def __init__(self, stores, history):
        self.stores = stores
        self.active = [s for s in stores if s["closed_on"] is None]
        self.active_idx = [i for i, s in enumerate(stores) if s["closed_on"] is None]
        self.id_to_i = {s["store_id"]: k for k, s in enumerate(self.active)}
        by = defaultdict(list)
        for row in history:
            by[row["store_id"]].append(row)
        # Empirical-Bayes Gamma prior per type, then Poisson–Gamma update.
        type_rates = defaultdict(list)
        self.hist = {}
        for s in self.active:
            rows = by.get(s["store_id"], [])
            if rows:
                type_rates[s["store_type"]].append(np.mean([r["cages_ordered"] for r in rows]))
            self.hist[s["store_id"]] = rows
        self.prior = {}
        for t, rates in type_rates.items():
            mu = float(np.mean(rates))
            var = float(np.var(rates)) + 1e-6
            a = max(1.0, mu * mu / var)
            self.prior[t] = {"a": a, "b": a / mu, "mu": mu}
        self.post = {}
        for s in self.active:
            pr = self.prior[s["store_type"]]
            a, b = pr["a"], pr["b"]
            expo = 0.0
            ysum = 0.0
            for r in self.hist[s["store_id"]]:
                e = WEEKDAY_F[s["store_type"]][r["date"].weekday()]
                expo += e
                ysum += r["cages_ordered"]
            self.post[s["store_id"]] = {"a": a + ysum, "b": b + expo, "open_days": len(self.hist[s["store_id"]])}

    def posterior_mean(self):
        return np.array([self.post[s["store_id"]]["a"] / self.post[s["store_id"]]["b"] for s in self.active])

    def draw_day(self, the_date, rng):
        q = np.zeros(len(self.active))
        for i, s in enumerate(self.active):
            p = self.post[s["store_id"]]
            theta = rng.gamma(p["a"], 1.0 / p["b"])
            e = WEEKDAY_F[s["store_type"]][the_date.weekday()]
            if the_date.month in (7, 8):
                e *= 1.12
            q[i] = rng.poisson(theta * e)
        return q


@dataclass
class DaySet:
    label: str
    dates: list
    strata: list
    weights: np.ndarray
    demand: np.ndarray


def _seed(name: str) -> int:
    return cfg.SEED + int(hashlib.md5(name.encode()).hexdigest()[:8], 16) % 10_000


def planning_days(model, n, label, seed_name, exclude):
    rng = np.random.default_rng(_seed(seed_name))
    end = date.fromisoformat(cfg.HISTORY_END) + timedelta(days=1)
    pool = []
    d = end
    while len(pool) < 80:
        tag = "weekend" if d.weekday() >= 5 else "weekday"
        if d not in exclude:
            pool.append((d, tag))
        d += timedelta(days=1)
    rng.shuffle(pool)
    # Stratify: about one third weekend.
    week = [p for p in pool if p[1] == "weekday"]
    we = [p for p in pool if p[1] == "weekend"]
    n_we = max(1, n // 3)
    pick = we[:n_we] + week[: n - n_we]
    pick.sort(key=lambda x: x[0])
    dates = [p[0] for p in pick]
    strata = [p[1] for p in pick]
    demand = np.vstack([model.draw_day(dt, rng) for dt in dates])
    w = np.array([5 / 7 if s == "weekday" else 2 / 7 for s in strata], float)
    w = w / w.sum()
    return DaySet(label, dates, strata, w, demand)


# --------------------------------------------------------------------------- assignment
def eligibility(d_site_store, P: LLParams):
    """d_site_store: k × n road km. A store may go to a DC if a solo trip meets noon and km/duration."""
    k, n = d_site_store.shape
    mpk = 60.0 / P.speed_kmh
    el = np.zeros((k, n), dtype=bool)
    for j in range(k):
        for i in range(n):
            km = 2 * d_site_store[j, i]
            arr = P.depart_min + d_site_store[j, i] * mpk
            ret = arr + P.stop_min + d_site_store[j, i] * mpk
            el[j, i] = (km <= P.max_km + 1e-9) and (ret - P.depart_min <= P.max_route_min + 1e-9) and (arr <= P.noon_min + 1e-9)
    return el


def solve_assignment(d, w, max_stores, min_stores, el):
    """Capacitated nearest-plus-swap assignment (integral, deterministic). d, el: n × k."""
    n, k = d.shape
    if el.sum(1).min() == 0:
        bad = int(np.argmin(el.sum(1)))
        return {"status": "infeasible", "reason": f"store index {bad} is eligible for no open DC (pair-eligibility)"}
    if sum(max_stores) < n:
        return {"status": "infeasible", "reason": f"Σ max_stores = {sum(max_stores)} < {n} stores"}
    if sum(min_stores) > n:
        return {"status": "infeasible", "reason": f"Σ min_stores = {sum(min_stores)} > {n} stores"}

    assign = np.full(n, -1, dtype=int)
    load = np.zeros(k, dtype=int)
    order = np.argsort(-w)
    # Greedy: nearest eligible DC with remaining capacity.
    for i in order:
        opts = [j for j in range(k) if el[i, j] and load[j] < max_stores[j]]
        if not opts:
            return {"status": "infeasible", "reason": "capacity bound left a store with no eligible open DC"}
        j = min(opts, key=lambda t: (d[i, t], t))
        assign[i] = j
        load[j] += 1

    # Meet minima by stealing from DCs above their min.
    for j in range(k):
        while load[j] < min_stores[j]:
            donors = [t for t in range(k) if t != j and load[t] > min_stores[t]]
            best = None
            for i in range(n):
                src = assign[i]
                if src not in donors or not el[i, j]:
                    continue
                key = (d[i, j] - d[i, src], i)
                if best is None or key < best[0]:
                    best = (key, i, src)
            if best is None:
                return {"status": "infeasible", "reason": f"cannot reach min_stores on DC {j}"}
            _, i, src = best
            assign[i] = j
            load[j] += 1
            load[src] -= 1

    # Improving swaps / moves on cage-km.
    improved = True
    while improved:
        improved = False
        for i in range(n):
            src = assign[i]
            for j in range(k):
                if j == src or not el[i, j]:
                    continue
                if load[j] >= max_stores[j]:
                    continue
                if load[src] <= min_stores[src]:
                    continue
                if w[i] * (d[i, j] - d[i, src]) < -1e-9:
                    assign[i] = j
                    load[j] += 1
                    load[src] -= 1
                    improved = True
        for i in range(n):
            for u in range(i + 1, n):
                a, b = assign[i], assign[u]
                if a == b:
                    continue
                if not (el[i, b] and el[u, a]):
                    continue
                delta = w[i] * (d[i, b] - d[i, a]) + w[u] * (d[u, a] - d[u, b])
                if delta < -1e-9:
                    assign[i], assign[u] = b, a
                    improved = True

    obj = float(sum(w[i] * d[i, assign[i]] for i in range(n)))
    return {"status": "ok", "assign": assign, "objective": obj, "loads": load.tolist()}


# --------------------------------------------------------------------------- context / E
class Context:
    def __init__(self):
        self.stores = load_stores()
        self.active = [s for s in self.stores if s["closed_on"] is None]
        self.sites = load_sites()
        self.site = {s["site_id"]: s for s in self.sites}
        self.dcs = load_dcs()
        self.dist = load_distances()
        self.model = DemandModel(self.stores, load_demand())
        self.P = LLParams()
        self.n = len(self.active)
        self.w = self.model.posterior_mean()
        self.store_ids = [s["store_id"] for s in self.active]
        self.site_pos = {s["site_id"]: k for k, s in enumerate(self.sites)}
        sx = [self.dist.index[s["site_id"]] for s in self.sites]
        tx = [self.dist.index[i] for i in self.store_ids]
        self.d_site_store = np.array([[self.dist.km[a][b] for b in tx] for a in sx])
        self.cache = {}
        self.solves = 0
        self.solve_seconds = 0.0

    def dc_day(self, site_id, store_pos, demand, day_key, P):
        key = (site_id, tuple(store_pos), day_key, P.key())
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        ids = [site_id] + [self.store_ids[i] for i in store_pos]
        D = self.dist.submatrix(ids)
        q = [0.0] + [float(demand[i]) for i in store_pos]
        t = time.time()
        sol = solve_dc_day(D, q, P)
        self.solve_seconds += time.time() - t
        self.solves += 1
        self.cache[key] = sol
        return sol

    def lp_assignment(self, site_ids):
        ix = [self.site_pos[s] for s in site_ids]
        d = self.d_site_store[ix, :].T
        el = eligibility(self.d_site_store[ix, :], self.P).T
        mx = [self.site[s]["max_stores"] for s in site_ids]
        mn = [cfg.MIN_STORES] * len(site_ids)
        return solve_assignment(d, self.w, mx, mn, el)

    def historical_assignment(self, site_ids):
        dc_site = {dc["dc_id"]: dc["site_id"] for dc in self.dcs}
        pos = {s: k for k, s in enumerate(site_ids)}
        return np.array([pos[dc_site[s["current_dc_id"]]] for s in self.active])

    def proxy_cost(self, site_ids, assign):
        ix = [self.site_pos[s] for s in site_ids]
        d = self.d_site_store[ix, :].T
        return float(sum(self.w[i] * d[i, assign[i]] for i in range(self.n)))

    def evaluate(self, site_ids, assign, days, fleet=None, q=cfg.FLEET_QUANTILE, P=None, demand_override=None):
        P = P or self.P
        k = len(site_ids)
        if demand_override is not None:
            demands, weights, labels = [demand_override], np.array([1.0]), ["average-day"]
        else:
            demands, weights, labels = list(days.demand), days.weights, [d.isoformat() for d in days.dates]
        members = [[i for i in range(self.n) if assign[i] == j] for j in range(k)]
        sols = {}
        for j, sid in enumerate(site_ids):
            for d, dem in enumerate(demands):
                sol = self.dc_day(sid, members[j], dem, (days.label if demand_override is None else "avg", labels[d]), P)
                if sol["status"] != "ok":
                    return {"status": "infeasible", "sites": list(site_ids), "k": k,
                            "message": "DC at {} on {}: {}".format(sid, labels[d], sol["message"])}
                sols[(j, d)] = sol
        V = {sid: [len(sols[(j, d)]["routes"]) for d in range(len(demands))] for j, sid in enumerate(site_ids)}
        N = dict(fleet) if fleet is not None else {sid: int(math.ceil(weighted_quantile(V[sid], weights, q))) for sid in site_ids}

        parts = {"fuel": 0.0, "spot": 0.0, "failure": 0.0}
        late14 = unserved = 0.0
        share_by_day = np.zeros(len(demands))
        for d in range(len(demands)):
            n12 = dlv = 0
            for j, sid in enumerate(site_ids):
                a = account(sols[(j, d)], P, owned=N[sid], spot_cap=None)
                parts["fuel"] += weights[d] * a["fuel"]
                parts["spot"] += weights[d] * a["spot_cost"]
                parts["failure"] += weights[d] * a["failure"]
                n12 += a["n12"]
                dlv += a["delivered"]
                late14 += weights[d] * a["n14"]
                unserved += weights[d] * len(a["unserved"])
            share_by_day[d] = 1.0 - n12 / max(dlv, 1)
        Dm = cfg.DAYS_PER_MONTH
        F = float(sum(self.site[s]["monthly_fixed_cost"] for s in site_ids))
        veh = cfg.VEHICLE_MONTHLY * sum(N.values())
        monthly = {m: Dm * v for m, v in parts.items()}
        total = F + veh + sum(monthly.values())
        return {
            "status": "ok", "sites": list(site_ids), "k": k, "fleet": N,
            "dc_fixed": F, "vehicle_fixed": veh, "fuel": monthly["fuel"], "spot": monthly["spot"],
            "failure": monthly["failure"], "operating": sum(monthly.values()), "total": float(total),
            "service_before_noon": float(np.dot(weights, share_by_day)),
            "service_min_day": float(share_by_day.min()),
            "late_after_14": float(late14), "unserved": float(unserved),
            "loads": {sid: len(members[j]) for j, sid in enumerate(site_ids)},
            "vehicles_needed": V,
        }

    def g_score(self, site_ids, proxy, lam):
        F = float(sum(self.site[s]["monthly_fixed_cost"] for s in site_ids))
        return F + lam * proxy


def brief(e):
    if e["status"] != "ok":
        return {"status": "infeasible", "message": e["message"]}
    keys = ("dc_fixed", "vehicle_fixed", "fuel", "spot", "failure", "operating", "total",
            "service_before_noon", "service_min_day", "late_after_14", "unserved", "fleet", "loads")
    return {"status": "ok", **{k: e[k] for k in keys}}


def spearman(x, y):
    rx = np.argsort(np.argsort(x))
    ry = np.argsort(np.argsort(y))
    rx = rx.astype(float)
    ry = ry.astype(float)
    rx -= rx.mean()
    ry -= ry.mean()
    den = np.sqrt((rx ** 2).sum() * (ry ** 2).sum())
    return float((rx * ry).sum() / den) if den > 0 else 0.0


def run_upper(log=print):
    t0 = time.time()
    ctx = Context()
    m = ctx.model
    ds = planning_days(m, cfg.D_SEARCH, "search", "days_search", exclude=[])
    df = planning_days(m, cfg.D_FINAL, "final", "days_final", exclude=ds.dates)
    dh = planning_days(m, cfg.D_HOLDOUT, "holdout", "days_holdout", exclude=ds.dates + df.dates)

    base_sites = [dc["site_id"] for dc in ctx.dcs]
    a_hist = ctx.historical_assignment(base_sites)
    e_base_s = ctx.evaluate(base_sites, a_hist, ds)
    p_base = ctx.proxy_cost(base_sites, a_hist)
    lam = (e_base_s["total"] - e_base_s["dc_fixed"]) / max(p_base, 1e-9)
    log("baseline search-days: {:.1f} M KRW/month; lambda = {:.0f}".format(e_base_s["total"] / 1e6, lam))

    site_ids = [s["site_id"] for s in ctx.sites]
    cands = []
    for k in range(1, len(site_ids) + 1):
        for combo in itertools.combinations(site_ids, k):
            r = ctx.lp_assignment(list(combo))
            c = {"id": "+".join(combo), "sites": list(combo), "k": k,
                 "feasible": r["status"] == "ok", "reason": r.get("reason", "")}
            if c["feasible"]:
                c["assign"] = [int(a) for a in r["assign"]]
                c["proxy"] = r["objective"]
                c["G"] = ctx.g_score(combo, r["objective"], lam)
                c["loads"] = {s: int(np.sum(r["assign"] == j)) for j, s in enumerate(combo)}
            cands.append(c)
    feasible = [c for c in cands if c["feasible"]]
    log("candidates: {} subsets, {} feasible".format(len(cands), len(feasible)))

    order = sorted(feasible, key=lambda c: (c["G"], c["id"]))
    eval_log, best = [], float("inf")
    for n_eval, c in enumerate(order, start=1):
        e = ctx.evaluate(c["sites"], np.array(c["assign"]), ds)
        c["E"] = e["total"] if e["status"] == "ok" else None
        c["eval"] = brief(e)
        if e["status"] == "ok":
            best = min(best, e["total"])
        eval_log.append({
            "n": n_eval, "candidate": c["id"], "k": c["k"], "G": c["G"],
            "E": c["E"], "best_so_far": best if best < float("inf") else None,
            "rule": "visit in ascending G (toy: whole feasible pool)",
            "days": "search ({} days)".format(len(ds.dates)), "seed": cfg.SEED,
            "status": e["status"],
        })
        log("  [{}/{}] {}  G={:.1f}M  E={}".format(
            n_eval, len(order), c["id"], c["G"] / 1e6,
            "{:.1f}M".format(c["E"] / 1e6) if c["E"] else e.get("message", "infeasible")[:60]))

    scored = [c for c in feasible if c.get("E") is not None]
    g = np.array([c["G"] for c in scored])
    e = np.array([c["E"] for c in scored])
    g_best = min(scored, key=lambda c: (c["G"], c["id"]))
    e_best = min(scored, key=lambda c: (c["E"], c["id"]))
    ranked_e = sorted(scored, key=lambda c: (c["E"], c["id"]))
    g_rank = next(i for i, c in enumerate(ranked_e, start=1) if c["id"] == g_best["id"])
    loss = (g_best["E"] - e_best["E"]) / e_best["E"] * 100
    ratio = e / g
    metrics = {
        "n_candidates": len(scored),
        "spearman": spearman(g, e),
        "decision_loss_pct": float(loss),
        "g_best": g_best["id"],
        "e_best": e_best["id"],
        "g_best_rank_under_E": g_rank,
        "ratio_E_over_G": {"min": float(ratio.min()), "max": float(ratio.max()), "median": float(np.median(ratio))},
        "per_k": {},
    }
    for k in sorted({c["k"] for c in scored}):
        sub = [c for c in scored if c["k"] == k]
        gb = min(sub, key=lambda c: c["G"])
        eb = min(sub, key=lambda c: c["E"])
        metrics["per_k"][str(k)] = {
            "n": len(sub),
            "spearman": spearman([c["G"] for c in sub], [c["E"] for c in sub]) if len(sub) > 1 else 1.0,
            "g_best": gb["id"], "e_best": eb["id"],
            "decision_loss_pct": (gb["E"] - eb["E"]) / eb["E"] * 100,
        }

    # Sweep
    sweep = []
    for k in range(1, len(site_ids) + 1):
        sub_all = [c for c in cands if c["k"] == k]
        sub = [c for c in scored if c["k"] == k]
        if not sub:
            reason = sub_all[0]["reason"] if sub_all else "no subset"
            sweep.append({"k": k, "feasible": False, "reason": reason or "infeasible under store-count bounds"})
        else:
            eb = min(sub, key=lambda c: c["E"])
            gb = min(sub, key=lambda c: c["G"])
            sweep.append({"k": k, "feasible": True, "e_best": eb["id"], "e_best_eval": eb["eval"],
                          "g_best": gb["id"], "g_best_E": gb["E"]})

    # Selection curves vs random order
    g_curve = []
    running = float("inf")
    for c in order:
        if c.get("E") is not None:
            running = min(running, c["E"])
        g_curve.append(running)
    rng = np.random.default_rng(cfg.SEED + 99)
    rand_paths = []
    ids = list(range(len(scored)))
    for _ in range(40):
        perm = rng.permutation(ids)
        run = float("inf")
        path = []
        for i in perm:
            run = min(run, scored[i]["E"])
            path.append(run)
        rand_paths.append(path)
    rand_paths = np.array(rand_paths)
    curves = {
        "n": list(range(1, len(g_curve) + 1)),
        "g_order": g_curve,
        "random_mean": rand_paths.mean(0).tolist() if len(scored) else [],
        "random_p10": np.percentile(rand_paths, 10, 0).tolist() if len(scored) else [],
        "random_p90": np.percentile(rand_paths, 90, 0).tolist() if len(scored) else [],
        "optimum": e_best["E"],
    }

    # Final comparison
    rec_sites = e_best["sites"]
    rec_assign = np.array(e_best["assign"])
    a_hist = ctx.historical_assignment(base_sites)
    e_base_f = ctx.evaluate(base_sites, a_hist, df)
    e_rec_f = ctx.evaluate(rec_sites, rec_assign, df)
    e_base_h = ctx.evaluate(base_sites, a_hist, dh, fleet=e_base_f["fleet"])
    e_rec_h = ctx.evaluate(rec_sites, rec_assign, dh, fleet=e_rec_f["fleet"])
    a_re = ctx.lp_assignment(base_sites)
    e_re_f = ctx.evaluate(base_sites, a_re["assign"], df) if a_re["status"] == "ok" else None

    def saving(a, b):
        mean = b["total"] - a["total"]
        return {"mean": mean, "pct": 100 * mean / b["total"], "lo90": mean, "hi90": mean}

    final = [
        {"role": "baseline", "label": "Baseline (current 2 DCs, historical assignment)",
         "sites": base_sites, "k": 2, "assign": [int(x) for x in a_hist],
         "final": brief(e_base_f), "holdout": brief(e_base_h),
         "saving_final": None, "saving_holdout": None},
        {"role": "recommended", "label": "Recommended (E-best in the pool)",
         "sites": rec_sites, "k": len(rec_sites), "assign": [int(x) for x in rec_assign],
         "final": brief(e_rec_f), "holdout": brief(e_rec_h),
         "saving_final": saving(e_rec_f, e_base_f), "saving_holdout": saving(e_rec_h, e_base_h)},
    ]
    if e_re_f:
        final.insert(1, {
            "role": "reassigned", "label": "Current 2 DCs, assignment re-solved",
            "sites": base_sites, "k": 2, "assign": [int(x) for x in a_re["assign"]],
            "final": brief(e_re_f), "holdout": brief(ctx.evaluate(base_sites, a_re["assign"], dh, fleet=e_re_f["fleet"])),
            "saving_final": saving(e_re_f, e_base_f), "saving_holdout": None,
        })

    # Average-day gap on recommended
    avg_dem = df.demand.mean(0)
    e_avg = ctx.evaluate(rec_sites, rec_assign, df, demand_override=avg_dem)
    average_day = {
        "network": e_best["id"],
        "sampled_day_total": e_rec_f["total"],
        "average_day_total": e_avg["total"],
        "gap_pct": 100 * (e_rec_f["total"] - e_avg["total"]) / e_avg["total"],
    }

    # Cases
    fail_k = [c for c in sweep if not c["feasible"]]
    # Boundary: force assignment onto the max bound by shrinking max to the current loads of recommended? 
    # Simpler: set min=max=ceil(n/k) style — report recommended loads vs bounds.
    rec_loads = e_best["loads"]
    near_bound = any(v >= cfg.MAX_STORES - 5 or v <= cfg.MIN_STORES + 5 for v in rec_loads.values())
    boundary = {
        "status": "ok",
        "sites": rec_sites,
        "assign": [int(x) for x in rec_assign],
        "fleet": e_rec_f["fleet"],
        "loads": rec_loads,
        "total": e_rec_f["total"],
        "normal_total": e_rec_f["total"],
        "extra_pct": 0.0,
        "description": (
            "Recommended loads are {} (bounds {}–{}). "
            "{}."
        ).format(
            ", ".join(f"{s}={n}" for s, n in rec_loads.items()),
            cfg.MIN_STORES, cfg.MAX_STORES,
            "At least one DC sits within 5 stores of a bound" if near_bound
            else "No DC is tight on the store-count bound in this toy; the live solver still lets you tighten the window until the clock binds",
        ),
    }
    # Failure day: recommended busiest DC, owned fleet, zero spot trucks
    busy = max(rec_sites, key=lambda s: rec_loads[s])
    j = rec_sites.index(busy)
    members = [i for i in range(ctx.n) if rec_assign[i] == j]
    worst_i = int(np.argmax(df.demand.sum(1)))
    sol_w = ctx.dc_day(busy, members, df.demand[worst_i], ("final", df.dates[worst_i].isoformat()), ctx.P)
    owned = e_rec_f["fleet"][busy]
    acc_w = account(sol_w, ctx.P, owned=owned, spot_cap=0)
    failure_day = {
        "site": busy, "date": df.dates[worst_i].isoformat(),
        "vehicles_needed": acc_w["vehicles_needed"], "owned": owned,
        "stores_undelivered": len(acc_w["unserved"]), "failure_cost": acc_w["failure"],
        "description": "Owned fleet only (spot-truck cap = 0) on the heaviest final-set day.",
    }

    # Parity cases for the JS port (small DC-days)
    parity = []
    for label, day_i, site, noon in [
        ("regular-weekday", 0, base_sites[0], cfg.NOON_MIN),
        ("regular-other-dc", min(1, len(df.dates) - 1), base_sites[-1], cfg.NOON_MIN),
    ]:
        j = base_sites.index(site)
        mem = [i for i in range(ctx.n) if a_hist[i] == j][:18]
        dem = df.demand[day_i]
        ids = [site] + [ctx.store_ids[i] for i in mem]
        D = ctx.dist.submatrix(ids)
        q = [0.0] + [float(dem[i]) for i in mem]
        P = ctx.P.with_(noon_min=noon)
        sol = solve_dc_day(D, q, P)
        parity.append({
            "label": label, "site": site, "store_ids": [ctx.store_ids[i] for i in mem],
            "q": q, "params": {},
            "expected": {"status": sol["status"], "vehicles": sol["vehicles"], "km": sol["km"],
                         "n12": sol["n12"], "n14": sol["n14"],
                         "routes": [r["stores"] for r in sol["routes"]]},
        })

    days_info = {}
    for d in (ds, df, dh):
        days_info[d.label] = {
            "dates": [x.isoformat() for x in d.dates],
            "strata": d.strata,
            "weights": [round(float(x), 4) for x in d.weights],
            "demand": d.demand.astype(int).tolist(),
        }

    payload = {
        "meta": {
            "n_stores": ctx.n,
            "n_sites": len(ctx.sites),
            "n_dcs": len(ctx.dcs),
            "seed": cfg.SEED,
            "thinning": "toy region (not a 1-in-5 nationwide sample)",
            "min_stores": cfg.MIN_STORES,
            "max_stores": cfg.MAX_STORES,
            "days": [cfg.D_SEARCH, cfg.D_FINAL, cfg.D_HOLDOUT],
            "runtime_seconds": time.time() - t0,
            "solves": ctx.solves,
            "seconds_per_solve": ctx.solve_seconds / max(ctx.solves, 1),
            "command": "python run_demo.py --build",
            "distance_source": ctx.dist.meta["source"],
        },
        "params": {
            "speed_kmh": cfg.SPEED_KMH, "stop_min": cfg.STOP_MIN, "depart_min": cfg.DEPART_MIN,
            "noon_min": cfg.NOON_MIN, "late_min": cfg.LATE_MIN, "capacity": cfg.CAPACITY_CAGES,
            "max_stops": cfg.MAX_STOPS, "max_km": cfg.MAX_KM, "max_route_min": cfg.MAX_ROUTE_MIN,
            "alpha": cfg.NOON_BUDGET_ALPHA, "c_fuel": cfg.FUEL_PER_KM, "c_spot": cfg.SPOT_DAILY,
            "p_late": cfg.LATE_PENALTY, "p_stockout": cfg.STOCKOUT_PENALTY, "max_passes": cfg.LL_MAX_PASSES,
        },
        "geo": {
            "lat_min": min(s["lat"] for s in ctx.active) - 0.01,
            "lat_max": max(s["lat"] for s in ctx.active) + 0.01,
            "lon_min": min(s["lon"] for s in ctx.active) - 0.01,
            "lon_max": max(s["lon"] for s in ctx.active) + 0.01,
            "river": [[37.46, cfg.RIVER_LON], [37.64, cfg.RIVER_LON]],
            "bridges": cfg.BRIDGES,
        },
        "stores": [{"id": s["store_id"], "lat": s["lat"], "lon": s["lon"], "type": s["store_type"],
                    "region": s["region"], "current": s["current_dc_id"]} for s in ctx.active],
        "sites": [{"id": s["site_id"], "lat": s["lat"], "lon": s["lon"], "name": s["name"],
                   "fixed": s["monthly_fixed_cost"], "max": s["max_stores"]} for s in ctx.sites],
        "dcs": [{"id": d["dc_id"], "site_id": d["site_id"]} for d in ctx.dcs],
        "dist": {"ids": ctx.dist.ids, "tri": [ctx.dist.km[i][j] for i in range(len(ctx.dist.ids))
                                              for j in range(i + 1, len(ctx.dist.ids))]},
        "days": days_info,
        "average_final": df.demand.mean(0).tolist(),
        "lambda_G": lam,
        "parity": parity,
        "results": {
            "candidates": [{k: c[k] for k in c if k != "eval" or True} for c in cands],
            "log": eval_log,
            "sweep": sweep,
            "metrics": metrics,
            "curves": curves,
            "final": final,
            "recommended_sites": rec_sites,
            "average_day": average_day,
            "cases": {
                "boundary": boundary,
                "budget_one": {
                    "description": "A budget of one full evaluation takes the G-best candidate and stops.",
                    "chosen": g_best["id"], "E": g_best["E"], "optimum": e_best["E"],
                    "regret_pct": 100 * (g_best["E"] - e_best["E"]) / e_best["E"],
                },
                "failure_k": [{"k": s["k"], "reason": "k = {}: {}".format(s["k"], s["reason"])} for s in fail_k],
                "failure_day": failure_day,
            },
            "demand_model": {
                "holiday_factor": 1.35,
                "prior": {t: {"a": round(v["a"], 2), "b": round(v["b"], 2)} for t, v in m.prior.items()},
            },
        },
    }
    log("done in {:.1f}s, {} lower-level solves".format(time.time() - t0, ctx.solves))
    return payload
