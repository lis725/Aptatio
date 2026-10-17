"""Concise agency wording derived only from one result's recorded evidence."""
import hashlib
import json
import math
from pathlib import Path

READABLE = {
    "health_status": {"healthy": "Healthy", "moderate": "Moderate", "unhealthy": "Unhealthy"},
    "house_price_zip": {"high": "High", "average": "Average", "low": "Low"},
    "area_type": {"micropolitan": "Micropolitan", "metropolitan": "Metropolitan", "small_town": "Small town", "rural": "Rural"},
    "housing_type": {"single_home": "Single home", "apartment": "Apartment"},
    "distance_to_clinic": {"near": "Near", "medium": "Medium distance", "far": "Far"},
    "driving_condition": {"good_summer": "Favorable driving conditions (summer)", "bad_winter": "Difficult driving conditions (winter)"},
}


def age_label(bounds):
    if not isinstance(bounds, dict) or "minimum" not in bounds or "maximum" not in bounds:
        return "Age category label unavailable"
    low, high = bounds["minimum"], bounds["maximum"]
    if high is None:
        return f"{low:g} and over" if bounds.get("minimum_inclusive") else f"Over {low:g}"
    if low is None:
        return f"Up to {high:g}" if bounds.get("maximum_inclusive") else f"Under {high:g}"
    start = f"{low:g}" if bounds.get("minimum_inclusive") else f"Over {low:g}"
    end = f"{high:g}" if bounds.get("maximum_inclusive") else f"under {high:g}"
    return f"{start} to {end}"


def readable_factors(full, profile, config_dir):
    boundaries = {}
    # Resolve against the caller's actual configuration, then verify result-bound bytes.
    # Missing/mutated config never falls back to arbitrary default boundaries.
    if config_dir is not None:
        try:
            raw = (Path(config_dir) / "hhvbp_global_config.json").read_bytes()
            expected = full["parameter_versions"]["hhvbp_global_config.json"]["sha256"]
            if hashlib.sha256(raw).hexdigest() == expected:
                boundaries = json.loads(raw)["age_configuration"]["numeric_age_boundaries"]
        except (OSError, KeyError, TypeError, ValueError):
            pass
    return {key: age_label(boundaries.get(value)) if key == "age_group" else
            READABLE.get(key, {}).get(value, "Category label unavailable" if value is not None else "Not available")
            for key, value in profile.items()}


def percentile_text(value, threshold=None):
    if value is None or not math.isfinite(value):
        return "Unavailable"
    percent = value * 100
    decimals = 2 if 0 < percent < 1 or 99 < percent < 100 else 1
    if threshold is not None and value != threshold and abs(value - threshold) < 0.001:
        decimals = max(decimals, 4)
    while decimals < 8 and ((0 < percent < 1 and round(percent, decimals) == 0)
                            or (99 < percent < 100 and round(percent, decimals) == 100)):
        decimals += 1
    if 0 < percent < 10 ** -8 or 0 < 100 - percent < 10 ** -8:
        return format(percent, ".16g")
    return f"{percent:.{decimals}f}"


def threshold_relation(value, threshold, enabled=True):
    if not enabled:
        return "Routing disabled"
    if value is None or threshold is None:
        return "Threshold relation unavailable"
    return ("Above" if value > threshold else "At" if value == threshold else "Below") + " RN/PT threshold"


def _routing_clause(label, route):
    evidence = route.get("evidence", {})
    name = evidence.get("route")
    if route.get("status") == "unavailable":
        return f"{label} routing evidence is unavailable."
    if name == "zip_history":
        return f"{label} are selected from clinicians with service history in this ZIP."
    if name == "global_high_risk":
        return f"{label} are selected from the full supplied roster using need-matched score positions."
    if name == "zip_history_fallback_full_pool":
        why = "the patient ZIP is missing" if evidence.get("fallback_reason") == "patient_zip_missing" else "the ZIP-history pool is too small"
        return f"{label} use the full roster because {why}."
    if name == "routing_disabled_full_pool":
        return f"{label} use the full roster because routing is disabled."
    if name == "zip_history_disabled_full_pool":
        return f"{label} use the full roster because ZIP filtering is disabled."
    return f"{label} selection uses the recorded candidate pool; routing details are unavailable."


def decision_paragraph(routes, relation):
    rn, pt = routes.get("RN", {}), routes.get("PT", {})
    keys = ("route", "fallback_reason")
    same = all(rn.get("evidence", {}).get(k) == pt.get("evidence", {}).get(k) for k in keys) and rn.get("status") == pt.get("status")
    sentence = _routing_clause("RN/PT", rn) if same else _routing_clause("RN", rn) + " " + _routing_clause("PT", pt)
    disabled = any(r.get("evidence", {}).get("route") in ("routing_disabled_full_pool", "zip_history_disabled_full_pool") for r in (rn, pt))
    if not disabled and relation.startswith(("Above", "At", "Below")):
        sentence = relation.replace(" RN/PT", " the RN/PT") + "; " + sentence
    return sentence


def concise_card(card):
    c = card.copy();e = c["selection_evidence"];r = c["routing_evidence"];d = c["discipline"]
    if c["status"] == "unavailable":
        c["summary"] = "The assignment is retained; aligned selection evidence is unavailable."
    elif c["clinician_id"] is None:
        c["summary"] = "No OT was supplied." if d == "OT" and r.get("full_pool_size") == 0 else f"No {d} clinician was returned for this slot."
    elif d == "OT":
        name = r.get("route")
        if name == "zip_history":
            text = "Selected from OTs with recorded service history in this ZIP."
        elif name == "global_high_risk":
            text = "Need is above the configured threshold; selected from the full supplied OT roster."
        elif name == "zip_history_fallback_full_pool":
            text = ("Patient ZIP is missing; selected from the full OT roster." if r.get("fallback_reason") == "patient_zip_missing" else
                    "No OT matches this ZIP; selected from the full OT roster.")
        elif name == "routing_disabled_full_pool":
            text = "Routing is disabled; selected from the full supplied OT roster."
        elif name == "zip_history_disabled_full_pool":
            text = "ZIP filtering is disabled; selected from the full OT roster."
        else:
            text = "Selected from the recorded OT pool; routing details are unavailable."
        if e.get("repeated_in_options") and r.get("selected_pool_size", 0) > 1:
            text += " This OT repeats because distinct candidates are limited."
        c["summary"] = text
    else:
        pool = "ZIP-history pool" if r.get("route") == "zip_history" else "full supplied roster"
        if e.get("repeated_in_options"):
            text = "Repeated score-position candidate because distinct clinicians in the selected pool are limited."
        elif c["option_rank"] == 1:
            text = f"Primary score-position match for the patient's modeled need within the {pool}."
        else:
            delta = e.get("selected_pool_rank", 0) - e.get("anchor_rank", 0)
            direction = "higher" if delta < 0 else "lower"
            if abs(delta) == 1:
                text = f"Adjacent {direction} score-position alternative to the primary match within the {pool}."
            elif delta:
                text = f"Alternative {abs(delta)} score positions {direction} than the primary match within the {pool}."
            else:
                text = "The primary score-position match repeats because distinct candidates in the selected pool are limited."
        if e.get("score_tie_count_selected_pool", 0) > 1:
            text += " Tied scores use deterministic ordering."
        c["summary"] = text
    return c


def page_presentation(full, distribution, routes, options):
    routing = full.get("threshold_routing", {})
    threshold = routing.get("threshold_percentile")
    used = distribution["assignment_percentile"]
    relation = threshold_relation(used, threshold, routing.get("enabled", False))
    notices = []
    if distribution["raw_score_override_used"] or distribution["percentile_override_used"]:
        notices.append("Test override active.")
        if distribution["raw_score_override_used"]:
            notices.append("The raw-score marker was supplied explicitly, not derived from the listed factors.")
        if distribution["percentile_override_used"]:
            notices.append("Assignment percentile: " + percentile_text(used, threshold) + "; the reference percentile describes the actual raw-score marker.")
    if distribution["status"] == "unavailable":
        notices.append("Reference distribution unavailable; assignments are retained.")
    shared = []
    ot = routes.get("OT", {}).get("evidence", {})
    if len(options) > 1 and ot.get("selected_pool_size") == 1:
        shared.append("Only one OT matches this ZIP, so the same OT appears in each option." if ot.get("route") == "zip_history" else
                      "Only one OT is in the selected pool, so the same OT appears in each option.")
    if any(m.get("value_source", "").startswith("imputed_") for option in options for card in option["team"].values() for m in card["metric_evidence"]):
        shared.append("Some clinician measures use imputed values.")
    return {"score": f"{distribution['patient_raw_score']:.3f}" if isinstance(distribution['patient_raw_score'], (int,float)) else "Unavailable",
            "percentile": percentile_text(distribution["patient_percentile"], threshold),
            "threshold_percentile": percentile_text(threshold), "relation": relation,
            "decision_paragraph": decision_paragraph(routes, relation), "position_notices": notices,
            "assignment_notices": shared}
