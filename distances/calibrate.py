"""Stratified, reproducible OSRM detour calibration (specification Section 3.2)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from project import CACHE_DIR, DATA_DIR, seed_sequence

BANDS = (0, 10, 30, 60, 150, float("inf"))
AREAS = ("capital", "other-metro", "rural")


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 12742.0 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def band_index(km):
    return np.searchsorted(BANDS[1:-1], km, side="right")


def pair_area(a, b):
    # Mixed endpoints: rural takes precedence; capital requires both endpoints.
    return "rural" if "rural" in (a, b) else ("capital" if a == b == "capital" else "other-metro")


def load_points(path):
    df = pd.read_csv(path, dtype={"store_id": str})
    required = {"store_id", "lat", "lon", "region"}
    if not required <= set(df):
        raise ValueError(f"{path}: missing columns {required - set(df)}")
    if df.store_id.isna().any() or df.store_id.duplicated().any():
        raise ValueError(f"{path}: missing or duplicate store_id")
    if not np.isfinite(df[["lat", "lon"]].to_numpy(float)).all() or not (df.lat.between(-90, 90) & df.lon.between(-180, 180)).all():
        raise ValueError(f"{path}: invalid lat/lon")
    if not df.region.isin(AREAS).all():
        raise ValueError(f"{path}: region must be one of {AREAS}")
    return df


def river_sides(lat, lon):
    """Approximate Han-bank screen, not a claim about the engine route's bridges."""
    xs = np.array([126.80, 126.88, 126.94, 127.01, 127.07, 127.12, 127.18])
    ys = np.array([37.59, 37.54, 37.52, 37.52, 37.54, 37.57, 37.58])
    inside = (lon >= xs[0]) & (lon <= xs[-1]) & (lat > 37.40) & (lat < 37.70)
    return np.where(inside, np.where(lat > np.interp(lon, xs, ys), 1, -1), 0)


def sample_pairs(stores, n=1000, seed=437, allow_smaller=False):
    """Keep random-priority reservoirs by band/area/bank, without a dense matrix."""
    if n < 10:
        raise ValueError("sample size must be at least 10")
    rng = np.random.default_rng(seed_sequence("distances", seed).spawn(2)[0])
    lat, lon = stores.lat.to_numpy(), stores.lon.to_numpy()
    regions = stores.region.to_numpy()
    banks = river_sides(lat, lon)
    reservoirs = {}
    for i in range(len(stores) - 1):
        js = np.arange(i + 1, len(stores))
        km = haversine(lat[i], lon[i], lat[js], lon[js])
        bands = band_index(km)
        areas = np.where((regions[i] == "rural") | (regions[js] == "rural"), "rural",
                         np.where((regions[i] == "capital") & (regions[js] == "capital"), "capital", "other-metro"))
        crosses = banks[i] * banks[js] == -1
        priorities = rng.random(len(js))
        for band in np.unique(bands):
            for area in AREAS:
                for cross in (False, True):
                    mask = (bands == band) & (areas == area) & (crosses == cross) & (km > 0.01)
                    if not mask.any():
                        continue
                    key = (int(band), area, cross)
                    arr = np.column_stack((priorities[mask], np.full(mask.sum(), i), js[mask], km[mask]))
                    arr = np.concatenate((reservoirs[key], arr)) if key in reservoirs else arr
                    if len(arr) > n:
                        arr = arr[np.argpartition(arr[:, 0], n - 1)[:n]]
                    reservoirs[key] = arr
    # Round robin gives equal coverage to available strata; spare capacity redistributes.
    ordered = {k: v[np.argsort(v[:, 0])].tolist() for k, v in sorted(reservoirs.items())}
    chosen = []
    while len(chosen) < n:
        progress = False
        for key, pool in ordered.items():
            if pool and len(chosen) < n:
                _, i, j, km = pool.pop(0)
                a, b = stores.iloc[int(i)], stores.iloc[int(j)]
                chosen.append({"from_id": a.store_id, "to_id": b.store_id, "haversine_km": km,
                               "band": key[0], "area": key[1], "river_screen": key[2]})
                progress = True
        if not progress:
            if allow_smaller:
                break
            raise ValueError(f"only {len(chosen)} unique non-coincident pairs exist; requested {n}")
    return pd.DataFrame(chosen)


def query_pair(session, base_url, a, b, timeout=15, max_snap_m=500):
    coords = f"{a.lon},{a.lat};{b.lon},{b.lat}"
    response = session.get(f"{base_url.rstrip('/')}/table/v1/driving/{coords}",
                           params={"annotations": "distance"}, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    if body.get("code") != "Ok":
        raise ValueError(f"OSRM: {body.get('code')}: {body.get('message', '')}")
    forward, reverse = body["distances"][0][1], body["distances"][1][0]
    if forward is None or reverse is None:
        raise ValueError("OSRM: no route in one or both directions")
    road = (float(forward) + float(reverse)) / 2000
    if not np.isfinite(road) or road <= 0:
        raise ValueError("OSRM returned invalid road distance")
    snaps = [p["distance"] for p in body.get("sources", []) if p]
    if len(snaps) != 2 or not np.isfinite(snaps).all():
        raise ValueError("OSRM returned missing/invalid snap distances")
    if max(snaps) > max_snap_m:
        raise ValueError(f"OSRM snap exceeds {max_snap_m} m: {max(snaps):.1f} m")
    return road, max(snaps, default=0.0)


class DetourModel:
    def __init__(self, payload):
        self.payload = payload

    @classmethod
    def load(cls, path=CACHE_DIR / "detour_model.json"):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def multiplier(self, km, area):
        band = str(int(band_index(km)))
        p = self.payload
        return p["by_band_area"].get(f"{band}|{area}", p["by_band"].get(band, p["global_ratio"]))

    def estimate(self, km, area):
        return float(km * self.multiplier(km, area))


def error_metrics(rows, model):
    if rows.empty:
        return {"n": 0}
    pred = np.array([model.estimate(r.haversine_km, r.area) for r in rows.itertuples()])
    actual = rows.road_km.to_numpy()
    return {"n": len(rows), "mape_percent": float(np.mean(np.abs(pred - actual) / actual) * 100),
            "bias_percent": float(np.mean((pred - actual) / actual) * 100),
            "mae_km": float(np.mean(np.abs(pred - actual)))}


def fit_model(rows, seed=437):
    rows = rows.copy()
    rows["split"] = "train"
    rng = np.random.default_rng(seed_sequence("distances", seed).spawn(2)[1])
    groups = list(rows.groupby(["band", "area", "river_screen"]))
    allocation = np.array([len(g) * 0.3 for _, g in groups])
    counts = np.floor(allocation).astype(int)
    for i in np.argsort(-(allocation - counts))[:round(len(rows) * 0.3) - counts.sum()]:
        counts[i] += 1
    for (_, g), count in zip(groups, counts):
        rows.loc[rng.choice(g.index, count, replace=False), "split"] = "validation"
    train = rows[rows.split == "train"]
    train = train.assign(ratio=train.road_km / train.haversine_km)
    payload = {"version": 1, "global_ratio": float(train.ratio.mean()),
               "by_band": {str(k): float(g.ratio.mean()) for k, g in train.groupby("band")},
               "by_band_area": {f"{b}|{a}": float(g.ratio.mean()) for (b, a), g in train.groupby(["band", "area"])},
               "fallback": "band-area, then band, then global; training observations only",
               "band_edges_km": [0, 10, 30, 60, 150, None], "seed": seed,
               "train_n": len(train), "validation_n": int((rows.split == "validation").sum()),
               "symmetric": "mean of forward and reverse OSRM distances",
               "area_rule": "rural if either endpoint rural; capital if both capital; otherwise other-metro",
               "river_screen": "approximate opposite Han banks; route crossing not verified"}
    model = DetourModel(payload)
    valid = rows[rows.split == "validation"]
    payload["validation"] = error_metrics(valid, model)
    payload["validation_by_band_area"] = {f"{b}|{a}": error_metrics(g, model) for (b, a), g in valid.groupby(["band", "area"])}
    payload["validation_river_screen"] = error_metrics(valid[valid.river_screen], model)
    payload["validation_long_distance"] = error_metrics(valid[valid.haversine_km > 60], model)
    payload["ratio_quantiles"] = {str(q): float(v) for q, v in (rows.road_km / rows.haversine_km).quantile([0, .1, .5, .9, 1]).items()}
    return model, rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stores", type=Path, default=DATA_DIR / "stores.csv")
    parser.add_argument("--output-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--osrm-url", default="http://localhost:5000")
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=437)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--max-snap-m", type=float, default=500, help="reject and replace pairs snapped farther than this")
    args = parser.parse_args(argv)
    if args.max_snap_m <= 0 or args.timeout <= 0:
        parser.error("--max-snap-m and --timeout must be positive")
    stores = load_points(args.stores)
    pool_size = min(args.sample_size * 8, len(stores) * (len(stores) - 1) // 2)
    pool = sample_pairs(stores, pool_size, args.seed, allow_smaller=True)
    if len(pool) < args.sample_size:
        raise ValueError(f"only {len(pool)} non-coincident pairs available; requested {args.sample_size}")
    pairs = pool.iloc[:args.sample_size].copy()
    points = stores.set_index("store_id")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(args.stores.read_bytes()).hexdigest()
    sample_path = args.output_dir / "detour_sample.csv"
    run_meta = args.output_dir / "detour_sample_meta.json"
    metadata = {"stores_sha256": fingerprint, "osrm_url": args.osrm_url, "seed": args.seed, "sample_size": args.sample_size,
                "max_snap_m": args.max_snap_m}
    resumed = False
    if sample_path.exists() and run_meta.exists() and json.loads(run_meta.read_text()) == metadata:
        old = pd.read_csv(sample_path)
        pool_keys = set(zip(pool.from_id, pool.to_id))
        if len(old) == len(pairs) and not old.duplicated(["from_id", "to_id"]).any() and set(zip(old.from_id, old.to_id)) <= pool_keys:
            pairs = old
            pairs["error"] = pairs.error.astype(object).fillna("")
            resumed = True
    if "road_km" not in pairs:
        pairs["road_km"] = np.nan
        pairs["max_snap_m"] = np.nan
        pairs["error"] = ""
    run_meta.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    rejected_path = args.output_dir / "detour_rejected.csv"
    rejected = []
    if resumed and rejected_path.exists() and rejected_path.stat().st_size > 2:
        rejected = pd.read_csv(rejected_path).to_dict("records")
    used = set(zip(pairs.from_id, pairs.to_id)) | {(r["from_id"], r["to_id"]) for r in rejected}
    reserve = pool[[key not in used for key in zip(pool.from_id, pool.to_id)]].copy()
    with requests.Session() as session:
        for index, row in pairs.iterrows():
            if pd.notna(row.road_km):
                continue
            while True:
                try:
                    road, snap = query_pair(session, args.osrm_url, points.loc[row.from_id], points.loc[row.to_id], args.timeout, args.max_snap_m)
                    pairs.loc[index, ["road_km", "max_snap_m", "error"]] = [road, snap, ""]
                    break
                except (requests.ConnectionError, requests.Timeout):
                    pairs.to_csv(sample_path, index=False)
                    pd.DataFrame(rejected, columns=pairs.columns).to_csv(rejected_path, index=False)
                    raise RuntimeError(f"OSRM unavailable at {args.osrm_url}; progress saved to {sample_path}") from None
                except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                    rejected.append({**row.to_dict(), "error": str(exc)})
                    used.add((row.from_id, row.to_id))
                    same = reserve[(reserve.band == row.band) & (reserve.area == row.area) & (reserve.river_screen == row.river_screen)]
                    if same.empty:
                        # Exhausted rare strata redistribute to other available geographic cells.
                        same = reserve[reserve.band == row.band]
                    if same.empty:
                        same = reserve
                    if same.empty:
                        pairs.to_csv(sample_path, index=False)
                        pd.DataFrame(rejected, columns=pairs.columns).to_csv(rejected_path, index=False)
                        raise ValueError("calibration reserve exhausted; check store coordinates and road coverage") from None
                    replacement = same.iloc[0]
                    reserve = reserve.drop(replacement.name)
                    for column in pool.columns:
                        pairs.loc[index, column] = replacement[column]
                    row = pairs.loc[index]
            if (index + 1) % 100 == 0:
                pairs.to_csv(sample_path, index=False)
                pd.DataFrame(rejected, columns=pairs.columns).to_csv(rejected_path, index=False)
                print(f"OSRM {index + 1}/{len(pairs)} pairs", flush=True)
    pairs.to_csv(sample_path, index=False)
    pd.DataFrame(rejected, columns=pairs.columns).to_csv(rejected_path, index=False)
    if pairs.road_km.isna().any():
        raise ValueError(f"{pairs.road_km.isna().sum()} calibration pairs failed; inspect {sample_path} and rerun to retry")
    model, pairs = fit_model(pairs, args.seed)
    model.payload.update(metadata)
    model.payload["rejected_pairs_n"] = len(rejected)
    model.payload["sampling_target"] = "road-connected endpoints snapping within max_snap_m; failed pairs replaced, rare exhausted cells redistribute"
    model.payload["snap_over_500m_n"] = int((pairs.max_snap_m > 500).sum())
    pairs.to_csv(sample_path, index=False)
    (args.output_dir / "detour_model.json").write_text(json.dumps(model.payload, indent=2), encoding="utf-8")
    print(json.dumps(model.payload["validation"], indent=2))


if __name__ == "__main__":
    main()
