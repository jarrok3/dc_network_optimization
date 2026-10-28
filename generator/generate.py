"""Create dummy input tables for the full/reduced run: stores, DCs, sites, demand history."""
from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

# Konfiguracja skalowania i parametrów (Thinning)
SEED = 437
THINNING_FRACTION = 0.2
N_STORES = int(10000 * THINNING_FRACTION)  # 2000
N_SITES = 60
N_CURRENT_DCS = 12

DC_FIXED_FULL = 150_000_000
DC_FIXED = int(DC_FIXED_FULL * THINNING_FRACTION)  # 30 000 000 KRW
MIN_STORES = int(500 * THINNING_FRACTION)          # 100
MAX_STORES = int(1400 * THINNING_FRACTION)         # 280

FUEL_PER_KM = 350
VEHICLE_MONTHLY = 5_500_000
SPOT_DAILY = 550_000
LATE_PENALTY = 120_000
STOCKOUT_PENALTY = 500_000
CAPACITY_CAGES = 48
N_HISTORY_DAYS = 365
HISTORY_END = "2026-10-27"

# Ścieżki wyjściowe
DATA_DIR = Path("data")
STORES_CSV = DATA_DIR / "stores.csv"
DCS_CSV = DATA_DIR / "dcs.csv"
SITES_CSV = DATA_DIR / "candidate_sites.csv"
DEMAND_CSV = DATA_DIR / "demand_history.csv"
COSTS_CSV = DATA_DIR / "cost_parameters.csv"

# Parametry popytu
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


def _clip_korea_mainland(lat: float, lon: float) -> tuple[float, float]:
    # Ograniczenia dla lądu Korei Południowej, z wykluczeniem wyspy Czedżu (lat < 34.0)
    return float(np.clip(lat, 34.3, 38.3)), float(np.clip(lon, 126.1, 129.4))


def generate_tables(seed: int | None = None) -> dict:
    rng = np.random.default_rng(seed if seed is not None else SEED)
    
    # 1. Generacja 60 lokalizacji kandydujących
    sites = []
    for i in range(N_SITES):
        lat = rng.uniform(34.3, 38.3)
        lon = rng.uniform(126.1, 129.4)
        mult = 1.3 if lat > 37.0 and lon < 127.5 else rng.uniform(0.9, 1.1)
        sites.append({
            "site_id": f"SITE_{i:02d}",
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "monthly_fixed_cost": int(round(DC_FIXED * mult)),
            "max_stores": MAX_STORES if rng.random() > 0.25 else 200,
            "name": f"Logistics_Zone_{i:02d}"
        })

    # 2. Wybór 12 historycznych magazynów
    dcs = []
    selected_sites = rng.choice(sites, N_CURRENT_DCS, replace=False)
    for i, site in enumerate(selected_sites):
        dcs.append({
            "dc_id": f"DC_{i:02d}",
            "site_id": site["site_id"],
            "lat": site["lat"],
            "lon": site["lon"],
            "lease_expires_on": (date(2027, 1, 1) + timedelta(days=int(rng.uniform(0, 1800)))).isoformat(),
            "monthly_fixed_cost": site["monthly_fixed_cost"],
            "max_stores": site["max_stores"],
            "min_stores": MIN_STORES,
            "name": f"Current_DC_{i:02d}"
        })

    # 3. Generacja 2000 sklepów
    stores = []
    for i in range(N_STORES):
        u = rng.random()
        if u < 0.51:
            region, lat0, lon0, scatter = "capital", 37.56, 126.98, 0.25
        elif u < 0.90:
            region = "other-metro"
            lat0 = rng.choice([35.17, 35.87, 35.15])
            lon0 = rng.choice([129.07, 128.60, 126.85])
            scatter = 0.15
        else:
            region, lat0, lon0, scatter = "rural", 36.5, 127.5, 1.5

        lat, lon = _clip_korea_mainland(rng.normal(lat0, scatter), rng.normal(lon0, scatter * 1.2))
        stype = TYPES[int(rng.choice(len(TYPES), p=TYPE_P))]
        opened = date(2012, 1, 1) + timedelta(days=int(rng.integers(0, 5000)))
        closed = ""
        if rng.random() < 0.02:
            closed = (date.fromisoformat(HISTORY_END) - timedelta(days=int(rng.integers(10, 80)))).isoformat()
            
        nearest_dc = min(dcs, key=lambda dc: (lat - dc["lat"])**2 + (lon - dc["lon"])**2)
        
        stores.append({
            "store_id": f"S{i + 1:04d}",
            "region": region,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "store_type": stype,
            "opened_on": opened.isoformat(),
            "closed_on": closed,
            "current_dc_id": nearest_dc["dc_id"],
        })

    # 4. Generacja historii popytu (12 miesięcy)
    end = date.fromisoformat(HISTORY_END)
    start = end - timedelta(days=N_HISTORY_DAYS - 1)
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

    # 5. Zapis parametrów kosztowych
    costs = [
        {"name": "fuel_per_km", "value": FUEL_PER_KM, "unit": "KRW/km"},
        {"name": "vehicle_monthly", "value": VEHICLE_MONTHLY, "unit": "KRW/month"},
        {"name": "spot_daily", "value": SPOT_DAILY, "unit": "KRW/day"},
        {"name": "late_penalty", "value": LATE_PENALTY, "unit": "KRW"},
        {"name": "stockout_penalty", "value": STOCKOUT_PENALTY, "unit": "KRW"},
        {"name": "capacity_cages", "value": CAPACITY_CAGES, "unit": "cages"},
        {"name": "dc_fixed", "value": DC_FIXED, "unit": "KRW/month"},
        {"name": "min_stores", "value": MIN_STORES, "unit": "stores"},
        {"name": "max_stores", "value": MAX_STORES, "unit": "stores"},
    ]

    _write_csv(STORES_CSV, stores, list(stores[0].keys()))
    _write_csv(DCS_CSV, dcs, list(dcs[0].keys()))
    _write_csv(SITES_CSV, sites, list(sites[0].keys()))
    _write_csv(DEMAND_CSV, history, ["store_id", "date", "cages_ordered"])
    _write_csv(COSTS_CSV, costs, ["name", "value", "unit"])
    
    return {"n_stores": N_STORES, "n_history": len(history), "n_sites": N_SITES, "n_dcs": N_CURRENT_DCS}

if __name__ == "__main__":
    info = generate_tables()
    print(f"Wygenerowano dane w katalogu {DATA_DIR}:", info)