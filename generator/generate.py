"""Create dummy input tables for the toy instance: stores, DCs, sites, demand history."""
from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as cfg

TYPES = ("residential", "office", "mixed", "roadside")
TYPE_P = (0.45, 0.20, 0.25, 0.10)
TYPE_MEAN = {"residential": 2.3, "office": 1.9, "mixed": 2.2, "roadside": 2.4}
WEEKDAY_F = {
    "residential": np.array([0.92, 0.90, 0.90, 0.92, 0.98, 1.18, 1.20]),
    "office": np.array([1.18, 1.20, 1.18, 1.15, 1.05, 0.62, 0.52]),
    "mixed": np.array([1.02, 1.02, 1.02, 1.04, 1.06, 0.92, 0.90]),
    "roadside": np.array([0.95, 0.95, 0.96, 0.98, 1.05, 1.10, 1.06]),
}
MONTH_F = np.array([0.92, 0.90, 0.95, 1.00, 1.05, 1.12, 1.15, 1.14, 1.05, 1.00, 0.96, 0.94])
HOLIDAYS = {date(2026, 8, 17), date(2026, 9, 24), date(2026, 9, 25), date(2026, 10, 3), date(2026, 10, 9)}


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def _clip_latlon(lat, lon):
    return float(np.clip(lat, 37.45, 37.65)), float(np.clip(lon, 126.82, 127.18))


def generate_tables(seed: int | None = None) -> dict:
    rng = np.random.default_rng(seed if seed is not None else cfg.SEED)
    n = cfg.N_STORES

    sites = []
    for rec in cfg.CURRENT_DCS + cfg.EXTRA_SITES:
        sites.append({
            "site_id": rec["site_id"],
            "lat": rec["lat"],
            "lon": rec["lon"],
            "monthly_fixed_cost": int(round(cfg.DC_FIXED * rec["mult"])),
            "max_stores": cfg.MAX_STORES,
            "name": rec["name"],
        })

    dcs = []
    for rec in cfg.CURRENT_DCS:
        dcs.append({
            "dc_id": rec["dc_id"],
            "site_id": rec["site_id"],
            "lat": rec["lat"],
            "lon": rec["lon"],
            "lease_expires_on": "2028-06-30" if rec["site_id"] == "W" else "2029-03-31",
            "monthly_fixed_cost": int(round(cfg.DC_FIXED * rec["mult"])),
            "max_stores": cfg.MAX_STORES,
            "min_stores": cfg.MIN_STORES,
            "name": rec["name"],
        })

    # Two urban clusters (west / east of the river) plus a thin rural scatter.
    stores = []
    for i in range(n):
        u = rng.random()
        if u < 0.46:
            region, lat0, lon0, scatter = "west-bank", 37.550, 126.910, 0.028
        elif u < 0.90:
            region, lat0, lon0, scatter = "east-bank", 37.538, 127.050, 0.030
        else:
            region, lat0, lon0, scatter = "outer", 37.545, 126.980, 0.070
        lat, lon = _clip_latlon(rng.normal(lat0, scatter), rng.normal(lon0, scatter * 1.4))
        stype = TYPES[int(rng.choice(len(TYPES), p=TYPE_P))]
        opened = date(2012, 1, 1) + timedelta(days=int(rng.integers(0, 14 * 365)))
        closed = ""
        if rng.random() < 0.02:
            closed = (date.fromisoformat(cfg.HISTORY_END) - timedelta(days=int(rng.integers(10, 80)))).isoformat()
        # Historical assignment: nearest current DC on map (baseline lock-in).
        d_w = (lat - cfg.CURRENT_DCS[0]["lat"]) ** 2 + (lon - cfg.CURRENT_DCS[0]["lon"]) ** 2
        d_e = (lat - cfg.CURRENT_DCS[1]["lat"]) ** 2 + (lon - cfg.CURRENT_DCS[1]["lon"]) ** 2
        current = "DC-W" if d_w <= d_e else "DC-E"
        stores.append({
            "store_id": f"S{i + 1:03d}",
            "region": region,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "store_type": stype,
            "opened_on": opened.isoformat(),
            "closed_on": closed,
            "current_dc_id": current,
        })

    end = date.fromisoformat(cfg.HISTORY_END)
    start = end - timedelta(days=cfg.N_HISTORY_DAYS - 1)
    history = []
    for s in stores:
        opened = date.fromisoformat(s["opened_on"])
        closed = date.fromisoformat(s["closed_on"]) if s["closed_on"] else None
        mu = TYPE_MEAN[s["store_type"]] * float(np.exp(rng.normal(0, 0.45)))
        theta = float(np.clip(mu, 0.5, 6.0))
        wd = WEEKDAY_F[s["store_type"]]
        d = start
        while d <= end:
            if d >= opened and (closed is None or d < closed):
                e = wd[d.weekday()] * MONTH_F[d.month - 1]
                if d in HOLIDAYS:
                    e *= 1.35
                y = int(rng.poisson(theta * e))
                history.append({"store_id": s["store_id"], "date": d.isoformat(), "cages_ordered": y})
            d += timedelta(days=1)

    costs = [
        {"name": "fuel_per_km", "value": cfg.FUEL_PER_KM, "unit": "KRW/km"},
        {"name": "vehicle_monthly", "value": cfg.VEHICLE_MONTHLY, "unit": "KRW/month"},
        {"name": "spot_daily", "value": cfg.SPOT_DAILY, "unit": "KRW/day"},
        {"name": "late_penalty", "value": cfg.LATE_PENALTY, "unit": "KRW"},
        {"name": "stockout_penalty", "value": cfg.STOCKOUT_PENALTY, "unit": "KRW"},
        {"name": "capacity_cages", "value": cfg.CAPACITY_CAGES, "unit": "cages"},
        {"name": "dc_fixed", "value": cfg.DC_FIXED, "unit": "KRW/month"},
        {"name": "min_stores", "value": cfg.MIN_STORES, "unit": "stores"},
        {"name": "max_stores", "value": cfg.MAX_STORES, "unit": "stores"},
    ]

    _write_csv(cfg.STORES_CSV, stores, list(stores[0].keys()))
    _write_csv(cfg.DCS_CSV, dcs, list(dcs[0].keys()))
    _write_csv(cfg.SITES_CSV, sites, list(sites[0].keys()))
    _write_csv(cfg.DEMAND_CSV, history, ["store_id", "date", "cages_ordered"])
    _write_csv(cfg.COSTS_CSV, costs, ["name", "value", "unit"])
    return {"n_stores": n, "n_history": len(history), "n_sites": len(sites), "n_dcs": len(dcs)}


def load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_stores():
    rows = load_csv(cfg.STORES_CSV)
    for r in rows:
        r["lat"] = float(r["lat"])
        r["lon"] = float(r["lon"])
        r["closed_on"] = r["closed_on"] or None
    return rows


def load_sites():
    rows = load_csv(cfg.SITES_CSV)
    for r in rows:
        r["lat"] = float(r["lat"])
        r["lon"] = float(r["lon"])
        r["monthly_fixed_cost"] = int(float(r["monthly_fixed_cost"]))
        r["max_stores"] = int(r["max_stores"])
    return rows


def load_dcs():
    rows = load_csv(cfg.DCS_CSV)
    for r in rows:
        r["lat"] = float(r["lat"])
        r["lon"] = float(r["lon"])
        r["monthly_fixed_cost"] = int(float(r["monthly_fixed_cost"]))
        r["max_stores"] = int(r["max_stores"])
        r["min_stores"] = int(r["min_stores"])
    return rows


def load_demand():
    rows = load_csv(cfg.DEMAND_CSV)
    for r in rows:
        r["cages_ordered"] = int(r["cages_ordered"])
        r["date"] = datetime.strptime(r["date"], "%Y-%m-%d").date()
    return rows


if __name__ == "__main__":
    info = generate_tables()
    print("wrote", info)
