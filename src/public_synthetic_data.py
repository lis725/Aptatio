from __future__ import annotations

import json
import math
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from hhvbp_assignment_service import ALL_DISCIPLINES, assign_clinicians
from hhvbp_local_externality import METRIC_ORDER, RAW_DIRECTION, build_patient_need_vector

DEFAULT_SERVICE_ZIPS = [
    {"zip5": "89501", "lat": 39.5260, "lon": -119.8138, "area_type": "metropolitan", "population_weight": 1.00, "house_price_zip": "average", "health_proxy": 0.50},
    {"zip5": "89502", "lat": 39.5051, "lon": -119.7898, "area_type": "metropolitan", "population_weight": 1.25, "house_price_zip": "low", "health_proxy": 0.65},
    {"zip5": "89503", "lat": 39.5405, "lon": -119.8437, "area_type": "metropolitan", "population_weight": 0.90, "house_price_zip": "average", "health_proxy": 0.55},
    {"zip5": "89506", "lat": 39.6214, "lon": -119.8421, "area_type": "metropolitan", "population_weight": 0.85, "house_price_zip": "average", "health_proxy": 0.52},
    {"zip5": "89509", "lat": 39.4934, "lon": -119.8246, "area_type": "metropolitan", "population_weight": 0.95, "house_price_zip": "high", "health_proxy": 0.38},
    {"zip5": "89523", "lat": 39.5281, "lon": -119.9075, "area_type": "metropolitan", "population_weight": 0.80, "house_price_zip": "high", "health_proxy": 0.36},
    {"zip5": "89431", "lat": 39.5400, "lon": -119.7480, "area_type": "metropolitan", "population_weight": 0.95, "house_price_zip": "low", "health_proxy": 0.62},
    {"zip5": "89433", "lat": 39.6001, "lon": -119.7750, "area_type": "metropolitan", "population_weight": 0.55, "house_price_zip": "average", "health_proxy": 0.54},
    {"zip5": "89434", "lat": 39.5448, "lon": -119.7091, "area_type": "metropolitan", "population_weight": 0.90, "house_price_zip": "average", "health_proxy": 0.49},
    {"zip5": "89436", "lat": 39.6260, "lon": -119.6940, "area_type": "micropolitan", "population_weight": 0.60, "house_price_zip": "average", "health_proxy": 0.47},
]

DEFAULT_AGE_DISTRIBUTION = {
    "age_40_50": 0.08,
    "age_50_60": 0.17,
    "age_60_70": 0.27,
    "age_70_80": 0.30,
    "age_80_plus": 0.18,
}

DEFAULT_BASELINES = {
    "PPH": 0.135,
    "DTC": 0.74,
    "DFS": 0.78,
    "Oral_Meds": 0.84,
    "Dyspnea": 0.91,
    "Care_of_Patients": 0.90,
    "Communications": 0.86,
    "Care_Issues": 0.87,
    "Agency_Rating": 0.88,
    "Recommend": 0.84,
}

REQUIRED_EPISODE_COLUMNS = [
    "episode_id_random",
    "patient_id_random",
    "service_month",
    "zip5_or_zcta",
    "patient_zip_for_routing",
    "age",
    "age_group",
    "health_status",
    "house_price_zip",
    "area_type",
    "housing_type",
    "distance_to_clinic",
    "driving_condition",
    "risk_score_rho",
    "risk_score_rho_raw",
    "risk_percentile_u",
    "assigned_RN_id",
    "assigned_PT_id",
    "assigned_OT_id",
    "recommended_RN_id",
    "recommended_PT_id",
    "recommended_OT_id",
    "route_RN",
    "route_PT",
    "route_OT",
]


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(obj: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    return path


def service_zip_df_from_config(config: dict) -> pd.DataFrame:
    rows = config.get("market", {}).get("service_zips") or DEFAULT_SERVICE_ZIPS
    df = pd.DataFrame(rows).copy()
    required = {"zip5", "lat", "lon", "area_type", "population_weight"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"market.service_zips is missing required fields: {sorted(missing)}")
    df["zip5"] = df["zip5"].astype(str).str.zfill(5)
    if "house_price_zip" not in df.columns:
        df["house_price_zip"] = "average"
    if "health_proxy" not in df.columns:
        df["health_proxy"] = 0.5
    return df


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def weighted_choice(rng: np.random.Generator, values: list[Any], weights: list[float]) -> Any:
    probs = np.array(weights, dtype=float)
    probs = probs / probs.sum()
    return values[int(rng.choice(len(values), p=probs))]


def age_from_group(rng: np.random.Generator, age_group: str) -> float:
    ranges = {
        "age_40_50": (40, 49.9),
        "age_50_60": (50, 59.9),
        "age_60_70": (60, 69.9),
        "age_70_80": (70, 80.0),
        "age_80_plus": (80.1, 92.0),
    }
    low, high = ranges[age_group]
    return round(float(rng.uniform(low, high)), 1)


def sample_age_group(rng: np.random.Generator, config: dict) -> str:
    distribution = config.get("fallback_distributions", {}).get("age_group", DEFAULT_AGE_DISTRIBUTION)
    return weighted_choice(rng, list(distribution.keys()), list(distribution.values()))


def sample_health_status(rng: np.random.Generator, age_group: str, health_proxy: float) -> str:
    age_risk = {
        "age_40_50": 0.05,
        "age_50_60": 0.12,
        "age_60_70": 0.22,
        "age_70_80": 0.34,
        "age_80_plus": 0.46,
    }[age_group]
    risk = min(max(0.15 + age_risk + 0.45 * (float(health_proxy) - 0.5), 0.05), 0.85)
    unhealthy = risk
    moderate = min(0.65, 0.35 + 0.20 * risk)
    healthy = max(0.05, 1.0 - unhealthy - moderate)
    total = healthy + moderate + unhealthy
    return weighted_choice(rng, ["healthy", "moderate", "unhealthy"], [healthy / total, moderate / total, unhealthy / total])


def distance_category(distance_miles: float) -> str:
    if distance_miles <= 8:
        return "near"
    if distance_miles <= 20:
        return "medium"
    return "far"


def driving_condition_from_area(rng: np.random.Generator, area_type: str, distance: str) -> str:
    bad_prob = 0.15
    if area_type in {"rural", "small_town"}:
        bad_prob += 0.25
    if distance == "far":
        bad_prob += 0.20
    return "bad_winter" if rng.random() < min(bad_prob, 0.75) else "good_summer"


def discipline_sequence_id(discipline: str, sequence: int) -> int:
    prefix = {"RN": 1, "PT": 2, "OT": 3}[discipline]
    return prefix * 100 + sequence


def default_radius_config() -> dict:
    return {
        "RN": {"metropolitan": 16, "micropolitan": 22, "small_town": 35, "rural": 45},
        "PT": {"metropolitan": 18, "micropolitan": 25, "small_town": 38, "rural": 48},
        "OT": {"metropolitan": 22, "micropolitan": 30, "small_town": 44, "rural": 55},
    }


def generate_public_synthetic_clinicians(config: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    seed = int(config.get("seed", 20260608))
    rng = np.random.default_rng(seed)
    service_zips = service_zip_df_from_config(config)
    roster_size = config.get("roster_size_by_discipline", {"RN": 12, "PT": 10, "OT": 3})
    radius_cfg = config.get("service_radius_miles_by_area_type", default_radius_config())
    quality_cfg = config.get("quality_distribution_config", {})
    quality_mean = float(quality_cfg.get("mean", 0.76))
    quality_sd = float(quality_cfg.get("sd", 0.08))
    sparse_probability = float(config.get("sparse_coverage_probability", 0.12))

    zip_values = service_zips["zip5"].tolist()
    zip_weights = service_zips["population_weight"].astype(float).tolist()
    clinician_rows: list[dict] = []
    zip_history_rows: list[dict] = []

    for discipline in ALL_DISCIPLINES:
        n_clinicians = int(roster_size.get(discipline, 0))
        for sequence in range(1, n_clinicians + 1):
            base_zip = weighted_choice(rng, zip_values, zip_weights)
            base_row = service_zips.loc[service_zips["zip5"] == base_zip].iloc[0]
            quality = float(np.clip(rng.normal(quality_mean, quality_sd), 0.35, 0.98))
            capacity = int(max(8, round(rng.normal(28 if discipline != "OT" else 18, 6))))
            coverage: list[str] = []
            for _, zip_row in service_zips.iterrows():
                miles = haversine_miles(base_row["lat"], base_row["lon"], zip_row["lat"], zip_row["lon"])
                radius = float(radius_cfg.get(discipline, {}).get(zip_row["area_type"], 20))
                if miles <= radius:
                    keep_probability = max(0.25, 1.0 - (miles / max(radius, 1.0)) * 0.65)
                    if rng.random() <= keep_probability:
                        coverage.append(str(zip_row["zip5"]))
            if base_zip not in coverage:
                coverage.append(base_zip)
            if len(coverage) > 1 and rng.random() < sparse_probability:
                coverage = [base_zip]

            metrics = {}
            for metric in METRIC_ORDER:
                baseline = DEFAULT_BASELINES[metric]
                direction = -1 if RAW_DIRECTION[metric] == "lower_is_better" else 1
                score = baseline + direction * (quality - 0.5) * 0.18 + rng.normal(0, 0.035)
                metrics[metric] = float(np.clip(score, 0.01, 0.99))

            clinician_id = discipline_sequence_id(discipline, sequence)
            blinded_name = f"{discipline}{sequence}"
            row = {
                "clinician_id": clinician_id,
                "blinded_name": blinded_name,
                "clinician_name": blinded_name,
                "discipline": discipline,
                "base_zip": base_zip,
                "base_lat": float(base_row["lat"]),
                "base_lon": float(base_row["lon"]),
                "capacity": capacity,
                "availability_weight": float(np.clip(rng.normal(1.0, 0.12), 0.5, 1.4)),
                "synthetic_quality_score": quality,
                "synthetic_clinician_data": True,
                "public_data_only": True,
            }
            row.update(metrics)
            clinician_rows.append(row)
            zip_history_rows.append(
                {
                    "clinician_id": clinician_id,
                    "blinded_name": blinded_name,
                    "zip_codes_treated": ",".join(sorted(set(coverage))),
                    "synthetic_zip_history": True,
                    "public_data_only": True,
                }
            )

    clinician_df = pd.DataFrame(clinician_rows)
    zip_history_df = pd.DataFrame(zip_history_rows)
    report = {
        "synthetic_data": True,
        "public_data_only": True,
        "synthetic_clinician_data": True,
        "synthetic_zip_history": True,
        "seed": seed,
        "roster_size_by_discipline": {disc: int((clinician_df["discipline"] == disc).sum()) for disc in ALL_DISCIPLINES},
        "zip_history_rows": int(len(zip_history_df)),
    }
    return clinician_df, zip_history_df, report


def clinician_rows_to_request(clinician_df: pd.DataFrame, zip_history_df: pd.DataFrame) -> list[dict]:
    zip_map = zip_history_df.set_index("clinician_id")["zip_codes_treated"].to_dict()
    clinicians: list[dict] = []
    for _, row in clinician_df.iterrows():
        metrics = {metric: float(row[metric]) for metric in METRIC_ORDER}
        clinicians.append(
            {
                "clinician_id": int(row["clinician_id"]),
                "clinician_name": str(row["clinician_name"]),
                "blinded_name": str(row["blinded_name"]),
                "discipline": str(row["discipline"]),
                "data_origin": "synthetic_public_only",
                "metric_scale": "proportion",
                "count_proxies": {"SOCs": float(row["capacity"]), "DCs": float(row["capacity"]), "Eligible_Surveys": max(float(row["capacity"]) / 4.0, 1.0)},
                "metrics": metrics,
                "zip_codes_treated": zip_map.get(int(row["clinician_id"]), ""),
                "synthetic_clinician_data": True,
                "public_data_only": True,
            }
        )
    return clinicians


def build_patient_profile_for_zip(rng: np.random.Generator, config: dict, service_zips: pd.DataFrame, office_row: pd.Series) -> dict:
    zip_row = service_zips.iloc[int(rng.choice(len(service_zips), p=service_zips["population_weight"] / service_zips["population_weight"].sum()))]
    age_group = sample_age_group(rng, config)
    age = age_from_group(rng, age_group)
    distance_miles = haversine_miles(office_row["lat"], office_row["lon"], zip_row["lat"], zip_row["lon"])
    distance = distance_category(distance_miles)
    health_status = sample_health_status(rng, age_group, float(zip_row.get("health_proxy", 0.5)))
    housing_apartment_prob = 0.45 if zip_row["area_type"] == "metropolitan" else 0.25
    if zip_row.get("house_price_zip") == "low":
        housing_apartment_prob += 0.12
    housing_type = "apartment" if rng.random() < min(housing_apartment_prob, 0.8) else "single_home"
    driving_condition = driving_condition_from_area(rng, str(zip_row["area_type"]), distance)
    return {
        "patient_zip": str(zip_row["zip5"]),
        "age": age,
        "age_group": age_group,
        "health_status": health_status,
        "house_price_zip": str(zip_row.get("house_price_zip", "average")),
        "area_type": str(zip_row["area_type"]),
        "housing_type": housing_type,
        "distance_to_clinic": distance,
        "driving_condition": driving_condition,
    }


def synthetic_outcomes(
    rng: np.random.Generator,
    risk_score: float,
    average_quality: float,
    zip_continuity: bool,
    config: dict,
) -> dict:
    outcome_cfg = config.get("synthetic_outcome_model", {})
    baselines = outcome_cfg.get("baseline_by_metric", DEFAULT_BASELINES)
    noise_sd = float(outcome_cfg.get("noise_sd", 0.035))
    risk_effect = float(outcome_cfg.get("risk_effect", 0.14))
    quality_effect = float(outcome_cfg.get("clinician_quality_effect", 0.16))
    continuity_effect = float(outcome_cfg.get("zip_continuity_effect", 0.025))
    out: dict[str, float] = {}
    for metric in METRIC_ORDER:
        baseline = float(baselines.get(metric, DEFAULT_BASELINES[metric]))
        continuity = continuity_effect if zip_continuity else 0.0
        quality_centered = average_quality - 0.5
        if RAW_DIRECTION[metric] == "lower_is_better":
            value = baseline + risk_effect * risk_score - quality_effect * quality_centered - continuity + rng.normal(0, noise_sd)
        else:
            value = baseline - risk_effect * risk_score + quality_effect * quality_centered + continuity + rng.normal(0, noise_sd)
        out[f"outcome_{metric}"] = float(np.clip(value, 0.0, 1.0))
    return out


def generate_public_semi_synthetic_dataset(config: dict, manifest: dict | None = None, config_dir: str | Path = "config") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    seed = int(config.get("seed", 20260608))
    rng = np.random.default_rng(seed)
    service_zips = service_zip_df_from_config(config)
    office_zip = str(config.get("market", {}).get("office_zip", service_zips.iloc[0]["zip5"])).zfill(5)
    office_row = service_zips.loc[service_zips["zip5"] == office_zip]
    if office_row.empty:
        office_row = service_zips.iloc[[0]]
    office_row = office_row.iloc[0]
    n_episodes = int(config.get("num_synthetic_episodes", 50))
    threshold = float(
        config.get("routing", {}).get(
            "risk_threshold_percentile", config.get("routing", {}).get("risk_threshold", 0.75)
        )
    )
    threshold_source = "public_only_semi_synthetic_capacity_pilot_not_outcome_validated"

    clinician_df, zip_history_df, clinician_report = generate_public_synthetic_clinicians(config)
    clinicians = clinician_rows_to_request(clinician_df, zip_history_df)
    quality_map = clinician_df.set_index("clinician_id")["synthetic_quality_score"].to_dict()

    patient_rows: list[dict] = []
    episode_rows: list[dict] = []
    for idx in range(1, n_episodes + 1):
        profile = build_patient_profile_for_zip(rng, config, service_zips, office_row)
        patient_id = f"syn_pat_{seed}_{idx:05d}"
        episode_id = f"syn_ep_{seed}_{idx:05d}"
        patient_obj = {
            "patient_id": patient_id,
            "age": profile["age"],
            "zip_code": profile["patient_zip"],
            "local_externalities": {
                "health_status": profile["health_status"],
                "age_group": profile["age_group"],
                "house_price_zip": profile["house_price_zip"],
                "area_type": profile["area_type"],
                "housing_type": profile["housing_type"],
                "distance_to_clinic": profile["distance_to_clinic"],
                "driving_condition": profile["driving_condition"],
            },
        }
        need_df = build_patient_need_vector(config_dir, patient_obj["local_externalities"])
        rho = float(np.clip(need_df["weighted_need_mean"].sum(), 0.0, 1.0))
        assignment_request = {
            "request_id": episode_id,
            "patient": patient_obj,
            "clinicians": clinicians,
            "options": {
                "top_k_groups": 1,
                "threshold_routing_enabled": True,
                "risk_threshold": threshold,
                "threshold_source": threshold_source,
            },
        }
        assignment = assign_clinicians(assignment_request, config_dir=config_dir)
        risk_percentile_u = float(assignment["patient_severity"]["risk_percentile_u"])
        recommended_ids = assignment["threshold_routing"]["recommended_clinician_ids"]
        routes = assignment["threshold_routing"]["route_by_discipline"]
        rn_id = recommended_ids["RN"][0]
        pt_id = recommended_ids["PT"][0]
        ot_id = recommended_ids["OT"][0]
        selected_qualities = [quality_map.get(value) for value in [rn_id, pt_id, ot_id] if value in quality_map]
        average_quality = float(np.mean(selected_qualities)) if selected_qualities else 0.65
        continuity = any(route == "zip_history" for route in routes.values())
        outcomes = synthetic_outcomes(rng, risk_percentile_u, average_quality, continuity, config)
        service_month = f"2026-{((idx - 1) % 12) + 1:02d}"

        patient_row = {
            "patient_id_random": patient_id,
            "zip5_or_zcta": profile["patient_zip"],
            "age": profile["age"],
            "age_group": profile["age_group"],
            "health_status": profile["health_status"],
            "house_price_zip": profile["house_price_zip"],
            "area_type": profile["area_type"],
            "housing_type": profile["housing_type"],
            "distance_to_clinic": profile["distance_to_clinic"],
            "driving_condition": profile["driving_condition"],
            "risk_score_rho": rho,
            "risk_score_rho_raw": rho,
            "risk_percentile_u": risk_percentile_u,
            "synthetic_data": True,
            "public_data_only": True,
            "synthetic_generation_seed": seed,
            "production_validated": False,
        }
        episode_row = {
            "episode_id_random": episode_id,
            **patient_row,
            "service_month": service_month,
            "patient_zip_for_routing": profile["patient_zip"],
            "assigned_RN_id": rn_id,
            "assigned_PT_id": pt_id,
            "assigned_OT_id": ot_id,
            "recommended_RN_id": rn_id,
            "recommended_PT_id": pt_id,
            "recommended_OT_id": ot_id,
            "route_RN": routes["RN"],
            "route_PT": routes["PT"],
            "route_OT": routes["OT"],
            "public_reference_manifest_id": (manifest or {}).get("manifest_id", "public_data_manifest_example"),
            "threshold_source": threshold_source,
            "production_validated": False,
            **outcomes,
        }
        patient_rows.append(patient_row)
        episode_rows.append(episode_row)

    patient_df = pd.DataFrame(patient_rows)
    episode_df = pd.DataFrame(episode_rows)
    calibration_df = episode_df.copy()
    report = {
        "created_at": config.get("creation_timestamp_utc", datetime.now(timezone.utc).isoformat()),
        "synthetic_data": True,
        "public_data_only": True,
        "production_validated": False,
        "threshold_source": threshold_source,
        "threshold_percentile": threshold,
        "threshold_semantics": "strict risk_percentile_u > threshold",
        "source_mode": config.get("mode", "offline_demo_fallback"),
        "configuration_hash": hashlib.sha256(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "manifest_hash": hashlib.sha256(
            json.dumps(manifest or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "data_source_warning": "offline_fallback_distributions" if config.get("mode") == "offline_demo_fallback" else None,
        "seed": seed,
        "num_synthetic_episodes": int(len(episode_df)),
        "age_group_distribution": patient_df["age_group"].value_counts().to_dict(),
        "zip_distribution": patient_df["zip5_or_zcta"].value_counts().to_dict(),
        "factor_distributions": {
            factor: patient_df[factor].value_counts().to_dict()
            for factor in ["health_status", "house_price_zip", "area_type", "housing_type", "distance_to_clinic", "driving_condition"]
        },
        "route_distribution": {
            discipline: episode_df[f"route_{discipline}"].value_counts().to_dict()
            for discipline in ALL_DISCIPLINES
        },
        "zip_fallback_rates": {
            discipline: float((episode_df[f"route_{discipline}"] == "zip_history_fallback_full_pool").mean())
            for discipline in ALL_DISCIPLINES
        },
        "synthetic_outcome_summaries": {
            metric: {
                "mean": float(episode_df[f"outcome_{metric}"].mean()),
                "min": float(episode_df[f"outcome_{metric}"].min()),
                "max": float(episode_df[f"outcome_{metric}"].max()),
            }
            for metric in METRIC_ORDER
        },
        "clinician_report": clinician_report,
        "warning": "Public-data-informed synthetic data is for development/demo/sensitivity analysis only, not historical validation.",
    }
    return patient_df, clinician_df, zip_history_df, calibration_df, report


def write_public_synthetic_outputs(
    patient_df: pd.DataFrame,
    clinician_df: pd.DataFrame,
    zip_history_df: pd.DataFrame,
    calibration_df: pd.DataFrame,
    report: dict,
    output_root: str | Path = ".",
) -> None:
    root = Path(output_root)
    processed = root / "data" / "processed"
    reports = root / "reports" / "dataset"
    processed.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)

    patient_df.to_csv(processed / "public_synthetic_patients.csv", index=False)
    clinician_df.to_csv(processed / "public_synthetic_clinicians.csv", index=False)
    zip_history_df.to_csv(processed / "public_synthetic_zip_history.csv", index=False)
    calibration_df.to_csv(processed / "threshold_calibration_ready_public_synthetic.csv", index=False)
    write_json(report, reports / "public_synthetic_dataset_report.json")
    provenance = {
        "created_at": report.get("created_at"),
        "synthetic_data": True,
        "public_data_only": True,
        "production_validated": False,
        "data_source_warning": report.get("data_source_warning"),
        "source_mode": report.get("source_mode"),
        "configuration_hash": report.get("configuration_hash"),
        "manifest_hash": report.get("manifest_hash"),
        "threshold_source": report.get("threshold_source"),
        "synthetic_zip_history": "synthetic_public_only",
        "warning": "No sponsor patient, clinician, assignment, ZIP-history, OASIS, HHCAHPS, scheduling, capacity, or outcome data were available.",
    }
    write_json(provenance, reports / "public_data_provenance.json")
    md = [
        "# Public Semi-Synthetic Dataset Report",
        "",
        "This dataset is public-data-informed and synthetic. It is for development, demo, and sensitivity analysis only.",
        "",
        f"- Episodes: {report['num_synthetic_episodes']}",
        f"- Seed: {report['seed']}",
        "- public_data_only: true",
        "- synthetic_data: true",
        "- production_validated: false",
        "",
        "Because no sponsor historical validation set is available, this output is not historical validation and does not prove payment improvement.",
    ]
    (reports / "public_synthetic_dataset_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
