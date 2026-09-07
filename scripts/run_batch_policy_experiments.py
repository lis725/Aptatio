"""Run public-only semi-synthetic batch capacity and policy experiments.

Major requested design (default): 5,000 patients, 100 seeds, scenarios A-I,
policies P0-P7, and threshold candidates 0.00..1.00.  That design is deliberately
large.  Use ``--quick`` for a deterministic smoke run, or specify smaller grids
while developing.
"""

from __future__ import annotations

import argparse
import itertools
import subprocess
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hhvbp_batch_experiments import (  # noqa: E402
    BatchExperimentConfig,
    POLICY_NAMES,
    SCENARIOS,
    assert_nonconstant_threshold_sensitivity,
    run_policy_threshold_experiment,
    run_structural_suite,
    save_experiment_artifacts,
)


def parse_csv_values(value: str, cast=float) -> tuple:
    values = tuple(cast(token.strip()) for token in value.split(",") if token.strip())
    if not values:
        raise argparse.ArgumentTypeError("expected at least one comma-delimited value")
    return values


def parse_thresholds(value: str) -> tuple[float, ...]:
    text = value.strip()
    if ":" not in text:
        return tuple(float(item) for item in parse_csv_values(text, str))
    parts = [float(item) for item in text.split(":")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("threshold range must be start:stop:step")
    start, stop, step = parts
    if step <= 0 or stop < start:
        raise argparse.ArgumentTypeError("threshold range requires stop >= start and step > 0")
    count = int(round((stop - start) / step))
    values = tuple(round(start + index * step, 10) for index in range(count + 1))
    if not values or values[-1] > stop + 1e-9:
        raise argparse.ArgumentTypeError("invalid threshold range")
    return values


def parse_roster(value: str) -> dict[str, int]:
    output: dict[str, int] = {}
    for token in value.split(","):
        if not token.strip():
            continue
        try:
            discipline, count = token.split("=", 1)
            output[discipline.strip().upper()] = int(count)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("roster must look like RN=24,PT=18,OT=6") from exc
    if set(output) != {"RN", "PT", "OT"} or any(value <= 0 for value in output.values()):
        raise argparse.ArgumentTypeError("roster must provide positive RN, PT, and OT counts")
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "reports" / "experiments" / "batch_capacity")
    parser.add_argument("--n-patients", type=int, default=5_000)
    parser.add_argument("--n-seeds", type=int, default=100)
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--roster-sizes", type=parse_roster, default=parse_roster("RN=24,PT=18,OT=6"))
    parser.add_argument(
        "--roster-scales",
        type=lambda value: parse_csv_values(value, float),
        default=(1.0,),
        help="Comma-separated roster multipliers, for example 0.75,1.0,1.25.",
    )
    parser.add_argument(
        "--capacity-levels",
        type=lambda value: parse_csv_values(value, float),
        default=(1.05,),
        help="Comma-separated total-capacity/demand ratios.",
    )
    parser.add_argument(
        "--zip-sparsity-levels",
        type=lambda value: parse_csv_values(value, float),
        default=(0.25,),
        help="Comma-separated probabilities that a clinician does not cover a ZIP.",
    )
    parser.add_argument("--availability-rate", type=float, default=0.96)
    parser.add_argument("--thresholds", type=parse_thresholds, default=parse_thresholds("0:1:0.01"))
    parser.add_argument(
        "--scenarios",
        type=lambda value: parse_csv_values(value.upper(), str),
        default=tuple(SCENARIOS),
    )
    parser.add_argument(
        "--policies",
        type=lambda value: parse_csv_values(value.upper(), str),
        default=tuple(POLICY_NAMES),
    )
    parser.add_argument("--structural", action="store_true", help="Also write factorial, ablation, reliability, and fairness outputs.")
    parser.add_argument("--retain-assignments", action="store_true", help="Write patient-level assignment details; large major runs can be enormous.")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Override the major design with a small deterministic smoke run.",
    )
    return parser


def scaled_roster(base: dict[str, int], scale: float) -> dict[str, int]:
    if scale <= 0:
        raise ValueError("roster scales must be positive")
    return {discipline: max(1, int(round(count * scale))) for discipline, count in base.items()}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.n_patients <= 0 or args.n_seeds <= 0:
        parser.error("n-patients and n-seeds must be positive")
    if args.quick:
        args.n_patients = 36
        args.n_seeds = 2
        args.roster_scales = (1.0,)
        args.capacity_levels = (0.9,)
        args.zip_sparsity_levels = (0.55,)
        args.thresholds = (0.25, 0.75)
        args.scenarios = ("A", "G", "H")
        args.policies = ("P0", "P4", "P6", "P7")
    unknown_scenarios = set(args.scenarios).difference(SCENARIOS)
    unknown_policies = set(args.policies).difference(POLICY_NAMES)
    if unknown_scenarios:
        parser.error(f"unknown scenarios: {sorted(unknown_scenarios)}")
    if unknown_policies:
        parser.error(f"unknown policies: {sorted(unknown_policies)}")
    if any(value <= 0 for value in args.capacity_levels):
        parser.error("capacity levels must be positive")
    if any(value < 0 or value > 1 for value in args.zip_sparsity_levels):
        parser.error("ZIP sparsity levels must be in [0, 1]")

    seeds = tuple(range(args.seed_start, args.seed_start + args.n_seeds))
    result_frames: list[pd.DataFrame] = []
    assignment_frames: list[pd.DataFrame] = []
    design_configs: list[BatchExperimentConfig] = []
    designs = list(itertools.product(args.roster_scales, args.capacity_levels, args.zip_sparsity_levels))
    total_policy_runs = len(designs) * len(args.scenarios) * len(seeds) * len(args.thresholds) * len(args.policies)
    print(f"Executing {total_policy_runs:,} complete policy reruns across {len(designs)} operational design(s).")

    for design_index, (roster_scale, capacity_level, zip_sparsity) in enumerate(designs, start=1):
        roster = scaled_roster(args.roster_sizes, float(roster_scale))
        label = f"design_{design_index:03d}_roster_{roster_scale:g}_capacity_{capacity_level:g}_zip_{zip_sparsity:g}"
        config = BatchExperimentConfig(
            n_patients=args.n_patients,
            seeds=seeds,
            roster_sizes=roster,
            capacity_ratio=float(capacity_level),
            zip_sparsity=float(zip_sparsity),
            availability_rate=float(args.availability_rate),
            thresholds=tuple(args.thresholds),
            policies=tuple(args.policies),
            design_label=label,
        )
        design_configs.append(config)

        def progress(completed, total, summary):
            interval = max(1, total // 20)
            if completed == total or completed % interval == 0:
                print(
                    f"[{design_index}/{len(designs)}] {completed:,}/{total:,}: "
                    f"scenario={summary['scenario']} seed={summary['seed']} "
                    f"threshold={summary['threshold_percentile']:.2f} policy={summary['policy']}"
                )

        results, assignments = run_policy_threshold_experiment(
            config,
            scenarios=tuple(args.scenarios),
            retain_assignments=args.retain_assignments,
            progress_callback=progress,
        )
        results["roster_scale"] = float(roster_scale)
        result_frames.append(results)
        if assignments is not None:
            assignments["design_label"] = label
            assignment_frames.append(assignments)

    combined = pd.concat(result_frames, ignore_index=True)
    details = pd.concat(assignment_frames, ignore_index=True) if assignment_frames else None
    if len(args.thresholds) > 1 and any(policy in args.policies for policy in ("P4", "P5", "P6")):
        assert_nonconstant_threshold_sensitivity(combined)
    structural = run_structural_suite() if args.structural else None
    command = subprocess.list2cmdline([sys.executable, str(Path(__file__).resolve()), *(argv or sys.argv[1:])])
    paths = save_experiment_artifacts(
        combined,
        args.output_dir,
        config=design_configs[0] if len(design_configs) == 1 else None,
        design_configs=design_configs,
        assignments=details,
        structural=structural,
        reproduction_command=command,
    )
    print(f"Wrote {len(paths)} artifacts under {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
