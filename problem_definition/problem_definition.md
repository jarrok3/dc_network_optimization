# Problem Definition: Distribution-Centre Network Redesign for Dasol Retail

**IE437 Midterm Project · Distribution-Centre Network Redesign — Formulating and Solving a Hierarchical Decision**

Author / student ID: [entry]  
Date / version: 2026-10-27 · v0.1 (Sections 1–2 drafted before any code; Sections 3–7 pending)  
Summary: TBD — to be written after the experiments (recommended number of DCs, estimated saving and its uncertainty, the one simplification that matters most).  
Files: `problem-definition.md`, `code/`, `demo.html` or the local app in `code/`, `proposal.html`  
LLM tools and use: Claude (Anthropic, Sonnet 5.5), 2026-10-27 — structuring the problem (boundary, decisions, dependencies, classification) and drafting Sections 1–2. Assessments of its suggestions are recorded in Section 7 (pending).

Marking: `[ASSUMPTION]` = assumption; `TBD` = unresolved fact (carried to the Section 3 table); `N/A — reason` = inapplicable.

## 1. Decision, Scope & Problem Structure

**Business need.** Dasol Retail (fictional) serves about 10,000 convenience stores from 12 distribution centres (DCs). The store-to-DC assignment was fixed long ago, and several DC leases expire in sequence over the coming years. Management wants to know how many DCs to run and where, and what evidence supports the answer.

> Strategy & Planning, with management approval, uses the posterior predictive of store demand, road distances, candidate sites and cost parameters, once now (before the leases expire), to decide how many DCs to operate, at which sites, which stores each serves and under what fleet policy, in order to minimise total monthly cost (DC fixed cost, vehicles, fuel, spot trucks, service failures) while at least 98% of a day's deliveries arrive before 12:00.

### The four statements

| Statement | Level | Decision or component it concerns | Consequence for comparison |
|---|---|---|---|
| Strategy & Planning: "Cut the DCs from twelve to eight and lease and staff costs fall sharply." | **Evaluation of a decision** (one term only) | `site-selection` at k = 8; component `dc-fixed-cost` | True by construction for one cost term (fixed cost is linear in the number of open sites). It says nothing about total cost, so it cannot be compared with anything until the other terms are added. Same unit as Delivery Operations: KRW per month. |
| Delivery Operations: "Fewer DCs means longer routes, more vehicles; it may end up costing more." | **Evaluation of a decision** (the complementary terms), stated as a hypothesis about sign and size | `site-selection` → `store-assignment` → `daily-routing`; components `vehicle-fixed-cost`, `fuel-cost`, `spot-hire-cost`, `failure-cost` | Directly commensurable with Strategy & Planning: both are terms of the same total monthly cost `E(k)`. Neither claim is decided alone; only their sum over k (the sweep) answers whether the saving is outweighed. |
| Data Team: "The volume-weighted centre of gravity is the optimal site; it takes minutes." | **Method for taking a decision** (candidate generation, the cheap criterion `G`) carrying an unverified validity claim ("optimal") | `site-selection`, via `G` | A method is judged by what the full evaluation `E` says about its output, not by its own score: prediction quality (how well `G` predicts `E`) and decision quality (how much the `G`-best candidate loses under `E`). It is compared with other generators and selection rules at the same budget, not with the cost claims. It does not choose k, and ignores capacity bounds, assignment, vehicle limits and the window. |
| Field Manager: "The centre of gravity may land across the river; trucks drive to the bridge." | **Claim about the validity of a model** (the distance component) | Road distances `l_uv` feeding `fuel-cost`, `route-limits`, `noon-share`, `pair-eligibility`, `assignment-proxy` and `G` | Not a cost and not a decision, so it has no common scale with the other three. It is a validity condition on all of them: tested by the calibration error of distances against road values and by the `G`–`E` gap. It also supports restricting sites to leasable candidates and computing `G` on road rather than map distance. |

**Consequence.** Two statements (Strategy & Planning, Delivery Operations) are evaluations on one scale (KRW per month) and combine into `E(k)`. The Data Team's statement is a method, ranked only through `E`. The Field Manager's statement is a validity claim about an input of all the others and is checked, not summed.

### Boundary

- **One decision problem (upper):** one network redesign chosen now for a steady-state planning horizon. Every current site can be kept or given up and every candidate site can be leased; the 12 current sites are themselves candidates. Demand is represented by sampled days from the posterior predictive (Section 3).
- **One decision problem (lower):** one DC's delivery plan for one day, solved after that day's orders are known (orders by the evening cutoff, routes planned the evening before).
- **Excluded, with consequences:**
  - The sequence of lease expiries (an extension). The result is a one-shot design and ignores transition cost and ordering. A sequential version would be Stateful.
  - The fresh-food second wave (out of scope per brief). Fleet need and kilometres are understated if it shares vehicles.
  - Multiple trips per vehicle, driver breaks, time-of-day traffic, dock congestion, stock or backlog carried between days (details in 2a).
  - Opening and closing of stores during the horizon: the store set is fixed.
  - Transition cost of closing or opening a DC. The recommendation is not a migration plan.
- **Current practice versus constructed baseline.** The company's actual practice (12 DCs, historical assignment, experience-based routes) is unknown here; no real data exist (`TBD`). The **baseline** is constructed: the 12 generated current sites with the generated historical assignment, scored by the same `E`, the same sampled days and the same routing method as every candidate. The baseline is exempt from the capacity bounds. A second reference, the same 12 sites with the assignment re-solved, separates the saving from re-assignment from the saving from changing the network.

### Decisions and dependencies

| Decision ID | What is chosen | Level | Approved by | How it is handled in the prototype |
|---|---|---|---|---|
| `site-selection` | Which candidate sites are open: set `S`, with `k = card(S)` | Upper | Management (proposed by Strategy & Planning) | Searched under budget `B` |
| `store-assignment` | Which open DC serves each store | Upper | Management | Re-solved inside every evaluation as a capacitated assignment LP (rationale in 2b) |
| `fleet-policy` | The quantile `q` of daily vehicle need that fixes owned vehicles per DC | Upper (candidate attribute) | Management / Delivery Operations | `q = 2/3` default; owned fleet `N_j` is derived by the rule `fleet-rule` |
| `daily-routing` | For one DC and one day: vehicles used and the store sequence of each | Lower | DC planner (in reality; the prototype computes it) | Heuristic solve (method in Section 4) |

Not decisions (supporting analyses): the demand model and its posterior predictive, distance lookup and its calibration, the cheap criterion `G`, the sweep over k, the surrogate and acquisition rule, and the sampled-day draws. Prediction and simulation alone do not decide anything; the decision they support is `site-selection`.

**Dependencies** (`parent → child: what changes`):

```text
site-selection → store-assignment : feasible-region (stores may go only to open sites),
                                    parameter (per-site min/max store bounds)
store-assignment → daily-routing  : feasible-region (the store set of DC j),
                                    parameter (n_j, demand vector, distance sub-matrix, the 2% noon budget)
fleet-policy → daily-routing      : parameter (owned fleet N_j = threshold above which spot trucks are paid),
                                    available-action (vehicles free of charge)
site-selection — fleet-policy     : no directed dependency (chosen together in one candidate, same level)
```

**Evaluation relations** (written separately from dependencies):

```yaml
- id: dc-fixed-cost
  decision: site-selection
  evaluated_by: none          # closed form
- id: vehicle-fixed-cost, fuel-cost, spot-hire-cost, failure-cost
  decision: site-selection, fleet-policy   # cost of the candidate (S, q)
  evaluated_by: daily-routing # run on every DC in S over the sampled days
- id: assignment-proxy
  decision: store-assignment
  evaluated_by: daily-routing # the distance proxy is judged by the routes it produces
```

The parent changes the child's problem (dependencies); the child supplies an evaluation of the parent (`evaluated_by`). Receiving that evaluation is not by itself Feedback.

### Classification

| Object | Property | Classification (or unresolved status) | Structural evidence or question to resolve |
|---|---|---|---|
| Decision dependency graph | Decision Structure | **Hierarchical** | Three directed edges, each changing the child's feasible region, parameter or available action (see Dependencies). Multiple decisions alone would not make it hierarchical; the edges do. |
| `site-selection` | State Structure | Stateless | One design chosen now; no state carried between decision epochs; no State-transition component. The lease sequence is excluded; including it would add a state (open DCs, remaining leases). Sampled demand days are draws of a parameter, not a state. |
| `site-selection` | Information Structure | Open-loop | Chosen once from information available now. Basis: `operational-requirement` (leases fix the network for years). The surrogate search reads the results of its own earlier evaluations; this is the algorithm using its logged evaluations, not newly observed future information. |
| `store-assignment` | State Structure | Stateless | One assignment for the horizon; no transition. |
| `store-assignment` | Information Structure | Open-loop | Fixed long-term (the existing assignment has barely changed). Basis: `operational-requirement`. Daily re-assignment of stores between DCs is excluded (`design-decision`). |
| `fleet-policy` | State Structure | Stateless | One quantile policy; no transition. |
| `fleet-policy` | Information Structure | Open-loop | Owned fleet is fixed before any day's demand is known. Spot hiring is the daily reaction and belongs to `daily-routing`. Basis: `operational-requirement`. |
| `daily-routing` | State Structure | Stateless `[ASSUMPTION]` | Load and arrival-time recursions are relations inside one plan, not transitions between decision epochs. Assumes no carry-over of unmet demand, stock or vehicle availability between days. Confirm with Delivery Operations (`TBD`, Section 3); if carry-over matters, this becomes Stateful. |
| `daily-routing` | Information Structure | Open-loop | The day's orders are known when routes are planned; travel times are deterministic in the model; the sequence is fixed before departure. Basis: `operational-requirement` (routes planned the evening before). In-day re-routing is excluded (`design-decision`); it would be Feedback. |

**Information timing across levels.** The upper decisions know only the distribution of demand (posterior predictive). Demand for day d is revealed to the lower level when its problem begins. This is a difference in what each problem knows, not Feedback inside either. `[ASSUMPTION]` If the boundary were redrawn as one problem "network plus a policy for daily reaction to demand", routing would be a recourse and the whole problem would be Feedback; the two-problem boundary above is a `design-decision`, chosen because the two decisions are taken by different people at different times.

**Execution is separate.** In reality `daily-routing` runs every evening; in the prototype it runs on sampled days. Daily re-execution of an independent problem does not make it Feedback, and the number of solver calls is not a state.

## 2. Computable Problem

### 2a · The lower level — one DC's daily operation

**Given** (for DC `j` and day `d`): the stores `I` assigned to `j` (`n = card(I)`); demand `q_i` in roll cages for each store on that day (a sampled day); road distance `l_uv` (km) between nodes (node 0 is the DC), symmetric `[ASSUMPTION]`; travel time `t_uv = 60·l_uv / v` minutes with `v = 35 km/h`; stop time `s = 15 min`; departure `τ0 = 05:00` for all vehicles `[ASSUMPTION]` (the brief allows 04:00–07:00; `TBD` with Delivery Operations); owned fleet `N` (a parameter passed down; `N = 0` in the sizing pass, see 2b); limits `Q = 48` cages, `M = 25` stops, `L = 250` km, route duration `T = 10 h` `[ASSUMPTION]` (loading treated as outside this duration; if loading counts inside the 10-h shift, `T` falls to about 9 h; `TBD`); noon budget `α = 2%`; rates `c_f = 350` KRW/km, `c_s = 550,000` KRW per spot truck-day, `p_14 = 120,000` KRW per delivery after 14:00, `p_so = 500,000` KRW per undelivered store; optional spot-truck availability cap `κ` (base run: no cap, `TBD`).

**Route set.** `R` is the set of feasible routes `r = (0, i_1, …, i_m, 0)`, each satisfying `Σ q_i ≤ Q`, `m ≤ M`, `len(r) ≤ L`, `duration(r) ≤ T`. Arrival at the h-th stop is `a_{i_h} = τ0 + t_{0,i_1} + Σ_{g<h} (s + t_{i_g,i_{g+1}})` (minutes since midnight). Let `n12(r)` and `n14(r)` count stops with `a > 12:00` and `a > 14:00`.

**Decided.** `λ_r ∈ {0,1}` (route `r` is driven; one vehicle each), `u_i ∈ {0,1}` (store `i` not delivered), `σ ≥ 0` (spot trucks hired). The vehicles used are `V = Σ_r λ_r`.

**Objective** (minimise daily operating cost, KRW):

```text
min  c_f · Σ_r len(r) λ_r  +  c_s · σ  +  p_14 · Σ_r n14(r) λ_r  +  p_so · Σ_i u_i
s.t. Σ_{r ∋ i} λ_r + u_i = 1          for every store i       (served once or stocked out)
     σ ≥ Σ_r λ_r − N,   σ ≥ 0          (and σ ≤ κ if a cap applies)
     Σ_r n12(r) λ_r ≤ floor(α · n)     (at most 2% of the DC's stores arrive after 12:00)
     λ_r, u_i ∈ {0,1}
```

This is a set-partitioning vehicle-routing problem with side constraints (capacity, stops, distance, duration and a time-window budget). It is combinatorial (NP-hard), so the solution method is a heuristic chosen in Section 4. Cost per solve and measured accuracy: `TBD` (Section 4).

**Modelled and omitted.**

| Feature | Treatment | Reason / consequence |
|---|---|---|
| Load, stops, distance, duration limits | Modelled (hard) | Core of the brief. With τ0 = 05:00 the clock (7 h to noon, 15 min per stop) binds before the 25-stop limit; which limit binds is itself a result to report. |
| Stop time and constant road speed | Modelled (15 min, 35 km/h) | Brief's defaults; 30–45 km/h tested as sensitivity. |
| Single vehicle type, one trip per vehicle | Modelled | A second trip would lower vehicles needed in dense territories; fleet need is overstated there. Omitted for tractability. |
| Driver breaks | Omitted | Would shorten feasible routes (understates vehicles). Not in the brief. |
| Time-of-day traffic | Omitted | Peak slowdowns flatter long routes; speed sensitivity partly covers it. |
| Loading-dock congestion, staggered departures | Omitted (single departure time) | Staggering would let long routes leave earlier; omission is conservative for them. |
| Empty-cage collection | Omitted | No load or time effect assumed. |
| Fresh-food second wave | Out of scope | Per brief. |
| Carry-over between days | Omitted `[ASSUMPTION]` | Makes `daily-routing` Stateless; to confirm. |

**How the delivery window enters.** Hybrid, stated explicitly. The 98%-before-noon requirement is a **hard side constraint per DC-day** (`α = 2%`), which is a conservative sufficient condition for the chain-wide 98% (`TBD`: whether the company means per DC or chain-wide). Delivery after 14:00 and non-delivery are **penalised** (soft, 120,000 and 500,000 KRW). The realised chain-level share before noon is reported for every network. Because the noon constraint is on the routes, a larger territory needs more vehicles, which is the mechanism behind Delivery Operations' claim.

**One solve returns upward** (per DC-day): vehicles needed `V`, kilometres, stops per route, `n12`, `n14`, undelivered stores `u`, spot trucks `σ`, and the operating cost.

### 2b · The connection

**Passes down:** the open set `S`, the fleet policy `q`, and the sampled demand days `D` (posterior-predictive draws, shared across all candidates and the baseline as common random numbers, so differences are not sampling noise). The assignment `A_j` is derived inside the evaluation from `S`.

**Passes up:** per DC-day `V`, km, `n12`, `n14`, `u`, `σ`; aggregated into expected monthly operating cost, owned vehicles and realised service level.

**One evaluation `E(S, q)`:**

1. Solve the assignment LP for `S` (reject `S` with the reason if infeasible).
2. **Sizing pass:** for each `j ∈ S` and `d ∈ D`, solve the lower level with `N = 0` (every vehicle priced as a spot truck, so vehicles are minimised first) and record `V_{j,d}`.
3. `N_j = ceil(Q_q({V_{j,d}}))`, the `q`-quantile of daily need (`fleet-rule`; `q = 2/3` default).
4. **Accounting:** spot trucks `σ_{j,d} = max(0, V_{j,d} − N_j)`, with the routes of step 2 frozen `[ASSUMPTION]`. This ignores that free owned vehicles could shorten routes, so cost is slightly overstated; to be checked on a small instance (Section 4).
5. `E = F(S) + c_v·ΣN_j + D_m · mean_d Σ_j [c_f·km + c_s·σ + p_14·n14 + p_so·u]`, with `D_m = 30.4` days per month `[ASSUMPTION]`.

**Cost of one evaluation:** `k · card(D)` lower-level solves plus one assignment LP. With 7 sampled days, k = 8 gives 56 solves. With 28 days, 224; the 14 held-out days add 112; the baseline costs 12 × (28 + 14) = 504 solves. The cost per solve is `TBD` (Section 4).

**Where demand uncertainty enters.** Only through `D`, in the lower level: fleet size (an order statistic) and spot hiring are not linear in demand, so an average day would understate both. Sampled days include a chain-wide calendar draw, so costs are correlated across DCs. **Averaged away (simplification):** `store-assignment` and `G` use posterior-mean volumes `w_i`; this goes in the Section 4 table. The fleet is sized and scored on the same sampled days, which is optimistic for spot cost; the held-out days measure the effect.

**Place of the assignment.** It is a decision (`store-assignment`) but **not part of the searched candidate**; it is re-solved inside each evaluation. Reasons: (i) for fixed `S` it is a transportation problem with bounds (an LP with integral vertices), exact and fast; (ii) putting about 2,000 store-to-DC choices into the candidate would give a search space a few dozen evaluations cannot learn; (iii) every candidate then gets a consistent, reproducible assignment. Cost: the LP minimises a distance proxy (`assignment-proxy`), not route cost, so compact routing and the window can be missed; this feeds the `G`–`E` gap and is a candidate check for Section 4. The baseline keeps its historical assignment (a fact of the operation), hence the second reference with re-solved assignment.

### 2c · The upper level — how many DCs, and where

**Variables and domains.** `y_j ∈ {0,1}` for each candidate site `j ∈ C` (`card(C)` about 40–80, including the 12 current sites); `k = Σ y_j`; `x_ij ∈ {0,1}` (store `i` served by `j`; derived by the LP, not searched); `q` (fleet quantile, base value 2/3). A **candidate** is `(S, q)`.

**Objective and constraints:**

```text
min over (y, q):  E(y, q) = Σ_j f_j y_j  +  c_v Σ_j N_j(y, q)  +  D_m · E_d [ Σ_j Φ_j(A_j(y), d, N_j) ]
                  where Φ_j is the optimal value of the lower level (2a) for DC j on day d

s.t. Σ_j x_ij = 1                         every store assigned once
     x_ij ≤ y_j,  x_ij ≤ elig_ij          only open sites; eligibility (a single-store route meets noon and the km/duration limits)
     min_j · y_j ≤ Σ_i x_ij ≤ max_j · y_j  store-count bounds per open DC (new designs only; baseline exempt)
     realised share of deliveries before 12:00 ≥ 98%   (implied by the per-DC-day noon budget; reported)
```

**Feasible range of k.** With `n` stores and the default bounds (500–1,400 stores per DC, scaled in proportion if thinned), `ceil(n/1,400) ≤ k ≤ floor(n/500)`. For n = 10,000 (and for 2,000 with bounds 100–280) this gives **8 ≤ k ≤ 20**. So Strategy & Planning's proposal of 8 is the smallest feasible network under these bounds, with every DC near its maximum. Site-specific `max_stores` can raise the minimum. The sweep covers this range; k = 8 is the natural boundary test case.

**Convex or linear, and combinatorial.**

- *Linear:* the fixed-cost term; the assignment for fixed `S` (transportation LP, exact); the LP relaxation of the capacitated p-median, which gives a lower bound for `G`.
- *Convex:* the one-site weighted-distance (Weber) problem under map distance. It is not convex on road distance, which is the Field Manager's point.
- *Combinatorial:* the choice of `S` (about 2.6 × 10⁹ subsets for k = 8 of 60 sites); the fleet quantile (an order statistic); and `Φ_j`, an NP-hard routing problem with no closed form.
- *Implication:* `E` is a black box, so no exact optimum of `E` is available. Exact solution is possible only for `G` (capacitated p-median as a MIP for each k) and for the lower level on small instances (to measure heuristic accuracy). The upper level is therefore searched under a budget `B` (Section 4).

### I/O schema

| Input / output | Table or parameter | Field | Type / unit | Meaning; one row is … |
|---|---|---|---|---|
| Input | `stores` | `store_id`, `region`, `lat`, `lon`, `store_type`, `opened_on`, `closed_on`, `current_dc_id` | text; text; decimal degrees; enum (residential / office / mixed / roadside); date; date or null; text | one store (all stores, or the thinned fraction) |
| Input | `demand_history` | `store_id`, `date`, `cages_ordered` | text; date; integer (roll cages) | one store on one day, last 12 months; feeds the demand model |
| Input | `dcs` | `dc_id`, `lat`, `lon`, `lease_expires_on`, `monthly_fixed_cost`, `max_stores`, `min_stores` | text; degrees; date; KRW; integer | one current DC (12 rows) |
| Input | `candidate_sites` | `site_id`, `lat`, `lon`, `monthly_fixed_cost`, `max_stores` | text; degrees; KRW; integer | one leasable site, 40–80, including the 12 current ones |
| Input | `cost_parameters` | name, value, unit | fuel 350 KRW/km; vehicle 5.5 M KRW/month; spot 550,000 KRW/day; late 120,000 KRW; stock-out 500,000 KRW; capacity 48 cages | one parameter |
| Input | model parameters | `v`, `s`, `τ0`, `T`, `M`, `L`, `α`, `q`, `D_m`, `κ`, thinning fraction, sampled-day counts (7 / 28 / 14), `B` (default 40), seeds | km/h; min; clock time; h; count; km; share; share; days; count; fraction; counts; count; integer | one scalar setting |
| Input | `road_distance` cache (ours) | `from_id`, `to_id`, `km`, `source` | text; text; km; enum (engine / calibrated detour) | one point pair; source, version, extract date stated in Section 3 |
| Output | recommended network | `site_id` list, `k`, `store_id → dc_id`, `N_j` per DC | text list; integer; text pairs; integer | the recommended `S`, its assignment and owned vehicles |
| Output | cost breakdown | component (the five cost components plus total), `mean`, interval on sampled days, interval on held-out days | text; KRW/month; KRW/month | one cost component of one network (baseline and recommended) |
| Output | service level | realised share before 12:00, share after 14:00, undelivered | share per network | one network |
| Output | evaluation log | `eval_id`, `S`, `q`, `k`, `G`, `E`, selection rule, seed, days used | integer; text list; number; KRW/month; text | one full evaluation (every one of the `B`) |
| Output | sweep | `k`, best `E`, `F`, operating cost | integer; KRW/month | one number of DCs |
| Output | `G`-versus-`E` table | candidate, `G`, `E` | KRW/month | one evaluated candidate |
| Output | lower-level run | route id, store sequence, km, stops, arrival times, late flags, vehicles, spot | mixed | one route on one DC-day |
| Output (baseline history) | `deliveries`, `vehicle_days`, `spot_hires` | as in the brief's Section 2.5 | as in the brief | produced by running our own lower level on the current network, and labelled so |

### The two criteria

- **Full evaluation `E(S, q)`:** fixed cost + vehicle cost + `D_m` × mean over sampled days of [fuel + spot + failure cost], with the assignment re-solved, the lower level solved for every DC on every day, and the fleet from the quantile rule (procedure in 2b). Expensive: `k · card(D)` routing solves per candidate; with the real routing system it would take hours.
- **Generation criterion `G(S) = F(S) + λ · P(S)`:** `P(S)` is the capacitated p-median assignment cost on **road** distances weighted by posterior-mean volume (the `assignment-proxy` value), and `λ` (KRW per cage-km per month) is a scale constant, calibrated on the evaluated candidates (Section 4). Cheap: one LP, or a MIP for the G-best set for each k.
- **What `G` ignores:** route sequencing (star distance replaces tour length), the 25-stop / 250-km / 48-cage limits and therefore the vehicle count, the stop time and the noon window, the fleet quantile and spot hiring, penalties for failures, demand variability (it uses means), and that the assignment minimises distance rather than route cost.

### Components

Roles and Models are kept separate (reference, Section 3). There is **no State-transition component** (`N/A — all decisions Stateless`) and **no Simulation component**: the sampled-day evaluation is a Monte Carlo average of a parameter (demand), and arrival times follow from an explicit recursion, not from executing system time and events. **No evaluation component is Data-driven.** The demand model is a supporting analysis that supplies parameters; the detour calibration supplies distance parameters. Neither is a learned relation from context and decision to outcome.

| Component ID | Decision ID | Role: Cost / Constraint / State-transition | Model: Formulation / Simulation / Data-driven | Definition, units, and evidence / assumption / unknown |
|---|---|---|---|---|
| `dc-fixed-cost` | `site-selection` | Cost (minimise) | Formulation | `F(S) = Σ_{j∈S} f_j`, KRW/month; default 150 M per DC, scaled with thinning; site-specific, higher in the capital region (fictional). |
| `vehicle-fixed-cost` | `fleet-policy` | Cost (minimise) | Formulation | `c_v · Σ_j N_j`, KRW/month; 5.5 M per vehicle including driver. |
| `fleet-rule` | `fleet-policy` | Constraint | Formulation | `N_j = ceil(Q_q({V_{j,d}}))` over sampled days; `q = 2/3` default. Uses sizing-pass outputs from `daily-routing`; an in-sample estimate. |
| `fuel-cost` | `daily-routing` | Cost (minimise) | Formulation | `c_f · Σ_r len(r) λ_r`, KRW/day; 350 KRW/km on cached road distances. |
| `spot-hire-cost` | `daily-routing` | Cost (minimise) | Formulation | `c_s · max(0, V − N)`, KRW/day; 550,000 KRW per truck-day; cap `κ` `TBD`. |
| `failure-cost` | `daily-routing` | Cost (minimise) | Formulation | `p_14 · (deliveries after 14:00) + p_so · (undelivered)`, KRW/day. |
| `load-limit` | `daily-routing` | Constraint (hard) | Formulation | `Σ q_i ≤ 48` cages per route; `q_i` from the posterior predictive. |
| `route-limits` | `daily-routing` | Constraint (hard) | Formulation | At most 25 stops, 250 km, and duration `≤ T`; stop time 15 min, speed 35 km/h. |
| `noon-share` | `daily-routing` | Constraint (hard) | Formulation | At most `floor(0.02 n)` stores arrive after 12:00 per DC-day; arrivals from the recursion in 2a. |
| `cover-once` | `daily-routing` | Constraint (hard) | Formulation | Each store is visited once or counted as undelivered. |
| `capacity-bounds` | `store-assignment` | Constraint (hard) | Formulation | Store count of each open DC between `min_j` and `max_j` (scaled if thinned); baseline exempt. |
| `pair-eligibility` | `store-assignment` | Constraint (hard) | Formulation | Store `i` may go to DC `j` only if a single-store route meets noon and the km/duration limits. |
| `assignment-proxy` | `store-assignment` | Cost (minimise) | Formulation | `Σ w_i · l_{i,j(i)}`, cage-km/day, posterior-mean volume `w_i`; a proxy for routing cost, judged by `daily-routing`. |

**Where the demand model supplies parameters:** sampled `q_i^d` to `load-limit` (and so to vehicles, kilometres, `fleet-rule` and the spot and failure costs); posterior-mean `w_i` to `assignment-proxy` and `G`. **Distances** (`l_uv`, `t_uv`) supply `fuel-cost`, `route-limits`, `noon-share`, `pair-eligibility`, `assignment-proxy` and `G`.

**Hard constraints versus preferences.** Hard: `capacity-bounds` (new designs only), `pair-eligibility`, `load-limit`, `route-limits`, `noon-share`, `cover-once`. Priced preferences: spot trucks, deliveries after 14:00, undelivered stores.

**Infeasible inputs** are reported, never silently repaired:

- A set `S` with `Σ max_j < n` or `Σ min_j > n`, or in which some store is eligible for no open DC, is rejected with the reason (stores or bounds responsible).
- A store demand above 48 cages raises a data error (not expected at the default mean of about 2.2 cages).
- A day the fleet cannot cover (spot cap `κ` binding) yields undelivered stores priced at `p_so`, and the result is flagged as not meeting the service level.
- Duplicate or missing IDs, non-finite coordinates or distances absent from the cache stop the run with a field-specific message.

**Follow-up questions (reference, Section 5).** *Stateful:* N/A — all decisions Stateless. *Feedback:* N/A — all decisions Open-loop. *Formulation:* answered above (variables, objective, hard and soft constraints, parameters, feasibility, infeasible-input handling). *Simulation:* N/A — no Simulation component. *Data-driven:* N/A for evaluation components (demand model and distance calibration are in Section 3). *Hierarchical:* parent and child decisions, what is passed or changed, and `evaluated_by` are in Section 1 and 2b; no feedback relationship is claimed.

## 3. Information & Uncertainty

*Pending.* Carried forward from Sections 1–2 for the facts, assumptions, unknowns and requests table:

- `TBD` Does loading count inside the 10-hour shift? Is the departure time 05:00 for all vehicles, or staggered between 04:00 and 07:00?
- `TBD` Is the 98%-before-noon target per DC or chain-wide? (Section 2 enforces it per DC-day.)
- `TBD` Is there a cap on spot-truck availability per DC-day? (Base run: no cap.)
- `TBD` Is there any carry-over of unmet demand, stock or vehicle availability between days? (Section 1 assumes none.)
- `[ASSUMPTION]` Symmetric road distances; `D_m = 30.4` days per month; routes frozen between sizing pass and accounting.
- To write: instantiated tables with seeds and distributions; the generation of the current 12 DCs, their historical assignment and the candidate sites; distance source and calibration; demand model (Poisson–Gamma with calendar exposure); thinning fraction and stop density; the sample-day construction including holiday draws.

## 4. Alternatives & Selected Approach

*Pending.* Checks already suggested by Sections 1–2, to include in the fidelity–computation table:

- Assignment by distance proxy versus assignment improved with route-based local swaps (effect on `E` and on the ranking of candidates).
- Frozen routes in the accounting step versus re-solving with owned vehicles free.
- Per-DC-day 2% noon budget versus a chain-wide 98% constraint.
- Which of load, stops, kilometres and the clock limits a vehicle's day, and how this changes when a territory grows (needed to interpret the sweep).
- Stop density after thinning (brief, Section 2.7).
- Fleet sized in-sample versus on held-out days.

## 5. Demonstration & Evaluation

*Pending.* Proposed boundary cases from Section 2: k = 8 (every DC near its maximum, the smallest feasible network); a budget of one evaluation. Proposed failure cases: k = 7 (violates the capacity bounds) and a day the fleet cannot cover.

## 6. Client Proposal

*Pending.*

## 7. LLM Review & Key Revisions

*Pending.* Three discussions to be recorded with the checks performed.

| Discussion / purpose | LLM suggestion | Your assessment and verification evidence | Final decision or revision; location in the deliverables |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |
