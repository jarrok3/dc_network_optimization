# Problem Definition: [Proposal Title]

**IE437 Midterm Project · Distribution-Centre Network Redesign — Formulating and Solving a Hierarchical Decision**

Author / student ID: 
Date / version:   
Summary: [two or three sentences — the recommended number of DCs, the estimated saving and its uncertainty, and the one simplification that matters most]  
Files: `problem-definition.md`, `code/`, `demo.html` or the local app in `code/`, `proposal.html`  
LLM tools and use: [tool/model, dates, and tasks supported]

Use the seven sections below. Replace prompts with your answer; adapt table rows to your scope. Cite sources beside the claims they support. Mark assumptions `[ASSUMPTION]`, unresolved facts `TBD`, and inapplicable items `N/A — reason`. Write each explanation once and refer back to it. Stable IDs are needed for decisions and components; a separate ID for every datum, setting, source or test is optional.

Read the [classification reference](problem-classification-reference.md) and the [format example](problem-definition-example.md). An unresolved classification is `unknown`, not a guessed label.

## 1. Decision, Scope & Problem Structure

State the business need and complete this sentence:

> [Who] uses [what information], [when], to decide [what], in order to improve [what outcome].

**The four statements.** Place each statement from the review meeting at a level — a decision, an evaluation of a decision, a method for taking a decision, or a claim about the validity of a model — or explain why it fits none. Draw the consequence: which of the four may be compared with which, and on what common scale.

| Statement | Level | Decision or component it concerns | Consequence for comparison |
|---|---|---|---|
| Strategy & Planning | | | |
| Delivery Operations | | | |
| Data Team | | | |
| Field Manager | | | |

**Boundary.** What interval and which decisions constitute one problem? One redesign chosen now, or a sequence of closures as leases expire? State exclusions and their consequences. Distinguish the company's current practice from the baseline you construct.

**Decisions and dependencies.** List each real decision with a stable ID, what is chosen, and who approves it. Write dependencies as `parent → child: what changes` (feasible region, state, parameter, objective, constraint, available action). Write evaluation relations separately as `evaluated_by`. A forecast, a distance lookup or a routing estimate is not a decision.

**Classification.** Derive it from the structure above; repeat decision rows as needed.

| Object | Property | Classification (or unresolved status) | Structural evidence or question to resolve |
|---|---|---|---|
| Decision dependency graph | Decision Structure | Flat / Hierarchical; if unresolved: unknown | |
| [Decision ID] | State Structure | Stateless / Stateful; if unresolved: unknown | |
| [Decision ID] | Information Structure | Open-loop / Feedback; if unresolved: unknown | |

Keep execution frequency, triggers and manual/automatic operation separate from classification.

## 2. Computable Problem

**2a · The lower level — one DC's daily operation.** Given, decided, objective, constraints, in compact standard form. Which features of the real operation are modelled and which are omitted, and why. How the delivery window enters — a hard constraint when routes are built, or penalties checked afterwards. What one solve returns upward.

**2b · The connection.** What passes down to the lower level and what passes up; how many lower-level solves one upper-level evaluation costs; where demand uncertainty enters; whether the store-to-DC assignment is part of the candidate or re-solved inside the evaluation, and why. Write the `evaluated_by` relation.

**2c · The upper level — how many DCs, and where.** Variables and domains (number, sites, assignment if part of the candidate, fleet policy), objective (fixed plus operating cost from 2b), constraints (capacity bounds, lease schedule), in standard form. Which parts are convex or linear and which are combinatorial, and what that implies for exact solution.

**I/O schema.** Every input table and parameter the system takes, and every output it returns.

| Input / output | Table or parameter | Field | Type / unit | Meaning; one row is … |
|---|---|---|---|---|
| Input | | | | |
| Output | | | | |

**The two criteria.** Define the generation criterion `G` (cheap, proposes candidates) and the full evaluation `E` (expensive, judges them) precisely. State what `G` ignores.

**Components.** Keep each component's Role and Model separate, using the reference definitions. Say which components the learned demand model supplies parameters to.

| Component ID | Decision ID | Role: Cost / Constraint / State-transition | Model: Formulation / Simulation / Data-driven | Definition, units, and evidence / assumption / unknown |
|---|---|---|---|---|
| | | | | |

Identify hard constraints versus preferences. Specify how infeasible inputs are handled. Answer only the applicable follow-up questions in the [reference](problem-classification-reference.md#5-follow-up-questions-only-where-applicable).

## 3. Information & Uncertainty

**The instantiated data.** For each table of the company's specification, how you generated it (distribution, parameters, seed) and which real-world property it reflects. The scale of your instance and what you expect to change at full scale.

**Distances.** Which route of Section 2.5 of the brief you took — detour model, routing engine, or a mixture; source, version, extract date, vehicle profile; which point pairs were computed and at what resolution; where the cache lives in `code/`; how any approximation was calibrated against road values and how large its error is on the calibration sample. **Instance.** Whether and how you thinned the chain (Section 2.7 of the brief): the fraction kept, how the DC fixed cost and capacity bounds were scaled, and how you handled stop density — one stop per sampled store with the distortion recorded, or aggregated stops. How the 12 current DCs and the candidate sites were placed, and why that is plausible.

**The demand model.** Prior and its strength, calendar effect, likelihood, update, posterior and posterior predictive for planning days. How short-history stores are handled. Where demand enters a cost, and the difference between sampled-day and average-day evaluation, explained whatever its size.

**Facts, assumptions, unknowns and requests.** Separate information available at decision time from later outcomes.

| Fact / assumption / unknown or request | Evidence or reason for current treatment | Why it matters | How to confirm or obtain it; priority | Fallback or limit until confirmed |
|---|---|---|---|---|
| | | | | |

Explain the main uncertainties, how you examined their effect, and when the recommendation should not be trusted.

## 4. Alternatives & Selected Approach

**Approaches.** Compare at least the baseline, a proxy-only approach (generate and judge with `G`), and your selected approach.

| Approach | Mechanism and required information | Strengths, limitations, and effort | Selection and reason |
|---|---|---|---|
| Baseline | | | |
| Proxy-only | | | |
| Selected | | | |

**The solution-algorithm plan.** The lower-level method and its cost per solve and measured accuracy; the search over the number of DCs; how candidates are generated for each number; the budget `B` and its justification; the surrogate and acquisition rule (or the justified alternative) and the parameterisation it works on — the knobs or the pool features; the comparison with random selection at the same budget; the statement that the reported search is one fresh run of `B` evaluations from the logged seeds; how the log is kept. Give the calculation flow from input to output, referring to Section 2. Explain tie-breaking and randomness. Identify actual calculations, precomputed results, illustrative calculations and unimplemented parts. Do not claim optimality beyond what the method establishes.

**The fidelity–computation balance.** One row per modelling choice — at least the distance model, the demand model and the number of sampled days, the routing method, the delivery window, the assignment, the candidate set, the number of DCs swept, the search and its budget.

| Modelling choice | What the real operation does | What you model | What it costs to compute | What the simplification could get wrong | How you checked whether it changes the decision |
|---|---|---|---|---|---|
| | | | | | |

Follow with the argument: where computation was spent and why; which simplification you would remove first; and for at least two simplifications the check — a rerun with the simplification relaxed on a smaller instance, or a bound — and whether the recommended number of DCs or the ranking of the top candidates changed.

**The remedy.** Which Lecture 5 remedy you applied to `G`, if the gap warranted one, and what it changed; or why none was needed.

## 5. Demonstration & Evaluation

Explain how to run `code/` (the three commands, their run times, the seed, what the reduced run omits) and how to open `demo.html` or start the local app, read the map, the sweep, the log and the `G`-versus-`E` comparison, and run the lower level live for one DC. State the browser checked and the offline result. Map the demo's behaviour to the decisions and components of Section 2.

Define the comparison conditions shared by every candidate and the baseline. Keep evaluation outcomes separate from information used to make the recommendation.

| Case | Inputs / reproduction steps | Expected result / pass criterion | Observed result and supported conclusion |
|---|---|---|---|
| Normal | | | |
| Boundary | | | |
| Failure | | | |

**Prediction quality and decision quality.** How well `G` predicts `E` across the evaluated candidates, and how much the `G`-chosen network loses under `E`; the cause of the gap. **The sweep.** Total cost and its two components against the number of DCs. **The log and the rule's worth.** Budget used, rule, the sequence of evaluated candidates, and the best cost found by your rule against random selection at the same budget. **Held-out days.** The saving on sampled days the search never saw. Write `Not run` for unexecuted checks. Summarise what the demonstration establishes and what it does not.

## 6. Client Proposal

The recommended number of DCs and their sites; the estimated saving against the baseline with its uncertainty; the assumptions under which it would reverse. The information to obtain first with the real tables (refer to Section 3), the first real-data check, and the conditions to proceed, revise or stop. End with the concrete decision requested from management. Use this argument in `proposal.html`, keeping its claims consistent with the code and the demonstration.

## 7. LLM Review & Key Revisions

Record three important discussions and your own checks. Accepted suggestions are valid examples. Combine important revisions with this record rather than keeping another change log.

| Discussion / purpose | LLM suggestion | Your assessment and verification evidence | Final decision or revision; location in the deliverables |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |
