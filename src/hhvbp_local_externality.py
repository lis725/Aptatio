from __future__ import annotations

import json
import math
from statistics import NormalDist
from copy import deepcopy
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd

try:
    from scipy.stats import beta as scipy_beta_dist
except ImportError:  # pragma: no cover - exercised in lightweight runtimes.
    scipy_beta_dist = None

METRIC_ORDER = [
    "PPH",
    "DTC",
    "DFS",
    "Oral_Meds",
    "Dyspnea",
    "Recommend",
    "Agency_Rating",
    "Care_Issues",
    "Communications",
    "Care_of_Patients",
]

RAW_DIRECTION = {
    "PPH": "lower_is_better",
    "DTC": "higher_is_better",
    "DFS": "higher_is_better",
    "Oral_Meds": "higher_is_better",
    "Dyspnea": "higher_is_better",
    "Care_of_Patients": "higher_is_better",
    "Communications": "higher_is_better",
    "Care_Issues": "higher_is_better",
    "Agency_Rating": "higher_is_better",
    "Recommend": "higher_is_better",
}

AGE_GROUP_ORDER = [
    "age_40_50",
    "age_50_60",
    "age_60_70",
    "age_70_80",
    "age_80_plus",
]

CMS_VERSION_LOCK = {
    "cms_model": "Expanded HHVBP",
    "performance_year": 2025,
    "payment_year": 2027,
    "measure_set_version": "CY2025",
    "cms_version_locked": True,
}

SEVEN_FACTOR_ORDER = [
    "health_status",
    "age_group",
    "house_price_zip",
    "area_type",
    "housing_type",
    "distance_to_clinic",
    "driving_condition",
]

CY2025_METRIC_WEIGHTS = {
    "DFS": 0.20,
    "Dyspnea": 0.06,
    "Oral_Meds": 0.09,
    "DTC": 0.09,
    "PPH": 0.26,
    "Care_of_Patients": 0.06,
    "Communications": 0.06,
    "Care_Issues": 0.06,
    "Agency_Rating": 0.06,
    "Recommend": 0.06,
}

REQUIRED_CANONICAL_METRIC_ALIASES = {
    "DFS": {"DFS", "DC Function", "Discharge Function Score"},
    "Dyspnea": {"Dyspnea", "Improvement in Dyspnea"},
    "Oral_Meds": {"Oral_Meds", "Improvement in Management of Oral Medications"},
    "DTC": {"DTC", "DTC-PAC", "Discharge to Community"},
    "PPH": {"PPH", "Potentially Preventable Hospitalization"},
    "Care_of_Patients": {"Care_of_Patients", "Care of Patients"},
    "Communications": {"Communications", "Communications Between Providers and Patients"},
    "Care_Issues": {"Care_Issues", "Specific Care Issues"},
    "Agency_Rating": {"Agency_Rating", "Overall Rating"},
    "Recommend": {"Recommend", "Willingness to Recommend"},
}

AGE_GROUP_RAW_ALIASES = {
    "40_49": "age_40_50",
    "50_59": "age_50_60",
    "60_69": "age_60_70",
    "70_79": "age_70_80",
    "80_89": "age_80_plus",
    "80_plus": "age_80_plus",
    "80+": "age_80_plus",
    "90_plus": "age_80_plus",
    "90+": "age_80_plus",
}

AGE_NUMERIC_BOUNDARIES = {
    "age_40_50": {
        "minimum": 40,
        "minimum_inclusive": True,
        "maximum": 50,
        "maximum_inclusive": False,
    },
    "age_50_60": {
        "minimum": 50,
        "minimum_inclusive": True,
        "maximum": 60,
        "maximum_inclusive": False,
    },
    "age_60_70": {
        "minimum": 60,
        "minimum_inclusive": True,
        "maximum": 70,
        "maximum_inclusive": False,
    },
    "age_70_80": {
        "minimum": 70,
        "minimum_inclusive": True,
        "maximum": 80,
        "maximum_inclusive": True,
    },
    "age_80_plus": {
        "minimum": 80,
        "minimum_inclusive": False,
        "maximum": None,
        "maximum_inclusive": False,
    },
}

AGE_BASE_FAVORABILITY = {
    "age_80_plus": 0.22,
    "age_70_80": 0.36,
    "age_60_70": 0.50,
    "age_50_60": 0.62,
    "age_40_50": 0.72,
}

AGE_EXPERT_ELICITATION = {
    "PPH": ("strong", 60, 75, 90),
    "DTC": ("strong", 55, 70, 85),
    "DFS": ("strong", 50, 65, 80),
    "Oral_Meds": ("medium", 45, 60, 75),
    "Dyspnea": ("medium", 35, 50, 65),
    "Recommend": ("weak", 20, 30, 40),
    "Agency_Rating": ("weak", 20, 30, 40),
    "Care_Issues": ("medium", 35, 45, 55),
    "Communications": ("medium", 30, 40, 50),
    "Care_of_Patients": ("medium", 30, 40, 50),
}


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalize_age_group(age_group: object, global_cfg: dict | None = None) -> str:
    """Return the canonical category for a configured raw age-band alias.

    Unknown values are returned unchanged so the existing patient-profile
    validator remains the single source of the public validation error.
    """
    token = str(age_group).strip()
    aliases = AGE_GROUP_RAW_ALIASES
    if global_cfg is not None:
        aliases = global_cfg.get("age_configuration", {}).get("raw_band_aliases", aliases)
    if token in AGE_GROUP_ORDER:
        return token
    return str(aliases.get(token, token))


def derive_age_group(age: float | int | str) -> str:
    """Convert a supported numeric age or raw database band into age_group."""
    if isinstance(age, str):
        token = age.strip()
        normalized_band = normalize_age_group(token)
        if normalized_band != token or normalized_band in AGE_GROUP_ORDER:
            return normalized_band
    try:
        numeric_age = float(age)
    except (TypeError, ValueError) as exc:
        raise ValueError("Patient age must be numeric when age_group is not supplied.") from exc

    if not np.isfinite(numeric_age):
        raise ValueError("Patient age must be finite when age_group is not supplied.")
    if numeric_age > 80:
        return "age_80_plus"
    if 70 <= numeric_age <= 80:
        return "age_70_80"
    if 60 <= numeric_age < 70:
        return "age_60_70"
    if 50 <= numeric_age < 60:
        return "age_50_60"
    if 40 <= numeric_age < 50:
        return "age_40_50"
    raise ValueError("Patient age must be in the supported range age >= 40.")


def normalize_patient_profile(patient_profile: dict, global_cfg: dict) -> dict:
    """Copy a patient profile and canonicalize supported age-band aliases."""
    normalized = dict(patient_profile)
    if "age_group" in normalized:
        normalized["age_group"] = normalize_age_group(normalized["age_group"], global_cfg)
    return normalized


def pert_mean(low: float, mode: float, high: float) -> float:
    return (low + 4.0 * mode + high) / 6.0


def symmetric_multiplier_map(delta: float) -> Dict[str, float]:
    return {"strong": math.exp(delta), "medium": 1.0, "weak": math.exp(-delta)}


def reliability_to_kappa(q: float, intercept: float, slope: float) -> float:
    return intercept + slope * q


def adjust_favorability(base_value: float, gap_scale: float) -> float:
    value = 0.5 + gap_scale * (base_value - 0.5)
    return min(max(value, 1e-6), 1.0 - 1e-6)


def get_metric_favorability(global_cfg: dict, metric_cfg: dict) -> Dict[str, Dict[str, float]]:
    favorability = deepcopy(global_cfg["base_favorability"])
    overrides = metric_cfg.get("favorability_overrides", {})
    for factor, category_map in overrides.items():
        if factor not in favorability:
            raise KeyError(f"Unknown factor in favorability_overrides: {factor}")
        for category, value in category_map.items():
            if category not in favorability[factor]:
                raise KeyError(f"Unknown category override for {factor}: {category}")
            favorability[factor][category] = float(value)
    return favorability


def parse_display_direction(raw_value: str | None) -> float:
    if raw_value is None:
        return 1.0
    value = str(raw_value).strip().lower()
    if value in {"higher_is_better", "higher", "positive", "+", "ascending"}:
        return 1.0
    if value in {"lower_is_better", "lower", "negative", "-", "descending"}:
        return -1.0
    raise ValueError(f"Unsupported display direction: {raw_value!r}")


def resolve_display_calibration(metric_key: str, metric_cfg: dict, global_cfg: dict, best_dist: dict, worst_dist: dict) -> dict:
    display_cfg = global_cfg.get("display_scale", {})
    national_average_map = display_cfg.get("national_average_by_metric", {})
    direction_map = display_cfg.get("display_direction_by_metric", {})
    units_map = display_cfg.get("units_per_latent_point_by_metric", {})
    x_label_map = display_cfg.get("x_label_by_metric", {})

    national_average = metric_cfg.get("national_average", national_average_map.get(metric_key))
    units_value = metric_cfg.get(
        "display_units_per_latent_point",
        units_map.get(metric_key, display_cfg.get("units_per_latent_point", 1.0)),
    )
    units_per_latent_point = float(units_value)
    if units_per_latent_point <= 0:
        raise ValueError(f"display_units_per_latent_point must be > 0 for {metric_key}")

    raw_direction = metric_cfg.get("display_direction", direction_map.get(metric_key, display_cfg.get("default_direction")))
    direction = parse_display_direction(raw_direction)
    latent_midpoint = 0.5 * (float(best_dist["latent_mean"]) + float(worst_dist["latent_mean"]))

    use_display_scale = national_average is not None
    x_label = metric_cfg.get("display_x_label", x_label_map.get(metric_key))
    if not x_label:
        if use_display_scale:
            x_label = display_cfg.get("default_x_label", f"{metric_key} displayed scale")
        else:
            x_label = f"{metric_key} latent score (0-100)"

    return {
        "use_display_scale": use_display_scale,
        "national_average": None if national_average is None else float(national_average),
        "units_per_latent_point": units_per_latent_point,
        "direction": direction,
        "latent_midpoint": latent_midpoint,
        "x_label": x_label,
    }


def transform_scalar_for_display(value: float | np.ndarray, calibration: dict) -> float | np.ndarray:
    if not calibration["use_display_scale"]:
        return np.array(value, copy=True) if isinstance(value, np.ndarray) else float(value)
    transformed = calibration["national_average"] + calibration["direction"] * calibration["units_per_latent_point"] * (np.asarray(value, dtype=float) - calibration["latent_midpoint"])
    return transformed if isinstance(value, np.ndarray) else float(transformed)


def apply_display_calibration_to_distribution(dist: dict, calibration: dict) -> dict:
    if not calibration["use_display_scale"]:
        dist["display_mean"] = float(dist["latent_mean"])
        dist["display_sd"] = float(dist["latent_sd"])
        dist["display_q10"] = float(dist["q10"])
        dist["display_q50"] = float(dist["q50"])
        dist["display_q90"] = float(dist["q90"])
        return dist

    direction = calibration["direction"]
    scale = calibration["units_per_latent_point"]
    if direction < 0:
        display_q10 = transform_scalar_for_display(dist["q90"], calibration)
        display_q50 = transform_scalar_for_display(dist["q50"], calibration)
        display_q90 = transform_scalar_for_display(dist["q10"], calibration)
    else:
        display_q10 = transform_scalar_for_display(dist["q10"], calibration)
        display_q50 = transform_scalar_for_display(dist["q50"], calibration)
        display_q90 = transform_scalar_for_display(dist["q90"], calibration)

    dist["display_mean"] = transform_scalar_for_display(dist["latent_mean"], calibration)
    dist["display_sd"] = float(abs(scale) * dist["latent_sd"])
    dist["display_q10"] = float(display_q10)
    dist["display_q50"] = float(display_q50)
    dist["display_q90"] = float(display_q90)
    return dist


def build_expert_prior_for_metric(global_cfg: dict, metric_cfg: dict) -> pd.DataFrame:
    rows = []
    for factor in global_cfg["factor_order"]:
        elic = metric_cfg["expert_elicitation"][factor]
        raw = pert_mean(float(elic["low"]), float(elic["mode"]), float(elic["high"]))
        rows.append({
            "externality": factor,
            "expert_swing_raw": raw,
            "importance_level": metric_cfg["importance_levels"][factor],
        })
    df = pd.DataFrame(rows)
    health_raw = float(df.loc[df["externality"] == "health_status", "expert_swing_raw"].iloc[0])
    if health_raw <= 0:
        raise ValueError("health_status swing must be > 0 for every metric")
    df["expert_relative_to_health"] = df["expert_swing_raw"] / health_raw
    total = float(df["expert_relative_to_health"].sum())
    df["baseline_prior"] = 100.0 * df["expert_relative_to_health"] / total
    return df


def deployed_weights_for_metric(metric_cfg: dict, prior_df: pd.DataFrame) -> pd.DataFrame:
    baseline_map = {row["externality"]: float(row["baseline_prior"]) for _, row in prior_df.iterrows()}
    levels = metric_cfg["importance_levels"]
    mult = symmetric_multiplier_map(float(metric_cfg["delta"]))
    raw = {factor: baseline_map[factor] * mult[levels[factor]] for factor in baseline_map}
    total = float(sum(raw.values()))
    rows = []
    for factor in sorted(raw):
        rows.append({
            "externality": factor,
            "runtime_weight": raw[factor] / total,
            "score_range": 100.0 * raw[factor] / total,
        })
    return pd.DataFrame(rows)


def factor_moment_table(global_cfg: dict, metric_cfg: dict, profile: Dict[str, str], score_ranges: Dict[str, float]) -> pd.DataFrame:
    rows = []
    intercept = float(global_cfg["distribution"]["kappa_intercept"])
    slope = float(global_cfg["distribution"]["kappa_slope"])
    metric_favorability = get_metric_favorability(global_cfg, metric_cfg)
    gap_scale = float(metric_cfg["gap_scale"])
    reliability = global_cfg["reliability"]

    for factor, category in profile.items():
        range_max = float(score_ranges[factor])
        base_value = float(metric_favorability[factor][category])
        mean_prop = adjust_favorability(base_value, gap_scale)
        q = float(reliability[factor])
        kappa = reliability_to_kappa(q, intercept, slope)
        mean_contribution = range_max * mean_prop
        variance_contribution = (range_max ** 2) * mean_prop * (1.0 - mean_prop) / (kappa + 1.0)
        rows.append({
            "externality": factor,
            "category": category,
            "mean_contribution": mean_contribution,
            "variance_contribution": variance_contribution,
        })
    return pd.DataFrame(rows)


def beta_from_moments(mean_value: float, variance_value: float, scale: float = 100.0) -> Tuple[float, float]:
    eps = 1e-10
    m = min(max(mean_value / scale, eps), 1.0 - eps)
    max_var = m * (1.0 - m) - eps
    v = min(max(variance_value / (scale ** 2), eps), max_var)
    common = m * (1.0 - m) / v - 1.0
    alpha = max(m * common, eps)
    beta = max((1.0 - m) * common, eps)
    return alpha, beta


def beta_ppf(probability: float, alpha: float, beta: float) -> float:
    """Return a beta quantile using SciPy when present, otherwise a stable approximation."""
    if scipy_beta_dist is not None:
        return float(scipy_beta_dist.ppf(probability, alpha, beta))

    mean = alpha / (alpha + beta)
    variance = (alpha * beta) / (((alpha + beta) ** 2) * (alpha + beta + 1.0))
    sd = math.sqrt(max(variance, 1e-12))
    approx = NormalDist(mu=mean, sigma=sd).inv_cdf(probability)
    return float(min(max(approx, 0.0), 1.0))


def build_profile_distribution(global_cfg: dict, metric_cfg: dict, profile: Dict[str, str], score_ranges: Dict[str, float]) -> dict:
    factor_df = factor_moment_table(global_cfg, metric_cfg, profile, score_ranges)
    mean_total = float(factor_df["mean_contribution"].sum())
    var_total = float(factor_df["variance_contribution"].sum())
    sd_total = math.sqrt(var_total)
    alpha_total, beta_total = beta_from_moments(mean_total, var_total, scale=100.0)
    return {
        "latent_mean": mean_total,
        "latent_sd": sd_total,
        "q10": float(100.0 * beta_ppf(0.10, alpha_total, beta_total)),
        "q50": float(100.0 * beta_ppf(0.50, alpha_total, beta_total)),
        "q90": float(100.0 * beta_ppf(0.90, alpha_total, beta_total)),
    }


def validate_model_config(global_cfg: dict, metric_library: dict) -> None:
    factor_order = global_cfg.get("factor_order", [])
    if not factor_order:
        raise ValueError("hhvbp_global_config.factor_order must contain at least one factor.")

    required_global_sections = ["reliability", "base_favorability", "profiles"]
    for section in required_global_sections:
        if section not in global_cfg:
            raise ValueError(f"hhvbp_global_config is missing required section {section!r}.")

    profiles = global_cfg["profiles"]
    for profile_name in ["best_case", "worst_case"]:
        if profile_name not in profiles:
            raise ValueError(f"hhvbp_global_config.profiles is missing {profile_name!r}.")
        missing = [factor for factor in factor_order if factor not in profiles[profile_name]]
        if missing:
            raise ValueError(f"Profile {profile_name!r} is missing factors: {missing}")

    for factor in factor_order:
        if factor not in global_cfg["reliability"]:
            raise ValueError(f"Reliability is missing factor {factor!r}.")
        if factor not in global_cfg["base_favorability"]:
            raise ValueError(f"Base favorability is missing factor {factor!r}.")
        allowed = set(global_cfg["base_favorability"][factor].keys())
        for profile_name in ["best_case", "worst_case"]:
            value = profiles[profile_name][factor]
            if value not in allowed:
                raise ValueError(
                    f"Profile {profile_name!r} uses invalid category {value!r} for {factor!r}. "
                    f"Allowed values: {sorted(allowed)}"
                )

    metrics = metric_library.get("metrics", {})
    if not metrics:
        raise ValueError("metric_parameter_library.metrics must contain at least one metric.")
    for metric_key, metric_cfg in metrics.items():
        for section in ["importance_levels", "expert_elicitation"]:
            if section not in metric_cfg:
                raise ValueError(f"Metric {metric_key!r} is missing {section!r}.")
            missing = [factor for factor in factor_order if factor not in metric_cfg[section]]
            if missing:
                raise ValueError(f"Metric {metric_key!r} {section!r} is missing factors: {missing}")

    for config_name, config in [
        ("hhvbp_global_config", global_cfg),
        ("metric_parameter_library", metric_library),
    ]:
        for field, expected in CMS_VERSION_LOCK.items():
            if config.get(field) != expected:
                raise ValueError(
                    f"{config_name}.{field} must be {expected!r} for the locked Expanded HHVBP CY2025 configuration."
                )

    if factor_order != SEVEN_FACTOR_ORDER:
        raise ValueError(
            "hhvbp_global_config.factor_order must contain exactly the seven locked factors "
            f"in canonical order: {SEVEN_FACTOR_ORDER}"
        )
    expected_factor_set = set(SEVEN_FACTOR_ORDER)
    for section in ["reliability", "base_favorability"]:
        section_factors = set(global_cfg[section])
        if section_factors != expected_factor_set:
            raise ValueError(
                f"hhvbp_global_config.{section} must contain exactly the seven locked factors; "
                f"found {sorted(section_factors)}."
            )
    for profile_name in ["best_case", "worst_case"]:
        profile_factors = set(profiles[profile_name])
        if profile_factors != expected_factor_set:
            raise ValueError(
                f"hhvbp_global_config.profiles.{profile_name} must contain exactly the seven locked factors; "
                f"found {sorted(profile_factors)}."
            )
    for metric_key, metric_cfg in metrics.items():
        for section in ["importance_levels", "expert_elicitation"]:
            section_factors = set(metric_cfg[section])
            if section_factors != expected_factor_set:
                raise ValueError(
                    f"Metric {metric_key!r} {section!r} must contain exactly the seven locked factors; "
                    f"found {sorted(section_factors)}."
                )

    if float(global_cfg["reliability"].get("age_group", -1.0)) != 0.95:
        raise ValueError("hhvbp_global_config.reliability.age_group must equal 0.95.")
    if global_cfg["base_favorability"].get("age_group") != AGE_BASE_FAVORABILITY:
        raise ValueError(
            "hhvbp_global_config.base_favorability.age_group must equal the locked five-category CY2025 age configuration."
        )

    age_cfg = global_cfg.get("age_configuration")
    if not isinstance(age_cfg, dict):
        raise ValueError("hhvbp_global_config.age_configuration must be an object.")
    if age_cfg.get("best_category") != "age_40_50":
        raise ValueError("hhvbp_global_config.age_configuration.best_category must equal 'age_40_50'.")
    if age_cfg.get("worst_category") != "age_80_plus":
        raise ValueError("hhvbp_global_config.age_configuration.worst_category must equal 'age_80_plus'.")
    if age_cfg.get("numeric_age_boundaries") != AGE_NUMERIC_BOUNDARIES:
        raise ValueError("hhvbp_global_config.age_configuration.numeric_age_boundaries does not match the locked age rules.")
    if age_cfg.get("raw_band_aliases") != AGE_GROUP_RAW_ALIASES:
        raise ValueError("hhvbp_global_config.age_configuration.raw_band_aliases does not match the locked alias map.")
    if profiles["best_case"].get("age_group") != "age_40_50":
        raise ValueError("hhvbp_global_config.profiles.best_case.age_group must equal 'age_40_50'.")
    if profiles["worst_case"].get("age_group") != "age_80_plus":
        raise ValueError("hhvbp_global_config.profiles.worst_case.age_group must equal 'age_80_plus'.")

    if set(metrics) != set(CY2025_METRIC_WEIGHTS):
        raise ValueError(
            "metric_parameter_library.metrics must contain exactly the ten Expanded HHVBP CY2025 metrics; "
            f"expected {sorted(CY2025_METRIC_WEIGHTS)}, found {sorted(metrics)}."
        )
    actual_weights = {metric: float(metrics[metric].get("composite_weight", -1.0)) for metric in metrics}
    if actual_weights != CY2025_METRIC_WEIGHTS:
        raise ValueError(
            "metric_parameter_library composite weights do not match the locked Expanded HHVBP CY2025 weights."
        )
    if math.fsum(actual_weights.values()) != 1.0:
        raise ValueError("Expanded HHVBP CY2025 composite weights must sum exactly to 1.00.")

    canonical_aliases = metric_library.get("canonical_metric_aliases")
    if not isinstance(canonical_aliases, dict):
        raise ValueError("metric_parameter_library.canonical_metric_aliases must be an object.")
    if set(canonical_aliases) != set(CY2025_METRIC_WEIGHTS):
        raise ValueError("canonical_metric_aliases must define exactly one alias list for each locked CY2025 metric.")
    claimed_aliases: dict[str, str] = {}
    for canonical, required_aliases in REQUIRED_CANONICAL_METRIC_ALIASES.items():
        aliases = canonical_aliases.get(canonical)
        if not isinstance(aliases, list) or not aliases:
            raise ValueError(f"canonical_metric_aliases[{canonical!r}] must be a non-empty list.")
        missing_aliases = sorted(required_aliases.difference(str(alias) for alias in aliases))
        if missing_aliases:
            raise ValueError(f"canonical_metric_aliases[{canonical!r}] is missing required aliases: {missing_aliases}")
        for alias in aliases:
            normalized_alias = " ".join(str(alias).strip().casefold().split())
            if not normalized_alias:
                raise ValueError(f"canonical_metric_aliases[{canonical!r}] contains an empty alias.")
            previous = claimed_aliases.get(normalized_alias)
            if previous is not None and previous != canonical:
                raise ValueError(
                    f"Metric alias {alias!r} is assigned to both {previous!r} and {canonical!r}; aliases must be unambiguous."
                )
            claimed_aliases[normalized_alias] = canonical

    for metric_key, (importance, low, mode, high) in AGE_EXPERT_ELICITATION.items():
        metric_cfg = metrics[metric_key]
        if metric_cfg["importance_levels"].get("age_group") != importance:
            raise ValueError(f"Metric {metric_key!r} has an incorrect locked age_group importance level.")
        elicitation = metric_cfg["expert_elicitation"].get("age_group", {})
        expected_elicitation = {
            "best_category": "age_40_50",
            "worst_category": "age_80_plus",
            "low": low,
            "mode": mode,
            "high": high,
        }
        if elicitation != expected_elicitation:
            raise ValueError(f"Metric {metric_key!r} has an incorrect locked age_group expert elicitation.")


def validate_patient_profile(patient_profile: dict, global_cfg: dict) -> None:
    missing = [factor for factor in global_cfg["factor_order"] if factor not in patient_profile]
    if missing:
        raise ValueError(f"Missing patient factors: {missing}")
    normalized_profile = normalize_patient_profile(patient_profile, global_cfg)
    for factor in global_cfg["factor_order"]:
        allowed = list(global_cfg["base_favorability"][factor].keys())
        value = normalized_profile[factor]
        if value not in allowed:
            raise ValueError(f"Invalid category for {factor}: {value!r}. Allowed values: {allowed}")


def build_best_worst_anchor_table(global_cfg: dict, metric_library: dict) -> pd.DataFrame:
    rows = []
    for metric_key, metric_cfg in metric_library["metrics"].items():
        prior_df = build_expert_prior_for_metric(global_cfg, metric_cfg)
        deployed_df = deployed_weights_for_metric(metric_cfg, prior_df)
        score_ranges = {row["externality"]: float(row["score_range"]) for _, row in deployed_df.iterrows()}
        best_dist = build_profile_distribution(global_cfg, metric_cfg, global_cfg["profiles"]["best_case"], score_ranges)
        worst_dist = build_profile_distribution(global_cfg, metric_cfg, global_cfg["profiles"]["worst_case"], score_ranges)
        calibration = resolve_display_calibration(metric_key, metric_cfg, global_cfg, best_dist, worst_dist)
        apply_display_calibration_to_distribution(best_dist, calibration)
        apply_display_calibration_to_distribution(worst_dist, calibration)
        rows.append({
            "metric_key": metric_key,
            "display_best_mean": float(best_dist["display_mean"]),
            "display_worst_mean": float(worst_dist["display_mean"]),
        })
    return pd.DataFrame(rows)


def build_patient_need_vector(config_dir: str | Path, patient_profile: dict) -> pd.DataFrame:
    config_dir = Path(config_dir)
    global_cfg = load_json(config_dir / "hhvbp_global_config.json")
    metric_library = load_json(config_dir / "metric_parameter_library.json")
    validate_model_config(global_cfg, metric_library)
    normalized_profile = normalize_patient_profile(patient_profile, global_cfg)
    validate_patient_profile(normalized_profile, global_cfg)
    factor_profile = {factor: normalized_profile[factor] for factor in global_cfg["factor_order"]}
    anchor_df = build_best_worst_anchor_table(global_cfg, metric_library)

    rows = []
    for metric_key, metric_cfg in metric_library["metrics"].items():
        prior_df = build_expert_prior_for_metric(global_cfg, metric_cfg)
        deployed_df = deployed_weights_for_metric(metric_cfg, prior_df)
        score_ranges = {row["externality"]: float(row["score_range"]) for _, row in deployed_df.iterrows()}
        best_dist = build_profile_distribution(global_cfg, metric_cfg, global_cfg["profiles"]["best_case"], score_ranges)
        worst_dist = build_profile_distribution(global_cfg, metric_cfg, global_cfg["profiles"]["worst_case"], score_ranges)
        calibration = resolve_display_calibration(metric_key, metric_cfg, global_cfg, best_dist, worst_dist)
        patient_dist = build_profile_distribution(global_cfg, metric_cfg, factor_profile, score_ranges)
        apply_display_calibration_to_distribution(patient_dist, calibration)

        anchor_row = anchor_df.loc[anchor_df["metric_key"] == metric_key]
        best_mean = float(anchor_row["display_best_mean"].iloc[0])
        worst_mean = float(anchor_row["display_worst_mean"].iloc[0])
        patient_mean = float(patient_dist["display_mean"])

        if RAW_DIRECTION[metric_key] == "higher_is_better":
            denominator = best_mean - worst_mean
            need_mean = 0.0 if denominator == 0 else (best_mean - patient_mean) / denominator
            conservative_need = 0.0 if denominator == 0 else (best_mean - float(patient_dist["display_q10"])) / denominator
        else:
            denominator = worst_mean - best_mean
            need_mean = 0.0 if denominator == 0 else (patient_mean - best_mean) / denominator
            conservative_need = 0.0 if denominator == 0 else (float(patient_dist["display_q90"]) - best_mean) / denominator

        rows.append({
            "metric_key": metric_key,
            "display_name": metric_cfg["display_name"],
            "metric_weight": float(metric_cfg["composite_weight"]),
            "patient_display_mean": patient_mean,
            "patient_display_sd": float(patient_dist["display_sd"]),
            "patient_display_q10": float(patient_dist["display_q10"]),
            "patient_display_q50": float(patient_dist["display_q50"]),
            "patient_display_q90": float(patient_dist["display_q90"]),
            "best_display_mean": best_mean,
            "worst_display_mean": worst_mean,
            "need_mean": float(np.clip(need_mean, 0.0, 1.0)),
            "need_conservative": float(np.clip(conservative_need, 0.0, 1.0)),
            "weighted_need_mean": float(metric_cfg["composite_weight"]) * float(np.clip(need_mean, 0.0, 1.0)),
            "weighted_need_conservative": float(metric_cfg["composite_weight"]) * float(np.clip(conservative_need, 0.0, 1.0)),
        })

    return pd.DataFrame(rows).sort_values("metric_weight", ascending=False).reset_index(drop=True)
