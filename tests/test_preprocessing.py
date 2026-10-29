"""Statistical invariants and offline integration checks, with no live server required."""
import json
import io
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from demand_model import DemandModel, PlanningDays
from distances.calibrate import fit_model, haversine, main as calibrate_main, query_pair, sample_pairs
from distances.lookup import RoadDistances
from planning_days import SET_SIZES, generate_sets


class DemandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        rng = np.random.default_rng(7)
        stores, rows = [], []
        start = date(2025, 10, 28)
        for i in range(10):
            opened = start if i < 8 else start + timedelta(days=351 if i == 8 else 365)
            stores.append({"store_id": f"S{i}", "store_type": "residential" if i % 2 else "office",
                           "opened_on": opened.isoformat(), "closed_on": "" if i != 7 else "2026-09-01"})
            for j in range(365):
                day = start + timedelta(days=j)
                if day < opened or (i == 7 and day >= date(2026, 9, 1)):
                    continue
                effect = (1.5 if day.weekday() >= 5 else 0.8) if i % 2 else (0.6 if day.weekday() >= 5 else 1.2)
                rows.append({"store_id": f"S{i}", "date": day.isoformat(), "cages_ordered": rng.poisson((1.1 + i * .15) * effect)})
        cls.stores, cls.history = cls.root / "stores.csv", cls.root / "history.csv"
        pd.DataFrame(stores).to_csv(cls.stores, index=False)
        pd.DataFrame(rows).to_csv(cls.history, index=False)
        cls.model = DemandModel.fit(cls.stores, cls.history)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_gamma_update_and_short_history(self):
        m = self.model
        self.assertEqual(m.metadata["likelihood"], "poisson")
        for i, t in enumerate(m.stores.store_type):
            self.assertAlmostEqual(m.a[i], m.metadata["priors"][t]["a"] + m.metadata["observed_cages"][i])
            self.assertAlmostEqual(m.b[i], m.metadata["priors"][t]["b"] + m.metadata["exposure"][i])
        self.assertEqual(m.metadata["observed_days"][8], 14)
        self.assertEqual(m.metadata["observed_days"][9], 0)
        self.assertAlmostEqual(m.posterior_mean()[9], m.metadata["priors"][m.stores.store_type.iloc[9]]["mu"])
        self.assertNotIn(7, m.active_idx)
        self.assertNotIn(9, m.active_idx)

    def test_calendar_normalization_and_estimated_weekends(self):
        days = [date(2025, 10, 28) + timedelta(days=i) for i in range(365)]
        np.testing.assert_allclose(self.model.calendar_factors(days).mean(axis=0), 1, atol=1e-12)
        wd = self.model.metadata["weekday_by_type"]
        self.assertGreater(np.mean(wd["residential"][5:]), np.mean(wd["residential"][:5]))
        self.assertLess(np.mean(wd["office"][5:]), np.mean(wd["office"][:5]))

    def test_model_round_trip(self):
        self.model.save(self.root / "saved")
        loaded = DemandModel.load(self.root / "saved")
        np.testing.assert_array_equal(loaded.posterior_mean(), self.model.posterior_mean())
        day = [date(2026, 1, 1)]
        np.testing.assert_array_equal(loaded.draw(day, np.random.default_rng(3)), self.model.draw(day, np.random.default_rng(3)))

    def test_planning_sets_weights_disjoint_and_reproducible(self):
        first = generate_sets(self.model, self.root / "plans1")
        second = generate_sets(self.model, self.root / "plans2")
        different = generate_sets(self.model, self.root / "plans3", seed=438)
        seen = set()
        for label, n in SET_SIZES.items():
            days = first[label]
            self.assertEqual(len(days.dates), n)
            self.assertEqual(len(set(days.dates)), n)
            self.assertFalse(seen.intersection(days.dates))
            seen.update(days.dates)
            self.assertAlmostEqual(days.weights.sum(), 1)
            self.assertTrue((days.weights > 0).all())
            self.assertEqual(days.demand.shape, (n, len(self.model.active_idx)))
            self.assertEqual(days.store_ids, self.model.stores.store_id.iloc[self.model.active_idx].tolist())
            np.testing.assert_array_equal(days.demand, second[label].demand)
            self.assertEqual(days.dates, second[label].dates)
            saved = PlanningDays.load(self.root / "plans1" / f"{label}.npz")
            np.testing.assert_array_equal(saved.demand, days.demand)
            self.assertFalse(np.array_equal(days.demand, different[label].demand))
        manifest = json.loads((self.root / "plans1" / "manifest.json").read_text())
        for info in manifest["sets"].values():
            for row in info["days"]:
                self.assertAlmostEqual(row["weight"], row["annual_group_days"] / 365 / row["sampled_group_days"])

    def test_missing_record_and_duplicates_rejected(self):
        history = pd.read_csv(self.history)
        path = self.root / "bad.csv"
        history.iloc[1:].to_csv(path, index=False)
        with self.assertRaisesRegex(ValueError, "missing open-day"):
            DemandModel.fit(self.stores, path)
        pd.concat([history, history.iloc[:1]]).to_csv(path, index=False)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            DemandModel.fit(self.stores, path)

    def test_negative_binomial_fallback(self):
        history = pd.read_csv(self.history)
        rng = np.random.default_rng(8)
        history["cages_ordered"] = rng.negative_binomial(1, .25, len(history))
        path = self.root / "overdispersed.csv"
        history.to_csv(path, index=False)
        model = DemandModel.fit(self.stores, path)
        self.assertEqual(model.metadata["likelihood"], "negative_binomial")
        np.testing.assert_allclose(model.grid_prob.sum(axis=1), 1)
        self.assertTrue(np.isfinite(model.posterior_mean()).all())
        prior = model.metadata["priors"][model.stores.store_type.iloc[9]]
        self.assertAlmostEqual(model.posterior_mean()[9], prior["mu"], places=5)
        self.assertTrue((model.draw([date(2026, 6, 1)], rng) >= 0).all())


class DistanceTests(unittest.TestCase):
    def test_calibration_replacements_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stores = root / "stores.csv"
            pd.DataFrame({"store_id": [f"S{i}" for i in range(12)], "lat": np.linspace(35, 38, 12),
                          "lon": np.linspace(126.5, 129, 12), "region": ["capital", "other-metro", "rural"] * 4}).to_csv(stores, index=False)
            queries = []
            def engine(session, url, a, b, timeout, max_snap_m):
                queries.append((a.name, b.name))
                if len(queries) == 1:
                    raise ValueError("OSRM snap exceeds 500 m")
                return float(haversine(a.lat, a.lon, b.lat, b.lon)) * 1.3, 20
            argv = ["--stores", str(stores), "--output-dir", str(root / "out"), "--sample-size", "10"]
            with patch("distances.calibrate.query_pair", side_effect=engine), patch("sys.stdout", new=io.StringIO()):
                calibrate_main(argv)
            self.assertEqual(len(queries), 11)
            sample = pd.read_csv(root / "out" / "detour_sample.csv")
            self.assertEqual(len(sample), 10)
            self.assertTrue(sample.road_km.notna().all())
            with patch("distances.calibrate.query_pair", side_effect=AssertionError("resume made a network query")), patch("sys.stdout", new=io.StringIO()):
                calibrate_main(argv)
            metadata = json.loads((root / "out" / "detour_model.json").read_text())
            self.assertEqual(metadata["rejected_pairs_n"], 1)
            self.assertEqual(metadata["train_n"], 7)
            self.assertEqual(metadata["validation_n"], 3)

    def test_sampling_unique_reproducible_and_river_coverage(self):
        rng = np.random.default_rng(1)
        frame = pd.DataFrame({"store_id": [f"S{i}" for i in range(90)],
                              "lat": np.r_[rng.uniform(37.42, 37.65, 30), rng.uniform(35, 38, 60)],
                              "lon": np.r_[rng.uniform(126.85, 127.10, 30), rng.uniform(126, 129, 60)],
                              "region": ["capital"] * 30 + ["other-metro"] * 30 + ["rural"] * 30})
        pairs = sample_pairs(frame, 100)
        pd.testing.assert_frame_equal(pairs, sample_pairs(frame, 100))
        self.assertEqual(len(pairs), 100)
        self.assertFalse(pairs.duplicated(["from_id", "to_id"]).any())
        self.assertEqual(set(pairs.band), set(range(5)))
        self.assertEqual(set(pairs.area), {"capital", "other-metro", "rural"})
        self.assertTrue(pairs.river_screen.any())

    def test_train_only_fit_and_held_out_error(self):
        rows = pd.DataFrame({"haversine_km": np.tile([5, 20, 40, 100, 200], 200),
                             "band": np.tile(range(5), 200), "area": "capital", "river_screen": False})
        rows["road_km"] = rows.haversine_km * 1.3
        model, split = fit_model(rows)
        self.assertEqual((split.split == "train").sum(), 700)
        self.assertEqual((split.split == "validation").sum(), 300)
        self.assertAlmostEqual(model.estimate(200, "capital"), 260)
        self.assertLess(model.payload["validation"]["mape_percent"], 1e-10)
        split.loc[split.split == "validation", "road_km"] *= 2
        changed, _ = fit_model(split.drop(columns="split"))
        self.assertEqual(model.payload["by_band_area"], changed.payload["by_band_area"])
        self.assertGreater(changed.payload["validation"]["mape_percent"], 40)

    def test_osrm_symmetry_units_and_failures(self):
        session = Mock()
        response = session.get.return_value
        response.json.return_value = {"code": "Ok", "distances": [[0, 10000], [12000, 0]],
                                      "sources": [{"distance": 10}, {"distance": 20}]}
        p = pd.Series({"lat": 37.5, "lon": 127.0})
        road, snap = query_pair(session, "http://localhost:5000", p, p)
        self.assertEqual((road, snap), (11, 20))
        self.assertEqual(session.get.call_args.kwargs["timeout"], 15)
        response.json.return_value["distances"][0][1] = None
        with self.assertRaisesRegex(ValueError, "no route"):
            query_pair(session, "http://localhost:5000", p, p)
        response.json.return_value["distances"][0][1] = 10000
        response.json.return_value["sources"][0]["distance"] = 700
        with self.assertRaisesRegex(ValueError, "snap exceeds"):
            query_pair(session, "http://localhost:5000", p, p)

    def test_offline_lookup_engine_detour_and_missing_short_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pd.DataFrame([{"store_id": "A", "lat": 37.5, "lon": 127, "region": "capital"},
                          {"store_id": "B", "lat": 35.2, "lon": 129, "region": "other-metro"},
                          {"store_id": "C", "lat": 37.51, "lon": 127, "region": "capital"}]).to_csv(root / "stores.csv", index=False)
            pd.DataFrame([{"site_id": "DC", "lat": 37.5, "lon": 127.1}]).to_csv(root / "sites.csv", index=False)
            pd.DataFrame([{"from_id": "DC", "to_id": "A", "km": 9, "source": "engine"}]).to_parquet(root / "cache.parquet")
            (root / "model.json").write_text(json.dumps({"global_ratio": 1.3, "by_band": {}, "by_band_area": {}}))
            table = RoadDistances(root / "stores.csv", root / "sites.csv", root / "cache.parquet", root / "model.json")
            self.assertEqual(table.lookup("A", "DC"), (9, "engine"))
            cached_matrix, sources = table.submatrix_with_sources(["A", "DC"])
            self.assertEqual(cached_matrix[0, 1], 9)
            self.assertEqual(sources[1, 0], "engine")
            self.assertEqual(table.lookup("A", "B")[1], "detour")
            self.assertEqual(table.lookup("A", "A"), (0, "identity"))
            with self.assertRaisesRegex(KeyError, "required engine distance"):
                table.lookup("A", "C")
            with self.assertRaisesRegex(KeyError, "required engine distance"):
                table.lookup("DC", "B")
            matrix = table.submatrix(["A", "B"])
            np.testing.assert_array_equal(matrix, matrix.T)


if __name__ == "__main__":
    unittest.main()
