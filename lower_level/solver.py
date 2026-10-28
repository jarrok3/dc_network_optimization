"""
One DC, one delivery day (problem-definition.md, Section 2a).

Clarke–Wright savings + local search, deterministic. A JavaScript port of
this file lives in ui/solver.js and must stay in lock-step.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as cfg

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

    def key(self):
        return tuple(self.__dict__.values())

    def with_(self, **kw):
        d = dict(self.__dict__)
        d.update(kw)
        return LLParams(**d)


class _Solver:
    def __init__(self, D, q, P, alpha):
        self.D, self.q, self.P = D, q, P
        self.n = len(D) - 1
        a = P.alpha if alpha is None else alpha
        self.budget = int(math.floor(a * self.n + 1e-9))
        self.mpk = 60.0 / P.speed_kmh
        self.R, self.S = [], []

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
        cf, cs = P.c_fuel, P.c_spot
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
                old1 = self.S[r1][0]
                best, best_delta = None, -TOL
                for r2 in range(len(self.R)):
                    if r2 == r1:
                        continue
                    seq2 = self.R[r2]
                    if self.S[r2][2] + q[node] > P.capacity + EPS or len(seq2) + 1 > P.max_stops:
                        continue
                    for pos in range(len(seq2) + 1):
                        new2 = seq2[:pos] + [node] + seq2[pos:]
                        s2 = self.ev(new2)
                        if not self.ok(new2, s2, True):
                            continue
                        delta = cf * (s1[0] + s2[0] - old1 - self.S[r2][0]) - (0.0 if new1 else cs)
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
                         - P.c_spot)
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


def solve_dc_day(D, q, P, alpha=None):
    n = len(D) - 1
    sv = _Solver(D, q, P, alpha)
    unserved, forced, main = [], [], []
    for i in range(1, n + 1):
        if q[i] <= 0:
            continue
        km, arr, ret = _solo(D, q, P, i)
        if q[i] > P.capacity + EPS or km > P.max_km + EPS or ret - P.depart_min > P.max_route_min + EPS:
            unserved.append(i)
        elif arr > P.noon_min + EPS:
            forced.append(i)
        else:
            main.append(i)

    if len(forced) > sv.budget:
        return {"status": "infeasible", "routes": [], "unserved": unserved, "forced_late": forced,
                "vehicles": 0, "km": 0.0, "n12": len(forced), "n14": 0, "budget": sv.budget, "n_stores": n,
                "message": ("{} stores cannot be reached by {:02d}:{:02d} even with a dedicated vehicle, but the noon "
                            "budget allows only {} of {} stores after noon. The service-level constraint cannot be met; "
                            "no routes are produced.").format(len(forced), int(P.noon_min // 60), int(P.noon_min % 60),
                                                              sv.budget, n)}

    if main:
        sv.savings(main)
        for _ in range(P.max_passes):
            ch = sv.two_opt()
            ch = sv.relocate() or ch
            ch = sv.swap() or ch
            ch = sv.eliminate(True) or ch
            if not ch:
                break
        b_eff = sv.budget - len(forced)
        if b_eff > 0:
            sv.eliminate(False, b_eff)

    routes = [_route_dict(sv, seq, False) for seq in sv.R]
    for i in forced:
        routes.append(_route_dict(sv, [i], True))
    routes.sort(key=lambda r: r["stores"][0])
    return {
        "status": "ok", "message": "", "routes": routes, "unserved": unserved, "forced_late": forced,
        "vehicles": len(routes), "km": sum(r["km"] for r in routes),
        "n12": sum(r["n12"] for r in routes), "n14": sum(r["n14"] for r in routes),
        "budget": sv.budget, "n_stores": n,
    }


def account(sol, P, owned=0, spot_cap=None):
    routes = sol["routes"]
    V = len(routes)
    spot = max(0, V - owned)
    unserved = list(sol["unserved"])
    drop = set()
    if spot_cap is not None and spot > spot_cap:
        k = spot - spot_cap
        order = sorted(range(V), key=lambda r: (routes[r]["load"], r))[:k]
        drop = set(order)
        for r in order:
            unserved += routes[r]["stores"]
        spot = spot_cap
    keep = [r for i, r in enumerate(routes) if i not in drop]
    km = sum(r["km"] for r in keep)
    n12 = sum(r["n12"] for r in keep)
    n14 = sum(r["n14"] for r in keep)
    delivered = sum(len(r["stores"]) for r in keep)
    fuel = P.c_fuel * km
    spot_cost = P.c_spot * spot
    failure = P.p_late * n14 + P.p_stockout * len(unserved)
    return {"vehicles_needed": V, "vehicles_run": len(keep), "km": km, "spot": spot, "n12": n12, "n14": n14,
            "delivered": delivered, "unserved": sorted(unserved), "fuel": fuel, "spot_cost": spot_cost,
            "failure": failure, "total": fuel + spot_cost + failure}


def validate(sol, D, q, P, alpha=None):
    n = len(D) - 1
    a = P.alpha if alpha is None else alpha
    budget = int(math.floor(a * n + 1e-9))
    mpk = 60.0 / P.speed_kmh
    bad, seen = [], []
    n12 = 0
    for r in sol["routes"]:
        seq = r["stores"]
        t, prev, km, load = P.depart_min, 0, 0.0, 0.0
        for s in seq:
            km += D[prev][s]
            t += D[prev][s] * mpk
            if t > P.noon_min + EPS:
                n12 += 1
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
        seen += seq
    need = [i for i in range(1, n + 1) if q[i] > 0]
    if sorted(seen + sol["unserved"]) != sorted(need):
        bad.append("cover")
    if n12 > budget:
        bad.append("noon-budget")
    return bad
