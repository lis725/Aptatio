# Public-Only Data Pipeline

The active checked-in build uses `offline_demo_fallback`. The official pages in `data/manifests/public_data_manifest.example.json` identify optional CMS, ACS, CDC PLACES, USDA RUCA, Zillow ZHVI, Census Gazetteer, NOAA, and disabled SynPUF inputs, but no source file is represented as downloaded. Each manifest entry records retrieval status, expected cache location, checksum status, columns that would be used, geography/year, and the intended transformation.

The deterministic fallback has two distinct products:

1. `data/reference/hhvbp_risk_reference.json`: 10,000 weighted Monte Carlo profiles used only to convert raw modeled need into an empirical reference percentile.
2. `data/reference/hhvbp_structural_grid.csv`: all 2,160 possible seven-factor profiles, used for structural and counterfactual analysis rather than population weighting.

Synthetic clinician ZIP history is labeled `synthetic_public_only`. Public agency-level outcomes, when later supplied through a manifest, may calibrate marginal distributions only; they cannot identify patient-clinician causal effects.

## Rebuild commands

```powershell
$PY = (Get-Command python).Source

& $PY scripts\build_risk_reference.py `
  --config-dir config `
  --sample-size 10000 `
  --seed 20260608 `
  --source-mode offline_demo_fallback `
  --creation-timestamp 2026-07-15T00:00:00Z

& $PY scripts\build_public_synthetic_clinicians.py `
  --config config\public_semi_synthetic_dataset_config.json

& $PY scripts\build_public_semi_synthetic_dataset.py `
  --config config\public_semi_synthetic_dataset_config.json `
  --manifest data\manifests\public_data_manifest.example.json
```

Raw public downloads, if later authorized and configured, belong under `data/raw/public/` and are not committed. Add the actual retrieval date, cache path, checksum, columns, and transformation to the manifest before treating an adapter output as a public observation.

## Generated episode data

`data/processed/threshold_calibration_ready_public_synthetic.csv` is the canonical
episode/calibration table and the default input to
`scripts/calibrate_threshold_public_synthetic.py`. The former
`public_synthetic_episodes.csv` contained the same bytes and was removed during
repository cleanup; the generator now writes this table once. Consumers of the
former filename should use the canonical path. Generated files under
`data/processed/` and `reports/dataset/` stay local and are excluded from Git.
