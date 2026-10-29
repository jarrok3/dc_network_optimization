# Daily routing for one DC and one day

The solver uses the generated nationwide [store tables](../data/),
[saved planning days](../code/demand/planning/) and
[engine/detour distance cache](../code/cache/). It runs offline and never
regenerates tables or draws new demand. The formulation is
[Section 2a](../problem_definition/problem_definition.md), with the sizing and
frozen-accounting convention of Section 2b.

## Run an example

From the project directory:

```powershell
..\venv_midterm\Scripts\python.exe -m lower_level
..\venv_midterm\Scripts\python.exe -m lower_level --site SITE_00 --nearest-stores 280 --all-days
..\venv_midterm\Scripts\python.exe -m lower_level --site SITE_56 --assignment historical
..\venv_midterm\Scripts\python.exe run_demo.py --routing --site SITE_00 --nearest-stores 100
..\venv_midterm\Scripts\python.exe -m lower_level.benchmark
```

The default is a **diagnostic subset** of the 100 nearest individually eligible
active stores for SITE_00 on D_dev day 0. It is not a full network assignment.
`--assignment historical` uses the selected current DC's historical territory,
restricted to the planning population. `--assignment path/to/assignment.csv`
accepts unique `store_id,site_id` rows supplied by the upper-level assignment.
`--stores S0001 S0002` takes an explicit store set instead. All store IDs must
occur in the selected planning set.

Use `--day-set D7|D28|H14|D_dev`, `--day-index`, `--all-days`, `--owned`,
`--spot-cap` and `--output` as needed. `--help` lists path overrides. Default
output is [code/results/daily_routing.json](../code/results/daily_routing.json).
With `--all-days` and the default sizing settings, the report also gives the
weighted 2/3 fleet quantile and operating cost after frozen-route repricing.
DC and owned-vehicle monthly fixed costs remain upper-level calculations.

## Interface for upper-level evaluation

[api.py](./api.py) exports `DailyRoutingSolver`, `price_routes`,
`load_cost_params` and `weighted_fleet_size`. Import them from `lower_level`.
Create one router and reuse it across candidates; it loads the distance cache
once and keeps a bounded LRU cache of assignment-specific distance matrices.

```python
from lower_level import DailyRoutingSolver, weighted_fleet_size

router = DailyRoutingSolver.from_project()
days = router.load_days("D7")
assigned_ids = ["S0001", "S0002"]  # replace with the upper-level LP assignment

# Sizing pass: routes optimized with N=0 and no spot cap.
sizing = router.solve_days("SITE_00", assigned_ids, days)
fleet = weighted_fleet_size(
    [day["vehicles_needed"] for day in sizing], days.weights, quantile=2 / 3
)

# Account the same routes with owned vehicles; this makes no additional solves.
priced = [router.reprice(day, owned=fleet) for day in sizing]
expected_daily_operating = sum(
    weight * day["costs"]["total"]
    for weight, day in zip(days.weights, priced)
)

# Assignment eligibility can be checked before solving; no assignment LP is run here.
eligible = router.eligibility("SITE_00", assigned_ids)

# Direct interface for an observed day, including owned-fleet reoptimization:
observed = router.solve(
    "SITE_00", {"S0001": 2, "S0002": 0}, owned=4, day_id="observed-day"
)
```

For final comparison, rerun the sizing pass on **D28** and select the fleet
there. For held-out evaluation, solve H14 days using **default N=0**, then call
`reprice(day, owned=fleet_from_D28)`; never estimate a new fleet from H14.
Passing `owned=fleet_from_D28` directly to `solve_day` instead reoptimizes the
routes and is a separate sensitivity experiment, not the frozen-route policy.
No hidden solver-result cache is used: each call solves its supplied demand
afresh. Only distance matrices are reused. Inputs and saved day arrays are not
modified. Store IDs are sorted before solving for deterministic tie breaking.

For integration with another distance provider, instantiate
`DailyRoutingSolver(provider, params=LLParams(...))`; the provider needs a
`submatrix([site_id, *store_ids])` method returning a symmetric km matrix.
Optional `submatrix_with_sources` supplies distance provenance. The optional
`eligibility` helper also needs scalar `lookup(site_id, store_id)`.

### Result contract

Each solve returns a JSON-serializable dictionary. Route store fields contain
**store IDs**. The matrix-level legacy functions in [solver.py](./solver.py)
still use integer node indices (depot 0) and remain importable.

| Field | Meaning |
|---|---|
| `status` | `ok` for a feasible, possibly penalized plan; invalid input raises `ValueError` |
| `service_level_met` | True iff unserved plus after-noon stores fit the 2% assigned-store budget |
| `noon_budget_met` | The formulation's hard bound on delivered stops after noon, checked separately |
| `service_before_noon` | Before-noon deliveries / **all assigned stores**; unserved stores count as failures |
| `share_before_noon_of_delivered` | Companion share with delivered stores as denominator |
| `vehicles_needed`, `vehicles_run`, `spot` | Planned sizing fleet, routes actually run, spot trucks priced |
| `routes` | Ordered store IDs, depot-to-depot node sequence, load, km, arrival times, return time and lateness |
| `unserved`, `unserved_reasons` | Explicit stockouts, including route-limit, noon-budget, cap or cost reasons |
| `costs` | Fuel, spot, after-14:00, stockout, combined failure and total daily KRW |
| `distance_sources` | Engine/detour counts for actual driven legs, including depot legs |
| `distance_matrix_sources` | Provenance counts over all unordered pairs in the input matrix |
| `timing` | Matrix preparation, solve and total seconds; whether the matrix came from cache |
| `parameters`, `day_id`, `day_weight` | Parameters and common-day identifiers used for this result |
| `optimality_proven` | Always false: this is a heuristic |

`reprice` freezes route sequences and orders. A binding spot cap retains the
minimum-cost subset of those fixed routes; it cannot move stops to other routes.
Reprice the original sizing result when comparing fleets. A previously capped
result cannot restore routes it has already shed. Direct solving with a cap also
uses this subset step after construction and improvement; it does not solve a
separate capacitated-fleet VRP exactly.

## Model rules and heuristic

- Every assigned store requires a visit or an explicit unserved decision,
  **including stores with zero cage demand**. Fractional demand is accepted for
  the specification's average-day sensitivity. Negative/nonfinite demand and
  demand above 48 cages (or an overridden capacity) raise data errors.
- Load <=48, stops <=25, total route km <=250 and departure-to-return duration
  <=600 minutes are hard limits. Travel time is km/35 km/h; stop time is 15 min;
  all routes depart at 05:00. Arrival is measured before stop service. Exactly
  noon/14:00 is on time. Return travel and the final stop's service count toward
  route duration. Defaults and sensitivities use immutable `LLParams`.
- Clarke–Wright construction and 2-opt, Or-opt, relocate, swap, 2-opt* and route
  elimination maintain all limits and initially make every delivery before noon.
  A final elimination/reinsertion pass may spend `floor(0.02*n)` late-stop slots.
  Removal checks the source route too, because the mixed distance model need
  not satisfy the triangle inequality. Input matrices must be symmetric.
- Improvements use fuel and the **marginal spot cost at the supplied owned
  fleet**. A final exact selection over the fixed routes accounts for optional
  stockouts and a spot cap. Construction/local search do not prove optimality
  over all routes or all individual stockout combinations.
- The specified spot charge is 550,000 KRW, exceeding the 500,000 KRW stockout
  penalty for one store. A dedicated spot trip can therefore be more expensive
  than not delivering. The solver follows that objective and flags the resulting
  service failure; it does not silently increase the stockout penalty. The upper
  layer must enforce `service_level_met` for any network it recommends. For a
  diagnostic that serves every individually feasible store, the Python solve
  methods accept `economic_stockouts=False`; that is a different selection policy.
- Unreachable individual stores are penalized and reported. Even if too many
  singleton deliveries would miss noon, `u_i` permits a feasible stockout plan.
  Thus `status="ok"` does **not** imply compliance with the delivery requirement.
- Missing required cache distances and malformed IDs stop with explicit errors.
  The router uses current cached distances and calibrated long legs. It does
  not repair the generated geography or the previously observed snap problems.

## Validation

Run `python -m unittest discover -s tests -v` from the project directory.
[test_lower_level.py](../tests/test_lower_level.py) checks all route limits,
coverage of zero-demand stores, noon-budget route elimination, late penalties,
fleet accounting, cost-driven stockouts, independent result validation, ID
alignment, common-day weights and matrix reuse. It also compares six small
synthetic cases against exhaustive feasible-route enumeration/set partitioning;
these are correctness/quality checks, not a general accuracy guarantee.

The [development benchmark](./benchmark.py) solves all seven saved D_dev days
for 100/200/280 nearest eligible stores at one site and saves median/p95 solve
times, service, fleets and weighted operating costs to
[code/results/lower_level_benchmark.json](../code/results/lower_level_benchmark.json).
Initial cache loading and matrix construction are reported separately from solve
time. These subsets are development evidence, not the specification's full
100-instance exact-solver or alternative-heuristic accuracy studies.

Measured on the current inputs at SITE_00 (seven D_dev days per size):

| Assigned stores | Median solve seconds | p95 seconds | Owned fleet (weighted 2/3) | Minimum before-noon share |
|---|---:|---:|---:|---:|
| 100 | 0.088 | 0.092 | 7 | 100% |
| 200 | 0.300 | 0.349 | 13 | 98% |
| 280 | 0.598 | 0.688 | 19 | 98.21% |

All 21 solves served every assigned store. These timings are machine-dependent
and exclude initial data loading/matrix preparation. The complete test suite
passed all 24 tests after implementation.
