from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from hhvbp_local_externality import METRIC_ORDER, RAW_DIRECTION
from hhvbp_batch_experiments import (
    BatchExperimentConfig,
    SCENARIOS,
    ZIP_CODES,
    generate_clinician_roster,
    generate_patient_cohort,
    run_threshold_sweep,
)
from hhvbp_risk_reference import empirical_risk_percentile, load_risk_reference

PUBLIC_SYNTHETIC_THRESHOLD_SOURCE = "public_only_semi_synthetic_capacity_pilot_not_outcome_validated"
CAPACITY_THRESHOLD_SOURCE = PUBLIC_SYNTHETIC_THRESHOLD_SOURCE
# Objective values are expressed in TPS-proxy points.  A coefficient of 10.0
# deducts 0.1 TPS point for each percentage-point miss from the selected
# scenario's target high-risk share.
DEFAULT_EQUITY_PENALTY_TPS = 10.0

CAPACITY_SCENARIO_SETTINGS = {
    "conservative_capacity_top_15_percent": {
        "high_risk_share": 0.15,
        "simulator_scenario": "H",
        "capacity_ratio": 0.70,
    },
    "balanced_capacity_top_25_percent": {
        "high_risk_share": 0.25,
        "simulator_scenario": "F",
        "capacity_ratio": 1.05,
    },
    "aggressive_capacity_top_35_percent": {
        "high_risk_share": 0.35,
        "simulator_scenario": "F",
        "capacity_ratio": 1.30,
    },
}


def choose_capacity_based_threshold(risk_scores: Iterable[float], high_risk_share: float) -> float:
    values = np.asarray(list(risk_scores), dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        raise ValueError("risk_scores must contain at least one finite value.")
    if high_risk_share <= 0 or high_risk_share >= 1:
        raise ValueError("high_risk_share must be between 0 and 1.")
    return float(np.quantile(values, 1.0 - high_risk_share))


def default_capacity_scenarios(risk_scores: Iterable[float]) -> dict:
    return {
        name: {
            **settings,
            "risk_threshold": choose_capacity_based_threshold(
                risk_scores, float(settings["high_risk_share"])
            ),
        }
        for name, settings in CAPACITY_SCENARIO_SETTINGS.items()
    }


def clip(value: float, low: float, high: float) -> float:
    return float(min(max(value, low), high))


def cms_style_care_score(performance: float, threshold: float, benchmark: float, baseline: float | None, direction: int) -> float:
    if benchmark == threshold:
        achievement = 0.0
    else:
        achievement = clip(10.0 * direction * (performance - threshold) / (direction * (benchmark - threshold)), 0.0, 10.0)
    if baseline is None or benchmark == baseline or direction * (baseline - benchmark) >= 0:
        improvement = 0.0
    else:
        improvement = clip(9.0 * direction * (performance - baseline) / (direction * (benchmark - baseline)), 0.0, 9.0)
    return max(achievement, improvement)


def tps_proxy_for_rows(rows: pd.DataFrame, reference_config: dict | None = None) -> float:
    if rows.empty:
        return 0.0
    weights = None
    thresholds = {}
    benchmarks = {}
    baselines = {}
    if reference_config:
        weights = reference_config.get("metric_weights")
        thresholds = reference_config.get("achievement_thresholds", {})
        benchmarks = reference_config.get("benchmarks", {})
        baselines = reference_config.get("agency_baselines", {})
    if not weights:
        weights = {metric: 1.0 / len(METRIC_ORDER) for metric in METRIC_ORDER}

    weighted_total = 0.0
    total_weight = 0.0
    for metric in METRIC_ORDER:
        column = f"outcome_{metric}"
        if column not in rows.columns:
            continue
        weight = float(weights.get(metric, 0.0))
        if weight <= 0:
            continue
        performance = float(rows[column].mean())
        direction = -1 if RAW_DIRECTION[metric] == "lower_is_better" else 1
        if metric in thresholds and metric in benchmarks:
            care = cms_style_care_score(
                performance=performance,
                threshold=float(thresholds[metric]),
                benchmark=float(benchmarks[metric]),
                baseline=None if metric not in baselines else float(baselines[metric]),
                direction=direction,
            )
        else:
            favorable = 1.0 - performance if direction < 0 else performance
            care = clip(10.0 * favorable, 0.0, 10.0)
        weighted_total += weight * care
        total_weight += weight
    if total_weight <= 0:
        return 0.0
    return float(100.0 * weighted_total / (10.0 * total_weight))


def assignment_workload_spread(rows: pd.DataFrame) -> float:
    clinician_columns = [column for column in ["recommended_RN_id", "recommended_PT_id", "recommended_OT_id"] if column in rows.columns]
    if not clinician_columns:
        return 0.0
    counts = pd.concat([rows[column] for column in clinician_columns], ignore_index=True).value_counts()
    if counts.empty or counts.mean() == 0:
        return 0.0
    return float(counts.std(ddof=0) / counts.mean())


def _zip_for_simulator(value: object) -> str:
    text = str(value).strip().zfill(5)
    if text in ZIP_CODES:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return ZIP_CODES[int.from_bytes(digest[:4], "big") % len(ZIP_CODES)]


def _cohort_for_full_rerun(calibration_df: pd.DataFrame, config: BatchExperimentConfig) -> pd.DataFrame:
    """Translate legacy episode rows into the immutable batch-simulator cohort."""
    required_factors = [
        "health_status", "age_group", "house_price_zip", "area_type",
        "housing_type", "distance_to_clinic", "driving_condition",
    ]
    missing_factors = [
        column for column in required_factors if column not in calibration_df.columns
    ]
    if missing_factors:
        raise ValueError(
            "Calibration input is incomplete; full threshold reruns require all "
            "seven patient risk factors. Missing factors: "
            + ", ".join(missing_factors)
            + "."
        )

    out = pd.DataFrame(index=calibration_df.index)
    if "patient_id_random" in calibration_df:
        out["patient_id"] = calibration_df["patient_id_random"].astype(str)
    else:
        out["patient_id"] = [f"legacy-calibration-{index:06d}" for index in range(len(calibration_df))]
    zip_column = "patient_zip_for_routing" if "patient_zip_for_routing" in calibration_df else "zip5_or_zcta"
    if zip_column in calibration_df:
        out["patient_zip"] = calibration_df[zip_column].map(_zip_for_simulator)
    else:
        out["patient_zip"] = [ZIP_CODES[index % len(ZIP_CODES)] for index in range(len(calibration_df))]
    for factor in required_factors:
        out[factor] = calibration_df[factor].astype(str)

    raw_column = "risk_score_rho_raw" if "risk_score_rho_raw" in calibration_df else "risk_score_rho"
    out["risk_score_rho_raw"] = calibration_df[raw_column].astype(float).clip(0.0, 1.0)
    # Never trust a legacy cohort percentile: it may have been a cohort rank or
    # derived from the obsolete 50-row reference.  Recompute from raw risk using
    # the same persisted ECDF as runtime.
    reference = load_risk_reference()
    values = reference["sorted_risk_scores"]
    out["risk_percentile_u"] = out["risk_score_rho_raw"].map(
        lambda value: empirical_risk_percentile(float(value), values)
    )
    out["clinical_need_decile"] = np.minimum(
        9, np.floor(out["risk_percentile_u"].to_numpy(dtype=float) * 10)
    ).astype(int) + 1
    noise_rng = np.random.default_rng(20260608)
    for metric in METRIC_ORDER:
        out[f"noise_{metric}"] = noise_rng.normal(0.0, 1.0, size=len(out))
    out["public_data_only"] = True
    out["synthetic_data"] = True
    out["production_validated"] = False
    return out.reset_index(drop=True)


def sensitivity_table(
    calibration_df: pd.DataFrame,
    candidate_threshold_grid: Iterable[float],
    reference_config: dict | None = None,
    lambda_fallback: float = 0.0,
    lambda_equity: float = DEFAULT_EQUITY_PENALTY_TPS,
    lambda_overload: float = 0.0,
    selected_scenario: str = "balanced_capacity_top_25_percent",
) -> pd.DataFrame:
    """Fully rerun threshold candidates and score them in TPS-proxy points.

    ``lambda_equity`` is denominated in TPS points per unit absolute difference
    between the realized and target high-risk shares.  Its default of 10.0 is
    therefore a 0.1 TPS-point deduction per percentage-point target miss.
    """
    if calibration_df.empty:
        raise ValueError("Calibration input must contain at least one row.")
    if "risk_score_rho" not in calibration_df.columns and "risk_score_rho_raw" not in calibration_df.columns:
        raise ValueError("Calibration input must contain risk_score_rho or risk_score_rho_raw.")

    if selected_scenario not in CAPACITY_SCENARIO_SETTINGS:
        raise ValueError(f"Unknown selected_scenario {selected_scenario!r}.")
    scenario_settings = CAPACITY_SCENARIO_SETTINGS[selected_scenario]
    simulator_scenario = str(scenario_settings["simulator_scenario"])
    thresholds = tuple(float(value) for value in candidate_threshold_grid)
    selection_seed = 20260608
    held_out_seed = 20260609
    config = BatchExperimentConfig(
        n_patients=len(calibration_df),
        seeds=(selection_seed, held_out_seed),
        thresholds=thresholds,
        policies=("P4",),
        capacity_ratio=float(scenario_settings["capacity_ratio"]),
        design_label="legacy_public_calibration_full_policy_rerun",
    )
    patients = _cohort_for_full_rerun(calibration_df, config)
    roster = generate_clinician_roster(config, seed=selection_seed, scenario=SCENARIOS[simulator_scenario])
    selection_table = run_threshold_sweep(
        patients,
        roster=roster,
        thresholds=thresholds,
        scenario=simulator_scenario,
        seeds=(selection_seed,),
        policy="P4",
        config=config,
    ).copy()
    selection_table["evaluation_split"] = "selection"

    held_out_patients = generate_patient_cohort(config, seed=held_out_seed)
    held_out_roster = generate_clinician_roster(
        config, seed=held_out_seed, scenario=SCENARIOS[simulator_scenario]
    )
    held_out_table = run_threshold_sweep(
        held_out_patients,
        roster=held_out_roster,
        thresholds=thresholds,
        scenario=simulator_scenario,
        seeds=(held_out_seed,),
        policy="P4",
        config=config,
    ).copy()
    held_out_table["evaluation_split"] = "held_out"
    table = pd.concat([selection_table, held_out_table], ignore_index=True)
    table["risk_threshold"] = table["threshold_percentile"]
    table["fallback_rate"] = table["zip_fallback_rate"]
    table["zip_continuity_share"] = table["zip_continuity_rate"]
    table["equity_gap_from_target"] = (
        table["high_risk_full_pool_share"] - float(scenario_settings["high_risk_share"])
    ).abs()
    # Preserve the legacy column as a dimensionless share gap while making the
    # actual TPS-unit deduction explicit in a separate column.
    table["equity_penalty"] = table["equity_gap_from_target"]
    table["equity_penalty_tps"] = (
        float(lambda_equity) * table["equity_gap_from_target"]
    )
    table["high_risk_share"] = table["high_risk_full_pool_share"]
    table["objective"] = (
        table["tps_proxy"]
        - float(lambda_fallback) * table["fallback_rate"]
        - table["equity_penalty_tps"]
        - float(lambda_overload) * table["workload_spread"]
    )
    table["lambda_equity_tps_per_unit_share"] = float(lambda_equity)
    table["objective_units"] = "TPS_proxy_points"
    table["threshold_source"] = PUBLIC_SYNTHETIC_THRESHOLD_SOURCE
    table["threshold_fully_rerun"] = True
    table["selected_capacity_scenario"] = selected_scenario
    table["evaluated_simulator_scenario"] = simulator_scenario
    table["evaluated_capacity_ratio"] = float(scenario_settings["capacity_ratio"])
    table["target_high_risk_share"] = float(scenario_settings["high_risk_share"])
    optimization_evaluated = len(set(thresholds)) >= 2
    table["optimization_evaluated"] = optimization_evaluated
    table["calibration_evaluation_design"] = (
        "held_out_simulation_selection"
        if optimization_evaluated
        else "fixed_threshold_only_no_optimization"
    )
    return table


def calibrate_public_synthetic_threshold(
    calibration_df: pd.DataFrame,
    candidate_threshold_grid: Iterable[float] | None = None,
    reference_config: dict | None = None,
    selected_scenario: str = "balanced_capacity_top_25_percent",
    lambda_equity: float = DEFAULT_EQUITY_PENALTY_TPS,
) -> tuple[dict, pd.DataFrame]:
    if candidate_threshold_grid is None:
        candidate_threshold_grid = [round(value, 2) for value in np.linspace(0.0, 1.0, 101)]
    if selected_scenario not in CAPACITY_SCENARIO_SETTINGS:
        raise ValueError(f"Unknown selected_scenario {selected_scenario!r}.")
    table = sensitivity_table(
        calibration_df,
        candidate_threshold_grid,
        reference_config=reference_config,
        lambda_equity=lambda_equity,
        selected_scenario=selected_scenario,
    )
    selection_rows = table[table["evaluation_split"] == "selection"]
    held_out_rows = table[table["evaluation_split"] == "held_out"]
    optimization_evaluated = bool(table["optimization_evaluated"].iloc[0])
    if optimization_evaluated:
        simulation_best = selection_rows.sort_values(
            ["objective", "risk_threshold"], ascending=[False, True]
        ).iloc[0]
        held_out_selected = held_out_rows[
            np.isclose(
                held_out_rows["risk_threshold"].astype(float),
                float(simulation_best["risk_threshold"]),
            )
        ].iloc[0]
        held_out_best_objective = float(held_out_rows["objective"].max())
        held_out_regret = held_out_best_objective - float(held_out_selected["objective"])
        simulation_recommended_threshold: float | None = float(
            simulation_best["risk_threshold"]
        )
        held_out_objective_at_selected: float | None = float(
            held_out_selected["objective"]
        )
        recommendation_label = "held_out_simulation_selection"
        selection_protocol = (
            "threshold_selected_on_selection_seed_then_evaluated_once_on_"
            "distinct_held_out_seed"
        )
    else:
        simulation_recommended_threshold = None
        held_out_objective_at_selected = None
        held_out_best_objective = None
        held_out_regret = None
        recommendation_label = "fixed_threshold_only_no_optimization"
        selection_protocol = "single_candidate_evaluated_no_threshold_selection"
    pilot_rows = selection_rows[np.isclose(selection_rows["risk_threshold"].astype(float), 0.75)]
    pilot = pilot_rows.iloc[0] if not pilot_rows.empty else table.iloc[(table["risk_threshold"] - 0.75).abs().argmin()]
    output = {
        "risk_threshold": 0.75,
        "threshold_percentile": 0.75,
        "simulation_recommended_threshold": simulation_recommended_threshold,
        "simulation_recommendation_label": recommendation_label,
        "optimization_evaluated": optimization_evaluated,
        "selection_protocol": selection_protocol,
        "evaluated_fixed_threshold": (
            None
            if optimization_evaluated
            else float(selection_rows["risk_threshold"].iloc[0])
        ),
        "selection_seed": 20260608,
        "held_out_evaluation_seed": 20260609,
        "held_out_objective_at_selected_threshold": held_out_objective_at_selected,
        "held_out_best_objective": held_out_best_objective,
        "held_out_regret": held_out_regret,
        "lambda_equity_tps_per_unit_share": float(lambda_equity),
        "equity_penalty_interpretation": (
            "TPS-proxy point deduction per unit absolute target-share miss; "
            f"{float(lambda_equity):g} equals {float(lambda_equity) / 100.0:g} "
            "TPS point per percentage-point miss"
        ),
        "threshold_source": CAPACITY_THRESHOLD_SOURCE,
        "public_data_only": True,
        "synthetic_data": True,
        "production_validated": False,
        "selected_scenario": selected_scenario,
        "evaluated_simulator_scenario": str(table["evaluated_simulator_scenario"].iloc[0]),
        "evaluated_capacity_ratio": float(table["evaluated_capacity_ratio"].iloc[0]),
        "scenario_high_risk_share": float(pilot["high_risk_share"]),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sensitivity_threshold_source": PUBLIC_SYNTHETIC_THRESHOLD_SOURCE,
        "warning": "The production/demo percentile remains 0.75. Any different recommendation is simulation-only and is not validated on sponsor historical outcomes.",
    }
    return output, table


def save_public_synthetic_threshold_artifacts(
    selected_threshold: dict,
    table: pd.DataFrame,
    output_root: str | Path = ".",
) -> None:
    root = Path(output_root)
    processed = root / "data" / "processed"
    reports = root / "reports" / "dataset"
    processed.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    table.to_csv(reports / "public_synthetic_threshold_sensitivity.csv", index=False)
    (processed / "public_synthetic_selected_threshold.json").write_text(
        json.dumps(selected_threshold, indent=2) + "\n",
        encoding="utf-8",
    )
    report_lines = [
        "# Public Synthetic Threshold Report",
        "",
        f"Selected scenario: {selected_threshold['selected_scenario']}",
        f"Production/demo pilot percentile (unchanged): {selected_threshold['risk_threshold']:.4f}",
        "Scenario-only simulation recommendation: "
        + (
            f"{selected_threshold['simulation_recommended_threshold']:.4f}"
            if selected_threshold.get("simulation_recommended_threshold") is not None
            else "none (fixed threshold only; no optimization)"
        ),
        "Held-out regret at that recommendation: "
        + (
            f"{selected_threshold['held_out_regret']:.6f}"
            if selected_threshold.get("held_out_regret") is not None
            else "not applicable"
        ),
        f"Calibration design: {selected_threshold['simulation_recommendation_label']}",
        f"Optimization evaluated: {str(bool(selected_threshold['optimization_evaluated'])).lower()}",
        (
            "Equity penalty: "
            f"{selected_threshold['lambda_equity_tps_per_unit_share']:.4f} TPS-proxy "
            "points per unit target-share miss"
        ),
        f"Source rows: {selected_threshold.get('calibration_input_row_count', 'not recorded')}",
        f"Analysis rows: {selected_threshold.get('calibration_analysis_row_count', 'not recorded')}",
        f"Analysis sampling: {selected_threshold.get('calibration_sampling', 'not recorded')}",
        "",
        "- public_data_only: true",
        "- synthetic_data: true",
        "- production_validated: false",
        "- threshold candidates fully rerun assignments, capacity, workload, and outcomes: true",
        "",
        selected_threshold["warning"],
    ]
    (reports / "public_synthetic_threshold_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
