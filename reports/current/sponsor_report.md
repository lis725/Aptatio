# Sponsor Review Brief: Current Aptatio Prototype

**Model lock:** Expanded HHVBP, CY2025 performance / CY2027 payment, measure set CY2025 (version locked).

**Validation boundary:** Public-data-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated.

These results describe configured model scenarios. They do not establish real-world clinical effects, payment effects, or a uniquely best assignment policy.

## Current decision policy

The sponsor-requested hard ZIP-history policy remains the default. Cases strictly above the 0.75 reference percentile use the full discipline pool. Cases at or below 0.75 try the assignment ZIP-history pool and fall back to the full pool when coverage does not meet the discipline minimum.

## What has been corrected in the current model

- The active patient model uses exactly seven factors, including the five-band age factor.
- Clinician percentiles are computed within discipline before ZIP filtering.
- Raw modeled need and empirical reference percentile are retained as different quantities.
- The mirrored anchor and strict threshold rule use the reference percentile.
- Assignment ZIP routing is separated from Reno distribution profiling.
- PPH and higher-is-better metric narratives use direction-correct anchor language.

## Reference and threshold provenance

The checked-in risk reference contains 10,000 weighted Monte Carlo profiles and is accompanied by the exhaustive 2,160-profile structural grid. Its source mode is `offline_demo_fallback` and sponsor data were not used.

The 0.75 threshold is a public-only semi-synthetic capacity pilot. It remains a review setting unless the project team approves a later change after operational evaluation.

## Completed simulation evidence

- The training-seed minimax-regret threshold was 0.99; held-out maximum regret was 0.000908. This did not change the 0.75 pilot.
- The fixed-0.75 P4 major-scenario run averaged utility 0.5842, ZIP continuity 0.925, and unassigned share 0.041.
- In the fixed-pilot bake-off, P4 utility was 0.5834; P6 utility was 0.5850, with P6 continuity 0.963 and travel 10.28. Policy ordering remains scenario-dependent.
- Across the operational stress grid, P4 utility was 0.4940; P6 had continuity 0.900 and travel 11.11.
- The structural audit covered 2,160 profiles with 540 configured-score monotonicity violations.

These are configured simulation comparisons, not sponsor historical validation or evidence of clinical causality, field benefit, or payment change.

## Decisions still requiring sponsor or project-team input

- Whether to retain hard ZIP filtering after reviewing fallback, travel, continuity, and workload results.
- What real clinician capacity and availability constraints should govern deployment.
- Whether an external clinician reference distribution can reduce same-discipline roster sensitivity.
- What authorized data and prospective design can support outcome validation.
- What fairness tolerances and escalation rules should be approved for a pilot.

## Boundaries for sponsor interpretation

Configured scenarios can compare policy behavior under stated assumptions. They do not demonstrate patient benefit, clinical causality, payment change, or field performance.
