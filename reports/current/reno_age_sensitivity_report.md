# Reno Five-Age Sensitivity Report

**Model lock:** Expanded HHVBP, CY2025 performance / CY2027 payment, measure set CY2025 (version locked).

**Validation boundary:** Public-data-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated.

These results describe configured model scenarios. They do not establish real-world clinical effects, payment effects, or a uniquely best assignment policy.

## Design

This is the dedicated age sensitivity, so age is intentionally varied across all five configured groups. All factors other than age are fixed at `health_status=moderate`, `house_price_zip=average`, `area_type=metropolitan`, `housing_type=single_home`, `distance_to_clinic=near`, and `driving_condition=good_summer`.

The Reno label supplies context only. No patient assignment ZIP is set and no clinician ZIP-history routing is applied.

## Aggregate need and reference percentile

| Age group | rho raw | u percentile | Strict 0.75 classification |
| --- | --- | --- | --- |
| age_40_50 | 0.233 | 0.084 | at_or_below_pilot_threshold |
| age_50_60 | 0.268 | 0.124 | at_or_below_pilot_threshold |
| age_60_70 | 0.309 | 0.197 | at_or_below_pilot_threshold |
| age_70_80 | 0.358 | 0.299 | at_or_below_pilot_threshold |
| age_80_plus | 0.406 | 0.413 | at_or_below_pilot_threshold |

## Metric-level sensitivity

| Metric | Age group | Mean (%) | Need mean | Distance to anchors |
| --- | --- | --- | --- | --- |
| PPH | age_40_50 | 8.96 | 0.231 | 8.96% is 2.19 points above best and 7.28 points below worst. |
| DTC | age_40_50 | 76.21 | 0.237 | 76.21% is 1.99 points below best and 6.42 points above worst. |
| DFS | age_40_50 | 77.73 | 0.235 | 77.73% is 1.89 points below best and 6.14 points above worst. |
| Oral_Meds | age_40_50 | 89.99 | 0.252 | 89.99% is 1.93 points below best and 5.70 points above worst. |
| Dyspnea | age_40_50 | 93.73 | 0.187 | 93.73% is 1.21 points below best and 5.28 points above worst. |
| Recommend | age_40_50 | 81.31 | 0.198 | 81.31% is 0.86 points below best and 3.49 points above worst. |
| Agency_Rating | age_40_50 | 87.56 | 0.243 | 87.56% is 1.09 points below best and 3.41 points above worst. |
| Care_Issues | age_40_50 | 86.54 | 0.273 | 86.54% is 1.26 points below best and 3.35 points above worst. |
| Communications | age_40_50 | 88.95 | 0.263 | 88.95% is 1.05 points below best and 2.94 points above worst. |
| Care_of_Patients | age_40_50 | 91.38 | 0.206 | 91.38% is 0.83 points below best and 3.19 points above worst. |
| PPH | age_50_60 | 9.32 | 0.270 | 9.32% is 2.56 points above best and 6.91 points below worst. |
| DTC | age_50_60 | 75.92 | 0.272 | 75.92% is 2.29 points below best and 6.13 points above worst. |
| DFS | age_50_60 | 77.44 | 0.271 | 77.44% is 2.18 points below best and 5.85 points above worst. |
| Oral_Meds | age_50_60 | 89.66 | 0.296 | 89.66% is 2.26 points below best and 5.37 points above worst. |
| Dyspnea | age_50_60 | 93.53 | 0.218 | 93.53% is 1.41 points below best and 5.08 points above worst. |
| Recommend | age_50_60 | 81.23 | 0.216 | 81.23% is 0.94 points below best and 3.41 points above worst. |
| Agency_Rating | age_50_60 | 87.46 | 0.264 | 87.46% is 1.19 points below best and 3.32 points above worst. |
| Care_Issues | age_50_60 | 86.37 | 0.310 | 86.37% is 1.43 points below best and 3.18 points above worst. |
| Communications | age_50_60 | 88.81 | 0.296 | 88.81% is 1.18 points below best and 2.80 points above worst. |
| Care_of_Patients | age_50_60 | 91.27 | 0.235 | 91.27% is 0.94 points below best and 3.08 points above worst. |
| PPH | age_60_70 | 9.77 | 0.317 | 9.77% is 3.00 points above best and 6.47 points below worst. |
| DTC | age_60_70 | 75.57 | 0.314 | 75.57% is 2.64 points below best and 5.77 points above worst. |
| DFS | age_60_70 | 77.09 | 0.314 | 77.09% is 2.53 points below best and 5.51 points above worst. |
| Oral_Meds | age_60_70 | 89.26 | 0.348 | 89.26% is 2.66 points below best and 4.97 points above worst. |
| Dyspnea | age_60_70 | 93.29 | 0.255 | 93.29% is 1.65 points below best and 4.84 points above worst. |
| Recommend | age_60_70 | 81.14 | 0.237 | 81.14% is 1.03 points below best and 3.31 points above worst. |
| Agency_Rating | age_60_70 | 87.35 | 0.290 | 87.35% is 1.31 points below best and 3.20 points above worst. |
| Care_Issues | age_60_70 | 86.17 | 0.355 | 86.17% is 1.64 points below best and 2.98 points above worst. |
| Communications | age_60_70 | 88.65 | 0.336 | 88.65% is 1.34 points below best and 2.64 points above worst. |
| Care_of_Patients | age_60_70 | 91.13 | 0.269 | 91.13% is 1.08 points below best and 2.94 points above worst. |
| PPH | age_70_80 | 10.28 | 0.371 | 10.28% is 3.52 points above best and 5.95 points below worst. |
| DTC | age_70_80 | 75.15 | 0.363 | 75.15% is 3.05 points below best and 5.36 points above worst. |
| DFS | age_70_80 | 76.69 | 0.365 | 76.69% is 2.93 points below best and 5.10 points above worst. |
| Oral_Meds | age_70_80 | 88.79 | 0.409 | 88.79% is 3.12 points below best and 4.51 points above worst. |
| Dyspnea | age_70_80 | 93.01 | 0.298 | 93.01% is 1.94 points below best and 4.56 points above worst. |
| Recommend | age_70_80 | 81.03 | 0.262 | 81.03% is 1.14 points below best and 3.21 points above worst. |
| Agency_Rating | age_70_80 | 87.21 | 0.320 | 87.21% is 1.44 points below best and 3.06 points above worst. |
| Care_Issues | age_70_80 | 85.93 | 0.406 | 85.93% is 1.87 points below best and 2.74 points above worst. |
| Communications | age_70_80 | 88.47 | 0.383 | 88.47% is 1.52 points below best and 2.46 points above worst. |
| Care_of_Patients | age_70_80 | 90.97 | 0.309 | 90.97% is 1.24 points below best and 2.78 points above worst. |
| PPH | age_80_plus | 10.80 | 0.426 | 10.80% is 4.03 points above best and 5.44 points below worst. |
| DTC | age_80_plus | 74.74 | 0.412 | 74.74% is 3.47 points below best and 4.95 points above worst. |
| DFS | age_80_plus | 76.28 | 0.415 | 76.28% is 3.33 points below best and 4.70 points above worst. |
| Oral_Meds | age_80_plus | 88.32 | 0.471 | 88.32% is 3.59 points below best and 4.04 points above worst. |
| Dyspnea | age_80_plus | 92.73 | 0.341 | 92.73% is 2.22 points below best and 4.28 points above worst. |
| Recommend | age_80_plus | 80.93 | 0.287 | 80.93% is 1.25 points below best and 3.10 points above worst. |
| Agency_Rating | age_80_plus | 87.08 | 0.350 | 87.08% is 1.58 points below best and 2.93 points above worst. |
| Care_Issues | age_80_plus | 85.69 | 0.458 | 85.69% is 2.11 points below best and 2.50 points above worst. |
| Communications | age_80_plus | 88.28 | 0.430 | 88.28% is 1.71 points below best and 2.27 points above worst. |
| Care_of_Patients | age_80_plus | 90.81 | 0.349 | 90.81% is 1.40 points below best and 2.62 points above worst. |

Reliability affects distribution uncertainty in the configured analytic model. The current assignment uses mean need, so uncertainty alone does not change routing.
