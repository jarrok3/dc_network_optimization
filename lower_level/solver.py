"""
One DC, one delivery day (problem-definition.md, Section 2a).

Clarke–Wright savings plus deterministic local search. The Python implementation
is the nationwide solver; the older browser demonstration is not its reference.
"""
from __future__ import annotations

import math
import sys
from numbers import Integral
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as cfg
import numpy as np

EPS = 1e-9
TOL = 1e-6


@dataclass(frozen=True)
class LLParams:
    speed_kmh: float = cfg.SPEED_KMH
    stop_min: float = cfg.STOP_MIN
    depart_min: float = cfg.DEPART_MIN
    noon_min: float = cfg.NOON_MIN
    late_min: float = cfg.LATE_MIN
    capacity: float = cfg.CAPACITY_CAGES
    max_stops: int = cfg.MAX_STOPS
    max_km: float = cfg.MAX_KM
    max_route_min: float = cfg.MAX_ROUTE_MIN
    alpha: float = cfg.NOON_BUDGET_ALPHA
    c_fuel: float = cfg.FUEL_PER_KM
    c_spot: float = cfg.SPOT_DAILY
    p_late: float = cfg.LATE_PENALTY
    p_stockout: float = cfg.STOCKOUT_PENALTY
    max_passes: int = cfg.LL_MAX_PASSES

    def __post_init__(self):
        positive = ("speed_kmh", "capacity", "max_km", "max_route_min")
        nonnegative = ("stop_min", "depart_min", "noon_min", "late_min", "c_fuel", "c_spot", "p_late", "p_stockout")
        for name in positive + nonnegative:
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0 or (name in positive and value == 0):
                raise ValueError(f"{name} must be finite and {'positive' if name in positive else 'nonnegative'}")
        if not math.isfinite(self.alpha) or not 0 <= self.alpha <= 1:
            raise ValueError("alpha must lie in [0, 1]")
        if self.depart_min > self.noon_min or self.noon_min > self.late_min:
            raise ValueError("times must satisfy depart_min <= noon_min <= late_min")
        for name, minimum in (("max_stops", 1), ("max_passes", 0)):
            if isinstance(getattr(self, name), bool) or not isinstance(getattr(self, name), Integral) or getattr(self, name) < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")

    def key(self):
        return tuple(self.__dict__.values())

    def with_(self, **kw):
        d = dict(self.__dict__)
        d.update(kw)
        return LLParams(**d)


class _Solver:
    def __init__(self, D, q, P, alpha, owned=0):
        self.D, self.q, self.P = D, q, P
        self.n = len(D) - 1
        a = P.alpha if alpha is None else alpha
        self.budget = int(math.floor(a * self.n + 1e-9))
        self.mpk = 60.0 / P.speed_kmh
        self.R, self.S = [], []
        self.owned = owned
        self.fixed_vehicles = 0

    def vehicle_saving(self, removed=1):
        v = len(self.R) + self.fixed_vehicles
        return self.P.c_spot * (max(0, v - self.owned) - max(0, v - removed - self.owned))

    def ev(self, seq):
        D, q, P, mpk = self.D, self.q, self.P, self.mpk
        km, t, prev, load, n12, n14 = 0.0, P.depart_min, 0, 0.0, 0, 0
        for s in seq:
            leg = D[prev][s]
            km += leg
            t += leg * mpk
            if t > P.noon_min + EPS:
                n12 += 1
            if t > P.late_min + EPS:
                n14 += 1
            t += P.stop_min
            load += q[s]
            prev = s
        leg = D[prev][0]
        km += leg
        t += leg * mpk
        return (km, t, load, n12, n14)

    def ok(self, seq, s, hard):
        P = self.P
        return (s[2] <= P.capacity + EPS and len(seq) <= P.max_stops and s[0] <= P.max_km + EPS
                and (s[1] - P.depart_min) <= P.max_route_min + EPS and (not hard or s[3] == 0))

    def savings(self, nodes):
        D, D0 = self.D, self.D[0]
        routes = {i: [i] for i in nodes}
        rid = {i: i for i in nodes}
        sav = []
        for a in range(len(nodes)):
            i = nodes[a]
            for b in range(a + 1, len(nodes)):
                j = nodes[b]
                s = D0[i] + D0[j] - D[i][j]
                if s > EPS:
                    sav.append((-s, i, j))
        sav.sort()
        for _, i, j in sav:
            ri, rj = rid[i], rid[j]
            if ri == rj:
                continue
            A, B = routes[ri], routes[rj]
            if A[-1] == i and B[0] == j:
                m = A + B
            elif A[0] == i and B[-1] == j:
                m = B + A
            elif A[-1] == i and B[-1] == j:
                m = A + B[::-1]
            elif A[0] == i and B[0] == j:
                m = A[::-1] + B
            else:
                continue
            s = self.ev(m)
            if self.ok(m, s, True):
                routes[ri] = m
                del routes[rj]
                for v in B:
                    rid[v] = ri
        self.R = sorted(routes.values(), key=lambda r: r[0])
        self.S = [self.ev(r) for r in self.R]

    def two_opt(self):
        imp = False
        for r in range(len(self.R)):
            again = True
            while again:
                again = False
                seq = self.R[r]
                m = len(seq)
                for a in range(m - 1):
                    for b in range(a + 1, m):
                        new = seq[:a] + seq[a:b + 1][::-1] + seq[b + 1:]
                        s = self.ev(new)
                        if self.ok(new, s, True) and s[0] < self.S[r][0] - 1e-7:
                            self.R[r], self.S[r] = new, s
                            imp = again = True
                            break
                    if again:
                        break
        return imp

    def relocate(self):
        P, q = self.P, self.q
        cf = P.c_fuel
        imp = False
        r1 = 0
        while r1 < len(self.R):
            p = 0
            deleted = False
            while p < len(self.R[r1]):
                seq1 = self.R[r1]
                node = seq1[p]
                new1 = seq1[:p] + seq1[p + 1:]
                s1 = self.ev(new1) if new1 else (0.0, 0.0, 0.0, 0, 0)
                if new1 and not self.ok(new1, s1, True):
                    p += 1
                    continue
                old1 = self.S[r1][0]
                best, best_delta = None, -TOL
                for r2 in range(len(self.R)):
                    if r2 == r1:
                        continue
                    seq2 = self.R[r2]
                    if self.S[r2][2] + q[node] > P.capacity + EPS or len(seq2) + 1 > P.max_stops:
                        continue
                    for pos in range(len(seq2) + 1):
                        prev = seq2[pos - 1] if pos else 0
                        nxt = seq2[pos] if pos < len(seq2) else 0
                        delta_km = s1[0] - old1 + self.D[prev][node] + self.D[node][nxt] - self.D[prev][nxt]
                        if cf * delta_km - (0 if new1 else self.vehicle_saving()) >= best_delta:
                            continue
                        new2 = seq2[:pos] + [node] + seq2[pos:]
                        s2 = self.ev(new2)
                        if not self.ok(new2, s2, True):
                            continue
                        delta = cf * (s1[0] + s2[0] - old1 - self.S[r2][0]) - (0.0 if new1 else self.vehicle_saving())
                        if delta < best_delta:
                            best_delta, best = delta, (r2, new2, s2)
                if best is not None:
                    r2, new2, s2 = best
                    self.R[r2], self.S[r2] = new2, s2
                    imp = True
                    if new1:
                        self.R[r1], self.S[r1] = new1, s1
                        continue
                    del self.R[r1]
                    del self.S[r1]
                    deleted = True
                    break
                p += 1
            if not deleted:
                r1 += 1
        return imp

    def swap(self):
        P, q = self.P, self.q
        cf = P.c_fuel
        imp = False
        for r1 in range(len(self.R)):
            for r2 in range(r1 + 1, len(self.R)):
                while True:
                    found = False
                    a, b = self.R[r1], self.R[r2]
                    for x in range(len(a)):
                        for y in range(len(b)):
                            i, j = a[x], b[y]
                            l1 = self.S[r1][2] - q[i] + q[j]
                            l2 = self.S[r2][2] - q[j] + q[i]
                            if l1 > P.capacity + EPS or l2 > P.capacity + EPS:
                                continue
                            ap, an = (a[x - 1] if x else 0), (a[x + 1] if x + 1 < len(a) else 0)
                            bp, bn = (b[y - 1] if y else 0), (b[y + 1] if y + 1 < len(b) else 0)
                            D = self.D
                            delta_km = (D[ap][j] + D[j][an] + D[bp][i] + D[i][bn]
                                        - D[ap][i] - D[i][an] - D[bp][j] - D[j][bn])
                            if cf * delta_km >= -TOL:
                                continue
                            n1 = a[:x] + [j] + a[x + 1:]
                            e1 = self.ev(n1)
                            if not self.ok(n1, e1, True):
                                continue
                            n2 = b[:y] + [i] + b[y + 1:]
                            e2 = self.ev(n2)
                            if not self.ok(n2, e2, True):
                                continue
                            if cf * (e1[0] + e2[0] - self.S[r1][0] - self.S[r2][0]) < -TOL:
                                self.R[r1], self.S[r1] = n1, e1
                                self.R[r2], self.S[r2] = n2, e2
                                found = True
                                break
                        if found:
                            break
                    if not found:
                        break
                    imp = True
        return imp

    def or_opt(self):
        """Move a consecutive block of one to three stops inside a route."""
        improved = False
        for r in range(len(self.R)):
            for _ in range(100):
                seq = self.R[r]
                found = False
                for size in range(1, min(3, len(seq) - 1) + 1):
                    for start in range(len(seq) - size + 1):
                        block = seq[start:start + size]
                        rest = seq[:start] + seq[start + size:]
                        for pos in range(len(rest) + 1):
                            new = rest[:pos] + block + rest[pos:]
                            stats = self.ev(new)
                            if self.P.c_fuel * (stats[0] - self.S[r][0]) < -TOL and self.ok(new, stats, True):
                                self.R[r], self.S[r] = new, stats
                                found = improved = True
                                break
                        if found:
                            break
                    if found:
                        break
                if not found:
                    break
        return improved

    def two_opt_star(self):
        """Exchange tails of two routes, accepting the first feasible cost improvement."""
        P = self.P
        for r1 in range(len(self.R)):
            for r2 in range(r1 + 1, len(self.R)):
                a, b = self.R[r1], self.R[r2]
                for x in range(len(a) + 1):
                    for y in range(len(b) + 1):
                        n1, n2 = a[:x] + b[y:], b[:y] + a[x:]
                        if len(n1) > P.max_stops or len(n2) > P.max_stops:
                            continue
                        removed = int(not n1) + int(not n2)
                        ap, an = (a[x - 1] if x else 0), (a[x] if x < len(a) else 0)
                        bp, bn = (b[y - 1] if y else 0), (b[y] if y < len(b) else 0)
                        D = self.D
                        delta = P.c_fuel * (D[ap][bn] + D[bp][an] - D[ap][an] - D[bp][bn]) - self.vehicle_saving(removed)
                        if delta >= -TOL:
                            continue
                        e1, e2 = self.ev(n1), self.ev(n2)
                        if not self.ok(n1, e1, True) or not self.ok(n2, e2, True):
                            continue
                        self.R[r1], self.S[r1] = n1, e1
                        self.R[r2], self.S[r2] = n2, e2
                        keep = [k for k, route in enumerate(self.R) if route]
                        self.R, self.S = [self.R[k] for k in keep], [self.S[k] for k in keep]
                        return True
        return False

    def _reinsert(self, r, hard, b_eff):
        P, q, D0 = self.P, self.q, self.D[0]
        cf, pl = P.c_fuel, P.p_late
        tmpR = [list(x) for k, x in enumerate(self.R) if k != r]
        tmpS = [self.S[k] for k in range(len(self.R)) if k != r]
        n12_tot = sum(s[3] for s in self.S) - self.S[r][3]
        for node in sorted(self.R[r], key=lambda v: (-D0[v], v)):
            best, bc = None, float("inf")
            for r2 in range(len(tmpR)):
                seq2 = tmpR[r2]
                if tmpS[r2][2] + q[node] > P.capacity + EPS or len(seq2) + 1 > P.max_stops:
                    continue
                for pos in range(len(seq2) + 1):
                    new2 = seq2[:pos] + [node] + seq2[pos:]
                    s2 = self.ev(new2)
                    if not self.ok(new2, s2, hard):
                        continue
                    dn12 = s2[3] - tmpS[r2][3]
                    if not hard and n12_tot + dn12 > b_eff:
                        continue
                    c = cf * (s2[0] - tmpS[r2][0]) + (0.0 if hard else pl * (s2[4] - tmpS[r2][4]))
                    if c < bc - 1e-12:
                        bc, best = c, (r2, new2, s2, dn12)
            if best is None:
                return None
            r2, new2, s2, dn12 = best
            tmpR[r2], tmpS[r2] = new2, s2
            n12_tot += dn12
        return tmpR, tmpS

    def eliminate(self, hard, b_eff=0):
        P = self.P
        imp = False
        while True:
            success = False
            for r in sorted(range(len(self.R)), key=lambda k: (len(self.R[k]), k)):
                if len(self.R) <= 1:
                    break
                res = self._reinsert(r, hard, b_eff)
                if res is None:
                    continue
                tmpR, tmpS = res
                new_km = old_km = 0.0
                for st in tmpS:
                    new_km += st[0]
                for st in self.S:
                    old_km += st[0]
                n14_new = sum(st[4] for st in tmpS)
                n14_old = sum(st[4] for st in self.S)
                delta = (P.c_fuel * (new_km - old_km)
                         + (0.0 if hard else P.p_late * (n14_new - n14_old))
                         - self.vehicle_saving())
                if delta < -TOL:
                    self.R, self.S = tmpR, tmpS
                    success = imp = True
                    break
            if not success:
                break
        return imp


def _solo(D, q, P, i):
    mpk = 60.0 / P.speed_kmh
    arr = P.depart_min + D[0][i] * mpk
    ret = arr + P.stop_min + D[i][0] * mpk
    return 2 * D[0][i], arr, ret


def _route_dict(sv, seq, forced):
    P, D, q = sv.P, sv.D, sv.q
    km, t, prev, arr = 0.0, P.depart_min, 0, []
    for s in seq:
        km += D[prev][s]
        t += D[prev][s] * sv.mpk
        arr.append(t)
        t += P.stop_min
        prev = s
    km += D[prev][0]
    t += D[prev][0] * sv.mpk
    return {"stores": list(seq), "km": km, "load": sum(q[s] for s in seq), "return_min": t, "arrivals": arr,
            "n12": sum(1 for a in arr if a > P.noon_min + EPS), "n14": sum(1 for a in arr if a > P.late_min + EPS),
            "forced_late": forced}


def _validate_inputs(D, q, P, alpha):
    matrix, demand = np.asarray(D, dtype=float), np.asarray(q, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] < 1 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError("D must be a square (n+1) x (n+1) distance matrix, including depot 0")
    if not np.isfinite(matrix).all() or (matrix < 0).any():
        raise ValueError("D must contain finite nonnegative road distances in km")
    if not np.allclose(matrix, matrix.T, rtol=0, atol=TOL) or not np.allclose(np.diag(matrix), 0, rtol=0, atol=EPS):
        raise ValueError("D must be symmetric with a zero diagonal (Section 2a)")
    if demand.ndim != 1 or len(demand) != len(matrix):
        raise ValueError("q must contain n+1 demands, including q[0]=0 for the depot")
    if not np.isfinite(demand).all() or (demand < 0).any() or demand[0] != 0:
        raise ValueError("q must be finite and nonnegative, with q[0]=0")
    if (demand > P.capacity + EPS).any():
        node = int(np.flatnonzero(demand > P.capacity + EPS)[0])
        raise ValueError(f"q[{node}]={demand[node]} exceeds vehicle capacity {P.capacity}; split deliveries are excluded")
    if alpha is not None and (not math.isfinite(alpha) or not 0 <= alpha <= 1):
        raise ValueError("alpha must lie in [0, 1]")
    return matrix.tolist(), demand.tolist()


def _fleet_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return int(value)


def _select_routes(routes, P, owned, spot_cap, economic):
    """Exact best subset of the fixed routes; this does not optimize their sequences."""
    V = len(routes)
    limit = V if spot_cap is None else min(V, owned + spot_cap)
    benefit = [P.p_stockout * len(r["stores"]) - P.c_fuel * r["km"] - P.p_late * r["n14"] for r in routes]
    order = sorted(range(V), key=lambda i: (-benefit[i], tuple(routes[i]["stores"])))
    if economic:
        best, gain, best_cost, delivered = 0, 0.0, 0.0, 0
        best_delivered = 0
        for k, index in enumerate(order[:limit], 1):
            gain += benefit[index]
            delivered += len(routes[index]["stores"])
            cost = -gain + P.c_spot * max(0, k - owned)
            if cost < best_cost - TOL or (abs(cost - best_cost) <= TOL and delivered > best_delivered):
                best, best_cost, best_delivered = k, cost, delivered
        limit = best
    selected = set(order[:limit])
    return [r for i, r in enumerate(routes) if i in selected], [r for i, r in enumerate(routes) if i not in selected]


def solve_dc_day(D, q, P=None, alpha=None, *, owned=0, spot_cap=None, economic_stockouts=True):
    """Solve one DC-day. Node 0 is the depot; every other node requires one visit.

    Default owned=0 is the sizing pass. Use account() to reprice its frozen
    routes, or pass owned explicitly to reoptimize against an existing fleet.
    Unserved stores remain explicit, penalized decisions, not solver infeasibility.
    Invalid data raises ValueError. The returned feasible plan is heuristic.
    """
    P = LLParams() if P is None else P
    D, q = _validate_inputs(D, q, P, alpha)
    owned = _fleet_integer(owned, "owned")
    if spot_cap is not None:
        spot_cap = _fleet_integer(spot_cap, "spot_cap")
    n = len(D) - 1
    sv = _Solver(D, q, P, alpha, owned)
    unserved, forced, main, reasons = [], [], [], {}
    for i in range(1, n + 1):
        km, arr, ret = _solo(D, q, P, i)
        if km > P.max_km + EPS or ret - P.depart_min > P.max_route_min + EPS:
            unserved.append(i)
            reasons[i] = "single_store_route_limits"
        elif arr > P.noon_min + EPS:
            forced.append(i)
        else:
            main.append(i)
    forced.sort(key=lambda i: (P.c_fuel * 2 * D[0][i] + P.p_late * int(P.depart_min + D[0][i] * sv.mpk > P.late_min + EPS), i))
    for i in forced[sv.budget:]:
        unserved.append(i)
        reasons[i] = "noon_budget"
    forced = forced[:sv.budget]
    sv.fixed_vehicles = len(forced)
    if main:
        sv.savings(main)
        for _ in range(P.max_passes):
            changed = sv.two_opt()
            changed = sv.or_opt() or changed
            changed = sv.relocate() or changed
            changed = sv.swap() or changed
            changed = sv.two_opt_star() or changed
            changed = sv.eliminate(True) or changed
            if not changed:
                break
        b_eff = sv.budget - len(forced)
        if b_eff > 0:
            sv.eliminate(False, b_eff)
    routes = [_route_dict(sv, seq, False) for seq in sv.R]
    routes.extend(_route_dict(sv, [i], True) for i in forced)
    routes.sort(key=lambda r: tuple(r["stores"]))
    before_selection = len(routes)
    routes, dropped = _select_routes(routes, P, owned, spot_cap, economic_stockouts)
    for route in dropped:
        for i in route["stores"]:
            unserved.append(i)
            reasons[i] = "spot_cap_or_cost" if spot_cap is not None else "economic_stockout"
    sol = {"status": "ok", "message": "", "routes": routes, "unserved": sorted(unserved),
           "unserved_reasons": reasons, "forced_late": sorted(i for r in routes if r["forced_late"] for i in r["stores"]),
           "vehicles": len(routes), "vehicles_before_selection": before_selection,
           "km": sum(r["km"] for r in routes), "n12": sum(r["n12"] for r in routes),
           "n14": sum(r["n14"] for r in routes), "budget": sv.budget, "n_stores": n,
           "algorithm": "Clarke-Wright + 2-opt/Or-opt/relocate/swap/2-opt*/elimination",
           "optimality_proven": False}
    priced = account(sol, P, owned, spot_cap)
    sol["cost"] = priced
    sol["service_level_met"] = priced["service_level_met"]
    if unserved:
        sol["message"] = f"{len(unserved)} of {n} assigned stores are unserved; inspect unserved_reasons"
    violations = validate(sol, D, q, P, alpha)
    if violations:
        raise RuntimeError(f"internal routing validation failed: {violations}")
    return sol


def account(sol, P, owned=0, spot_cap=None):
    """Reprice frozen routes, dropping the least valuable routes only if a cap binds.

    Owned vehicle fixed costs and DC fixed costs belong to the upper level.
    Fixed-route subset selection is exact; no store sequences are changed here.
    """
    if sol.get("status") != "ok":
        raise ValueError("cannot account an unsuccessful routing solution")
    owned = _fleet_integer(owned, "owned")
    if spot_cap is not None:
        spot_cap = _fleet_integer(spot_cap, "spot_cap")
    routes = sol["routes"]
    V = len(routes)
    unserved = list(sol["unserved"])
    keep, dropped = _select_routes(routes, P, owned, spot_cap, economic=False)
    for route in dropped:
        unserved.extend(route["stores"])
    spot = max(0, len(keep) - owned)
    km = sum(r["km"] for r in keep)
    n12 = sum(r["n12"] for r in keep)
    n14 = sum(r["n14"] for r in keep)
    delivered = sum(len(r["stores"]) for r in keep)
    fuel = P.c_fuel * km
    spot_cost = P.c_spot * spot
    late_cost, stockout_cost = P.p_late * n14, P.p_stockout * len(unserved)
    failure = late_cost + stockout_cost
    n = sol["n_stores"]
    before_noon = delivered - n12
    service = before_noon / n if n else 1.0
    return {"vehicles_needed": V, "vehicles_run": len(keep), "km": km, "spot": spot, "n12": n12, "n14": n14,
            "delivered": delivered, "unserved": sorted(unserved), "fuel": fuel, "spot_cost": spot_cost,
            "late_cost": late_cost, "stockout_cost": stockout_cost, "failure": failure,
            "total": fuel + spot_cost + failure, "routes": keep, "dropped_routes": dropped,
            "noon_budget_met": n12 <= sol["budget"], "service_before_noon": service,
            "share_before_noon_of_delivered": before_noon / delivered if delivered else (1.0 if n == 0 else 0.0),
            "service_level_met": n12 + len(unserved) <= sol["budget"], "spot_cap_binding": bool(dropped)}


def validate(sol, D, q, P, alpha=None):
    """Recompute coverage, arrival times, hard constraints and result summaries."""
    try:
        D, q = _validate_inputs(D, q, P, alpha)
    except (ValueError, TypeError) as exc:
        return [f"input: {exc}"]
    n = len(D) - 1
    a = P.alpha if alpha is None else alpha
    budget = int(math.floor(a * n + 1e-9))
    mpk = 60.0 / P.speed_kmh
    bad, seen = [], []
    n12 = n14 = 0
    total_km = 0.0
    for r in sol["routes"]:
        seq = r["stores"]
        if not seq or any(not isinstance(s, Integral) or not 1 <= s <= n for s in seq):
            bad.append("route-node")
            continue
        t, prev, km, load = P.depart_min, 0, 0.0, 0.0
        arrivals, route12, route14 = [], 0, 0
        for s in seq:
            km += D[prev][s]
            t += D[prev][s] * mpk
            if t > P.noon_min + EPS:
                n12 += 1
                route12 += 1
            if t > P.late_min + EPS:
                n14 += 1
                route14 += 1
            arrivals.append(t)
            t += P.stop_min
            load += q[s]
            prev = s
        km += D[prev][0]
        t += D[prev][0] * mpk
        if load > P.capacity + 1e-6:
            bad.append("load")
        if len(seq) > P.max_stops:
            bad.append("stops")
        if km > P.max_km + 1e-6:
            bad.append("km")
        if t - P.depart_min > P.max_route_min + 1e-6:
            bad.append("duration")
        for field, value in (("km", km), ("load", load), ("return_min", t), ("n12", route12), ("n14", route14)):
            if field not in r or not math.isfinite(r[field]) or abs(r[field] - value) > TOL:
                bad.append(f"route-{field}-summary")
        if len(r.get("arrivals", [])) != len(arrivals) or not np.allclose(r["arrivals"], arrivals, atol=TOL, rtol=0):
            bad.append("route-arrivals-summary")
        total_km += km
        seen += seq
    need = list(range(1, n + 1))
    if sorted(seen + sol["unserved"]) != sorted(need):
        bad.append("cover")
    if n12 > budget:
        bad.append("noon-budget")
    if sol.get("vehicles") != len(sol["routes"]):
        bad.append("vehicles-summary")
    if abs(sol.get("km", 0) - total_km) > TOL:
        bad.append("km-summary")
    if sol.get("n12") != n12 or sol.get("n14") != n14:
        bad.append("lateness-summary")
    return bad
