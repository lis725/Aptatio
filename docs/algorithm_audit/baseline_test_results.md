# Phase 0 Baseline Test Results

Captured before implementation changes on 2026-07-15. The repository itself is the source of truth for paths and available suites.

## Repository state

- Workspace: `C:\Users\iced_lemonade\Documents\Aptatio Project`
- The requested `backend/backend_interface.py` path does not exist. The protected integration file is the repository-root `backend_interface.py`.
- A `.git` directory exists but is empty. Both `git status` and `git rev-parse HEAD` fail with `fatal: not a git repository`; consequently no pre-change commit or tracked-worktree status is available.
- Existing automated tests: one module, `tests/test_public_age_zip_threshold.py`, containing 12 `unittest` cases.
- Existing report-generator tests: none.
- Existing experimental test suites: none.
- Existing PDF/LaTeX report generators: none.

## Protected backend interface baseline

- File: `backend_interface.py`
- Size: 2,895 bytes
- SHA-256: `4BE0C46769FC637C3F77D4008A6CB3624B17B7F628E59992F370BF7E6B2D84BB`
- Public entry points:
  - `create_request_payload(patient: dict, clinicians: list[dict], request_id: str | None = None, options: dict[str, Any] | None = None) -> dict`
  - `assign_from_request(request: dict, value_key: str = "clinician_id") -> dict`
  - `assign_from_components(patient: dict, clinicians: list[dict], request_id: str | None = None, options: dict[str, Any] | None = None, value_key: str = "clinician_name") -> dict`
- Exceptions are not caught by the interface; internal `RequestValidationError` (`ValueError`) propagates.
- `create_request_payload` always includes `patient` and `clinicians`, includes any non-`None` `request_id`, and includes `options` only when truthy.

Default output from `examples/example_assignment_request.json`:

```json
{
  "RN": [105, 108, 102],
  "PT": [204, 203, 207],
  "OT": [302, 303, 301]
}
```

The same request through the component helper's default `clinician_name` output:

```json
{
  "RN": ["RN5", "RN8", "RN2"],
  "PT": ["PT4", "Capel, Jenny (PT)", "PT7"],
  "OT": ["OT2", "OT3", "OT1"]
}
```

## Existing test result

Command:

```powershell
$PY = 'C:\Users\iced_lemonade\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:PYTHONDONTWRITEBYTECODE = '1'
& $PY -B -m unittest discover -s tests -p 'test*.py' -v
```

Result: **12 passed, 0 failed, 0 errors** in **1.942 seconds** under Python 3.12.13, NumPy 2.3.5, and pandas 3.0.1.

All four threshold-routing examples also matched `examples/threshold_routing_expected_results.md`:

- `threshold_high_full_pool_01.json`
- `threshold_high_full_pool_02.json`
- `threshold_low_zip_history_01.json`
- `threshold_low_fallback_02.json`

## Pre-change defect evidence

- Clinician metric percentiles were ranked over the mixed RN/PT/OT roster. Adding unrelated OT rows changed RN/PT ranks and, for a lower-risk example, changed the RN recommendation from `[101, 107, 103]` to `[103, 107, 101]`.
- The existing 101-threshold sensitivity artifact contained one distinct TPS proxy, workload spread, and objective value. The implementation changed only the high-risk mask while reusing assignments and outcomes.
- Runtime threshold routing was disabled, raw aggregate risk `rho` was used as though it were a percentile, the reference cohort contained only 50 rows, and no 2,160-profile structural grid existed.

## Existing artifact inventory

- `data/processed/`: 50 patients/episodes, 25 clinicians (12 RN, 10 PT, 3 OT), 25 synthetic ZIP-history rows, and one calibration-ready cohort.
- `reports/dataset/`: a constant 101-row threshold sensitivity CSV plus small JSON/Markdown dataset summaries.
- `tmp/`: orphaned preview images and nontechnical LaTeX fragments with stale six-factor/raw-risk wording; no current PDF files.
- No Reno, all-other-metrics, age-sensitivity, sponsor, nontechnical, structural-experiment, policy-bake-off, or capacity-simulation generators existed.

Generated artifacts were retained for comparison. None were deleted during baseline capture.
