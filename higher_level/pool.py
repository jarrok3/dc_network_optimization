"""Capacitated p-median seeds, knob variants and feasible one-site neighbours."""
from dataclasses import asdict, dataclass
from time import perf_counter

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from distances.calibrate import haversine
from project import seed_sequence


@dataclass
class Candidate:
    sites: tuple
    G: float
    proxy: float
    features: list
    origin: str
    quantile: float = 2 / 3
    vehicle_proxy_term: float = 0.0

    @property
    def id(self):
        return "+".join(self.sites)

    def to_dict(self):
        return {"id": self.id, **asdict(self)}


def p_median(context, k, multiplier, *, beta=1.0, spacing_km=0.0, time_limit=20.0):
    """Exact MIP formulation, possibly time limited; only site variables are binary.

    Given integral site decisions, the assignment polytope is integral. Final
    assignments are always recovered with the exact transportation LP.
    """
    ctx = context
    if not isinstance(k, int) or not 1 <= k <= ctx.m or multiplier <= 0 or beta < 0 or spacing_km < 0 or time_limit <= 0:
        raise ValueError("invalid p-median k, multiplier, exponent, spacing or time limit")
    started = perf_counter()
    i, j = np.nonzero(ctx.eligible)
    e, m, n = len(i), ctx.m, ctx.n
    x = np.arange(e)
    y = np.arange(e, e + m)
    rows = np.r_[i, n + j, n + m + j, n + np.arange(m), n + m + np.arange(m), np.full(m, n + 2 * m)]
    cols = np.r_[x, x, x, y, y, y]
    data = np.r_[np.ones(3 * e), -ctx.minimum, -ctx.maximum, np.ones(m)]
    lower = np.r_[np.ones(n), np.zeros(m), np.full(m, -np.inf), k]
    upper = np.r_[np.ones(n), np.full(m, np.inf), np.zeros(m), k]
    if spacing_km:
        if ctx.site_lat_lon is None:
            raise ValueError("site coordinates required for spacing variants")
        lat, lon = ctx.site_lat_lon.T
        distances = haversine(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
        a, b = np.nonzero(np.triu(distances < spacing_km, 1))
        r = len(lower) + np.arange(len(a))
        rows, cols, data = np.r_[rows, r, r], np.r_[cols, e + a, e + b], np.r_[data, np.ones(2 * len(a))]
        lower, upper = np.r_[lower, np.full(len(a), -np.inf)], np.r_[upper, np.ones(len(a))]
    A = coo_matrix((data, (rows, cols)), shape=(len(lower), e + m)).tocsc()
    # Optimize in millions of KRW to avoid unnecessary objective scaling problems.
    objective = np.r_[multiplier * ctx.volumes[i] ** beta * ctx.road[i, j], ctx.fixed_costs] / 1e6
    result = milp(objective, integrality=np.r_[np.zeros(e), np.ones(m)],
                  bounds=Bounds(np.zeros(e + m), np.ones(e + m)),
                  constraints=LinearConstraint(A, lower, upper), options={"time_limit": float(time_limit), "mip_rel_gap": .001})
    meta = {"k": k, "status": "infeasible" if result.status == 2 else "unknown", "message": result.message,
            "beta": beta, "spacing_km": spacing_km, "seconds": perf_counter() - started,
            "optimality_proven": False, "mip_gap": None, "bound_krw": None}
    if getattr(result, "mip_dual_bound", None) is not None and np.isfinite(result.mip_dual_bound):
        meta["bound_krw"] = float(result.mip_dual_bound * 1e6)
    if result.x is None:
        return meta
    selected = np.flatnonzero(result.x[e:] > .5)
    if len(selected) != k:
        return meta
    sites = tuple(ctx.site_ids[t] for t in selected)
    assignment = ctx.assignment(sites)
    if assignment["status"] != "ok":
        raise RuntimeError("p-median incumbent failed exact assignment recovery")
    gap = getattr(result, "mip_gap", None)
    meta.update(status="ok", sites=list(sites), mip_gap=float(gap) if gap is not None and np.isfinite(gap) else None,
                objective_krw=float(result.fun * 1e6),
                optimality_proven=bool(result.status == 0 and gap is not None and gap <= 1e-8))
    return meta


def greedy_fallback(ctx, k, rng, attempts=50):
    """Deterministic seeded fallback; failure means unknown, never proven infeasible."""
    forced = sorted(set(int(np.flatnonzero(row)[0]) for row in ctx.eligible if row.sum() == 1))
    if len(forced) > k:
        return None
    for attempt in range(attempts):
        selected = list(forced)
        while len(selected) < k:
            opts = [j for j in range(ctx.m) if j not in selected]
            scores = []
            for j in opts:
                ix = selected + [j]
                d = np.where(ctx.eligible[:, ix], ctx.road[:, ix], 1e8)
                score = float(ctx.volumes @ d.min(axis=1))
                score += max(0, ctx.n - ctx.maximum[ix].sum()) * 1e7
                if attempt:
                    score *= float(rng.uniform(.8, 1.2))
                scores.append((score, ctx.site_ids[j], j))
            selected.append(min(scores)[2])
        sites = tuple(sorted(ctx.site_ids[j] for j in selected))
        if ctx.assignment(sites)["status"] == "ok":
            return sites
    return None


def build_pool(ctx, multiplier, *, size=200, k_values=None, seed=437, mip_time_limit=20, quantile=2 / 3, log=print):
    if not isinstance(size, int) or size < 1:
        raise ValueError("pool size must be a positive integer")
    ks = ctx.k_values() if k_values is None else sorted(set(k_values))
    if not ks or any(k not in ctx.k_values() for k in ks):
        raise ValueError("requested k values must be in the current capacity-based range")
    if size < len(ks):
        raise ValueError("pool size must allow at least one seed per requested k")
    rng = np.random.default_rng(seed_sequence("search", seed).spawn(2)[0])
    pool, seeds, certificates, rejected = {}, {}, {}, []

    def add(sites, origin):
        sites = ctx.canonical_sites(sites)
        if sites in pool:
            return pool[sites]
        assignment = ctx.assignment(sites)
        if assignment["status"] != "ok":
            return None
        G, features = ctx.proxy(sites, assignment, multiplier)
        candidate = Candidate(sites, G, assignment["objective"], features, origin, quantile)
        pool[sites] = candidate
        return candidate

    for k in ks:
        cert = p_median(ctx, k, multiplier, time_limit=mip_time_limit)
        certificates[k] = cert
        sites = cert.get("sites")
        if sites is None and cert["status"] != "infeasible":
            sites = greedy_fallback(ctx, k, rng)
        if sites is None:
            rejected.append({"k": k, "status": cert["status"], "reason": cert["message"]})
            log(f"k={k}: {cert['status']}; no seed", flush=True)
            continue
        candidate = add(sites, "p-median" if cert["status"] == "ok" else "greedy fallback")
        seeds[k] = candidate
        log(f"k={k}: seed G={candidate.G / 1e6:.3f} M KRW; MIP gap={cert['mip_gap']}", flush=True)
    if not seeds:
        return {"candidates": [], "seeds": [], "certificates": certificates, "rejected_k": rejected}
    quota = max(1, int(np.ceil(size / len(seeds))))
    # Geographic neighbours are cheap and retain the same k.
    for k, candidate in seeds.items():
        neighbours = []
        for opened in candidate.sites:
            closed = [s for s in ctx.site_ids if s not in candidate.sites]
            if ctx.site_lat_lon is not None:
                p = ctx.site_lat_lon[ctx.site_index[opened]]
                closed.sort(key=lambda s: (float(haversine(p[0], p[1], *ctx.site_lat_lon[ctx.site_index[s]])), s))
            for replacement in closed[:5]:
                neighbours.append(tuple(sorted((set(candidate.sites) - {opened}) | {replacement})))
        for neighbour in neighbours:
            if len(pool) >= size or sum(len(s) == k for s in pool) >= quota:
                break
            add(neighbour, "one-site neighbour")
        if sum(len(s) == k for s in pool) < quota:
            for beta, spacing in ((0, 0), (.5, 0), (1, 30), (0, 30), (.5, 30)):
                if len(pool) >= size or sum(len(s) == k for s in pool) >= quota:
                    break
                variant = p_median(ctx, k, multiplier, beta=beta, spacing_km=spacing, time_limit=min(5, mip_time_limit))
                if variant["status"] == "ok":
                    add(variant["sites"], f"beta={beta},spacing={spacing}")
    # Seeded swaps of existing candidates fill remaining capacity without an O(P*m*k) exhaustive sweep.
    for iteration in range(max(100, size * 30)):
        if len(pool) >= size:
            break
        k = sorted(seeds)[iteration % len(seeds)]
        candidates = sorted((c for c in pool.values() if len(c.sites) == k), key=lambda c: c.sites)
        base = candidates[int(rng.integers(len(candidates)))]
        closed = [s for s in ctx.site_ids if s not in base.sites]
        if not closed:
            break
        opened = base.sites[int(rng.integers(k))]
        replacement = closed[int(rng.integers(len(closed)))]
        add((set(base.sites) - {opened}) | {replacement}, "seeded random one-site swap")
    candidates = sorted(pool.values(), key=lambda c: (len(c.sites), c.sites))
    # The initial incumbent keeps its solver certificate. Other candidates remain challengers.
    return {"candidates": candidates, "seeds": [seeds[k].id for k in sorted(seeds)],
            "certificates": certificates, "rejected_k": rejected, "requested_size": size, "actual_size": len(candidates)}


def add_vehicle_proxy_term(ctx, pool):
    """Section 4.5 development remedy, applied once to the existing candidate pool.

    Add a monthly fleet approximation using volume, stops and available noon
    time. It is a proxy feature, not a route-feasibility certificate.
    """
    candidates = []
    P = ctx.params
    for candidate in pool["candidates"]:
        assignment = ctx.assignment(candidate.sites)
        estimate = 0
        for j, site in enumerate(candidate.sites):
            members = np.flatnonzero(assignment["assign"] == j)
            if not len(members):
                continue
            distance = ctx.road[members, ctx.site_index[site]].mean()
            minutes = max(0, P.noon_min - P.depart_min - 2 * distance * 60 / P.speed_kmh)
            stops_by_time = max(1, min(P.max_stops, int(np.floor(minutes / max(P.stop_min, 1e-9))) + 1))
            estimate += int(np.ceil(max(ctx.volumes[members].sum() / P.capacity,
                                        len(members) / P.max_stops, len(members) / stops_by_time)))
        term = ctx.vehicle_monthly * estimate
        features = list(candidate.features)
        G = candidate.G + term
        features[1] = float(np.log(G))
        candidates.append(Candidate(candidate.sites, G, candidate.proxy, features, candidate.origin,
                                    candidate.quantile, term))
    seed_ids = []
    for k in sorted({len(c.sites) for c in candidates}):
        seed_ids.append(min((c for c in candidates if len(c.sites) == k), key=lambda c: (c.G, c.sites)).id)
    return {**pool, "candidates": candidates, "seeds": seed_ids, "proxy_mode": "vehicle-term correction",
            "seed_status": "best corrected-G member per k in the generated pool; not a corrected-G global optimum",
            "certificate_objective": "MIP certificates apply to the original F + lambda*P before correction"}
