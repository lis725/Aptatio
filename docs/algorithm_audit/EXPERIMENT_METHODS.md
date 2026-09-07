# Experiment Methods

## Evidence boundary

No usable sponsor patient, clinician, assignment, ZIP-history, OASIS, HHCAHPS, scheduling, capacity, or outcome dataset exists. Experiments are deterministic public-only, expert-configured, semi-synthetic simulations. They test implementation behavior and sensitivity, not causal effectiveness or official payment impact.

## Reference and structural analysis

- Weighted Monte Carlo reference: 10,000 profiles, seed `20260608`.
- Exact structural grid: 2,160 profiles (`3 x 5 x 3 x 4 x 2 x 3 x 2`).
- ECDF: average-rank ties, linear interpolation between support points, clamp to `[0, 1]`.
- Quantiles: deterministic type-7 linear interpolation.
- Pilot percentile: 0.75; raw equivalent `0.5696213523887566`.
- Every candidate percentile reports its own type-7 raw-score equivalent; the pilot raw cutoff is not reused for other candidates.

Structural outputs include metric need means, weighted contributions, raw score, persisted-reference percentile, route, representative anchors, rank-access diagnostics, ablations, reliability sensitivity, and one-factor counterfactual fairness comparisons. Ablations and counterfactuals rerun the actual P4 assignment service against a fixed representative roster. Reliability changes uncertainty, not mean-only routing decisions, in the active online model.

## Batch simulator

Each scenario/seed creates one immutable patient cohort, clinician roster, capacity vector, availability draw, ZIP-history draw, quality-uncertainty draw, and outcome-noise array. Policies share these draws (common random numbers). Each policy/threshold run copies the roster, resets workload, and sequentially assigns each patient/discipline subject to availability, discipline compatibility, and remaining capacity.

Policies:

- P0 deterministic round-robin baseline.
- P1 highest-scoring feasible clinician.
- P2 historical raw-rho mirrored baseline.
- P3 corrected percentile mirror without ZIP routing.
- P4 corrected threshold plus hard ZIP-history filter (sponsor/default policy).
- P5 ZIP history as a soft bonus.
- P6 capacity-constrained deterministic greedy optimization.
- P7 latent-quality, capacity-feasible greedy comparator.

Scenarios:

- A additive clinician-quality effect.
- B positive high-risk by clinician-quality complementarity.
- C diminishing returns / negative interaction.
- D no true clinician or routing causal effect.
- E no ZIP-continuity benefit.
- F positive ZIP-continuity benefit.
- G sparse ZIP coverage.
- H clinician capacity shortage.
- I high clinician-score uncertainty.

Outputs include CY2025 TPS proxy, simulated outcome utility, regret versus a separately computed unconstrained latent-information upper bound, regret versus the best included policy, high-risk share, ZIP fallback/continuity, workload spread/Gini/max overload, utilization, travel proxy, and gaps by age, ZIP, area type, housing, house-price group, and clinical-need decile. The analytical upper bound ignores feasibility constraints and therefore bounds every included policy; P7 remains a feasible comparator rather than an oracle.

## Executed tiered design and runtime justification

The CLI exposes the requested full default (5,000 patients, 100 seeds, 101 thresholds, 9 scenarios, 8 policies). A timing run measured **248.1 seconds** for only 11 P4 thresholds in one 5,000-patient scenario/seed. Linear extrapolation of the full Cartesian request is about **190 days** on this workspace, before multi-roster/capacity/ZIP designs.

The final artifacts therefore use a transparent tiered design:

1. 5,000 patients in each major scenario at P4 and the locked 0.75 pilot.
2. Full 0.00-1.00 by 0.01 threshold grid for P4 on two deterministic seeds with a reduced cohort: the first seed selects and the second seed evaluates.
3. Complete P0-P7 scenario bake-off at the locked pilot with an intermediate cohort and two seeds.
4. Separate multi-roster, multi-capacity, and multi-ZIP-sparsity stress grid.
5. Exact 2,160-profile structural, ablation, reliability, and counterfactual diagnostics.

The two-seed fine grid is a runtime-constrained held-out sensitivity screen, not a substitute for 100-seed uncertainty estimation. Scenario-specific best and minimax-regret thresholds are selected only on the first seed and audited only on the second; they are labeled simulation-only. A one-seed multi-threshold grid is in-sample sensitivity only and emits no recommendation. Fixed-threshold runs explicitly report that threshold optimization was not evaluated. Runtime configuration remains 0.75.

The public-only capacity calibration is separate from the fine grid. It uses a deterministic 500-row analysis sample from the 10,000-episode build and deducts 10 TPS-proxy points per unit absolute miss from the scenario target high-risk share (equivalently 0.1 TPS point per percentage-point miss). Incomplete seven-factor input is rejected rather than silently replaced. Its selection and held-out seeds are distinct, and its result remains simulation-only.

Experiment evidence loaded into sponsor reports is accepted only when tier metadata matches the current CY2025 lock, persisted risk reference, design role, evidence-boundary flags, and implementation-file hashes. PDF manifests bind each rendered PDF to its source Markdown SHA-256 and the current source-bundle identity.

## Technical fairness scope

Counterfactuals hold a modeled profile fixed and change one configured attribute, then rerun P4 against the same fixed roster. Reported differences are technical allocation diagnostics under the simulator. They are not legal conclusions and do not establish discrimination or clinical fairness.
