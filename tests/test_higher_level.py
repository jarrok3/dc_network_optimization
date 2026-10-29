"""Exact assignment/MIP checks, accounting, search freshness and holdout isolation."""
import itertools
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from demand_model import PlanningDays
from higher_level import NetworkContext, NetworkEvaluator, SearchSettings, run_pipeline, solve_assignment
from higher_level.evaluation import bootstrap_interval, comparison_intervals
from higher_level.pool import add_vehicle_proxy_term, build_pool, p_median
from higher_level.search import run_development, run_search
from higher_level.surrogate import GapGaussianProcess, expected_improvement
from lower_level import DailyRoutingSolver, LLParams


def quiet(*args, **kwargs):
    pass


class MemoryDistances:
    def __init__(self):
        self.points = {"A": (0, 0), "B": (4, 0), "C": (2, 2),
                       "s1": (1, 0), "s2": (1, 1), "s3": (3, 0), "s4": (3, 1)}

    def lookup(self, a, b):
        return float(np.linalg.norm(np.array(self.points[a]) - self.points[b])), "engine"

    def submatrix(self, ids):
        return np.array([[self.lookup(a, b)[0] for b in ids] for a in ids])


def make_context(loader_hook=None):
    distances = MemoryDistances()
    ids, sites = ["s1", "s2", "s3", "s4"], ["A", "B", "C"]
    road = np.array([[distances.lookup(s, j)[0] for j in sites] for s in ids])
    router = DailyRoutingSolver(distances, LLParams())

    def loader(label):
        if loader_hook:
            loader_hook(label)
        n = {"D_dev": 7, "D7": 7, "D28": 28, "H14": 14}[label]
        offset = {"D_dev": 0, "D7": 50, "D28": 100, "H14": 200}[label]
        dates = [date(2026, 1, 1) + timedelta(days=offset + i) for i in range(n)]
        # Deliberately permute input store columns to exercise ID alignment.
        return PlanningDays(label, dates, np.ones(n) / n, ids[::-1], np.full((n, 4), 2))
    ctx = NetworkContext(ids, sites, road, road, [2] * 4, [100, 120, 90], [1] * 3, [4] * 3,
                         router, {"s1": "A", "s2": "A", "s3": "B", "s4": "B"},
                         vehicle_monthly=10, day_loader=loader)
    ctx.site_lat_lon = np.array([[37, 127], [37, 127.1], [37.1, 127]])
    return ctx


class AssignmentTests(unittest.TestCase):
    def test_lp_matches_exhaustive_and_meets_minima(self):
        d = np.array([[1, 4], [2, 2], [4, 1], [3, 2]], float)
        w = np.array([1, 4, 2, 3], float)
        el = np.array([[1, 0], [1, 1], [0, 1], [1, 1]], bool)
        result = solve_assignment(d, w, [2, 2], [2, 3], el)
        costs = []
        for a in itertools.product(range(2), repeat=4):
            loads = np.bincount(a, minlength=2)
            if (loads >= [2, 2]).all() and (loads <= [2, 3]).all() and all(el[i, j] for i, j in enumerate(a)):
                costs.append(sum(w[i] * d[i, j] for i, j in enumerate(a)))
        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(result["objective"], min(costs))
        self.assertTrue(result["optimality_proven"])

    def test_hall_conflict_and_bad_data(self):
        result = solve_assignment(np.ones((3, 2)), np.ones(3), [0, 0], [1, 2], [[1, 0], [1, 0], [0, 1]])
        self.assertEqual(result["status"], "infeasible")
        self.assertIn("eligibility/capacity", result["reason"])
        with self.assertRaises(ValueError):
            solve_assignment([[1, float("nan")]], [1], [0, 0], [1, 1])
        with self.assertRaises(ValueError):
            solve_assignment([[1]], [1], [.5], [1])

    def test_mip_seed_matches_subset_enumeration(self):
        ctx = make_context()
        result = p_median(ctx, 2, 10, time_limit=5)
        exact = min(ctx.fixed_costs[[ctx.site_index[s] for s in sites]].sum() + 10 * ctx.assignment(sites)["objective"]
                    for sites in itertools.combinations(ctx.site_ids, 2))
        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(result["objective_krw"], exact, places=5)
        self.assertTrue(result["optimality_proven"])


class EvaluationTests(unittest.TestCase):
    def test_weighted_monthly_accounting_and_frozen_holdout(self):
        ctx = make_context()
        evaluator = NetworkEvaluator(ctx)
        result = evaluator.evaluate(["A", "B"], "D28")
        self.assertTrue(result["feasible"])
        self.assertEqual(result["lower_level_solves"], 56)
        self.assertEqual(result["fleet"], {"A": 1, "B": 1})
        self.assertAlmostEqual(result["total"], sum(result[k] for k in ("dc_fixed", "vehicle_fixed", "fuel", "spot", "failure")))
        self.assertAlmostEqual(np.dot(result["weights"], result["per_day_total"]), result["total"])
        held = evaluator.evaluate(["A", "B"], "H14", fleet=result["fleet"])
        self.assertEqual(held["fleet"], result["fleet"])
        self.assertEqual(held["fleet_source"], "fixed from D28")
        self.assertEqual(held["lower_level_solves"], 28)

    def test_holdout_cannot_size_fleet_or_load_before_rejection(self):
        loaded = []
        ctx = make_context(loaded.append)
        with self.assertRaisesRegex(ValueError, "H14 requires"):
            NetworkEvaluator(ctx).evaluate(["A"], "H14")
        self.assertNotIn("H14", loaded)

    def test_preflight_catches_uncovered_without_reading_days(self):
        loaded = []
        ctx = make_context(loaded.append)
        ctx.road[0] = 200
        ctx.eligible[0] = False
        with tempfile.TemporaryDirectory() as tmp:
            report = run_pipeline(ctx, tmp, SearchSettings(budget=4, pool_size=6), log=quiet)
        self.assertEqual(report["status"], "infeasible_input")
        self.assertIsNone(report["recommended"])
        self.assertEqual(report["preflight"]["uncovered_stores"][0]["store_id"], "s1")
        self.assertEqual(loaded, [])
        self.assertEqual(ctx.router.solve_calls, 0)

    def test_service_failure_is_not_selected_as_feasible(self):
        result = NetworkEvaluator(make_context()).evaluate(["A", "B", "C"], "D7")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["feasible"])
        self.assertGreater(max(result["unserved_by_day"]), 0)


class SurrogateTests(unittest.TestCase):
    def test_lognormal_ei_matches_monte_carlo_and_zero_variance(self):
        rng = np.random.default_rng(437)
        expected = float(expected_improvement([100], [.1], [.3], 120)[0])
        draws = 100 * np.exp(rng.normal(.1, .3, 300000))
        actual = np.maximum(120 - draws, 0).mean()
        self.assertAlmostEqual(expected, actual, delta=.15)
        self.assertEqual(expected_improvement([100], [0], [0], 120)[0], 20)

    def test_gp_handles_duplicate_features(self):
        x = np.zeros((3, 6))
        gp = GapGaussianProcess().fit(x, np.array([.1, .1, .1]))
        mean, std = gp.predict(np.zeros((2, 6)))
        np.testing.assert_allclose(mean, .1, atol=1e-5)
        self.assertTrue(np.isfinite(std).all())
        self.assertTrue((std >= 0).all())

    def test_paired_bootstrap_uses_weights_once(self):
        a = bootstrap_interval([0, 100], [.9, .1], np.random.default_rng(1), 1000)
        self.assertEqual(a["mean"], 10)
        row = {"status": "ok", "day_labels": ["a", "b"], "weights": [.9, .1], "per_day_total": [100, 200], "total": 110}
        same = comparison_intervals(row, row, np.random.default_rng(2), 100)
        self.assertEqual((same["mean"], same["lower"], same["upper"]), (0, 0, 0))


class PipelineTests(unittest.TestCase):
    def test_end_to_end_search_fresh_solves_seeds_and_holdout_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = []
            def hook(label):
                loaded.append(label)
                if label == "H14":
                    self.assertTrue((Path(tmp) / "recommended.json").exists())
            ctx = make_context(hook)
            settings = SearchSettings(budget=4, pool_size=6, mip_time_limit=3, bootstrap_replicates=40)
            report = run_pipeline(ctx, tmp, settings, log=quiet)
            self.assertIn(report["status"], ("complete", "holdout_service_failure"))
            self.assertTrue(report["heldout_accessed"])
            self.assertLess(loaded.index("D7"), loaded.index("H14"))
            self.assertEqual(report["holdout"]["recommended"]["fleet"], report["recommended"]["fleet"])
            for name in ("search", "control"):
                lines = [json.loads(s) for s in (Path(tmp) / "logs" / f"{name}_437.jsonl").read_text().splitlines()]
                self.assertEqual(len(lines), 4)
                self.assertEqual(len({s["candidate_id"] for s in lines}), 4)
                self.assertTrue(all(s["number_of_solves"] == s["k"] * 7 for s in lines))
                self.assertEqual([s["phase"] for s in lines[:3]], ["seed"] * 3)
            self.assertTrue((Path(tmp) / "logs" / "seeds.json").exists())
            self.assertEqual(report["recommended"]["day_set"], "D28")
            json.dumps(json.loads((Path(tmp) / "report.json").read_text()), allow_nan=False)

    def test_development_is_separate_and_pool_replays_do_not_resolve(self):
        ctx = make_context()
        pool = build_pool(ctx, 10, size=5, mip_time_limit=3, log=quiet)
        evaluator = NetworkEvaluator(ctx)
        report = run_development(evaluator, pool, budget=4, replays=1, log=quiet)
        self.assertEqual(report["day_set"], "D_dev")
        expected = sum(len(c.sites) * 7 for c in pool["candidates"])
        self.assertEqual(ctx.router.solve_calls, expected)
        self.assertEqual(len(report["replays"]), 2)
        self.assertNotIn("H14", ctx._days)
        corrected_pool = add_vehicle_proxy_term(ctx, pool)
        self.assertTrue(all(c.vehicle_proxy_term > 0 for c in corrected_pool["candidates"]))
        self.assertTrue(all(a.G > b.G for a, b in zip(corrected_pool["candidates"], pool["candidates"])))
        second = run_development(evaluator, corrected_pool, budget=4, replays=1, log=quiet,
                                 cached_evaluations=report["evaluations"])
        self.assertTrue(second["development_E_reused"])
        self.assertEqual(ctx.router.solve_calls, expected)


if __name__ == "__main__":
    unittest.main()
