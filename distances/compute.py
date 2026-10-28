"""
Road-distance cache for the toy instance.

Source: latitude / longitude → haversine, then a river-aware detour
(cross only at the three bridges) times a 1.18 urban-grid factor.
This is a stated approximation of road length, cached so the demo
runs offline. It is not an OSRM engine value.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as cfg
from generator.generate import load_dcs, load_sites, load_stores


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _crosses_river(lon1, lon2) -> bool:
    r = cfg.RIVER_LON
    return (lon1 - r) * (lon2 - r) < 0


def road_km(lat1, lon1, lat2, lon2) -> float:
    """Bridge-constrained path length × urban-grid factor."""
    if lat1 == lat2 and lon1 == lon2:
        return 0.0
    direct = haversine_km(lat1, lon1, lat2, lon2)
    if not _crosses_river(lon1, lon2):
        return cfg.ROAD_FACTOR * direct
    best = None
    for blat, blon in cfg.BRIDGES:
        via = haversine_km(lat1, lon1, blat, blon) + haversine_km(blat, blon, lat2, lon2)
        if best is None or via < best:
            best = via
    return cfg.ROAD_FACTOR * best


class DistanceTable:
    def __init__(self, ids, km, meta):
        self.ids = list(ids)
        self.index = {i: k for k, i in enumerate(self.ids)}
        n = len(self.ids)
        self.km = [[0.0] * n for _ in range(n)]
        k = 0
        for i in range(n):
            for j in range(i + 1, n):
                v = km[k]
                k += 1
                self.km[i][j] = self.km[j][i] = v
        self.meta = meta

    def submatrix(self, ids):
        ix = [self.index[i] for i in ids]
        return [[self.km[a][b] for b in ix] for a in ix]


def build_distance_cache() -> DistanceTable:
    stores = load_stores()
    sites = load_sites()
    points = []
    for s in sites:
        points.append((s["site_id"], s["lat"], s["lon"]))
    for s in stores:
        points.append((s["store_id"], s["lat"], s["lon"]))
    ids = [p[0] for p in points]
    tri = []
    for i in range(len(points)):
        _, la, lo = points[i]
        for j in range(i + 1, len(points)):
            _, lb, lp = points[j]
            tri.append(round(road_km(la, lo, lb, lp), 4))
    meta = {
        "source": "haversine + river-bridge detour × 1.18",
        "engine": "none (toy approximation, not OSRM)",
        "river_lon": cfg.RIVER_LON,
        "bridges": cfg.BRIDGES,
        "n_points": len(ids),
        "symmetric": True,
    }
    cfg.DISTANCE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"ids": ids, "tri": tri, "meta": meta}
    cfg.DIST_JSON.write_text(json.dumps(payload), encoding="utf-8")
    return DistanceTable(ids, tri, meta)


def load_distances() -> DistanceTable:
    payload = json.loads(cfg.DIST_JSON.read_text(encoding="utf-8"))
    return DistanceTable(payload["ids"], payload["tri"], payload["meta"])


def submatrix(table: DistanceTable, ids):
    return table.submatrix(ids)


if __name__ == "__main__":
    t = build_distance_cache()
    print("cached", t.meta["n_points"], "points →", cfg.DIST_JSON)
