# Aptatio Expanded HHVBP CY2025 Assignment Prototype

This repository recommends RN, PT, and OT clinicians for a home-health patient while preserving the existing backend-facing contract. The production/demo algorithm is intentionally locked to the Expanded HHVBP **CY2025 performance year / CY2027 payment year** measure set.

All validation in this repository is public-data-only, expert-configured, semi-synthetic, and simulation-based. It is not outcome-validated and does not establish causal clinician effects, patient benefit, payment improvement, or a real-world optimal threshold.

## Quick start

Use Python 3.12 (the verified runtime). From the repository root:

```sh
python -m venv .venv
```

Activate the environment with `.venv\Scripts\Activate.ps1` in PowerShell, or
`source .venv/bin/activate` on macOS/Linux, then run:

```sh
python -m pip install -r requirements.txt
python -B run_simple.py --request examples/example_assignment_request.json
python -B -m unittest discover -s tests -p 'test*.py' -v
```

The example and tests use the included reference data. Generated datasets are
local outputs; rebuild them only when running the data pipeline or calibration.
See [repository layout](docs/REPOSITORY_LAYOUT.md) for the file organization and cleanup record.

## Backend contract

The integration entry points remain unchanged:

```python
from backend_interface import assign_from_request, assign_from_components
```

Their simplified response remains:

```json
{"RN": [...], "PT": [...], "OT": [...]}
```

`backend_interface.py` is protected by a byte-level contract test. Rich metadata is available only through internal `assign_clinicians()` / `assign_with_audit()` in `src/hhvbp_assignment_service.py`.

## Active algorithm

Patient need uses exactly seven factors:

1. `health_status`
2. `age_group`
3. `house_price_zip`
4. `area_type`
5. `housing_type`
6. `distance_to_clinic`
7. `driving_condition`

Numeric age 80 maps to `age_70_80`; the indivisible raw band `80_89` maps to `age_80_plus`. Supported raw aliases also include `40_49`, `50_59`, `60_69`, `70_79`, `80_plus`, `80+`, `90_plus`, and `90+`.

The internal audit keeps two distinct quantities:

- `risk_score_rho_raw`: aggregate modeled need index; not a probability.
- `risk_percentile_u`: empirical percentile of the raw index against `data/reference/hhvbp_risk_reference.json`.

Clinician metric percentiles are computed **within discipline** on the full request roster before any ZIP filtering. The same-discipline request roster is the default normalization reference, so same-discipline roster changes can still move percentiles; unrelated disciplines cannot.

Default routing uses the sponsor-requested hard-ZIP pilot:

- `risk_percentile_u > 0.75`: full discipline pool (`global_high_risk`).
- `risk_percentile_u <= 0.75`: ZIP-history pool when it has at least RN=3, PT=3, or OT=1 clinicians.
- Insufficient ZIP coverage or a missing patient ZIP: audited full-pool fallback.
- RN/PT use percentile-mirrored ordering inside the selected pool.
- OT retains deterministic rotation inside its selected pool.

The mirrored anchor is:

```text
round_half_up((1 - risk_percentile_u) * (n_selected - 1))
```

Candidate order is anchor, anchor-1, anchor+1, anchor-2, anchor+2, subject to valid bounds.

## CY2025 metrics

The locked weights are DFS 0.20, Dyspnea 0.06, Oral Medications 0.09, DTC-PAC 0.09, PPH 0.26, and five HHCAHPS measures at 0.06 each. They sum exactly to 1.00. Canonical aliases prevent DFS/DC Function, DTC/DTC-PAC, Agency Rating/Overall Rating, Recommend/Willingness to Recommend, Care Issues/Specific Care Issues, and Communications labels from being omitted or double-counted.

See `docs/cms_version_notes.md` and `docs/algorithm_audit/CY2025_CONFIGURATION.md`. CY2026 migration is out of scope.

## Deterministic reference and public-only pipeline

The checked-in offline fallback contains:

- 10,000 weighted Monte Carlo profiles for the empirical risk reference;
- the exhaustive 2,160-profile structural grid;
- seed, creation timestamp, configuration hash, source mode, factor distributions, quantiles, and checksums;
- `tau_raw = 0.5696213523887566` for the 0.75 pilot percentile.

Official public-source pages and explicit not-retrieved fallback metadata are recorded in `data/manifests/public_data_manifest.example.json`. Synthetic clinician ZIP history is labeled `synthetic_public_only`.

## Batch simulation and experiments

`src/hhvbp_batch_experiments.py` compares:

- P0 deterministic round-robin;
- P1 highest-scoring feasible;
- P2 historical raw-rho mirror;
- P3 corrected percentile mirror without ZIP routing;
- P4 hard-ZIP sponsor policy;
- P5 soft-ZIP bonus;
- P6 capacity-constrained greedy optimization;
- P7 latent-quality capacity-feasible greedy comparator.

Scenarios A-I cover additive effects, positive/negative risk-quality interaction, no clinician effect, no/positive ZIP benefit, sparse ZIPs, capacity shortage, and score uncertainty. Every threshold reruns routing, pools, assignment, capacity consumption, workload, and outcomes with common random numbers.

Regret is computed against a separately calculated unconstrained latent-information
upper bound. P7 is an included, capacity-feasible comparator and is never labeled or
used as the oracle. Threshold recommendations require distinct selection and held-out
seeds; one-seed grids are reported only as in-sample sensitivity analyses. The runtime
pilot remains 0.75.

The CLI defaults expose the requested 5,000-patient / 100-seed / 101-threshold design, but that full Cartesian run is intentionally not implied to be cheap:

```powershell
$PY = (Get-Command python).Source

& $PY scripts\run_batch_policy_experiments.py --quick --structural
& $PY scripts\run_batch_policy_experiments.py --n-patients 5000 --n-seeds 100 --thresholds 0:1:0.01 --structural
```

Generated machine-readable results live under `reports/experiments/`. P6 remains experimental and does not replace P4.

## Rebuild and test

```powershell
$PY = (Get-Command python).Source

& $PY scripts\build_risk_reference.py --config-dir config --sample-size 10000 --seed 20260608 --source-mode offline_demo_fallback --creation-timestamp 2026-07-15T00:00:00Z
& $PY scripts\build_public_synthetic_clinicians.py
& $PY scripts\build_public_semi_synthetic_dataset.py
& $PY scripts\calibrate_threshold_public_synthetic.py --max-calibration-rows 500 --lambda-equity 10
& $PY scripts\generate_reports.py
& $PY scripts\generate_pdf_reports.py
& $PY -B -m unittest discover -s tests -p 'test*.py' -v
```

See `docs/public_data_pipeline.md`, `docs/data_dictionary.md`, and `docs/algorithm_audit/` for methods, results, limitations, the contract snapshot, and exact audit commands.
