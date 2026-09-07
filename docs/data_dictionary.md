# Aptatio CY2025 Data Dictionary

All checked-in reference and experiment data are public-data-only, expert-configured, semi-synthetic, or simulation-based. No sponsor patient-, clinician-, assignment-, ZIP-history-, OASIS-, HHCAHPS-, scheduling-, capacity-, or outcome-level data were available.

## Patient and reference fields

| Field | Type | Meaning |
|---|---:|---|
| `health_status` | category | `healthy`, `moderate`, or `unhealthy`. |
| `age_group` | category | Canonical band: `age_40_50`, `age_50_60`, `age_60_70`, `age_70_80`, or `age_80_plus`. |
| `house_price_zip` | category | Expert/public-proxy group: `high`, `average`, or `low`. |
| `area_type` | category | `metropolitan`, `micropolitan`, `small_town`, or `rural`. |
| `housing_type` | category | `single_home` or `apartment`. |
| `distance_to_clinic` | category | `near`, `medium`, or `far`. |
| `driving_condition` | category | `good_summer` or `bad_winter`. |
| `rho_raw` / `risk_score_rho_raw` | float | Aggregate modeled patient need index. It is not a probability. |
| `risk_percentile_u` | float `[0,1]` | Interpolated empirical percentile of `rho_raw` against the persisted 10,000-row reference. |
| `threshold_percentile` | float `[0,1]` | Operational percentile cutoff; production/demo pilot is `0.75`. High risk uses strict `u > 0.75`. |
| `tau_raw` / `threshold_raw_equivalent` | float | Type-7 raw-score quantile corresponding to the percentile threshold. |
| `threshold_route` | category | Structural label: `global_high_risk` or `lower_risk_zip_policy`. |

Metric need columns in the structural grid use `need_mean_<metric>` and `weighted_need_mean_<metric>` for the ten canonical CY2025 metrics: `DFS`, `Dyspnea`, `Oral_Meds`, `DTC`, `PPH`, `Care_of_Patients`, `Communications`, `Care_Issues`, `Agency_Rating`, and `Recommend`.

## Clinician and assignment fields

| Field | Type | Meaning |
|---|---:|---|
| `clinician_id` | integer | Stable ID; examples follow RN=1xx, PT=2xx, OT=3xx. |
| `discipline` | category | `RN`, `PT`, or `OT`. |
| `<metric>_source` | category | `observed`, `imputed_discipline_median`, or `imputed_org_benchmark`. |
| `<metric>_capability` | float `[0,1]` | Percentile computed within discipline on the full request roster before ZIP filtering. |
| `full_pool_rank` | integer | Rank in the complete discipline pool; retained after ZIP filtering. |
| `ranking_position` | integer | Rank inside the selected routed pool. |
| `zip_history_zip5s` | string array | Normalized five-character ZIPs, deduplicated. |
| `route_by_discipline` | mapping | `global_high_risk`, `zip_history`, or `zip_history_fallback_full_pool` (plus explicit disabled/debug labels). |
| `fallback_reason` | mapping | Missing ZIP or insufficient ZIP-pool detail. |
| `imputation_confidence` | category | Observed, discipline-median imputation, or organization-benchmark imputation flag. |

OT is not converted into the RN/PT CY2025 scoring model. It uses deterministic rotation within the routed OT pool.

## Batch experiment fields

| Field | Meaning |
|---|---|
| `policy` | P0 round-robin, P1 highest feasible, P2 historical raw-rho mirror, P3 percentile mirror/no ZIP, P4 hard-ZIP sponsor policy, P5 soft-ZIP bonus, P6 capacity-constrained optimizer, or P7 latent-quality capacity-feasible greedy comparator. |
| `scenario` | A-I scenario key defined in `src/hhvbp_batch_experiments.py`. |
| `tps_proxy` | CY2025-weighted simulated performance proxy; not an official CMS payment calculation. |
| `simulated_outcome_utility` | Mean favorable simulated outcome under explicit scenario assumptions. |
| `scenario_oracle_upper_bound_utility` | Unconstrained latent-information analytical upper bound; not an implementable policy. |
| `regret_vs_scenario_oracle` | Difference between the analytical upper bound and policy utility. The legacy column name is retained for artifact compatibility. |
| `regret_vs_best_included_policy` | Difference from the best P0-P7 policy for the same design/scenario/seed/threshold. |
| `zip_fallback_rate` / `zip_continuity_rate` | Operational routing rates. |
| `workload_spread`, `workload_gini`, `max_overload` | Capacity/workload diagnostics. |
| `travel_proxy` | Synthetic ZIP travel cost, not observed travel. |
| `fairness_gap_*` | Maximum-minus-minimum simulated utility gap across the named subgroup. Technical audit only, not a legal conclusion. |
| `assignment_signature` | Stable hash of selected pairs, used to prove threshold reruns change assignments. |
| `rerun_id` | Stable hash including the canonical design/configuration identity. |
| `common_random_numbers` | `true` when policies share patient, clinician, and noise draws. |

## Provenance fields

Every current artifact records `public_data_only=true` and `production_validated=false`. Reference metadata also records source mode, seed, sample size, configuration hash, component checksums, factor distributions, creation timestamp, and the warning that no sponsor data or outcome validation were used.
