"""Toy-instance settings (IE437 midterm, Section 5: few hundred stores, two DCs)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SEED = 437
N_STORES = 160
N_HISTORY_DAYS = 90
HISTORY_END = "2026-10-27"

# Geography: fictional basin around a north–south river (bridges only).
RIVER_LON = 126.980
BRIDGES = [(37.500, 126.980), (37.545, 126.980), (37.590, 126.980)]
ROAD_FACTOR = 1.18  # urban grid on top of haversine / bridge path
KM_PER_DEG_LAT = 111.32

# Current DCs (also candidate sites) and two extra leasable sites.
CURRENT_DCS = [
    {"site_id": "W", "dc_id": "DC-W", "name": "West yard", "lat": 37.548, "lon": 126.905, "mult": 1.0},
    {"site_id": "E", "dc_id": "DC-E", "name": "East yard", "lat": 37.536, "lon": 127.055, "mult": 1.0},
]
EXTRA_SITES = [
    {"site_id": "N", "name": "North interchange", "lat": 37.612, "lon": 126.972, "mult": 1.15},
    {"site_id": "S", "name": "South logistics", "lat": 37.478, "lon": 127.012, "mult": 0.90},
]

# Teaching defaults (brief 2.6), with store-count bounds sized for this toy.
DC_FIXED = 18_000_000          # KRW / month (toy region, not nationwide thinning)
VEHICLE_MONTHLY = 5_500_000
FUEL_PER_KM = 350
SPOT_DAILY = 550_000
LATE_PENALTY = 120_000
STOCKOUT_PENALTY = 500_000
CAPACITY_CAGES = 48
MAX_STOPS = 25
MAX_KM = 250
SPEED_KMH = 35
STOP_MIN = 15
DEPART_MIN = 5 * 60            # 05:00
NOON_MIN = 12 * 60
LATE_MIN = 14 * 60
MAX_ROUTE_MIN = 10 * 60
NOON_BUDGET_ALPHA = 0.02
FLEET_QUANTILE = 2.0 / 3.0
DAYS_PER_MONTH = 30.4
MIN_STORES = 50
MAX_STORES = 120
LL_MAX_PASSES = 4

D_SEARCH = 3
D_FINAL = 5
D_HOLDOUT = 3

GENERATOR_DIR = ROOT / "generator" / "data"
DISTANCE_DIR = ROOT / "distances" / "cache"
UI_DIR = ROOT / "ui"

STORES_CSV = GENERATOR_DIR / "stores.csv"
DCS_CSV = GENERATOR_DIR / "dcs.csv"
SITES_CSV = GENERATOR_DIR / "candidate_sites.csv"
DEMAND_CSV = GENERATOR_DIR / "demand_history.csv"
COSTS_CSV = GENERATOR_DIR / "cost_parameters.csv"
DIST_JSON = DISTANCE_DIR / "road_distance.json"
TOY_JS = UI_DIR / "toy_data.js"
DEMO_HTML = UI_DIR / "demo.html"
