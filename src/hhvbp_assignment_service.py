from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import numpy as np
import pandas as pd

from hhvbp_local_externality import (
    METRIC_ORDER,
    RAW_DIRECTION,
    build_patient_need_vector,
    derive_age_group,
    load_json,
    normalize_age_group,
)

OUTCOME_METRICS = {"PPH", "DTC", "DFS", "Oral_Meds", "Dyspnea"}
SURVEY_METRICS = {"Care_of_Patients", "Communications", "Care_Issues", "Agency_Rating", "Recommend"}
ALL_DISCIPLINES = ["RN", "PT", "OT"]
DEFAULT_SCORED_DISCIPLINES = ["RN", "PT"]
DISCIPLINE_ID_PREFIX = {"RN": 1, "PT": 2, "OT": 3}
PATIENT_PROFILE_EXCLUDE_KEYS = {
    "patient_id",
    "metadata",
    "severity_score_override",
    "age",
    "patient_age",
    "zip",
    "zip_code",
    "patient_zip",
}
PATIENT_ZIP_KEYS = ["zip_code", "patient_zip", "zip"]
ZIP_HISTORY_REQUEST_KEYS = ["clinician_zip_history", "zip_history", "clinician_zip_history_rows"]
DEFAULT_CANONICAL_METRIC_ALIASES = {
    "DFS": ["DFS", "DC Function", "Discharge Function Score"],
    "Dyspnea": ["Dyspnea", "Improvement in Dyspnea"],
    "Oral_Meds": ["Oral_Meds", "Oral Meds", "Improvement in Management of Oral Medications"],
    "DTC": ["DTC", "DTC-PAC", "Discharge to Community-Post Acute Care", "Discharge to Community—Post Acute Care"],
    "PPH": ["PPH", "Potentially Preventable Hospitalization"],
    "Care_of_Patients": ["Care_of_Patients", "Care of Patients"],
    "Communications": ["Communications", "Communications Between Providers and Patients"],
    "Care_Issues": ["Care_Issues", "Care Issues", "Specific Care Issues"],
    "Agency_Rating": ["Agency_Rating", "Agency Rating", "Overall Rating", "Overall / Agency Rating"],
    "Recommend": ["Recommend", "Willingness to Recommend"],
}


class RequestValidationError(ValueError):
    pass


def stable_sha256_for_file(path: str | Path) -> str:
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable_hash_to_int(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest(), 16)


def resolve_config_dir(config_dir: str | Path | None = None) -> Path:
    if config_dir is None:
        return Path(__file__).resolve().parents[1] / "config"
    return Path(config_dir).resolve()


def load_assignment_parameters(config_dir: str | Path | None = None) -> dict:
    config_dir = resolve_config_dir(config_dir)
    return load_json(config_dir / "assignment_parameters.json")


def normalize_metric_name(value: Any) -> str:
    """Normalize a metric label for alias matching without changing values."""
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def load_canonical_metric_aliases(config_dir: str | Path | None = None) -> dict[str, list[str]]:
    """Load the CY2025 canonical name map, with a safe in-code compatibility fallback."""
    config_dir = resolve_config_dir(config_dir)
    path = config_dir / "metric_parameter_library.json"
    configured = load_json(path).get("canonical_metric_aliases", {}) if path.exists() else {}
    if not configured:
        return {key: list(values) for key, values in DEFAULT_CANONICAL_METRIC_ALIASES.items()}

    # The preferred schema is canonical -> aliases. Accept alias -> canonical as a
    # compatibility convenience for older local configuration experiments.
    if all(isinstance(value, str) for value in configured.values()):
        aliases: dict[str, list[str]] = {metric: [metric] for metric in METRIC_ORDER}
        for alias, canonical in configured.items():
            if canonical in aliases:
                aliases[canonical].append(str(alias))
        return aliases

    aliases = {metric: [metric] for metric in METRIC_ORDER}
    for canonical, values in configured.items():
        if canonical not in aliases:
            continue
        if isinstance(values, str):
            values = [values]
        if isinstance(values, (list, tuple, set)):
            aliases[canonical].extend(str(value) for value in values)
    return aliases


def build_metric_alias_lookup(metric_aliases: dict[str, Sequence[str]] | None = None) -> dict[str, str]:
    alias_map = metric_aliases or DEFAULT_CANONICAL_METRIC_ALIASES
    lookup: dict[str, str] = {}
    for canonical in METRIC_ORDER:
        aliases = list(alias_map.get(canonical, [])) + [canonical]
        for alias in aliases:
            normalized = normalize_metric_name(alias)
            previous = lookup.get(normalized)
            if previous is not None and previous != canonical:
                raise RequestValidationError(
                    f"Metric alias {alias!r} maps to both {previous!r} and {canonical!r}."
                )
            lookup[normalized] = canonical
    return lookup


def normalize_metric_value(value: Any, scale: str = "auto") -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None
        value = float(stripped)
    else:
        value = float(value)

    if np.isnan(value):
        return None

    scale = (scale or "auto").lower()
    if scale == "proportion":
        normalized = value
    elif scale == "percentage":
        normalized = value / 100.0
    elif scale == "auto":
        normalized = value / 100.0 if abs(value) > 1.0 else value
    else:
        raise RequestValidationError(f"Unsupported metric scale {scale!r}. Use 'auto', 'percentage', or 'proportion'.")
    return float(normalized)


def normalize_unit_interval(value: Any, field_name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise RequestValidationError(f"{field_name} must be numeric and between 0 and 1.") from exc
    if np.isnan(numeric) or numeric < 0.0 or numeric > 1.0:
        raise RequestValidationError(f"{field_name} must be between 0 and 1 inclusive.")
    return float(numeric)


def normalize_zip5(value: Any, field_name: str = "ZIP") -> str | None:
    """Normalize ZIP-like input to a five-character string, preserving leading zeroes."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise RequestValidationError(f"{field_name} must be a 5-digit ZIP code, not a boolean.")
    if isinstance(value, (int, np.integer)):
        numeric = int(value)
        if 0 <= numeric <= 99999:
            return f"{numeric:05d}"
        raise RequestValidationError(f"{field_name} must be a 5-digit ZIP code.")
    if isinstance(value, float):
        if not np.isfinite(value) or not float(value).is_integer():
            raise RequestValidationError(f"{field_name} must be a 5-digit ZIP code.")
        numeric = int(value)
        if 0 <= numeric <= 99999:
            return f"{numeric:05d}"
        raise RequestValidationError(f"{field_name} must be a 5-digit ZIP code.")

    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{1,5}", text):
        return text.zfill(5)
    zip_plus_four = re.fullmatch(r"(\d{5})-\d{4}", text)
    if zip_plus_four:
        return zip_plus_four.group(1)
    raise RequestValidationError(f"{field_name} must be a 5-digit ZIP code.")


def parse_zip_history_value(value: Any) -> tuple[set[str], dict]:
    """Parse comma/semicolon/pipe separated ZIP history strings."""
    audit = {
        "input_present": value is not None,
        "tokens_seen": 0,
        "valid_zip_tokens": 0,
        "duplicate_zip_tokens": 0,
        "invalid_zip_tokens": [],
    }
    if value is None:
        return set(), audit
    if isinstance(value, (list, tuple, set, np.ndarray, pd.Series)):
        raw_tokens: list[Any] = list(value)
    else:
        raw_tokens = re.split(r"[,;|]", str(value))

    zip_codes: set[str] = set()
    for token in raw_tokens:
        text = str(token).strip()
        if not text:
            continue
        audit["tokens_seen"] += 1
        try:
            zip5 = normalize_zip5(token, field_name="zip_codes_treated")
        except RequestValidationError:
            audit["invalid_zip_tokens"].append(text)
            continue
        if zip5 is None:
            continue
        if zip5 in zip_codes:
            audit["duplicate_zip_tokens"] += 1
        else:
            zip_codes.add(zip5)
            audit["valid_zip_tokens"] += 1
    return zip_codes, audit


def first_present_value(source: dict, keys: Iterable[str]) -> Any:
    for key in keys:
        if key in source and source[key] not in (None, ""):
            return source[key]
    return None


def to_json_safe_scalar(value: Any) -> Any:
    """Convert numpy scalar values to plain Python values for JSON output."""
    if isinstance(value, np.generic):
        return value.item()
    return value


def encode_clinician_integer_id(discipline: str, sequence: int) -> int:
    """Encode clinician id as a three-digit category + sequence integer.

    Convention:
      - RN -> 1xx, e.g. RN1 = 101
      - PT -> 2xx, e.g. PT1 = 201
      - OT -> 3xx, e.g. OT1 = 301

    The final two digits support sequence numbers 01 through 99.
    """
    discipline = str(discipline).strip().upper()
    if discipline not in DISCIPLINE_ID_PREFIX:
        raise RequestValidationError(f"Cannot encode clinician id for unknown discipline {discipline!r}.")
    sequence = int(sequence)
    if sequence < 1 or sequence > 99:
        raise RequestValidationError(
            f"Clinician sequence for {discipline} must be between 1 and 99 to fit the 3-digit id convention."
        )
    return int(DISCIPLINE_ID_PREFIX[discipline] * 100 + sequence)


def extract_sequence_from_text(text: Any, discipline: str) -> int | None:
    """Infer the clinician number from labels such as RN1, RN_RN1, PT5, or OT03."""
    if text is None:
        return None
    discipline = str(discipline).strip().upper()
    text_upper = str(text).strip().upper()
    if not text_upper or discipline not in DISCIPLINE_ID_PREFIX:
        return None

    match = re.search(rf"{re.escape(discipline)}\D*0*(\d{{1,2}})\b", text_upper)
    if match:
        return int(match.group(1))
    return None


def ensure_clinician_id(clinician: dict) -> int:
    """Return a clinician id using the agreed integer convention.

    The backend should ideally pass integer ids directly. This helper also
    accepts numeric strings like "101" and legacy demo ids like "RN_RN1" for
    backward-compatible local testing. Non-numeric ids without an inferable
    discipline sequence are rejected so production data stays consistent.
    """
    discipline = str(clinician.get("discipline", "")).strip().upper()
    existing = clinician.get("clinician_id")

    if existing is not None and str(existing).strip() != "":
        if isinstance(existing, (int, np.integer)) and not isinstance(existing, bool):
            return int(existing)
        if isinstance(existing, float) and float(existing).is_integer():
            return int(existing)
        if isinstance(existing, str):
            stripped = existing.strip()
            if stripped.isdigit():
                return int(stripped)
            sequence = extract_sequence_from_text(stripped, discipline)
            if sequence is not None:
                return encode_clinician_integer_id(discipline, sequence)

    sequence = extract_sequence_from_text(clinician.get("clinician_name"), discipline)
    if sequence is not None:
        return encode_clinician_integer_id(discipline, sequence)

    raise RequestValidationError(
        "clinician_id must be an integer using the category convention "
        "RN=1xx, PT=2xx, OT=3xx. For example, RN1=101, PT1=201, OT1=301."
    )


def clinician_possible_ids_from_label(label: Any, discipline: str | None = None) -> set[int]:
    ids: set[int] = set()
    if label is None:
        return ids
    disciplines = [discipline] if discipline else ALL_DISCIPLINES
    for disc in disciplines:
        sequence = extract_sequence_from_text(label, disc)
        if sequence is not None:
            ids.add(encode_clinician_integer_id(disc, sequence))
    return ids


def normalize_match_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def clinician_match_keys_from_values(
    clinician_id: Any = None,
    clinician_name: Any = None,
    blinded_name: Any = None,
    discipline: str | None = None,
) -> set[str]:
    keys: set[str] = set()
    for raw_id in [clinician_id]:
        if raw_id is None or str(raw_id).strip() == "":
            continue
        try:
            if isinstance(raw_id, str) and not raw_id.strip().isdigit():
                for inferred_id in clinician_possible_ids_from_label(raw_id, discipline):
                    keys.add(f"id:{inferred_id}")
            else:
                keys.add(f"id:{int(float(raw_id))}")
        except (TypeError, ValueError):
            pass
    for label in [clinician_name, blinded_name]:
        normalized = normalize_match_text(label)
        if normalized:
            keys.add(f"name:{normalized}")
            for inferred_id in clinician_possible_ids_from_label(label, discipline):
                keys.add(f"id:{inferred_id}")
    return keys


def build_clinician_lookup(prepared_source_df: pd.DataFrame) -> dict[str, int]:
    lookup: dict[str, int] = {}
    for _, row in prepared_source_df.iterrows():
        clinician_id = int(row["clinician_id"])
        keys = clinician_match_keys_from_values(
            clinician_id=clinician_id,
            clinician_name=row.get("clinician_name"),
            blinded_name=row.get("blinded_name"),
            discipline=row.get("discipline"),
        )
        for key in keys:
            lookup[key] = clinician_id
    return lookup


def match_zip_history_row_to_clinician(row: dict, lookup: dict[str, int]) -> int | None:
    discipline = first_present_value(row, ["discipline", "Discipline"])
    keys = clinician_match_keys_from_values(
        clinician_id=first_present_value(row, ["clinician_id", "Clinician ID", "id", "ID"]),
        clinician_name=first_present_value(row, ["clinician_name", "Clinician Name", "name", "Name"]),
        blinded_name=first_present_value(row, ["blinded_name", "Blinded Name", "Blinded Names", "blinded"]),
        discipline=None if discipline is None else str(discipline).strip().upper(),
    )
    for key in keys:
        if key in lookup:
            return lookup[key]
    return None


def validate_request_shape(request: dict) -> None:
    if not isinstance(request, dict):
        raise RequestValidationError("Top-level request must be a JSON object.")
    if "patient" not in request:
        raise RequestValidationError("Request must contain a 'patient' object.")
    if "clinicians" not in request:
        raise RequestValidationError("Request must contain a 'clinicians' array.")
    if not isinstance(request["patient"], dict):
        raise RequestValidationError("'patient' must be a JSON object.")
    if not isinstance(request["clinicians"], list):
        raise RequestValidationError("'clinicians' must be a JSON array.")
    if len(request["clinicians"]) == 0:
        raise RequestValidationError("'clinicians' array cannot be empty.")


def extract_patient_profile(patient_obj: dict) -> dict:
    local_externalities = patient_obj.get("local_externalities", {})
    if local_externalities is None:
        local_externalities = {}
    if not isinstance(local_externalities, dict):
        raise RequestValidationError("Patient local externalities must be a JSON object.")

    if "local_externalities" in patient_obj:
        profile = dict(local_externalities)
    else:
        profile = {
            k: v
            for k, v in patient_obj.items()
            if k not in PATIENT_PROFILE_EXCLUDE_KEYS and k != "local_externalities"
        }

    age_group = first_present_value(patient_obj, ["age_group"])
    if age_group is None:
        age_group = first_present_value(local_externalities, ["age_group"])
    if age_group is None:
        raw_age = first_present_value(patient_obj, ["age", "patient_age"])
        if raw_age is None:
            raw_age = first_present_value(local_externalities, ["age"])
        if raw_age is None:
            raise RequestValidationError("Patient age_group or numeric age is required for seven-factor HHVBP need scoring.")
        try:
            age_group = derive_age_group(raw_age)
        except ValueError as exc:
            raise RequestValidationError(str(exc)) from exc

    profile["age_group"] = normalize_age_group(age_group)
    for key in PATIENT_PROFILE_EXCLUDE_KEYS:
        profile.pop(key, None)
    return profile


def extract_patient_zip(patient_obj: dict) -> str | None:
    local_externalities = patient_obj.get("local_externalities", {})
    if local_externalities is None:
        local_externalities = {}
    if not isinstance(local_externalities, dict):
        raise RequestValidationError("Patient local externalities must be a JSON object.")
    raw_zip = first_present_value(patient_obj, PATIENT_ZIP_KEYS)
    if raw_zip is None:
        raw_zip = first_present_value(local_externalities, ["zip_code", "zip"])
    return normalize_zip5(raw_zip, field_name="patient ZIP") if raw_zip is not None else None


def determine_scored_disciplines(assignment_params: dict) -> List[str]:
    scored = assignment_params.get("scored_disciplines") or DEFAULT_SCORED_DISCIPLINES
    normalized = []
    for discipline in scored:
        disc = str(discipline).strip().upper()
        if disc in ALL_DISCIPLINES and disc not in normalized:
            normalized.append(disc)
    if not normalized:
        raise RequestValidationError("At least one scored discipline must be configured.")
    return normalized


def build_effective_discipline_metric_weights(assignment_params: dict) -> dict:
    raw_weights = assignment_params["discipline_metric_weights"]
    scored_disciplines = determine_scored_disciplines(assignment_params)
    effective: dict = {}
    for metric in METRIC_ORDER:
        metric_map = raw_weights.get(metric, {})
        denominator = float(sum(max(float(metric_map.get(discipline, 0.0) or 0.0), 0.0) for discipline in scored_disciplines))
        if denominator <= 0:
            raise RequestValidationError(f"Metric {metric!r} has no positive weight among scored disciplines {scored_disciplines}.")
        effective[metric] = {
            discipline: (
                float(metric_map.get(discipline, 0.0) or 0.0) / denominator
                if discipline in scored_disciplines
                else 0.0
            )
            for discipline in ALL_DISCIPLINES
        }
    return effective


def clinician_records_to_dataframe(
    request: dict,
    assignment_params: dict,
    metric_aliases: dict[str, Sequence[str]] | None = None,
) -> tuple[pd.DataFrame, dict]:
    validate_request_shape(request)
    default_scale = request.get("options", {}).get("metric_input_scale_default", assignment_params.get("default_metric_input_scale", "auto"))
    benchmark_map = assignment_params["metric_benchmarks"]
    metric_alias_lookup = build_metric_alias_lookup(metric_aliases)

    rows: List[dict] = []
    missing_summary = {metric: 0 for metric in METRIC_ORDER}
    alias_usage: dict[str, int] = {}
    duplicate_alias_values_deduplicated = 0
    unknown_metric_keys: set[str] = set()

    for idx, clinician in enumerate(request["clinicians"]):
        if not isinstance(clinician, dict):
            raise RequestValidationError(f"Each clinician must be an object. Bad item at index {idx}.")
        discipline = str(clinician.get("discipline", "")).strip().upper()
        if discipline not in ALL_DISCIPLINES:
            raise RequestValidationError(f"Clinician {clinician.get('clinician_name', idx)!r} has invalid discipline {discipline!r}.")
        clinician_name = clinician.get("clinician_name")
        if not clinician_name:
            raise RequestValidationError(f"Clinician at index {idx} is missing 'clinician_name'.")

        metric_scale = clinician.get("metric_scale", default_scale)
        metrics = clinician.get("metrics", {})
        if not isinstance(metrics, dict):
            raise RequestValidationError(f"Clinician {clinician_name!r} has non-object 'metrics'.")
        count_proxies = clinician.get("count_proxies", {}) or {}
        if not isinstance(count_proxies, dict):
            raise RequestValidationError(f"Clinician {clinician_name!r} has non-object 'count_proxies'.")

        row = {
            "clinician_id": ensure_clinician_id(clinician),
            "clinician_name": str(clinician_name),
            "blinded_name": str(clinician.get("blinded_name", clinician_name)),
            "discipline": discipline,
            "data_origin": clinician.get("data_origin", "integration_request"),
            "SOCs": float(count_proxies.get("SOCs", 0) or 0),
            "DCs": float(count_proxies.get("DCs", 0) or 0),
            "Eligible_Surveys": float(count_proxies.get("Eligible_Surveys", 0) or 0),
        }
        metric_candidates: dict[str, list[tuple[str, Any]]] = {metric: [] for metric in METRIC_ORDER}
        for raw_metric_name, raw_metric_value in metrics.items():
            canonical = metric_alias_lookup.get(normalize_metric_name(raw_metric_name))
            if canonical is None:
                unknown_metric_keys.add(str(raw_metric_name))
                continue
            metric_candidates[canonical].append((str(raw_metric_name), raw_metric_value))
            if normalize_metric_name(raw_metric_name) != normalize_metric_name(canonical):
                alias_usage[str(raw_metric_name)] = alias_usage.get(str(raw_metric_name), 0) + 1

        for metric in METRIC_ORDER:
            candidates = metric_candidates[metric]
            nonmissing_candidates: list[tuple[str, float]] = []
            for raw_name, raw_value in candidates:
                candidate_value = normalize_metric_value(raw_value, metric_scale)
                if candidate_value is not None:
                    nonmissing_candidates.append((raw_name, candidate_value))
            if len(nonmissing_candidates) > 1:
                reference_value = nonmissing_candidates[0][1]
                conflicts = [
                    (name, value)
                    for name, value in nonmissing_candidates[1:]
                    if not np.isclose(value, reference_value, rtol=0.0, atol=1e-12)
                ]
                if conflicts:
                    supplied = ", ".join(f"{name}={value}" for name, value in nonmissing_candidates)
                    raise RequestValidationError(
                        f"Clinician {clinician_name!r} supplied conflicting aliases for {metric}: {supplied}."
                    )
                duplicate_alias_values_deduplicated += len(nonmissing_candidates) - 1
            normalized = nonmissing_candidates[0][1] if nonmissing_candidates else None
            if normalized is None:
                missing_summary[metric] += 1
            row[metric] = normalized
            row[f"{metric}_benchmark"] = float(benchmark_map[metric])
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        raise RequestValidationError("No usable clinician rows were found in request.")
    summary = {
        "n_clinicians": int(len(df)),
        "discipline_counts": {discipline: int((df["discipline"] == discipline).sum()) for discipline in ALL_DISCIPLINES},
        "missing_metric_cells_before_imputation": missing_summary,
        "metric_aliases_used": dict(sorted(alias_usage.items())),
        "duplicate_alias_values_deduplicated": int(duplicate_alias_values_deduplicated),
        "unknown_metric_keys_ignored": sorted(unknown_metric_keys),
    }
    return df, summary


def extract_zip_history_rows(request: dict) -> list[dict]:
    rows: list[dict] = []
    for key in ZIP_HISTORY_REQUEST_KEYS:
        value = request.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
        elif isinstance(value, dict):
            nested_rows = value.get("rows")
            if isinstance(nested_rows, list):
                rows.extend(row for row in nested_rows if isinstance(row, dict))
            else:
                rows.append(value)
    return rows


def merge_zip_sets(target: set[str], incoming: set[str]) -> int:
    duplicate_count = len(target.intersection(incoming))
    target.update(incoming)
    return duplicate_count


def build_clinician_zip_history_map(request: dict, clinician_df: pd.DataFrame) -> tuple[dict[int, set[str]], dict]:
    lookup = build_clinician_lookup(clinician_df)
    history_by_id: dict[int, set[str]] = {int(row["clinician_id"]): set() for _, row in clinician_df.iterrows()}
    audit = {
        "inline_clinician_rows_seen": int(len(request.get("clinicians", []))),
        "inline_clinician_rows_with_zip_history": 0,
        "separate_zip_history_rows_seen": 0,
        "rows_parsed": 0,
        "matched_rows": 0,
        "unmatched_rows": 0,
        "invalid_rows": 0,
        "duplicate_zip_tokens": 0,
        "invalid_zip_tokens_count": 0,
        "invalid_zip_tokens_sample": [],
        "unmatched_row_keys_sample": [],
        "clinicians_with_zip_history": 0,
    }

    def record_zip_audit(zip_audit: dict) -> None:
        audit["duplicate_zip_tokens"] += int(zip_audit.get("duplicate_zip_tokens", 0))
        invalid_tokens = list(zip_audit.get("invalid_zip_tokens", []))
        audit["invalid_zip_tokens_count"] += len(invalid_tokens)
        remaining = max(0, 10 - len(audit["invalid_zip_tokens_sample"]))
        if remaining:
            audit["invalid_zip_tokens_sample"].extend(invalid_tokens[:remaining])

    for idx, clinician in enumerate(request.get("clinicians", [])):
        raw_zip_history = first_present_value(clinician, ["zip_codes_treated", "Zip Codes Treated"])
        if raw_zip_history is None:
            continue
        audit["inline_clinician_rows_with_zip_history"] += 1
        audit["rows_parsed"] += 1
        zip_codes, zip_audit = parse_zip_history_value(raw_zip_history)
        record_zip_audit(zip_audit)
        clinician_id = int(clinician_df.iloc[idx]["clinician_id"])
        audit["duplicate_zip_tokens"] += merge_zip_sets(history_by_id[clinician_id], zip_codes)
        audit["matched_rows"] += 1

    separate_rows = extract_zip_history_rows(request)
    audit["separate_zip_history_rows_seen"] = len(separate_rows)
    for row in separate_rows:
        raw_zip_history = first_present_value(row, ["zip_codes_treated", "Zip Codes Treated", "zip_codes", "ZIP Codes"])
        if raw_zip_history is None:
            audit["invalid_rows"] += 1
            continue
        audit["rows_parsed"] += 1
        zip_codes, zip_audit = parse_zip_history_value(raw_zip_history)
        record_zip_audit(zip_audit)
        clinician_id = match_zip_history_row_to_clinician(row, lookup)
        if clinician_id is None:
            audit["unmatched_rows"] += 1
            if len(audit["unmatched_row_keys_sample"]) < 10:
                audit["unmatched_row_keys_sample"].append(
                    {
                        "clinician_id": first_present_value(row, ["clinician_id", "Clinician ID", "id", "ID"]),
                        "clinician_name": first_present_value(row, ["clinician_name", "Clinician Name", "name", "Name"]),
                        "blinded_name": first_present_value(row, ["blinded_name", "Blinded Name", "Blinded Names", "blinded"]),
                    }
                )
            continue
        audit["duplicate_zip_tokens"] += merge_zip_sets(history_by_id[clinician_id], zip_codes)
        audit["matched_rows"] += 1

    audit["clinicians_with_zip_history"] = int(sum(1 for zips in history_by_id.values() if zips))
    return history_by_id, audit


def attach_zip_history_to_panel(prepared_df: pd.DataFrame, zip_history_by_id: dict[int, set[str]]) -> pd.DataFrame:
    out = prepared_df.copy()
    out["zip_history_zip5s"] = [
        tuple(sorted(zip_history_by_id.get(int(clinician_id), set())))
        for clinician_id in out["clinician_id"]
    ]
    return out


def prepare_clinician_panel(raw_df: pd.DataFrame, assignment_params: dict) -> tuple[pd.DataFrame, dict]:
    if raw_df.empty:
        raise ValueError("raw_df is empty.")
    candidate_df = raw_df.copy()

    for metric in METRIC_ORDER:
        discipline_medians = candidate_df.groupby("discipline")[metric].median()
        filled_values: List[float] = []
        source_labels: List[str] = []
        for _, row in candidate_df.iterrows():
            observed_value = row.get(metric)
            if observed_value is not None and not pd.isna(observed_value):
                filled_values.append(float(observed_value))
                source_labels.append("observed")
                continue
            disc = row["discipline"]
            disc_med = discipline_medians.get(disc)
            if disc_med is not None and not pd.isna(disc_med):
                filled_values.append(float(disc_med))
                source_labels.append("imputed_discipline_median")
            else:
                filled_values.append(float(row[f"{metric}_benchmark"]))
                source_labels.append("imputed_org_benchmark")
        candidate_df[metric] = filled_values
        candidate_df[f"{metric}_source"] = source_labels

    candidate_df["outcome_volume_proxy"] = candidate_df[["SOCs", "DCs"]].fillna(0).max(axis=1)
    candidate_df["survey_volume_proxy"] = candidate_df["Eligible_Surveys"].fillna(0)

    shrinkage_cfg = assignment_params.get("shrinkage", {})
    # Production ranking must never opt into shrinkage implicitly.  The
    # checked-in CY2025 configuration disables it because metric-specific
    # clinician sample sizes are unavailable; experiments may enable it only
    # through an explicit configuration override.
    enable_shrinkage = bool(assignment_params.get("enable_shrinkage", False))
    outcome_k = float(shrinkage_cfg.get("outcome_k", 30.0))
    survey_k = float(shrinkage_cfg.get("survey_k", 15.0))

    for metric in METRIC_ORDER:
        is_observed_like = candidate_df[f"{metric}_source"].isin(["observed"])
        base_n = candidate_df["survey_volume_proxy"] if metric in SURVEY_METRICS else candidate_df["outcome_volume_proxy"]
        metric_n = np.where(is_observed_like, base_n, 0.0)
        k_value = survey_k if metric in SURVEY_METRICS else outcome_k
        if enable_shrinkage:
            metric_lambda = np.where(metric_n > 0, metric_n / (metric_n + k_value), 0.0)
            metric_value_for_score = metric_lambda * candidate_df[metric].astype(float) + (1.0 - metric_lambda) * candidate_df[f"{metric}_benchmark"].astype(float)
        else:
            metric_lambda = np.ones(len(candidate_df), dtype=float)
            metric_value_for_score = candidate_df[metric].astype(float)
        candidate_df[f"{metric}_n"] = metric_n.astype(float)
        candidate_df[f"{metric}_lambda"] = metric_lambda.astype(float)
        candidate_df[f"{metric}_score_ready"] = metric_value_for_score.astype(float)

    for metric in METRIC_ORDER:
        ascending = RAW_DIRECTION[metric] != "lower_is_better"
        candidate_df[f"{metric}_capability"] = (
            candidate_df.groupby("discipline", sort=False)[f"{metric}_score_ready"]
            .rank(method="average", pct=True, ascending=ascending)
            .astype(float)
        )

    source_columns = [f"{metric}_source" for metric in METRIC_ORDER]
    candidate_df["all_metrics_observed"] = candidate_df[source_columns].eq("observed").all(axis=1)
    candidate_df["any_metric_imputed"] = ~candidate_df["all_metrics_observed"]
    candidate_df["imputation_confidence"] = np.where(
        candidate_df[source_columns].eq("imputed_org_benchmark").any(axis=1),
        "low_org_benchmark_imputation",
        np.where(
            candidate_df[source_columns].eq("imputed_discipline_median").any(axis=1),
            "moderate_discipline_median_imputation",
            "observed_no_imputation",
        ),
    )

    imputation_summary = {
        metric: {
            "observed": int((candidate_df[f"{metric}_source"] == "observed").sum()),
            "imputed_discipline_median": int((candidate_df[f"{metric}_source"] == "imputed_discipline_median").sum()),
            "imputed_org_benchmark": int((candidate_df[f"{metric}_source"] == "imputed_org_benchmark").sum()),
        }
        for metric in METRIC_ORDER
    }

    keep_cols = [
        "clinician_id",
        "clinician_name",
        "blinded_name",
        "discipline",
        "data_origin",
        "SOCs",
        "DCs",
        "Eligible_Surveys",
        "outcome_volume_proxy",
        "survey_volume_proxy",
        "all_metrics_observed",
        "any_metric_imputed",
        "imputation_confidence",
    ]
    for metric in METRIC_ORDER:
        keep_cols.extend(
            [
                metric,
                f"{metric}_source",
                f"{metric}_benchmark",
                f"{metric}_n",
                f"{metric}_lambda",
                f"{metric}_score_ready",
                f"{metric}_capability",
            ]
        )

    prepared_df = candidate_df[keep_cols].sort_values(
        ["discipline", "clinician_id", "clinician_name"], ascending=[True, True, True]
    ).reset_index(drop=True)
    return prepared_df, imputation_summary


def compute_clinician_rankings(
    prepared_scorecard_df: pd.DataFrame,
    patient_need_df: pd.DataFrame,
    discipline_metric_weights: dict,
    scored_disciplines: Sequence[str],
) -> Dict[str, pd.DataFrame]:
    metric_weight_map = patient_need_df.set_index("metric_key")["metric_weight"].to_dict()
    patient_need_map = patient_need_df.set_index("metric_key")["need_mean"].to_dict()
    scored_set = set(scored_disciplines)
    rankings: Dict[str, pd.DataFrame] = {}
    for discipline in ALL_DISCIPLINES:
        sub = prepared_scorecard_df.loc[prepared_scorecard_df["discipline"] == discipline].copy()
        if sub.empty:
            rankings[discipline] = sub
            continue
        contribution_cols = []
        for metric in METRIC_ORDER:
            col = f"{metric}_discipline_contribution"
            contribution_cols.append(col)
            if discipline in scored_set:
                sub[col] = (
                    float(metric_weight_map[metric])
                    * float(patient_need_map[metric])
                    * float(discipline_metric_weights[metric][discipline])
                    * sub[f"{metric}_capability"].astype(float)
                )
            else:
                sub[col] = 0.0

        if discipline in scored_set:
            sub["discipline_score"] = sub[contribution_cols].sum(axis=1)
            sub["ranking_basis"] = "vbp_weighted_score"
            sub = sub.sort_values(
                ["discipline_score", "clinician_id", "clinician_name"],
                ascending=[False, True, True],
            ).reset_index(drop=True)
        else:
            sub["discipline_score"] = 0.0
            sub["ranking_basis"] = "not_scored_for_vbp"
            sub = sub.sort_values(["clinician_name", "clinician_id"], ascending=[True, True]).reset_index(drop=True)
        sub["full_pool_rank"] = np.arange(1, len(sub) + 1, dtype=int)
        rankings[discipline] = sub
    return rankings


def build_patient_severity_context(
    patient_need_df: pd.DataFrame,
    patient_obj: dict,
    options: dict | None = None,
    risk_reference: dict | None = None,
) -> dict:
    options = options or {}
    override = patient_obj.get("severity_score_override", None)
    source = "sum(weighted_need_mean)"
    if override is not None:
        risk_score_rho_raw = normalize_unit_interval(override, "patient.severity_score_override")
        source = "patient.severity_score_override"
    elif options.get("severity_score_override") is not None:
        risk_score_rho_raw = normalize_unit_interval(options["severity_score_override"], "options.severity_score_override")
        source = "options.severity_score_override"
    else:
        total_weighted_need = float(patient_need_df["weighted_need_mean"].sum())
        risk_score_rho_raw = float(np.clip(total_weighted_need, 0.0, 1.0))

    if options.get("risk_percentile_override") is not None:
        risk_percentile_u = normalize_unit_interval(
            options["risk_percentile_override"], "options.risk_percentile_override"
        )
        percentile_source = "options.risk_percentile_override"
    else:
        if risk_reference is None:
            from hhvbp_risk_reference import load_risk_reference

            risk_reference = load_risk_reference()
        from hhvbp_risk_reference import empirical_risk_percentile

        sorted_scores = risk_reference.get("sorted_risk_scores", [])
        risk_percentile_u = empirical_risk_percentile(risk_score_rho_raw, sorted_scores)
        percentile_source = str(
            risk_reference.get("metadata", {}).get(
                "source_mode", risk_reference.get("source_mode", "persisted_reference_distribution")
            )
        )

    return {
        # Compatibility fields remain raw; routing/mirroring must use risk_percentile_u.
        "severity_score": float(risk_score_rho_raw),
        "rho": float(risk_score_rho_raw),
        "risk_score_rho_raw": float(risk_score_rho_raw),
        "risk_percentile_u": float(risk_percentile_u),
        "source": source,
        "risk_percentile_source": percentile_source,
        "formula": "risk_score_rho_raw = clip(sum(weighted_need_mean), 0, 1); risk_percentile_u = empirical CDF of rho_raw against the persisted public-only semi-synthetic reference.",
        "interpretation": "rho_raw is an aggregate modeled need index, not a probability. risk_percentile_u is its relative position in the configured reference distribution.",
        "aggregate_weighted_need_mean": float(patient_need_df["weighted_need_mean"].sum()),
    }


def round_half_up(value: float) -> int:
    return int(np.floor(float(value) + 0.5))


def anchor_index_from_risk_percentile(risk_percentile_u: float, pool_size: int) -> int:
    if pool_size <= 0:
        raise RequestValidationError("Cannot compute mirrored anchor with an empty clinician pool.")
    if pool_size == 1:
        return 0
    raw = (1.0 - normalize_unit_interval(risk_percentile_u, "risk_percentile_u")) * float(pool_size - 1)
    return int(np.clip(round_half_up(raw), 0, pool_size - 1))


def anchor_index_from_severity(severity_score: float, pool_size: int) -> int:
    """Backward-compatible internal alias; the input is now a reference percentile."""
    return anchor_index_from_risk_percentile(severity_score, pool_size)


def describe_relative_to_anchor(anchor_index: int, selected_index: int) -> str:
    delta = selected_index - anchor_index
    if delta == 0:
        return "anchor"
    distance = abs(delta)
    if delta < 0:
        return "one_rank_better" if distance == 1 else f"{distance}_ranks_better"
    return "one_rank_worse" if distance == 1 else f"{distance}_ranks_worse"


def candidate_indices_from_anchor(anchor_index: int, pool_size: int, top_k: int) -> List[int]:
    if pool_size <= 0 or top_k <= 0:
        return []

    unique_indices: List[int] = [anchor_index]

    if anchor_index == 0:
        step = 1
        while len(unique_indices) < min(top_k, pool_size) and anchor_index + step < pool_size:
            unique_indices.append(anchor_index + step)
            step += 1
    elif anchor_index == pool_size - 1:
        step = 1
        while len(unique_indices) < min(top_k, pool_size) and anchor_index - step >= 0:
            unique_indices.append(anchor_index - step)
            step += 1
    else:
        step = 1
        while len(unique_indices) < min(top_k, pool_size):
            better_idx = anchor_index - step
            worse_idx = anchor_index + step
            if better_idx >= 0:
                unique_indices.append(better_idx)
            if len(unique_indices) >= min(top_k, pool_size):
                break
            if worse_idx < pool_size:
                unique_indices.append(worse_idx)
            step += 1

    if not unique_indices:
        unique_indices = [0]

    padded = list(unique_indices)
    cycle_base = list(unique_indices)
    cycle_idx = 0
    while len(padded) < top_k:
        padded.append(cycle_base[cycle_idx % len(cycle_base)])
        cycle_idx += 1
    return padded[:top_k]


def row_metric_contributions(row: pd.Series) -> dict:
    return {metric: float(row.get(f"{metric}_discipline_contribution", 0.0) or 0.0) for metric in METRIC_ORDER}


def row_metric_value_sources(row: pd.Series) -> dict:
    return {metric: str(row.get(f"{metric}_source", "unknown")) for metric in METRIC_ORDER}


def row_to_ranking_record(row: pd.Series, rank: int) -> dict:
    return {
        "rank": rank,
        "full_pool_rank": int(row.get("full_pool_rank", rank)),
        "clinician_id": to_json_safe_scalar(row["clinician_id"]),
        "clinician_name": row["clinician_name"],
        "discipline": row["discipline"],
        "discipline_score": float(row["discipline_score"]),
        "ranking_basis": row.get("ranking_basis", "vbp_weighted_score"),
        "outcome_volume_proxy": float(row.get("outcome_volume_proxy", 0.0) or 0.0),
        "survey_volume_proxy": float(row.get("survey_volume_proxy", 0.0) or 0.0),
        "metric_contributions": row_metric_contributions(row),
        "metric_value_sources": row_metric_value_sources(row),
        "all_metrics_observed": bool(row.get("all_metrics_observed", False)),
        "any_metric_imputed": bool(row.get("any_metric_imputed", True)),
        "imputation_confidence": str(row.get("imputation_confidence", "unknown")),
    }


def ranking_df_to_records(df: pd.DataFrame, top_k: int | None) -> List[dict]:
    if df.empty:
        return []
    limit = len(df) if top_k is None else min(int(top_k), len(df))
    out = []
    for rank, (_, row) in enumerate(df.head(limit).iterrows(), start=1):
        out.append(row_to_ranking_record(row, rank=rank))
    return out


def build_scored_discipline_candidates(
    discipline_df: pd.DataFrame,
    risk_percentile_u: float,
    discipline: str,
    top_k_groups: int,
) -> tuple[List[dict], dict]:
    if discipline_df.empty:
        raise RequestValidationError(f"Mirror matching requires at least one {discipline} clinician in the request pool.")

    pool_size = len(discipline_df)
    anchor_index = anchor_index_from_risk_percentile(risk_percentile_u, pool_size)
    selected_indices = candidate_indices_from_anchor(anchor_index, pool_size, top_k_groups)

    candidates = []
    for option_rank, idx in enumerate(selected_indices, start=1):
        row = discipline_df.iloc[idx]
        candidates.append(
            {
                "option_rank": option_rank,
                "selection_role": describe_relative_to_anchor(anchor_index, idx),
                "ranking_position": int(idx + 1),
                "full_pool_rank": int(row.get("full_pool_rank", idx + 1)),
                "clinician_id": to_json_safe_scalar(row["clinician_id"]),
                "clinician_name": row["clinician_name"],
                "discipline": row["discipline"],
                "discipline_score": float(row["discipline_score"]),
                "ranking_basis": row.get("ranking_basis", "vbp_weighted_score"),
                "metric_contributions": row_metric_contributions(row),
                "metric_value_sources": row_metric_value_sources(row),
                "imputation_confidence": str(row.get("imputation_confidence", "unknown")),
            }
        )

    context = {
        "discipline": discipline,
        "pool_size": pool_size,
        "anchor_ranking_position": int(anchor_index + 1),
        "selected_ranking_positions": [int(idx + 1) for idx in selected_indices],
        "anchor_formula": "anchor_index = round_half_up((1 - risk_percentile_u) * (n - 1)) where clinician index 0 is highest capability.",
        "neighbor_rule": "Primary anchor first, then one rank better, then one rank worse. At the edges, fan inward without leaving the pool.",
    }
    return candidates, context


def build_ot_candidates(
    ot_df: pd.DataFrame,
    request_id: str | None,
    patient_id: str | None,
    top_k_groups: int,
) -> tuple[List[dict | None], dict]:
    if top_k_groups <= 0:
        return [], {"strategy": "disabled", "pool_size": 0}
    if ot_df.empty:
        return [None for _ in range(top_k_groups)], {
            "strategy": "no_ot_available",
            "pool_size": 0,
            "note": "No OT clinicians were supplied in the request pool.",
        }

    ordered_df = ot_df.sort_values(["clinician_name", "clinician_id"], ascending=[True, True]).reset_index(drop=True)
    seed_text = f"{request_id or ''}|{patient_id or ''}|OT"
    offset = stable_hash_to_int(seed_text) % len(ordered_df)
    rotated_df = pd.concat([ordered_df.iloc[offset:], ordered_df.iloc[:offset]], axis=0).reset_index(drop=True)

    ot_candidates: List[dict] = []
    for idx in range(top_k_groups):
        row = rotated_df.iloc[idx % len(rotated_df)]
        ot_candidates.append(
            {
                "option_rank": idx + 1,
                "selection_role": "arbitrary_non_scored_ot_assignment",
                "ranking_position": int((idx % len(rotated_df)) + 1),
                "full_pool_rank": int(row.get("full_pool_rank", (idx % len(rotated_df)) + 1)),
                "clinician_id": to_json_safe_scalar(row["clinician_id"]),
                "clinician_name": row["clinician_name"],
                "discipline": row["discipline"],
                "discipline_score": None,
                "ranking_basis": "not_scored_for_vbp",
                "metric_contributions": {},
                "metric_value_sources": row_metric_value_sources(row),
                "imputation_confidence": str(row.get("imputation_confidence", "unknown")),
            }
        )
    context = {
        "discipline": "OT",
        "pool_size": int(len(ordered_df)),
        "strategy": "deterministic_rotation_unique_if_possible",
        "note": "OT is not scored for the current HHVBP objective. The service therefore assigns OT arbitrarily but deterministically, without repetition when the OT pool has at least three clinicians.",
        "rotation_offset": int(offset),
        "selected_names": [candidate["clinician_name"] for candidate in ot_candidates],
    }
    return ot_candidates, context


def build_recommended_groups(
    clinician_rankings: Dict[str, pd.DataFrame],
    severity_context: dict,
    request: dict,
    top_k_groups: int,
) -> tuple[List[dict], dict]:
    risk_percentile_u = float(severity_context["risk_percentile_u"])
    patient_id = request.get("patient", {}).get("patient_id")
    request_id = request.get("request_id")

    rn_candidates, rn_context = build_scored_discipline_candidates(
        clinician_rankings.get("RN", pd.DataFrame()), risk_percentile_u, discipline="RN", top_k_groups=top_k_groups
    )
    pt_candidates, pt_context = build_scored_discipline_candidates(
        clinician_rankings.get("PT", pd.DataFrame()), risk_percentile_u, discipline="PT", top_k_groups=top_k_groups
    )
    ot_candidates, ot_context = build_ot_candidates(
        clinician_rankings.get("OT", pd.DataFrame()), request_id=request_id, patient_id=patient_id, top_k_groups=top_k_groups
    )

    groups: List[dict] = []
    for option_idx in range(top_k_groups):
        rn_candidate = rn_candidates[option_idx] if option_idx < len(rn_candidates) else None
        pt_candidate = pt_candidates[option_idx] if option_idx < len(pt_candidates) else None
        ot_candidate = ot_candidates[option_idx] if option_idx < len(ot_candidates) else None

        scored_total = 0.0
        for candidate in [rn_candidate, pt_candidate]:
            if candidate and candidate.get("discipline_score") is not None:
                scored_total += float(candidate["discipline_score"])

        groups.append(
            {
                "option_rank": option_idx + 1,
                "selection_rule": "Mirrored percentile assignment on scored RN/PT rankings; OT assigned separately because OT does not affect the current HHVBP score.",
                "scored_disciplines_total": float(scored_total),
                "team": {
                    "RN": rn_candidate,
                    "PT": pt_candidate,
                    "OT": ot_candidate,
                },
            }
        )

    context = {
        "assignment_mode": "mirrored_percentile_three_options_v1",
        "top_k_groups": int(top_k_groups),
        "patient_severity_score": float(severity_context["risk_score_rho_raw"]),
        "risk_score_rho_raw": float(severity_context["risk_score_rho_raw"]),
        "risk_percentile_u": float(risk_percentile_u),
        "patient_severity_source": severity_context["source"],
        "rn_context": rn_context,
        "pt_context": pt_context,
        "ot_context": ot_context,
    }
    return groups, context


def resolve_threshold_routing(assignment_params: dict, options: dict | None = None) -> dict:
    options = options or {}
    cfg = dict(assignment_params.get("threshold_routing", {}) or {})
    enabled = bool(options.get("threshold_routing_enabled", cfg.get("enabled", False)))
    if not enabled:
        return {
            "enabled": False,
            "tau": None,
            "threshold_percentile": None,
            "threshold_source": "threshold_routing_disabled",
            "zip_history_enabled": False,
            "config": cfg,
        }

    if "risk_threshold" in options and options.get("risk_threshold") is not None:
        tau = normalize_unit_interval(options.get("risk_threshold"), "options.risk_threshold")
        threshold_source = options.get("threshold_source") or cfg.get("threshold_source") or "request_threshold_not_outcome_validated"
        if threshold_source == "unset_until_calibrated":
            threshold_source = "request_threshold_not_outcome_validated"
    elif cfg.get("risk_threshold") is not None:
        tau = normalize_unit_interval(cfg.get("risk_threshold"), "threshold_routing.risk_threshold")
        threshold_source = cfg.get("threshold_source") or "configured_threshold_not_outcome_validated"
    elif bool(cfg.get("allow_demo_default_threshold", False)):
        tau = normalize_unit_interval(cfg.get("demo_default_threshold", 0.75), "threshold_routing.demo_default_threshold")
        threshold_source = "demo_default_unvalidated"
    else:
        raise RequestValidationError(
            "threshold_routing is enabled, but no risk_threshold was supplied in request.options "
            "or config/assignment_parameters.json. Provide a threshold or explicitly enable the demo default."
        )

    return {
        "enabled": True,
        "tau": tau,
        "threshold_percentile": tau,
        "threshold_source": threshold_source,
        "zip_history_enabled": bool(cfg.get("zip_history_enabled", True)),
        "config": cfg,
    }


def filter_ranking_by_zip_history(ranking_df: pd.DataFrame, patient_zip: str | None) -> pd.DataFrame:
    if ranking_df.empty or patient_zip is None:
        return ranking_df.iloc[0:0].copy()
    if "zip_history_zip5s" not in ranking_df.columns:
        return ranking_df.iloc[0:0].copy()
    mask = ranking_df["zip_history_zip5s"].apply(lambda zips: patient_zip in set(zips or ()))
    return ranking_df.loc[mask].reset_index(drop=True)


def select_rankings_for_threshold_route(
    clinician_rankings: Dict[str, pd.DataFrame],
    severity_context: dict,
    routing_context: dict,
    patient_zip: str | None,
) -> tuple[Dict[str, pd.DataFrame], dict]:
    selected_rankings: Dict[str, pd.DataFrame] = {}
    route_by_discipline: dict[str, str] = {}
    full_pool_size_by_discipline: dict[str, int] = {}
    zip_pool_size_by_discipline: dict[str, int] = {}
    selected_pool_size_by_discipline: dict[str, int] = {}
    fallback_reason_by_discipline: dict[str, str | None] = {}

    rho_raw = float(severity_context["risk_score_rho_raw"])
    risk_percentile_u = float(severity_context["risk_percentile_u"])
    tau = routing_context.get("tau")
    cfg = routing_context.get("config", {})
    enabled = bool(routing_context.get("enabled", False))
    zip_history_enabled = bool(routing_context.get("zip_history_enabled", False))
    min_pool_size_by_discipline = cfg.get("min_pool_size_by_discipline", {}) or {}
    fallback_to_full_pool = bool(cfg.get("fallback_to_full_pool", True))

    for discipline in ALL_DISCIPLINES:
        full_pool = clinician_rankings.get(discipline, pd.DataFrame()).reset_index(drop=True)
        full_pool_size_by_discipline[discipline] = int(len(full_pool))
        zip_pool = filter_ranking_by_zip_history(full_pool, patient_zip)
        zip_pool_size = int(len(zip_pool))
        zip_pool_size_by_discipline[discipline] = zip_pool_size
        fallback_reason: str | None = None

        if not enabled:
            selected_pool = full_pool
            route = "routing_disabled_full_pool"
        elif tau is not None and risk_percentile_u > float(tau):
            selected_pool = full_pool
            route = "global_high_risk"
        elif not zip_history_enabled:
            selected_pool = full_pool
            route = "zip_history_disabled_full_pool"
        else:
            if patient_zip is None:
                selected_pool = full_pool
                route = "zip_history_fallback_full_pool"
                fallback_reason = "patient_zip_missing"
            else:
                min_pool_size = int(min_pool_size_by_discipline.get(discipline, 1))
                if zip_pool_size >= min_pool_size:
                    selected_pool = zip_pool
                    route = "zip_history"
                elif fallback_to_full_pool:
                    selected_pool = full_pool
                    route = "zip_history_fallback_full_pool"
                    fallback_reason = f"zip_pool_size {zip_pool_size} below minimum {min_pool_size}"
                else:
                    raise RequestValidationError(
                        f"{discipline} ZIP-history pool has {zip_pool_size} clinicians, below configured minimum {min_pool_size}, "
                        "and fallback_to_full_pool is false."
                    )

        selected_rankings[discipline] = selected_pool.reset_index(drop=True)
        route_by_discipline[discipline] = route
        selected_pool_size_by_discipline[discipline] = int(len(selected_pool))
        fallback_reason_by_discipline[discipline] = fallback_reason

    audit = {
        "enabled": enabled,
        "zip_history_enabled": zip_history_enabled,
        "rho": rho_raw,
        "risk_score_rho_raw": rho_raw,
        "risk_percentile_u": risk_percentile_u,
        "tau": None if tau is None else float(tau),
        "threshold_percentile": None if tau is None else float(tau),
        "threshold_raw_equivalent": routing_context.get("threshold_raw_equivalent"),
        "threshold_source": routing_context.get("threshold_source"),
        "patient_zip": patient_zip,
        "route_by_discipline": route_by_discipline,
        "full_pool_size_by_discipline": full_pool_size_by_discipline,
        "zip_pool_size_by_discipline": zip_pool_size_by_discipline,
        "selected_pool_size_by_discipline": selected_pool_size_by_discipline,
        "fallback_reason_by_discipline": fallback_reason_by_discipline,
        "capability_percentiles_scope": "within_discipline_on_full_request_pool_before_zip_filtering",
        "public_data_only": True,
        "production_validated": False,
        "threshold_provenance_label": "public_only_semi_synthetic_capacity_pilot_not_outcome_validated",
    }
    return selected_rankings, audit


def build_simple_assignment_output(
    recommended_groups: List[dict],
    value_key: str = "clinician_name",
) -> dict:
    """Return only the three selected clinicians per discipline.

    Output shape:
        {
          "RN": [...],
          "PT": [...],
          "OT": [...]
        }

    value_key can be either clinician_name or clinician_id.
    """
    if value_key not in {"clinician_name", "clinician_id"}:
        raise RequestValidationError("value_key must be 'clinician_name' or 'clinician_id'.")

    simple_output = {discipline: [] for discipline in ALL_DISCIPLINES}
    for group in recommended_groups:
        team = group.get("team", {})
        for discipline in ALL_DISCIPLINES:
            member = team.get(discipline)
            simple_output[discipline].append(to_json_safe_scalar(member.get(value_key)) if member else None)
    return simple_output


def assign_clinicians_simple(
    request: dict,
    config_dir: str | Path | None = None,
    value_key: str = "clinician_name",
) -> dict:
    full_response = assign_clinicians(request, config_dir=config_dir)
    return build_simple_assignment_output(
        full_response.get("recommended_groups", []),
        value_key=value_key,
    )


def summarize_parameter_versions(config_dir: Path) -> dict:
    files = ["hhvbp_global_config.json", "metric_parameter_library.json", "assignment_parameters.json"]
    return {
        filename: {
            "sha256": stable_sha256_for_file(config_dir / filename),
            "relative_path": f"config/{filename}",
        }
        for filename in files
    }


def assign_clinicians(request: dict, config_dir: str | Path | None = None) -> dict:
    config_dir = resolve_config_dir(config_dir)
    assignment_params = load_assignment_parameters(config_dir)
    canonical_metric_aliases = load_canonical_metric_aliases(config_dir)
    effective_weights = build_effective_discipline_metric_weights(assignment_params)
    scored_disciplines = determine_scored_disciplines(assignment_params)

    validate_request_shape(request)
    options = request.get("options", {})
    patient_obj = request["patient"]
    patient_profile = extract_patient_profile(patient_obj)
    patient_zip = extract_patient_zip(patient_obj)
    patient_need_df = build_patient_need_vector(config_dir, patient_profile)
    from hhvbp_risk_reference import load_risk_reference, quantile_raw

    reference_relative_path = assignment_params.get("threshold_routing", {}).get(
        "risk_reference_relative_path", "data/reference/hhvbp_risk_reference.json"
    )
    reference_path = Path(reference_relative_path)
    if not reference_path.is_absolute():
        reference_path = config_dir.parent / reference_path
    risk_reference = load_risk_reference(
        reference_path if reference_path.exists() else None,
        config_dir=config_dir,
    )
    severity_context = build_patient_severity_context(
        patient_need_df,
        patient_obj,
        request.get("options", {}),
        risk_reference=risk_reference,
    )

    raw_clinician_df, raw_summary = clinician_records_to_dataframe(
        request, assignment_params, metric_aliases=canonical_metric_aliases
    )
    zip_history_by_id, zip_parser_audit = build_clinician_zip_history_map(request, raw_clinician_df)
    prepared_df, imputation_summary = prepare_clinician_panel(raw_clinician_df, assignment_params)
    prepared_df = attach_zip_history_to_panel(prepared_df, zip_history_by_id)
    clinician_rankings = compute_clinician_rankings(prepared_df, patient_need_df, effective_weights, scored_disciplines)
    routing_context = resolve_threshold_routing(assignment_params, options)
    if routing_context.get("tau") is not None:
        routing_context["threshold_raw_equivalent"] = quantile_raw(
            risk_reference.get("sorted_risk_scores", []), float(routing_context["tau"])
        )
    routed_rankings, threshold_routing_audit = select_rankings_for_threshold_route(
        clinician_rankings=clinician_rankings,
        severity_context=severity_context,
        routing_context=routing_context,
        patient_zip=patient_zip,
    )

    defaults = assignment_params.get("matching_defaults", {})
    top_k_per_discipline_raw = options.get("top_k_per_discipline", defaults.get("top_k_per_discipline"))
    top_k_per_discipline = None if top_k_per_discipline_raw is None else int(top_k_per_discipline_raw)
    top_k_groups = int(options.get("top_k_groups", defaults.get("top_k_groups", 3)))

    recommended_groups, mirror_context = build_recommended_groups(
        clinician_rankings=routed_rankings,
        severity_context=severity_context,
        request=request,
        top_k_groups=top_k_groups,
    )
    threshold_routing_audit["zip_parser_audit"] = zip_parser_audit
    threshold_routing_audit["recommended_clinician_ids"] = build_simple_assignment_output(
        recommended_groups, value_key="clinician_id"
    )
    threshold_routing_audit["recommended_clinician_names"] = build_simple_assignment_output(
        recommended_groups, value_key="clinician_name"
    )
    threshold_routing_audit["selected_clinician_ranks"] = {
        discipline: [
            {
                "option_rank": int(group["option_rank"]),
                "selected_pool_rank": int(group["team"][discipline]["ranking_position"]),
                "full_pool_rank": int(group["team"][discipline]["full_pool_rank"]),
                "clinician_id": to_json_safe_scalar(group["team"][discipline]["clinician_id"]),
            }
            for group in recommended_groups
            if group.get("team", {}).get(discipline) is not None
        ]
        for discipline in ALL_DISCIPLINES
    }
    data_origins = sorted(str(value) for value in prepared_df["data_origin"].dropna().unique())
    threshold_routing_audit["value_provenance"] = {
        "patient_factors": "observed_or_request_supplied",
        "clinician_metric_imputation_by_metric": imputation_summary,
        "clinician_data_origins": data_origins,
        "contains_synthetic_clinicians": any("synthetic" in value.lower() for value in data_origins),
    }

    global_cfg = load_json(config_dir / "hhvbp_global_config.json")
    cms_version_lock = {
        key: global_cfg.get(key)
        for key in ["cms_model", "performance_year", "payment_year", "measure_set_version", "cms_version_locked"]
    }

    response = {
        "schema_version": assignment_params.get("schema_version", "2.0.0"),
        "assignment_mode": (
            "threshold_routed_mirrored_percentile_three_options_v1"
            if threshold_routing_audit["enabled"]
            else "mirrored_percentile_three_options_v1"
        ),
        "request_id": request.get("request_id"),
        "scope_note": assignment_params.get("scope_note"),
        "cms_version_lock": cms_version_lock,
        "patient": {
            "patient_id": patient_obj.get("patient_id"),
            "local_externalities": patient_profile,
            "age_group": patient_profile.get("age_group"),
            "patient_zip": patient_zip,
            "severity_score_override": patient_obj.get("severity_score_override"),
        },
        "input_summary": raw_summary,
        "preparation_summary": {
            "imputation_by_metric": imputation_summary,
            "capability_ranking_scope": "Within discipline on the full request pool before ZIP filtering.",
            "roster_sensitivity_note": "The default normalization reference is the full request roster within each discipline; ranks can change when that same-discipline roster changes.",
            "shrinkage": assignment_params.get("shrinkage", {}),
            "shrinkage_enabled": bool(assignment_params.get("enable_shrinkage", False)),
            "ranking_uncertainty": "Unidentifiable from available production data when metric-specific sample sizes are absent; synthetic counts are simulation-only.",
        },
        "parameter_versions": summarize_parameter_versions(config_dir),
        "effective_discipline_metric_weights": effective_weights,
        "applied_options": {
            "top_k_per_discipline": top_k_per_discipline,
            "top_k_groups": top_k_groups,
            "severity_score_override": request.get("options", {}).get("severity_score_override"),
            "risk_threshold": threshold_routing_audit["tau"],
            "threshold_source": threshold_routing_audit["threshold_source"],
            "ot_assignment_strategy": options.get("ot_assignment_strategy", defaults.get("ot_assignment_strategy", "deterministic_rotation_unique_if_possible")),
        },
        "patient_need": patient_need_df.to_dict(orient="records"),
        "patient_severity": severity_context,
        "threshold_routing": threshold_routing_audit,
        "assignment_audit": {
            "risk_score_rho_raw": severity_context["risk_score_rho_raw"],
            "risk_percentile_u": severity_context["risk_percentile_u"],
            "threshold_percentile": threshold_routing_audit["threshold_percentile"],
            "threshold_raw_equivalent": threshold_routing_audit["threshold_raw_equivalent"],
            "threshold_source": threshold_routing_audit["threshold_source"],
            "patient_zip": patient_zip,
            "route_by_discipline": threshold_routing_audit["route_by_discipline"],
            "full_pool_size": threshold_routing_audit["full_pool_size_by_discipline"],
            "zip_pool_size": threshold_routing_audit["zip_pool_size_by_discipline"],
            "selected_pool_size": threshold_routing_audit["selected_pool_size_by_discipline"],
            "fallback_reason": threshold_routing_audit["fallback_reason_by_discipline"],
            "selected_clinician_ranks": threshold_routing_audit["selected_clinician_ranks"],
            "value_provenance": threshold_routing_audit["value_provenance"],
            "risk_reference": {
                "reference_id": risk_reference["metadata"]["reference_id"],
                "config_hash": risk_reference["metadata"]["config_hash"],
                "config_file_hashes_sha256": risk_reference["metadata"]["config_file_hashes_sha256"],
                "sample_size": int(risk_reference["metadata"]["sample_size"]),
                "artifact_path": risk_reference["artifact_path"],
                "artifact_sha256": risk_reference["artifact_sha256"],
            },
            "public_data_only": True,
            "production_validated": False,
        },
        "discipline_rankings": {
            discipline: ranking_df_to_records(clinician_rankings.get(discipline, pd.DataFrame()), top_k_per_discipline)
            for discipline in ALL_DISCIPLINES
        },
        "routed_discipline_rankings": {
            discipline: ranking_df_to_records(routed_rankings.get(discipline, pd.DataFrame()), top_k_per_discipline)
            for discipline in ALL_DISCIPLINES
        },
        "mirrored_assignment_context": mirror_context,
        "recommended_groups": recommended_groups,
        "recommended_team": recommended_groups[0] if recommended_groups else None,
        "team_rankings": recommended_groups,
        "team_rankings_note": "In schema_version 2.0.0, team_rankings is kept only as a backward-compatible alias for recommended_groups. It no longer represents additive cartesian team enumeration.",
    }
    return response


def assign_with_audit(request: dict, config_dir: str | Path | None = None) -> dict:
    """Internal rich-audit entry point; the simplified backend contract is unchanged."""
    return assign_clinicians(request=request, config_dir=config_dir)


def load_request(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(obj: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    return path
