"""Validated nationwide inputs and cheap assignment/proxy operations."""
from pathlib import Path
import hashlib
import math

import numpy as np
import pandas as pd

from demand_model import DemandModel, PlanningDays
from distances.calibrate import haversine
from lower_level import DailyRoutingSolver
from project import CACHE_DIR, DATA_DIR, DEMAND_DIR
from .assignment import eligibility, solve_assignment


class NetworkContext:
    def __init__(self, store_ids, site_ids, road, map_km, volumes, fixed_costs, minimum, maximum,
                 router, historical=None, vehicle_monthly=5_500_000, days_per_month=30.4, day_loader=None):
        self.store_ids, self.site_ids = list(store_ids), list(site_ids)
        self.n, self.m = len(self.store_ids), len(self.site_ids)
        if not self.n or not self.m or len(set(self.store_ids)) != self.n or len(set(self.site_ids)) != self.m or set(self.store_ids) & set(self.site_ids):
            raise ValueError("store/site IDs must be nonempty, unique and disjoint")
        self.road, self.map_km = np.asarray(road, float), np.asarray(map_km, float)
        self.volumes, self.fixed_costs = np.asarray(volumes, float), np.asarray(fixed_costs, float)
        raw_min, raw_max = np.asarray(minimum, float), np.asarray(maximum, float)
        if not np.isfinite(raw_min).all() or not np.isfinite(raw_max).all() or (raw_min != np.floor(raw_min)).any() or (raw_max != np.floor(raw_max)).any():
            raise ValueError("store-count bounds must be finite integers")
        self.minimum, self.maximum = raw_min.astype(int), raw_max.astype(int)
        for name, shape in (("road", (self.n, self.m)), ("map_km", (self.n, self.m)), ("volumes", (self.n,)),
                            ("fixed_costs", (self.m,)), ("minimum", (self.m,)), ("maximum", (self.m,))):
            arr = getattr(self, name)
            if arr.shape != shape or not np.isfinite(arr).all() or (arr < 0).any():
                raise ValueError(f"{name}: invalid shape, nonfinite or negative values")
        if (self.maximum < self.minimum).any() or (self.maximum <= 0).any() or not (self.volumes > 0).all():
            raise ValueError("max_stores must be >= min_stores; posterior means must be positive")
        if not math.isfinite(vehicle_monthly) or vehicle_monthly < 0 or not math.isfinite(days_per_month) or days_per_month <= 0:
            raise ValueError("invalid monthly vehicle cost or days_per_month")
        self.router, self.params = router, router.params
        self.vehicle_monthly, self.days_per_month = float(vehicle_monthly), float(days_per_month)
        self.eligible = eligibility(self.road, self.params)
        self.site_index = {s: i for i, s in enumerate(self.site_ids)}
        self.historical = dict(historical or {})
        self.baseline_sites = sorted(set(self.historical.values()))
        self.day_loader, self._days = day_loader, {}
        self.scope = {"kind": "full population", "n_stores": self.n}
        self.provenance = {}
        self.site_lat_lon = None

    @classmethod
    def from_project(cls, data_dir=DATA_DIR, cache_dir=CACHE_DIR, model_dir=DEMAND_DIR,
                     planning_dir=DEMAND_DIR / "planning", diagnostic_size=None, diagnostic_site="SITE_00",
                     min_stores=None, max_stores=None):
        data_dir, cache_dir, model_dir, planning_dir = map(Path, (data_dir, cache_dir, model_dir, planning_dir))
        router = DailyRoutingSolver.from_project(data_dir, cache_dir)
        model = DemandModel.load(model_dir)
        posterior = dict(zip(model.stores.store_id, model.posterior_mean()))
        ids = sorted(model.stores.store_id.iloc[model.active_idx].tolist())
        sites = pd.read_csv(data_dir / "candidate_sites.csv", dtype={"site_id": str}).sort_values("site_id").reset_index(drop=True)
        site_ids = sites.site_id.tolist()
        if not {"monthly_fixed_cost", "max_stores"} <= set(sites):
            raise ValueError("candidate_sites missing monthly_fixed_cost or max_stores")
        if diagnostic_size is not None:
            if not isinstance(diagnostic_size, int) or not 1 <= diagnostic_size <= len(ids):
                raise ValueError("diagnostic_size must be a positive integer <= active store count")
            el = router.eligibility(diagnostic_site, ids)
            ranked = sorted((router.distances.lookup(diagnostic_site, s)[0], s) for s in ids if el[s])
            if len(ranked) < diagnostic_size:
                raise ValueError("not enough individually eligible stores for the requested diagnostic subset")
            ids = sorted(s for _, s in ranked[:diagnostic_size])
        index = pd.MultiIndex.from_product([site_ids, ids], names=router.distances.cache.index.names)
        rows = router.distances.cache.reindex(index)
        road = rows.km.to_numpy().reshape(len(site_ids), len(ids)).T.copy()
        missing = np.argwhere(~np.isfinite(road))
        for i, j in missing:
            km, source = router.distances.lookup(site_ids[j], ids[i])
            if source != "engine":
                raise ValueError(f"site-store pair must use engine distance: {site_ids[j]}, {ids[i]}")
            road[i, j] = km
        if not rows.source.dropna().isin(["engine"]).all():
            raise ValueError("site-store cache contains estimated distances; engine values required")
        store_points = router.distances.stores.loc[ids]
        map_km = haversine(store_points.lat.to_numpy()[:, None], store_points.lon.to_numpy()[:, None],
                           sites.lat.to_numpy()[None, :], sites.lon.to_numpy()[None, :])
        costs_frame = pd.read_csv(data_dir / "cost_parameters.csv")
        if costs_frame.name.duplicated().any():
            raise ValueError("duplicate cost parameter names")
        costs = costs_frame.set_index("name").value
        default_min = float(costs["min_stores"] if min_stores is None else min_stores)
        mins = sites["min_stores"].to_numpy(float) if "min_stores" in sites and min_stores is None else np.full(len(sites), default_min)
        maxs = sites.max_stores.to_numpy(float) if max_stores is None else np.full(len(sites), float(max_stores))
        if not np.equal(mins, np.floor(mins)).all() or not np.equal(maxs, np.floor(maxs)).all():
            raise ValueError("store-count bounds must be integers")
        stores = pd.read_csv(data_dir / "stores.csv", dtype=str).set_index("store_id")
        dcs = pd.read_csv(data_dir / "dcs.csv", dtype=str)
        if dcs.dc_id.duplicated().any() or dcs.site_id.duplicated().any() or not dcs.site_id.isin(site_ids).all():
            raise ValueError("dcs: duplicate IDs/sites or unknown candidate sites")
        dc_site = dict(zip(dcs.dc_id, dcs.site_id))
        historical = {s: dc_site[stores.loc[s, "current_dc_id"]] for s in ids}
        obj = cls(ids, site_ids, road, map_km, [posterior[s] for s in ids], sites.monthly_fixed_cost.to_numpy(),
                  mins, maxs, router, historical, float(costs["vehicle_monthly"]),
                  day_loader=lambda label: PlanningDays.load(planning_dir / f"{label}.npz"))
        # Keep all current sites, including sites with no stores in a diagnostic subset.
        obj.baseline_sites = sorted(dcs.site_id.tolist())
        obj.site_lat_lon = sites[["lat", "lon"]].to_numpy(float)
        obj.scope = {"kind": "full population" if diagnostic_size is None else "nearest-eligible diagnostic subset",
                     "n_stores": len(ids), "diagnostic_site": diagnostic_site if diagnostic_size else None,
                     "bounds_overridden": min_stores is not None or max_stores is not None}
        paths = [data_dir / "stores.csv", data_dir / "candidate_sites.csv", data_dir / "dcs.csv",
                 data_dir / "cost_parameters.csv", model_dir / "model.json", model_dir / "posterior.npz",
                 cache_dir / "road_distance.parquet", cache_dir / "detour_model.json"]
        obj.provenance = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        return obj

    def canonical_sites(self, sites):
        sites = list(sites)
        if not sites or len(set(sites)) != len(sites) or not set(sites) <= set(self.site_ids):
            raise ValueError("open sites must be a nonempty, unique list of candidate IDs")
        return tuple(sorted(sites))

    def k_values(self):
        hi = np.cumsum(np.sort(self.maximum)[::-1])
        lower = int(np.searchsorted(hi, self.n)) + 1
        upper = int(np.sum(np.cumsum(np.sort(self.minimum)) <= self.n))
        return list(range(lower, upper + 1)) if lower <= min(upper, self.m) else []

    def preflight(self):
        bad = np.flatnonzero(~self.eligible.any(axis=1))
        details = []
        P = self.params
        for i in bad:
            j = int(np.argmin(self.road[i]))
            km = float(self.road[i, j])
            details.append({"store_id": self.store_ids[i], "nearest_site": self.site_ids[j], "one_way_km": km,
                            "round_trip_km": 2 * km, "solo_duration_min": 2 * km * 60 / P.speed_kmh + P.stop_min,
                            "solo_arrival_min": P.depart_min + km * 60 / P.speed_kmh})
        report = {"status": "infeasible" if len(bad) else "ok", "scope": self.scope, "n_stores": self.n,
                  "n_sites": self.m, "capacity_possible_k": self.k_values(), "uncovered_stores": details,
                  "site_eligible_counts": dict(zip(self.site_ids, self.eligible.sum(axis=0).astype(int).tolist()))}
        if len(bad):
            report["reason"] = "active stores have no eligible candidate site; no network subset can cover them"
        elif not self.k_values():
            report.update(status="infeasible", reason="store-count bounds exclude every k")
        else:
            capacity = solve_assignment(self.road, self.volumes, np.zeros(self.m), self.maximum, self.eligible)
            if capacity["status"] != "ok":
                report.update(status="infeasible", reason=capacity["reason"])
        return report

    def days(self, label):
        if label not in ("D_dev", "D7", "D28", "H14"):
            raise ValueError("unknown planning-day label")
        if label not in self._days:
            if self.day_loader is None:
                raise ValueError("no planning-day loader configured")
            source = self.day_loader(label)
            if source.label != label or len(set(source.store_ids)) != len(source.store_ids):
                raise ValueError("planning-day label mismatch or duplicate IDs")
            if len(set(source.dates)) != len(source.dates):
                raise ValueError(f"{label} has duplicate calendar dates")
            for other, loaded in self._days.items():
                if set(source.dates) & set(loaded.dates):
                    raise ValueError(f"planning sets {label} and {other} overlap in calendar dates")
            positions = {s: i for i, s in enumerate(source.store_ids)}
            missing = set(self.store_ids) - set(positions)
            if missing:
                raise ValueError(f"{label} missing active stores: {sorted(missing)}")
            if np.asarray(source.demand).shape != (len(source.dates), len(source.store_ids)):
                raise ValueError("planning-day demand shape mismatch")
            ix = [positions[s] for s in self.store_ids]
            self._days[label] = PlanningDays(label, list(source.dates), np.array(source.weights, copy=True),
                                              list(self.store_ids), np.array(source.demand[:, ix], copy=True))
            digest = hashlib.sha256()
            digest.update("|".join(self.store_ids + [d.isoformat() for d in source.dates]).encode())
            digest.update(self._days[label].weights.tobytes())
            digest.update(self._days[label].demand.tobytes())
            self.provenance[f"planning:{label}"] = digest.hexdigest()
        return self._days[label]

    def assignment(self, sites, mode="optimized"):
        sites = self.canonical_sites(sites)
        ix = [self.site_index[s] for s in sites]
        if mode == "historical":
            if set(self.historical) != set(self.store_ids) or not set(self.historical.values()) <= set(sites):
                raise ValueError("historical assignment does not cover the active stores at these sites")
            position = {s: j for j, s in enumerate(sites)}
            assign = np.array([position[self.historical[s]] for s in self.store_ids])
            result = {"status": "ok", "assign": assign, "loads": np.bincount(assign, minlength=len(sites)).tolist(),
                      "objective": float(np.sum(self.volumes * self.road[np.arange(self.n), np.array(ix)[assign]])),
                      "optimality_proven": False}
        elif mode in ("optimized", "reassigned_baseline"):
            # Both baseline references are exempt from store-count bounds.
            lo = self.minimum[ix] if mode == "optimized" else np.zeros(len(ix))
            hi = self.maximum[ix] if mode == "optimized" else np.full(len(ix), self.n)
            result = solve_assignment(self.road[:, ix], self.volumes, lo, hi, self.eligible[:, ix])
        else:
            raise ValueError("assignment mode must be optimized, historical or reassigned_baseline")
        result.update(sites=list(sites), mode=mode)
        if result["status"] != "ok":
            if "uncovered_indices" in result:
                result["uncovered_stores"] = [self.store_ids[i] for i in result["uncovered_indices"]]
            return result
        result["store_to_site"] = dict(zip(self.store_ids, [sites[j] for j in result["assign"]]))
        result["ineligible_stores"] = [self.store_ids[i] for i, j in enumerate(result["assign"]) if not self.eligible[i, ix[j]]]
        return result

    def proxy(self, sites, assignment, multiplier):
        sites = self.canonical_sites(sites)
        if not math.isfinite(multiplier) or multiplier <= 0:
            raise ValueError("proxy multiplier must be finite and positive")
        ix = np.array([self.site_index[s] for s in sites])
        chosen = ix[assignment["assign"]]
        road = self.road[np.arange(self.n), chosen]
        direct = self.map_km[np.arange(self.n), chosen]
        ratio = np.divide(road, direct, out=np.ones_like(road), where=direct > 1e-6)
        G = float(self.fixed_costs[ix].sum() + multiplier * assignment["objective"])
        if G <= 0:
            raise ValueError("G must be positive for log-gap modelling")
        forced = chosen != ix[np.argmin(self.road[:, ix], axis=1)]
        features = [len(sites), math.log(G), float(ratio.mean()), float(forced.mean()),
                    float(np.max(np.asarray(assignment["loads"]) / self.maximum[ix])), float(np.quantile(road, .9))]
        return G, features
