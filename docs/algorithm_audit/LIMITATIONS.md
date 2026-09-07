# Limitations

- No sponsor-level validation data were available. All patient, clinician, ZIP-history, capacity, assignment, and outcome evidence is public-only, expert-configured, semi-synthetic, or simulated.
- `risk_score_rho_raw` is a modeled need index, not a calibrated clinical probability. `risk_percentile_u` is relative only to the configured reference distribution.
- The 0.75 threshold is a balanced capacity pilot and is not outcome-validated. A different simulation recommendation is not a production change.
- Public agency outcomes can calibrate marginal distributions but cannot identify patient-clinician causal effects or ZIP-continuity benefits.
- The default clinician percentile reference is the same-discipline request roster. It fixes cross-discipline contamination but retains sensitivity to same-discipline roster composition.
- No metric-specific clinician sample sizes are available. Production ranking uncertainty is therefore unidentifiable; generic/synthetic counts are not treated as observed evidence.
- Missing clinician metrics use audited discipline-median or organization-benchmark imputation. Imputation can compress ranks and should not be presented as observed performance.
- P6 is a deterministic greedy capacity optimizer rather than a complete operational scheduler or exact global mixed-integer solution.
- P7 uses latent simulated quality but remains capacity-feasible and greedy; it is a comparator, not a real-world oracle. The regret upper bound is intentionally unconstrained and is not an implementable policy.
- Travel is a ZIP-based synthetic proxy, not observed drive time, routing, weather, or scheduling cost.
- Sequential simulation ordering can affect capacity-constrained assignments; ordering is deterministic for reproducibility.
- Fairness gaps are model diagnostics and can reflect the configured factor weights, ZIP coverage, roster, and scenario assumptions. They are not legal conclusions.
- Reliability affects modeled uncertainty but not mean-only online routing decisions in the current implementation.
- The executed seed count is smaller than 100 because measured runtime implies roughly 190 days for the full Cartesian default. Reported thresholds and policy rankings have substantial Monte Carlo uncertainty; the fine-grid held-out evaluation uses only one selection and one evaluation seed.
- The public synthetic build contains 10,000 episodes, but its 101-threshold calibration uses an explicitly recorded deterministic 500-row analysis sample for feasible runtime. Its 10 TPS-point/unit target-share penalty is an expert-configured design choice, not an estimated clinical tradeoff. This does not increase the evidence level.
- TPS is a CY2025-weighted proxy, not an official CMS TPS/payment calculation and not evidence of payment improvement.
- CY2026 migration, live public-source downloads, real scheduling integration, and prospective outcome validation remain future work.
