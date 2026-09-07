"""Deterministic public-only batch assignment and policy experiments.

This module is intentionally independent of the online assignment interface.  It
does not contain production policy configuration and does not change the sponsor
policy.  Every generated patient, clinician, ZIP history, capacity value, and
outcome is semi-synthetic.  Results are simulation evidence only: they are not
patient-level predictions, causal estimates, outcome validation, or evidence of
payment improvement.

The experiment design uses common random numbers.  For a given configuration and
seed, all policies and threshold candidates receive the exact same patients,
clinician latent values, and patient outcome-noise draws.  ``simulate_policy``
copies the roster before every run, so each threshold re-routes patients, rebuilds
pools, reassigns clinicians, consumes capacity, and re-simulates outcomes.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from hhvbp_risk_reference import (
    DEFAULT_STRUCTURAL_GRID_PATH,
    DEFAULT_STRUCTURAL_GRID_METADATA_PATH,
    empirical_risk_percentile,
    load_risk_reference,
    quantile_raw,
)


PROVENANCE_LABEL = "public_only_semi_synthetic_capacity_pilot_not_outcome_validated"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CMS_MODEL = "Expanded HHVBP"
PERFORMANCE_YEAR = 2025
PAYMENT_YEAR = 2027
MEASURE_SET_VERSION = "CY2025"
PRODUCTION_PILOT_THRESHOLD = 0.75
DISCIPLINES = ("RN", "PT", "OT")
MIN_ZIP_POOL = {"RN": 3, "PT": 3, "OT": 1}

# Canonical CY2025 weights are repeated here so the simulator is standalone and
# cannot accidentally inherit a later runtime configuration migration.
CY2025_WEIGHTS = {
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
METRICS = tuple(CY2025_WEIGHTS)
LOWER_IS_BETTER = frozenset({"PPH"})

POLICY_NAMES = {
    "P0": "deterministic_round_robin_baseline",
    "P1": "highest_scoring_feasible",
    "P2": "historical_raw_rho_mirrored",
    "P3": "percentile_mirrored_no_zip",
    "P4": "threshold_hard_zip_sponsor_policy",
    "P5": "threshold_soft_zip_bonus",
    "P6": "capacity_constrained_greedy_optimization",
    "P7": "latent_quality_capacity_greedy_comparator",
}


@dataclass(frozen=True)
class Scenario:
    """Outcome and operational assumptions for a named simulation scenario."""

    key: str
    description: str
    quality_effect: float = 0.16
    risk_quality_interaction: float = 0.0
    zip_continuity_effect: float = 0.03
    travel_penalty: float = 0.025
    capacity_multiplier: float = 1.0
    zip_sparsity_override: float | None = None
    score_uncertainty: float = 0.035


SCENARIOS: dict[str, Scenario] = {
    "A": Scenario("A", "additive clinician-quality effect", quality_effect=0.18),
    "B": Scenario(
        "B",
        "positive high-risk by clinician-quality complementarity",
        quality_effect=0.11,
        risk_quality_interaction=0.24,
    ),
    "C": Scenario(
        "C",
        "diminishing returns / negative interaction",
        quality_effect=0.20,
        risk_quality_interaction=-0.25,
    ),
    "D": Scenario(
        "D",
        "no true clinician or routing causal effect",
        quality_effect=0.0,
        risk_quality_interaction=0.0,
        zip_continuity_effect=0.0,
        travel_penalty=0.0,
    ),
    "E": Scenario("E", "no ZIP-continuity benefit", zip_continuity_effect=0.0),
    "F": Scenario("F", "positive ZIP-continuity benefit", zip_continuity_effect=0.09),
    "G": Scenario("G", "sparse ZIP coverage", zip_sparsity_override=0.78),
    "H": Scenario("H", "clinician capacity shortage", capacity_multiplier=0.68),
    "I": Scenario("I", "high clinician-score uncertainty", score_uncertainty=0.20),
}


def _default_thresholds() -> tuple[float, ...]:
    return tuple(round(i / 100.0, 2) for i in range(101))


@dataclass(frozen=True)
class BatchExperimentConfig:
    """Simulation controls; defaults represent the requested major run size."""

    n_patients: int = 5_000
    seeds: tuple[int, ...] = tuple(range(100))
    roster_sizes: Mapping[str, int] = field(
        default_factory=lambda: {"RN": 24, "PT": 18, "OT": 6}
    )
    capacity_ratio: float = 1.05
    zip_sparsity: float = 0.25
    availability_rate: float = 0.96
    thresholds: tuple[float, ...] = field(default_factory=_default_thresholds)
    policies: tuple[str, ...] = tuple(POLICY_NAMES)
    disciplines: tuple[str, ...] = DISCIPLINES
    min_zip_pool: Mapping[str, int] = field(default_factory=lambda: dict(MIN_ZIP_POOL))
    outcome_noise_sd: float = 0.035
    design_label: str = "requested_major_design"

    def validate(self) -> None:
        if self.n_patients <= 0:
            raise ValueError("n_patients must be positive")
        if not self.seeds:
            raise ValueError("at least one seed is required")
        if self.capacity_ratio <= 0:
            raise ValueError("capacity_ratio must be positive")
        if not 0 <= self.zip_sparsity <= 1:
            raise ValueError("zip_sparsity must be in [0, 1]")
        if not 0 < self.availability_rate <= 1:
            raise ValueError("availability_rate must be in (0, 1]")
        if any(t < 0 or t > 1 for t in self.thresholds):
            raise ValueError("threshold candidates must be in [0, 1]")
        unknown_policies = set(self.policies).difference(POLICY_NAMES)
        if unknown_policies:
            raise ValueError(f"unknown policies: {sorted(unknown_policies)}")
        for discipline in self.disciplines:
            if discipline not in DISCIPLINES:
                raise ValueError(f"unsupported discipline {discipline!r}")
            if int(self.roster_sizes.get(discipline, 0)) <= 0:
                raise ValueError(f"roster size for {discipline} must be positive")


ZIP_CODES = ("01234", "10001", "30301", "60601", "85001", "89501", "89502", "94105")
ZIP_AREA = {
    "01234": "rural",
    "10001": "metropolitan",
    "30301": "metropolitan",
    "60601": "metropolitan",
    "85001": "metropolitan",
    "89501": "micropolitan",
    "89502": "micropolitan",
    "94105": "metropolitan",
}
ZIP_PRICE = {
    "01234": "low",
    "10001": "high",
    "30301": "average",
    "60601": "average",
    "85001": "low",
    "89501": "average",
    "89502": "low",
    "94105": "high",
}

FACTOR_LEVELS: dict[str, tuple[str, ...]] = {
    "health_status": ("healthy", "moderate", "unhealthy"),
    "age_group": ("age_40_50", "age_50_60", "age_60_70", "age_70_80", "age_80_plus"),
    "house_price_zip": ("high", "average", "low"),
    "area_type": ("metropolitan", "micropolitan", "small_town", "rural"),
    "housing_type": ("single_home", "apartment"),
    "distance_to_clinic": ("near", "medium", "far"),
    "driving_condition": ("good_summer", "bad_winter"),
}
FACTOR_SEVERITY: dict[str, dict[str, float]] = {
    factor: {level: i / (len(levels) - 1) for i, level in enumerate(levels)}
    for factor, levels in FACTOR_LEVELS.items()
}
# House-price levels run from favorable high to unfavorable low; all other level
# tuples are likewise ordered from lower modeled need to higher modeled need.
FACTOR_WEIGHTS = {
    "health_status": 0.32,
    "age_group": 0.18,
    "house_price_zip": 0.12,
    "area_type": 0.10,
    "housing_type": 0.08,
    "distance_to_clinic": 0.12,
    "driving_condition": 0.08,
}


@lru_cache(maxsize=1)
def _canonical_risk_assets() -> tuple[dict, pd.DataFrame]:
    """Load the same persisted ECDF and canonical grid used by runtime."""

    reference = load_risk_reference()
    if not DEFAULT_STRUCTURAL_GRID_METADATA_PATH.exists():
        raise ValueError(
            f"canonical structural metadata is missing: {DEFAULT_STRUCTURAL_GRID_METADATA_PATH}"
        )
    grid_metadata = json.loads(
        DEFAULT_STRUCTURAL_GRID_METADATA_PATH.read_text(encoding="utf-8")
    )
    grid = pd.read_csv(DEFAULT_STRUCTURAL_GRID_PATH, dtype={factor: str for factor in FACTOR_LEVELS})
    required = set(FACTOR_LEVELS) | {"rho_raw", "risk_percentile_u"}
    missing = required.difference(grid.columns)
    if missing:
        raise ValueError(f"canonical structural grid missing columns: {sorted(missing)}")
    if len(grid) != 2_160 or grid[list(FACTOR_LEVELS)].duplicated().any():
        raise ValueError("canonical structural grid must contain 2,160 unique factor profiles")
    reference_metadata = reference["metadata"]
    expected_metadata = {
        "reference_id": reference_metadata["reference_id"],
        "config_hash": reference_metadata["config_hash"],
        "row_count": 2_160,
        "expected_row_count": 2_160,
        "factor_order": list(FACTOR_LEVELS),
        "risk_reference_artifact_sha256": reference["artifact_sha256"],
        "structural_grid_csv_sha256": hashlib.sha256(
            DEFAULT_STRUCTURAL_GRID_PATH.read_bytes()
        ).hexdigest(),
    }
    mismatches = {
        key: (grid_metadata.get(key), expected)
        for key, expected in expected_metadata.items()
        if grid_metadata.get(key) != expected
    }
    if mismatches:
        raise ValueError(f"canonical structural metadata mismatch: {mismatches}")
    if not grid_metadata.get("public_data_only") or grid_metadata.get("production_validated"):
        raise ValueError("canonical structural metadata has invalid evidence-boundary flags")
    if not math.isclose(
        float(grid_metadata.get("tau_raw", math.nan)),
        float(reference["tau_raw"]),
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError("canonical structural metadata has a stale raw threshold")
    expected_percentiles = grid["rho_raw"].map(
        lambda value: empirical_risk_percentile(
            float(value), reference["sorted_risk_scores"]
        )
    )
    if not np.allclose(
        grid["risk_percentile_u"].to_numpy(dtype=float),
        expected_percentiles.to_numpy(dtype=float),
        rtol=0.0,
        atol=1e-12,
    ):
        raise ValueError("canonical structural grid percentiles do not match the persisted ECDF")
    return reference, grid


def _reference_percentiles(values: Iterable[float]) -> np.ndarray:
    reference, _ = _canonical_risk_assets()
    sorted_values = reference["sorted_risk_scores"]
    return np.fromiter(
        (empirical_risk_percentile(float(value), sorted_values) for value in values),
        dtype=float,
    )


def _attach_canonical_risk(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach canonical raw risk and persisted-reference percentile by profile."""

    _, grid = _canonical_risk_assets()
    factors = list(FACTOR_LEVELS)
    lookup = grid[factors + ["rho_raw", "risk_percentile_u"]]
    out = frame.copy()
    out["_input_order"] = np.arange(len(out), dtype=int)
    out = out.merge(lookup, on=factors, how="left", validate="many_to_one", sort=False)
    if out[["rho_raw", "risk_percentile_u"]].isna().any().any():
        raise ValueError("one or more generated profiles are absent from the canonical structural grid")
    out = out.sort_values("_input_order", kind="stable").drop(columns="_input_order").reset_index(drop=True)
    return out.rename(columns={"rho_raw": "risk_score_rho_raw"})


@lru_cache(maxsize=1)
def _canonical_factor_contributions() -> dict[str, dict[str, float]]:
    """Recover exact additive raw-risk contributions from the canonical grid."""

    _, grid = _canonical_risk_assets()
    global_config = json.loads(
        (PROJECT_ROOT / "config" / "hhvbp_global_config.json").read_text(encoding="utf-8")
    )
    best = global_config["profiles"]["best_case"]
    base_mask = np.ones(len(grid), dtype=bool)
    for factor in FACTOR_LEVELS:
        base_mask &= grid[factor].eq(best[factor]).to_numpy()
    baseline = float(grid.loc[base_mask, "rho_raw"].iloc[0])
    contributions: dict[str, dict[str, float]] = {}
    for factor in FACTOR_LEVELS:
        contributions[factor] = {}
        for level in FACTOR_LEVELS[factor]:
            mask = np.ones(len(grid), dtype=bool)
            for other in FACTOR_LEVELS:
                mask &= grid[other].eq(level if other == factor else best[other]).to_numpy()
            contributions[factor][level] = float(grid.loc[mask, "rho_raw"].iloc[0]) - baseline

    reconstructed = np.full(len(grid), baseline, dtype=float)
    for factor in FACTOR_LEVELS:
        reconstructed += grid[factor].map(contributions[factor]).to_numpy(dtype=float)
    if not np.allclose(reconstructed, grid["rho_raw"].to_numpy(dtype=float), rtol=0.0, atol=1e-12):
        raise ValueError("canonical risk grid is not additive at the configured tolerance")
    return contributions


def round_half_up(value: float) -> int:
    """Round non-negative values with .5 going upward."""

    return int(math.floor(float(value) + 0.5))


def mirrored_anchor_index(risk_value: float, pool_size: int) -> int:
    if pool_size <= 0:
        raise ValueError("pool_size must be positive")
    risk = float(np.clip(risk_value, 0.0, 1.0))
    return round_half_up((1.0 - risk) * (pool_size - 1))


def stable_empirical_percentile(values: Sequence[float]) -> np.ndarray:
    """Return deterministic average-tie empirical percentiles on [0, 1]."""

    series = pd.Series(np.asarray(values, dtype=float))
    if not np.isfinite(series).all():
        raise ValueError("risk values must all be finite")
    if len(series) == 1:
        return np.array([0.5], dtype=float)
    ranks = series.rank(method="average").to_numpy(dtype=float)
    return np.clip((ranks - 1.0) / (len(series) - 1.0), 0.0, 1.0)


def canonical_config_hash(config: BatchExperimentConfig) -> str:
    payload = asdict(config)
    payload["roster_sizes"] = dict(config.roster_sizes)
    payload["min_zip_pool"] = dict(config.min_zip_pool)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=list).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _frame_content_hash(frame: pd.DataFrame) -> str:
    """Hash the exact ordered input frame used by a simulation rerun."""

    payload = frame.to_json(
        orient="split",
        date_format="iso",
        date_unit="ns",
        double_precision=15,
        default_handler=str,
        index=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _simulation_design_fingerprint(
    patients: pd.DataFrame,
    clinicians: pd.DataFrame,
    min_zip_pool: Mapping[str, int],
    outcome_noise_sd: float,
    *,
    config_hash: str | None = None,
) -> str:
    """Bind rerun identity to inputs and operational controls.

    Direct helper calls may not have a :class:`BatchExperimentConfig`, so input
    content is always included.  Config-driven callers add the canonical config
    hash as an additional design boundary.
    """

    payload = {
        "config_hash": config_hash,
        "patient_frame_sha256": _frame_content_hash(patients),
        "clinician_frame_sha256": _frame_content_hash(clinicians),
        "min_zip_pool": {str(key): int(value) for key, value in sorted(min_zip_pool.items())},
        "outcome_noise_sd": float(outcome_noise_sd),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _seed_sequence(seed: int, stream: int) -> np.random.Generator:
    # SeedSequence makes streams stable when new generators are added later.
    return np.random.default_rng(np.random.SeedSequence([int(seed), int(stream), 2025]))


def _sample_categories(rng: np.random.Generator, levels: Sequence[str], probabilities: Sequence[float], n: int) -> np.ndarray:
    probs = np.asarray(probabilities, dtype=float)
    probs = probs / probs.sum()
    return rng.choice(np.asarray(levels, dtype=object), size=n, p=probs)


def generate_patient_cohort(config: BatchExperimentConfig, seed: int) -> pd.DataFrame:
    """Generate a repeatable semi-synthetic cohort and common noise draws."""

    config.validate()
    n = config.n_patients
    rng = _seed_sequence(seed, 1)
    zip_values = _sample_categories(rng, ZIP_CODES, [0.07, 0.15, 0.13, 0.14, 0.11, 0.13, 0.17, 0.10], n)
    age = _sample_categories(
        rng,
        FACTOR_LEVELS["age_group"],
        [0.08, 0.17, 0.27, 0.30, 0.18],
        n,
    )
    health = _sample_categories(rng, FACTOR_LEVELS["health_status"], [0.25, 0.48, 0.27], n)
    housing = _sample_categories(rng, FACTOR_LEVELS["housing_type"], [0.68, 0.32], n)
    distance = _sample_categories(rng, FACTOR_LEVELS["distance_to_clinic"], [0.43, 0.38, 0.19], n)
    driving = _sample_categories(rng, FACTOR_LEVELS["driving_condition"], [0.78, 0.22], n)
    area = np.array([ZIP_AREA[str(value)] for value in zip_values], dtype=object)
    house_price = np.array([ZIP_PRICE[str(value)] for value in zip_values], dtype=object)

    frame = pd.DataFrame(
        {
            "patient_id": [f"S{int(seed):05d}-P{i:06d}" for i in range(n)],
            "patient_zip": zip_values.astype(str),
            "health_status": health,
            "age_group": age,
            "house_price_zip": house_price,
            "area_type": area,
            "housing_type": housing,
            "distance_to_clinic": distance,
            "driving_condition": driving,
        }
    )
    # Risk is intentionally not ranked within this generated cohort.  Use the
    # canonical seven-factor score and the persisted 10,000-profile ECDF so a
    # given patient profile has identical routing semantics online and in every
    # experiment, regardless of cohort size/composition.
    frame = _attach_canonical_risk(frame)
    frame["clinical_need_decile"] = np.minimum(9, np.floor(frame["risk_percentile_u"] * 10)).astype(int) + 1

    noise_rng = _seed_sequence(seed, 2)
    for index, metric in enumerate(METRICS):
        frame[f"noise_{metric}"] = noise_rng.normal(0.0, 1.0, size=n)
    frame["public_data_only"] = True
    frame["synthetic_data"] = True
    frame["production_validated"] = False
    return frame


def _capacity_vector(total_demand: int, n_clinicians: int, ratio: float, draws: np.ndarray) -> np.ndarray:
    target = max(1, int(round(total_demand * ratio)))
    weights = np.clip(draws, 0.4, None)
    raw = target * weights / weights.sum()
    values = np.floor(raw).astype(int)
    remainder = target - int(values.sum())
    if remainder > 0:
        order = np.argsort(-(raw - values), kind="stable")
        values[order[:remainder]] += 1
    return np.maximum(values, 1)


def generate_clinician_roster(
    config: BatchExperimentConfig,
    seed: int,
    scenario: Scenario,
) -> pd.DataFrame:
    """Generate discipline-compatible capacities and synthetic ZIP histories."""

    config.validate()
    rng = _seed_sequence(seed, 3)
    sparsity = config.zip_sparsity if scenario.zip_sparsity_override is None else scenario.zip_sparsity_override
    rows: list[dict] = []
    for discipline in config.disciplines:
        count = int(config.roster_sizes[discipline])
        quality = np.clip(rng.beta(5.5, 2.2, size=count), 0.05, 0.99)
        observed_z = rng.normal(0.0, 1.0, size=count)
        capacity_draws = rng.lognormal(mean=0.0, sigma=0.18, size=count)
        capacities = _capacity_vector(
            config.n_patients,
            count,
            config.capacity_ratio * scenario.capacity_multiplier,
            capacity_draws,
        )
        available_draws = rng.random(count)
        if not np.any(available_draws < config.availability_rate):
            available_draws[0] = 0.0
        for index in range(count):
            base_zip_index = int(rng.integers(0, len(ZIP_CODES)))
            base_zip = ZIP_CODES[base_zip_index]
            coverage_draws = rng.random(len(ZIP_CODES))
            service_zips = {
                zip_code
                for zip_code, draw in zip(ZIP_CODES, coverage_draws)
                if draw >= float(sparsity)
            }
            service_zips.add(base_zip)
            capacity = int(capacities[index])
            current_workload = int(min(capacity - 1, math.floor(capacity * rng.uniform(0.0, 0.04))))
            rows.append(
                {
                    "clinician_id": f"{discipline}-{index + 1:03d}",
                    "discipline": discipline,
                    "true_quality": float(quality[index]),
                    "score_noise_z": float(observed_z[index]),
                    "observed_quality": float(
                        np.clip(quality[index] + scenario.score_uncertainty * observed_z[index], 0.0, 1.0)
                    ),
                    "capacity": capacity,
                    "current_workload": current_workload,
                    "available": bool(available_draws[index] < config.availability_rate),
                    "base_zip": base_zip,
                    "service_zips": tuple(sorted(service_zips)),
                    "synthetic_clinician_data": True,
                    "synthetic_zip_history": True,
                    "public_data_only": True,
                }
            )
    return pd.DataFrame(rows).sort_values(["discipline", "clinician_id"], kind="stable").reset_index(drop=True)


def generate_simulation_inputs(
    config: BatchExperimentConfig,
    seed: int,
    scenario: Scenario | str = "A",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return the immutable patient/roster bundle used by all comparisons."""

    scenario_obj = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    return generate_patient_cohort(config, seed), generate_clinician_roster(config, seed, scenario_obj)


def travel_proxy(patient_zip: str, clinician_zip: str) -> float:
    """Deterministic synthetic travel miles; not a geocoded road distance."""

    p = ZIP_CODES.index(str(patient_zip))
    c = ZIP_CODES.index(str(clinician_zip))
    separation = min(abs(p - c), len(ZIP_CODES) - abs(p - c))
    return float(2.5 + 7.5 * separation)


def expected_favorable_outcome(
    risk_percentile: float,
    true_quality: float,
    zip_continuity: bool,
    travel_miles: float,
    scenario: Scenario,
) -> float:
    quality_centered = float(true_quality) - 0.5
    value = (
        0.66
        - 0.22 * float(risk_percentile)
        + scenario.quality_effect * quality_centered
        + scenario.risk_quality_interaction * float(risk_percentile) * quality_centered
        + scenario.zip_continuity_effect * float(bool(zip_continuity))
        - scenario.travel_penalty * (float(travel_miles) / 50.0)
    )
    return float(np.clip(value, 0.02, 0.98))


def scenario_oracle_upper_bound_utility(
    patients: pd.DataFrame,
    clinicians: pd.DataFrame,
    scenario: Scenario | str,
) -> float:
    """Return a valid latent-information upper bound, ignoring capacity coupling.

    Each patient/discipline independently receives its best available clinician.
    Relaxing shared capacity makes this an upper bound on every feasible policy,
    not an implementable scheduler.  It is used only as the regret reference.
    """

    scenario_obj = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    available = clinicians[clinicians["available"]].copy()
    values: list[float] = []
    for patient in patients.itertuples(index=False):
        patient_zip = str(patient.patient_zip)
        risk_percentile = float(patient.risk_percentile_u)
        for discipline in sorted(set(clinicians["discipline"])):
            pool = available[available["discipline"] == discipline]
            if pool.empty:
                values.append(0.0)
                continue
            best = max(
                expected_favorable_outcome(
                    risk_percentile,
                    float(clinician.true_quality),
                    patient_zip in set(clinician.service_zips),
                    travel_proxy(patient_zip, str(clinician.base_zip)),
                    scenario_obj,
                )
                for clinician in pool.itertuples(index=False)
            )
            values.append(float(best))
    return float(np.mean(values)) if values else 0.0


def _estimated_favorable_outcome(patient: pd.Series, clinician: pd.Series, scenario: Scenario) -> float:
    continuity = str(patient["patient_zip"]) in set(clinician["service_zips"])
    miles = travel_proxy(str(patient["patient_zip"]), str(clinician["base_zip"]))
    return expected_favorable_outcome(
        float(patient["risk_percentile_u"]),
        float(clinician["observed_quality"]),
        continuity,
        miles,
        scenario,
    )


def _select_mirrored(pool: pd.DataFrame, risk_value: float, score_column: str = "observed_quality") -> pd.Series:
    ranked = pool.sort_values([score_column, "clinician_id"], ascending=[False, True], kind="stable")
    return ranked.iloc[mirrored_anchor_index(risk_value, len(ranked))]


def _gini(values: Sequence[float]) -> float:
    array = np.asarray(values, dtype=float)
    if array.size == 0 or np.allclose(array.sum(), 0.0):
        return 0.0
    array = np.sort(np.clip(array, 0.0, None))
    n = len(array)
    return float((2.0 * np.dot(np.arange(1, n + 1), array) / (n * array.sum())) - (n + 1) / n)


def _group_gap(frame: pd.DataFrame, group: str, value: str = "expected_outcome") -> float:
    if frame.empty or group not in frame or value not in frame:
        return 0.0
    means = frame.groupby(group, dropna=False)[value].mean()
    if len(means) < 2:
        return 0.0
    return float(means.max() - means.min())


def _clinician_pool(
    roster: pd.DataFrame,
    workload: Mapping[str, int],
    discipline: str,
    require_capacity: bool = True,
) -> pd.DataFrame:
    pool = roster[(roster["discipline"] == discipline) & roster["available"]].copy()
    if require_capacity:
        pool = pool[
            [int(workload[str(cid)]) < int(capacity) for cid, capacity in zip(pool["clinician_id"], pool["capacity"])]
        ]
    return pool


def _choose_clinician(
    policy: str,
    patient: pd.Series,
    discipline: str,
    roster: pd.DataFrame,
    workload: Mapping[str, int],
    threshold: float,
    scenario: Scenario,
    min_zip_pool: Mapping[str, int],
    round_robin_cursor: dict[str, int],
) -> tuple[pd.Series | None, str, bool, int, int, int, str | None]:
    """Select one feasible clinician and return routing audit fields."""

    full_available = _clinician_pool(roster, workload, discipline, require_capacity=False)
    full_feasible = _clinician_pool(roster, workload, discipline, require_capacity=True)
    patient_zip = str(patient["patient_zip"])
    zip_available = full_available[full_available["service_zips"].map(lambda values: patient_zip in set(values))]
    zip_feasible = full_feasible[full_feasible["service_zips"].map(lambda values: patient_zip in set(values))]
    high_risk = float(patient["risk_percentile_u"]) > float(threshold)
    route = "global_high_risk" if high_risk else "global_lower_risk"
    fallback_reason: str | None = None
    selected_available = full_available
    selected_feasible = full_feasible

    if policy == "P4" and not high_risk:
        if len(zip_available) >= int(min_zip_pool[discipline]):
            route = "zip_history"
            selected_available = zip_available
            selected_feasible = zip_feasible
        else:
            route = "zip_history_fallback_full_pool"
            fallback_reason = "zip_pool_below_minimum"
    elif policy in {"P5", "P6"} and not high_risk:
        route = "soft_zip_bonus" if policy == "P5" else "capacity_optimization_lower_risk"
        if len(zip_available) < int(min_zip_pool[discipline]):
            fallback_reason = "zip_pool_below_minimum_soft_policy"
    elif policy == "P7":
        route = "latent_quality_capacity_greedy"
    elif policy in {"P0", "P1", "P2", "P3"}:
        route = POLICY_NAMES[policy]

    if selected_feasible.empty and policy == "P4" and not full_feasible.empty:
        selected_feasible = full_feasible
        route = "zip_history_capacity_fallback_full_pool"
        fallback_reason = "selected_zip_pool_capacity_exhausted"
    if selected_feasible.empty:
        return (
            None,
            route,
            high_risk,
            len(full_available),
            len(zip_available),
            len(selected_available),
            fallback_reason or "no_available_capacity",
        )

    if policy == "P0":
        ranked = selected_feasible.sort_values("clinician_id", kind="stable")
        cursor = round_robin_cursor[discipline] % len(ranked)
        chosen = ranked.iloc[cursor]
        round_robin_cursor[discipline] += 1
    elif policy == "P1":
        scores = selected_feasible.apply(lambda row: _estimated_favorable_outcome(patient, row, scenario), axis=1)
        ranked = selected_feasible.assign(_score=scores).sort_values(
            ["_score", "clinician_id"], ascending=[False, True], kind="stable"
        )
        chosen = ranked.iloc[0]
    elif policy == "P2":
        chosen = _select_mirrored(selected_feasible, float(patient["risk_score_rho_raw"]))
    elif policy == "P4" and discipline == "OT":
        # The sponsor/default comparator preserves OT's deterministic rotation;
        # threshold routing still determines which OT pool rotates.
        ranked = selected_feasible.sort_values("clinician_id", kind="stable")
        cursor = round_robin_cursor[discipline] % len(ranked)
        chosen = ranked.iloc[cursor]
        round_robin_cursor[discipline] += 1
    elif policy in {"P3", "P4"}:
        chosen = _select_mirrored(selected_feasible, float(patient["risk_percentile_u"]))
    elif policy == "P5":
        low_risk_bonus = 0.10 if not high_risk else 0.0
        scores = selected_feasible.apply(
            lambda row: float(row["observed_quality"])
            + low_risk_bonus * float(patient_zip in set(row["service_zips"]))
            - 0.015 * travel_proxy(patient_zip, str(row["base_zip"])) / 50.0,
            axis=1,
        )
        ranked = selected_feasible.assign(_soft_score=scores).sort_values(
            ["_soft_score", "clinician_id"], ascending=[False, True], kind="stable"
        )
        chosen = ranked.iloc[mirrored_anchor_index(float(patient["risk_percentile_u"]), len(ranked))]
    elif policy == "P6":
        low_risk_bonus = 0.06 if not high_risk else 0.0
        scores = selected_feasible.apply(
            lambda row: _estimated_favorable_outcome(patient, row, scenario)
            + low_risk_bonus * float(patient_zip in set(row["service_zips"]))
            - 0.025 * (int(workload[str(row["clinician_id"])]) / max(int(row["capacity"]), 1)),
            axis=1,
        )
        chosen = selected_feasible.assign(_objective=scores).sort_values(
            ["_objective", "clinician_id"], ascending=[False, True], kind="stable"
        ).iloc[0]
    elif policy == "P7":
        scores = selected_feasible.apply(
            lambda row: expected_favorable_outcome(
                float(patient["risk_percentile_u"]),
                float(row["true_quality"]),
                patient_zip in set(row["service_zips"]),
                travel_proxy(patient_zip, str(row["base_zip"])),
                scenario,
            ),
            axis=1,
        )
        chosen = selected_feasible.assign(_oracle=scores).sort_values(
            ["_oracle", "clinician_id"], ascending=[False, True], kind="stable"
        ).iloc[0]
    else:  # guarded by config validation and public API validation
        raise ValueError(f"unknown policy {policy!r}")

    return (
        chosen,
        route,
        high_risk,
        len(full_available),
        len(zip_available),
        len(selected_available),
        fallback_reason,
    )


def simulate_policy(
    patients: pd.DataFrame,
    clinicians: pd.DataFrame,
    scenario: Scenario | str,
    policy: str,
    threshold: float,
    *,
    seed: int,
    min_zip_pool: Mapping[str, int] | None = None,
    outcome_noise_sd: float = 0.035,
    oracle_upper_bound_utility: float | None = None,
    design_fingerprint: str | None = None,
) -> tuple[dict, pd.DataFrame]:
    """Run one complete capacity-constrained policy simulation.

    The input frames are never mutated.  A fresh workload map is initialized on
    every call; this is the mechanism that makes every threshold candidate a true
    policy rerun instead of a post-hoc relabeling of fixed assignments.
    """

    if policy not in POLICY_NAMES:
        raise ValueError(f"unknown policy {policy!r}")
    threshold = float(threshold)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be in [0, 1]")
    scenario_obj = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    minima = dict(MIN_ZIP_POOL if min_zip_pool is None else min_zip_pool)
    if design_fingerprint is None:
        design_fingerprint = _simulation_design_fingerprint(
            patients,
            clinicians,
            minima,
            outcome_noise_sd,
        )
    roster = clinicians.copy(deep=True).reset_index(drop=True)
    if oracle_upper_bound_utility is None:
        oracle_upper_bound_utility = scenario_oracle_upper_bound_utility(
            patients, roster, scenario_obj
        )
    workload = {
        str(row.clinician_id): int(row.current_workload)
        for row in roster.itertuples(index=False)
    }
    initial_workload = dict(workload)
    round_robin_cursor = {discipline: 0 for discipline in DISCIPLINES}
    top_tier_cutoff = {
        discipline: float(group["true_quality"].quantile(0.75))
        for discipline, group in roster.groupby("discipline")
    }

    # P6 prioritizes high modeled need.  P7 is a feasible latent-information
    # greedy comparator (not the analytical scenario upper bound): it prioritizes
    # the largest attainable favorable outcome when capacity is scarce, then
    # selects using latent (not observed) clinician quality.  Other policies
    # preserve sequential arrival order.  Ties use stable patient IDs.
    if policy == "P6":
        ordered_patients = patients.sort_values(
            ["risk_percentile_u", "patient_id"], ascending=[False, True], kind="stable"
        )
    elif policy == "P7":
        available_roster = roster[roster["available"]]

        def oracle_priority(patient: pd.Series) -> float:
            best_by_discipline: list[float] = []
            for discipline in sorted(set(available_roster["discipline"])):
                pool = available_roster[available_roster["discipline"] == discipline]
                values = [
                    expected_favorable_outcome(
                        float(patient["risk_percentile_u"]),
                        float(clinician["true_quality"]),
                        str(patient["patient_zip"]) in set(clinician["service_zips"]),
                        travel_proxy(str(patient["patient_zip"]), str(clinician["base_zip"])),
                        scenario_obj,
                    )
                    for _, clinician in pool.iterrows()
                ]
                if values:
                    best_by_discipline.append(max(values))
            return float(np.mean(best_by_discipline)) if best_by_discipline else 0.0

        ordered_patients = patients.copy()
        ordered_patients["_oracle_priority"] = ordered_patients.apply(oracle_priority, axis=1)
        ordered_patients = ordered_patients.sort_values(
            ["_oracle_priority", "patient_id"], ascending=[False, True], kind="stable"
        )
    else:
        ordered_patients = patients.sort_values("patient_id", kind="stable")

    assignment_rows: list[dict] = []
    for patient_tuple in ordered_patients.itertuples(index=False):
        patient = pd.Series(patient_tuple._asdict())
        for discipline in sorted(set(roster["discipline"])):
            chosen, route, high_risk, full_size, zip_size, selected_size, fallback_reason = _choose_clinician(
                policy,
                patient,
                discipline,
                roster,
                workload,
                threshold,
                scenario_obj,
                minima,
                round_robin_cursor,
            )
            common = {
                "patient_id": str(patient["patient_id"]),
                "discipline": discipline,
                "patient_zip": str(patient["patient_zip"]),
                "age_group": str(patient["age_group"]),
                "area_type": str(patient["area_type"]),
                "house_price_zip": str(patient["house_price_zip"]),
                "housing_type": str(patient["housing_type"]),
                "clinical_need_decile": int(patient["clinical_need_decile"]),
                "risk_score_rho_raw": float(patient["risk_score_rho_raw"]),
                "risk_percentile_u": float(patient["risk_percentile_u"]),
                "threshold_percentile": threshold,
                "high_risk": bool(high_risk),
                "route": route,
                "full_pool_size": int(full_size),
                "zip_pool_size": int(zip_size),
                "selected_pool_size": int(selected_size),
                "fallback_reason": fallback_reason,
                "policy": policy,
                "scenario": scenario_obj.key,
                "seed": int(seed),
            }
            if chosen is None:
                expected = 0.03
                row = {
                    **common,
                    "clinician_id": None,
                    "assigned": False,
                    "selected_true_quality": np.nan,
                    "selected_observed_quality": np.nan,
                    "zip_continuity": False,
                    "travel_proxy": np.nan,
                    "top_tier_clinician": False,
                    "expected_outcome": expected,
                }
            else:
                clinician_id = str(chosen["clinician_id"])
                workload[clinician_id] += 1
                continuity = str(patient["patient_zip"]) in set(chosen["service_zips"])
                miles = travel_proxy(str(patient["patient_zip"]), str(chosen["base_zip"]))
                expected = expected_favorable_outcome(
                    float(patient["risk_percentile_u"]),
                    float(chosen["true_quality"]),
                    continuity,
                    miles,
                    scenario_obj,
                )
                row = {
                    **common,
                    "clinician_id": clinician_id,
                    "assigned": True,
                    "selected_true_quality": float(chosen["true_quality"]),
                    "selected_observed_quality": float(chosen["observed_quality"]),
                    "zip_continuity": bool(continuity),
                    "travel_proxy": miles,
                    "top_tier_clinician": bool(float(chosen["true_quality"]) >= top_tier_cutoff[discipline]),
                    "expected_outcome": expected,
                }
            favorable_values: list[float] = []
            for metric_index, metric in enumerate(METRICS):
                offset = (metric_index - (len(METRICS) - 1) / 2.0) * 0.002
                favorable = float(
                    np.clip(
                        expected + offset + outcome_noise_sd * float(patient[f"noise_{metric}"]),
                        0.0,
                        1.0,
                    )
                )
                favorable_values.append(favorable)
                row[f"favorable_{metric}"] = favorable
                row[f"outcome_{metric}"] = 1.0 - favorable if metric in LOWER_IS_BETTER else favorable
            row["simulated_outcome_utility"] = float(np.mean(favorable_values))
            assignment_rows.append(row)

    assignments = pd.DataFrame(assignment_rows)
    assigned = assignments[assignments["assigned"]]
    assignment_counts = {
        clinician_id: int(workload[clinician_id] - initial_workload[clinician_id])
        for clinician_id in workload
    }
    count_values = np.asarray(list(assignment_counts.values()), dtype=float)
    final_workload_values = np.asarray([workload[clinician_id] for clinician_id in workload], dtype=float)
    utilization_values: list[float] = []
    overload_values: list[float] = []
    for clinician in roster.itertuples(index=False):
        final = workload[str(clinician.clinician_id)]
        capacity = max(int(clinician.capacity), 1)
        utilization_values.append(final / capacity)
        overload_values.append(max(0.0, final - capacity) / capacity)

    performance = {
        metric: float(assignments[f"outcome_{metric}"].mean())
        for metric in METRICS
    }
    favorable_means = {
        metric: float(assignments[f"favorable_{metric}"].mean())
        for metric in METRICS
    }
    tps_proxy = float(100.0 * sum(CY2025_WEIGHTS[m] * favorable_means[m] for m in METRICS))
    lower_risk = assignments[~assignments["high_risk"]]
    fallback_mask = lower_risk["route"].astype(str).str.contains("fallback", regex=False)
    rerun_payload = (
        f"{design_fingerprint}|{scenario_obj.key}|{seed}|{policy}|"
        f"{threshold:.8f}|{len(patients)}"
    )
    assignment_payload = "|".join(
        assignments["patient_id"].astype(str)
        + ":"
        + assignments["discipline"].astype(str)
        + ":"
        + assignments["clinician_id"].fillna("UNASSIGNED").astype(str)
        + ":"
        + assignments["route"].astype(str)
    )
    reference = _canonical_risk_assets()[0]
    reference_metadata = reference["metadata"]
    summary = {
        "scenario": scenario_obj.key,
        "scenario_description": scenario_obj.description,
        "seed": int(seed),
        "policy": policy,
        "policy_name": POLICY_NAMES[policy],
        "threshold_percentile": threshold,
        "n_patients": int(len(patients)),
        "n_service_needs": int(len(assignments)),
        "assigned_count": int(assignments["assigned"].sum()),
        "unassigned_rate": float(1.0 - assignments["assigned"].mean()),
        "tps_proxy": tps_proxy,
        "simulated_outcome_utility": float(assignments["simulated_outcome_utility"].mean()),
        "expected_outcome_utility": float(assignments["expected_outcome"].mean()),
        "scenario_oracle_utility": float(oracle_upper_bound_utility),
        "high_risk_full_pool_share": float(assignments["high_risk"].mean()),
        "zip_fallback_rate": float(fallback_mask.mean()) if not lower_risk.empty else 0.0,
        "zip_continuity_rate": float(assigned["zip_continuity"].mean()) if not assigned.empty else 0.0,
        "workload_spread": float(final_workload_values.std(ddof=0) / final_workload_values.mean())
        if final_workload_values.size and final_workload_values.mean() > 0
        else 0.0,
        "workload_gini": _gini(final_workload_values),
        "new_assignment_workload_gini": _gini(count_values),
        "max_overload": float(max(overload_values, default=0.0)),
        "mean_utilization": float(np.mean(utilization_values)) if utilization_values else 0.0,
        "travel_proxy": float(assigned["travel_proxy"].mean()) if not assigned.empty else np.nan,
        "top_tier_access_rate": float(assigned["top_tier_clinician"].mean()) if not assigned.empty else 0.0,
        "fairness_gap_age_group": _group_gap(assignments, "age_group"),
        "fairness_gap_zip": _group_gap(assignments, "patient_zip"),
        "fairness_gap_area_type": _group_gap(assignments, "area_type"),
        "fairness_gap_housing_type": _group_gap(assignments, "housing_type"),
        "fairness_gap_house_price_group": _group_gap(assignments, "house_price_zip"),
        "fairness_gap_clinical_need_decile": _group_gap(assignments, "clinical_need_decile"),
        "assignment_signature": hashlib.sha256(assignment_payload.encode("utf-8")).hexdigest(),
        "threshold_rerun_id": hashlib.sha256(rerun_payload.encode("utf-8")).hexdigest()[:16],
        "rerun_design_fingerprint": design_fingerprint,
        "common_random_numbers": True,
        "capacity_constraints_enforced": True,
        "availability_constraints_enforced": True,
        "discipline_constraints_enforced": True,
        "ot_deterministic_rotation_preserved": bool(policy == "P4"),
        "cms_model": CMS_MODEL,
        "performance_year": PERFORMANCE_YEAR,
        "payment_year": PAYMENT_YEAR,
        "measure_set_version": MEASURE_SET_VERSION,
        "cms_version_locked": True,
        "threshold_source": PROVENANCE_LABEL,
        "risk_reference_id": reference_metadata["reference_id"],
        "risk_reference_config_hash": reference_metadata["config_hash"],
        "risk_reference_sample_size": int(reference_metadata["sample_size"]),
        "threshold_raw_equivalent": float(
            quantile_raw(reference["sorted_risk_scores"], threshold)
        ),
        "risk_percentile_semantics": "persisted_reference_ecdf",
        "public_data_only": True,
        "synthetic_data": True,
        "production_validated": False,
    }
    summary.update({f"metric_performance_{metric}": value for metric, value in performance.items()})
    return summary, assignments.sort_values(["patient_id", "discipline"], kind="stable").reset_index(drop=True)


def _attach_oracle_regret(results: pd.DataFrame) -> pd.DataFrame:
    keys = ["scenario", "seed", "threshold_percentile"]
    best_included = results.groupby(keys, as_index=False)["expected_outcome_utility"].max().rename(
        columns={"expected_outcome_utility": "best_included_policy_utility"}
    )
    out = results.merge(best_included, on=keys, how="left", validate="many_to_one")
    out["regret_vs_best_included_policy"] = np.maximum(
        0.0,
        out["best_included_policy_utility"] - out["expected_outcome_utility"],
    )
    if "scenario_oracle_utility" not in out:
        raise ValueError("results are missing the analytical scenario oracle upper bound")
    excess = out["expected_outcome_utility"] - out["scenario_oracle_utility"]
    if float(excess.max()) > 1e-10:
        raise AssertionError(
            "a feasible policy exceeded the unconstrained scenario oracle upper bound"
        )
    out["regret_vs_scenario_oracle"] = np.clip(
        out["scenario_oracle_utility"] - out["expected_outcome_utility"],
        0.0,
        None,
    )
    out["oracle_regret_available"] = True
    out["regret_reference"] = "unconstrained_latent_information_upper_bound"
    return out


def run_policy_threshold_experiment(
    config: BatchExperimentConfig,
    *,
    scenarios: Sequence[str] = tuple(SCENARIOS),
    retain_assignments: bool = False,
    progress_callback=None,
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Execute all requested seeds, scenarios, candidates, and policies.

    ``generate_simulation_inputs`` is called once per scenario/seed.  Every call
    to ``simulate_policy`` then starts from that immutable raw bundle and a fresh
    workload map, satisfying the full-rerun requirement while retaining common
    random numbers.
    """

    config.validate()
    unknown = set(scenarios).difference(SCENARIOS)
    if unknown:
        raise ValueError(f"unknown scenarios: {sorted(unknown)}")
    result_rows: list[dict] = []
    assignment_frames: list[pd.DataFrame] = []
    total = len(scenarios) * len(config.seeds) * len(config.thresholds) * len(config.policies)
    completed = 0
    config_hash = canonical_config_hash(config)
    for scenario_key in scenarios:
        scenario = SCENARIOS[scenario_key]
        for seed in config.seeds:
            patients, clinicians = generate_simulation_inputs(config, int(seed), scenario)
            oracle_upper_bound = scenario_oracle_upper_bound_utility(
                patients, clinicians, scenario
            )
            design_fingerprint = _simulation_design_fingerprint(
                patients,
                clinicians,
                config.min_zip_pool,
                config.outcome_noise_sd,
                config_hash=config_hash,
            )
            for threshold in config.thresholds:
                for policy in config.policies:
                    summary, assignments = simulate_policy(
                        patients,
                        clinicians,
                        scenario,
                        policy,
                        float(threshold),
                        seed=int(seed),
                        min_zip_pool=config.min_zip_pool,
                        outcome_noise_sd=config.outcome_noise_sd,
                        oracle_upper_bound_utility=oracle_upper_bound,
                        design_fingerprint=design_fingerprint,
                    )
                    summary.update(
                        {
                            "config_hash": config_hash,
                            "design_label": config.design_label,
                            "capacity_ratio": config.capacity_ratio,
                            "zip_sparsity": config.zip_sparsity,
                            "roster_RN": int(config.roster_sizes.get("RN", 0)),
                            "roster_PT": int(config.roster_sizes.get("PT", 0)),
                            "roster_OT": int(config.roster_sizes.get("OT", 0)),
                        }
                    )
                    # A rerun identifier is an audit key, so it must also
                    # distinguish operational designs (roster/capacity/ZIP
                    # settings), not just scenario/seed/policy/threshold.
                    summary["threshold_rerun_id"] = hashlib.sha256(
                        f"{config_hash}|{summary['threshold_rerun_id']}".encode("utf-8")
                    ).hexdigest()[:16]
                    result_rows.append(summary)
                    if retain_assignments:
                        assignments = assignments.copy()
                        assignments["config_hash"] = config_hash
                        assignment_frames.append(assignments)
                    completed += 1
                    if progress_callback is not None:
                        progress_callback(completed, total, summary)
    results = _attach_oracle_regret(pd.DataFrame(result_rows))
    details = pd.concat(assignment_frames, ignore_index=True) if assignment_frames else None
    return results, details


def select_simulation_thresholds(
    results: pd.DataFrame,
    *,
    policy: str = "P4",
) -> tuple[dict, pd.DataFrame]:
    """Select thresholds on deterministic training seeds and audit held-out seeds."""

    required = {
        "scenario",
        "policy",
        "threshold_percentile",
        "simulated_outcome_utility",
        "workload_gini",
        "unassigned_rate",
        "travel_proxy",
    }
    missing = required.difference(results.columns)
    if missing:
        raise ValueError(f"results missing required columns: {sorted(missing)}")
    subset = results[results["policy"] == policy].copy()
    if subset.empty:
        raise ValueError(f"results contain no rows for policy {policy}")
    subset["simulation_objective"] = (
        subset["simulated_outcome_utility"]
        - 0.04 * subset["workload_gini"]
        - 0.40 * subset["unassigned_rate"]
        - 0.001 * subset["travel_proxy"].fillna(0.0)
    )
    metric_columns = (
        "simulation_objective",
        "tps_proxy",
        "simulated_outcome_utility",
        "zip_fallback_rate",
        "zip_continuity_rate",
        "workload_gini",
        "unassigned_rate",
        "travel_proxy",
    )

    def aggregate_rows(frame: pd.DataFrame) -> pd.DataFrame:
        return (
            frame.groupby(["scenario", "threshold_percentile"], as_index=False)
            .agg(**{column: (column, "mean") for column in metric_columns})
            .sort_values(["scenario", "threshold_percentile"], kind="stable")
        )

    aggregate = aggregate_rows(subset)
    aggregate["scenario_best_objective"] = aggregate.groupby("scenario")["simulation_objective"].transform("max")
    aggregate["scenario_threshold_regret"] = aggregate["scenario_best_objective"] - aggregate["simulation_objective"]

    thresholds = sorted(float(value) for value in subset["threshold_percentile"].unique())
    seeds = sorted(int(value) for value in subset["seed"].unique()) if "seed" in subset else []
    optimization_evaluated = len(thresholds) >= 2
    held_out_evaluation = optimization_evaluated and len(seeds) >= 2
    if held_out_evaluation:
        split_index = max(1, len(seeds) // 2)
        selection_seeds = seeds[:split_index]
        evaluation_seeds = seeds[split_index:]
    else:
        selection_seeds = seeds
        evaluation_seeds = []

    selection_subset = subset[subset["seed"].isin(selection_seeds)] if seeds else subset
    selection = aggregate_rows(selection_subset)
    selection["selection_scenario_best_objective"] = selection.groupby("scenario")[
        "simulation_objective"
    ].transform("max")
    selection["selection_scenario_threshold_regret"] = (
        selection["selection_scenario_best_objective"] - selection["simulation_objective"]
    )
    selection = selection.rename(
        columns={column: f"selection_{column}" for column in metric_columns}
    )
    aggregate = aggregate.merge(
        selection,
        on=["scenario", "threshold_percentile"],
        how="left",
        validate="one_to_one",
    )

    if evaluation_seeds:
        evaluation = aggregate_rows(subset[subset["seed"].isin(evaluation_seeds)])
        evaluation["held_out_scenario_best_objective"] = evaluation.groupby("scenario")[
            "simulation_objective"
        ].transform("max")
        evaluation["held_out_scenario_threshold_regret"] = (
            evaluation["held_out_scenario_best_objective"] - evaluation["simulation_objective"]
        )
        evaluation = evaluation.rename(
            columns={column: f"held_out_{column}" for column in metric_columns}
        )
        aggregate = aggregate.merge(
            evaluation,
            on=["scenario", "threshold_percentile"],
            how="left",
            validate="one_to_one",
        )
    else:
        aggregate["held_out_simulation_objective"] = np.nan
        aggregate["held_out_scenario_best_objective"] = np.nan
        aggregate["held_out_scenario_threshold_regret"] = np.nan

    if not optimization_evaluated:
        aggregate["threshold_evaluation_role"] = "fixed_threshold_only_no_optimization"
        recommendation = {
            "policy": policy,
            "threshold_optimization_evaluated": False,
            "evaluated_thresholds": thresholds,
            "scenario_specific_best_thresholds": {},
            "robust_minimax_regret_threshold": None,
            "robust_max_regret": None,
            "selection_seeds": selection_seeds,
            "held_out_evaluation_seeds": evaluation_seeds,
            "held_out_evaluation_performed": False,
            "production_demo_threshold": PRODUCTION_PILOT_THRESHOLD,
            "production_threshold_changed": False,
            "interpretation": "Fixed-threshold evaluation only; threshold optimization was not evaluated.",
            "threshold_source": PROVENANCE_LABEL,
        }
        return recommendation, aggregate

    if not evaluation_seeds:
        aggregate["threshold_evaluation_role"] = "in_sample_sensitivity_only_no_recommendation"
        recommendation = {
            "policy": policy,
            "threshold_optimization_evaluated": False,
            "in_sample_sensitivity_evaluated": True,
            "evaluated_thresholds": thresholds,
            "scenario_specific_best_thresholds": {},
            "robust_minimax_regret_threshold": None,
            "robust_max_regret": None,
            "selection_seeds": selection_seeds,
            "held_out_evaluation_seeds": [],
            "held_out_evaluation_performed": False,
            "production_demo_threshold": PRODUCTION_PILOT_THRESHOLD,
            "production_threshold_changed": False,
            "interpretation": "In-sample threshold sensitivity only; no threshold recommendation is emitted without held-out seeds.",
            "threshold_source": PROVENANCE_LABEL,
        }
        return recommendation, aggregate

    aggregate["threshold_evaluation_role"] = "candidate_threshold_optimization"
    best_rows = (
        aggregate.sort_values(
            ["scenario", "selection_simulation_objective", "threshold_percentile"],
            ascending=[True, False, True],
            kind="stable",
        )
        .groupby("scenario", as_index=False)
        .first()
    )
    minimax = (
        aggregate.groupby("threshold_percentile", as_index=False)["selection_scenario_threshold_regret"]
        .max()
        .sort_values(["selection_scenario_threshold_regret", "threshold_percentile"], kind="stable")
        .iloc[0]
    )
    robust_threshold = float(minimax["threshold_percentile"])
    scenario_thresholds = {
        str(row.scenario): float(row.threshold_percentile)
        for row in best_rows.itertuples(index=False)
    }
    held_out_scenario_regret: dict[str, float] = {}
    held_out_robust_max_regret: float | None = None
    if evaluation_seeds:
        for scenario, selected_threshold in scenario_thresholds.items():
            match = aggregate[
                (aggregate["scenario"].astype(str) == scenario)
                & np.isclose(aggregate["threshold_percentile"].astype(float), selected_threshold)
            ]
            held_out_scenario_regret[scenario] = float(
                match["held_out_scenario_threshold_regret"].iloc[0]
            )
        robust_rows = aggregate[np.isclose(aggregate["threshold_percentile"], robust_threshold)]
        held_out_robust_max_regret = float(
            robust_rows["held_out_scenario_threshold_regret"].max()
        )
    recommendation = {
        "policy": policy,
        "threshold_optimization_evaluated": True,
        "evaluated_thresholds": thresholds,
        "scenario_specific_best_thresholds": scenario_thresholds,
        "robust_minimax_regret_threshold": robust_threshold,
        "robust_max_regret": float(minimax["selection_scenario_threshold_regret"]),
        "selection_seeds": selection_seeds,
        "held_out_evaluation_seeds": evaluation_seeds,
        "held_out_evaluation_performed": bool(evaluation_seeds),
        "held_out_scenario_regret_at_selected_thresholds": held_out_scenario_regret,
        "held_out_robust_max_regret": held_out_robust_max_regret,
        "production_demo_threshold": PRODUCTION_PILOT_THRESHOLD,
        "production_threshold_changed": False,
        "interpretation": "Simulation-optimal only under explicit semi-synthetic assumptions; not outcome-validated.",
        "threshold_source": PROVENANCE_LABEL,
    }
    return recommendation, aggregate


def assert_nonconstant_threshold_sensitivity(
    results: pd.DataFrame,
    *,
    policies: Sequence[str] = ("P4", "P5", "P6"),
) -> None:
    """Fail a non-degenerate calibration whose threshold never changes results."""

    subset = results[results["policy"].isin(policies)]
    if subset["threshold_percentile"].nunique() < 2:
        raise AssertionError("at least two threshold candidates are required")
    changing = False
    group_columns = [
        column
        for column in ["design_label", "config_hash", "scenario", "seed", "policy"]
        if column in subset.columns
    ]
    for _, group in subset.groupby(group_columns):
        signatures = group[
            ["zip_fallback_rate", "zip_continuity_rate", "simulated_outcome_utility", "tps_proxy"]
        ].round(12)
        if len(signatures.drop_duplicates()) > 1:
            changing = True
            break
    if not changing:
        raise AssertionError(
            "threshold sensitivity is constant even though routing/capacity policies were evaluated"
        )


def run_threshold_sweep(
    patients_or_count: int | pd.DataFrame,
    roster: Mapping[str, int] | pd.DataFrame | None = None,
    thresholds: Iterable[float] | None = None,
    *,
    scenario: Scenario | str = "A",
    seeds: Sequence[int] = (0,),
    policy: str = "P4",
    config: BatchExperimentConfig | None = None,
) -> pd.DataFrame:
    """Convenience API for a fully re-executed threshold sensitivity sweep.

    ``patients_or_count`` may be a positive patient count or a patient frame
    produced by :func:`generate_patient_cohort`.  A supplied patient frame must be
    paired with a clinician frame; generated runs may instead receive a roster-size
    mapping.  The returned assignment signature makes it straightforward for
    callers and regression tests to prove that routing changes reached assignment.
    """

    if policy not in POLICY_NAMES:
        raise ValueError(f"unknown policy {policy!r}")
    scenario_obj = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    candidates = tuple(_default_thresholds() if thresholds is None else (float(value) for value in thresholds))
    if not candidates:
        raise ValueError("at least one threshold candidate is required")
    seed_values = tuple(int(value) for value in seeds)
    if not seed_values:
        raise ValueError("at least one seed is required")

    base = config or BatchExperimentConfig(
        n_patients=int(patients_or_count) if isinstance(patients_or_count, (int, np.integer)) else len(patients_or_count),
        seeds=seed_values,
        thresholds=candidates,
        policies=(policy,),
        design_label="threshold_sweep_api",
    )
    if isinstance(roster, Mapping):
        payload = asdict(base)
        payload["roster_sizes"] = dict(roster)
        payload["seeds"] = seed_values
        payload["thresholds"] = candidates
        payload["policies"] = (policy,)
        base = BatchExperimentConfig(**payload)

    rows: list[dict] = []
    generated = isinstance(patients_or_count, (int, np.integer))
    if generated and int(patients_or_count) <= 0:
        raise ValueError("patients_or_count must be positive")
    if not generated and not isinstance(roster, pd.DataFrame):
        raise ValueError("a clinician DataFrame roster is required with a patient DataFrame")
    for seed in seed_values:
        if generated:
            payload = asdict(base)
            payload.update(
                {
                    "n_patients": int(patients_or_count),
                    "seeds": seed_values,
                    "thresholds": candidates,
                    "policies": (policy,),
                }
            )
            local_config = BatchExperimentConfig(**payload)
            patient_frame, clinician_frame = generate_simulation_inputs(local_config, seed, scenario_obj)
            minima = local_config.min_zip_pool
            noise_sd = local_config.outcome_noise_sd
            active_config = local_config
        else:
            patient_frame = patients_or_count.copy(deep=True)
            clinician_frame = roster.copy(deep=True)  # type: ignore[union-attr]
            minima = base.min_zip_pool
            noise_sd = base.outcome_noise_sd
            active_config = base
        oracle_upper_bound = scenario_oracle_upper_bound_utility(
            patient_frame, clinician_frame, scenario_obj
        )
        design_fingerprint = _simulation_design_fingerprint(
            patient_frame,
            clinician_frame,
            minima,
            noise_sd,
            config_hash=canonical_config_hash(active_config),
        )
        for threshold in candidates:
            summary, _ = simulate_policy(
                patient_frame,
                clinician_frame,
                scenario_obj,
                policy,
                threshold,
                seed=seed,
                min_zip_pool=minima,
                outcome_noise_sd=noise_sd,
                oracle_upper_bound_utility=oracle_upper_bound,
                design_fingerprint=design_fingerprint,
            )
            rows.append(summary)
    return _attach_oracle_regret(pd.DataFrame(rows))


DEFAULT_RELIABILITY = {
    "health_status": 0.95,
    "age_group": 0.95,
    "house_price_zip": 0.85,
    "area_type": 0.90,
    "housing_type": 0.80,
    "distance_to_clinic": 0.95,
    "driving_condition": 0.90,
}


def _profile_risk(profile: Mapping[str, str], included_factors: Iterable[str] | None = None) -> float:
    factors = tuple(FACTOR_LEVELS if included_factors is None else included_factors)
    unknown = set(factors).difference(FACTOR_LEVELS)
    if unknown:
        raise ValueError(f"unknown included factors: {sorted(unknown)}")
    contributions = _canonical_factor_contributions()
    denominator = sum(max(contributions[factor].values()) for factor in factors)
    if denominator <= 0:
        raise ValueError("included factors must have positive total weight")
    return float(
        sum(
            contributions[factor][str(profile[factor])]
            for factor in factors
        )
        / denominator
    )


def generate_full_factorial_profiles(
    *,
    threshold: float = PRODUCTION_PILOT_THRESHOLD,
    representative_pool_sizes: Sequence[int] = (3, 5, 10),
) -> pd.DataFrame:
    """Generate all 2,160 seven-factor profiles for structural analysis."""

    factor_names = tuple(FACTOR_LEVELS)
    reference, canonical = _canonical_risk_assets()
    frame = canonical[["profile_id", *factor_names]].copy()
    frame["risk_score_rho_raw"] = canonical["rho_raw"].to_numpy(dtype=float)
    frame["risk_percentile_u"] = canonical["risk_percentile_u"].to_numpy(dtype=float)
    recomputed_percentiles = _reference_percentiles(frame["risk_score_rho_raw"])
    if not np.allclose(
        recomputed_percentiles,
        frame["risk_percentile_u"].to_numpy(dtype=float),
        rtol=0.0,
        atol=1e-12,
    ):
        raise ValueError("canonical structural percentiles do not match the persisted ECDF")

    contributions = _canonical_factor_contributions()
    contribution_weights = {
        factor: max(contributions[factor].values()) for factor in factor_names
    }
    weighted_reliability = sum(
        contribution_weights[factor] * DEFAULT_RELIABILITY[factor]
        for factor in factor_names
    ) / sum(contribution_weights.values())
    for factor in factor_names:
        frame[f"contribution_{factor}"] = frame[factor].map(contributions[factor]).astype(float)
    for metric_index, metric in enumerate(METRICS):
        frame[f"metric_need_mean_{metric}"] = canonical[f"need_mean_{metric}"].to_numpy(dtype=float)
        frame[f"metric_need_sd_{metric}"] = float(
            0.025 + (1.0 - weighted_reliability) * (0.10 + 0.003 * metric_index)
        )
    if len(frame) != 2_160:
        raise AssertionError(f"full factorial must contain 2,160 rows, got {len(frame)}")
    frame["threshold_route"] = np.where(
        frame["risk_percentile_u"] > float(threshold),
        "global_high_risk",
        "zip_history_or_fallback_lower_risk",
    )
    for pool_size in representative_pool_sizes:
        if int(pool_size) <= 0:
            raise ValueError("representative pool sizes must be positive")
        frame[f"anchor_rank_pool_{int(pool_size)}"] = frame["risk_percentile_u"].map(
            lambda value: mirrored_anchor_index(float(value), int(pool_size)) + 1
        )
    frame["public_data_only"] = True
    frame["synthetic_data"] = True
    frame["production_validated"] = False
    frame["provenance"] = PROVENANCE_LABEL
    frame["risk_reference_id"] = reference["metadata"]["reference_id"]
    frame["risk_reference_sample_size"] = int(reference["metadata"]["sample_size"])
    frame["risk_percentile_semantics"] = "persisted_reference_ecdf"
    frame["metric_need_uncertainty_method"] = "configured_reliability_proxy_not_empirical"
    return frame


def structural_diagnostics(profiles: pd.DataFrame) -> dict:
    """Summarize score compression and representative-rank reachability."""

    anchor_columns = [column for column in profiles if column.startswith("anchor_rank_pool_")]
    anchors: dict[str, dict] = {}
    for column in anchor_columns:
        pool_size = int(column.rsplit("_", 1)[-1])
        values = profiles[column].astype(int)
        anchors[column] = {
            "reachable_ranks": sorted(int(value) for value in values.unique()),
            "unreachable_ranks": sorted(set(range(1, pool_size + 1)).difference(values.unique())),
            "top_1_profiles": int((values == 1).sum()),
            "top_3_profiles": int((values <= min(3, pool_size)).sum()),
            "middle_profiles": int(((values > max(1, pool_size // 3)) & (values <= max(1, 2 * pool_size // 3))).sum()),
            "bottom_profiles": int((values == pool_size).sum()),
        }
    # The additive score is monotone by construction.  Verify it directly across
    # adjacent levels while holding every other factor fixed.
    monotonicity_violations = 0
    for factor, levels in FACTOR_LEVELS.items():
        other = [name for name in FACTOR_LEVELS if name != factor]
        for _, group in profiles.groupby(other, sort=False):
            ordered = group.set_index(factor).loc[list(levels), "risk_score_rho_raw"].to_numpy()
            monotonicity_violations += int(np.sum(np.diff(ordered) < -1e-12))
    return {
        "profile_count": int(len(profiles)),
        "risk_raw_min": float(profiles["risk_score_rho_raw"].min()),
        "risk_raw_max": float(profiles["risk_score_rho_raw"].max()),
        "risk_raw_range": float(profiles["risk_score_rho_raw"].max() - profiles["risk_score_rho_raw"].min()),
        "risk_unique_values": int(profiles["risk_score_rho_raw"].nunique()),
        "monotonicity_violations": int(monotonicity_violations),
        "age_health_joint_weight": float(
            max(_canonical_factor_contributions()["age_group"].values())
            + max(_canonical_factor_contributions()["health_status"].values())
        ),
        "age_health_double_counting_interaction": False,
        "age_health_note": "Age and health are separate additive configured signals; causal overlap cannot be identified from synthetic data.",
        "anchors": anchors,
        "provenance": PROVENANCE_LABEL,
    }


ABLATION_VARIANTS: dict[str, tuple[str, ...]] = {
    "full_seven_factor": tuple(FACTOR_WEIGHTS),
    "no_age": tuple(factor for factor in FACTOR_WEIGHTS if factor != "age_group"),
    "no_house_price": tuple(factor for factor in FACTOR_WEIGHTS if factor != "house_price_zip"),
    "no_area_type": tuple(factor for factor in FACTOR_WEIGHTS if factor != "area_type"),
    "no_housing_type": tuple(factor for factor in FACTOR_WEIGHTS if factor != "housing_type"),
    "clinical_only": ("health_status", "age_group"),
    "environment_only": (
        "house_price_zip",
        "area_type",
        "housing_type",
        "distance_to_clinic",
        "driving_condition",
    ),
}


def _structural_patient_zip(row: pd.Series) -> str:
    candidates = [
        zip_code
        for zip_code in ZIP_CODES
        if ZIP_AREA[zip_code] == str(row["area_type"])
        and ZIP_PRICE[zip_code] == str(row["house_price_zip"])
    ]
    if not candidates:
        candidates = [
            zip_code for zip_code in ZIP_CODES
            if ZIP_AREA[zip_code] == str(row["area_type"])
        ]
    if not candidates:
        candidates = list(ZIP_CODES)
    digest = hashlib.sha256(str(row["profile_id"]).encode("utf-8")).digest()
    return candidates[int.from_bytes(digest[:4], "big") % len(candidates)]


def _structural_policy_patients(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    out = frame[list(FACTOR_LEVELS) + ["risk_score_rho_raw", "risk_percentile_u"]].copy()
    out.insert(0, "patient_id", [f"{prefix}-{index:04d}" for index in range(len(out))])
    source_rows = frame.reset_index(drop=True).copy()
    if "profile_id" not in source_rows:
        source_rows["profile_id"] = out["patient_id"]
    out["patient_zip"] = source_rows.apply(_structural_patient_zip, axis=1)
    out["clinical_need_decile"] = np.minimum(
        9, np.floor(out["risk_percentile_u"].to_numpy(dtype=float) * 10)
    ).astype(int) + 1
    for metric in METRICS:
        out[f"noise_{metric}"] = 0.0
    out["public_data_only"] = True
    out["synthetic_data"] = True
    out["production_validated"] = False
    return out


def _representative_structural_roster(n_patients: int) -> pd.DataFrame:
    config = BatchExperimentConfig(
        n_patients=int(n_patients),
        seeds=(7001,),
        roster_sizes={"RN": 10, "PT": 8, "OT": 4},
        capacity_ratio=5.0,
        zip_sparsity=0.35,
        availability_rate=1.0,
        thresholds=(PRODUCTION_PILOT_THRESHOLD,),
        policies=("P4",),
        design_label="structural_fixed_roster_current_policy",
    )
    return generate_clinician_roster(config, 7001, SCENARIOS["F"])


def _selected_rank_map(roster: pd.DataFrame) -> dict[str, int]:
    ranked = roster.sort_values(
        ["discipline", "observed_quality", "clinician_id"],
        ascending=[True, False, True],
        kind="stable",
    ).copy()
    ranked["selected_full_pool_rank"] = ranked.groupby("discipline").cumcount() + 1
    return {
        str(row.clinician_id): int(row.selected_full_pool_rank)
        for row in ranked.itertuples(index=False)
    }


def factor_ablation_experiment(
    profiles: pd.DataFrame | None = None,
    *,
    threshold: float = PRODUCTION_PILOT_THRESHOLD,
    representative_pool_size: int = 10,
) -> pd.DataFrame:
    """Compare configured factor subsets on the complete structural grid."""

    frame = generate_full_factorial_profiles(threshold=threshold) if profiles is None else profiles.copy()
    full_raw = frame["risk_score_rho_raw"].to_numpy(dtype=float)
    full_high = frame["risk_percentile_u"].to_numpy(dtype=float) > threshold
    full_anchor = np.array(
        [mirrored_anchor_index(value, representative_pool_size) + 1 for value in frame["risk_percentile_u"]]
    )
    roster = _representative_structural_roster(len(frame))
    clinician_ranks = _selected_rank_map(roster)
    baseline_assignments: pd.DataFrame | None = None
    baseline_workload_gini: float | None = None
    rows: list[dict] = []
    for name, included in ABLATION_VARIANTS.items():
        raw = np.array(
            [_profile_risk(row, included) for row in frame[list(FACTOR_LEVELS)].to_dict("records")],
            dtype=float,
        )
        percentile = _reference_percentiles(raw)
        high = percentile > threshold
        anchors = np.array([mirrored_anchor_index(value, representative_pool_size) + 1 for value in percentile])
        # Spearman is Pearson correlation of stable average ranks.
        correlation = float(pd.Series(full_raw).rank(method="average").corr(pd.Series(raw).rank(method="average")))
        variant_frame = frame.copy()
        variant_frame["risk_score_rho_raw"] = raw
        variant_frame["risk_percentile_u"] = percentile
        patients = _structural_policy_patients(variant_frame, "STRUCT")
        oracle_upper_bound = scenario_oracle_upper_bound_utility(
            patients, roster, SCENARIOS["F"]
        )
        summary, assignments = simulate_policy(
            patients,
            roster,
            "F",
            "P4",
            threshold,
            seed=7001,
            outcome_noise_sd=0.0,
            oracle_upper_bound_utility=oracle_upper_bound,
        )
        assignments = assignments.copy()
        assignments["selected_full_pool_rank"] = assignments["clinician_id"].map(clinician_ranks)
        assignments["zip_fallback"] = assignments["route"].astype(str).str.contains("fallback", regex=False)
        if baseline_assignments is None:
            baseline_assignments = assignments[
                ["patient_id", "discipline", "clinician_id", "route"]
            ].copy()
            baseline_workload_gini = float(summary["workload_gini"])
            recommendation_change = 0.0
            actual_route_change = 0.0
        else:
            comparison = assignments.merge(
                baseline_assignments,
                on=["patient_id", "discipline"],
                how="left",
                suffixes=("", "_full"),
                validate="one_to_one",
            )
            recommendation_change = float(
                (
                    comparison["clinician_id"].fillna("UNASSIGNED")
                    != comparison["clinician_id_full"].fillna("UNASSIGNED")
                ).mean()
            )
            actual_route_change = float((comparison["route"] != comparison["route_full"]).mean())
        assigned = assignments[assignments["assigned"]]
        access = assigned["top_tier_clinician"].astype(bool)
        age_gap = assigned.groupby("age_group")["top_tier_clinician"].mean()
        price_gap = assigned.groupby("house_price_zip")["top_tier_clinician"].mean()
        rows.append(
            {
                "variant": name,
                "included_factors": "|".join(included),
                "spearman_rho_vs_full": correlation,
                "high_risk_classification_change_rate": float(np.mean(high != full_high)),
                "route_change_rate": actual_route_change,
                "recommendation_anchor_change_rate": float(np.mean(anchors != full_anchor)),
                "recommendation_change_rate": recommendation_change,
                "top_tier_access_rate": float(access.mean()),
                "age_group_top_tier_access_gap": float(age_gap.max() - age_gap.min()),
                "house_price_top_tier_access_gap": float(price_gap.max() - price_gap.min()),
                "mean_selected_full_pool_rank": float(assigned["selected_full_pool_rank"].mean()),
                "zip_fallback_rate": float(assignments["zip_fallback"].mean()),
                "zip_continuity_rate": float(assigned["zip_continuity"].mean()),
                "simulated_outcome_utility": float(summary["simulated_outcome_utility"]),
                "travel_proxy": float(summary["travel_proxy"]),
                "workload_gini": float(summary["workload_gini"]),
                "workload_gini_change": float(summary["workload_gini"] - float(baseline_workload_gini)),
                "unassigned_rate": float(summary["unassigned_rate"]),
                "assignment_method": "fixed_roster_full_current_P4_policy_rerun",
                "public_data_only": True,
                "synthetic_data": True,
                "production_validated": False,
                "provenance": PROVENANCE_LABEL,
                "risk_reference_id": _canonical_risk_assets()[0]["metadata"]["reference_id"],
                "risk_percentile_semantics": "persisted_reference_ecdf",
            }
        )
    return pd.DataFrame(rows)


def reliability_sensitivity_experiment(
    profiles: pd.DataFrame | None = None,
    *,
    reliability_values: Iterable[float] = tuple(round(value, 2) for value in np.linspace(0.1, 1.0, 10)),
) -> pd.DataFrame:
    """Vary reliability and explicitly record its uncertainty-only role."""

    frame = generate_full_factorial_profiles() if profiles is None else profiles
    baseline_risk = frame["risk_score_rho_raw"].to_numpy(dtype=float)
    values = tuple(float(value) for value in reliability_values)
    if any(value < 0.1 or value > 1.0 for value in values):
        raise ValueError("reliability values must be in [0.10, 1.00]")
    rows: list[dict] = []
    contribution_weights = {
        factor: max(values_by_level.values())
        for factor, values_by_level in _canonical_factor_contributions().items()
    }
    weight_total = sum(contribution_weights.values())
    for factor in FACTOR_LEVELS:
        for reliability in values:
            reliability_map = dict(DEFAULT_RELIABILITY)
            reliability_map[factor] = reliability
            weighted = sum(
                contribution_weights[name] * reliability_map[name]
                for name in FACTOR_LEVELS
            ) / weight_total
            mean_uncertainty = 0.025 + 0.10 * (1.0 - weighted)
            rows.append(
                {
                    "factor": factor,
                    "reliability": reliability,
                    "mean_risk_uncertainty": float(mean_uncertainty),
                    "spearman_rho_vs_default": 1.0,
                    "high_risk_change_rate": 0.0,
                    "route_change_rate": 0.0,
                    "recommendation_change_rate": 0.0,
                    "mean_risk_score_check": float(baseline_risk.mean()),
                    "reliability_affects_assignment_mean": False,
                    "reliability_role": "uncertainty_only_in_mean_based_assignment",
                    "public_data_only": True,
                    "synthetic_data": True,
                    "production_validated": False,
                    "risk_reference_id": _canonical_risk_assets()[0]["metadata"]["reference_id"],
                }
            )
    return pd.DataFrame(rows)


def counterfactual_fairness_experiment(
    profiles: pd.DataFrame | None = None,
    *,
    threshold: float = PRODUCTION_PILOT_THRESHOLD,
    representative_pool_size: int = 10,
) -> pd.DataFrame:
    """One-attribute-at-a-time technical audit through the actual P4 simulator."""

    if profiles is not None and len(profiles) != 2_160:
        raise ValueError("profiles must be the complete 2,160-row structural grid")
    persisted_reference = _canonical_risk_assets()[0]
    baseline = {
        "health_status": "moderate",
        "age_group": "age_70_80",
        "house_price_zip": "average",
        "area_type": "micropolitan",
        "housing_type": "single_home",
        "distance_to_clinic": "medium",
        "driving_condition": "good_summer",
    }
    variations = {
        "age_group": FACTOR_LEVELS["age_group"],
        "zip_area": FACTOR_LEVELS["area_type"],
        "house_price_zip": FACTOR_LEVELS["house_price_zip"],
        "housing_type": FACTOR_LEVELS["housing_type"],
    }
    baseline_level = {
        "age_group": baseline["age_group"],
        "zip_area": baseline["area_type"],
        "house_price_zip": baseline["house_price_zip"],
        "housing_type": baseline["housing_type"],
    }
    roster = _representative_structural_roster(1)
    clinician_ranks = _selected_rank_map(roster)
    rows: list[dict] = []
    for attribute, levels in variations.items():
        for level in levels:
            profile = dict(baseline)
            actual_factor = "area_type" if attribute == "zip_area" else attribute
            profile[actual_factor] = level
            raw = _profile_risk(profile)
            percentile = empirical_risk_percentile(raw, persisted_reference["sorted_risk_scores"])
            variant_frame = pd.DataFrame(
                [{"profile_id": f"CF-{attribute}-{level}", **profile, "risk_score_rho_raw": raw, "risk_percentile_u": percentile}]
            )
            patients = _structural_policy_patients(variant_frame, "CF")
            oracle_upper_bound = scenario_oracle_upper_bound_utility(
                patients, roster, SCENARIOS["F"]
            )
            summary, assignments = simulate_policy(
                patients,
                roster,
                "F",
                "P4",
                threshold,
                seed=7001,
                outcome_noise_sd=0.0,
                oracle_upper_bound_utility=oracle_upper_bound,
            )
            assignments = assignments.copy()
            assignments["selected_full_pool_rank"] = assignments["clinician_id"].map(clinician_ranks)
            assigned = assignments[assignments["assigned"]]
            high = bool(assignments["high_risk"].iloc[0])
            route = "|".join(
                f"{row.discipline}={row.route}"
                for row in assignments.sort_values("discipline").itertuples(index=False)
            )
            fallback = float(assignments["route"].astype(str).str.contains("fallback", regex=False).mean())
            top_tier = float(assigned["top_tier_clinician"].mean()) if not assigned.empty else 0.0
            selected_rank = float(assigned["selected_full_pool_rank"].mean()) if not assigned.empty else np.nan
            rows.append(
                {
                    "attribute_varied": attribute,
                    "level": level,
                    "baseline_level": baseline_level[attribute],
                    "risk_score_rho_raw": raw,
                    "risk_percentile_u": percentile,
                    "high_risk": high,
                    "route": route,
                    "patient_zip": str(patients["patient_zip"].iloc[0]),
                    "selected_clinician_rank": selected_rank,
                    "selected_clinician_ids": "|".join(
                        str(value) for value in assigned.sort_values("discipline")["clinician_id"]
                    ),
                    "top_tier_access": top_tier,
                    "zip_fallback_proxy": fallback,
                    "zip_fallback_rate": fallback,
                    "zip_continuity_rate": float(assigned["zip_continuity"].mean()) if not assigned.empty else 0.0,
                    "simulated_outcome": float(summary["simulated_outcome_utility"]),
                    "travel_proxy": float(summary["travel_proxy"]),
                    "workload_proxy": float(summary["workload_gini"]),
                    "workload_gini": float(summary["workload_gini"]),
                    "unassigned_rate": float(summary["unassigned_rate"]),
                    "assignment_method": "fresh_fixed_roster_full_current_P4_policy_rerun",
                    "technical_fairness_audit_not_legal_conclusion": True,
                    "public_data_only": True,
                    "synthetic_data": True,
                    "production_validated": False,
                    "provenance": PROVENANCE_LABEL,
                    "risk_reference_id": persisted_reference["metadata"]["reference_id"],
                    "risk_percentile_semantics": "persisted_reference_ecdf",
                }
            )
    output = pd.DataFrame(rows)
    for attribute, group in output.groupby("attribute_varied"):
        baseline_row = group[group["level"] == group["baseline_level"]].iloc[0]
        mask = output["attribute_varied"] == attribute
        output.loc[mask, "high_risk_changed"] = (
            output.loc[mask, "high_risk"].astype(bool) != bool(baseline_row["high_risk"])
        )
        output.loc[mask, "route_changed"] = output.loc[mask, "route"] != str(baseline_row["route"])
        output.loc[mask, "selected_rank_change"] = (
            output.loc[mask, "selected_clinician_rank"] - float(baseline_row["selected_clinician_rank"])
        )
        output.loc[mask, "top_tier_access_changed"] = ~np.isclose(
            output.loc[mask, "top_tier_access"].astype(float),
            float(baseline_row["top_tier_access"]),
        )
        output.loc[mask, "fallback_change"] = output.loc[mask, "zip_fallback_proxy"] - float(
            baseline_row["zip_fallback_proxy"]
        )
        output.loc[mask, "outcome_change"] = output.loc[mask, "simulated_outcome"] - float(
            baseline_row["simulated_outcome"]
        )
        output.loc[mask, "travel_change"] = output.loc[mask, "travel_proxy"] - float(
            baseline_row["travel_proxy"]
        )
        output.loc[mask, "workload_change"] = output.loc[mask, "workload_proxy"] - float(
            baseline_row["workload_proxy"]
        )
    return output


def run_structural_suite() -> dict[str, pd.DataFrame | dict]:
    profiles = generate_full_factorial_profiles()
    return {
        "profiles": profiles,
        "diagnostics": structural_diagnostics(profiles),
        "factor_ablation": factor_ablation_experiment(profiles),
        "reliability_sensitivity": reliability_sensitivity_experiment(profiles),
        "counterfactual_fairness": counterfactual_fairness_experiment(profiles),
    }


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def save_experiment_artifacts(
    results: pd.DataFrame,
    output_dir: str | Path,
    *,
    config: BatchExperimentConfig | None = None,
    design_configs: Sequence[BatchExperimentConfig] | None = None,
    assignments: pd.DataFrame | None = None,
    structural: Mapping[str, pd.DataFrame | dict] | None = None,
    reproduction_command: str | None = None,
) -> dict[str, Path]:
    """Write machine-readable outputs and a cautious human-readable summary."""

    if results.empty:
        raise ValueError("results must not be empty")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    paths["policy_threshold_results"] = root / "policy_threshold_results.csv"
    results.to_csv(paths["policy_threshold_results"], index=False)
    if assignments is not None:
        paths["assignment_details"] = root / "assignment_details.csv"
        assignments.to_csv(paths["assignment_details"], index=False)

    threshold_policy = "P4" if "P4" in set(results["policy"]) else str(results["policy"].iloc[0])
    recommendation, threshold_table = select_simulation_thresholds(results, policy=threshold_policy)
    paths["threshold_summary"] = root / "threshold_summary.csv"
    threshold_table.to_csv(paths["threshold_summary"], index=False)
    paths["threshold_recommendations"] = root / "threshold_recommendations.json"
    paths["threshold_recommendations"].write_text(
        json.dumps(_json_safe(recommendation), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    structural_diagnostic: dict | None = None
    if structural:
        for name, artifact in structural.items():
            if isinstance(artifact, pd.DataFrame):
                path = root / f"structural_{name}.csv"
                artifact.to_csv(path, index=False)
                paths[f"structural_{name}"] = path
            elif name == "diagnostics":
                structural_diagnostic = artifact
                path = root / "structural_diagnostics.json"
                path.write_text(json.dumps(_json_safe(artifact), indent=2, sort_keys=True) + "\n", encoding="utf-8")
                paths["structural_diagnostics"] = path

    effective_design_configs = list(design_configs or ([] if config is None else [config]))
    implementation_paths = [
        Path(__file__).resolve(),
        PROJECT_ROOT / "scripts" / "run_batch_policy_experiments.py",
    ]
    metadata = {
        "artifact_family": "batch_capacity_policy_threshold_experiments",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "provenance": PROVENANCE_LABEL,
        "source_mode": "deterministic_offline_public_only_semi_synthetic",
        "public_data_only": True,
        "synthetic_data": True,
        "production_validated": False,
        "outcome_validated": False,
        "causal_effects_identified": False,
        "cms_model": CMS_MODEL,
        "performance_year": PERFORMANCE_YEAR,
        "payment_year": PAYMENT_YEAR,
        "measure_set_version": MEASURE_SET_VERSION,
        "cms_version_locked": True,
        "cy2025_weights": CY2025_WEIGHTS,
        "cy2025_weights_sum": sum(CY2025_WEIGHTS.values()),
        "common_random_numbers": True,
        "threshold_candidates_fully_rerun": True,
        "threshold_optimization_evaluated": bool(recommendation["threshold_optimization_evaluated"]),
        "held_out_threshold_evaluation_performed": bool(
            recommendation.get("held_out_evaluation_performed", False)
        ),
        "oracle_regret_available": bool(results["oracle_regret_available"].all()),
        "risk_reference_id": _canonical_risk_assets()[0]["metadata"]["reference_id"],
        "risk_reference_config_hash": _canonical_risk_assets()[0]["metadata"]["config_hash"],
        "risk_reference_sample_size": int(_canonical_risk_assets()[0]["metadata"]["sample_size"]),
        "risk_percentile_semantics": "persisted_reference_ecdf",
        "production_demo_threshold": PRODUCTION_PILOT_THRESHOLD,
        "production_threshold_changed": False,
        "result_rows": int(len(results)),
        "scenarios": sorted(str(value) for value in results["scenario"].unique()),
        "policies": sorted(str(value) for value in results["policy"].unique()),
        "seeds": sorted(int(value) for value in results["seed"].unique()),
        "thresholds": sorted(float(value) for value in results["threshold_percentile"].unique()),
        "config": None if config is None else asdict(config),
        "config_hash": None if config is None else canonical_config_hash(config),
        "design_configs": [asdict(item) for item in effective_design_configs],
        "design_config_hashes": [canonical_config_hash(item) for item in effective_design_configs],
        "implementation_file_hashes_sha256": {
            str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in implementation_paths
            if path.exists()
        },
        "reproduction_command": reproduction_command,
        "limitations": [
            "No sponsor patient, clinician, assignment, capacity, scheduling, or outcome data were available.",
            "Clinician causal effects and ZIP-continuity effects are explicit scenario assumptions.",
            "The P6 optimizer is a deterministic capacity-constrained greedy approximation, not an exact operational scheduler.",
            "P7 is a feasible latent-quality greedy comparator, not the regret oracle and not evidence of a real-world oracle.",
            "Scenario-oracle regret uses an unconstrained per-patient latent-information upper bound; it is not an implementable policy.",
            "Threshold recommendations require at least two candidates; held-out evaluation requires at least two seeds.",
            "TPS is a CY2025-weighted simulation proxy, not an official CMS payment calculation.",
        ],
    }
    paths["metadata"] = root / "experiment_metadata.json"
    paths["metadata"].write_text(
        json.dumps(_json_safe(metadata), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    policy_summary = (
        results.groupby(["policy", "policy_name"], as_index=False)
        .agg(
            tps_proxy=("tps_proxy", "mean"),
            utility=("simulated_outcome_utility", "mean"),
            regret=("regret_vs_scenario_oracle", "mean"),
            best_included_regret=("regret_vs_best_included_policy", "mean"),
            fallback=("zip_fallback_rate", "mean"),
            continuity=("zip_continuity_rate", "mean"),
            workload_gini=("workload_gini", "mean"),
            unassigned=("unassigned_rate", "mean"),
            travel=("travel_proxy", "mean"),
        )
        .sort_values("policy", kind="stable")
    )
    if recommendation["threshold_optimization_evaluated"]:
        threshold_status_line = (
            f"- Simulation minimax-regret threshold for {threshold_policy}: "
            f"{recommendation['robust_minimax_regret_threshold']:.2f} "
            "(selected on training seeds; not activated)"
        )
    elif recommendation.get("in_sample_sensitivity_evaluated"):
        threshold_status_line = (
            f"- Threshold sensitivity: {len(recommendation['evaluated_thresholds'])} candidates "
            "evaluated in-sample; no recommendation emitted without held-out seeds"
        )
    else:
        threshold_status_line = (
            f"- Threshold optimization: not evaluated "
            f"(fixed candidate {results['threshold_percentile'].iloc[0]:.2f} only)"
        )
    lines = [
        "# Batch Capacity and Policy Experiment Summary",
        "",
        "These results are public-data-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated. They do not establish causal patient benefit, payment improvement, or real-world optimality.",
        "",
        f"- CMS model lock: {CMS_MODEL}, CY{PERFORMANCE_YEAR} performance year / CY{PAYMENT_YEAR} payment year",
        f"- Production/demo threshold retained: {PRODUCTION_PILOT_THRESHOLD:.2f}",
        threshold_status_line,
        f"- Held-out threshold evaluation: {'yes' if recommendation.get('held_out_evaluation_performed') else 'no'}",
        f"- Analytical scenario-upper-bound regret available: {'yes' if results['oracle_regret_available'].all() else 'no'}",
        f"- Result rows: {len(results):,}",
        f"- Common random numbers: yes",
        f"- Every candidate resets workload and reruns route/pool/assignment/capacity/outcome: yes",
        "",
        "## Policy averages",
        "",
        "| Policy | TPS proxy | Utility | Upper-bound regret | Best-included regret | ZIP fallback | ZIP continuity | Workload Gini | Unassigned | Travel proxy |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in policy_summary.itertuples(index=False):
        oracle_regret = "n/a" if pd.isna(row.regret) else f"{row.regret:.4f}"
        lines.append(
            f"| {row.policy} | {row.tps_proxy:.3f} | {row.utility:.4f} | {oracle_regret} | "
            f"{row.best_included_regret:.4f} | {row.fallback:.3f} | {row.continuity:.3f} | {row.workload_gini:.3f} | "
            f"{row.unassigned:.3f} | {row.travel:.2f} |"
        )
    lines.extend(["", "## Scenario-specific simulation thresholds", ""])
    if recommendation["threshold_optimization_evaluated"]:
        for scenario, value in recommendation["scenario_specific_best_thresholds"].items():
            lines.append(f"- Scenario {scenario}: {value:.2f} (selected on training seeds)")
    elif recommendation.get("in_sample_sensitivity_evaluated"):
        lines.append("Multiple candidates were screened in-sample; no recommendation was emitted.")
    else:
        lines.append("Threshold optimization was not evaluated in this fixed-threshold run.")
    lines.extend(
        [
            "",
            "These are simulation-specific recommendations only. The configured 0.75 sponsor pilot remains unchanged.",
            "",
            "## Structural audit",
            "",
        ]
    )
    if structural_diagnostic:
        lines.extend(
            [
                f"- Full factorial profiles: {structural_diagnostic['profile_count']:,}",
                f"- Monotonicity violations: {structural_diagnostic['monotonicity_violations']}",
                f"- Unique raw-risk values: {structural_diagnostic['risk_unique_values']}",
                f"- Age + health configured joint weight: {structural_diagnostic['age_health_joint_weight']:.2f}",
            ]
        )
    else:
        lines.append("Structural suite was not requested for this run.")
    lines.extend(
        [
            "",
            "## Reproduction",
            "",
            reproduction_command or "Use scripts/run_batch_policy_experiments.py with the settings in experiment_metadata.json.",
            "",
        ]
    )
    paths["markdown_summary"] = root / "EXPERIMENT_SUMMARY.md"
    paths["markdown_summary"].write_text("\n".join(lines), encoding="utf-8")
    return paths
