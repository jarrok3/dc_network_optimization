"""ID-based daily routing interface for upper-level candidate evaluation."""
from __future__ import annotations

import copy
import math
from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from demand_model import PlanningDays
from distances.lookup import RoadDistances
from project import CACHE_DIR, DATA_DIR, DEMAND_DIR
from .solver import EPS, LLParams, account, solve_dc_day


def weighted_fleet_size(vehicles, weights, quantile=2 / 3):
    """Inverse empirical weighted CDF (integer fleet-rule in Section 2b)."""
    values, weights = np.asarray(vehicles, dtype=float), np.asarray(weights, dtype=float)
    if values.ndim != 1 or not len(values) or weights.shape != values.shape:
        raise ValueError("vehicles and weights must be nonempty vectors with matching lengths")
    if not np.isfinite(values).all() or (values < 0).any() or (values != np.floor(values)).any():
        raise ValueError("vehicles must be finite nonnegative integer counts")
    if not np.isfinite(weights).all() or (weights <= 0).any():
        raise ValueError("day weights must be finite and positive")
    if not math.isfinite(quantile) or not 0 <= quantile <= 1:
        raise ValueError("quantile must lie in [0, 1]")
    order = np.argsort(values, kind="stable")
    cumulative = np.cumsum(weights[order]) / weights.sum()
    pos = min(int(np.searchsorted(cumulative, quantile, side="left")), len(values) - 1)
    return int(math.ceil(values[order[pos]]))


def load_cost_params(path=DATA_DIR / "cost_parameters.csv"):
    costs = pd.read_csv(path)
    if not {"name", "value"} <= set(costs) or costs.name.isna().any() or costs.name.duplicated().any():
        raise ValueError("cost_parameters.csv requires unique name and value columns")
    names = {"fuel_per_km": "c_fuel", "spot_daily": "c_spot", "late_penalty": "p_late",
             "stockout_penalty": "p_stockout", "capacity_cages": "capacity"}
    values = costs.set_index("name").value
    missing = set(names) - set(values.index)
    if missing:
        raise ValueError(f"cost_parameters.csv missing lower-level parameters: {sorted(missing)}")
    return LLParams(**{target: float(values[source]) for source, target in names.items()})


def _source_counts(routes):
    counts = {}
    for route in routes:
        for name, count in route.get("distance_sources", {}).items():
            counts[name] = counts.get(name, 0) + count
    return counts


def price_routes(result, params, owned, spot_cap=None):
    """Account a saved sizing result with frozen routes; never reroute held-out days.

    Reprice the original sizing result when comparing fleets. Once a capped
    result has shed routes, repricing that capped result cannot recover them.
    """
    priced = account(result, params, owned, spot_cap)
    updated = copy.deepcopy(result)
    for name in ("km", "n12", "n14", "delivered", "unserved", "service_before_noon",
                 "share_before_noon_of_delivered", "service_level_met", "noon_budget_met", "spot_cap_binding"):
        updated[name] = priced[name]
    updated.update(owned=int(owned), spot_cap=None if spot_cap is None else int(spot_cap), spot=priced["spot"],
                   vehicles_needed=result.get("vehicles_needed", len(result["routes"])), vehicles_run=priced["vehicles_run"],
                   routes=copy.deepcopy(priced["routes"]), dropped_routes=copy.deepcopy(priced["dropped_routes"]),
                   distance_sources=_source_counts(priced["routes"]))
    updated["costs"] = {name: priced[name] for name in ("fuel", "spot_cost", "late_cost", "stockout_cost", "failure", "total")}
    reasons = dict(result.get("unserved_reasons", {}))
    for route in priced["dropped_routes"]:
        reasons.update({s: "spot_cap" for s in route["stores"]})
    updated["unserved_reasons"] = reasons
    updated["message"] = f"{len(priced['unserved'])} of {result['n_stores']} assigned stores are unserved" if priced["unserved"] else ""
    updated["accounting"] = "frozen routes"
    return updated


class DailyRoutingSolver:
    """Reuse one instance across candidates; distances are loaded once and cached.

    solve(): explicit store-ID -> cages mapping.
    solve_day()/solve_days(): saved common-random-number PlanningDays.
    reprice(): owned fleet and optional spot cap, with routes frozen.
    """
    def __init__(self, distances, params=None, matrix_cache_size=16):
        if not isinstance(matrix_cache_size, int) or matrix_cache_size < 0:
            raise ValueError("matrix_cache_size must be a nonnegative integer")
        self.distances = distances
        self.params = LLParams() if params is None else params
        self.matrix_cache_size = matrix_cache_size
        self._matrices = OrderedDict()
        self.matrix_builds = self.matrix_cache_hits = self.solve_calls = 0

    @classmethod
    def from_project(cls, data_dir=DATA_DIR, cache_dir=CACHE_DIR, params=None, matrix_cache_size=16):
        data_dir, cache_dir = Path(data_dir), Path(cache_dir)
        distances = RoadDistances(data_dir / "stores.csv", data_dir / "candidate_sites.csv",
                                  cache_dir / "road_distance.parquet", cache_dir / "detour_model.json")
        return cls(distances, load_cost_params(data_dir / "cost_parameters.csv") if params is None else params, matrix_cache_size)

    @staticmethod
    def load_days(label="D7", directory=DEMAND_DIR / "planning"):
        if label not in ("D_dev", "D7", "D28", "H14"):
            raise ValueError("day set must be D_dev, D7, D28 or H14")
        return PlanningDays.load(Path(directory) / f"{label}.npz")

    def _ids(self, site_id, store_ids):
        ids = list(store_ids)
        if not isinstance(site_id, str) or any(not isinstance(s, str) or not s for s in ids):
            raise ValueError("site_id and store IDs must be nonempty strings")
        if len(set(ids)) != len(ids) or site_id in ids:
            raise ValueError("assigned store IDs must be unique and different from the depot")
        if hasattr(self.distances, "sites") and site_id not in self.distances.sites.index:
            raise ValueError(f"unknown candidate site: {site_id}")
        if hasattr(self.distances, "stores"):
            unknown = set(ids) - set(self.distances.stores.index)
            if unknown:
                raise ValueError(f"unknown assigned stores: {sorted(unknown)}")
        return tuple(sorted(ids))

    def _matrix(self, site_id, ids):
        key = (site_id, ids)
        if key in self._matrices:
            self.matrix_cache_hits += 1
            self._matrices.move_to_end(key)
            return self._matrices[key], True
        nodes = [site_id, *ids]
        if hasattr(self.distances, "submatrix_with_sources"):
            matrix, sources = self.distances.submatrix_with_sources(nodes)
        else:
            matrix = np.asarray(self.distances.submatrix(nodes), dtype=float)
            sources = np.full(matrix.shape, "provided", dtype="<U8")
            np.fill_diagonal(sources, "identity")
        # Providers may return their own cached arrays; never change their flags.
        matrix, sources = np.array(matrix, dtype=float, copy=True), np.array(sources, copy=True)
        if sources.shape != matrix.shape:
            raise ValueError("distance provenance matrix shape must match the distance matrix")
        matrix.setflags(write=False)
        sources.setflags(write=False)
        self.matrix_builds += 1
        if self.matrix_cache_size:
            self._matrices[key] = (matrix, sources)
            while len(self._matrices) > self.matrix_cache_size:
                self._matrices.popitem(last=False)
        return (matrix, sources), False

    def solve(self, site_id, demand, *, store_ids=None, owned=0, spot_cap=None, day_id=None, economic_stockouts=True):
        started = perf_counter()
        if not isinstance(demand, Mapping):
            raise ValueError("demand must map store IDs to cage quantities")
        ids = self._ids(site_id, demand.keys() if store_ids is None else store_ids)
        missing = set(ids) - set(demand)
        if missing:
            raise ValueError(f"missing demand for assigned stores: {sorted(missing)}")
        values = []
        for store in ids:
            value = float(demand[store])
            if not math.isfinite(value) or value < 0 or value > self.params.capacity + EPS:
                raise ValueError(f"demand for {store} must be finite, nonnegative and <= {self.params.capacity}; got {value}")
            values.append(value)
        (matrix, sources), hit = self._matrix(site_id, ids)
        prepared = perf_counter()
        sol = solve_dc_day(matrix, [0, *values], self.params, owned=owned, spot_cap=spot_cap, economic_stockouts=economic_stockouts)
        solved = perf_counter()
        self.solve_calls += 1
        labels = [site_id, *ids]
        routes = []
        for number, route in enumerate(sol["routes"], 1):
            mapped = copy.deepcopy(route)
            path = [0, *route["stores"], 0]
            counts = {}
            for a, b in zip(path, path[1:]):
                name = str(sources[a, b])
                counts[name] = counts.get(name, 0) + 1
            mapped.update(route_id=number, stores=[labels[i] for i in route["stores"]],
                          nodes=[labels[i] for i in path], distance_sources=counts,
                          depart_min=self.params.depart_min, duration_min=route["return_min"] - self.params.depart_min)
            routes.append(mapped)
        tri_sources = sources[np.triu_indices(len(labels), 1)]
        names, counts = np.unique(tri_sources, return_counts=True)
        result = {"status": sol["status"], "message": sol["message"], "site_id": site_id,
                  "day_id": None if day_id is None else str(day_id), "store_ids": list(ids),
                  "n_stores": len(ids), "budget": sol["budget"], "routes": routes,
                  "unserved": [labels[i] for i in sol["unserved"]],
                  "unserved_reasons": {labels[i]: reason for i, reason in sol["unserved_reasons"].items()},
                  "vehicles_before_selection": sol["vehicles_before_selection"], "vehicles_needed": len(routes),
                  "distance_matrix_sources": {str(k): int(v) for k, v in zip(names, counts)},
                  "solver": sol["algorithm"], "optimality_proven": False,
                  "routing_mode": "sizing" if owned == 0 and spot_cap is None else "fleet-aware heuristic",
                  "economic_stockouts": economic_stockouts,
                  "timing": {"matrix_seconds": prepared - started, "solve_seconds": solved - prepared,
                             "matrix_cache_hit": hit, "total_seconds": solved - started}}
        result = price_routes(result, self.params, owned, spot_cap)
        result["accounting"] = "routes optimized at supplied owned fleet, then priced"
        result["parameters"] = dict(self.params.__dict__)
        result["timing"]["total_seconds"] = perf_counter() - started
        return result

    def solve_day(self, site_id, store_ids, days, day_index=0, **options):
        if not isinstance(day_index, int) or not 0 <= day_index < len(days.dates):
            raise ValueError(f"day_index must be an integer in [0, {len(days.dates) - 1}]")
        demand = np.asarray(days.demand)
        weights = np.asarray(days.weights)
        if demand.shape != (len(days.dates), len(days.store_ids)) or len(set(days.store_ids)) != len(days.store_ids):
            raise ValueError("planning-day demand shape or duplicate store IDs are invalid")
        if weights.shape != (len(days.dates),) or not np.isfinite(weights).all() or (weights <= 0).any() or not np.isclose(weights.sum(), 1):
            raise ValueError("planning-day weights must be positive and sum to one")
        ids = self._ids(site_id, store_ids)
        missing = set(ids) - set(days.store_ids)
        if missing:
            raise ValueError(f"assigned stores absent from {days.label}: {sorted(missing)}")
        values = dict(zip(days.store_ids, demand[day_index]))
        result = self.solve(site_id, values, store_ids=ids, day_id=f"{days.label}:{day_index}:{days.dates[day_index].isoformat()}", **options)
        result.update(day_set=days.label, day_index=day_index, date=days.dates[day_index].isoformat(), day_weight=float(weights[day_index]))
        return result

    def solve_days(self, site_id, store_ids, days, **options):
        ids = list(store_ids)
        return [self.solve_day(site_id, ids, days, i, **options) for i in range(len(days.dates))]

    def reprice(self, result, owned, spot_cap=None):
        return price_routes(result, self.params, owned, spot_cap)

    def eligibility(self, site_id, store_ids):
        """Single-store eligibility for an upper-level assignment, before demand is known."""
        ids = self._ids(site_id, store_ids)
        P = self.params
        output = {}
        for store in ids:
            km, _ = self.distances.lookup(site_id, store)
            arrival = P.depart_min + km * 60 / P.speed_kmh
            output[store] = bool(2 * km <= P.max_km + EPS and 2 * km * 60 / P.speed_kmh + P.stop_min <= P.max_route_min + EPS and arrival <= P.noon_min + EPS)
        return output
