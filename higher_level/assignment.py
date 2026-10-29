"""Exact capacitated store assignment using the integral transportation LP."""
import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix, vstack


def eligibility(distances, params):
    d = np.asarray(distances, dtype=float)
    return ((2 * d <= params.max_km + 1e-9)
            & (2 * d * 60 / params.speed_kmh + params.stop_min <= params.max_route_min + 1e-9)
            & (params.depart_min + d * 60 / params.speed_kmh <= params.noon_min + 1e-9))


def solve_assignment(distances, volumes, minimum, maximum, eligible=None):
    """Arrays are stores x open sites. Return indices into the supplied site order.

    Infeasibility is a domain result; malformed data raises ValueError. A greedy
    failure is never used as a certificate of infeasibility.
    """
    d, w = np.asarray(distances, float), np.asarray(volumes, float)
    lo, hi = np.asarray(minimum, float), np.asarray(maximum, float)
    if d.ndim != 2 or not d.shape[1] or w.shape != (d.shape[0],) or lo.shape != (d.shape[1],) or hi.shape != lo.shape:
        raise ValueError("assignment dimensions must be stores x sites with matching weights and bounds")
    if not all(np.isfinite(a).all() for a in (d, w, lo, hi)) or (d < 0).any() or (w < 0).any():
        raise ValueError("assignment distances/volumes/bounds must be finite and nonnegative")
    if (lo < 0).any() or (hi < lo).any() or (lo != np.floor(lo)).any() or (hi != np.floor(hi)).any():
        raise ValueError("assignment bounds must be nonnegative integers with min <= max")
    n, k = d.shape
    el = np.ones(d.shape, bool) if eligible is None else np.asarray(eligible, bool)
    if el.shape != d.shape:
        raise ValueError("eligibility shape must match the distance matrix")
    uncovered = np.flatnonzero(~el.any(axis=1)).tolist()
    if uncovered:
        return {"status": "infeasible", "reason": "stores eligible for no open site", "uncovered_indices": uncovered}
    if lo.sum() > n or hi.sum() < n:
        return {"status": "infeasible", "reason": f"store-count bounds exclude n={n}: sum(min)={lo.sum():g}, sum(max)={hi.sum():g}"}
    if n == 0:
        return {"status": "ok", "assign": np.empty(0, int), "loads": [0] * k, "objective": 0.0, "optimality_proven": True}
    i, j = np.nonzero(el)
    arcs = np.arange(len(i))
    stores = coo_matrix((np.ones(len(i)), (i, arcs)), shape=(n, len(i))).tocsr()
    sites = coo_matrix((np.ones(len(j)), (j, arcs)), shape=(k, len(i))).tocsr()
    costs = w[i] * d[i, j]
    scale = max(float(costs.max()), 1.0)
    res = linprog(costs / scale, A_ub=vstack([sites, -sites]), b_ub=np.r_[hi, -lo],
                  A_eq=stores, b_eq=np.ones(n), bounds=(0, 1), method="highs-ds")
    if res.status == 2:
        return {"status": "infeasible", "reason": "transportation LP infeasible (eligibility/capacity conflict)", "solver_message": res.message}
    if not res.success:
        raise RuntimeError(f"assignment LP failed: {res.message}")
    rounded = np.rint(res.x)
    if np.max(np.abs(rounded - res.x)) > 1e-5:
        raise RuntimeError("transportation LP returned a fractional solution; refusing unsafe rounding")
    chosen = rounded > .5
    assignment = np.full(n, -1, int)
    assignment[i[chosen]] = j[chosen]
    loads = np.bincount(assignment, minlength=k)
    if chosen.sum() != n or (assignment < 0).any() or (loads < lo).any() or (loads > hi).any():
        raise RuntimeError("assignment LP solution failed independent coverage/capacity validation")
    return {"status": "ok", "assign": assignment, "loads": loads.tolist(),
            "objective": float(np.sum(w * d[np.arange(n), assignment])), "optimality_proven": True}
