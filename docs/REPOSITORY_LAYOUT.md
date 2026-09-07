# Repository layout

| Path | Purpose | Versioned |
| --- | --- | --- |
| `backend_interface.py`, `run_simple.py` | Backend contract and example CLI | Yes |
| `src/` | Assignment, risk, calibration, simulation, and reporting code | Yes |
| `config/` | Model parameters and public-data configuration | Yes |
| `scripts/` | Dataset, experiment, and report commands | Yes |
| `tests/` | Regression and interface tests | Yes |
| `examples/` | Example requests and expected routing results | Yes |
| `data/reference/` | Required offline reference data and checksums | Yes |
| `data/manifests/` | Public-source metadata | Yes |
| `docs/` | Methods, interfaces, historical audits, and usage | Yes |
| `reports/current/` | Current Markdown reports, scenario tables, and manifest | Yes |
| `reports/experiments/` | Finalized evidence for five experiment tiers | Yes |
| `output/pdf/` | Six final PDF reports and their manifest | Yes |
| `data/processed/`, `reports/dataset/` | Rebuildable local dataset outputs | No |
| `tmp/`, `__pycache__/`, `.venv/` | Temporary checks, Python cache, local environment | No |

## Cleanup on 2026-09-07

Removed 116 redundant/cache files totaling 22,888,407 bytes (21.83 MiB).

- Removed obsolete PDF preview/render checks, temporary CLI experiment outputs,
  extracted historical text, and superseded LaTeX fragments under `tmp/`.
- Removed Python bytecode caches.
- Removed the duplicate generated `data/processed/public_synthetic_episodes.csv`.
  Its content remains in `threshold_calibration_ready_public_synthetic.csv`, which
  is already used by the calibration CLI. Updated the generator to write one copy.
- Retained identical recommendation JSON files in separate finalized experiment
  directories: each is part of that experiment's complete output bundle.
- Retained CSV/JSON representations, current Markdown/PDF reports, and historical
  audit documents because they serve distinct uses or preserve experiment evidence.
- Added dependency pins, portable setup instructions, and ignore rules that keep
  caches, local tool state, environments, and credentials out of Git.
- Preserved existing file locations and exact bytes for the backend contract,
  reference artifacts, and report snapshots. Git line-ending conversion is disabled
  to preserve their byte-level checksum contracts across platforms.

Rebuild local data with the commands in [public_data_pipeline.md](public_data_pipeline.md).
Historical audit commands and report manifests retain their original machine paths
as provenance; use the root README for current setup, and regenerate reports after
moving the repository if manifests with the new checkout paths are needed.

## Verification after cleanup

Verified on 2026-09-07 with Python 3.12.14 and the pinned direct dependencies:

- Full `unittest` suite: 107 passed, 0 failed, 0 skipped (166.620 seconds).
- A temporary checkout containing only upload files successfully ran the example,
  generated 20 episodes in the single canonical table, and calibrated a 10-row
  sample through the default CLI input path.
- Backend byte contract and current report/PDF artifact hashes passed.
- The report claim scan passed for 14 files.
- Upload review found no common secret-pattern matches or ignored files in the
  staged set. Generated datasets and temporary files are excluded from the repository.
