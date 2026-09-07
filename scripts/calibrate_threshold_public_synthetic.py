from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hhvbp_threshold_calibration import (  # noqa: E402
    DEFAULT_EQUITY_PENALTY_TPS,
    calibrate_public_synthetic_threshold,
    save_public_synthetic_threshold_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate public-synthetic threshold sensitivity outputs.")
    parser.add_argument(
        "--input",
        default=str(PROJECT_ROOT / "data" / "processed" / "threshold_calibration_ready_public_synthetic.csv"),
    )
    parser.add_argument("--output-root", default=str(PROJECT_ROOT))
    parser.add_argument("--selected-scenario", default="balanced_capacity_top_25_percent")
    parser.add_argument(
        "--lambda-equity",
        type=float,
        default=DEFAULT_EQUITY_PENALTY_TPS,
        help=(
            "TPS-proxy points deducted per unit absolute miss from the selected "
            "capacity scenario's target high-risk share (default: 10.0, or 0.1 "
            "TPS point per percentage-point miss)."
        ),
    )
    parser.add_argument(
        "--max-calibration-rows",
        type=int,
        default=None,
        help=(
            "Optional deterministic analysis-sample cap. The source dataset remains "
            "unchanged; sampling uses random_state=20260608 and is recorded in the output."
        ),
    )
    args = parser.parse_args()

    calibration_df = pd.read_csv(args.input)
    input_row_count = len(calibration_df)
    if args.max_calibration_rows is not None:
        if args.max_calibration_rows <= 0:
            raise ValueError("--max-calibration-rows must be positive when provided.")
        if input_row_count > args.max_calibration_rows:
            calibration_df = (
                calibration_df.sample(n=args.max_calibration_rows, random_state=20260608)
                .sort_index()
                .reset_index(drop=True)
            )
    selected, table = calibrate_public_synthetic_threshold(
        calibration_df=calibration_df,
        selected_scenario=args.selected_scenario,
        lambda_equity=args.lambda_equity,
    )
    selected["calibration_input_row_count"] = int(input_row_count)
    selected["calibration_analysis_row_count"] = int(len(calibration_df))
    selected["calibration_sampling"] = (
        "deterministic_without_replacement_random_state_20260608"
        if len(calibration_df) < input_row_count
        else "full_input"
    )
    save_public_synthetic_threshold_artifacts(selected, table, args.output_root)
    print(json.dumps(selected, indent=2))


if __name__ == "__main__":
    main()
