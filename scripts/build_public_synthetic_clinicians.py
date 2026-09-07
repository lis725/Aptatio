from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from public_synthetic_data import generate_public_synthetic_clinicians, load_json, write_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Build public-only synthetic clinicians and ZIP coverage.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / "public_semi_synthetic_dataset_config.json"))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "data" / "processed"))
    args = parser.parse_args()

    config = load_json(args.config)
    clinician_df, zip_history_df, report = generate_public_synthetic_clinicians(config)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    clinician_df.to_csv(output_dir / "public_synthetic_clinicians.csv", index=False)
    zip_history_df.to_csv(output_dir / "public_synthetic_zip_history.csv", index=False)
    write_json(report, PROJECT_ROOT / "reports" / "dataset" / "public_synthetic_clinician_report.json")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
