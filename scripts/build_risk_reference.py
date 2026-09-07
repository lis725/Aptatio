from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hhvbp_risk_reference import (  # noqa: E402
    DEFAULT_MONTE_CARLO_SIZE,
    DEFAULT_REFERENCE_DIR,
    DEFAULT_SEED,
    generate_risk_reference,
    write_risk_reference_artifacts,
)


def _load_factor_distributions(path: str | None) -> dict | None:
    if path is None:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    distributions = payload.get("factor_distributions", payload)
    if not isinstance(distributions, dict):
        raise ValueError("Distribution JSON must be a factor mapping or contain factor_distributions.")
    return distributions


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build the deterministic public-only weighted Monte Carlo risk reference "
            "and exhaustive 2,160-profile structural grid."
        )
    )
    parser.add_argument("--config-dir", default=str(PROJECT_ROOT / "config"))
    parser.add_argument("--output-dir", default=str(DEFAULT_REFERENCE_DIR))
    parser.add_argument("--sample-size", type=int, default=DEFAULT_MONTE_CARLO_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--source-mode",
        default=None,
        help="Defaults to mode in public_semi_synthetic_dataset_config.json.",
    )
    parser.add_argument(
        "--factor-distributions",
        default=None,
        help="Optional JSON mapping of factor/category weights; all seven factors must be represented.",
    )
    parser.add_argument(
        "--creation-timestamp",
        default=None,
        help="Optional ISO timestamp for reproducible fixture builds; defaults to current UTC time.",
    )
    args = parser.parse_args()

    reference, structural_grid = generate_risk_reference(
        config_dir=args.config_dir,
        sample_size=args.sample_size,
        seed=args.seed,
        factor_distributions=_load_factor_distributions(args.factor_distributions),
        source_mode=args.source_mode,
        creation_timestamp=args.creation_timestamp,
    )
    paths = write_risk_reference_artifacts(reference, structural_grid, args.output_dir)
    metadata = reference["metadata"]
    print(
        json.dumps(
            {
                "reference_id": metadata["reference_id"],
                "sample_size": metadata["sample_size"],
                "structural_grid_size": metadata["structural_grid_size"],
                "seed": metadata["seed"],
                "source_mode": metadata["source_mode"],
                "config_hash": metadata["config_hash"],
                "threshold_percentile": reference["threshold_percentile"],
                "tau_raw": reference["tau_raw"],
                "artifacts": {name: str(path.resolve()) for name, path in paths.items()},
                "public_data_only": True,
                "semi_synthetic": True,
                "outcome_validated": False,
                "production_validated": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
