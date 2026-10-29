"""Persist disjoint, weighted posterior-predictive day sets for all candidates."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from calendar_effects import holiday_class, season
from demand_model import DemandModel, PlanningDays
from project import DEMAND_DIR, seed_sequence

SET_SIZES = {"D_dev": 7, "D7": 7, "D28": 28, "H14": 14}


def calendar_groups(dates, holidays, max_groups):
    """Coarsen rare cross-strata when n is smaller than their count.

    A probability sample from each merged group still has exactly its annual
    share as its weight. No holiday or season is forced into a seven-day set.
    """
    by = defaultdict(list)
    for d in dates:
        by[(season(d), d.weekday() >= 5, holiday_class(d, holidays))].append(d)
    groups = [{"keys": [key], "dates": days} for key, days in sorted(by.items())]
    # Each original group needs enough distinct dates for all four sets.
    while len(groups) > max_groups or (len(groups) > 1 and min(len(g["dates"]) for g in groups) < len(SET_SIZES)):
        i = min(range(len(groups)), key=lambda k: (len(groups[k]["dates"]), k))
        def score(j):
            left, right = groups[i]["keys"], groups[j]["keys"]
            same_season = any(a[0] == b[0] for a in left for b in right)
            same_day = any(a[1] == b[1] for a in left for b in right)
            return (not same_season, not same_day, len(groups[j]["dates"]), j)
        j = min((k for k in range(len(groups)) if k != i), key=score)
        groups[j]["keys"].extend(groups[i]["keys"])
        groups[j]["dates"].extend(groups[i]["dates"])
        groups.pop(i)
    return groups, len(by)


def select_dates(calendar, n, holidays, rng, used):
    groups, original_strata = calendar_groups(calendar, holidays, n)
    sizes = np.array([len(g["dates"]) for g in groups])
    allocation = np.ones(len(groups), dtype=int)
    # Largest-remainder allocation of spare slots, preserving at least one per group.
    extra = (n - len(groups)) * sizes / sizes.sum()
    allocation += np.floor(extra).astype(int)
    for i in np.argsort(-(extra - np.floor(extra)), kind="stable")[:n - allocation.sum()]:
        allocation[i] += 1
    selected, weights, detail = [], [], []
    for group, count in zip(groups, allocation):
        available = sorted(d for d in group["dates"] if d not in used)
        if len(available) < count:
            raise ValueError("planning calendar too small for disjoint sets in a stratum")
        draw = rng.choice(len(available), count, replace=False)
        weight = len(group["dates"]) / len(calendar) / count
        for pos in draw:
            day = available[int(pos)]
            selected.append(day)
            weights.append(weight)
            used.add(day)
            detail.append({"date": day.isoformat(), "weight": weight, "season": season(day),
                           "weekend": day.weekday() >= 5, "holiday_class": holiday_class(day, holidays),
                           "merged_strata": group["keys"], "annual_group_days": len(group["dates"]), "sampled_group_days": int(count)})
    return selected, np.array(weights), detail, original_strata


def generate_sets(model, output_dir=DEMAND_DIR / "planning", seed=437, calendar_start=None):
    start = date.fromisoformat(calendar_start or model.metadata["history_start"])
    try:
        end = start.replace(year=start.year + 1)
    except ValueError:
        end = start.replace(year=start.year + 1, day=28)
    calendar = [start + timedelta(days=i) for i in range((end - start).days)]
    holidays = set(model.metadata["holidays"])
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    used, sets = set(), {}
    streams = seed_sequence("planning", seed).spawn(len(SET_SIZES))
    manifest = {"seed": seed, "calendar_start": start.isoformat(), "calendar_end_exclusive": end.isoformat(),
                "calendar_interpretation": "representative annual calendar; store set frozen at history end",
                "sampling": "weekday/weekend x season x holiday; merge strata with fewer than four annual dates, then coarsen smallest strata within seasons for small sets",
                "weights": "original annual group share / sampled group days; sum to 1 within each set",
                "independence": "independent SeedSequence streams for each set and for dates/demand; date exclusion enforces disjointness (dates are consequently not statistically independent across sets)",
                "common_random_numbers": "load these saved arrays for every candidate and baseline; never resample inside evaluation",
                "holdout": "H14 is reserved for final evaluation with D28 fleets fixed",
                "likelihood": model.metadata["likelihood"], "sets": {}}
    model_digest = hashlib.sha256(json.dumps(model.metadata, sort_keys=True).encode()).hexdigest()
    manifest["model_sha256"] = model_digest
    for (label, n), stream in zip(SET_SIZES.items(), streams):
        date_seed, demand_seed = stream.spawn(2)
        dates, weights, details, original_count = select_dates(calendar, n, holidays, np.random.default_rng(date_seed), used)
        demand = model.draw(dates, np.random.default_rng(demand_seed))
        if (demand > 48).any():
            row, col = np.argwhere(demand > 48)[0]
            raise ValueError(f"{label} {dates[row]} store {model.stores.store_id.iloc[model.active_idx[col]]}: demand exceeds 48 cages")
        ids = model.stores.store_id.iloc[model.active_idx].tolist()
        days = PlanningDays(label, dates, weights, ids, demand)
        np.savez_compressed(output_dir / f"{label}.npz", label=label, dates=np.array([d.isoformat() for d in dates]),
                            weights=weights, store_ids=np.array(ids), demand=demand, model_sha256=model_digest)
        pd.DataFrame([{k: v for k, v in row.items() if k != "merged_strata"} for row in details]).to_csv(output_dir / f"{label}_days.csv", index=False)
        pd.DataFrame(demand, index=[d.isoformat() for d in dates], columns=ids).rename_axis("date").to_csv(output_dir / f"{label}_demand.csv")
        manifest["sets"][label] = {"n_days": n, "n_stores": len(ids), "original_strata": original_count,
                                    "date_spawn_key": list(date_seed.spawn_key), "demand_spawn_key": list(demand_seed.spawn_key), "days": details}
        sets[label] = days
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return sets


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=DEMAND_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEMAND_DIR / "planning")
    parser.add_argument("--seed", type=int, default=437)
    parser.add_argument("--calendar-start", help="ISO date; defaults to representative history-year calendar")
    args = parser.parse_args(argv)
    sets = generate_sets(DemandModel.load(args.model_dir), args.output_dir, args.seed, args.calendar_start)
    print("Saved " + ", ".join(f"{k}: {len(v.dates)} days" for k, v in sets.items()))


if __name__ == "__main__":
    main()
