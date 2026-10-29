"""
Budowa lokalnego bufora odległości drogowych z użyciem serwera OSRM.
Zapisuje wyniki do pliku Parquet oraz generuje metadane JSON.
"""

import csv
import json
from datetime import date
import math
import time
from pathlib import Path

import pandas as pd
import requests

# Konfiguracja ścieżek wejściowych i wyjściowych
ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
CACHE_DIR = ROOT_DIR / "code" / "cache"

STORES_CSV = DATA_DIR / "stores.csv"
SITES_CSV = DATA_DIR / "candidate_sites.csv"

PARQUET_OUT = CACHE_DIR / "road_distance.parquet"
META_OUT = CACHE_DIR / "osrm_meta.json"

# Konfiguracja OSRM
OSRM_URL = "http://localhost:5000/table/v1/car"
MAX_DISTANCE_KM = 60.0  # Maksymalna odległość w linii prostej dla par store-store
CHUNK_SIZE = 100        # Rozmiar pojedynczego zapytania do serwera (ilość celów)

def haversine(lat1, lon1, lat2, lon2):
    """Oblicza odległość w linii prostej między współrzędnymi w kilometrach."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c

def get_osrm_distances(src, dst_list):
    """
    Wysyła zapytanie do lokalnego OSRM o odległości od punktu src do punktów dst_list.
    Zwraca listę odległości w kilometrach.
    """
    # OSRM przyjmuje format {lon},{lat}
    coords = [f"{src['lon']},{src['lat']}"] + [f"{d['lon']},{d['lat']}" for d in dst_list]
    coords_str = ";".join(coords)
    
    # sources=0 oznacza, że tylko pierwszy punkt na liście jest punktem startowym
    # annotations=distance wymusza zwrot odległości (w metrach) zamiast czasu
    url = f"{OSRM_URL}/{coords_str}?sources=0&annotations=distance"
    
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        if data["code"] != "Ok":
            raise ValueError(f"OSRM zwrócił kod: {data['code']}")
            
        # Wyciągamy dystanse z pierwszego źródła do wszystkich celów (omijając dystans źródło-źródło na indeksie 0)
        distances_meters = data["distances"][0][1:]
        return [dist / 1000.0 if dist is not None else None for dist in distances_meters]
        
    except Exception as e:
        print(f"Błąd zapytania OSRM dla źródła {src['id']}: {e}")
        return [None] * len(dst_list)

def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    print("Wczytywanie lokalizacji...")
    sites = []
    with open(SITES_CSV, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sites.append({"id": row["site_id"], "lat": float(row["lat"]), "lon": float(row["lon"])})
            
    stores = []
    with open(STORES_CSV, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            stores.append({"id": row["store_id"], "lat": float(row["lat"]), "lon": float(row["lon"])})

    results = []
    total_queries = 0
    t0 = time.time()

    print("1/2 Przetwarzanie par: Site -> Store (wszystkie)")
    for site in sites:
        for i in range(0, len(stores), CHUNK_SIZE):
            chunk = stores[i:i + CHUNK_SIZE]
            distances = get_osrm_distances(site, chunk)
            for store, dist in zip(chunk, distances):
                if dist is not None:
                    results.append({"from_id": site["id"], "to_id": store["id"], "km": round(dist, 4), "source": "engine"})
                    # Założenie o symetryczności (dodanie obu kierunków ułatwi później filtrowanie macierzy)
                    results.append({"from_id": store["id"], "to_id": site["id"], "km": round(dist, 4), "source": "engine"})
            total_queries += len(chunk)

    print(f"2/2 Przetwarzanie par: Store -> Store (<= {MAX_DISTANCE_KM} km)")
    for i, src_store in enumerate(stores):
        valid_targets = []
        # iterujemy tylko do przodu (j > i), by nie dublować zapytań dla (A->B) oraz (B->A)
        for j in range(i + 1, len(stores)):
            dst_store = stores[j]
            if haversine(src_store["lat"], src_store["lon"], dst_store["lat"], dst_store["lon"]) <= MAX_DISTANCE_KM:
                valid_targets.append(dst_store)
        
        # Zapytania w chunkach dla pofiltrowanych celów
        for k in range(0, len(valid_targets), CHUNK_SIZE):
            chunk = valid_targets[k:k + CHUNK_SIZE]
            distances = get_osrm_distances(src_store, chunk)
            for dst_store, dist in zip(chunk, distances):
                if dist is not None:
                    results.append({"from_id": src_store["id"], "to_id": dst_store["id"], "km": round(dist, 4), "source": "engine"})
                    results.append({"from_id": dst_store["id"], "to_id": src_store["id"], "km": round(dist, 4), "source": "engine"})
            total_queries += len(chunk)
            
        if (i + 1) % 200 == 0:
            print(f"  Przetworzono {i + 1}/{len(stores)} sklepów jako punkty startowe...")

    print("Zapisywanie bazy (Cache) do Parquet...")
    df = pd.DataFrame(results)
    # Ze względu na dodanie odwróconych par (B->A), usuwamy potencjalne zduplikowane rekordy z różnych etapów (np. site i store)
    df = df.drop_duplicates(subset=["from_id", "to_id"])
    df.to_parquet(PARQUET_OUT, index=False)

    print("Generowanie pliku metadanych...")
    meta_info = {
        "source": "engine",
        "distance_metric": "km",
        "engine": "OSRM osrm-backend via car.lua",
        "extract_date": str(date.today()), # Można później nadpisać na datę wyciągu OSM
        "pairs_computed": len(df),
        "symmetric_assumption": True,
        "store_store_radius_km": MAX_DISTANCE_KM,
        "execution_time_seconds": round(time.time() - t0, 1)
    }
    
    with open(META_OUT, "w", encoding="utf-8") as f:
        json.dump(meta_info, f, indent=4)
        
    print(f"Sukces! Czas wykonania: {meta_info['execution_time_seconds']} sek.")
    print(f"Zapisano {len(df)} krawędzi (kierunkowych) w: {PARQUET_OUT}")

if __name__ == "__main__":
    main()
