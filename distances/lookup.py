"""Offline nationwide distance lookup: engine cache first, detour for long store legs."""
from pathlib import Path

import numpy as np
import pandas as pd

from distances.calibrate import DetourModel, haversine, load_points, pair_area
from project import CACHE_DIR, DATA_DIR


class RoadDistances:
    def __init__(self, stores_path=DATA_DIR / "stores.csv", sites_path=DATA_DIR / "candidate_sites.csv",
                 cache_path=CACHE_DIR / "road_distance.parquet", model_path=CACHE_DIR / "detour_model.json"):
        self.stores = load_points(stores_path).set_index("store_id")
        sites = pd.read_csv(sites_path, dtype={"site_id": str})
        if not {"site_id", "lat", "lon"} <= set(sites):
            raise ValueError("candidate_sites: missing site_id/lat/lon")
        if sites.site_id.isna().any() or sites.site_id.duplicated().any() or set(sites.site_id) & set(self.stores.index):
            raise ValueError("candidate_sites: missing/duplicate IDs or IDs overlap stores")
        if not np.isfinite(sites[["lat", "lon"]].to_numpy(float)).all() or not (sites.lat.between(-90, 90) & sites.lon.between(-180, 180)).all():
            raise ValueError("candidate_sites: invalid lat/lon")
        self.sites = sites.set_index("site_id")
        cache = pd.read_parquet(cache_path)
        if not {"from_id", "to_id", "km", "source"} <= set(cache):
            raise ValueError("road_distance cache: missing from_id/to_id/km/source")
        if cache.duplicated(["from_id", "to_id"]).any() or not np.isfinite(cache.km).all() or (cache.km < 0).any():
            raise ValueError("road_distance cache: duplicate pairs or invalid km")
        known_ids = set(self.stores.index) | set(self.sites.index)
        if not cache.from_id.isin(known_ids).all() or not cache.to_id.isin(known_ids).all():
            raise ValueError("road_distance cache: missing or unknown endpoint ID")
        if not cache.source.isin(["engine", "detour"]).all():
            raise ValueError("road_distance cache: source must be engine or detour")
        self.cache = cache.set_index(["from_id", "to_id"])
        self.points = pd.concat([self.stores[["lat", "lon"]], self.sites[["lat", "lon"]]])
        self.model_path = Path(model_path)
        self.model = None

    def lookup(self, a, b):
        if a not in self.points.index or b not in self.points.index:
            raise KeyError(f"unknown distance endpoint: {a}, {b}")
        if a == b:
            return 0.0, "identity"
        for key in ((a, b), (b, a)):
            if key in self.cache.index:
                row = self.cache.loc[key]
                return float(row.km), str(row.source)
        p, q = self.points.loc[a], self.points.loc[b]
        direct = float(haversine(p.lat, p.lon, q.lat, q.lon))
        if a not in self.stores.index or b not in self.stores.index or direct <= 60:
            raise KeyError(f"required engine distance absent from cache: {a}, {b} ({direct:.3f} km)")
        if self.model is None:
            self.model = DetourModel.load(self.model_path)
        area = pair_area(self.stores.loc[a, "region"], self.stores.loc[b, "region"])
        return self.model.estimate(direct, area), "detour"

    def submatrix_with_sources(self, ids):
        """Batch cached pairs; only uncached long legs need scalar detour lookup."""
        ids = list(ids)
        if len(set(ids)) != len(ids):
            raise ValueError("distance submatrix IDs must be unique")
        unknown = set(ids) - set(self.points.index)
        if unknown:
            raise KeyError(f"unknown distance endpoints: {sorted(unknown)}")
        n = len(ids)
        matrix = np.zeros((n, n))
        sources = np.full((n, n), "identity", dtype="<U8")
        i, j = np.triu_indices(n, k=1)
        if not len(i):
            return matrix, sources
        names = np.asarray(ids)
        forward = pd.MultiIndex.from_arrays([names[i], names[j]], names=self.cache.index.names)
        rows = self.cache.reindex(forward)
        km = rows.km.to_numpy(copy=True)
        source = rows.source.to_numpy(copy=True)
        missing = np.flatnonzero(pd.isna(km))
        if len(missing):
            backward = pd.MultiIndex.from_arrays([names[j[missing]], names[i[missing]]], names=self.cache.index.names)
            reverse = self.cache.reindex(backward)
            km[missing], source[missing] = reverse.km.to_numpy(), reverse.source.to_numpy()
        for pos in np.flatnonzero(pd.isna(km)):
            km[pos], source[pos] = self.lookup(ids[i[pos]], ids[j[pos]])
        matrix[i, j] = matrix[j, i] = km
        sources[i, j] = sources[j, i] = source
        return matrix, sources

    def submatrix(self, ids):
        return self.submatrix_with_sources(ids)[0]
