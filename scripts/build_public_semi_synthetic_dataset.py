from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from public_synthetic_data import (  # noqa: E402
    generate_public_semi_synthetic_dataset,
    load_json,
    write_public_synthetic_outputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a public-data-only semi-synthetic HHVBP dataset.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / "public_semi_synthetic_dataset_config.json"))
    parser.add_argument("--manifest", default=str(PROJECT_ROOT / "data" / "manifests" / "public_data_manifest.example.json"))
    parser.add_argument("--output-root", default=str(PROJECT_ROOT))
    parser.add_argument(
        "--num-episodes",
        type=int,
        default=None,
        help="Optional smoke/demo override; the persisted risk reference remains a separate 10,000-row artifact.",
    )
    args = parser.parse_args()

    config = load_json(args.config)
    if args.num_episodes is not None:
        if args.num_episodes <= 0:
            raise ValueError("--num-episodes must be positive")
        config["num_synthetic_episodes"] = int(args.num_episodes)
    manifest = load_json(args.manifest)
    patient_df, clinician_df, zip_history_df, calibration_df, report = generate_public_semi_synthetic_dataset(
        config=config,
        manifest=manifest,
        config_dir=PROJECT_ROOT / "config",
    )
    write_public_synthetic_outputs(patient_df, clinician_df, zip_history_df, calibration_df, report, args.output_root)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
