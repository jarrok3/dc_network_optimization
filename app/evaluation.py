"""
evaluation.py -- the connection between the levels: the full evaluation E and the cheap criterion G.

E(S, q) for a candidate (open sites S, fleet quantile q), problem-definition.md Section 2b:
  1. assignment of stores to the open sites (history for the baseline, LP otherwise);
  2. sizing pass: lower level with every vehicle priced as a spot truck, for every DC and
     every sampled day  ->  vehicles needed V_{j,d};
  3. owned fleet N_j = omega-weighted q-quantile of V_{j,.}  (or fixed, for held-out days);
  4. accounting: spot trucks (V - N_j)+ on the frozen routes, fuel, failures;
  5. monthly cost = DC fixed + vehicle fixed + D_m * sum_d omega_d * daily operating cost.

G(S) = F(S) + lambda * P(S), P = the capacitated assignment cost in cage-km/day (road distance).
G ignores routing, vehicle limits, the window, the fleet quantile and demand variability.
"""
import time

import numpy as np

import config as cfg
import distances
from assignment import eligibility, solve_assignment
from demand_model import DemandModel
from lower_level import LLParams, account, solve_dc_day
from tables import load_dcs, load_sites, load_stores


def weighted_quantile(values, weights, q):
    values, weights = np.asarray(values, float), np.asarray(weights, float)
    order = np.argsort(values, kind="stable")
    cum = np.cumsum(weights[order]) / weights.sum()
    return float(values[order][np.searchsorted(cum, q - 1e-12)])


class Context:
    def __init__(self):
        self.stores = load_stores()
        self.active = [s for s in self.stores if s["closed_on"] is None]
        self.sites = load_sites()
        self.site = {s["site_id"]: s for s in self.sites}
        self.dcs = load_dcs()
        self.dist = distances.load_distances()
        self.model = DemandModel.fit()
        self.P = LLParams()
        self.n = len(self.active)
        self.w = self.model.posterior_mean()[self.model.active_idx]
        self.store_ids = [s["store_id"] for s in self.active]
        sx = [self.dist.index[s["site_id"]] for s in self.sites]
        tx = [self.dist.index[i] for i in self.store_ids]
        self.site_pos = {s["site_id"]: k for k, s in enumerate(self.sites)}
        self.d_site_store = self.dist.road_km[np.ix_(sx, tx)]          # sites x stores (road km)
        self.map_site_store = self.dist.map_km[np.ix_(sx, tx)]
        self.cache = {}
        self.solves, self.solve_seconds, self.cache_hits = 0, 0.0, 0
        self.timings = []                      # (stores in the DC-day, seconds) per solve

    # ----------------------------------------------------------------- lower level (memoised)
    def dc_day(self, site_id, store_pos, demand, day_key, P):
        key = (site_id, tuple(store_pos), day_key, P.key())
        hit = self.cache.get(key)
        if hit is not None:
            self.cache_hits += 1
            return hit
        ids = [site_id] + [self.store_ids[i] for i in store_pos]
        D = self.dist.submatrix(ids)
        q = [0.0] + [float(demand[i]) for i in store_pos]
        t = time.time()
        sol = solve_dc_day(D, q, P)
        dt = time.time() - t
        self.solve_seconds += dt
        self.timings.append((len(store_pos), dt))
        self.solves += 1
        self.cache[key] = sol
        return sol

    # ----------------------------------------------------------------- assignment
    def lp_assignment(self, site_ids, P=None, max_override=None, min_override=None):
        P = P or self.P
        ix = [self.site_pos[s] for s in site_ids]
        d = self.d_site_store[ix, :].T
        el = eligibility(self.d_site_store[ix, :], P).T
        mx = [max_override if max_override is not None else self.site[s]["max_stores"] for s in site_ids]
        mn = [min_override if min_override is not None else cfg.MIN_STORES for s in site_ids]
        return solve_assignment(d, self.w, mx, mn, el)

    def historical_assignment(self, site_ids):
        """Assignment recorded in `stores.current_dc_id` (baseline); index into site_ids via the DC table."""
        dc_site = {dc["dc_id"]: dc["site_id"] for dc in self.dcs}
        pos = {s: k for k, s in enumerate(site_ids)}
        return np.array([pos[dc_site[s["current_dc_id"]]] for s in self.active])

    def proxy_cost(self, site_ids, assign):
        ix = [self.site_pos[s] for s in site_ids]
        d = self.d_site_store[ix, :].T
        return float(sum(self.w[i] * d[i, assign[i]] for i in range(self.n)))

    # ----------------------------------------------------------------- full evaluation E
    def evaluate(self, site_ids, assign, days, fleet=None, q=cfg.FLEET_QUANTILE, P=None, demand_override=None):
        """
        Monthly cost of the network on a set of sampled days.  `fleet` (dict site -> owned vehicles)
        fixes the fleet (held-out days); otherwise it is sized here from the vehicles needed.
        demand_override: a single demand vector (used for the average-day evaluation).
        """
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
        N = dict(fleet) if fleet is not None else {sid: int(weighted_quantile(V[sid], weights, q)) for sid in site_ids}

        parts = {"fuel": 0.0, "spot": 0.0, "failure": 0.0}
        per_day = np.zeros(len(demands))
        late12 = late14 = delivered = unserved = 0.0
        share_by_day = np.zeros(len(demands))
        spot_by_site = {sid: 0.0 for sid in site_ids}
        km_by_site = {sid: 0.0 for sid in site_ids}
        for d in range(len(demands)):
            n12 = dlv = 0
            for j, sid in enumerate(site_ids):
                a = account(sols[(j, d)], P, owned=N[sid], spot_cap=cfg.SPOT_CAP)
                parts["fuel"] += weights[d] * a["fuel"]
                parts["spot"] += weights[d] * a["spot_cost"]
                parts["failure"] += weights[d] * a["failure"]
                per_day[d] += a["total"]
                n12 += a["n12"]; dlv += a["delivered"]
                late14 += weights[d] * a["n14"]; unserved += weights[d] * len(a["unserved"])
                spot_by_site[sid] += weights[d] * a["spot"]
                km_by_site[sid] += weights[d] * a["km"]
            share_by_day[d] = 1.0 - n12 / max(dlv, 1)
        Dm = cfg.DAYS_PER_MONTH
        F = float(sum(self.site[s]["monthly_fixed_cost"] for s in site_ids))
        veh = cfg.VEHICLE_MONTHLY * sum(N.values())
        monthly = {m: Dm * v for m, v in parts.items()}
        total = F + veh + sum(monthly.values())
        per_day_total = F + veh + Dm * per_day
        return {
            "status": "ok", "sites": list(site_ids), "k": k, "fleet": N,
            "dc_fixed": F, "vehicle_fixed": veh, "fuel": monthly["fuel"], "spot": monthly["spot"], "failure": monthly["failure"],
            "operating": sum(monthly.values()), "total": float(total),
            "service_before_noon": float(np.dot(weights, share_by_day)), "service_min_day": float(share_by_day.min()),
            "late_after_14": float(late14), "unserved": float(unserved),
            "loads": {sid: len(members[j]) for j, sid in enumerate(site_ids)},
            "vehicles_needed": V, "km_per_day": {s: float(v) for s, v in km_by_site.items()},
            "spot_trucks_per_day": {s: float(v) for s, v in spot_by_site.items()},
            "per_day_total": per_day_total.tolist(), "weights": weights.tolist(), "day_labels": labels,
        }

    # ----------------------------------------------------------------- cheap criterion G
    def g_score(self, site_ids, proxy, lam):
        F = float(sum(self.site[s]["monthly_fixed_cost"] for s in site_ids))
        return F + lam * proxy