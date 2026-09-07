"""Current Markdown/CSV/JSON reporting for the CY2025 Aptatio prototype.

The reports in this module are deliberately narrow about what the available
data can support.  Reno tables are model-generated scenario distributions;
they are not patient-level observations or predictions.  The ZIP attached to
each Reno scenario is used only to select configured geographic attributes and
is never passed through the clinician assignment ZIP-history router.

PDF rendering is intentionally a separate, subsequent step in
``scripts/generate_pdf_reports.py``.  Keeping the source reports and their
machine-readable inputs together makes it possible to scan every generated
claim before rendering.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from hhvbp_local_externality import (
    AGE_GROUP_ORDER,
    METRIC_ORDER,
    RAW_DIRECTION,
    build_patient_need_vector,
)
from hhvbp_risk_reference import empirical_risk_percentile, load_risk_reference


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_REFERENCE_PATH = PROJECT_ROOT / "data" / "reference" / "hhvbp_risk_reference.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports" / "current"
DEFAULT_EXPERIMENT_ROOT = PROJECT_ROOT / "reports" / "experiments"

REPORT_GENERATOR_VERSION = "1.2.0"
CONTROLLED_RENO_AGE_GROUP = "age_70_80"
PILOT_THRESHOLD_PERCENTILE = 0.75
THRESHOLD_PROVENANCE = "public_only_semi_synthetic_capacity_pilot_not_outcome_validated"
VALIDATION_BOUNDARY = (
    "Public-data-only, expert-configured, semi-synthetic, simulation-based, "
    "and not outcome-validated."
)

METRIC_DISPLAY_NAMES: dict[str, str] = {
    "PPH": "Potentially Preventable Hospitalization (PPH)",
    "DTC": "Discharge to Community–Post Acute Care (DTC-PAC)",
    "DFS": "Discharge Function Score (DFS/DC Function)",
    "Oral_Meds": "Improvement in Management of Oral Medications",
    "Dyspnea": "Improvement in Dyspnea",
    "Care_of_Patients": "Care of Patients",
    "Communications": "Communications Between Providers and Patients",
    "Care_Issues": "Specific Care Issues",
    "Agency_Rating": "Overall / Agency Rating",
    "Recommend": "Willingness to Recommend",
}

CY2025_METRIC_WEIGHTS: dict[str, float] = {
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

SEVEN_FACTORS = (
    "health_status",
    "age_group",
    "house_price_zip",
    "area_type",
    "housing_type",
    "distance_to_clinic",
    "driving_condition",
)

EXPERIMENT_IMPLEMENTATION_PATHS = (
    "scripts/run_batch_policy_experiments.py",
    "src/hhvbp_batch_experiments.py",
)
EXPERIMENT_SCENARIOS = tuple("ABCDEFGHI")

# These are checked-in offline demo scenario inputs, not downloaded Reno
# observations.  This mirrors the fallback market table used by the public
# semi-synthetic data builder while keeping report generation independent of
# assignment and outcome-simulation modules.
RENO_SERVICE_ZIP_SCENARIOS: tuple[dict[str, Any], ...] = (
    {"zip5": "89501", "lat": 39.5260, "lon": -119.8138, "area_type": "metropolitan", "house_price_zip": "average"},
    {"zip5": "89502", "lat": 39.5051, "lon": -119.7898, "area_type": "metropolitan", "house_price_zip": "low"},
    {"zip5": "89503", "lat": 39.5405, "lon": -119.8437, "area_type": "metropolitan", "house_price_zip": "average"},
    {"zip5": "89506", "lat": 39.6214, "lon": -119.8421, "area_type": "metropolitan", "house_price_zip": "average"},
    {"zip5": "89509", "lat": 39.4934, "lon": -119.8246, "area_type": "metropolitan", "house_price_zip": "high"},
    {"zip5": "89523", "lat": 39.5281, "lon": -119.9075, "area_type": "metropolitan", "house_price_zip": "high"},
    {"zip5": "89431", "lat": 39.5400, "lon": -119.7480, "area_type": "metropolitan", "house_price_zip": "low"},
    {"zip5": "89433", "lat": 39.6001, "lon": -119.7750, "area_type": "metropolitan", "house_price_zip": "average"},
    {"zip5": "89434", "lat": 39.5448, "lon": -119.7091, "area_type": "metropolitan", "house_price_zip": "average"},
    {"zip5": "89436", "lat": 39.6260, "lon": -119.6940, "area_type": "micropolitan", "house_price_zip": "average"},
)


@dataclass(frozen=True)
class UnsupportedClaimRule:
    rule_id: str
    explanation: str
    pattern: re.Pattern[str]


UNSUPPORTED_CLAIM_RULES: tuple[UnsupportedClaimRule, ...] = (
    UnsupportedClaimRule(
        "guaranteed_improvement",
        "Claims that the model guarantees payment, outcome, or patient-benefit improvement are unsupported.",
        re.compile(
            r"\b(?:guarantee(?:s|d)?|ensure(?:s|d)?)\b[^.\n]{0,90}"
            r"\b(?:payment|payments|outcome improvement|patient benefit)\b",
            re.IGNORECASE,
        ),
    ),
    UnsupportedClaimRule(
        "payment_maximization",
        "The public-only simulation cannot establish payment improvement or maximization.",
        re.compile(
            r"\b(?:maximi[sz](?:e|es|ed|ing)|increase(?:s|d|ing)?|improve(?:s|d|ment|ments|ing)?)\b"
            r"[^.\n]{0,55}\b(?:payment|payments)\b|"
            r"\bpayment[- ]maximi[sz](?:e|es|ed|ing)\b",
            re.IGNORECASE,
        ),
    ),
    UnsupportedClaimRule(
        "patient_outcome_benefit",
        "The available data cannot establish patient or clinical outcome benefit.",
        re.compile(
            r"\b(?:model|policy|algorithm|assignment|threshold|system)\b[^.\n]{0,65}"
            r"\b(?:improve(?:s|d)?|benefit(?:s|ed)?|cause(?:s|d)?|produce(?:s|d)?|deliver(?:s|ed)?)\b"
            r"[^.\n]{0,55}\b(?:patient\s+benefit|patient\s+outcomes?|clinical\s+outcomes?)\b|"
            r"\bproven\b[^.\n]{0,55}\b(?:patient\s+benefit|clinical\s+benefit|outcome\s+improvement)\b",
            re.IGNORECASE,
        ),
    ),
    UnsupportedClaimRule(
        "real_world_optimality",
        "A real-world, causal, clinical, or empirical optimality claim is unsupported.",
        re.compile(
            r"\b(?:real[- ]world|causally|clinically|empirically)\s+"
            r"(?:optimal|best)\b|\boptimal\s+(?:threshold|policy|assignment|model)\b|"
            r"\b(?:threshold|policy|assignment|algorithm|model)\s+is\s+optimal\b",
            re.IGNORECASE,
        ),
    ),
    UnsupportedClaimRule(
        "causal_clinician_effect",
        "Clinician causal effects are not identified by the available public-only data.",
        re.compile(
            r"\bcausal\s+clinician\s+effects?\b|"
            r"\bclinician(?:\s+quality)?\s+(?:causes?|causally\s+improves?)\b",
            re.IGNORECASE,
        ),
    ),
    UnsupportedClaimRule(
        "sponsor_data_validation",
        "No sponsor patient, clinician, assignment, outcome, or capacity data were available for validation.",
        re.compile(
            r"\bvalidated\s+(?:using|on|against|with)\s+(?:the\s+)?sponsor(?:'s)?\b",
            re.IGNORECASE,
        ),
    ),
    UnsupportedClaimRule(
        "empirical_reno_prediction",
        "Reno outputs are configured scenarios, not empirical patient-level predictions.",
        re.compile(
            r"\bempirical(?:ly)?\b[^.\n]{0,45}\bReno\b[^.\n]{0,45}\bprediction(?:s)?\b|"
            r"\bReno\b[^.\n]{0,45}\bempirical\s+patient[- ]level\s+prediction(?:s)?\b",
            re.IGNORECASE,
        ),
    ),
    UnsupportedClaimRule(
        "stale_six_factor_wording",
        "Current reports must describe the active seven-factor model.",
        re.compile(r"\bsix[- ]factor\b", re.IGNORECASE),
    ),
)


class UnsupportedReportClaimError(ValueError):
    """Raised when generated report text contains a prohibited claim."""


class ExperimentEvidenceValidationError(ValueError):
    """Raised when experiment evidence is incomplete, stale, or mislabelled."""


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _generation_timestamp(public_config: Mapping[str, Any], explicit: str | None = None) -> str:
    """Return a reproducible artifact timestamp when the dataset config supplies one."""
    if explicit:
        return explicit
    source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if source_date_epoch:
        timestamp = datetime.fromtimestamp(int(source_date_epoch), tz=timezone.utc)
        return timestamp.isoformat().replace("+00:00", "Z")
    configured = public_config.get("creation_timestamp_utc")
    if configured:
        return str(configured).replace("+00:00", "Z")
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * radius * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _distance_category(distance_miles: float) -> str:
    if distance_miles <= 8.0:
        return "near"
    if distance_miles <= 20.0:
        return "medium"
    return "far"


def _validate_reporting_configuration(
    global_config: Mapping[str, Any],
    metric_library: Mapping[str, Any],
    assignment_config: Mapping[str, Any],
) -> None:
    expected_lock = {
        "cms_model": "Expanded HHVBP",
        "performance_year": 2025,
        "payment_year": 2027,
        "measure_set_version": "CY2025",
        "cms_version_locked": True,
    }
    for key, expected in expected_lock.items():
        if global_config.get(key) != expected or metric_library.get(key) != expected:
            raise ValueError(f"Report generation requires the locked {key}={expected!r} configuration.")

    if tuple(global_config.get("factor_order", ())) != SEVEN_FACTORS:
        raise ValueError("Report generation requires exactly the canonical seven-factor order.")

    configured_weights = {
        metric: float(config["composite_weight"])
        for metric, config in metric_library.get("metrics", {}).items()
    }
    if configured_weights != CY2025_METRIC_WEIGHTS:
        raise ValueError("The configured measure set does not equal the locked CY2025 weights.")
    if not math.isclose(sum(configured_weights.values()), 1.0, abs_tol=1e-12):
        raise ValueError("CY2025 metric weights must sum to 1.00.")

    routing = assignment_config.get("threshold_routing", {})
    if float(routing.get("risk_threshold", -1.0)) != PILOT_THRESHOLD_PERCENTILE:
        raise ValueError("Current report generation requires the unchanged 0.75 pilot percentile threshold.")
    if routing.get("threshold_source") != THRESHOLD_PROVENANCE:
        raise ValueError("Unexpected threshold provenance label in assignment configuration.")


def _load_reporting_context(
    config_dir: str | Path = DEFAULT_CONFIG_DIR,
    reference_path: str | Path = DEFAULT_REFERENCE_PATH,
) -> dict[str, Any]:
    config_dir = Path(config_dir)
    global_config = _load_json(config_dir / "hhvbp_global_config.json")
    metric_library = _load_json(config_dir / "metric_parameter_library.json")
    assignment_config = _load_json(config_dir / "assignment_parameters.json")
    public_config = _load_json(config_dir / "public_semi_synthetic_dataset_config.json")
    _validate_reporting_configuration(global_config, metric_library, assignment_config)
    reference = load_risk_reference(Path(reference_path))
    if len(reference["sorted_risk_scores"]) < 10_000:
        raise ValueError("Current reports require a risk reference with at least 10,000 scores.")
    return {
        "config_dir": config_dir,
        "global_config": global_config,
        "metric_library": metric_library,
        "assignment_config": assignment_config,
        "public_config": public_config,
        "reference": reference,
    }


def _experiment_tier_specs() -> dict[str, dict[str, Any]]:
    """Return the exact experiment roles allowed to support current report claims."""

    return {
        "threshold_fine_grid": {
            "files": ("threshold_recommendations.json", "threshold_summary.csv"),
            "thresholds": tuple(index / 100.0 for index in range(101)),
            "policies": ("P4",),
            "scenarios": EXPERIMENT_SCENARIOS,
            "seed_count": 2,
            "design_count": 1,
            "n_patients": 100,
            "optimization": True,
            "held_out": True,
            "role": "held_out_fine_threshold_selection",
        },
        "major_scenarios_5000": {
            "files": ("policy_threshold_results.csv",),
            "thresholds": (PILOT_THRESHOLD_PERCENTILE,),
            "policies": ("P4",),
            "scenarios": EXPERIMENT_SCENARIOS,
            "seed_count": 1,
            "design_count": 1,
            "n_patients": 5_000,
            "optimization": False,
            "held_out": False,
            "role": "fixed_threshold_major_scenarios",
        },
        "policy_bakeoff": {
            "files": ("policy_threshold_results.csv",),
            "thresholds": (PILOT_THRESHOLD_PERCENTILE,),
            "policies": tuple(f"P{index}" for index in range(8)),
            "scenarios": EXPERIMENT_SCENARIOS,
            "seed_count": 2,
            "design_count": 1,
            "n_patients": 500,
            "optimization": False,
            "held_out": False,
            "role": "fixed_threshold_policy_bakeoff",
        },
        "operational_stress_grid": {
            "files": ("policy_threshold_results.csv",),
            "thresholds": (0.25, PILOT_THRESHOLD_PERCENTILE),
            "policies": ("P4", "P5", "P6"),
            "scenarios": ("F", "G", "H"),
            "seed_count": 1,
            "design_count": 8,
            "n_patients": 200,
            "optimization": False,
            "held_out": False,
            "role": "multi_design_threshold_sensitivity_not_selection",
        },
        "structural_audit": {
            "files": ("structural_diagnostics.json",),
            "thresholds": (PILOT_THRESHOLD_PERCENTILE,),
            "policies": ("P4",),
            "scenarios": ("A",),
            "seed_count": 1,
            "design_count": 1,
            "n_patients": 10,
            "optimization": False,
            "held_out": False,
            "role": "fixed_threshold_structural_audit",
        },
    }


def _numeric_tuple(values: Any) -> tuple[float, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    try:
        return tuple(round(float(value), 12) for value in values)
    except (TypeError, ValueError):
        return ()


def _validate_experiment_metadata(
    tier: str,
    metadata: Mapping[str, Any],
    spec: Mapping[str, Any],
    context: Mapping[str, Any],
) -> None:
    """Fail closed unless metadata proves the artifact has its claimed role."""

    def require(condition: bool, detail: str) -> None:
        if not condition:
            raise ExperimentEvidenceValidationError(f"Experiment tier {tier!r}: {detail}")

    model_lock = context["global_config"]
    required_values = {
        "artifact_family": "batch_capacity_policy_threshold_experiments",
        "cms_model": model_lock["cms_model"],
        "performance_year": model_lock["performance_year"],
        "payment_year": model_lock["payment_year"],
        "measure_set_version": model_lock["measure_set_version"],
        "cms_version_locked": model_lock["cms_version_locked"],
        "public_data_only": True,
        "synthetic_data": True,
        "outcome_validated": False,
        "production_validated": False,
        "production_threshold_changed": False,
        "causal_effects_identified": False,
        "risk_percentile_semantics": "persisted_reference_ecdf",
        "provenance": THRESHOLD_PROVENANCE,
        "production_demo_threshold": PILOT_THRESHOLD_PERCENTILE,
    }
    for key, expected in required_values.items():
        require(metadata.get(key) == expected, f"expected {key}={expected!r}, found {metadata.get(key)!r}.")

    reference_metadata = context["reference"].get("metadata", {})
    expected_reference = {
        "risk_reference_id": reference_metadata.get("reference_id"),
        "risk_reference_config_hash": reference_metadata.get("config_hash"),
        "risk_reference_sample_size": int(reference_metadata.get("sample_size", 0)),
    }
    for key, expected in expected_reference.items():
        require(metadata.get(key) == expected, f"does not match the current persisted {key}.")

    thresholds = _numeric_tuple(metadata.get("thresholds"))
    expected_thresholds = _numeric_tuple(spec["thresholds"])
    policies = tuple(metadata.get("policies", ()))
    scenarios = tuple(metadata.get("scenarios", ()))
    seeds = tuple(metadata.get("seeds", ()))
    require(thresholds == expected_thresholds, f"thresholds do not match role {spec['role']!r}.")
    require(policies == tuple(spec["policies"]), f"policies do not match role {spec['role']!r}.")
    require(scenarios == tuple(spec["scenarios"]), f"scenarios do not match role {spec['role']!r}.")
    require(len(seeds) == int(spec["seed_count"]), f"seed count does not match role {spec['role']!r}.")
    require(
        metadata.get("threshold_optimization_evaluated") is bool(spec["optimization"]),
        f"threshold optimization flag does not match role {spec['role']!r}.",
    )
    require(
        metadata.get("held_out_threshold_evaluation_performed") is bool(spec["held_out"]),
        f"held-out evaluation flag does not match role {spec['role']!r}.",
    )

    designs = metadata.get("design_configs")
    design_hashes = metadata.get("design_config_hashes")
    require(isinstance(designs, list), "design_configs must be a list.")
    require(len(designs) == int(spec["design_count"]), f"design count does not match role {spec['role']!r}.")
    require(isinstance(design_hashes, list) and len(design_hashes) == len(designs), "design hashes are incomplete.")
    require(len(set(design_hashes)) == len(design_hashes), "design hashes are not unique.")
    require(all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in design_hashes), "design hashes are malformed.")
    for design in designs:
        require(isinstance(design, Mapping), "each design config must be an object.")
        require(int(design.get("n_patients", -1)) == int(spec["n_patients"]), "design patient count is stale or mislabelled.")
        require(_numeric_tuple(design.get("thresholds")) == expected_thresholds, "design thresholds disagree with tier metadata.")
        require(tuple(design.get("policies", ())) == policies, "design policies disagree with tier metadata.")
        require(tuple(design.get("seeds", ())) == seeds, "design seeds disagree with tier metadata.")

    expected_rows = len(designs) * len(scenarios) * len(seeds) * len(thresholds) * len(policies)
    require(metadata.get("result_rows") == expected_rows, "result row count is inconsistent with the declared design.")
    if len(designs) == 1:
        require(metadata.get("config_hash") == design_hashes[0], "single-design config hash does not match its design hash.")

    if tier == "operational_stress_grid":
        stress_cells = {
            (
                round(float(design.get("capacity_ratio", -1)), 12),
                round(float(design.get("zip_sparsity", -1)), 12),
                tuple(sorted(dict(design.get("roster_sizes", {})).items())),
            )
            for design in designs
        }
        require(len(stress_cells) == 8, "stress designs do not form eight unique operating cells.")
        require({cell[0] for cell in stress_cells} == {0.7, 1.2}, "stress capacity levels are incomplete.")
        require({cell[1] for cell in stress_cells} == {0.2, 0.8}, "stress ZIP-sparsity levels are incomplete.")
        require(len({cell[2] for cell in stress_cells}) == 2, "stress roster levels are incomplete.")

    recorded_hashes = metadata.get("implementation_file_hashes_sha256")
    require(isinstance(recorded_hashes, Mapping), "implementation hashes are missing.")
    for relative_path in EXPERIMENT_IMPLEMENTATION_PATHS:
        implementation_path = PROJECT_ROOT / relative_path
        require(implementation_path.is_file(), f"current implementation file {relative_path!r} is unavailable.")
        require(
            recorded_hashes.get(relative_path) == _sha256(implementation_path),
            f"implementation hash for {relative_path!r} is stale.",
        )


def _load_experiment_evidence(
    experiment_root: str | Path = DEFAULT_EXPERIMENT_ROOT,
    reporting_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load decision evidence only after all present tiers pass provenance checks."""

    root = Path(experiment_root)
    context = dict(reporting_context or _load_reporting_context())
    specs = _experiment_tier_specs()
    tier_paths: dict[str, dict[str, Path]] = {}
    tier_metadata: dict[str, dict[str, Any]] = {}
    source_paths: list[Path] = []

    for tier, spec in specs.items():
        tier_root = root / tier
        paths = {name: tier_root / name for name in spec["files"]}
        metadata_path = tier_root / "experiment_metadata.json"
        present = [path for path in (*paths.values(), metadata_path) if path.exists()]
        if not present:
            continue
        missing = [path.name for path in (*paths.values(), metadata_path) if not path.is_file()]
        if missing:
            raise ExperimentEvidenceValidationError(
                f"Experiment tier {tier!r} is incomplete; missing {', '.join(sorted(missing))}."
            )
        metadata = _load_json(metadata_path)
        _validate_experiment_metadata(tier, metadata, spec, context)
        tier_paths[tier] = paths
        tier_metadata[tier] = metadata
        source_paths.extend([metadata_path, *paths.values()])

    def csv_rows(path: Path) -> list[dict[str, str]]:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def validate_policy_rows(tier: str, rows: Sequence[Mapping[str, str]]) -> None:
        metadata = tier_metadata[tier]
        if len(rows) != int(metadata["result_rows"]):
            raise ExperimentEvidenceValidationError(f"Experiment tier {tier!r}: CSV row count disagrees with metadata.")
        expected = {
            "cms_model": str(metadata["cms_model"]),
            "performance_year": str(metadata["performance_year"]),
            "payment_year": str(metadata["payment_year"]),
            "measure_set_version": str(metadata["measure_set_version"]),
            "cms_version_locked": "True",
            "threshold_source": THRESHOLD_PROVENANCE,
            "risk_reference_id": str(metadata["risk_reference_id"]),
            "risk_reference_config_hash": str(metadata["risk_reference_config_hash"]),
            "risk_reference_sample_size": str(metadata["risk_reference_sample_size"]),
            "risk_percentile_semantics": "persisted_reference_ecdf",
            "public_data_only": "True",
            "synthetic_data": "True",
            "production_validated": "False",
        }
        allowed_thresholds = set(_numeric_tuple(metadata["thresholds"]))
        allowed_policies = set(metadata["policies"])
        allowed_scenarios = set(metadata["scenarios"])
        allowed_design_hashes = set(metadata["design_config_hashes"])
        for row_number, row in enumerate(rows, start=2):
            for key, expected_value in expected.items():
                if row.get(key) != expected_value:
                    raise ExperimentEvidenceValidationError(
                        f"Experiment tier {tier!r}: CSV row {row_number} has invalid {key}."
                    )
            try:
                threshold = round(float(row["threshold_percentile"]), 12)
            except (KeyError, TypeError, ValueError) as exc:
                raise ExperimentEvidenceValidationError(
                    f"Experiment tier {tier!r}: CSV row {row_number} has an invalid threshold."
                ) from exc
            if threshold not in allowed_thresholds or row.get("policy") not in allowed_policies or row.get("scenario") not in allowed_scenarios:
                raise ExperimentEvidenceValidationError(
                    f"Experiment tier {tier!r}: CSV row {row_number} falls outside its declared design."
                )
            if row.get("config_hash") not in allowed_design_hashes:
                raise ExperimentEvidenceValidationError(
                    f"Experiment tier {tier!r}: CSV row {row_number} has an undeclared design hash."
                )

    def number(row: Mapping[str, str], key: str) -> float:
        try:
            return float(row[key])
        except (KeyError, TypeError, ValueError):
            return math.nan

    def policy_means(rows: Sequence[Mapping[str, str]], policy: str) -> dict[str, float] | None:
        selected = [row for row in rows if row.get("policy") == policy]
        if not selected:
            return None
        fields = (
            "tps_proxy", "simulated_outcome_utility", "regret_vs_scenario_oracle",
            "zip_fallback_rate", "zip_continuity_rate", "workload_gini",
            "unassigned_rate", "travel_proxy",
        )
        output: dict[str, float] = {}
        for field in fields:
            values = [number(row, field) for row in selected]
            finite = [value for value in values if math.isfinite(value)]
            output[field] = sum(finite) / len(finite) if finite else math.nan
        output["rows"] = float(len(selected))
        return output

    fine: dict[str, Any] | None = None
    if "threshold_fine_grid" in tier_paths:
        fine = _load_json(tier_paths["threshold_fine_grid"]["threshold_recommendations.json"])
        fine_spec = specs["threshold_fine_grid"]
        if (
            _numeric_tuple(fine.get("evaluated_thresholds")) != _numeric_tuple(fine_spec["thresholds"])
            or fine.get("policy") != "P4"
            or fine.get("threshold_optimization_evaluated") is not True
            or fine.get("held_out_evaluation_performed") is not True
            or fine.get("production_demo_threshold") != PILOT_THRESHOLD_PERCENTILE
            or fine.get("production_threshold_changed") is not False
            or fine.get("threshold_source") != THRESHOLD_PROVENANCE
        ):
            raise ExperimentEvidenceValidationError(
                "Experiment tier 'threshold_fine_grid': recommendation payload does not match held-out fine-grid metadata."
            )
        fine_summary = csv_rows(tier_paths["threshold_fine_grid"]["threshold_summary.csv"])
        expected_summary_rows = len(fine_spec["scenarios"]) * len(fine_spec["thresholds"])
        if len(fine_summary) != expected_summary_rows or any(
            row.get("threshold_evaluation_role") != "candidate_threshold_optimization" for row in fine_summary
        ):
            raise ExperimentEvidenceValidationError(
                "Experiment tier 'threshold_fine_grid': threshold summary is incomplete or mislabelled."
            )

    policy_rows: dict[str, list[dict[str, str]]] = {}
    for tier in ("major_scenarios_5000", "policy_bakeoff", "operational_stress_grid"):
        if tier in tier_paths:
            rows = csv_rows(tier_paths[tier]["policy_threshold_results.csv"])
            validate_policy_rows(tier, rows)
            policy_rows[tier] = rows

    diagnostics: dict[str, Any] | None = None
    if "structural_audit" in tier_paths:
        diagnostics = _load_json(tier_paths["structural_audit"]["structural_diagnostics.json"])
        reference_metadata = context["reference"].get("metadata", {})
        if (
            diagnostics.get("profile_count") != reference_metadata.get("structural_grid_size")
            or diagnostics.get("provenance") != THRESHOLD_PROVENANCE
        ):
            raise ExperimentEvidenceValidationError(
                "Experiment tier 'structural_audit': diagnostics do not match the current structural reference."
            )

    major_rows = policy_rows.get("major_scenarios_5000", [])
    bakeoff_rows = policy_rows.get("policy_bakeoff", [])
    stress_rows = policy_rows.get("operational_stress_grid", [])
    return {
        "available": bool(tier_paths),
        "validation_status": "passed" if tier_paths else "not_available",
        "validated_tiers": {tier: specs[tier]["role"] for tier in tier_paths},
        "fine_threshold": fine,
        "major_p4": policy_means(major_rows, "P4"),
        "bakeoff_p4": policy_means(bakeoff_rows, "P4"),
        "bakeoff_p5": policy_means(bakeoff_rows, "P5"),
        "bakeoff_p6": policy_means(bakeoff_rows, "P6"),
        "bakeoff_p7": policy_means(bakeoff_rows, "P7"),
        "stress_p4": policy_means(stress_rows, "P4"),
        "stress_p6": policy_means(stress_rows, "P6"),
        "structural_diagnostics": diagnostics,
        "source_paths": source_paths,
    }


def build_reno_geographic_profiles() -> list[dict[str, Any]]:
    """Build controlled Reno geography scenarios without invoking ZIP routing."""
    office = next(row for row in RENO_SERVICE_ZIP_SCENARIOS if row["zip5"] == "89501")
    scenarios: list[dict[str, Any]] = []
    for geography in RENO_SERVICE_ZIP_SCENARIOS:
        distance_miles = _haversine_miles(
            float(office["lat"]),
            float(office["lon"]),
            float(geography["lat"]),
            float(geography["lon"]),
        )
        profile = {
            "health_status": "moderate",
            "age_group": CONTROLLED_RENO_AGE_GROUP,
            "house_price_zip": geography["house_price_zip"],
            "area_type": geography["area_type"],
            "housing_type": "single_home",
            "distance_to_clinic": _distance_category(distance_miles),
            "driving_condition": "good_summer",
        }
        scenarios.append(
            {
                "scenario_id": f"reno_geography_{geography['zip5']}",
                "profiling_zip": str(geography["zip5"]),
                "patient_zip_for_assignment_routing": None,
                "assignment_zip_routing_applied": False,
                "scenario_source_mode": "offline_demo_fallback_configured_market_profile",
                "distance_from_configured_office_miles": round(distance_miles, 3),
                "profile": profile,
            }
        )
    return scenarios


def build_reno_age_sensitivity_profiles() -> list[dict[str, Any]]:
    """Vary the five age groups while holding all six other factors fixed."""
    fixed_profile = {
        "health_status": "moderate",
        "house_price_zip": "average",
        "area_type": "metropolitan",
        "housing_type": "single_home",
        "distance_to_clinic": "near",
        "driving_condition": "good_summer",
    }
    scenarios = []
    for age_group in AGE_GROUP_ORDER:
        profile = dict(fixed_profile)
        profile["age_group"] = age_group
        scenarios.append(
            {
                "scenario_id": f"reno_age_sensitivity_{age_group}",
                "profiling_zip": "89501",
                "patient_zip_for_assignment_routing": None,
                "assignment_zip_routing_applied": False,
                "scenario_source_mode": "expert_configured_age_sensitivity",
                "distance_from_configured_office_miles": 0.0,
                "profile": profile,
            }
        )
    return scenarios


def describe_distance_to_anchors(
    metric_key: str,
    mean: float,
    best: float,
    worst: float,
) -> dict[str, Any]:
    """Return direction-correct distances and plain-language interpretation."""
    direction = RAW_DIRECTION[metric_key]
    if direction == "higher_is_better":
        below_best = max(float(best) - float(mean), 0.0)
        above_worst = max(float(mean) - float(worst), 0.0)
        language = (
            f"{mean:.2f}% is {below_best:.2f} points below best and "
            f"{above_worst:.2f} points above worst."
        )
        return {
            "points_from_best": below_best,
            "points_from_worst": above_worst,
            "distance_from_best_label": "points_below_best",
            "distance_from_worst_label": "points_above_worst",
            "relative_to_anchor_language": language,
        }
    if direction == "lower_is_better":
        above_best = max(float(mean) - float(best), 0.0)
        below_worst = max(float(worst) - float(mean), 0.0)
        language = (
            f"{mean:.2f}% is {above_best:.2f} points above best and "
            f"{below_worst:.2f} points below worst."
        )
        return {
            "points_from_best": above_best,
            "points_from_worst": below_worst,
            "distance_from_best_label": "points_above_best",
            "distance_from_worst_label": "points_below_worst",
            "relative_to_anchor_language": language,
        }
    raise ValueError(f"Unsupported metric direction for {metric_key}: {direction!r}")


def _evaluate_scenarios(
    scenarios: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    analysis_type: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reference = context["reference"]
    config_dir = Path(context["config_dir"])
    for scenario in scenarios:
        profile = dict(scenario["profile"])
        need_table = build_patient_need_vector(config_dir, profile)
        rho_raw = float(need_table["weighted_need_mean"].sum())
        risk_percentile = empirical_risk_percentile(rho_raw, reference["sorted_risk_scores"])
        threshold_class = (
            "above_pilot_threshold"
            if risk_percentile > PILOT_THRESHOLD_PERCENTILE
            else "at_or_below_pilot_threshold"
        )
        for metric_key in METRIC_ORDER:
            metric_row = need_table.loc[need_table["metric_key"] == metric_key].iloc[0]
            mean = float(metric_row["patient_display_mean"])
            best = float(metric_row["best_display_mean"])
            worst = float(metric_row["worst_display_mean"])
            anchor_description = describe_distance_to_anchors(metric_key, mean, best, worst)
            row: dict[str, Any] = {
                "analysis_type": analysis_type,
                "scenario_id": str(scenario["scenario_id"]),
                "profiling_zip": str(scenario["profiling_zip"]),
                "patient_zip_for_assignment_routing": scenario["patient_zip_for_assignment_routing"],
                "assignment_zip_routing_applied": bool(scenario["assignment_zip_routing_applied"]),
                "scenario_source_mode": str(scenario["scenario_source_mode"]),
                "distance_from_configured_office_miles": float(
                    scenario["distance_from_configured_office_miles"]
                ),
                **{factor: profile[factor] for factor in SEVEN_FACTORS},
                "metric_key": metric_key,
                "metric_display_name": METRIC_DISPLAY_NAMES[metric_key],
                "metric_direction": RAW_DIRECTION[metric_key],
                "metric_weight": float(metric_row["metric_weight"]),
                "display_mean": mean,
                "display_sd": float(metric_row["patient_display_sd"]),
                "display_q10": float(metric_row["patient_display_q10"]),
                "display_q50": float(metric_row["patient_display_q50"]),
                "display_q90": float(metric_row["patient_display_q90"]),
                "best_display_mean": best,
                "worst_display_mean": worst,
                "metric_need_mean": float(metric_row["need_mean"]),
                "risk_score_rho_raw": rho_raw,
                "risk_percentile_u": risk_percentile,
                "threshold_percentile": PILOT_THRESHOLD_PERCENTILE,
                "risk_threshold_classification": threshold_class,
                "public_data_only": True,
                "expert_configured": True,
                "semi_synthetic": True,
                "simulation_based": True,
                "outcome_validated": False,
                **anchor_description,
            }
            rows.append(row)
    return rows


def build_reno_geographic_rows(
    config_dir: str | Path = DEFAULT_CONFIG_DIR,
    reference_path: str | Path = DEFAULT_REFERENCE_PATH,
) -> list[dict[str, Any]]:
    context = _load_reporting_context(config_dir, reference_path)
    return _evaluate_scenarios(build_reno_geographic_profiles(), context, "reno_geographic_sensitivity")


def build_reno_age_sensitivity_rows(
    config_dir: str | Path = DEFAULT_CONFIG_DIR,
    reference_path: str | Path = DEFAULT_REFERENCE_PATH,
) -> list[dict[str, Any]]:
    context = _load_reporting_context(config_dir, reference_path)
    return _evaluate_scenarios(build_reno_age_sensitivity_profiles(), context, "reno_age_sensitivity")


def _model_lock_text(global_config: Mapping[str, Any]) -> str:
    return (
        f"Expanded HHVBP, CY{global_config['performance_year']} performance / "
        f"CY{global_config['payment_year']} payment, measure set "
        f"{global_config['measure_set_version']} (version locked)."
    )


def _common_report_preamble(title: str, context: Mapping[str, Any]) -> list[str]:
    return [
        f"# {title}",
        "",
        f"**Model lock:** {_model_lock_text(context['global_config'])}",
        "",
        f"**Validation boundary:** {VALIDATION_BOUNDARY}",
        "",
        (
            "These results describe configured model scenarios. They do not establish real-world "
            "clinical effects, payment effects, or a uniquely best assignment policy."
        ),
        "",
    ]


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{float(value):.{digits}f}"
    return str(value)


def _markdown_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    output = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        output.append("| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |")
    return output


def render_reno_pph_report(rows: Sequence[Mapping[str, Any]], context: Mapping[str, Any]) -> str:
    pph_rows = [row for row in rows if row["metric_key"] == "PPH"]
    lines = _common_report_preamble("Reno PPH Scenario and Sensitivity Report", context)
    lines.extend(
        [
            "## Scope and controls",
            "",
            (
                "Every geographic comparison fixes `age_group=age_70_80`, health status at "
                "`moderate`, housing at `single_home`, and driving at `good_summer`. Configured "
                "house-price category, area type, and distance category may vary by ZIP scenario."
            ),
            "",
            (
                "The ZIP below is a profiling label used to select configured scenario attributes. "
                "It is not a patient assignment ZIP, no clinician ZIP-history pool is built here, "
                "and `assignment_zip_routing_applied=false` for every row. Assignment-time ZIP "
                "routing is a separate operational rule described in the current-model report."
            ),
            "",
            (
                "The displayed PPH values are analytic model distributions under configured "
                "assumptions, not empirical patient-level Reno estimates. Because PPH is "
                "lower-is-better, distance wording is “points above best” and “points below worst.”"
            ),
            "",
            "## Controlled geographic scenarios",
            "",
        ]
    )
    table_rows = []
    for row in pph_rows:
        table_rows.append(
            (
                row["profiling_zip"],
                row["area_type"],
                row["house_price_zip"],
                row["distance_to_clinic"],
                _fmt(row["display_mean"], 2),
                _fmt(row["display_q10"], 2),
                _fmt(row["display_q90"], 2),
                row["relative_to_anchor_language"],
                _fmt(row["risk_score_rho_raw"], 3),
                _fmt(row["risk_percentile_u"], 3),
            )
        )
    lines.extend(
        _markdown_table(
            [
                "Profiling ZIP",
                "Area",
                "House-price group",
                "Distance",
                "Mean (%)",
                "Q10",
                "Q90",
                "Distance to anchors",
                "rho raw",
                "u percentile",
            ],
            table_rows,
        )
    )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            (
                "`risk_score_rho_raw` is a weighted modeled-need index. `risk_percentile_u` is its "
                "relative position against the checked-in 10,000-profile public-only semi-synthetic "
                "reference. Neither is a calibrated clinical event probability. The strict pilot "
                "classification is above 0.75; a value equal to 0.75 remains at or below threshold."
            ),
            "",
            (
                "Scenario inputs come from the checked-in offline demo market table. Blank public "
                "source fields are not interpreted as downloaded observations."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def render_reno_other_metrics_report(rows: Sequence[Mapping[str, Any]], context: Mapping[str, Any]) -> str:
    lines = _common_report_preamble("Reno All Other CY2025 Metrics Scenario Report", context)
    lines.extend(
        [
            "## Scope and controls",
            "",
            (
                "This report uses the same controlled geography design as the PPH report: "
                "`age_group=age_70_80` and the other stated controls remain fixed. The ZIP is for "
                "distribution profiling only; assignment ZIP-history routing is not run."
            ),
            "",
            (
                "All metrics in this report are higher-is-better. Each mean is therefore described "
                "as points below the configured best anchor and points above the configured worst "
                "anchor. Values are model-generated distributions, not empirical patient-level Reno estimates."
            ),
            "",
        ]
    )
    for metric_key in METRIC_ORDER:
        if metric_key == "PPH":
            continue
        metric_rows = [row for row in rows if row["metric_key"] == metric_key]
        lines.extend([f"## {METRIC_DISPLAY_NAMES[metric_key]}", ""])
        lines.extend(
            _markdown_table(
                ["Profiling ZIP", "Mean (%)", "Q10", "Q90", "Distance to anchors", "rho raw", "u percentile"],
                (
                    (
                        row["profiling_zip"],
                        _fmt(row["display_mean"], 2),
                        _fmt(row["display_q10"], 2),
                        _fmt(row["display_q90"], 2),
                        row["relative_to_anchor_language"],
                        _fmt(row["risk_score_rho_raw"], 3),
                        _fmt(row["risk_percentile_u"], 3),
                    )
                    for row in metric_rows
                ),
            )
        )
        lines.append("")
    lines.extend(
        [
            "## Limitations",
            "",
            (
                "The distributions reflect expert elicitation, configured factor favorability, and "
                "analytic Beta moment matching. Agency practice, caregiver support, OASIS detail, "
                "scheduling, clinician capacity, and patient-level outcomes were unavailable."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def render_reno_age_sensitivity_report(rows: Sequence[Mapping[str, Any]], context: Mapping[str, Any]) -> str:
    lines = _common_report_preamble("Reno Five-Age Sensitivity Report", context)
    lines.extend(
        [
            "## Design",
            "",
            (
                "This is the dedicated age sensitivity, so age is intentionally varied across all "
                "five configured groups. All factors other than age are fixed at `health_status=moderate`, "
                "`house_price_zip=average`, `area_type=metropolitan`, `housing_type=single_home`, "
                "`distance_to_clinic=near`, and `driving_condition=good_summer`."
            ),
            "",
            (
                "The Reno label supplies context only. No patient assignment ZIP is set and no "
                "clinician ZIP-history routing is applied."
            ),
            "",
            "## Aggregate need and reference percentile",
            "",
        ]
    )
    first_metric_rows = [row for row in rows if row["metric_key"] == "PPH"]
    lines.extend(
        _markdown_table(
            ["Age group", "rho raw", "u percentile", "Strict 0.75 classification"],
            (
                (
                    row["age_group"],
                    _fmt(row["risk_score_rho_raw"], 3),
                    _fmt(row["risk_percentile_u"], 3),
                    row["risk_threshold_classification"],
                )
                for row in first_metric_rows
            ),
        )
    )
    lines.extend(["", "## Metric-level sensitivity", ""])
    lines.extend(
        _markdown_table(
            ["Metric", "Age group", "Mean (%)", "Need mean", "Distance to anchors"],
            (
                (
                    row["metric_key"],
                    row["age_group"],
                    _fmt(row["display_mean"], 2),
                    _fmt(row["metric_need_mean"], 3),
                    row["relative_to_anchor_language"],
                )
                for row in rows
            ),
        )
    )
    lines.extend(
        [
            "",
            (
                "Reliability affects distribution uncertainty in the configured analytic model. "
                "The current assignment uses mean need, so uncertainty alone does not change routing."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def render_nontechnical_report(context: Mapping[str, Any]) -> str:
    lines = _common_report_preamble("Aptatio Assignment Model: Nontechnical Explanation", context)
    lines.extend(
        [
            "## What the model does",
            "",
            (
                "The prototype recommends RN, PT, and OT clinicians for a home-health patient. It "
                "uses seven patient-side factors: health status, age group, ZIP house-price group, "
                "area type, housing type, distance to clinic, and driving condition. The active "
                "quality weights are frozen to CY2025 performance for CY2027 payment."
            ),
            "",
            "## Two numbers that should not be confused",
            "",
            (
                "The raw score, `risk_score_rho_raw`, is the weighted sum of the model's ten metric "
                "need scores. It is an index from the configured model, not a probability and not a "
                "historical rate."
            ),
            "",
            (
                "The reference percentile, `risk_percentile_u`, says where that raw index falls "
                "relative to a checked-in 10,000-profile public-only semi-synthetic reference. A "
                "percentile is a relative position; it does not turn the raw index into a clinical probability."
            ),
            "",
            "## How clinician ranking works",
            "",
            (
                "RN clinicians are compared only with RN clinicians, and PT clinicians only with PT "
                "clinicians. Metric percentiles are calculated on each discipline's full request pool "
                "before any ZIP filter. This prevents unrelated discipline rosters or a small ZIP pool "
                "from redefining capability ranks. Ties use stable clinician identifiers and names."
            ),
            "",
            (
                "These ranks are roster-relative. Adding or removing a clinician in the same "
                "discipline can change percentiles unless a stable external reference distribution is supplied."
            ),
            "",
            "## The strict pilot routing rule",
            "",
            (
                "A patient is in the high-risk route only when `risk_percentile_u > 0.75`. At exactly "
                "0.75, the patient remains in the lower-risk route. High-risk cases use the full "
                "discipline pool. Lower-risk cases first use clinicians with history in the patient's assignment ZIP."
            ),
            "",
            (
                "If that ZIP-history pool is too small, the policy falls back to the full pool. The "
                "minimums are RN=3, PT=3, and OT=1. ZIP history records past service coverage; it is "
                "different from the Reno ZIP labels used only for distribution profiling in the Reno reports."
            ),
            "",
            (
                "Inside the selected RN/PT pool, the corrected mirrored anchor uses the reference "
                "percentile. OT remains a separate deterministic rotation because OT is not scored in "
                "the current HHVBP objective."
            ),
            "",
            "## Why capacity and scenario simulation are still needed",
            "",
            (
                "The one-patient interface does not consume future caseload capacity. Repeated "
                "recommendations can therefore concentrate work even when every individual ranking is "
                "computed correctly. Batch simulation is needed to measure fallback, continuity, travel, "
                "workload spread, overload, and sensitivity to different assumptions."
            ),
            "",
            "## What the available data cannot answer",
            "",
            (
                "No usable sponsor patient, clinician, assignment, ZIP-history, OASIS, HHCAHPS, "
                "scheduling, capacity, or outcome dataset was available. Public agency-level data can "
                "help configure marginal distributions, but it cannot identify patient-clinician "
                "effects. The 0.75 threshold remains a capacity-oriented pilot setting with provenance "
                f"`{THRESHOLD_PROVENANCE}`."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def render_current_model_report(context: Mapping[str, Any]) -> str:
    metric_library = context["metric_library"]
    lines = _common_report_preamble("Current CY2025 Assignment Model", context)
    lines.extend(["## Locked measure set", ""])
    lines.extend(
        _markdown_table(
            ["Canonical metric", "Accepted name context", "CY2025 weight", "Direction"],
            (
                (
                    metric,
                    METRIC_DISPLAY_NAMES[metric],
                    _fmt(metric_library["metrics"][metric]["composite_weight"], 2),
                    RAW_DIRECTION[metric],
                )
                for metric in CY2025_METRIC_WEIGHTS
            ),
        )
    )
    lines.extend(
        [
            "",
            (
                "The canonical name map treats DFS/DC Function, DTC/DTC-PAC, Agency Rating/Overall "
                "Rating, Recommend/Willingness to Recommend, Care Issues/Specific Care Issues, and "
                "Communications aliases as single metrics so they are neither duplicated nor omitted."
            ),
            "",
            "## Patient severity context",
            "",
            (
                "For each of the ten metrics, expert-elicited factor importance and category "
                "favorability produce an analytic Beta moment-matched distribution. Direction-corrected "
                "distance between configured best and worst anchors becomes metric need. CY2025 weights "
                "then produce `risk_score_rho_raw`; the empirical reference converts it to `risk_percentile_u`."
            ),
            "",
            "## Assignment sequence",
            "",
            "1. Normalize metric names, score directions, ages, and ZIP inputs.",
            "2. Compute capability percentiles within each discipline on its full request pool.",
            "3. Classify high risk only when `risk_percentile_u > 0.75`.",
            "4. Use the full pool for high-risk cases; otherwise try the assignment ZIP-history pool.",
            "5. Fall back to the full pool when ZIP coverage is below RN=3, PT=3, or OT=1.",
            "6. Apply percentile-mirrored RN/PT ordering in the selected pool and deterministic OT rotation.",
            "",
            (
                "ZIP selection does not recompute capability percentiles. The request-pool reference "
                "means same-discipline roster changes can still move percentile values."
            ),
            "",
            "## Audit fields",
            "",
            (
                "The internal audit pathway records raw rho, reference percentile, percentile and raw "
                "thresholds, threshold provenance, normalized patient ZIP, route and pool sizes by "
                "discipline, fallback reason, selected ranks, and observed/imputed/synthetic value status. "
                "The simplified RN/PT/OT frontend response remains separate."
            ),
            "",
            "## Limits requiring operational evaluation",
            "",
            (
                "The online policy does not jointly schedule a cohort or consume capacity. Clinician "
                "sample sizes are not available for production uncertainty estimation. Travel, fairness, "
                "continuity, workload, and threshold behavior therefore require explicit batch scenarios "
                "and eventual validation with authorized operational and outcome data."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def render_sponsor_report(context: Mapping[str, Any]) -> str:
    reference_metadata = context["reference"].get("metadata", {})
    evidence = context.get("experiment_evidence", {})
    experiment_lines: list[str] = []
    fine = evidence.get("fine_threshold")
    if isinstance(fine, Mapping) and fine.get("threshold_optimization_evaluated"):
        experiment_lines.append(
            f"- The training-seed minimax-regret threshold was {float(fine['robust_minimax_regret_threshold']):.2f}; "
            f"held-out maximum regret was {float(fine.get('held_out_robust_max_regret', math.nan)):.6f}. "
            "This did not change the 0.75 pilot."
        )
    major = evidence.get("major_p4")
    if isinstance(major, Mapping):
        experiment_lines.append(
            f"- The fixed-0.75 P4 major-scenario run averaged utility {float(major['simulated_outcome_utility']):.4f}, "
            f"ZIP continuity {float(major['zip_continuity_rate']):.3f}, and unassigned share {float(major['unassigned_rate']):.3f}."
        )
    p4 = evidence.get("bakeoff_p4")
    p6 = evidence.get("bakeoff_p6")
    if isinstance(p4, Mapping) and isinstance(p6, Mapping):
        experiment_lines.append(
            f"- In the fixed-pilot bake-off, P4 utility was {float(p4['simulated_outcome_utility']):.4f}; "
            f"P6 utility was {float(p6['simulated_outcome_utility']):.4f}, with P6 continuity "
            f"{float(p6['zip_continuity_rate']):.3f} and travel {float(p6['travel_proxy']):.2f}. "
            "Policy ordering remains scenario-dependent."
        )
    stress_p4 = evidence.get("stress_p4")
    stress_p6 = evidence.get("stress_p6")
    if isinstance(stress_p4, Mapping) and isinstance(stress_p6, Mapping):
        experiment_lines.append(
            f"- Across the operational stress grid, P4 utility was {float(stress_p4['simulated_outcome_utility']):.4f}; "
            f"P6 had continuity {float(stress_p6['zip_continuity_rate']):.3f} and travel {float(stress_p6['travel_proxy']):.2f}."
        )
    diagnostics = evidence.get("structural_diagnostics")
    if isinstance(diagnostics, Mapping):
        experiment_lines.append(
            f"- The structural audit covered {int(diagnostics.get('profile_count', 0)):,} profiles with "
            f"{int(diagnostics.get('monotonicity_violations', 0))} configured-score monotonicity violations."
        )
    if not experiment_lines:
        experiment_lines.append("- Completed experiment artifacts were not available when this source report was generated.")

    lines = _common_report_preamble("Sponsor Review Brief: Current Aptatio Prototype", context)
    lines.extend(
        [
            "## Current decision policy",
            "",
            (
                "The sponsor-requested hard ZIP-history policy remains the default. Cases strictly "
                "above the 0.75 reference percentile use the full discipline pool. Cases at or below "
                "0.75 try the assignment ZIP-history pool and fall back to the full pool when coverage "
                "does not meet the discipline minimum."
            ),
            "",
            "## What has been corrected in the current model",
            "",
            "- The active patient model uses exactly seven factors, including the five-band age factor.",
            "- Clinician percentiles are computed within discipline before ZIP filtering.",
            "- Raw modeled need and empirical reference percentile are retained as different quantities.",
            "- The mirrored anchor and strict threshold rule use the reference percentile.",
            "- Assignment ZIP routing is separated from Reno distribution profiling.",
            "- PPH and higher-is-better metric narratives use direction-correct anchor language.",
            "",
            "## Reference and threshold provenance",
            "",
            (
                f"The checked-in risk reference contains {reference_metadata.get('sample_size', 0):,} "
                "weighted Monte Carlo profiles and is accompanied by the exhaustive 2,160-profile "
                "structural grid. Its source mode is "
                f"`{reference_metadata.get('source_mode', 'unknown')}` and sponsor data were not used."
            ),
            "",
            (
                "The 0.75 threshold is a public-only semi-synthetic capacity pilot. It remains a "
                "review setting unless the project team approves a later change after operational evaluation."
            ),
            "",
            "## Completed simulation evidence",
            "",
            *experiment_lines,
            "",
            (
                "These are configured simulation comparisons, not sponsor historical validation or evidence "
                "of clinical causality, field benefit, or payment change."
            ),
            "",
            "## Decisions still requiring sponsor or project-team input",
            "",
            "- Whether to retain hard ZIP filtering after reviewing fallback, travel, continuity, and workload results.",
            "- What real clinician capacity and availability constraints should govern deployment.",
            "- Whether an external clinician reference distribution can reduce same-discipline roster sensitivity.",
            "- What authorized data and prospective design can support outcome validation.",
            "- What fairness tolerances and escalation rules should be approved for a pilot.",
            "",
            "## Boundaries for sponsor interpretation",
            "",
            (
                "Configured scenarios can compare policy behavior under stated assumptions. They do "
                "not demonstrate patient benefit, clinical causality, payment change, or field performance."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def render_report_index(context: Mapping[str, Any]) -> str:
    lines = _common_report_preamble("Current Report Artifact Index", context)
    lines.extend(
        [
            "## Human-readable reports",
            "",
            "- [Reno PPH report](reno_pph_report.md)",
            "- [Reno all-other-metrics report](reno_other_metrics_report.md)",
            "- [Reno five-age sensitivity report](reno_age_sensitivity_report.md)",
            "- [Nontechnical explanation](nontechnical_report.md)",
            "- [Current-model report](current_model_report.md)",
            "- [Sponsor review brief](sponsor_report.md)",
            "",
            "## Machine-readable scenario data",
            "",
            "- `reno_pph_scenarios.csv` and `reno_pph_scenarios.json`",
            "- `reno_other_metrics_scenarios.csv` and `reno_other_metrics_scenarios.json`",
            "- `reno_age_sensitivity.csv` and `reno_age_sensitivity.json`",
            "",
            "## Current PDFs",
            "",
            "Run `python scripts/generate_pdf_reports.py` after this source generator. It writes six current PDFs and `pdf_manifest.json` under `output/pdf/`, then links that manifest back into `report_manifest.json`.",
            "",
            (
                "`report_manifest.json` records source hashes, output hashes, model lock, and the "
                "unsupported-claim scan result. `pdf_or_latex_generated` remains false until the separate PDF step succeeds."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _is_negated(text: str, match_start: int) -> bool:
    prefix = text[max(0, match_start - 60) : match_start]
    return bool(
        re.search(
            r"(?:\bnot\b|\bno\b|\bnever\b|\bcannot\b|\bdoes\s+not\b|\bdo\s+not\b|\bwithout\b)"
            r"[^.;:\n]{0,42}$",
            prefix,
            flags=re.IGNORECASE,
        )
    )


def scan_unsupported_claims(text: str, source: str = "<memory>") -> list[dict[str, Any]]:
    """Return unsupported-claim findings with deterministic line locations."""
    findings: list[dict[str, Any]] = []
    for rule in UNSUPPORTED_CLAIM_RULES:
        for match in rule.pattern.finditer(text):
            if _is_negated(text, match.start()):
                continue
            line_number = text.count("\n", 0, match.start()) + 1
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            if line_end < 0:
                line_end = len(text)
            excerpt = text[line_start:line_end].strip()
            findings.append(
                {
                    "source": source,
                    "line": line_number,
                    "rule_id": rule.rule_id,
                    "explanation": rule.explanation,
                    "excerpt": excerpt,
                }
            )
    return sorted(findings, key=lambda finding: (finding["source"], finding["line"], finding["rule_id"]))


def scan_report_paths(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for raw_path in sorted((Path(path) for path in paths), key=lambda path: str(path)):
        text = raw_path.read_text(encoding="utf-8")
        findings.extend(scan_unsupported_claims(text, source=str(raw_path)))
    return findings


def assert_no_unsupported_claims(paths: Iterable[str | Path]) -> None:
    findings = scan_report_paths(paths)
    if findings:
        detail = "\n".join(
            f"{finding['source']}:{finding['line']} [{finding['rule_id']}] {finding['excerpt']}"
            for finding in findings
        )
        raise UnsupportedReportClaimError(f"Unsupported report claims found:\n{detail}")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text(path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"Cannot write empty report CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _dataset_payload(
    artifact_type: str,
    rows: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    generated_at: str,
    controls: Mapping[str, Any],
) -> dict[str, Any]:
    reference_metadata = context["reference"].get("metadata", {})
    return {
        "artifact_type": artifact_type,
        "schema_version": "1.0",
        "generated_at_utc": generated_at,
        "generator_version": REPORT_GENERATOR_VERSION,
        "model_lock": {
            key: context["global_config"][key]
            for key in ("cms_model", "performance_year", "payment_year", "measure_set_version", "cms_version_locked")
        },
        "validation": {
            "public_data_only": True,
            "expert_configured": True,
            "semi_synthetic": True,
            "simulation_based": True,
            "outcome_validated": False,
            "production_validated": False,
            "sponsor_data_used": False,
            "warning": VALIDATION_BOUNDARY,
        },
        "threshold": {
            "strict_rule": "risk_percentile_u > 0.75",
            "lower_risk_rule": "risk_percentile_u <= 0.75",
            "threshold_percentile": PILOT_THRESHOLD_PERCENTILE,
            "threshold_raw_equivalent": float(context["reference"]["tau_raw"]),
            "provenance": THRESHOLD_PROVENANCE,
        },
        "risk_reference": {
            "reference_id": reference_metadata.get("reference_id"),
            "source_mode": reference_metadata.get("source_mode"),
            "sample_size": reference_metadata.get("sample_size"),
            "structural_grid_size": reference_metadata.get("structural_grid_size"),
        },
        "controls": dict(controls),
        "assignment_zip_routing_applied": False,
        "row_count": len(rows),
        "rows": list(rows),
    }


def write_report_bundle(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    config_dir: str | Path = DEFAULT_CONFIG_DIR,
    reference_path: str | Path = DEFAULT_REFERENCE_PATH,
    generated_at: str | None = None,
    experiment_root: str | Path = DEFAULT_EXPERIMENT_ROOT,
) -> dict[str, Any]:
    """Generate the complete current Markdown/CSV/JSON report bundle."""
    output_dir = Path(output_dir)
    context = _load_reporting_context(config_dir, reference_path)
    context["experiment_evidence"] = _load_experiment_evidence(
        experiment_root,
        reporting_context=context,
    )
    generated_at_utc = _generation_timestamp(context["public_config"], generated_at)

    geographic_rows = _evaluate_scenarios(
        build_reno_geographic_profiles(), context, "reno_geographic_sensitivity"
    )
    age_rows = _evaluate_scenarios(
        build_reno_age_sensitivity_profiles(), context, "reno_age_sensitivity"
    )
    pph_rows = [row for row in geographic_rows if row["metric_key"] == "PPH"]
    other_rows = [row for row in geographic_rows if row["metric_key"] != "PPH"]

    documents = {
        "reno_pph_report.md": render_reno_pph_report(geographic_rows, context),
        "reno_other_metrics_report.md": render_reno_other_metrics_report(geographic_rows, context),
        "reno_age_sensitivity_report.md": render_reno_age_sensitivity_report(age_rows, context),
        "nontechnical_report.md": render_nontechnical_report(context),
        "current_model_report.md": render_current_model_report(context),
        "sponsor_report.md": render_sponsor_report(context),
        "README.md": render_report_index(context),
    }
    for filename, text in documents.items():
        _write_text(output_dir / filename, text)

    controls_geo = {
        "controlled_age_group": CONTROLLED_RENO_AGE_GROUP,
        "fixed_health_status": "moderate",
        "fixed_housing_type": "single_home",
        "fixed_driving_condition": "good_summer",
        "varied_geographic_factors": ["house_price_zip", "area_type", "distance_to_clinic"],
        "profiling_zip_is_assignment_zip": False,
    }
    controls_age = {
        "varied_factor": "age_group",
        "age_groups": list(AGE_GROUP_ORDER),
        "fixed_factors": {
            "health_status": "moderate",
            "house_price_zip": "average",
            "area_type": "metropolitan",
            "housing_type": "single_home",
            "distance_to_clinic": "near",
            "driving_condition": "good_summer",
        },
        "profiling_zip_is_assignment_zip": False,
    }
    json_payloads = {
        "reno_pph_scenarios.json": _dataset_payload(
            "reno_pph_model_scenario_distributions", pph_rows, context, generated_at_utc, controls_geo
        ),
        "reno_other_metrics_scenarios.json": _dataset_payload(
            "reno_other_metrics_model_scenario_distributions", other_rows, context, generated_at_utc, controls_geo
        ),
        "reno_age_sensitivity.json": _dataset_payload(
            "reno_five_age_model_sensitivity", age_rows, context, generated_at_utc, controls_age
        ),
    }
    for filename, payload in json_payloads.items():
        _write_json(output_dir / filename, payload)

    csv_payloads = {
        "reno_pph_scenarios.csv": pph_rows,
        "reno_other_metrics_scenarios.csv": other_rows,
        "reno_age_sensitivity.csv": age_rows,
    }
    for filename, rows in csv_payloads.items():
        _write_csv(output_dir / filename, rows)

    claim_scan_paths = [
        output_dir / filename
        for filename in (*documents.keys(), *json_payloads.keys(), *csv_payloads.keys())
    ]
    assert_no_unsupported_claims(claim_scan_paths)

    source_paths = [
        Path(config_dir) / "hhvbp_global_config.json",
        Path(config_dir) / "metric_parameter_library.json",
        Path(config_dir) / "assignment_parameters.json",
        Path(config_dir) / "public_semi_synthetic_dataset_config.json",
        Path(reference_path),
    ]
    source_paths.extend(context["experiment_evidence"].get("source_paths", []))
    artifact_paths = sorted(claim_scan_paths, key=lambda path: path.name)
    manifest = {
        "artifact_type": "current_hhvbp_report_bundle_manifest",
        "schema_version": "1.0",
        "generated_at_utc": generated_at_utc,
        "generator_version": REPORT_GENERATOR_VERSION,
        "model_lock": {
            key: context["global_config"][key]
            for key in ("cms_model", "performance_year", "payment_year", "measure_set_version", "cms_version_locked")
        },
        "validation_boundary": VALIDATION_BOUNDARY,
        "threshold_provenance": THRESHOLD_PROVENANCE,
        "unsupported_claim_scan": {
            "status": "passed",
            "files_scanned": len(claim_scan_paths),
            "finding_count": 0,
            "rule_ids": [rule.rule_id for rule in UNSUPPORTED_CLAIM_RULES],
        },
        "source_sha256": {str(path.relative_to(PROJECT_ROOT)): _sha256(path) for path in source_paths},
        "artifacts": [
            {
                "path": path.name,
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in artifact_paths
        ],
        "pdf_or_latex_generated": False,
    }
    _write_json(output_dir / "report_manifest.json", manifest)
    return manifest


__all__ = [
    "AGE_GROUP_ORDER",
    "CONTROLLED_RENO_AGE_GROUP",
    "DEFAULT_OUTPUT_DIR",
    "PILOT_THRESHOLD_PERCENTILE",
    "THRESHOLD_PROVENANCE",
    "ExperimentEvidenceValidationError",
    "UnsupportedReportClaimError",
    "assert_no_unsupported_claims",
    "build_reno_age_sensitivity_profiles",
    "build_reno_age_sensitivity_rows",
    "build_reno_geographic_profiles",
    "build_reno_geographic_rows",
    "describe_distance_to_anchors",
    "scan_report_paths",
    "scan_unsupported_claims",
    "write_report_bundle",
]
