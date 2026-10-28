"""
upper_level.py -- the upper level: how many DCs, and where (problem-definition.md, Section 2c).

Toy pipeline (brief, Section 5):  generator -> distances -> lower level -> E on the baseline
-> G candidates -> one comparison.  At toy scale the whole candidate pool is small (6 sites, so
63 subsets, 35 of them feasible), so every feasible candidate is evaluated in full.  That is NOT
the budgeted search of the full run (surrogate + acquisition, budget B); it lets the toy report
G-versus-E over the whole pool and compare "visit in G order" with "visit in random order".

Steps (run_upper):
  1. sampled demand days: search / final / held-out, disjoint dates, common random numbers;
  2. baseline (current DCs, historical assignment) on the search days -> calibrates lambda in G;
  3. every subset of the candidate sites: assignment LP, feasibility, proxy cost, G;
  4. full evaluation E of every feasible candidate on the search days, in G order (the log);
  5. prediction quality (G vs E), decision quality, search curves vs random order, sweep over k;
  6. final comparison on the final days and on held-out days (fleets fixed from the final days);
  7. average-day gap, normal / boundary / failure cases.
"""
import itertools
import json
import time

import numpy as np
from scipy.stats import pearsonr, spearmanr

import config as cfg
from evaluation import Context
from lower_level import account


# ---------------------------------------------------------------------------
def bootstrap_interval(diff, weights, seed_name="bootstrap", level=0.90):
    """Weighted mean of per-day differences and a percentile interval from resampling days."""
    diff, weights = np.asarray(diff, float), np.asarray(weights, float)
    mean = float(np.dot(diff, weights) / weights.sum())
    rng = np.random.default_rng(cfg.seed(seed_name))
    n = len(diff)
    draws = []
    for _ in range(cfg.N_BOOTSTRAP):
        ix = rng.integers(0, n, n)
        draws.append(np.dot(diff[ix], weights[ix]) / weights[ix].sum())
    lo, hi = np.percentile(draws, [50 * (1 - level), 100 - 50 * (1 - level)])
    return mean, float(lo), float(hi)


def centroid_sites(ctx, k):
    """Data Team's method: volume-weighted centre of gravity (k-means on map coordinates), snapped to the nearest site."""
    lat = np.array([s["lat"] for s in ctx.active]); lon = np.array([s["lon"] for s in ctx.active])
    x = (lon - lon.mean()) * cfg.KM_PER_DEG_LAT * np.cos(np.radians(lat.mean()))
    y = (lat - lat.mean()) * cfg.KM_PER_DEG_LAT
    pts, w = np.column_stack([x, y]), ctx.w
    cen = [pts[int(np.argmax(w))]]
    while len(cen) < k:                                           # farthest-point initialisation
        d = np.min([np.hypot(*(pts - c).T) for c in cen], axis=0)
        cen.append(pts[int(np.argmax(d * w))])
    cen = np.array(cen)
    for _ in range(100):
        lab = np.argmin([np.hypot(*(pts - c).T) for c in cen], axis=0)
        new = np.array([np.average(pts[lab == j], axis=0, weights=w[lab == j]) if (lab == j).any() else cen[j] for j in range(k)])
        if np.allclose(new, cen):
            break
        cen = new
    sx = (np.array([s["lon"] for s in ctx.sites]) - lon.mean()) * cfg.KM_PER_DEG_LAT * np.cos(np.radians(lat.mean()))
    sy = (np.array([s["lat"] for s in ctx.sites]) - lat.mean()) * cfg.KM_PER_DEG_LAT
    chosen = []
    for c in cen:
        order = np.argsort(np.hypot(sx - c[0], sy - c[1]))
        pick = next(int(j) for j in order if ctx.sites[int(j)]["site_id"] not in chosen)
        chosen.append(ctx.sites[pick]["site_id"])
    return chosen, cen.tolist()


def brief(e):
    """Compact record of an evaluation result (no per-day arrays)."""
    if e["status"] != "ok":
        return {"status": "infeasible", "message": e["message"]}
    keys = ("dc_fixed", "vehicle_fixed", "fuel", "spot", "failure", "operating", "total", "service_before_noon",
            "service_min_day", "late_after_14", "unserved", "fleet", "loads", "km_per_day", "spot_trucks_per_day")
    return {"status": "ok", **{k: e[k] for k in keys}}


def binding_summary(ctx, sites, assign, days):
    """Which limit ends a vehicle's day?  Shares of routes close to each limit (final days)."""
    P = ctx.P
    members = [[i for i in range(ctx.n) if assign[i] == j] for j in range(len(sites))]
    n = cap = clock = stops = km = 0
    for j, sid in enumerate(sites):
        for d, dem in enumerate(days.demand):
            sol = ctx.dc_day(sid, members[j], dem, (days.label, days.dates[d].isoformat()), P)
            for r in sol["routes"]:
                n += 1
                cap += r["load"] >= 0.95 * P.capacity
                clock += r["arrivals"][-1] >= P.noon_min - 30
                stops += len(r["stores"]) >= P.max_stops
                km += r["km"] >= 0.9 * P.max_km
    return {"routes": n, "load_within_5pct_of_capacity": cap / n, "last_stop_within_30min_of_noon": clock / n,
            "at_stop_limit": stops / n, "within_10pct_of_km_limit": km / n}


# ---------------------------------------------------------------------------
def run_upper(ctx=None, log=print):
    ctx = ctx or Context()
    m = ctx.model
    t0 = time.time()
    # ---- 1. sampled demand days (disjoint dates)
    ds = m.planning_days(cfg.D_SEARCH, "search", "days_search", "post_search")
    df = m.planning_days(cfg.D_FINAL, "final", "days_final", "post_final", exclude=ds.dates)
    dh = m.planning_days(cfg.D_HOLDOUT, "holdout", "days_holdout", "post_holdout", exclude=ds.dates + df.dates)
    days_info = {d.label: {"dates": [x.isoformat() for x in d.dates], "strata": d.strata,
                           "weights": [round(float(x), 4) for x in d.weights],
                           "total_cages": [int(x) for x in d.demand.sum(1)]} for d in (ds, df, dh)}

    # ---- 2. baseline and lambda
    base_sites = [dc["site_id"] for dc in ctx.dcs]
    a_hist = ctx.historical_assignment(base_sites)
    e_base_s = ctx.evaluate(base_sites, a_hist, ds)
    p_base = ctx.proxy_cost(base_sites, a_hist)
    lam = (e_base_s["total"] - e_base_s["dc_fixed"]) / p_base       # KRW/month per cage-km/day
    log("baseline on search days: total {:.1f} M KRW/month; lambda = {:.0f}".format(e_base_s["total"] / 1e6, lam))

    # ---- 3. all subsets of candidate sites
    site_ids = [s["site_id"] for s in ctx.sites]
    cands = []
    for k in range(1, len(site_ids) + 1):
        for combo in itertools.combinations(site_ids, k):
            r = ctx.lp_assignment(list(combo))
            c = {"id": "+".join(combo), "sites": list(combo), "k": k, "feasible": r["status"] == "ok", "reason": r.get("reason", "")}
            if c["feasible"]:
                c["assign"] = [int(a) for a in r["assign"]]
                c["proxy"] = r["objective"]
                c["G"] = ctx.g_score(combo, r["objective"], lam)
                c["loads"] = {s: int(np.sum(r["assign"] == j)) for j, s in enumerate(combo)}
            cands.append(c)
    feasible = [c for c in cands if c["feasible"]]
    log("candidates: {} subsets, {} feasible (k = {})".format(len(cands), len(feasible), sorted({c['k'] for c in feasible})))

    # ---- 4. full evaluation E in G order
    order = sorted(feasible, key=lambda c: (c["G"], c["id"]))
    eval_log, best = [], float("inf")
    for n_eval, c in enumerate(order, start=1):
        e = ctx.evaluate(c["sites"], np.array(c["assign"]), ds)
        c["E"] = e["total"]
        c["eval"] = brief(e)
        best = min(best, e["total"])
        eval_log.append({"n": n_eval, "candidate": c["id"], "k": c["k"], "G": c["G"], "E": e["total"], "best_so_far": best,
                         "rule": "visit in ascending G (toy: the whole feasible pool is evaluated)",
                         "days": "search ({} days)".format(len(ds.dates)), "seed": cfg.MASTER_SEED})
    log("evaluated {} candidates in {:.0f}s ({} lower-level solves, {:.2f}s each on average)".format(
        len(order), time.time() - t0, ctx.solves, ctx.solve_seconds / max(ctx.solves, 1)))

    # ---- 5. prediction quality, decision quality, curves, sweep
    G = np.array([c["G"] for c in feasible]); E = np.array([c["E"] for c in feasible])
    ratio = E / G
    g_best = min(feasible, key=lambda c: (c["G"], c["id"]))
    e_best = min(feasible, key=lambda c: (c["E"], c["id"]))
    per_k = {}
    for k in sorted({c["k"] for c in feasible}):
        ck = [c for c in feasible if c["k"] == k]
        gb, eb = min(ck, key=lambda c: c["G"]), min(ck, key=lambda c: c["E"])
        per_k[k] = {"n": len(ck), "spearman": float(spearmanr([c["G"] for c in ck], [c["E"] for c in ck])[0]),
                    "g_best": gb["id"], "e_best": eb["id"],
                    "decision_loss_pct": float(100 * (gb["E"] - eb["E"]) / eb["E"])}
    metrics = {
        "n_candidates": len(feasible),
        "spearman": float(spearmanr(G, E)[0]), "pearson": float(pearsonr(G, E)[0]),
        "g_best": g_best["id"], "g_best_k": g_best["k"], "g_best_E": g_best["E"],
        "e_best": e_best["id"], "e_best_k": e_best["k"], "e_best_E": e_best["E"],
        "decision_loss_pct": float(100 * (g_best["E"] - e_best["E"]) / e_best["E"]),
        "g_best_rank_under_E": int(1 + np.sum(E < g_best["E"])),
        "ratio_E_over_G": {"min": float(ratio.min()), "median": float(np.median(ratio)), "max": float(ratio.max())},
        "per_k": per_k,
    }
    # G-order versus random order: best-so-far after n evaluations
    rng = np.random.default_rng(cfg.seed("random_orders"))
    curves = np.array([np.minimum.accumulate(E[rng.permutation(len(E))]) for _ in range(cfg.N_RANDOM_ORDERS)])
    gcurve = np.minimum.accumulate(np.array([c["E"] for c in order]))
    curves_out = {"n": list(range(1, len(E) + 1)), "g_order": gcurve.tolist(), "random_mean": curves.mean(0).tolist(),
                  "random_p10": np.percentile(curves, 10, axis=0).tolist(), "random_p90": np.percentile(curves, 90, axis=0).tolist(),
                  "optimum": float(E.min())}
    sweep = []
    for k in range(1, len(site_ids) + 1):
        ck = [c for c in cands if c["k"] == k]
        fk = [c for c in ck if c["feasible"]]
        if not fk:
            sweep.append({"k": k, "feasible": False, "reason": ck[0]["reason"]})
            continue
        eb, gb = min(fk, key=lambda c: c["E"]), min(fk, key=lambda c: c["G"])
        sweep.append({"k": k, "feasible": True, "e_best": eb["id"], "e_best_eval": eb["eval"], "g_best": gb["id"],
                      "g_best_E": gb["E"], "g_best_G": gb["G"], "e_best_G": eb["G"]})

    # ---- 6. final comparison (final days; fleets then fixed for the held-out days)
    cen_sites, cen_xy = centroid_sites(ctx, len(base_sites))
    cen_id = "+".join(sorted(cen_sites, key=site_ids.index))
    cands_by_id = {c["id"]: c for c in cands}
    entries = [("Baseline (current DCs, historical assignment)", base_sites, a_hist, "baseline"),
               ("Baseline, assignment re-solved", base_sites, np.array(ctx.lp_assignment(base_sites)["assign"]), "baseline-lp")]
    shortlist = []
    for label, cid, role in [("G-best over all k", g_best["id"], "g-best"),
                             ("Best by E, k = {}".format(len(base_sites)), None, "e-best-k2"),
                             ("Data Team centroid (map distance), snapped", cen_id, "centroid")]:
        if cid is None:
            ck = [c for c in feasible if c["k"] == len(base_sites)]
            cid = min(ck, key=lambda c: c["E"])["id"]
        c = cands_by_id.get(cid)
        if c and c["feasible"]:
            shortlist.append((label, c["sites"], np.array(c["assign"]), role))
    k_other = sorted({c["k"] for c in feasible} - {len(base_sites)})
    for k in k_other:
        ck = [c for c in feasible if c["k"] == k]
        c = min(ck, key=lambda c: c["E"])
        shortlist.append(("Best by E, k = {}".format(k), c["sites"], np.array(c["assign"]), "e-best-k{}".format(k)))
    seen, final_entries = {}, []
    for lab, sites_, asg, role in entries + shortlist:
        key = (tuple(sites_), tuple(int(x) for x in asg))
        if key in seen:                                   # same network under another rule: merge labels
            i = seen[key]
            final_entries[i] = (final_entries[i][0] + " = " + lab, *final_entries[i][1:])
            continue
        seen[key] = len(final_entries)
        final_entries.append((lab, sites_, asg, role))
    evals = []
    for lab, sites_, asg, role in final_entries:
        ef = ctx.evaluate(sites_, asg, df)
        eh = ctx.evaluate(sites_, asg, dh, fleet=ef["fleet"])          # fleet fixed from the final days
        evals.append((ef, eh))
        log("  final {:46s} total {:7.1f} M | held-out {:7.1f} M".format(lab, ef["total"] / 1e6, eh["total"] / 1e6))
    ef_base, eh_base = evals[0]                                        # entries[0] is the baseline
    final_rows = []
    for (lab, sites_, asg, role), (ef, eh) in zip(final_entries, evals):
        row = {"label": lab, "role": role, "sites": sites_, "k": len(sites_), "assign": [int(a) for a in asg],
               "final": brief(ef), "holdout": brief(eh), "per_day_final": ef["per_day_total"], "per_day_holdout": eh["per_day_total"]}
        if role != "baseline":
            for tag, e, b in (("final", ef, ef_base), ("holdout", eh, eh_base)):
                mean, lo, hi = bootstrap_interval(np.array(b["per_day_total"]) - np.array(e["per_day_total"]), e["weights"])
                row["saving_" + tag] = {"mean": mean, "lo90": lo, "hi90": hi, "pct": 100 * mean / b["total"]}
        final_rows.append(row)
    movers = [r for r in final_rows if r["role"] not in ("baseline", "baseline-lp")]
    recommended = min(movers, key=lambda r: r["final"]["total"])

    # ---- 7. average-day gap, cases
    rec_asg = np.array(recommended["assign"])
    e_avg = ctx.evaluate(recommended["sites"], rec_asg, df, demand_override=df.average_day)
    avg_day = {"network": recommended["label"], "sampled_day_total": recommended["final"]["total"],
               "average_day_total": e_avg["total"],
               "gap_pct": 100 * (recommended["final"]["total"] - e_avg["total"]) / recommended["final"]["total"],
               "sampled_day_operating": recommended["final"]["operating"] + recommended["final"]["vehicle_fixed"],
               "average_day_operating": e_avg["operating"] + e_avg["vehicle_fixed"]}

    # boundary: both DCs exactly at the store-count bound (125 each)
    bound = -(-ctx.n // len(recommended["sites"]))
    rb = ctx.lp_assignment(recommended["sites"], max_override=bound)
    boundary = {"description": "Same sites as the recommended network, store-count bound lowered to ceil(n/k) = {} per DC, so every DC sits exactly at the bound.".format(bound)}
    if rb["status"] == "ok":
        eb_ = ctx.evaluate(recommended["sites"], np.array(rb["assign"]), df)
        boundary.update({"status": "ok", "sites": recommended["sites"], "assign": [int(a) for a in rb["assign"]], "fleet": eb_["fleet"],
                         "loads": eb_["loads"], "total": eb_["total"], "normal_total": recommended["final"]["total"],
                         "extra_pct": 100 * (eb_["total"] - recommended["final"]["total"]) / recommended["final"]["total"]})
    else:
        boundary.update({"status": "infeasible", "reason": rb["reason"]})
    budget1 = {"description": "Budget of one evaluation: evaluate only the G-best candidate.",
               "chosen": g_best["id"], "E": g_best["E"], "optimum": e_best["E"],
               "regret_pct": 100 * (g_best["E"] - e_best["E"]) / e_best["E"]}
    # failure: a day the fleet cannot cover (spot trucks unavailable)
    rec_f = ctx.evaluate(recommended["sites"], rec_asg, df)
    worst = int(np.argmax(df.demand.sum(1)))
    busiest = max(rec_f["fleet"], key=lambda s: rec_f["vehicles_needed"][s][worst])
    j = recommended["sites"].index(busiest)
    mem = [i for i in range(ctx.n) if rec_asg[i] == j]
    sol = ctx.dc_day(busiest, mem, df.demand[worst], ("final", df.dates[worst].isoformat()), ctx.P)
    n_owned = rec_f["fleet"][busiest]
    a_cap = account(sol, ctx.P, owned=n_owned, spot_cap=0)
    failure_day = {"description": "Heaviest final day, busiest DC, no spot trucks available (cap 0): the owned fleet cannot cover the day.",
                   "site": busiest, "date": df.dates[worst].isoformat(), "vehicles_needed": len(sol["routes"]), "owned": n_owned,
                   "stores_undelivered": len(a_cap["unserved"]), "failure_cost": a_cap["failure"]}
    failure_k = [{"k": s["k"], "reason": s["reason"]} for s in sweep if not s["feasible"]]

    binding = {"baseline": binding_summary(ctx, base_sites, a_hist, df),
               "recommended": binding_summary(ctx, recommended["sites"], rec_asg, df)}
    timing = {}
    by_n = {}
    for n_st, sec in ctx.timings:
        by_n.setdefault(int(round(n_st / 25.0)) * 25, []).append(sec)
    timing["seconds_per_solve_by_stores"] = {str(k): {"median": float(np.median(v)), "p95": float(np.percentile(v, 95)), "count": len(v)} for k, v in sorted(by_n.items())}
    timing["solves"], timing["cache_hits"] = ctx.solves, ctx.cache_hits
    timing["upper_level_seconds"] = time.time() - t0

    return {
        "lambda": lam, "days": days_info, "baseline_search": brief(e_base_s), "baseline_proxy": p_base,
        "candidates": [{k: v for k, v in c.items() if k != "eval"} | ({"eval": c["eval"]} if "eval" in c else {}) for c in cands],
        "log": eval_log, "metrics": metrics, "curves": curves_out, "sweep": sweep,
        "centroid": {"sites": cen_sites, "xy_km": cen_xy, "id": cen_id},
        "final": final_rows, "recommended": recommended["label"], "recommended_sites": recommended["sites"],
        "average_day": avg_day, "cases": {"boundary": boundary, "budget_one": budget1, "failure_day": failure_day, "failure_k": failure_k},
        "binding": binding, "timing": timing,
        "days_demand": {d.label: d.demand.astype(int).tolist() for d in (ds, df, dh)},
        "average_day_final": df.average_day.tolist(),
    }