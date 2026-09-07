# Experiment Results

## Interpretation boundary

All results are public-data-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated. They test implementation behavior under explicit assumptions. They do not establish causal clinician effects, patient benefit, payment improvement, or a real-world optimal threshold. The production/demo policy remains P4 at percentile 0.75.

## Final artifact set

| Tier | Design | Final rows/runs | Role |
|---|---|---:|---|
| Major scenarios | P4, 5,000 patients, scenarios A-I, one seed, threshold 0.75 | 9 | Stable large-cohort scenario check; no optimization |
| Fine threshold grid | P4, 100 patients, scenarios A-I, seeds 0/1, thresholds 0.00-1.00 by 0.01 | 1,818 | Seed 0 selection, seed 1 held-out sensitivity |
| Policy bake-off | P0-P7, 500 patients, scenarios A-I, two seeds, threshold 0.75 | 144 | Fixed-pilot policy comparison; no threshold optimization |
| Operational stress | P4-P6, 200 patients, F/G/H, one seed, two thresholds and 2 x 2 x 2 roster/capacity/ZIP designs | 144 | In-sample sensitivity only; no recommendation |
| Structural audit | Exact seven-factor Cartesian grid | 2,160 profiles | Deterministic correctness, ablation, reliability, and counterfactual diagnostics |

All 2,115 batch rows have unique rerun identities within their artifact tiers. The analytical unconstrained latent-information upper bound is greater than or equal to every included-policy result; P7 is a capacity-feasible latent-quality greedy comparator and is not the oracle.

## Sponsor policy across major scenarios

For the 5,000-patient P4 run, the mean simulated utility was 0.584152 and mean TPS proxy was 58.260271. Mean ZIP continuity was 0.924919 and mean ZIP fallback was 0.025450.

| Scenario | Utility | TPS proxy | Upper-bound regret | ZIP fallback | ZIP continuity | Unassigned |
|---|---:|---:|---:|---:|---:|---:|
| A additive | 0.609998 | 60.843808 | 0.040538 | 0.011746 | 0.947224 | 0.007133 |
| B positive interaction | 0.623056 | 62.149672 | 0.041569 | 0.011746 | 0.947224 | 0.007133 |
| C negative interaction | 0.586610 | 58.505050 | 0.032774 | 0.011746 | 0.947224 | 0.007133 |
| D no clinician/routing effect | 0.557056 | 55.549632 | 0.003738 | 0.011746 | 0.947224 | 0.007133 |
| E no ZIP benefit | 0.578086 | 57.652588 | 0.035830 | 0.011746 | 0.947224 | 0.007133 |
| F positive ZIP benefit | 0.662728 | 66.116788 | 0.040627 | 0.011746 | 0.947224 | 0.007133 |
| G sparse ZIP coverage | 0.602724 | 60.116445 | 0.036271 | 0.135582 | 0.750353 | 0.007133 |
| H capacity shortage | 0.430614 | 42.914360 | 0.213908 | 0.012669 | 0.943553 | 0.310267 |
| I score uncertainty | 0.606501 | 60.494098 | 0.036854 | 0.010320 | 0.947022 | 0.007133 |

The stress signatures are clear: sparse ZIP coverage raises fallback and reduces continuity, while capacity shortage drives the largest regret and unassigned rate. Scenario D correctly changes assignments across thresholds without creating a true assignment effect in outcomes.

## Fixed-pilot policy bake-off

| Policy | Utility | TPS proxy | Upper-bound regret | ZIP continuity | Travel proxy |
|---|---:|---:|---:|---:|---:|
| P0 | 0.578346 | 57.685986 | 0.062073 | 0.746058 | 17.265897 |
| P1 | 0.587772 | 58.628583 | 0.052647 | 0.919595 | 12.283750 |
| P2 | 0.577612 | 57.612619 | 0.062807 | 0.738420 | 17.486291 |
| P3 | 0.577531 | 57.604524 | 0.062888 | 0.731197 | 17.313740 |
| P4 | 0.583390 | 58.190364 | 0.057029 | 0.914387 | 16.470850 |
| P5 | 0.576879 | 57.539336 | 0.063540 | 0.713484 | 17.365586 |
| P6 | 0.585006 | 58.351244 | 0.055435 | 0.962800 | 10.281399 |
| P7 | 0.591777 | 59.029617 | 0.048673 | 0.919156 | 11.930244 |

P7 has the highest utility in scenarios A and C-I; P6 leads scenario B. P4 exceeds P5 overall by 0.006511 utility, 0.651028 TPS points, 0.200903 continuity, and 0.894736 lower travel proxy, at the cost of a 0.029071 fallback rate. P6 exceeds P4 overall by 0.001616 utility and 0.160880 TPS points, with 0.048413 greater continuity and 6.189451 lower travel proxy. In the capacity-shortage scenario H, however, P4 exceeds P6 by 0.019351 utility; no alternative is uniformly dominant.

P6 is therefore a recommended subject for further operational study, not an activated replacement. P5's tested soft-bonus formulation does not outperform P4 overall and also remains inactive.

## Fine-grid threshold sensitivity

The seed-0 minimax-regret threshold was 0.99. Its maximum regret was 0.001207 on selection and 0.000908 on the held-out seed. Scenario-specific selections varied from 0.17 to 1.00, demonstrating substantial scenario dependence.

| Threshold | Split | Objective | Utility | TPS proxy | ZIP continuity | ZIP fallback |
|---|---|---:|---:|---:|---:|---:|
| 0.75 | Selection | 0.547436 | 0.589879 | 58.901025 | 0.932434 | 0.023148 |
| 0.75 | Held out | 0.523920 | 0.578518 | 57.654768 | 0.910445 | 0.045725 |
| 0.99 | Selection | 0.548386 | 0.590737 | 58.986809 | 0.964762 | 0.031746 |
| 0.99 | Held out | 0.525316 | 0.579790 | 57.781999 | 0.946601 | 0.051852 |

On the one held-out seed, 0.99 improves objective by 0.001396, utility by 0.001272, TPS proxy by 0.127231, and continuity by 3.62 percentage points versus 0.75, while fallback rises 0.61 points. Those small simulated gains, the 0.17-1.00 scenario spread, and the two-seed limit do not justify a production change. The runtime pilot remains 0.75.

## Public synthetic calibration

The rebuilt public-only pipeline contains 10,000 synthetic episodes. Its separate 101-threshold calibration used a deterministic 500-row analysis sample and an explicit 10 TPS-point penalty per unit miss from the target high-risk share. It recorded both counts, selected 0.79 on seed `20260608` with a 0.250 selected high-risk share, and evaluated it on seed `20260609`. Held-out objective at 0.79 was 62.928931 versus the held-out best 63.375202, for regret 0.446271. The artifact retains `risk_threshold=0.75`, labels 0.79 simulation-only, and records `production_validated=false`.

## Operational stress sensitivity

Across the 48 matched one-seed design cells, P4 led expected utility in 30, P6 in 18, and P5 in none. P4 averaged 0.4937 expected utility, 74.82% continuity, 6.15% fallback, 15.90 travel, and 22.33% unassigned. P6 averaged 0.4891 utility, 90.03% continuity, zero fallback, 11.11 travel, and the same unassigned rate. P5 averaged 0.4814 utility and 46.81% continuity.

- At capacity 0.70, P4 led 22 of 24 cells and P6 led 2; at capacity 1.20, P6 led 16 of 24 and P4 led 8. Unassigned fell from 38.19% to 6.47% for all three policies.
- Moving the ZIP-sparsity setting from 0.20 to 0.80 reduced P4 continuity from 83.93% to 65.70% and raised fallback from 3.27% to 9.03%. Scenario G overrides effective sparsity to 0.78, so its nominal 0.20/0.80 pairs are intentionally identical.
- Increasing roster scale from 0.75 to 1.25 did not reduce unassigned rate in this design because capacity ratio fixes modeled slots; with one seed, unassigned instead moved from 21.83% to 22.83%. This is a design caveat, not evidence that larger rosters worsen access.
- For P4, threshold 0.75 versus 0.25 increased utility by 0.0079 and continuity by 18.64 percentage points, while fallback rose 2.48 points. P5/P6 utilities were essentially threshold-insensitive in this formulation.

This factorial is explicitly in-sample sensitivity only and emits no threshold recommendation.

## Structural and technical fairness audit

- All 2,160 unique factor profiles exactly match the canonical raw score and persisted-reference percentile.
- Weighted factor contributions reconstruct raw risk with maximum numerical error `2.39e-15`.
- Strict routing classifies 694 profiles as `u > 0.75` and 1,466 as lower-risk; semantic route classification matches the canonical grid exactly.
- Seven ablations and fourteen one-factor counterfactual rows rerun actual P4 assignments against a fixed roster; seventy reliability rows confirm reliability changes uncertainty but not mean-only runtime routing.
- The diagnostic reports 540 monotonicity flags, all for metropolitan-to-micropolitan comparisons. This follows the locked configured contributions (metropolitan positive, micropolitan zero) and is recorded as an unresolved configuration decision rather than suppressed.
- Structural route vocabulary differs only in literal label (`lower_risk_zip_policy` versus `zip_history_or_fallback_lower_risk`); semantic classification is identical.

These counterfactuals are technical model diagnostics, not clinical, legal, or discrimination findings.

## Robustness judgment

P4 is mechanically correct, deterministic, and reasonably stable in additive, interaction, ZIP-benefit, no-benefit, and score-uncertainty simulations. It is **not robustly best across policies or operational conditions**: P6 slightly improves average simulated utility/continuity/travel, while P4 performs better under the modeled capacity shortage; sparse ZIPs materially weaken P4 routing. No simulation result changes the production/demo threshold or policy.

Machine-readable evidence is under `reports/experiments/`; exact commands are in `REPRODUCTION_COMMANDS.md` and limitations in `LIMITATIONS.md`.
