# Final Test Results

## Outcome

Final verification completed on 2026-07-15 in the bundled Windows workspace runtime.

- Automated tests: **107 passed, 0 failed, 0 skipped**.
- `unittest` runtime: **200.184 seconds** (tool wall time 201.1 seconds).
- Unsupported-claim scan: **passed**, 14 current report files scanned.
- Protected backend contract: **passed**.
- Final experiment integrity audit: **passed** for all five tiers.
- Final PDF source/hash/link/page audit: **passed**.

Baseline before implementation was 12 tests passing in 1.942 seconds. Existing tests remained green and the suite grew by 95 regression tests.

## Exact full-suite command

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\iced_lemonade\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s tests -p 'test*.py' -v
```

Observed footer:

```text
Ran 107 tests in 200.184s

OK
```

## Protected backend contract

`backend_interface.py` remained byte-for-byte unchanged:

- Bytes: `2,895`
- Baseline and final SHA-256: `4BE0C46769FC637C3F77D4008A6CB3624B17B7F628E59992F370BF7E6B2D84BB`
- Filesystem modification time remained `2026-04-19 05:02:32`.

Final public signatures:

```text
assign_from_request(request: 'dict', value_key: 'str' = 'clinician_id') -> 'dict'
assign_from_components(patient: 'dict', clinicians: 'list[dict]', request_id: 'str | None' = None, options: 'dict[str, Any] | None = None, value_key: 'str' = 'clinician_name') -> 'dict'
```

The default response remains exactly an object with `RN`, `PT`, and `OT` list values.

## Manual example replay

The bundled base request and all four threshold-routing examples executed successfully through `run_simple.py`. The four routing outputs match `examples/threshold_routing_expected_results.md` after that document was corrected from historical raw-rho anchoring to persisted-percentile anchoring. Routes were full pool for both high-risk examples, ZIP history for the covered lower-risk example, and audited full-pool fallback for the sparse lower-risk example.

## Data and experiment integrity

- Risk reference: 10,000 sorted scores; current config/component hashes and score-bound reference ID validated.
- Exact structural grid: 2,160 unique Cartesian profiles; reference/grid sidecar SHA-256 bindings validated.
- Public-only build: 10,000 patient rows, 10,000 episode rows, and 10,000 calibration-ready rows.
- Public calibration: 202 rows (101 candidates x selection/held-out); 500 deterministic analysis rows recorded from 10,000 input rows; selected simulation threshold 0.79; runtime threshold remains 0.75.

Final batch rows:

| Tier | Rows | Unique rerun IDs | Candidate raw-threshold equality | Upper-bound violations |
|---|---:|---:|---:|---:|
| Major scenarios | 9 | 9 | exact | 0 |
| Fine threshold grid | 1,818 | 1,818 | maximum error `1.11e-16` | 0 |
| Policy bake-off | 144 | 144 | exact | 0 |
| Operational stress | 144 | 144 | maximum error `5.55e-17` | 0 |
| Structural audit batch row | 1 | 1 | exact | 0 |

Every tier records the current batch implementation hash `b044ca084fe51c267257de7013ef66aa2f04df56a0005dffa9dfdcf4f21f1393`, current persisted reference identity, `public_data_only=true`, and `production_validated=false`.

## Reports and PDFs

`scripts/generate_reports.py` produced 14 current artifacts and its unsupported-claim scan passed. Experiment evidence loading validated each present tier's metadata, role, design, implementation hashes, reference identity, and evidence-boundary flags before producing sponsor claims.

Six PDFs were regenerated with ReportLab and rendered at 150 DPI with bundled Poppler for page-by-page visual inspection:

| PDF | Pages |
|---|---:|
| Current model | 2 |
| Nontechnical report | 1 |
| Reno age sensitivity | 2 |
| Reno other metrics | 4 |
| Reno PPH | 1 |
| Sponsor brief | 1 |
| **Total** | **11** |

All 11 final raster hashes matched the visually inspected pages. No clipping, overflow, broken tables, or stale wording was observed. Poppler emitted non-fatal lookup warnings for `Symbol`/`ArialUnicode`; the PDFs intentionally use ASCII-safe Helvetica/Courier content, and rendered pages were complete.

The PDF manifest validates all six source Markdown SHA-256 values, PDF SHA-256 values/byte counts, deterministic manifest identity, source-bundle identity, and the `current` link in `reports/current/report_manifest.json`. Both manifests retain `public_data_only=true` and `production_validated=false`.

## Focused regression evidence

In addition to the final full run:

- Risk/assignment provenance suites: 27/27 passed in 6.284 seconds.
- Backend/public contract suites: 20/20 passed in 11.532 seconds.
- Corrected public calibration suite: 15/15 passed in 8.417 seconds.
- Reporting/PDF provenance suites: 20/20 passed in 1.519 seconds.

Exact rebuild and verification commands are in `REPRODUCTION_COMMANDS.md`.
