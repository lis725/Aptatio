"""Read-only evidence, reason codes, and deterministic agency-facing rendering."""
from __future__ import annotations

import copy
import math
import re
from decimal import Decimal
from typing import Any

from hhvbp_local_externality import METRIC_ORDER, RAW_DIRECTION

SCHEMA_VERSION = "assignment_explanations.v2"
TEMPLATE_VERSION = "en.v2"
SCORE_ABS_TOLERANCE = 1e-12
SCORE_REL_TOLERANCE = 1e-10
DISCIPLINES = ("RN", "PT", "OT")
ROUTE_CODES = {
    "global_high_risk": "HIGH_NEED_FULL_POOL",
    "zip_history": "ZIP_HISTORY_POOL",
    "zip_history_fallback_full_pool": "ZIP_POOL_FALLBACK",
    "routing_disabled_full_pool": "ROUTING_DISABLED",
    "zip_history_disabled_full_pool": "ZIP_HISTORY_DISABLED",
}


class ExplanationEvidenceUnavailable(ValueError):
    """An expected, bounded evidence gap; contains no request or patient details."""


def counted(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def ordinal_percentile(value: float, *, exact: bool = False) -> str:
    """Readable percentiles; exact mode avoids rounding strict route boundaries."""
    percent = Decimal(str(value)) * 100
    text = format(percent, "f") if exact else format(percent, ".2f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if Decimal(text) == Decimal(text).to_integral_value():
        number = int(Decimal(text))
        suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    else:
        suffix = "th"
    return f"{text}{suffix} percentile"


def route_percentiles(percentile: float, threshold: float) -> tuple[str, str]:
    left, right = ordinal_percentile(percentile), ordinal_percentile(threshold)
    if percentile != threshold and left == right:
        left, right = ordinal_percentile(percentile, exact=True), ordinal_percentile(threshold, exact=True)
    return left, right


def finite(value: Any) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def json_safe(value: Any) -> Any:
    """Map nonfinite evidence to explicit null; never stringify arbitrary objects."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if hasattr(value, "item"):
        return json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def origin_evidence(value: Any) -> dict:
    # Only expose a bounded source label, never a supplied path or arbitrary object.
    label = value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_. -]{1,120}", value) else None
    normalized = (label or "").lower()
    if not normalized or normalized in {"integration_request", "unknown", "none"}:
        kind = "unknown"
    elif normalized.startswith("mixed"):
        kind = "mixed_declared"
    elif normalized.startswith(("synthetic", "public_synthetic", "semi_synthetic", "public_semi_synthetic")):
        kind = "synthetic_declared"
    else:
        kind = "supplied_label_unverified"
    return {"label": label, "classification": kind, "independently_verified": False}


def capture_assignment_evidence(
    rankings: dict, routed_rankings: dict, groups: list[dict], patient_need: Any,
    effective_weights: dict, params: dict, routing_context: dict, options: dict,
    global_config: dict, aliases: dict, reference_metadata: dict,
    *, patient_zip: str | None = None,
) -> dict:
    """Serialize existing computations after selection, before display truncation.

    No scoring, routing, selection, or reference fitting occurs here.
    Positional lookup avoids joining selected records on clinician_id alone.
    """
    needs = {r["metric_key"]: r for r in patient_need.to_dict(orient="records")}
    metric_metadata = {
        m: {"display_name": needs[m]["display_name"],
            "readable_name": max(aliases.get(m, [needs[m]["display_name"]]), key=lambda s: (len(s), s)),
            "direction": RAW_DIRECTION[m]}
        for m in METRIC_ORDER
    }
    cfg = routing_context.get("config", {})
    if not routing_context["enabled"]:
        threshold_source = "disabled"
    elif options.get("risk_threshold") is not None:
        threshold_source = "options.risk_threshold"
    elif cfg.get("risk_threshold") is not None:
        threshold_source = "config.threshold_routing.risk_threshold"
    else:
        threshold_source = "config.threshold_routing.demo_default_threshold"
    slots = []
    for group in groups:
        for discipline in DISCIPLINES:
            member = group["team"].get(discipline)
            pool = routed_rankings[discipline]
            full = rankings[discipline]
            slot = {
                "option_rank": group["option_rank"], "discipline": discipline,
                "clinician_id": member["clinician_id"] if member else None,
                "clinician_name": member["clinician_name"] if member else None,
                "full_pool_records": len(full),
                "zip_pool_records": int(full["zip_history_zip5s"].apply(
                    lambda zips: patient_zip in set(zips or ())
                ).sum()) if len(full) and patient_zip is not None else 0,
                "selected_pool_records": len(pool),
                "distinct_candidates": int(pool["clinician_id"].nunique()) if len(pool) else 0,
                "metrics": [],
            }
            if member is None:
                slot["status"] = "complete"
                slots.append(slot)
                continue
            if discipline == "OT":
                matches = pool.loc[(pool["clinician_id"] == member["clinician_id"]) &
                                   (pool["clinician_name"] == member["clinician_name"])]
                # OT does not use metric rankings. Conflicting duplicate labels
                # cannot establish a unique data origin.
                origins = [origin_evidence(v) for v in matches["data_origin"].tolist()]
                slot["data_origin"] = origins[0] if origins and all(v == origins[0] for v in origins) else origin_evidence(None)
                slot["status"] = "complete"
                slots.append(slot)
                continue
            row = pool.iloc[member["ranking_position"] - 1]
            if row["clinician_id"] != member["clinician_id"] or row["clinician_name"] != member["clinician_name"]:
                slot["status"] = "unavailable"
                slots.append(slot)
                continue
            slot["data_origin"] = origin_evidence(row.get("data_origin"))
            slot["score_tie_count_selected_pool"] = int((pool["discipline_score"] == row["discipline_score"]).sum())
            slot["score_tie_count_full_pool"] = int((full["discipline_score"] == row["discipline_score"]).sum())
            for metric in METRIC_ORDER:
                need = needs[metric]
                supplied = finite(row[f"_explanation_input_{metric}"])
                observed_values = [finite(v) for v in full[f"_explanation_input_{metric}"]]
                observed_values = [v for v in observed_values if v is not None]
                tied = sum(v == supplied for v in observed_values) - (1 if supplied is not None else 0)
                less_favorable = None
                if supplied is not None:
                    less_favorable = sum(
                        v > supplied if RAW_DIRECTION[metric] == "lower_is_better" else v < supplied
                        for v in observed_values
                    )
                contribution = finite(row[f"{metric}_discipline_contribution"])
                ready = [finite(v) for v in full[f"{metric}_score_ready"]]
                ready = [v for v in ready if v is not None]
                slot["metrics"].append({
                    "metric_key": metric, **metric_metadata[metric],
                    "metric_weight": float(need["metric_weight"]),
                    "modeled_need": float(need["need_mean"]),
                    "weighted_patient_need": float(need["weighted_need_mean"]),
                    "effective_discipline_weight": float(effective_weights[metric][discipline]),
                    "normalized_supplied_value": supplied,
                    "imputed_or_supplied_value": finite(row[metric]),
                    "value_for_scoring": finite(row[f"{metric}_score_ready"]),
                    "value_source": str(row[f"{metric}_source"]),
                    "shrinkage_enabled": bool(params.get("enable_shrinkage", False)),
                    "scoring_adjustment_applied": bool(params.get("enable_shrinkage", False)) and
                        finite(row[f"{metric}_score_ready"]) != finite(row[metric]),
                    "scoring_adjustment_weight": finite(row[f"{metric}_lambda"]),
                    "capability_percentile": finite(row[f"{metric}_capability"]),
                    "reference_pool_size": len(full),
                    "reference_distinct_scoring_values": len(set(ready)),
                    "reference_scope": "full_same_discipline_request_roster_before_zip_filtering",
                    "observed_reference_count": len(observed_values),
                    "observed_less_favorable_count": less_favorable,
                    "observed_tied_peer_count": max(tied, 0) if supplied is not None else None,
                    "contribution": contribution,
                    "distinct_observed_advantage": bool(
                        supplied is not None and less_favorable and tied == 0
                        and contribution is not None and contribution > 0
                    ),
                })
            slot["status"] = "complete" if all(
                m["value_for_scoring"] is not None and m["capability_percentile"] is not None
                and m["contribution"] is not None for m in slot["metrics"]
            ) else "partial"
            slots.append(slot)
    # Internal evidence for the fixed historical reference, captured from the
    # FULL rankings before display truncation. It is not a public roster export.
    reference_records = {
        discipline: [{
            "clinician_id": row["clinician_id"],
            "clinician_name": row["clinician_name"],
            "data_origin": origin_evidence(row.get("data_origin")),
            "metrics": {metric: {
                "normalized_supplied_value": finite(row[f"_explanation_input_{metric}"]),
                "value_source": str(row[f"{metric}_source"]),
                "value_for_scoring": finite(row[f"{metric}_score_ready"]),
            } for metric in METRIC_ORDER},
        } for _, row in rankings[discipline].iterrows()]
        for discipline in ("RN", "PT")
    }
    return json_safe({
        "schema_version": "assignment_evidence.v2",
        "reference_records": reference_records,
        "factor_order": global_config["factor_order"],
        "metric_metadata": metric_metadata,
        "threshold_value_source": threshold_source,
        "minimum_zip_pool": {d: int(cfg.get("min_pool_size_by_discipline", {}).get(d, 1)) for d in DISCIPLINES},
        "reference_source_mode": reference_metadata.get("source_mode"),
        "reference_sampling_method": reference_metadata.get("sampling_method"),
        "shrinkage_enabled": bool(params.get("enable_shrinkage", False)),
        "selected_slots": slots,
    })


def binding(decision_id: str, rank: int, discipline: str, member: dict | None) -> dict:
    return {"decision_id": decision_id, "option_rank": rank, "discipline": discipline,
            "clinician_id": member.get("clinician_id") if member else None}


def unavailable_slot(decision_id: str, rank: int, discipline: str,
                     member: dict | None, assignment_value: Any) -> dict:
    return {
        **binding(decision_id, rank, discipline, member),
        "clinician_name": member.get("clinician_name") if member else None,
        "assignment_value": assignment_value, "status": "unavailable",
        "summary": "The assignment is retained, but aligned explanation evidence is unavailable.",
        "reason_codes": ["EXPLANATION_EVIDENCE_UNAVAILABLE"], "evidence": {},
        "warnings": ["No personalized selection reason was inferred from missing or mismatched evidence."],
    }


def render_route(evidence: dict, discipline: str) -> str:
    route = evidence["route"]
    count = evidence["selected_pool_size"]
    full = f"the full same-discipline roster supplied in this request ({counted(count, discipline + ' record')})"
    if route == "global_high_risk":
        percentile, threshold = route_percentiles(evidence["risk_percentile"], evidence["threshold_percentile"])
        return (f"The patient's modeled need is at the {percentile}, which strictly exceeds "
                f"the {threshold} threshold, so this uses {full}.")
    if route == "zip_history":
        percentile, threshold = route_percentiles(evidence["risk_percentile"], evidence["threshold_percentile"])
        return (f"The patient's modeled need is at the {percentile}, at or below the {threshold} threshold. "
                f"The ZIP-history pool has {counted(evidence['zip_pool_size'], 'record')}, meeting the minimum of {evidence['minimum_zip_pool']}.")
    if route == "zip_history_fallback_full_pool":
        if evidence["fallback_reason"] == "patient_zip_missing":
            return f"No patient ZIP was supplied, so this falls back to {full}."
        if discipline == "OT":
            if evidence["full_pool_size"] == 0:
                return "No OT clinician was supplied in the full request; the permitted fallback therefore leaves the OT slot empty."
            return f"No OT has recorded service history in the patient's ZIP, so the permitted fallback uses {full}."
        return (f"The ZIP-history pool has {counted(evidence['zip_pool_size'], 'record')}, below the required "
                f"{evidence['minimum_zip_pool']}; this falls back to {full}.")
    if route == "routing_disabled_full_pool":
        return f"Routing is disabled; this uses {full}."
    if route == "zip_history_disabled_full_pool":
        return f"ZIP-history filtering is disabled; this uses {full}."
    raise ExplanationEvidenceUnavailable("Unsupported route evidence.")


def routing_explanations(full: dict, captured: dict) -> dict:
    routing = full.get("threshold_routing", {})
    result = {}
    for discipline in DISCIPLINES:
        try:
            evidence = {
                "route": routing["route_by_discipline"][discipline],
                "risk_percentile": routing["risk_percentile_u"],
                "threshold_percentile": routing["threshold_percentile"],
                "threshold_value_source": captured["threshold_value_source"],
                "threshold_provenance_label": origin_evidence(routing["threshold_source"])["label"],
                "full_pool_size": routing["full_pool_size_by_discipline"][discipline],
                "zip_pool_size": routing["zip_pool_size_by_discipline"][discipline],
                "selected_pool_size": routing["selected_pool_size_by_discipline"][discipline],
                "minimum_zip_pool": captured["minimum_zip_pool"][discipline],
                "fallback_reason": routing["fallback_reason_by_discipline"][discipline],
                "patient_zip": routing["patient_zip"],
                "routing_policy": "threshold_routing.v1",
            }
            if evidence["route"] not in ROUTE_CODES:
                raise ExplanationEvidenceUnavailable("Unsupported route evidence.")
            codes = [ROUTE_CODES[evidence["route"]]]
            if evidence["threshold_value_source"] == "options.risk_threshold":
                codes.append("THRESHOLD_OVERRIDE_USED")
            if evidence["fallback_reason"] == "patient_zip_missing":
                codes.append("PATIENT_ZIP_MISSING")
            summary = render_route(evidence, discipline)
            if "THRESHOLD_OVERRIDE_USED" in codes:
                summary += " The applied threshold was overridden for this request."
            result[discipline] = {"status": "complete", "summary": summary,
                                  "reason_codes": codes, "evidence": evidence}
        except (KeyError, ExplanationEvidenceUnavailable):
            result[discipline] = {"status": "unavailable", "summary": "Candidate-pool evidence is unavailable.",
                                  "reason_codes": ["EXPLANATION_EVIDENCE_UNAVAILABLE"], "evidence": {}}
    return result


def patient_context(full: dict, captured: dict) -> dict:
    try:
        severity = full["patient_severity"]
        inputs = {factor: full["patient"]["local_externalities"][factor] for factor in captured["factor_order"]}
        priorities = sorted(full["patient_need"], key=lambda r: (-r["weighted_need_mean"], METRIC_ORDER.index(r["metric_key"])))
        priorities = [
            {"metric_key": row["metric_key"], **captured["metric_metadata"][row["metric_key"]],
             "modeled_need": row["need_mean"], "metric_weight": row["metric_weight"],
             "weighted_patient_need": row["weighted_need_mean"]}
            for row in [r for r in priorities if r["weighted_need_mean"] > 0][:3]
        ]
        raw_source = severity["source"]
        percentile_source = severity["risk_percentile_source"]
        raw_override = raw_source in {"patient.severity_score_override", "options.severity_score_override"}
        percentile_override = percentile_source == "options.risk_percentile_override"
        summary = f"The patient's modeled need is at the {ordinal_percentile(severity['risk_percentile_u'])} among the configured reference profiles; this is not an event probability."
        if raw_override or percentile_override:
            summary += " A debug override controls " + (
                "both the raw index and percentile." if raw_override and percentile_override else
                "the raw index used to obtain that percentile." if raw_override else "the percentile used for routing and positioning."
            )
        else:
            summary += " It was derived from the supplied patient factors."
        summary += (" Leading priorities after metric weighting are " + ", ".join(p["readable_name"] for p in priorities) + "."
                    if priorities else " No positive metric priority is established because all weighted needs are zero.")
        return {
            "status": "complete", "summary": summary, "normalized_inputs": inputs,
            "risk_score_raw": severity["risk_score_rho_raw"],
            "risk_percentile": severity["risk_percentile_u"],
            "raw_index_source": raw_source, "percentile_source": percentile_source,
            "raw_index_override_used": raw_override, "percentile_override_used": percentile_override,
            "reason_codes": ["DEBUG_OVERRIDE_USED"] if raw_override or percentile_override else [],
            "priority_rule": "Top three positive weighted_need_mean values, descending; ties follow METRIC_ORDER. These are metric priorities, not factor attributions.",
            "metric_priorities": priorities,
            "reference_description": {
                "source_mode": captured.get("reference_source_mode"),
                "sampling_method": captured.get("reference_sampling_method"),
                "sample_size": full.get("assignment_audit", {}).get("risk_reference", {}).get("sample_size"),
                "interpretation": "Relative position in the configured reference profiles, not a validated individual outcome probability.",
            },
            "warnings": ["Area-level housing prices do not measure this patient's personal wealth. No largest individual factor driver is attributed."],
        }
    except KeyError:
        return {"status": "unavailable", "summary": "Patient-context evidence is unavailable.",
                "reason_codes": ["EXPLANATION_EVIDENCE_UNAVAILABLE"]}


def select_reason_codes(discipline: str, member: dict | None, evidence: dict,
                        route: dict, rank: int, group_count: int) -> list[str]:
    codes = list(route["reason_codes"])
    if member is None:
        if discipline == "OT":
            return codes + (["NO_OT_SUPPLIED"] if evidence.get("full_pool_records") == 0
                            else ["OT_SELECTED_POOL_EMPTY"] if evidence.get("selected_pool_records") == 0
                            else ["NO_CANDIDATE_SELECTED"])
        return codes + ["NO_CANDIDATE_SELECTED"]
    codes.append("OT_DETERMINISTIC_ROTATION" if discipline == "OT" else
                 "MIRROR_PRIMARY" if rank == 1 else "MIRROR_ALTERNATIVE")
    if evidence["distinct_candidates"] < group_count:
        codes.append("LIMITED_DISTINCT_CANDIDATES")
    kind = evidence.get("data_origin", {}).get("classification", "unknown")
    if kind in {"synthetic_declared", "mixed_declared"}:
        codes.append("SYNTHETIC_DATA" if kind == "synthetic_declared" else "MIXED_DATA_ORIGIN")
    elif kind == "unknown":
        codes.append("DATA_ORIGIN_UNKNOWN")
    if discipline != "OT":
        if evidence.get("score_tie_count_selected_pool", 0) > 1:
            codes.append("SCORE_TIE")
        if any(m["value_source"].startswith("imputed_") for m in evidence["metrics"]):
            codes.append("METRIC_IMPUTED")
        if not any(m["distinct_observed_advantage"] for m in evidence["leading_metrics"]):
            codes.append("NO_DISTINCT_METRIC_ADVANTAGE")
        if evidence.get("matching_score") == 0:
            codes.append("UNINFORMATIVE_MATCHING_SCORE")
    return codes


def render_clinician(discipline: str, member: dict | None, evidence: dict, rank: int) -> str:
    if member is None:
        if discipline == "OT":
            if evidence.get("full_pool_records") == 0:
                return "No OT clinician was supplied in the full request, so this option has no OT assignment. OT performance is not modeled."
            if evidence.get("full_pool_records", 0) > 0 and evidence.get("selected_pool_records") == 0:
                return "OT clinicians were supplied, but none were returned from the selected pool, so this option has no OT assignment. OT performance is not modeled."
            return "No OT clinician was returned for this slot. OT performance is not modeled."
        return "No clinician was selected for this slot."
    if discipline == "OT":
        route = evidence.get("route")
        if route == "zip_history":
            text = "This OT has recorded service history in the patient's ZIP and was selected from the ZIP-history pool."
        elif route == "routing_disabled_full_pool":
            text = "Routing is disabled, so this OT was selected from the full supplied OT roster."
        elif route == "zip_history_disabled_full_pool":
            text = "ZIP-history filtering is disabled, so this OT was selected from the full supplied OT roster."
        elif route == "zip_history_fallback_full_pool":
            text = ("No patient ZIP was supplied, so this OT was selected from the permitted full-roster fallback."
                    if evidence.get("fallback_reason") == "patient_zip_missing" else
                    "No OT has recorded service history in the patient's ZIP, so this OT was selected from the permitted full-roster fallback.")
        else:
            text = "This OT was selected from the recorded OT candidate pool."
        text += " Selection within this pool uses deterministic rotation; OT performance is not modeled."
        if evidence.get("repeated_in_options"):
            text += f" This clinician appears again because the pool contains only {counted(evidence['distinct_candidates'], 'distinct OT candidate')}."
        return text
    pos, anchor = evidence["selected_pool_rank"], evidence["anchor_rank"]
    if rank == 1:
        text = f"The assignment policy chose position {pos} of {evidence['selected_pool_records']} in the {discipline} matching-score order for this patient's modeled need."
    elif pos == anchor:
        text = f"This alternative repeats the primary selection at position {pos} because the selected {discipline} pool has limited distinct candidates."
    else:
        direction = "higher" if pos < anchor else "lower"
        text = (f"This alternative is {counted(abs(pos-anchor), 'position')} {direction} in the {discipline} matching-score order "
                f"than the primary selection (position {pos} of {evidence['selected_pool_records']}).")
    leading = evidence["leading_metrics"]
    if not leading or evidence.get("matching_score") == 0:
        return text + " The matching score provides no positive metric contribution to distinguish this selection."
    metric = leading[0]
    name = metric["readable_name"]
    direction = "lower values rank higher" if metric["direction"] == "lower_is_better" else "higher values rank higher"
    if metric["value_source"] == "observed" and metric["normalized_supplied_value"] is not None:
        values = f"supplied value {metric['normalized_supplied_value']:.6g}"
        if metric.get("scoring_adjustment_applied"):
            values += f", adjusted scoring value {metric['value_for_scoring']:.6g} under the configured shrinkage setting"
        text += (f" The largest recorded score contribution is {name}: {values} ({direction}), "
                 f"at the {ordinal_percentile(metric['capability_percentile'])} for scoring among "
                 f"{counted(metric['reference_pool_size'], 'supplied ' + discipline + ' record')}.")
    else:
        source = {"imputed_discipline_median": "same-discipline median", "imputed_org_benchmark": "organization benchmark"}.get(metric["value_source"], "unavailable source")
        text += f" For the largest score contribution, {name}, the value was filled from the {source}; it is not observed personal performance."
    origin = evidence.get("data_origin", {}).get("classification", "unknown")
    if origin == "synthetic_declared":
        text += " These synthetic records do not establish personal clinical performance."
    elif origin == "mixed_declared":
        text += " These records have mixed real and synthetic origins, so they do not establish verified personal performance."
    elif not any(m["distinct_observed_advantage"] for m in leading):
        text += " These leading records establish no distinct observed advantage; the score order does not prove better care."
    else:
        text += " Supplied values are unverified record evidence, and this score position does not prove better care."
    return text


def clinician_explanation(full: dict, captured: dict | None, route: dict, decision_id: str,
                          rank: int, discipline: str, member: dict | None,
                          value: Any, group_count: int) -> dict:
    if captured is None or captured.get("status") == "unavailable":
        return unavailable_slot(decision_id, rank, discipline, member, value)
    if not {"selected_pool_records", "distinct_candidates", "metrics"}.issubset(captured):
        return unavailable_slot(decision_id, rank, discipline, member, value)
    evidence = copy.deepcopy(captured)
    route_evidence = route.get("evidence", {})
    evidence.update(
        route=route_evidence.get("route"),
        fallback_reason=route_evidence.get("fallback_reason"),
        routing_policy=route_evidence.get("routing_policy"),
    )
    if "full_pool_size" in route_evidence:
        evidence["full_pool_records"] = route_evidence["full_pool_size"]
        evidence["zip_pool_records"] = route_evidence["zip_pool_size"]
    if discipline == "OT":
        evidence["performance_forecast_status"] = "not_modeled"
    warnings = []
    if member is not None:
        origin = evidence.get("data_origin", origin_evidence(None))
        if origin["classification"] == "synthetic_declared":
            warnings.append("The data-origin label declares synthetic records; supplied values are not established real clinical evidence.")
        elif origin["classification"] == "mixed_declared":
            warnings.append("The data-origin label declares mixed records; the real/synthetic split is unknown.")
        elif origin["classification"] == "unknown":
            warnings.append("Data origin is unknown; supplied does not mean independently verified real-world data.")
        if evidence["distinct_candidates"] < group_count:
            warnings.append(f"Available distinct candidates: {evidence['distinct_candidates']} for {group_count} requested options; repeated clinicians are retained.")
        evidence["repeated_in_options"] = any(
            g["team"].get(discipline) is not None and
            g["team"][discipline]["clinician_id"] == member["clinician_id"]
            for g in full["recommended_groups"][:rank-1]
        )
        if discipline == "OT":
            context = full.get("mirrored_assignment_context", {}).get("ot_context", {})
            if "rotation_offset" not in context:
                return unavailable_slot(decision_id, rank, discipline, member, value)
            evidence.update(rotation_position=member["ranking_position"],
                            rotation_offset=context["rotation_offset"],
                            policy="deterministic_rotation", performance_ranked=False)
        else:
            context = full.get("mirrored_assignment_context", {}).get(discipline.lower()+"_context", {})
            required = {"anchor_ranking_position", "pool_size"}
            if not required.issubset(context) or not evidence.get("metrics"):
                return unavailable_slot(decision_id, rank, discipline, member, value)
            metrics = evidence["metrics"]
            required_metric_fields = {
                "metric_key", "readable_name", "direction", "value_source",
                "normalized_supplied_value", "value_for_scoring", "capability_percentile",
                "contribution", "reference_pool_size", "observed_less_favorable_count",
                "observed_tied_peer_count", "distinct_observed_advantage",
            }
            if (len(metrics) != len(METRIC_ORDER)
                    or any(not required_metric_fields.issubset(m) for m in metrics)
                    or {m["metric_key"] for m in metrics} != set(METRIC_ORDER)):
                return unavailable_slot(decision_id, rank, discipline, member, value)
            if any(m.get("contribution") is None or m.get("capability_percentile") is None
                   or m.get("value_for_scoring") is None for m in metrics):
                card = unavailable_slot(decision_id, rank, discipline, member, value)
                card["status"] = "partial"
                card["evidence"] = evidence
                return card
            total = math.fsum(m["contribution"] for m in metrics)
            score = finite(member["discipline_score"])
            if score is None or not math.isclose(total, score, abs_tol=SCORE_ABS_TOLERANCE, rel_tol=SCORE_REL_TOLERANCE):
                card = unavailable_slot(decision_id, rank, discipline, member, value)
                card["warnings"] = ["Metric contributions do not reconcile to the recorded matching score; no score explanation was rendered."]
                return card
            # Check recorded contribution/source identity, not a new scoring formula.
            if any(m["contribution"] != member["metric_contributions"].get(m["metric_key"]) or
                   m["value_source"] != member["metric_value_sources"].get(m["metric_key"]) for m in metrics):
                return unavailable_slot(decision_id, rank, discipline, member, value)
            leading = sorted(metrics, key=lambda m: (-m["contribution"], METRIC_ORDER.index(m["metric_key"])))[:3]
            evidence.update(
                selected_pool_rank=member["ranking_position"], full_pool_rank=member["full_pool_rank"],
                anchor_rank=context["anchor_ranking_position"], selection_role=member["selection_role"],
                matching_score=score, contribution_total=total, contributions_reconcile=True,
                leading_metrics=leading, leading_metric_rule="Top three recorded contributions; ties follow METRIC_ORDER.",
            )
            if any(m["value_source"].startswith("imputed_") for m in metrics):
                warnings.append("Some scoring values were imputed; see metric-level sources. They are not observed personal performance.")
            if evidence.get("score_tie_count_selected_pool", 0) > 1:
                warnings.append("Matching-score ties use clinician ID, then name, for deterministic ordering; ties do not establish superiority.")
            if any(m.get("shrinkage_enabled") for m in metrics):
                warnings.append("The configured shrinkage adjustment changes scoring inputs only; it is not a forecast uncertainty model. Original supplied values remain separately recorded.")
    status = "partial" if route["status"] != "complete" or evidence.get("status") == "partial" else "complete"
    return {
        **binding(decision_id, rank, discipline, member),
        "clinician_name": member.get("clinician_name") if member else None,
        "assignment_value": value, "status": status,
        "summary": render_clinician(discipline, member, evidence, rank),
        "reason_codes": select_reason_codes(discipline, member, evidence, route, rank, group_count),
        "evidence": evidence, "warnings": warnings,
    }


def build_assignment_explanations(full_result: dict, *, decision_id: str,
                                  assignments: dict, value_key: str) -> dict:
    """Build from one execution. Missing/mismatched evidence fails closed per slot.

    Only bounded evidence gaps are handled. Unexpected programming errors propagate.
    """
    captured = full_result.get("explanation_evidence", {})
    groups = full_result["recommended_groups"]
    if any(len(assignments[d]) != len(groups) for d in DISCIPLINES):
        raise ExplanationEvidenceUnavailable("Assignment slot count does not align.")
    routes = routing_explanations(full_result, captured)
    patient = patient_context(full_result, captured)
    evidence_slots = {}
    for slot in captured.get("selected_slots", []):
        key = (slot["option_rank"], slot["discipline"])
        evidence_slots[key] = None if key in evidence_slots else slot
    options = []
    for index, group in enumerate(groups):
        rank = index+1
        team = {}
        for discipline in DISCIPLINES:
            member = group.get("team", {}).get(discipline)
            value = assignments[discipline][index]
            expected = member.get(value_key) if member else None
            if value != expected or group["option_rank"] != rank or (member and member.get("discipline") != discipline):
                raise ExplanationEvidenceUnavailable("Assignment identity or position does not align.")
            evidence = evidence_slots.get((rank, discipline))
            if evidence is not None and (
                evidence.get("clinician_id") != (member.get("clinician_id") if member else None) or
                evidence.get("clinician_name") != (member.get("clinician_name") if member else None)
            ):
                evidence = None
            team[discipline] = clinician_explanation(full_result, evidence, routes[discipline], decision_id,
                                                     rank, discipline, member, value, len(groups))
        options.append({"option_rank": rank,
                        "label": "Primary option under the current assignment policy" if rank == 1 else
                                 f"Alternative option {rank-1} under the current assignment policy",
                        "team": team})
    statuses = [patient["status"]] + [v["status"] for v in routes.values()] + [
        card["status"] for option in options for card in option["team"].values()
    ]
    status = "complete" if all(s == "complete" for s in statuses) else (
        "unavailable" if all(s == "unavailable" for s in statuses) else "partial")
    reference = full_result.get("assignment_audit", {}).get("risk_reference", {})
    provenance = {
        "model_scope": full_result.get("cms_version_lock"),
        "configuration_hashes": {k: v["sha256"] for k, v in full_result.get("parameter_versions", {}).items()},
        "risk_reference": {k: reference.get(k) for k in ("reference_id", "config_hash", "artifact_sha256", "sample_size")},
        "schema_version": SCHEMA_VERSION, "template_version": TEMPLATE_VERSION,
        "evidence_schema_version": captured.get("schema_version"),
        "code_build_version": None, "production_validated": False,
    }
    return json_safe({
        "schema_version": SCHEMA_VERSION, "template_version": TEMPLATE_VERSION, "status": status,
        "summary": f"The response contains {counted(len(groups), 'option')} in current policy order. RN/PT follow the existing need-based positions in the matching-score order; OT uses deterministic rotation within its recorded threshold-routed pool.",
        "patient_context": patient, "routing_by_discipline": routes, "options": options,
        "provenance": provenance,
    })


def unavailable_explanations(full_result: dict, *, decision_id: str,
                             assignments: dict, value_key: str) -> dict:
    """Preserve assignment slots when a bounded builder alignment check fails."""
    groups = full_result["recommended_groups"]
    return {
        "schema_version": SCHEMA_VERSION, "template_version": TEMPLATE_VERSION,
        "status": "unavailable", "summary": "Assignments are retained; explanation evidence could not be aligned.",
        "patient_context": {"status": "unavailable"}, "routing_by_discipline": {},
        "options": [{"option_rank": i+1,
                     "label": "Primary option under the current assignment policy" if i == 0 else f"Alternative option {i} under the current assignment policy",
                     "team": {d: unavailable_slot(decision_id, i+1, d, group.get("team", {}).get(d), assignments[d][i])
                              for d in DISCIPLINES}}
                    for i, group in enumerate(groups)],
        "provenance": {"schema_version": SCHEMA_VERSION, "template_version": TEMPLATE_VERSION, "code_build_version": None},
    }


def explanations_to_markdown(response: dict) -> str:
    """Readable view of the returned explanations; never re-executes assignment."""
    exp = response["explanations"]
    lines = ["# Assignment explanations", "", f"Decision: {response['decision_id']}", "",
             exp["summary"], "", exp["patient_context"].get("summary", ""), ""]
    for discipline, route in exp["routing_by_discipline"].items():
        lines.extend([f"- {discipline}: {route['summary']}"])
    for option in exp["options"]:
        lines.extend(["", f"## {option['label']}", ""])
        for discipline, card in option["team"].items():
            lines.extend([f"### {discipline}: {card['clinician_name'] or 'No clinician'}", "",
                          card["summary"], ""])
            lines.extend(f"- {warning}" for warning in card["warnings"])
            lines.append("")
    return "\n".join(lines) + "\n"
