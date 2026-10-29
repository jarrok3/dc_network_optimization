# Strategic distribution-centre solver

This package implements Sections 2b/2c and 4.3 of the
[specification](../problem_definition/problem_definition.md) using the prepared
data and the [deterministic daily router](../lower_level/README.md). All distance
and demand operations are offline. It does not regenerate stores, query OSRM,
refit demand or draw new planning days.

## Current full-data result

The prepared instance has **1,970 active stores** and **60 candidate sites**.
The necessary store-count range is **8–19 DCs** with the current 100-store
minima and 200/280-store maxima. These bounds alone do not establish feasibility.

**No feasible network exists under the current eligibility rules:** stores
S0268, S0656, S0681, S0867, S1126, S1191, S1286 and S1899 have no eligible site.
For each, the closest cached candidate is SITE_33 at 132.0711 km one way,
making a single-store round trip **264.1422 km**, above the **250 km** limit.
These are the current cached distances; this code does not verify or repair
the earlier geography/snap issues.

The default command writes a feasibility report and a null recommendation,
then stops before routing/search or any held-out-data access. It never drops
these stores or relaxes limits implicitly. Input locations/candidates and their
cached distances must be corrected or extended before a national recommendation
can be produced. Merely increasing k cannot cover a store eligible for no site.

## Commands

From the project directory, with [requirements.txt](../requirements.txt) installed:

```powershell
..\venv_midterm\Scripts\python.exe -m higher_level
..\venv_midterm\Scripts\python.exe -m higher_level check
..\venv_midterm\Scripts\python.exe run_demo.py --network check
```

For a complete **development subset** exercise on current inputs:

```powershell
..\venv_midterm\Scripts\python.exe -m higher_level run --diagnostic-size 200 --budget 4 --pool-size 10 --mip-time-limit 3 --bootstrap-replicates 200
```

That explicitly uses the 200 nearest individually eligible active stores around
SITE_00, retains the current bounds and runs k=1,2. It is not a national result.
It evaluates both references, both selection rules, the D28 shortlist, H14 with
fixed fleets, paired savings, cost intervals and the average-day sensitivity.
The completed development run chose SITE_00 at about **116.77 M KRW/month**;
minimum H14 before-noon share was **98%**. Those figures apply only to that subset.

Evaluate a specified network without running the search:

```powershell
..\venv_midterm\Scripts\python.exe -m higher_level evaluate --diagnostic-size 200 --sites SITE_00 --day-set D28 --routes
..\venv_midterm\Scripts\python.exe -m higher_level evaluate --diagnostic-size 200 --sites SITE_00 --day-set H14 --fleet-json code/results/higher_level_diagnostic/evaluation_D28.json
```

`--fleet-json` accepts a saved evaluation containing `fleet`, or a site-to-count
JSON dictionary. H14 requires that fixed fleet; callers must supply the D28 fleet.
`--assignment-mode historical` uses stored historical assignment; use the current
site list for that reference. `reassigned_baseline` reoptimizes its assignment
without store-count bounds. All ordinary candidates use `optimized` and full
capacity/eligibility checks.

`run --help` lists seed, budget, pool size, MIP time limit, quantile, k restriction,
bootstrap and development options. `--min-stores`/`--max-stores` are explicit
sensitivity overrides and are recorded in scope metadata. Full-data output
defaults to [code/results/higher_level/](../code/results/higher_level/); diagnostic
output uses [code/results/higher_level_diagnostic/](../code/results/higher_level_diagnostic/).
Use `--output-dir` to keep separate experiments. Repeating a command updates its
output files; use the current `report.json` status as the authoritative result.

The old [upper_level/search.py](../upper_level/search.py) now forwards
`run_upper` to this package. [run_demo.py](../run_demo.py) supports `--network`
and routes `--build` to the strategic JSON workflow without regenerating data.
The existing browser page remains a static toy illustration; its UI is not a
viewer for these new reports.

## Python interface

```python
from higher_level import NetworkContext, NetworkEvaluator, SearchSettings, run_pipeline

ctx = NetworkContext.from_project()
check = ctx.preflight()

evaluator = NetworkEvaluator(ctx)
# Replace sites with a candidate that passes the assignment feasibility check.
sites = ["SITE_00", "SITE_01"]
assigned = ctx.assignment(sites)
if assigned["status"] == "ok":
    final = evaluator.evaluate(sites, "D28", quantile=2 / 3)
    if final["feasible"]:
        holdout = evaluator.evaluate(sites, "H14", fleet=final["fleet"])

# Full pipeline. Current national data returns status='infeasible_input'.
report = run_pipeline(ctx, "code/results/my_network_run", SearchSettings())
```

`NetworkContext` can also be constructed with arrays and an injected
`DailyRoutingSolver`/planning-day loader for small instances and tests. Distances
are **stores × candidate sites**; volume and ID vectors use the same store order.
`NetworkEvaluator.evaluate` returns explicit assignment infeasibility instead of
pretending a greedy failure proves infeasibility. Malformed inputs raise errors.

## Implemented calculation flow

1. Load active stores and posterior means, site costs/bounds, historical DC IDs,
   engine site–store distances and the saved common planning days. Validate IDs,
   shapes, finite values and integrality of capacity bounds. A global all-site
   coverage/capacity check rejects impossible input before expensive routing.
2. Evaluate the historical baseline and the same sites with re-solved assignment
   on D28. Both references are exempt from store-count bounds. The historical
   reference preserves its assignment, including any ineligible pairs; routing
   reports their stockouts/service failures. Reassignment requires eligibility.
3. Calibrate `lambda = baseline monthly operating / baseline cage-km per day`.
   Operating here is fuel + spot + failure, excluding DC and owned-vehicle fixed
   cost, matching the existing evaluator's convention. It is fixed thereafter.
4. Generate a G seed for each capacity-possible k using a capacitated p-median
   MIP. Site variables are binary; given them, the transportation assignment LP
   has integral vertices. Add volume exponents 0/.5/1, 0/30-km spacing variants,
   nearby one-site swaps and seeded random swaps, removing duplicates/infeasible
   sets. MIP bounds and gaps are logged. Timeouts without an incumbent are
   **unknown**, with a labelled greedy fallback; they are not infeasibility proofs.
5. Fit exact assignments by LP for every full evaluation. Solve each DC/day
   afresh at N=0. Derive the owned fleet with the day-weighted quantile and
   reprice the same routes with the fleet fixed. Costs are
   `DC fixed + vehicle monthly * owned + 30.4 * weighted daily operating`.
6. Run one fresh D7 search: one seed per represented k, then expected improvement
   under a Matérn-5/2 Gaussian process on `log(E/G)`, with six standardized features
   (k, log G, detour, capacity-forced share, max utilization, p90 road distance).
   The implementation uses the lognormal EI formula. Ties prefer lower k, then
   lexicographic site IDs. Gaussian-process hyperparameters are optimized from
   observed search evaluations under fixed bounds, not held-out results.
7. Run an independent random-selection control with the same initial seeds,
   pool, D7 arrays and evaluation budget. It also recomputes every routing solve.
   Only distance matrices are cached. If the feasible pool has fewer members
   than B, both runs use that same smaller effective budget and report exhaustion.
8. Re-evaluate the best found per k and top five overall on D28. Select the best
   service-feasible result. Infeasible/failed-service candidates retain their
   costs and diagnostics but cannot be recommended.
9. Only after saving that D28 decision, load H14. Evaluate the chosen design and
   both references with their respective D28 fleets fixed. Report paired daily
   savings and component/total intervals. A held-out service failure is reported
   as `holdout_service_failure`; H14 never selects a replacement network.
10. Evaluate the selected design on one posterior-mean day as the average-day
    sensitivity. Fleets are sized on that single day, so this intentionally
    compares the average-day approximation with sampled-day fleet sizing.

The five monthly components, daily totals, fixed fleets, service shares,
unserved counts, assignment and routing solve counts are available in each
evaluation. `retain_routes=True` includes the ordered lower-level routes.
`feasible` requires the chain to meet 98% **on every sampled day**, plus the
lower solver's conservative per-DC service flags. Undelivered stores count as
failures. A computed `status='ok'` alone is not service feasibility.

### Optional development benchmark and remedy

`run --development-replays 100` first evaluates the entire pool on D_dev, then
replays guided/random selection 100 times from permuted seed orders. It reports
proxy rank correlation/error, loss of the G-best service-feasible member and
regret relative to the best service-feasible pool member. This can be expensive;
it is not performed by default and the report says so explicitly.

When rank correlation is below .8 or G-best loss exceeds 2%, the Section 4.5
remedy adds a monthly fleet proxy based on volume, stop count and approximate
time available before noon. Development metrics/replays are repeated using the
same D_dev E values. Corrected-G seeds are the best corrected scores per k in
the generated pool; original MIP certificates continue to refer to the original
G objective. This distinction is saved in the pool metadata. The reported D7
search then uses the frozen corrected proxy/seeds and fresh routing solves.
The correction is applied once; remaining threshold failures stay visible.

## Reports and reproducibility

| Output | Contents |
|---|---|
| `feasibility.json` | Uncovered store IDs, nearest cached site/distance, capacity k range |
| `report.json` | Authoritative run status, scope, settings, provenance, all summaries |
| `baseline_D28.json`, `baseline_reassigned_D28.json` | Both reference evaluations |
| `pool.json` | Distinct candidates, G/features, seed certificates and generator settings |
| `logs/seeds.json` | Frozen seed IDs, written before the first reported D7 evaluation |
| `logs/search_<seed>.jsonl`, `logs/control_<seed>.jsonl` | One line per fresh evaluation, phase, G/E, components, GP/EI, dates/weights, solves and runtime |
| `logs/*_evaluations/` | Full individual search evaluations |
| `sweep.json` | Best service-feasible evaluated design per k, or explicit failure |
| `recommended.json` | D28 design, store-to-site mapping, owned fleets, components; null if no decision is possible |
| `holdout.json` | H14 comparison with fleets fixed |
| `development*.json` | Optional benchmark, replays and correction evidence |

Random streams use the project's named `SeedSequence` branches. The saved
planning arrays are reused by every candidate and reference. Input and used
day-array hashes are recorded. The fixed pool and deterministic router make the
search reproducible; time-limited MIP incumbents may vary with solver version,
machine and time limit, so their gaps/certificates are recorded rather than
claiming bit-for-bit reproducibility across machines.

Intervals use a weighted empirical paired-day bootstrap: sample days with their
saved probabilities and average the sampled values without applying the weights
again. It conditions on the fitted models and fixed fleets and does not preserve
stratum quotas or capture road-model, parameter or heuristic uncertainty. No
global optimality is claimed for routing or strategic E. Development evidence and
the diagnostic subset are not reported as a full national experiment.

## Tests

Run `python -m unittest discover -s tests -v`. The suite includes exact LP and
MIP checks against enumeration, eligibility/capacity conflicts, accounting,
weighted fleet sizing, service rejection, GP/EI numerical checks, fresh routing
counts for guided and control runs, correction-cache isolation and an end-to-end
test asserting that the D28 recommendation exists before H14 is loaded.
