# Changelog

## 2026-07-15 - CY2025 algorithm audit and simulation release

`backend_interface.py` was not changed. The paths below are every source, configuration, test, documentation, data, experiment, report, and PDF release file changed or generated for this audit. Ephemeral render/CLI inspection files under `tmp/` are verification scratch, not release artifacts.

### Runtime, configuration, and pipeline code

- `README.md`
- `config/assignment_parameters.json`
- `config/hhvbp_global_config.json`
- `config/metric_parameter_library.json`
- `config/public_semi_synthetic_dataset_config.json`
- `data/manifests/public_data_manifest.example.json`
- `scripts/build_public_semi_synthetic_dataset.py`
- `scripts/build_risk_reference.py`
- `scripts/calibrate_threshold_public_synthetic.py`
- `scripts/generate_pdf_reports.py`
- `scripts/generate_reports.py`
- `scripts/run_batch_policy_experiments.py`
- `src/data_adapters/public_adapter_base.py`
- `src/hhvbp_assignment_service.py`
- `src/hhvbp_batch_experiments.py`
- `src/hhvbp_local_externality.py`
- `src/hhvbp_reporting.py`
- `src/hhvbp_risk_reference.py`
- `src/hhvbp_threshold_calibration.py`
- `src/public_synthetic_data.py`

### Tests

- `tests/test_assignment_invariants.py`
- `tests/test_backend_interface_contract.py`
- `tests/test_batch_policy_experiments.py`
- `tests/test_cy2025_configuration_and_age.py`
- `tests/test_pdf_reporting.py`
- `tests/test_public_age_zip_threshold.py`
- `tests/test_reporting.py`
- `tests/test_risk_reference.py`
- `tests/test_stability_provenance_and_claims.py`

### Documentation and audit logs

- `CHANGELOG.md`
- `docs/cms_version_notes.md`
- `docs/data_dictionary.md`
- `docs/public_data_pipeline.md`
- `docs/algorithm_audit/ALGORITHM_DEFECTS_AND_FIXES.md`
- `docs/algorithm_audit/BACKEND_INTERFACE_CONTRACT.md`
- `docs/algorithm_audit/CY2025_CONFIGURATION.md`
- `docs/algorithm_audit/EXPERIMENT_METHODS.md`
- `docs/algorithm_audit/EXPERIMENT_RESULTS.md`
- `docs/algorithm_audit/FINAL_TEST_RESULTS.md`
- `docs/algorithm_audit/IMPLEMENTATION_SUMMARY.md`
- `docs/algorithm_audit/LIMITATIONS.md`
- `docs/algorithm_audit/REPRODUCTION_COMMANDS.md`
- `docs/algorithm_audit/UNRESOLVED_PROJECT_DECISIONS.md`
- `docs/algorithm_audit/baseline_test_results.md`
- `examples/threshold_routing_expected_results.md`

### Deterministic reference and public-only data

- `data/reference/hhvbp_risk_reference.json`
- `data/reference/hhvbp_structural_grid.csv`
- `data/reference/hhvbp_structural_grid.metadata.json`
- `data/processed/public_synthetic_clinicians.csv`
- `data/processed/public_synthetic_episodes.csv`
- `data/processed/public_synthetic_patients.csv`
- `data/processed/public_synthetic_selected_threshold.json`
- `data/processed/public_synthetic_zip_history.csv`
- `data/processed/threshold_calibration_ready_public_synthetic.csv`
- `reports/dataset/public_data_provenance.json`
- `reports/dataset/public_synthetic_clinician_report.json`
- `reports/dataset/public_synthetic_dataset_report.json`
- `reports/dataset/public_synthetic_dataset_report.md`
- `reports/dataset/public_synthetic_threshold_report.md`
- `reports/dataset/public_synthetic_threshold_sensitivity.csv`

### Machine-readable experiment evidence

- `reports/experiments/major_scenarios_5000/experiment_metadata.json`
- `reports/experiments/major_scenarios_5000/EXPERIMENT_SUMMARY.md`
- `reports/experiments/major_scenarios_5000/policy_threshold_results.csv`
- `reports/experiments/major_scenarios_5000/threshold_recommendations.json`
- `reports/experiments/major_scenarios_5000/threshold_summary.csv`
- `reports/experiments/threshold_fine_grid/experiment_metadata.json`
- `reports/experiments/threshold_fine_grid/EXPERIMENT_SUMMARY.md`
- `reports/experiments/threshold_fine_grid/policy_threshold_results.csv`
- `reports/experiments/threshold_fine_grid/threshold_recommendations.json`
- `reports/experiments/threshold_fine_grid/threshold_summary.csv`
- `reports/experiments/policy_bakeoff/experiment_metadata.json`
- `reports/experiments/policy_bakeoff/EXPERIMENT_SUMMARY.md`
- `reports/experiments/policy_bakeoff/policy_threshold_results.csv`
- `reports/experiments/policy_bakeoff/threshold_recommendations.json`
- `reports/experiments/policy_bakeoff/threshold_summary.csv`
- `reports/experiments/operational_stress_grid/experiment_metadata.json`
- `reports/experiments/operational_stress_grid/EXPERIMENT_SUMMARY.md`
- `reports/experiments/operational_stress_grid/policy_threshold_results.csv`
- `reports/experiments/operational_stress_grid/threshold_recommendations.json`
- `reports/experiments/operational_stress_grid/threshold_summary.csv`
- `reports/experiments/structural_audit/experiment_metadata.json`
- `reports/experiments/structural_audit/EXPERIMENT_SUMMARY.md`
- `reports/experiments/structural_audit/policy_threshold_results.csv`
- `reports/experiments/structural_audit/structural_counterfactual_fairness.csv`
- `reports/experiments/structural_audit/structural_diagnostics.json`
- `reports/experiments/structural_audit/structural_factor_ablation.csv`
- `reports/experiments/structural_audit/structural_profiles.csv`
- `reports/experiments/structural_audit/structural_reliability_sensitivity.csv`
- `reports/experiments/structural_audit/threshold_recommendations.json`
- `reports/experiments/structural_audit/threshold_summary.csv`

### Regenerated current reports

- `reports/current/README.md`
- `reports/current/current_model_report.md`
- `reports/current/nontechnical_report.md`
- `reports/current/reno_age_sensitivity.csv`
- `reports/current/reno_age_sensitivity.json`
- `reports/current/reno_age_sensitivity_report.md`
- `reports/current/reno_other_metrics_report.md`
- `reports/current/reno_other_metrics_scenarios.csv`
- `reports/current/reno_other_metrics_scenarios.json`
- `reports/current/reno_pph_report.md`
- `reports/current/reno_pph_scenarios.csv`
- `reports/current/reno_pph_scenarios.json`
- `reports/current/report_manifest.json`
- `reports/current/sponsor_report.md`

### Regenerated PDFs

- `output/pdf/current_model_report.pdf`
- `output/pdf/nontechnical_report.pdf`
- `output/pdf/pdf_manifest.json`
- `output/pdf/reno_age_sensitivity_report.pdf`
- `output/pdf/reno_other_metrics_report.pdf`
- `output/pdf/reno_pph_report.pdf`
- `output/pdf/sponsor_report.pdf`

### Principal behavior changes

- Locked active runtime configuration to Expanded HHVBP CY2025 performance year / CY2027 payment year.
- Corrected within-discipline pre-ZIP ranking, alias handling, raw-risk/ECDF separation, strict percentile routing, ZIP fallback, tie-breaking, and audit provenance.
- Rebuilt the 10,000-profile reference and exact 2,160-profile structural grid.
- Replaced constant threshold masking with full policy reruns and held-out evaluation.
- Separated the analytical regret upper bound from feasible P7, made rerun IDs design-specific, and replaced proxy structural diagnostics with actual P4 reruns.
- Kept P4 and percentile 0.75 as the default; P5/P6 remain experimental.
