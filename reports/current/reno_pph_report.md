# Reno PPH Scenario and Sensitivity Report

**Model lock:** Expanded HHVBP, CY2025 performance / CY2027 payment, measure set CY2025 (version locked).

**Validation boundary:** Public-data-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated.

These results describe configured model scenarios. They do not establish real-world clinical effects, payment effects, or a uniquely best assignment policy.

## Scope and controls

Every geographic comparison fixes `age_group=age_70_80`, health status at `moderate`, housing at `single_home`, and driving at `good_summer`. Configured house-price category, area type, and distance category may vary by ZIP scenario.

The ZIP below is a profiling label used to select configured scenario attributes. It is not a patient assignment ZIP, no clinician ZIP-history pool is built here, and `assignment_zip_routing_applied=false` for every row. Assignment-time ZIP routing is a separate operational rule described in the current-model report.

The displayed PPH values are analytic model distributions under configured assumptions, not empirical patient-level Reno estimates. Because PPH is lower-is-better, distance wording is “points above best” and “points below worst.”

## Controlled geographic scenarios

| Profiling ZIP | Area | House-price group | Distance | Mean (%) | Q10 | Q90 | Distance to anchors | rho raw | u percentile |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 89501 | metropolitan | average | near | 10.28 | 8.79 | 11.78 | 10.28% is 3.52 points above best and 5.95 points below worst. | 0.358 | 0.299 |
| 89502 | metropolitan | low | near | 11.07 | 9.60 | 12.54 | 11.07% is 4.30 points above best and 5.17 points below worst. | 0.420 | 0.453 |
| 89503 | metropolitan | average | near | 10.28 | 8.79 | 11.78 | 10.28% is 3.52 points above best and 5.95 points below worst. | 0.358 | 0.299 |
| 89506 | metropolitan | average | near | 10.28 | 8.79 | 11.78 | 10.28% is 3.52 points above best and 5.95 points below worst. | 0.358 | 0.299 |
| 89509 | metropolitan | high | near | 9.50 | 8.03 | 10.97 | 9.50% is 2.73 points above best and 6.74 points below worst. | 0.295 | 0.168 |
| 89523 | metropolitan | high | near | 9.50 | 8.03 | 10.97 | 9.50% is 2.73 points above best and 6.74 points below worst. | 0.295 | 0.168 |
| 89431 | metropolitan | low | near | 11.07 | 9.60 | 12.54 | 11.07% is 4.30 points above best and 5.17 points below worst. | 0.420 | 0.453 |
| 89433 | metropolitan | average | near | 10.28 | 8.79 | 11.78 | 10.28% is 3.52 points above best and 5.95 points below worst. | 0.358 | 0.299 |
| 89434 | metropolitan | average | near | 10.28 | 8.79 | 11.78 | 10.28% is 3.52 points above best and 5.95 points below worst. | 0.358 | 0.299 |
| 89436 | micropolitan | average | medium | 10.45 | 8.96 | 11.94 | 10.45% is 3.69 points above best and 5.79 points below worst. | 0.391 | 0.380 |

## Interpretation boundary

`risk_score_rho_raw` is a weighted modeled-need index. `risk_percentile_u` is its relative position against the checked-in 10,000-profile public-only semi-synthetic reference. Neither is a calibrated clinical event probability. The strict pilot classification is above 0.75; a value equal to 0.75 remains at or below threshold.

Scenario inputs come from the checked-in offline demo market table. Blank public source fields are not interpreted as downloaded observations.
