# Reproduction Commands

Commands below are the PowerShell commands used for the finalized artifacts on 2026-07-15. They assume the repository root is the current directory.

```powershell
$PY = 'C:\Users\iced_lemonade\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
```

## Reference and public-only data

```powershell
& $PY -B scripts\build_risk_reference.py --config-dir config --sample-size 10000 --seed 20260608 --source-mode offline_demo_fallback --creation-timestamp 2026-07-15T00:00:00Z
& $PY -B scripts\build_public_synthetic_clinicians.py
& $PY -B scripts\build_public_semi_synthetic_dataset.py
& $PY -B scripts\calibrate_threshold_public_synthetic.py --max-calibration-rows 500 --lambda-equity 10
```

The deterministic calibration sample is drawn without replacement with `random_state=20260608`; both the 10,000-row input count and 500-row analysis count are written to the selected-threshold JSON.

## Finalized experiment tiers

```powershell
# Major P4 scenarios: 9 runs, 5,000 patients each, fixed pilot threshold.
& $PY -B scripts\run_batch_policy_experiments.py --output-dir reports\experiments\major_scenarios_5000 --n-patients 5000 --n-seeds 1 --thresholds 0.75 --scenarios A,B,C,D,E,F,G,H,I --policies P4

# Fine P4 threshold grid: 1,818 runs, 100 patients, selection seed 0 and held-out seed 1.
& $PY -B scripts\run_batch_policy_experiments.py --output-dir reports\experiments\threshold_fine_grid --n-patients 100 --n-seeds 2 --thresholds 0:1:0.01 --scenarios A,B,C,D,E,F,G,H,I --policies P4

# Full policy bake-off: 144 runs, 500 patients, 2 seeds, fixed pilot threshold.
& $PY -B scripts\run_batch_policy_experiments.py --output-dir reports\experiments\policy_bakeoff --n-patients 500 --n-seeds 2 --thresholds 0.75 --scenarios A,B,C,D,E,F,G,H,I --policies P0,P1,P2,P3,P4,P5,P6,P7

# Operational stress factorial: 144 unique runs; sensitivity-only because it uses one seed.
& $PY -B scripts\run_batch_policy_experiments.py --output-dir reports\experiments\operational_stress_grid --n-patients 200 --n-seeds 1 --thresholds 0.25,0.75 --scenarios F,G,H --policies P4,P5,P6 --roster-scales 0.75,1.25 --capacity-levels 0.7,1.2 --zip-sparsity-levels 0.2,0.8

# Exact 2,160-profile structural audit and actual-policy ablation/fairness reruns.
& $PY -B scripts\run_batch_policy_experiments.py --output-dir reports\experiments\structural_audit --n-patients 10 --n-seeds 1 --thresholds 0.75 --scenarios A --policies P4 --structural
```

## Reports and PDFs

```powershell
& $PY -B scripts\generate_reports.py
& $PY -B scripts\generate_reports.py --check-claims-only
& $PY -B scripts\generate_pdf_reports.py
```

Final PDF page images were rendered for visual inspection with bundled Poppler:

```powershell
$PDFTOPPM = 'C:\Users\iced_lemonade\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin\pdftoppm.exe'
New-Item -ItemType Directory -Force -Path tmp\pdf_final_verification_20260715_134916 | Out-Null
Get-ChildItem output\pdf\*.pdf | ForEach-Object {
    & $PDFTOPPM -jpeg -r 150 $_.FullName (Join-Path tmp\pdf_final_verification_20260715_134916 $_.BaseName)
}
```

## Contract, examples, and full tests

```powershell
Get-FileHash backend_interface.py -Algorithm SHA256
& $PY -B run_simple.py --request examples\example_assignment_request.json
& $PY -B -m unittest discover -s tests -p 'test*.py' -v
```

The final observed pass/fail count, runtime, protected-file checksum, and PDF page audit are recorded in `FINAL_TEST_RESULTS.md`.
