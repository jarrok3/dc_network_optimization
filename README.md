# Distribution Center Network Redesign and optimization

In the idea of data-driven optimization this repository focuses on delivering a coherent solution to the problem of fundamental distribution center redesign satisfying cost optimization. The problem is hypothetical as are the presented solutions.

# Deliverables, what to expect in this repo

The contents of this repository consist of certain deliverables described in the initial assignment description, which are:

- the problem description file,
- the suggested solution presentation for stakeholers,
- the demo of a plausible solution.

## Nationwide preprocessing (Sections 3.2 and 3.4)

Run the following commands from this directory using the project virtual
environment. Install [requirements.txt](./requirements.txt) first if needed.
All input/output defaults are anchored to this directory, so scripts also work
when called by absolute path from another working directory.

```powershell
..\venv_midterm\Scripts\python.exe -m distances.calibrate
..\venv_midterm\Scripts\python.exe demand_model.py
..\venv_midterm\Scripts\python.exe planning_days.py
..\venv_midterm\Scripts\python.exe -m unittest discover -s tests -v
```

Calibration requires the local OSRM server at `http://localhost:5000`.
Fitting, planning-day generation, lookup and tests run offline. The generated
tables already in [data/](./data/) are reused; no regeneration is necessary.
To create new tables, run [generator/generate.py](./generator/generate.py).

| File/folder | Role |
|---|---|
| [data/](./data/) | Nationwide store/site tables and generated demand history |
| [distances/build_cache.py](./distances/build_cache.py) | Existing OSRM cache builder: site–store pairs and store–store pairs within 60 km |
| [distances/calibrate.py](./distances/calibrate.py) | New calibration CLI, sampling, validation and detour model |
| [distances/lookup.py](./distances/lookup.py) | New offline engine/detour lookup and routing submatrices |
| [demand_model.py](./demand_model.py) | New calendar regression, empirical-Bayes priors, posterior fitting/loading |
| [planning_days.py](./planning_days.py) | New weighted D_dev / D7 / D28 / H14 generation |
| [calendar_effects.py](./calendar_effects.py) | Shared holiday labels and season definitions |
| [project.py](./project.py) | Nationwide paths and named independent random streams |
| [tests/test_preprocessing.py](./tests/test_preprocessing.py) | Offline statistical and integration checks |
| [code/cache/](./code/cache/) | Existing Parquet cache and new calibration outputs |
| [code/demand/](./code/demand/) | Fitted model, posteriors and planning arrays |

[config.py](./config.py), [distances/compute.py](./distances/compute.py),
[upper_level/search.py](./upper_level/search.py) and [run_demo.py](./run_demo.py)
belong to an older toy workflow. Its generator loader imports are incomplete;
it is not an executable nationwide optimizer. The files under [app/](./app/)
also reference missing assignment/table modules. This change provides executable
preprocessing and explicit interfaces for a future nationwide evaluator; it
does not implement the full search or repair the old demo's build pipeline.
Daily routing now has an executable nationwide API and CLI: see
[lower_level/README.md](./lower_level/README.md). For a standalone example run
`python -m lower_level`, or `python run_demo.py --routing`; these commands use
the existing tables, saved planning days and distance cache.

### 1. Detour calibration

```powershell
..\venv_midterm\Scripts\python.exe -m distances.calibrate --osrm-url http://localhost:5000 --sample-size 1000 --seed 437
```

The script draws unique unordered store pairs across map-distance bands
0–10, 10–30, 30–60, 60–150 and 150+ km and regions. Rural takes precedence
when either endpoint is rural; capital means both endpoints are in the capital
region; the other pairs use the other-metropolitan category. An approximate
opposite-bank Han River screen gets a separate sampling quota. It screens
endpoint locations; it does not verify which bridge an OSRM route uses.

Each OSRM table request retrieves both directions. Their mean implements the
specification's symmetric-distance assumption. Unreachable, zero-distance and
over-500-m snap pairs are logged and replaced from a deterministic reserve.
Replacements prefer the same stratum; exhausted rare strata redistribute.
The sample therefore represents road-connected, adequately snapped endpoints.
The current synthetic generator does not enforce road snapping; rejected
points and the existing distance cache should be revisited before a final
network recommendation. Calibration does not alter those input coordinates
or rebuild the existing cache.

The output is [code/cache/detour_model.json](./code/cache/detour_model.json),
with arithmetic mean road/haversine ratios per band and region, band/global
fallbacks fitted on training observations only, and 30% held-out MAPE, signed
percentage bias and mean absolute error. The split has exactly 700 training
and 300 validation observations for a 1,000-pair run. The sample, snap distances,
split labels and rejected-query audit are saved next to the model. The JSON
also records input hashes, seed, OSRM URL, sample count and ratio quantiles.
Existing OSRM extract/version metadata remains in the original cache metadata.

Rerunning resumes completed pairs when input hash and query settings match.
Remove/move the sample and its metadata to request a fresh engine run, for
example after changing the OSRM extract. `--timeout`, `--max-snap-m`, `--stores`
and `--output-dir` are configurable. Failed connection attempts stop promptly
and save progress. No haversine substitute is presented as an engine result.

The offline lookup reads the existing Parquet cache first. A missing long
store–store pair uses the calibrated estimate; a missing short pair or
site–store pair raises an explicit error. It never contacts OSRM.

```python
from distances.lookup import RoadDistances

distances = RoadDistances()
km, source = distances.lookup("S0001", "S0002")  # source: engine or detour
matrix = distances.submatrix(["SITE_00", "S0001", "S0002"])
```

### 2. Demand model and common planning days

The fitter uses stores with at least 300 observed days for a Poisson regression
with store fixed effects, weekday-by-type effects, monthly seasonality and
ordinary/pre-holiday/holiday effects. Block-coordinate maximum likelihood
estimates the factors; their product is normalized to calendar mean one for
each store type. It never imports the generator's multiplier values.

Type-specific Gamma prior means and strengths use method of moments on
long-history rates, subtracting Poisson sampling variance. Posterior shape is
`a_g + sum(cages)` and posterior rate is `b_g + sum(calendar exposure)` over
observed open days. Short-history stores shrink toward their type mean; zero-
history stores retain the type prior. All open dates, including zero-demand
days, must be present. Duplicates, unknown IDs, negative/fractional counts and
records outside store lifetimes fail validation. Closed stores contribute to
estimation but are excluded from the planning population, which is frozen at
the history end date.

The adjusted Pearson dispersion check uses long-history residuals after calendar
and store effects. Above the configurable threshold (default 1.2), an NB2
likelihood with method-of-moments overdispersion and the Gamma prior is integrated
numerically on a log-rate grid. This is an approximate nonconjugate posterior,
and is explicitly identified in metadata; it is not called a Gamma posterior.
Calendar and prior estimates use the Poisson fit as a working fit in that branch.

The default five holiday dates match the **existing synthetic generator**, not
an official Korean public-holiday calendar. To replace them, provide a CSV with
a unique ISO `date` column using `demand_model.py --holidays path/to/holidays.csv`.
The same labels, including pre-holidays, are then persisted for prediction.
For prior-strength sensitivity, use `--prior-strength 0.5` or `2` with a
separate output directory; do not replace the reported model inadvertently.

The model saves calendar effects, priors, dispersion diagnostics and input hashes
to [code/demand/model.json](./code/demand/model.json), per-store posteriors to
[code/demand/posterior.csv](./code/demand/posterior.csv), and reloadable arrays.

```python
from demand_model import DemandModel, PlanningDays
from project import DEMAND_DIR

model = DemandModel.load()
mean_cages = model.posterior_mean()[model.active_idx]  # assignment / cheap proxy
days = PlanningDays.load(DEMAND_DIR / "planning" / "D7.npz")
# days.store_ids: stable column order, days.demand: (7, number_of_active_stores)
# days.dates: labels, days.weights: positive and sum to one
```

The planning script creates all four sets once: D_dev (7), D7 (7), D28 (28),
H14 (14). Each set and its date/demand draws has its own spawned RNG stream.
Each store gets a fresh posterior rate and predictive count per sampled day.
All candidates and baselines must reuse these saved arrays, indexed by store ID.
Use D_dev only for development, D7 for search, D28 for fleet sizing/final ranking,
and H14 for the final comparison with the fleets fixed from D28. These
preprocessing modules do not themselves run candidate search or fleet sizing.

Dates come from a representative year beginning at the history start by default,
so observed holiday labels remain applicable. They are calendar scenarios, not
future observed orders. A different representative year can be supplied with
`planning_days.py --calendar-start YYYY-MM-DD`; supply matching holidays when
fitting if that year needs different holiday dates.

**Small-set stratification:** the full weekday/weekend × season × holiday-class
cross-product has more than seven populated cells. Rare cells with fewer than
four dates are merged to support four disjoint sets. For seven-day sets, the
smallest cells are further merged, preferring the same season and day class.
Dates are drawn uniformly without replacement within each resulting group.
Weight is the group's original annual share divided by its sampled-day count.
The manifest records every merged cell and weight. This represents the entire
calendar with explicit coarsening; a seven-day set does not guarantee a holiday
or all original cells. Previously used dates are excluded across sets. Thus RNG
streams and predictive shocks are independent, while the required disjointness
introduces dependence between calendar selections; literal full independence
and guaranteed disjointness on the same finite calendar cannot both hold.

[code/demand/planning/manifest.json](./code/demand/planning/manifest.json)
records these choices, seeds and the model fingerprint. Each set has a compact
NPZ file plus human-readable date/weight and demand CSVs. No demand draws occur
inside candidate evaluation. Repeating the commands with the same inputs and
seed reproduces the fitted parameters, selected dates and demand arrays.

### Validation on the current generated inputs (2026-10-29)

The live OSRM calibration obtained 1,000 valid unique pairs after rejecting
925 attempted pairs (including snap failures). All retained endpoints snapped
within 500 m. On the 300 validation pairs, MAPE was 12.27% and mean signed
percentage error was +4.11%. On the **90 validation pairs beyond 60 km**, the
part of the model actually used for estimated routes, MAPE was **5.84%**, bias
was **+0.72%**, and MAE was **10.83 km**. These are stratified-sample metrics,
not estimates weighted to the chain's actual routed-leg frequencies.

The fitter read 714,255 observations for 2,000 stores and converged in nine
calendar iterations. Adjusted Pearson dispersion was **0.9961**, retaining
the Poisson likelihood. The four sets contain demand for **1,970 active
stores**, **56 distinct dates**, and normalized weights. The automated suite
checks posterior arithmetic, estimated calendar patterns, normalization,
short/zero-history treatment, negative-binomial fallback, model serialization,
planning reproducibility, disjointness, engine units/symmetry, rejected snaps,
calibration replacement/resume, validation separation and offline lookup.
