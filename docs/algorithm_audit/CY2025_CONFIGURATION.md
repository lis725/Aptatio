# Expanded HHVBP CY2025 Configuration Audit

## Version lock

| Field | Locked value |
|---|---:|
| `cms_model` | Expanded HHVBP |
| `performance_year` | 2025 |
| `payment_year` | 2027 |
| `measure_set_version` | CY2025 |
| `cms_version_locked` | true |

This is the production/demo baseline intentionally frozen for sponsor review. CY2026 migration is out of scope, and the active library contains no CY2026 measures.

## Active measures and weights

| Canonical key | CY2025 measure | Weight |
|---|---|---:|
| `DFS` | Discharge Function Score / DC Function | 0.20 |
| `Dyspnea` | Improvement in Dyspnea | 0.06 |
| `Oral_Meds` | Improvement in Management of Oral Medications | 0.09 |
| `DTC` | Discharge to Community—Post Acute Care / DTC-PAC | 0.09 |
| `PPH` | Potentially Preventable Hospitalization | 0.26 |
| `Care_of_Patients` | Care of Patients | 0.06 |
| `Communications` | Communications Between Providers and Patients | 0.06 |
| `Care_Issues` | Specific Care Issues | 0.06 |
| `Agency_Rating` | Overall / Agency Rating | 0.06 |
| `Recommend` | Willingness to Recommend | 0.06 |
| **Total** | **Ten active measures** | **1.00** |

`config/metric_parameter_library.json` stores one `canonical_metric_aliases` list per canonical key. Each list includes its canonical key and supported names such as DFS/DC Function, DTC/DTC-PAC, Agency_Rating/Overall Rating, Recommend/Willingness to Recommend, Care_Issues/Specific Care Issues, and Communications/Communications Between Providers and Patients. The loader treats a second alias for an already supplied canonical metric as a duplicate rather than a second measure, and configuration validation rejects aliases claimed by two canonical metrics.

## Seven patient-side factors

The active factor order is exactly:

1. `health_status`
2. `age_group`
3. `house_price_zip`
4. `area_type`
5. `housing_type`
6. `distance_to_clinic`
7. `driving_condition`

Configuration validation rejects missing, extra, or reordered active factors. Age remains a risk/need factor, not an access restriction.

## Age configuration

Age reliability is `0.95`. Base favorability is:

| Canonical category | Favorability |
|---|---:|
| `age_40_50` | 0.72 |
| `age_50_60` | 0.62 |
| `age_60_70` | 0.50 |
| `age_70_80` | 0.36 |
| `age_80_plus` | 0.22 |

The best category is `age_40_50`; the worst category is `age_80_plus`.

### Numeric ages

| Numeric rule | Canonical category |
|---|---|
| `40 <= age < 50` | `age_40_50` |
| `50 <= age < 60` | `age_50_60` |
| `60 <= age < 70` | `age_60_70` |
| `70 <= age <= 80` | `age_70_80` |
| `age > 80` | `age_80_plus` |

A numeric age below 40, a nonnumeric unsupported value, or a nonfinite value continues to use the project’s existing validation error behavior. No backend-interface change is required.

### Raw database bands

| Raw band | Canonical category |
|---|---|
| `40_49` | `age_40_50` |
| `50_59` | `age_50_60` |
| `60_69` | `age_60_70` |
| `70_79` | `age_70_80` |
| `80_89` | `age_80_plus` |
| `80_plus` or `80+` | `age_80_plus` |
| `90_plus` or `90+` | `age_80_plus` |

The boundary distinction is intentional: **numeric age 80 maps to `age_70_80`, while an indivisible raw database band `80_89` maps to `age_80_plus`**. The raw band cannot be split into age 80 versus ages above 80, so it follows the conservative older-band alias specified for this prototype.

## Metric-level age elicitation

Every metric uses `age_40_50` as its best age category and `age_80_plus` as its worst.

| Metric | Importance | Low | Mode | High |
|---|---|---:|---:|---:|
| `PPH` | strong | 60 | 75 | 90 |
| `DTC` | strong | 55 | 70 | 85 |
| `DFS` | strong | 50 | 65 | 80 |
| `Oral_Meds` | medium | 45 | 60 | 75 |
| `Dyspnea` | medium | 35 | 50 | 65 |
| `Recommend` | weak | 20 | 30 | 40 |
| `Agency_Rating` | weak | 20 | 30 | 40 |
| `Care_Issues` | medium | 35 | 45 | 55 |
| `Communications` | medium | 30 | 40 | 50 |
| `Care_of_Patients` | medium | 30 | 40 | 50 |

## Validation scope

The lock and boundary tests establish configuration correctness and deterministic mapping behavior only. The model remains public-data-only, expert-configured, semi-synthetic and simulation-based where patient/clinician data are required, and not outcome-validated. These checks do not establish causal optimality, payment maximization, clinical efficacy, or production readiness.
