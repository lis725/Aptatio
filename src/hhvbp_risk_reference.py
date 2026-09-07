"""Deterministic public-only risk reference and structural profile grid.

The reference produced here is a weighted Monte Carlo *simulation* over the
configured seven-factor patient categories.  It is not a sponsor cohort and it
is not an outcome-calibrated clinical probability model.  The exhaustive grid
is kept separately so structural coverage can be audited without conflating a
uniform Cartesian product with the weighted reference population.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import itertools
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Iterable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_REFERENCE_DIR = PROJECT_ROOT / "data" / "reference"
DEFAULT_REFERENCE_ARTIFACT_PATH = DEFAULT_REFERENCE_DIR / "hhvbp_risk_reference.json"
DEFAULT_STRUCTURAL_GRID_PATH = DEFAULT_REFERENCE_DIR / "hhvbp_structural_grid.csv"
DEFAULT_STRUCTURAL_GRID_METADATA_PATH = DEFAULT_REFERENCE_DIR / "hhvbp_structural_grid.metadata.json"

DEFAULT_SEED = 20260608
DEFAULT_MONTE_CARLO_SIZE = 10_000
MINIMUM_MONTE_CARLO_SIZE = 10_000
THRESHOLD_PERCENTILE = 0.75
THRESHOLD_SOURCE = "public_only_semi_synthetic_capacity_pilot_not_outcome_validated"
REFERENCE_SCHEMA_VERSION = "1.0"

EXPECTED_FACTOR_ORDER = (
    "health_status",
    "age_group",
    "house_price_zip",
    "area_type",
    "housing_type",
    "distance_to_clinic",
    "driving_condition",
)
EXPECTED_FACTOR_CARDINALITIES = {
    "health_status": 3,
    "age_group": 5,
    "house_price_zip": 3,
    "area_type": 4,
    "housing_type": 2,
    "distance_to_clinic": 3,
    "driving_condition": 2,
}
STRUCTURAL_GRID_SIZE = math.prod(EXPECTED_FACTOR_CARDINALITIES.values())

# These are deliberately explicit expert-configured offline fallback weights.
# The checked-in public semi-synthetic configuration can override any factor;
# it currently supplies the age distribution.  Category names are always
# validated against hhvbp_global_config.json before sampling.
OFFLINE_EXPERT_FACTOR_DISTRIBUTIONS: dict[str, dict[str, float]] = {
    "health_status": {"healthy": 0.20, "moderate": 0.50, "unhealthy": 0.30},
    "age_group": {
        "age_40_50": 0.08,
        "age_50_60": 0.17,
        "age_60_70": 0.27,
        "age_70_80": 0.30,
        "age_80_plus": 0.18,
    },
    "house_price_zip": {"high": 0.25, "average": 0.50, "low": 0.25},
    "area_type": {
        "micropolitan": 0.15,
        "metropolitan": 0.60,
        "small_town": 0.15,
        "rural": 0.10,
    },
    "housing_type": {"single_home": 0.65, "apartment": 0.35},
    "distance_to_clinic": {"near": 0.45, "medium": 0.35, "far": 0.20},
    "driving_condition": {"good_summer": 0.70, "bad_winter": 0.30},
}

RAW_DIRECTION = {
    "PPH": "lower_is_better",
    "DTC": "higher_is_better",
    "DFS": "higher_is_better",
    "Oral_Meds": "higher_is_better",
    "Dyspnea": "higher_is_better",
    "Recommend": "higher_is_better",
    "Agency_Rating": "higher_is_better",
    "Care_Issues": "higher_is_better",
    "Communications": "higher_is_better",
    "Care_of_Patients": "higher_is_better",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_sorted_values(values: Iterable[float]) -> list[float]:
    try:
        result = [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise ValueError("Risk reference values must be a non-empty iterable of finite numbers.") from exc
    if not result:
        raise ValueError("Risk reference values must contain at least one value.")
    if not all(math.isfinite(value) for value in result):
        raise ValueError("Risk reference values must all be finite.")
    if any(left > right for left, right in zip(result, result[1:])):
        result.sort()
    return result


def quantile_raw(sorted_values: Iterable[float], probability: float = THRESHOLD_PERCENTILE) -> float:
    """Return a deterministic type-7 linearly interpolated raw-score quantile."""

    values = _coerce_sorted_values(sorted_values)
    probability = float(probability)
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be finite and within [0, 1].")
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return values[lower_index]
    fraction = position - lower_index
    return float(values[lower_index] + fraction * (values[upper_index] - values[lower_index]))


def empirical_risk_percentile(value: float, sorted_values: Iterable[float]) -> float:
    """Map a raw score to a stable interpolated percentile in ``[0, 1]``.

    Distinct ordered observations use plotting positions ``i / (n - 1)``.
    Exact ties receive their deterministic average-rank plotting position.  A
    value strictly between support points is linearly interpolated between the
    two adjacent tie-group positions.  Values outside the observed range clamp
    to zero or one.  For a degenerate one-value distribution, the exact support
    value maps to 0.5 (with values below/above still mapping to 0/1).
    """

    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise ValueError("value must be finite.")
    values = _coerce_sorted_values(sorted_values)
    minimum = values[0]
    maximum = values[-1]
    if numeric_value < minimum:
        return 0.0
    if numeric_value > maximum:
        return 1.0
    if len(values) == 1 or minimum == maximum:
        return 0.5

    left = bisect.bisect_left(values, numeric_value)
    right = bisect.bisect_right(values, numeric_value)
    denominator = float(len(values) - 1)
    if left != right:
        midpoint_index = 0.5 * (left + right - 1)
        return float(min(max(midpoint_index / denominator, 0.0), 1.0))

    # The value lies strictly between the last lower and first upper support
    # points.  Interpolate between the average-rank positions of those groups.
    lower_value = values[left - 1]
    upper_value = values[left]
    lower_left = bisect.bisect_left(values, lower_value)
    lower_right = bisect.bisect_right(values, lower_value)
    upper_left = bisect.bisect_left(values, upper_value)
    upper_right = bisect.bisect_right(values, upper_value)
    lower_percentile = 0.5 * (lower_left + lower_right - 1) / denominator
    upper_percentile = 0.5 * (upper_left + upper_right - 1) / denominator
    fraction = (numeric_value - lower_value) / (upper_value - lower_value)
    percentile = lower_percentile + fraction * (upper_percentile - lower_percentile)
    return float(min(max(percentile, 0.0), 1.0))


def _pert_mean(low: float, mode: float, high: float) -> float:
    return (low + 4.0 * mode + high) / 6.0


def _parse_display_direction(raw_value: object) -> float:
    if raw_value is None:
        return 1.0
    value = str(raw_value).strip().lower()
    if value in {"higher_is_better", "higher", "positive", "+", "ascending"}:
        return 1.0
    if value in {"lower_is_better", "lower", "negative", "-", "descending"}:
        return -1.0
    raise ValueError(f"Unsupported display direction: {raw_value!r}")


class _CompiledRiskModel:
    """Small compiled representation of the canonical mean-risk calculation."""

    def __init__(self, config_dir: str | Path) -> None:
        self.config_dir = Path(config_dir).resolve()
        self.global_path = self.config_dir / "hhvbp_global_config.json"
        self.metric_path = self.config_dir / "metric_parameter_library.json"
        self.global_config = _load_json(self.global_path)
        self.metric_library = _load_json(self.metric_path)
        self.factor_order = tuple(self.global_config.get("factor_order", ()))
        if self.factor_order != EXPECTED_FACTOR_ORDER:
            raise ValueError(
                "The active risk model must contain exactly the configured seven factors in canonical order. "
                f"Expected {list(EXPECTED_FACTOR_ORDER)}, found {list(self.factor_order)}."
            )
        self.categories = {
            factor: tuple(self.global_config["base_favorability"][factor].keys())
            for factor in self.factor_order
        }
        cardinalities = {factor: len(values) for factor, values in self.categories.items()}
        if cardinalities != EXPECTED_FACTOR_CARDINALITIES:
            raise ValueError(
                f"Configured factor cardinalities must produce the exact 2,160 grid; found {cardinalities}."
            )
        self.metric_order = tuple(self.metric_library.get("metrics", {}).keys())
        if not self.metric_order:
            raise ValueError("metric_parameter_library.json must define at least one metric.")
        self.compiled_metrics = [self._compile_metric(metric) for metric in self.metric_order]
        weight_sum = sum(metric["weight"] for metric in self.compiled_metrics)
        if not math.isclose(weight_sum, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(f"Metric composite weights must sum to 1.0; found {weight_sum:.12f}.")

    def _compile_metric(self, metric_key: str) -> dict:
        metric_cfg = self.metric_library["metrics"][metric_key]
        if metric_key not in RAW_DIRECTION:
            raise ValueError(f"No raw score direction is configured for metric {metric_key!r}.")

        relative_swings: dict[str, float] = {}
        for factor in self.factor_order:
            elicitation = metric_cfg["expert_elicitation"][factor]
            relative_swings[factor] = _pert_mean(
                float(elicitation["low"]),
                float(elicitation["mode"]),
                float(elicitation["high"]),
            )
        health_swing = relative_swings["health_status"]
        if health_swing <= 0:
            raise ValueError(f"health_status expert swing must be positive for {metric_key}.")
        relative_swings = {factor: value / health_swing for factor, value in relative_swings.items()}
        prior_total = sum(relative_swings.values())
        baseline_prior = {factor: value / prior_total for factor, value in relative_swings.items()}

        delta = float(metric_cfg["delta"])
        multipliers = {"strong": math.exp(delta), "medium": 1.0, "weak": math.exp(-delta)}
        weighted = {
            factor: baseline_prior[factor] * multipliers[metric_cfg["importance_levels"][factor]]
            for factor in self.factor_order
        }
        weighted_total = sum(weighted.values())
        score_ranges = {factor: 100.0 * value / weighted_total for factor, value in weighted.items()}

        favorability = {
            factor: {category: float(value) for category, value in categories.items()}
            for factor, categories in self.global_config["base_favorability"].items()
        }
        for factor, category_map in metric_cfg.get("favorability_overrides", {}).items():
            for category, value in category_map.items():
                favorability[factor][category] = float(value)
        gap_scale = float(metric_cfg["gap_scale"])
        contribution_by_factor: dict[str, dict[str, float]] = {}
        for factor in self.factor_order:
            contribution_by_factor[factor] = {}
            for category in self.categories[factor]:
                mean_proportion = 0.5 + gap_scale * (favorability[factor][category] - 0.5)
                mean_proportion = min(max(mean_proportion, 1e-6), 1.0 - 1e-6)
                contribution_by_factor[factor][category] = score_ranges[factor] * mean_proportion

        best_profile = self.global_config["profiles"]["best_case"]
        worst_profile = self.global_config["profiles"]["worst_case"]
        best_latent = sum(contribution_by_factor[factor][best_profile[factor]] for factor in self.factor_order)
        worst_latent = sum(contribution_by_factor[factor][worst_profile[factor]] for factor in self.factor_order)
        display_cfg = self.global_config.get("display_scale", {})
        national_average = metric_cfg.get(
            "national_average",
            display_cfg.get("national_average_by_metric", {}).get(metric_key),
        )
        display_direction = metric_cfg.get(
            "display_direction",
            display_cfg.get("display_direction_by_metric", {}).get(
                metric_key,
                display_cfg.get("default_direction"),
            ),
        )
        units = float(
            metric_cfg.get(
                "display_units_per_latent_point",
                display_cfg.get("units_per_latent_point_by_metric", {}).get(
                    metric_key,
                    display_cfg.get("units_per_latent_point", 1.0),
                ),
            )
        )
        if units <= 0:
            raise ValueError(f"Display units per latent point must be positive for {metric_key}.")
        direction_multiplier = _parse_display_direction(display_direction)
        latent_midpoint = 0.5 * (best_latent + worst_latent)

        def display_mean(latent_mean: float) -> float:
            if national_average is None:
                return float(latent_mean)
            return float(national_average) + direction_multiplier * units * (latent_mean - latent_midpoint)

        return {
            "metric_key": metric_key,
            "weight": float(metric_cfg["composite_weight"]),
            "raw_direction": RAW_DIRECTION[metric_key],
            "contribution_by_factor": contribution_by_factor,
            "display_mean": display_mean,
            "best_display_mean": display_mean(best_latent),
            "worst_display_mean": display_mean(worst_latent),
        }

    def validate_profile(self, profile: Mapping[str, str]) -> None:
        missing = [factor for factor in self.factor_order if factor not in profile]
        extras = [factor for factor in profile if factor not in self.factor_order]
        if missing or extras:
            raise ValueError(f"Profile factors do not match the configured seven factors; missing={missing}, extras={extras}.")
        for factor in self.factor_order:
            if profile[factor] not in self.categories[factor]:
                raise ValueError(
                    f"Invalid category {profile[factor]!r} for {factor}; "
                    f"allowed={list(self.categories[factor])}."
                )

    def score_profile(self, profile: Mapping[str, str]) -> tuple[float, dict[str, dict[str, float]]]:
        self.validate_profile(profile)
        metric_details: dict[str, dict[str, float]] = {}
        rho_raw = 0.0
        for metric in self.compiled_metrics:
            latent_mean = sum(
                metric["contribution_by_factor"][factor][profile[factor]]
                for factor in self.factor_order
            )
            patient_display_mean = metric["display_mean"](latent_mean)
            best_mean = metric["best_display_mean"]
            worst_mean = metric["worst_display_mean"]
            if metric["raw_direction"] == "higher_is_better":
                denominator = best_mean - worst_mean
                need_mean = 0.0 if denominator == 0 else (best_mean - patient_display_mean) / denominator
            else:
                denominator = worst_mean - best_mean
                need_mean = 0.0 if denominator == 0 else (patient_display_mean - best_mean) / denominator
            need_mean = float(min(max(need_mean, 0.0), 1.0))
            weighted_need = metric["weight"] * need_mean
            rho_raw += weighted_need
            metric_details[metric["metric_key"]] = {
                "patient_display_mean": float(patient_display_mean),
                "need_mean": need_mean,
                "weighted_need_mean": float(weighted_need),
            }
        return float(min(max(rho_raw, 0.0), 1.0)), metric_details


def risk_score_for_profile(profile: Mapping[str, str], config_dir: str | Path = DEFAULT_CONFIG_DIR) -> float:
    """Return the canonical aggregate raw need score ``rho_raw`` for a profile."""

    model = _CompiledRiskModel(config_dir)
    rho_raw, _ = model.score_profile(profile)
    return rho_raw


def build_structural_grid(
    config_dir: str | Path = DEFAULT_CONFIG_DIR,
    sorted_values: Iterable[float] | None = None,
    threshold_percentile: float = THRESHOLD_PERCENTILE,
) -> list[dict]:
    """Build the exhaustive, deterministic 2,160-profile Cartesian grid."""

    threshold_percentile = float(threshold_percentile)
    if not math.isfinite(threshold_percentile) or not 0.0 <= threshold_percentile <= 1.0:
        raise ValueError("threshold_percentile must be finite and within [0, 1].")
    model = _CompiledRiskModel(config_dir)
    reference_values = None if sorted_values is None else _coerce_sorted_values(sorted_values)
    rows: list[dict] = []
    category_product = itertools.product(*(model.categories[factor] for factor in model.factor_order))
    for index, category_values in enumerate(category_product, start=1):
        profile = dict(zip(model.factor_order, category_values))
        rho_raw, metric_details = model.score_profile(profile)
        row: dict[str, object] = {"profile_id": f"profile_{index:04d}", **profile}
        for metric in model.metric_order:
            detail = metric_details[metric]
            row[f"need_mean_{metric}"] = detail["need_mean"]
            row[f"weighted_need_mean_{metric}"] = detail["weighted_need_mean"]
        row["rho_raw"] = rho_raw
        if reference_values is not None:
            percentile = empirical_risk_percentile(rho_raw, reference_values)
            row["risk_percentile_u"] = percentile
            row["threshold_percentile"] = threshold_percentile
            row["threshold_route"] = (
                "global_high_risk" if percentile > threshold_percentile else "lower_risk_zip_policy"
            )
        rows.append(row)
    if len(rows) != STRUCTURAL_GRID_SIZE:
        raise AssertionError(f"Structural grid must contain exactly {STRUCTURAL_GRID_SIZE} rows; found {len(rows)}.")
    return rows


def _resolve_factor_distributions(
    model: _CompiledRiskModel,
    explicit_distributions: Mapping[str, Mapping[str, float]] | None,
) -> tuple[dict[str, dict[str, float]], dict[str, str], dict]:
    public_config_path = model.config_dir / "public_semi_synthetic_dataset_config.json"
    public_config = _load_json(public_config_path) if public_config_path.exists() else {}
    public_fallback = public_config.get("fallback_distributions", {})
    resolved: dict[str, dict[str, float]] = {}
    sources: dict[str, str] = {}

    for factor in model.factor_order:
        if explicit_distributions is not None and factor in explicit_distributions:
            candidate = explicit_distributions[factor]
            source = "caller_supplied_expert_or_public_distribution"
        elif factor in public_fallback:
            candidate = public_fallback[factor]
            source = "config/public_semi_synthetic_dataset_config.json:fallback_distributions"
        else:
            candidate = OFFLINE_EXPERT_FACTOR_DISTRIBUTIONS[factor]
            source = "expert_configured_offline_semi_synthetic_fallback_v1"

        configured_categories = set(model.categories[factor])
        supplied_categories = set(candidate)
        if supplied_categories != configured_categories:
            raise ValueError(
                f"Distribution categories for {factor} must exactly match configured categories; "
                f"missing={sorted(configured_categories - supplied_categories)}, "
                f"extra={sorted(supplied_categories - configured_categories)}."
            )
        ordered_weights = {category: float(candidate[category]) for category in model.categories[factor]}
        if not all(math.isfinite(weight) and weight >= 0.0 for weight in ordered_weights.values()):
            raise ValueError(f"Distribution weights for {factor} must be finite and non-negative.")
        total = sum(ordered_weights.values())
        if total <= 0.0:
            raise ValueError(f"Distribution weights for {factor} must have a positive sum.")
        resolved[factor] = {category: weight / total for category, weight in ordered_weights.items()}
        sources[factor] = source

    return resolved, sources, public_config


def _reference_config_provenance(
    model: _CompiledRiskModel,
    distributions: Mapping[str, Mapping[str, float]],
    public_config: Mapping[str, object],
) -> tuple[str, dict[str, str]]:
    """Return the canonical reference-config hash and its file-level bindings."""

    config_payload = {
        "hhvbp_global_config": model.global_config,
        "metric_parameter_library": model.metric_library,
        "public_semi_synthetic_dataset_config": public_config,
        "resolved_factor_distributions": distributions,
        "reference_schema_version": REFERENCE_SCHEMA_VERSION,
    }
    file_hashes = {
        model.global_path.name: _sha256_file(model.global_path),
        model.metric_path.name: _sha256_file(model.metric_path),
    }
    public_config_path = model.config_dir / "public_semi_synthetic_dataset_config.json"
    if public_config_path.exists():
        file_hashes[public_config_path.name] = _sha256_file(public_config_path)
    return _sha256_text(_canonical_json(config_payload)), file_hashes


def _validate_cy2025_lock(model: _CompiledRiskModel) -> None:
    expected = {
        "performance_year": 2025,
        "payment_year": 2027,
        "measure_set_version": "CY2025",
        "cms_version_locked": True,
    }
    actual = {key: model.global_config.get(key) for key in expected}
    if actual != expected:
        raise ValueError(
            "Risk reference validation requires the locked CY2025/PY2027 configuration; "
            f"expected {expected}, found {actual}."
        )


def _weighted_category(
    rng: random.Random,
    categories: Sequence[str],
    probabilities: Mapping[str, float],
) -> str:
    draw = rng.random()
    cumulative = 0.0
    for category in categories:
        cumulative += probabilities[category]
        if draw < cumulative:
            return category
    return categories[-1]


def generate_risk_reference(
    config_dir: str | Path = DEFAULT_CONFIG_DIR,
    sample_size: int = DEFAULT_MONTE_CARLO_SIZE,
    seed: int = DEFAULT_SEED,
    factor_distributions: Mapping[str, Mapping[str, float]] | None = None,
    source_mode: str | None = None,
    creation_timestamp: str | None = None,
) -> tuple[dict, list[dict]]:
    """Generate the weighted Monte Carlo reference and enriched exact grid."""

    sample_size = int(sample_size)
    if sample_size < MINIMUM_MONTE_CARLO_SIZE:
        raise ValueError(f"sample_size must be at least {MINIMUM_MONTE_CARLO_SIZE:,}.")
    seed = int(seed)
    model = _CompiledRiskModel(config_dir)
    distributions, distribution_sources, public_config = _resolve_factor_distributions(
        model,
        factor_distributions,
    )
    if source_mode is None:
        source_mode = str(public_config.get("mode", "offline_demo_fallback"))
    if not source_mode.strip():
        raise ValueError("source_mode must be a non-empty string.")
    timestamp = creation_timestamp or _utc_timestamp()

    base_grid = build_structural_grid(model.config_dir)
    score_by_profile = {
        tuple(row[factor] for factor in model.factor_order): float(row["rho_raw"])
        for row in base_grid
    }
    realized_counts = {
        factor: {category: 0 for category in model.categories[factor]}
        for factor in model.factor_order
    }
    rng = random.Random(seed)
    sampled_scores: list[float] = []
    for _ in range(sample_size):
        categories = tuple(
            _weighted_category(rng, model.categories[factor], distributions[factor])
            for factor in model.factor_order
        )
        for factor, category in zip(model.factor_order, categories):
            realized_counts[factor][category] += 1
        sampled_scores.append(score_by_profile[categories])
    sampled_scores.sort()

    quantiles = {
        f"{percentile / 100.0:.2f}": quantile_raw(sampled_scores, percentile / 100.0)
        for percentile in range(101)
    }
    tau_raw = quantile_raw(sampled_scores, THRESHOLD_PERCENTILE)
    config_hash, file_hashes = _reference_config_provenance(model, distributions, public_config)
    reference_id = _sha256_text(
        _canonical_json(
            {
                "config_hash": config_hash,
                "seed": seed,
                "sample_size": sample_size,
                "sorted_risk_scores": sampled_scores,
            }
        )
    )
    realized_proportions = {
        factor: {category: count / sample_size for category, count in category_counts.items()}
        for factor, category_counts in realized_counts.items()
    }
    metadata = {
        "artifact_type": "hhvbp_weighted_monte_carlo_risk_reference",
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "reference_id": reference_id,
        "creation_timestamp": timestamp,
        "created_at": timestamp,
        "source_mode": source_mode,
        "seed": seed,
        "sample_size": sample_size,
        "minimum_required_sample_size": MINIMUM_MONTE_CARLO_SIZE,
        "sampling_method": "independent_weighted_categorical_monte_carlo",
        "factor_order": list(model.factor_order),
        "factor_distributions": distributions,
        "factor_distribution_sources": distribution_sources,
        "realized_factor_counts": realized_counts,
        "realized_factor_distributions": realized_proportions,
        "config_hash_algorithm": "sha256_canonical_json",
        "config_hash": config_hash,
        "config_file_hashes_sha256": file_hashes,
        "structural_grid_size": len(base_grid),
        "structural_grid_expected_size": STRUCTURAL_GRID_SIZE,
        "risk_score_field": "rho_raw",
        "risk_score_interpretation": "aggregate patient need/risk index; not a probability",
        "empirical_percentile_method": "average_rank_for_ties_linear_between_support_points_clamped_0_1",
        "quantile_method": "type_7_linear_interpolation",
        "threshold_percentile": THRESHOLD_PERCENTILE,
        "tau_raw": tau_raw,
        "threshold_source": THRESHOLD_SOURCE,
        "public_data_only": True,
        "offline": True,
        "semi_synthetic": True,
        "synthetic_data": True,
        "expert_configured": True,
        "sponsor_data_used": False,
        "historical_sponsor_data": False,
        "outcome_validated": False,
        "production_validated": False,
        "data_source_warning": "offline_fallback_distributions",
        "provenance_label": THRESHOLD_SOURCE,
        "warning": (
            "Public-data-only, expert-configured offline semi-synthetic simulation; "
            "not sponsor historical data and not outcome validated."
        ),
        "risk_score_summary": {
            "minimum": sampled_scores[0],
            "maximum": sampled_scores[-1],
            "mean": fmean(sampled_scores),
        },
    }
    reference = {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "metadata": metadata,
        "sorted_risk_scores": sampled_scores,
        "quantiles": quantiles,
        "threshold_percentile": THRESHOLD_PERCENTILE,
        "tau_raw": tau_raw,
    }
    enriched_grid = build_structural_grid(
        model.config_dir,
        sorted_values=sampled_scores,
        threshold_percentile=THRESHOLD_PERCENTILE,
    )
    return reference, enriched_grid


def _resolve_reference_artifact_path(path_or_config_dir: str | Path | None) -> Path:
    if path_or_config_dir is None:
        return DEFAULT_REFERENCE_ARTIFACT_PATH
    supplied = Path(path_or_config_dir)
    if supplied.is_file():
        return supplied
    candidates = (
        supplied / DEFAULT_REFERENCE_ARTIFACT_PATH.name,
        supplied / "risk_reference.json",
        supplied / "data" / "reference" / DEFAULT_REFERENCE_ARTIFACT_PATH.name,
        supplied.parent / "data" / "reference" / DEFAULT_REFERENCE_ARTIFACT_PATH.name,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"Could not find {DEFAULT_REFERENCE_ARTIFACT_PATH.name!r} from {supplied}. "
        f"Checked: {[str(path) for path in candidates]}"
    )


def load_risk_reference(
    path_or_config_dir: str | Path | None = None,
    *,
    config_dir: str | Path = DEFAULT_CONFIG_DIR,
) -> dict:
    """Load a persisted reference only when it matches current CY2025 config and content."""

    artifact_path = _resolve_reference_artifact_path(path_or_config_dir)
    payload = _load_json(artifact_path)
    if not isinstance(payload.get("metadata"), dict):
        raise ValueError(f"Risk reference artifact {artifact_path} is missing metadata.")
    metadata = payload["metadata"]

    model = _CompiledRiskModel(config_dir)
    _validate_cy2025_lock(model)
    current_distributions, _, current_public_config = _resolve_factor_distributions(model, None)
    expected_config_hash, expected_file_hashes = _reference_config_provenance(
        model,
        current_distributions,
        current_public_config,
    )
    persisted_file_hashes = metadata.get("config_file_hashes_sha256")
    if persisted_file_hashes != expected_file_hashes:
        raise ValueError(
            f"Risk reference artifact {artifact_path} does not match the current component config files: "
            f"expected SHA256 values {expected_file_hashes}, found {persisted_file_hashes}."
        )
    if metadata.get("config_hash") != expected_config_hash:
        raise ValueError(
            f"Risk reference artifact {artifact_path} has stale or mismatched configuration provenance: "
            f"expected config_hash {expected_config_hash}, found {metadata.get('config_hash')}."
        )

    sorted_values = _coerce_sorted_values(payload.get("sorted_risk_scores", ()))
    if sorted_values != payload.get("sorted_risk_scores"):
        raise ValueError(f"Risk reference artifact {artifact_path} must persist scores in sorted order.")
    if int(metadata.get("sample_size", -1)) != len(sorted_values):
        raise ValueError(f"Risk reference artifact {artifact_path} has inconsistent sample_size metadata.")
    if len(sorted_values) < MINIMUM_MONTE_CARLO_SIZE:
        raise ValueError(
            f"Risk reference artifact {artifact_path} must contain at least "
            f"{MINIMUM_MONTE_CARLO_SIZE:,} scores."
        )

    try:
        seed = int(metadata["seed"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Risk reference artifact {artifact_path} has invalid seed metadata.") from exc
    expected_reference_id = _sha256_text(
        _canonical_json(
            {
                "config_hash": expected_config_hash,
                "seed": seed,
                "sample_size": len(sorted_values),
                "sorted_risk_scores": sorted_values,
            }
        )
    )
    if metadata.get("reference_id") != expected_reference_id:
        raise ValueError(
            f"Risk reference artifact {artifact_path} failed its content binding: "
            f"expected reference_id {expected_reference_id}, found {metadata.get('reference_id')}."
        )

    if float(payload.get("threshold_percentile", math.nan)) != THRESHOLD_PERCENTILE:
        raise ValueError(f"Risk reference artifact {artifact_path} has an unsupported threshold_percentile.")
    if float(metadata.get("threshold_percentile", math.nan)) != THRESHOLD_PERCENTILE:
        raise ValueError(f"Risk reference artifact {artifact_path} has inconsistent threshold metadata.")
    expected_tau = quantile_raw(sorted_values, THRESHOLD_PERCENTILE)
    if not math.isclose(float(payload.get("tau_raw", math.nan)), expected_tau, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(f"Risk reference artifact {artifact_path} has an inconsistent tau_raw value.")
    if not math.isclose(float(metadata.get("tau_raw", math.nan)), expected_tau, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(f"Risk reference artifact {artifact_path} has inconsistent tau_raw metadata.")

    expected_quantiles = {
        f"{percentile / 100.0:.2f}": quantile_raw(sorted_values, percentile / 100.0)
        for percentile in range(101)
    }
    persisted_quantiles = payload.get("quantiles")
    if not isinstance(persisted_quantiles, dict) or set(persisted_quantiles) != set(expected_quantiles):
        raise ValueError(f"Risk reference artifact {artifact_path} has incomplete quantile content.")
    if any(
        not math.isclose(
            float(persisted_quantiles[key]),
            value,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        for key, value in expected_quantiles.items()
    ):
        raise ValueError(f"Risk reference artifact {artifact_path} has quantiles inconsistent with its scores.")

    payload["sorted_risk_scores"] = sorted_values
    payload["artifact_path"] = str(artifact_path.resolve())
    payload["artifact_sha256"] = _sha256_file(artifact_path)
    return payload


def write_risk_reference_artifacts(
    reference: Mapping[str, object],
    structural_grid: Sequence[Mapping[str, object]],
    output_dir: str | Path = DEFAULT_REFERENCE_DIR,
) -> dict[str, Path]:
    """Persist the reference JSON, exact-grid CSV, and grid metadata sidecar."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    reference_path = output_dir / DEFAULT_REFERENCE_ARTIFACT_PATH.name
    grid_path = output_dir / DEFAULT_STRUCTURAL_GRID_PATH.name
    grid_metadata_path = output_dir / DEFAULT_STRUCTURAL_GRID_METADATA_PATH.name
    if len(structural_grid) != STRUCTURAL_GRID_SIZE:
        raise ValueError(
            f"structural_grid must contain exactly {STRUCTURAL_GRID_SIZE} rows; found {len(structural_grid)}."
        )
    reference_path.write_text(json.dumps(reference, indent=2) + "\n", encoding="utf-8")
    fieldnames = list(structural_grid[0].keys())
    with grid_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(structural_grid)
    metadata = reference["metadata"]
    grid_metadata = {
        "artifact_type": "hhvbp_exhaustive_structural_profile_grid",
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "creation_timestamp": metadata["creation_timestamp"],
        "reference_id": metadata["reference_id"],
        "config_hash": metadata["config_hash"],
        "row_count": len(structural_grid),
        "expected_row_count": STRUCTURAL_GRID_SIZE,
        "factor_order": metadata["factor_order"],
        "enumeration": "exact_cartesian_product_not_population_weighted",
        "risk_percentiles_against": reference_path.name,
        "risk_reference_artifact_sha256": _sha256_file(reference_path),
        "structural_grid_csv_sha256": _sha256_file(grid_path),
        "threshold_percentile": reference["threshold_percentile"],
        "tau_raw": reference["tau_raw"],
        "public_data_only": True,
        "semi_synthetic": True,
        "synthetic_data": True,
        "sponsor_data_used": False,
        "outcome_validated": False,
        "production_validated": False,
        "warning": metadata["warning"],
    }
    grid_metadata_path.write_text(json.dumps(grid_metadata, indent=2) + "\n", encoding="utf-8")
    return {
        "reference": reference_path,
        "structural_grid": grid_path,
        "structural_grid_metadata": grid_metadata_path,
    }


__all__ = [
    "DEFAULT_MONTE_CARLO_SIZE",
    "DEFAULT_REFERENCE_ARTIFACT_PATH",
    "DEFAULT_SEED",
    "DEFAULT_STRUCTURAL_GRID_PATH",
    "DEFAULT_STRUCTURAL_GRID_METADATA_PATH",
    "MINIMUM_MONTE_CARLO_SIZE",
    "STRUCTURAL_GRID_SIZE",
    "THRESHOLD_PERCENTILE",
    "THRESHOLD_SOURCE",
    "build_structural_grid",
    "empirical_risk_percentile",
    "generate_risk_reference",
    "load_risk_reference",
    "quantile_raw",
    "risk_score_for_profile",
    "write_risk_reference_artifacts",
]
