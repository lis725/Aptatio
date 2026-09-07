# Current Report Artifact Index

**Model lock:** Expanded HHVBP, CY2025 performance / CY2027 payment, measure set CY2025 (version locked).

**Validation boundary:** Public-data-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated.

These results describe configured model scenarios. They do not establish real-world clinical effects, payment effects, or a uniquely best assignment policy.

## Human-readable reports

- [Reno PPH report](reno_pph_report.md)
- [Reno all-other-metrics report](reno_other_metrics_report.md)
- [Reno five-age sensitivity report](reno_age_sensitivity_report.md)
- [Nontechnical explanation](nontechnical_report.md)
- [Current-model report](current_model_report.md)
- [Sponsor review brief](sponsor_report.md)

## Machine-readable scenario data

- `reno_pph_scenarios.csv` and `reno_pph_scenarios.json`
- `reno_other_metrics_scenarios.csv` and `reno_other_metrics_scenarios.json`
- `reno_age_sensitivity.csv` and `reno_age_sensitivity.json`

## Current PDFs

Run `python scripts/generate_pdf_reports.py` after this source generator. It writes six current PDFs and `pdf_manifest.json` under `output/pdf/`, then links that manifest back into `report_manifest.json`.

`report_manifest.json` records source hashes, output hashes, model lock, and the unsupported-claim scan result. `pdf_or_latex_generated` remains false until the separate PDF step succeeds.
