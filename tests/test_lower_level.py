"""Independent feasibility, exact small-instance costs and upper-level API checks."""
import copy
import itertools
import json
import math
import unittest
from datetime import date

import numpy as np

from demand_model import PlanningDays
from lower_level import DailyRoutingSolver, LLParams, account, solve_dc_day, validate, weighted_fleet_size
from lower_level.solver import _Solver


def exact_small_cost(D, q, P, owned=0):
    """Exhaustive feasible routes + set partitioning, including unserved decisions."""
    n = len(q) - 1
    route_options = []
    for size in range(1, n + 1):
        for subset in itertools.combinations(range(1, n + 1), size):
            if size > P.max_stops or sum(q[i] for i in subset) > P.capacity:
                continue
            mask = sum(1 << (i - 1) for i in subset)
            best = {}
            for order in itertools.permutations(subset):
                km, time, previous, n12, n14 = 0, P.depart_min, 0, 0, 0
                for node in order:
                    km += D[previous][node]
                    time += D[previous][node] * 60 / P.speed_kmh
                    n12 += int(time > P.noon_min + 1e-9)
                    n14 += int(time > P.late_min + 1e-9)
                    time += P.stop_min
                    previous = node
                km += D[previous][0]
                time += D[previous][0] * 60 / P.speed_kmh
                if km <= P.max_km + 1e-9 and time - P.depart_min <= P.max_route_min + 1e-9:
                    cost = km * P.c_fuel + n14 * P.p_late
                    best[n12] = min(best.get(n12, math.inf), cost)
            route_options.extend((mask, late, cost) for late, cost in best.items())
    budget = math.floor(P.alpha * n + 1e-9)
    # State = served mask, number of routes, after-noon stops.
    dp = {(0, 0, 0): 0.0}
    for mask in range(1 << n):
        for (seen, vehicles, late), cost in list(dp.items()):
            if seen != mask:
                continue
            for rmask, rlate, rcost in route_options:
                if rmask & seen or late + rlate > budget:
                    continue
                key = (seen | rmask, vehicles + 1, late + rlate)
                dp[key] = min(dp.get(key, math.inf), cost + rcost)
    return min(cost + P.c_spot * max(0, vehicles - owned) + P.p_stockout * (n - mask.bit_count())
               for (mask, vehicles, late), cost in dp.items())


class CoreTests(unittest.TestCase):
    def test_zero_demand_still_requires_coverage(self):
        D = [[0, 1, 1], [1, 0, 1], [1, 1, 0]]
        q, P = [0, 0, 1], LLParams()
        sol = solve_dc_day(D, q, P, owned=1)
        self.assertEqual(sorted(i for r in sol["routes"] for i in r["stores"]), [1, 2])
        self.assertEqual(validate(sol, D, q, P), [])
        self.assertEqual(sol["cost"]["delivered"], 2)

    def test_bad_inputs_fail_with_data_errors(self):
        P = LLParams()
        for D, q in (([[0, 1], [1, 0]], [0, 49]), ([[0, 1], [2, 0]], [0, 1]),
                     ([[0, float("nan")], [1, 0]], [0, 1]), ([[0, 1], [1, 0]], [0, -1]),
                     ([[0, 1], [1, 0]], [0]), ([[0, 1], [1, 0]], [1, 1])):
            with self.assertRaises(ValueError):
                solve_dc_day(D, q, P)
        for kwargs in ({"owned": -1}, {"owned": 1.5}, {"spot_cap": -1}, {"alpha": 2}):
            with self.assertRaises(ValueError):
                solve_dc_day([[0]], [0], P, **kwargs)
        for kwargs in ({"speed_kmh": 0}, {"alpha": -.1}, {"max_stops": 1.5}, {"p_stockout": -1}):
            with self.assertRaises(ValueError):
                LLParams(**kwargs)

    def test_economic_stockout_and_owned_vehicle_objective(self):
        D, q, P = [[0, 1], [1, 0]], [0, 1], LLParams()
        sized = solve_dc_day(D, q, P)
        self.assertEqual(sized["unserved"], [1])
        self.assertEqual(sized["cost"]["total"], 500000)
        self.assertFalse(sized["service_level_met"])
        owned = solve_dc_day(D, q, P, owned=1)
        self.assertEqual(owned["unserved"], [])
        self.assertEqual(owned["cost"]["total"], 700)
        self.assertEqual(owned["cost"]["total"], exact_small_cost(D, q, P, owned=1))

    def test_all_hard_route_limits(self):
        coords = np.array([[0, 0], [1, 0], [2, 0], [0, 1], [0, 2], [1, 1]])
        D = np.linalg.norm(coords[:, None] - coords, axis=2)
        for changes in ({"capacity": 3}, {"max_stops": 2}, {"max_km": 5}, {"max_route_min": 40}, {"noon_min": 330}):
            P = LLParams().with_(**changes)
            q = [0, 1, 1, 1, 1, 1]
            sol = solve_dc_day(D, q, P, economic_stockouts=False)
            self.assertEqual(validate(sol, D, q, P), [])
            self.assertEqual(sum(len(r["stores"]) for r in sol["routes"]) + len(sol["unserved"]), 5)

    def test_noon_budget_and_late_penalty(self):
        # With a deliberately later departure, a feasible 180-km singleton arrives after 14:00.
        P = LLParams(depart_min=690, alpha=1)
        sol = solve_dc_day([[0, 90], [90, 0]], [0, 1], P, owned=1)
        self.assertEqual(sol["n12"], 1)
        self.assertEqual(sol["n14"], 1)
        self.assertEqual(sol["cost"]["late_cost"], 120000)
        strict = solve_dc_day([[0, 90], [90, 0]], [0, 1], P.with_(alpha=0), owned=1)
        self.assertEqual(strict["unserved"], [1])
        self.assertEqual(strict["unserved_reasons"][1], "noon_budget")
        self.assertEqual(strict["n12"], 0)
        self.assertEqual(strict["status"], "ok")  # u_i makes this a feasible penalized plan.

    def test_noon_budget_pass_eliminates_a_route(self):
        n = 51
        D = np.ones((n + 1, n + 1))
        np.fill_diagonal(D, 0)
        q = [0] + [0] * n
        hard = LLParams(noon_min=719, max_stops=30, alpha=0)
        soft = hard.with_(alpha=.02)
        a = solve_dc_day(D, q, hard, economic_stockouts=False)
        b = solve_dc_day(D, q, soft, economic_stockouts=False)
        self.assertLess(b["vehicles"], a["vehicles"])
        self.assertEqual(b["n12"], 1)
        self.assertEqual(validate(b, D, q, soft), [])

    def test_capped_frozen_accounting_chooses_lowest_cost_subset(self):
        # Equal loads but very different stop counts: shedding by load loses valuable coverage.
        P = LLParams()
        routes = [{"stores": [1, 2, 3], "load": 3, "km": 10, "n12": 0, "n14": 0},
                  {"stores": [4], "load": 3, "km": 20, "n12": 0, "n14": 0}]
        sol = {"status": "ok", "routes": routes, "unserved": [], "n_stores": 4, "budget": 0}
        original = copy.deepcopy(sol)
        capped = account(sol, P, owned=0, spot_cap=1)
        self.assertEqual(capped["unserved"], [4])
        self.assertEqual(capped["spot"], 1)
        self.assertFalse(capped["service_level_met"])
        self.assertEqual(sol, original)
        uncapped = account(sol, P, owned=2)
        self.assertEqual(uncapped["spot"], 0)
        self.assertEqual(uncapped["total"], 30 * P.c_fuel)

    def test_exact_small_instances_and_determinism(self):
        rng = np.random.default_rng(437)
        for _ in range(6):
            coords = rng.uniform(0, 8, (6, 2))
            D = np.linalg.norm(coords[:, None] - coords, axis=2)
            q = [0, 2, 1, 0, 3, 2]
            P = LLParams(capacity=6, max_stops=3)
            sol = solve_dc_day(D, q, P)
            self.assertEqual(sol, solve_dc_day(D, q, P))
            self.assertEqual(validate(sol, D, q, P), [])
            exact = exact_small_cost(D, q, P)
            self.assertGreaterEqual(sol["cost"]["total"] + 1e-5, exact)
            self.assertLess(sol["cost"]["total"] / exact, 1.1)

    def test_validator_detects_tampering(self):
        D, q, P = [[0, 1], [1, 0]], [0, 1], LLParams()
        sol = solve_dc_day(D, q, P, owned=1)
        sol["routes"][0]["arrivals"][0] += 10
        sol["routes"][0]["load"] = 0
        problems = validate(sol, D, q, P)
        self.assertIn("route-arrivals-summary", problems)
        self.assertIn("route-load-summary", problems)

    def test_or_opt_and_two_opt_star_improve_routes(self):
        coords = np.array([[0, 0], [1, 0], [2, 0], [1, 10], [2, 10]], dtype=float)
        D = np.linalg.norm(coords[:, None] - coords, axis=2).tolist()
        sv = _Solver(D, [0, 1, 1, 1, 1], LLParams(), None)
        sv.R = [[1, 3], [4, 2]]
        sv.S = [sv.ev(r) for r in sv.R]
        before = sum(s[0] for s in sv.S)
        self.assertTrue(sv.two_opt_star())
        self.assertLess(sum(s[0] for s in sv.S), before)
        sv.R = [[1, 4, 2, 3]]
        sv.S = [sv.ev(r) for r in sv.R]
        before = sv.S[0][0]
        self.assertTrue(sv.or_opt())
        self.assertLess(sv.S[0][0], before)


class FakeDistances:
    def __init__(self):
        self.calls = 0

    def submatrix(self, ids):
        self.calls += 1
        a = np.ones((len(ids), len(ids)))
        np.fill_diagonal(a, 0)
        return a


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.distances = FakeDistances()
        self.router = DailyRoutingSolver(self.distances)
        self.days = PlanningDays("D_dev", [date(2026, 1, 1), date(2026, 1, 2)],
                                 np.array([.25, .75]), ["B", "A"], np.array([[2, 0], [1, 3]]))

    def test_id_mapping_sorting_and_cached_matrices(self):
        first = self.router.solve_day("DC", ["B", "A"], self.days, owned=1)
        second = self.router.solve_day("DC", ["A", "B"], self.days, 1, owned=1)
        self.assertEqual(first["store_ids"], ["A", "B"])
        self.assertEqual(first["routes"][0]["load"], 2)
        self.assertEqual(second["routes"][0]["load"], 4)
        self.assertEqual(first["day_weight"], .25)
        self.assertEqual(self.distances.calls, 1)
        self.assertEqual(self.router.matrix_cache_hits, 1)
        json.dumps(first, allow_nan=False)

    def test_frozen_fleet_accounting_and_weighted_quantile(self):
        results = self.router.solve_days("DC", ["A", "B"], self.days)
        fleet = weighted_fleet_size([r["vehicles_needed"] for r in results], self.days.weights)
        calls = self.router.solve_calls
        priced = self.router.reprice(results[0], fleet)
        self.assertEqual(priced["routes"], results[0]["routes"])
        self.assertEqual(priced["spot"], 0)
        self.assertEqual(self.router.solve_calls, calls)
        self.assertEqual(weighted_fleet_size([1, 9], [.9, .1], 2 / 3), 1)
        self.assertEqual(weighted_fleet_size([1, 9], [.1, .9], 2 / 3), 9)
        capped = self.router.reprice(results[0], 0, spot_cap=0)
        self.assertEqual(set(capped["unserved"]), {"A", "B"})
        self.assertEqual(results[0]["unserved"], [])

    def test_empty_assignment_and_bad_day_inputs(self):
        empty = self.router.solve("DC", {})
        self.assertEqual(empty["costs"]["total"], 0)
        self.assertTrue(empty["service_level_met"])
        with self.assertRaisesRegex(ValueError, "A"):
            self.router.solve("DC", {"A": 49})
        with self.assertRaisesRegex(ValueError, "missing demand"):
            self.router.solve("DC", {}, store_ids=["A"])
        with self.assertRaises(ValueError):
            self.router.solve_day("DC", ["A", "A"], self.days)
        with self.assertRaises(ValueError):
            self.router.solve_day("DC", ["A"], self.days, 2)
        with self.assertRaises(ValueError):
            weighted_fleet_size([1], [0])


if __name__ == "__main__":
    unittest.main()
