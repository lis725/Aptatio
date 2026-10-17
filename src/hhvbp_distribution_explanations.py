"""Patient raw-reference position and recorded assignment reasons, without projections."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from statistics import median
from hhvbp_local_externality import METRIC_ORDER
from pathlib import Path

import numpy as np
from hhvbp_assignment_explanations import (
    DISCIPLINES, ExplanationEvidenceUnavailable, build_assignment_explanations,
    json_safe, ordinal_percentile, unavailable_explanations,
)
from hhvbp_risk_reference import empirical_risk_percentile
from hhvbp_distribution_density import density_curve, threshold_marker
from hhvbp_distribution_presentation import concise_card, readable_factors, page_presentation

SCHEMA_VERSION = "agency_distribution_explanations.v1"
TEMPLATE_VERSION = "distribution_en.v2"
FACTOR_LABELS = {
    "health_status": "Health status (provided)", "age_group": "Age group",
    "house_price_zip": "ZIP-level house-price category", "area_type": "Area type",
    "housing_type": "Housing type", "distance_to_clinic": "Distance to clinic",
    "driving_condition": "Driving conditions",
}
METRIC_FIELDS = (
    "metric_key", "readable_name", "direction", "normalized_supplied_value",
    "imputed_or_supplied_value", "value_for_scoring", "value_source",
    "scoring_adjustment_applied", "scoring_adjustment_weight", "shrinkage_enabled",
    "capability_percentile", "reference_pool_size", "reference_scope",
    "metric_weight", "modeled_need", "weighted_patient_need",
    "effective_discipline_weight", "contribution",
)
SELECTION_FIELDS = (
    "selected_pool_rank", "full_pool_rank", "anchor_rank", "selection_role",
    "matching_score", "contribution_total", "contributions_reconcile",
    "selected_pool_records", "distinct_candidates", "repeated_in_options",
    "score_tie_count_selected_pool", "rotation_position", "rotation_offset",
    "performance_ranked", "data_origin",
)


class DistributionEvidenceUnavailable(ValueError):
    """Bounded reference-evidence gap after successful assignment."""


def _read_bound_reference(full: dict) -> tuple[dict, dict]:
    audit = full.get("assignment_audit", {}).get("risk_reference", {})
    try:
        raw = Path(audit["artifact_path"]).read_bytes()
    except (KeyError, OSError, TypeError) as exc:
        raise DistributionEvidenceUnavailable("REFERENCE_UNREADABLE") from exc
    if hashlib.sha256(raw).hexdigest() != audit.get("artifact_sha256"):
        raise DistributionEvidenceUnavailable("REFERENCE_CHANGED_AFTER_ASSIGNMENT")
    try:
        reference = json.loads(raw)
        metadata = reference["metadata"]
        scores = reference["sorted_risk_scores"]
        valid = (
            isinstance(scores, list) and len(scores) > 0
            and all(isinstance(v, (float, int)) and not isinstance(v, bool)
                    and math.isfinite(v) and 0 <= v <= 1 for v in scores)
            and scores == sorted(scores)
            and len(scores) == metadata["sample_size"] == audit["sample_size"]
            and metadata["reference_id"] == audit["reference_id"]
            and metadata["config_hash"] == audit["config_hash"]
        )
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise DistributionEvidenceUnavailable("REFERENCE_EVIDENCE_INVALID") from exc
    if not valid:
        raise DistributionEvidenceUnavailable("REFERENCE_EVIDENCE_INVALID")
    return reference, audit


def build_patient_distribution(full: dict, decision_id: str) -> dict:
    severity = full.get("patient_severity", {})
    raw, used = severity.get("risk_score_rho_raw"), severity.get("risk_percentile_u")
    raw_override = severity.get("source") in {
        "patient.severity_score_override", "options.severity_score_override",
    }
    percentile_override = severity.get("risk_percentile_source") == "options.risk_percentile_override"
    basic = {
        "patient_raw_score": raw, "assignment_percentile": used,
        "raw_score_source": severity.get("source"),
        "assignment_percentile_source": severity.get("risk_percentile_source"),
        "raw_score_override_used": raw_override,
        "percentile_override_used": percentile_override,
    }
    try:
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1
               for v in (raw, used)):
            raise DistributionEvidenceUnavailable("PATIENT_POSITION_EVIDENCE_INVALID")
        reference, audit = _read_bound_reference(full)
        scores, meta = reference["sorted_risk_scores"], reference["metadata"]
        mapped = empirical_risk_percentile(raw, scores)
        if not percentile_override and mapped != used:
            raise DistributionEvidenceUnavailable("RUNTIME_PERCENTILE_MISMATCH")
    except DistributionEvidenceUnavailable as exc:
        return {**basic, "status": "unavailable", "patient_percentile": None,
                "patient_decile": None, "chart_data": None, "reference": None,
                "summary": "The assignment is retained, but its bound reference distribution cannot be displayed.",
                "reason_codes": [str(exc)], "warnings": ["No replacement or regenerated reference was used."]}
    decile = min(10, max(1, math.ceil(mapped * 10)))
    band = "lower-need end" if mapped < .3 else "middle portion" if mapped < .7 else "higher-need end"
    notes = [
        "Density is a display-only smoothed view of persisted reference scores, not observed agency patients.",
        "The raw score is a modeled need index; its percentile is a relative position, not an event probability.",
    ]
    codes = ["PERSISTED_RAW_SCORE_REFERENCE", "REFERENCE_HASH_VERIFIED"]
    summary = (f"The patient's raw modeled-need score falls near the {ordinal_percentile(mapped)} "
               f"of the configured reference profiles, in the {band} of this distribution.")
    if raw_override:
        codes.append("RAW_SCORE_OVERRIDE_USED")
        notes.append("The displayed raw score was explicitly overridden; the patient factors did not determine this marker.")
        summary += " The raw score shown was explicitly overridden."
    if percentile_override:
        codes.append("ASSIGNMENT_PERCENTILE_OVERRIDE_USED")
        note = (f"Assignment uses an explicit override of {ordinal_percentile(used, exact=True)}; "
                f"the raw-score marker maps to {ordinal_percentile(mapped)} in the reference.")
        notes.append(note)
        summary += " " + note
    metadata = {k: meta.get(k) for k in (
        "reference_id", "config_hash", "schema_version", "sample_size", "seed",
        "source_mode", "sampling_method", "creation_timestamp", "reference_version",
    ) if k in meta}
    metadata.update(artifact_sha256=audit["artifact_sha256"],
                    schema_version=reference.get("schema_version", meta.get("schema_version")),
                    interpretation="Persisted configured reference profiles; not an observed agency cohort.")
    counts, bins = np.histogram(scores, bins=np.linspace(0., 1., 31))
    chart = {
        "status": "ready", "chart_type": "raw_score_density_with_patient_and_threshold_markers",
        "decision_id": decision_id, "x_label": "Modeled-need score",
        "y_label": "Density", "distribution_source": "persisted_runtime_raw_scores",
        "bins": bins.tolist(), "bin_counts": counts.tolist(), "sample_size": len(scores),
        "bin_rule": "Left-closed/right-open equal-width bins; final bin includes 1.0.",
        "patient_raw_score": raw, "patient_percentile": mapped, "patient_decile": decile,
        "assignment_percentile": used, "percentile_override_used": percentile_override,
        "raw_score_override_used": raw_override, "patient_label": "Incoming patient",
        "reference_id": meta["reference_id"], "reference_artifact_sha256": audit["artifact_sha256"],
        "raw_support": [scores[0], scores[-1]], "notes": notes,
    }
    chart["density_curve"] = density_curve(scores, audit["artifact_sha256"])
    routing = full.get("threshold_routing", {})
    chart["threshold_marker"] = (threshold_marker(scores, routing["threshold_percentile"])
                                 if routing.get("enabled") and routing.get("zip_history_enabled")
                                 and not (raw_override or percentile_override) else None)
    # Provenance is taken from this result's bound reference, not a fixed count or a guessed cohort.
    origin = "configured semi-synthetic" if meta.get("semi_synthetic") is True else "configured"
    chart["reference_note"] = (f"Smoothed view of {len(scores):,} {origin} reference profiles; "
                               "modeled need, not a clinical risk probability.")
    if chart["density_curve"]["status"] == "point_mass":
        chart["reference_note"] = (f"{len(scores):,} {origin} reference profiles share one score; "
                                   "modeled need, not a clinical risk probability.")
    elif chart["density_curve"]["status"] == "unavailable":
        chart["reference_note"] = f"Density unavailable for {len(scores):,} bound reference profiles; assignments are retained."
    return {**basic, "status": "complete", "patient_percentile": mapped,
            "patient_decile": decile, "position_band": band, "summary": summary,
            "chart_data": chart, "reference": metadata, "reason_codes": codes, "warnings": notes}


def _patient_profile(full: dict, base: dict) -> dict:
    context = base.get("patient_context", {})
    factors = context.get("normalized_inputs", {})
    priorities = copy.deepcopy(context.get("metric_priorities", []))
    summary = "The model used the seven supplied factor categories listed here."
    if priorities:
        summary += " The largest weighted metric priorities are " + ", ".join(p["readable_name"] for p in priorities) + "."
    else:
        summary += " No positive weighted metric priority is available."
    warnings = ["Metric priorities are model weights applied to need values, not diagnoses or individual factor attributions.",
                "ZIP-level house prices do not establish this patient's personal income or wealth."]
    if context.get("raw_index_override_used") or context.get("percentile_override_used"):
        warnings.append("An explicit position override is active; priorities still describe the supplied factor profile.")
    return {
        "status": context.get("status", "unavailable"),
        "patient_profile": {k: factors.get(k) for k in FACTOR_LABELS},
        "factor_labels": FACTOR_LABELS.copy(), "patient_zip": full.get("patient", {}).get("patient_zip"),
        "modeled_priority_metrics": priorities, "priority_rule": context.get("priority_rule"),
        "patient_need_summary": summary if factors else "Patient-factor evidence is unavailable.",
        "warnings": warnings,
    }


def _card(card: dict, route: dict, group_count: int) -> dict:
    evidence = card.get("evidence", {})
    routing = copy.deepcopy(route.get("evidence", {}))
    selection = {k: copy.deepcopy(evidence[k]) for k in SELECTION_FIELDS if k in evidence}
    discipline = card["discipline"]
    metrics = [{k: copy.deepcopy(m.get(k)) for k in METRIC_FIELDS} for m in evidence.get("metrics", [])]
    leading = [m["metric_key"] for m in evidence.get("leading_metrics", [])]
    for m in metrics:
        m["is_leading_contribution"] = m["metric_key"] in leading
    # The older renderer is used only for validated evidence alignment, never for
    # historical comparisons, projections, or the new clinician prose.
    codes = [c for c in card["reason_codes"] if c != "NO_DISTINCT_METRIC_ADVANTAGE"]
    warnings = [w for w in card.get("warnings", []) if "forecast" not in w.lower()]
    if any(m.get("shrinkage_enabled") for m in metrics):
        warnings.append("Configured shrinkage modifies scoring inputs; original supplied values are recorded separately.")
    if (card["status"] == "unavailable" or not evidence or
            (card["clinician_id"] is not None and discipline != "OT" and "selected_pool_rank" not in evidence)):
        summary = "The assignment is retained, but its aligned selection evidence is unavailable."
    elif card["clinician_id"] is None:
        summary = ("No OT was supplied in the full request, so this option has an empty OT slot."
                   if discipline == "OT" and evidence.get("full_pool_records") == 0 else
                   f"No {discipline} clinician was returned from the selected pool for this slot.")
    elif discipline == "OT":
        route_name = routing.get("route")
        if route_name == "zip_history":
            summary = "This OT was selected from clinicians with recorded service history in the patient's ZIP."
        elif route_name == "zip_history_fallback_full_pool":
            summary = ("The patient ZIP is missing, so this OT comes from the permitted full-pool fallback."
                       if routing.get("fallback_reason") == "patient_zip_missing" else
                       "No matching OT ZIP service history was recorded, so this OT comes from the permitted full-pool fallback.")
        else:
            summary = route.get("summary", "This OT comes from the recorded candidate pool.")
        summary += " This does not necessarily mean the OT previously treated this patient."
        if routing.get("selected_pool_size") == 1 and group_count > 1:
            summary += " Only one OT is in this pool, so the same OT fills every returned option."
            codes.append("SINGLE_OT_POOL_REPEATED")
        elif evidence.get("repeated_in_options"):
            summary += " The rotation repeats after the available OT candidates are exhausted."
        selection["policy"] = "deterministic_rotation_in_recorded_pool"
        warnings.append("Area service history does not establish prior treatment of this patient or current availability.")
    else:
        selection["policy"] = "mirrored_percentile_target_then_outward_positions"
        pool = {"zip_history": "ZIP-history pool", "global_high_risk": "full same-discipline pool",
                "zip_history_fallback_full_pool": "full pool after permitted fallback",
                "routing_disabled_full_pool": "full pool with routing disabled",
                "zip_history_disabled_full_pool": "full pool with ZIP filtering disabled"}.get(routing.get("route"), "recorded pool")
        pos, anchor = evidence.get("selected_pool_rank"), evidence.get("anchor_rank")
        summary = f"This {discipline} occupies position {pos} of {evidence.get('selected_pool_records')} in the {pool}'s matching-score order."
        summary += f" The policy maps the assignment percentile to target position {anchor}, then fills options using its recorded outward order."
        supporting = evidence.get("leading_metrics", [])
        if supporting and evidence.get("matching_score") != 0:
            first = supporting[0]
            if first.get("value_source", "").startswith("imputed_"):
                summary += f" The largest recorded score contribution is {first['readable_name']}, using a filled-in scoring value."
            else:
                summary += f" The largest recorded score contribution is {first['readable_name']}, with supplied value {first['normalized_supplied_value']:.6g}."
        else:
            summary += " The matching score has no positive metric contribution; selection follows the recorded policy and tie order."
        if any(m.get("value_source", "").startswith("imputed_") for m in metrics):
            warnings.append("Filled-in values are scoring substitutes and are not observed personal performance.")
        warnings.append("Supporting values explain the matching calculation; they are not a claim of clinical superiority.")
    return {k: card.get(k) for k in ("decision_id", "option_rank", "clinician_id", "clinician_name", "assignment_value", "discipline", "status")} | {
        "summary": summary, "reason_codes": list(dict.fromkeys(codes)),
        "routing_evidence": routing, "selection_evidence": selection,
        "metric_evidence": metrics, "warnings": list(dict.fromkeys(warnings)),
    }


def build_distribution_explanations(full: dict, *, decision_id: str, assignments: dict, value_key: str, config_dir: str | Path | None = None, request: dict | None = None) -> dict:
    try:
        base = build_assignment_explanations(full, decision_id=decision_id, assignments=assignments, value_key=value_key)
    except ExplanationEvidenceUnavailable:
        base = unavailable_explanations(full, decision_id=decision_id, assignments=assignments, value_key=value_key)
    distribution = build_patient_distribution(full, decision_id)
    profile = _patient_profile(full, base)
    profile["readable_factors"] = readable_factors(full, profile["patient_profile"], config_dir)
    routes = copy.deepcopy(base["routing_by_discipline"])
    options = [{"option_rank": option["option_rank"], "option_label": "Primary option" if option["option_rank"] == 1 else f"Alternative {option['option_rank'] - 1}",
                "team": {d: concise_card(_card(card, routes.get(d, {}), len(base["options"]))) for d, card in option["team"].items()}}
               for option in base["options"]]
    references = descriptive_references(full, request)
    for option in options:
        for discipline, card in option["team"].items():
            if discipline in ("RN", "PT") and card.get("clinician_id") is not None:
                card["report_appraisal"] = metric_appraisal(card, references.get(discipline, {}), profile)
    statuses = [distribution["status"], profile["status"]] + [r["status"] for r in routes.values()] + [c["status"] for o in options for c in o["team"].values()]
    provenance = copy.deepcopy(base["provenance"])
    provenance.update(schema_version=SCHEMA_VERSION, template_version=TEMPLATE_VERSION,
                      explanation_mode="patient_distribution_and_assignment_reasons")
    return json_safe({
        "status": "complete" if all(s == "complete" for s in statuses) else "partial" if any(s == "complete" for s in statuses) else "unavailable",
        "patient_distribution": distribution, "patient_profile_summary": profile,
        "routing_summary": routes, "options": options,
        "report_refinement": {"schema_version": "descriptive_report.v1",
                              "policy_regions": policy_regions(full, distribution, config_dir)},
        "presentation": page_presentation(full, distribution, routes, options),
        "warnings": ["This explains the current modeled distribution and recorded selection policy; it is not a clinical eligibility assessment."],
        "provenance": provenance,
    })


# Descriptive display evidence only. Existing engine percentiles/scores are never
# recalculated. Six decimal places and an absolute 1e-6 tolerance prevent a
# categorical difference that disappears in the displayed normalized values.
COMPARISON_TOLERANCE = 1e-6
REFERENCE_SCOPE = 'full_same_discipline_request_roster_before_zip_filtering'


def _valid_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _metric_metadata(request, record, metric, discipline):
    """Optional declared semantics; absent metadata stays explicitly unverified."""
    from hhvbp_assignment_service import ensure_clinician_id
    request = request or {}
    metadata = request.get('metric_metadata', {})
    global_meta = metadata.get(metric, {}) if isinstance(metadata, dict) else {}
    global_meta = global_meta if isinstance(global_meta, dict) else {}
    values = []
    for row in request.get('clinicians', []):
        if str(row.get('discipline', '')).strip().upper() == discipline and ensure_clinician_id(row) == record['clinician_id']:
            local = row.get('metric_metadata', {})
            local = local.get(metric, {}) if isinstance(local, dict) else {}
            values.append({**global_meta, **(local if isinstance(local, dict) else {})})
    if not values:
        values = [global_meta]
    return values


def descriptive_references(full, request=None):
    """One fixed, supplied-only median per discipline/metric, including the selected person.

    The existing engine captured reference_records before display truncation.
    Distinct IDs are equally weighted; conflicting duplicate IDs invalidate the
    comparison. Known incompatible semantics invalidate the metric for everyone.
    """
    result = {}
    captured = full.get('explanation_evidence', {})
    for discipline in ('RN', 'PT'):
        result[discipline] = {}
        records = captured.get('reference_records', {}).get(discipline, [])
        for metric in METRIC_ORDER:
            supplied = {}; semantics = []; conflict = False
            for record in records:
                cell = record.get('metrics', {}).get(metric, {})
                value = cell.get('normalized_supplied_value')
                if cell.get('value_source') != 'observed' or not _valid_number(value) or not 0 <= value <= 1:
                    continue
                identifier = record['clinician_id']
                if identifier in supplied and abs(supplied[identifier]-value) > COMPARISON_TOLERANCE:
                    conflict = True
                supplied[identifier] = value
                semantics.extend(_metric_metadata(request, record, metric, discipline))
            declarations = {key: {str(m[key]).strip().lower() for m in semantics if m.get(key) is not None}
                            for key in ('unit', 'definition', 'reporting_period', 'population', 'direction')}
            unit_alias = {'percentage': 'proportion', 'percent': 'proportion', 'fraction': 'proportion',
                          'normalized_score': 'score', 'normalized_value': 'score'}
            units = {unit_alias.get(unit, unit) for unit in declarations['unit']}
            direction = captured.get('metric_metadata', {}).get(metric, {}).get('direction')
            incompatible = [key for key in ('definition', 'reporting_period', 'population') if len(declarations[key]) > 1]
            if len(units) > 1 or any(unit not in ('proportion', 'score') for unit in units):
                incompatible.append('unit')
            if declarations['direction'] and declarations['direction'] != {direction}:
                incompatible.append('direction')
            values = list(supplied.values())
            status = ('conflicting_duplicate_ids' if conflict else 'incompatible_' + '_'.join(incompatible) if incompatible else
                      'insufficient_distinct_records' if len(values) < 2 else 'comparable')
            result[discipline][metric] = {
                'method': 'median_of_distinct_personally_supplied_records', 'scope': REFERENCE_SCOPE,
                'distinct_clinician_count': len(values), 'median': median(values) if values and status == 'comparable' else None,
                'has_variation': bool(values and max(values)-min(values) > COMPARISON_TOLERANCE),
                'status': status, 'direction': direction,
                'unit': 'verified_proportion' if units == {'proportion'} and all(m.get('unit') for m in semantics) else 'normalized_value',
                'declared_metadata': {k: sorted(v) for k,v in declarations.items()},
                'metadata_complete': bool(semantics) and all(all(m.get(k) for k in ('unit','definition','reporting_period')) for m in semantics),
                'provenance_note': 'Comparison of supplied records; sources and periods are not independently verified.',
                'absolute_tolerance': COMPARISON_TOLERANCE,
            }
    return result


def _display_value(value, unit):
    if not _valid_number(value):
        return 'unavailable'
    return (f'{value*100:.4f}'.rstrip('0').rstrip('.') + '%' if unit == 'verified_proportion'
            else f'{value:.6f}'.rstrip('0').rstrip('.') or '0')


def metric_appraisal(card, references, profile):
    """All used metrics are classified before choosing a short display subset."""
    priority = [m['metric_key'] for m in profile.get('modeled_priority_metrics', []) if m.get('weighted_patient_need', 0) > 0]
    order = list(dict.fromkeys(priority + METRIC_ORDER))
    records = []
    for metric in card.get('metric_evidence', []):
        if not _valid_number(metric.get('effective_discipline_weight')) or metric['effective_discipline_weight'] <= 0:
            continue
        key = metric['metric_key']; reference = references.get(key, {})
        supplied = metric.get('normalized_supplied_value'); center = reference.get('median')
        direction = metric.get('direction'); source = metric.get('value_source')
        reason = None; relation = 'unassessable'
        if source != 'observed' or not _valid_number(supplied):
            reason = 'imputed_value' if str(source).startswith('imputed') else 'personal_value_missing'
        elif not 0 <= supplied <= 1 or direction not in ('lower_is_better','higher_is_better'):
            reason = 'invalid_scale_or_direction'
        elif reference.get('status') != 'comparable' or not _valid_number(center):
            reason = reference.get('status', 'reference_unavailable')
        else:
            difference = supplied - center
            if abs(difference) <= COMPARISON_TOLERANCE or _display_value(supplied, reference['unit']) == _display_value(center, reference['unit']):
                relation = 'at_reference'; reason = 'no_distinguishable_difference'
            else:
                favorable = difference < 0 if direction == 'lower_is_better' else difference > 0
                relation = 'support' if favorable else 'attention'
        name = str(metric.get('readable_name') or key).replace('_', ' ').replace('—', '-')
        unit = reference.get('unit', 'normalized_value')
        value_label, median_label = _display_value(supplied, unit), _display_value(center, unit)
        if relation in ('support', 'attention', 'at_reference'):
            sentence = (f'{name}: {value_label} vs supplied median {median_label} '
                        f'({"lower" if direction == "lower_is_better" else "higher"} is favorable; '
                        f'{reference["distinct_clinician_count"]} supplied IDs).')
        elif reason == 'imputed_value':
            sentence = f'{name}: no personal supplied value; scoring used an imputed value.'
        elif reason == 'personal_value_missing':
            sentence = f'{name}: personal supplied evidence is missing.'
        else:
            sentence = f'{name}: comparison unavailable ({str(reason).replace("_", " ")}).'
        records.append({
            'metric_key': key, 'readable_name': name, 'supplied_value': supplied,
            'scoring_value': metric.get('value_for_scoring'), 'value_source': source,
            'direction': direction, 'unit': unit, 'reference': reference,
            'relation': relation, 'reason': reason, 'statement': sentence,
            'capability_percentile': metric.get('capability_percentile'),
            'score_percentile_reference': 'full scored roster, including imputed peers',
            'score_reference_size': metric.get('reference_pool_size'),
            'adjustment_applied': bool(metric.get('scoring_adjustment_applied') or metric.get('shrinkage_enabled')),
        })
    records.sort(key=lambda m: order.index(m['metric_key']) if m['metric_key'] in order else len(order))
    support = [m for m in records if m['relation'] == 'support']
    attention = [m for m in records if m['relation'] == 'attention']
    gaps = [m for m in records if m['relation'] == 'unassessable']
    origin = card.get('selection_evidence', {}).get('data_origin', {})
    label = ('Synthetic supplied records' if origin.get('classification') == 'synthetic_declared' else
             'Supplied records (source unknown)' if origin.get('classification') == 'unknown' else 'Supplied records (unverified)')
    scope = (f'{label}; normalized values unless % is shown. Reference: full supplied {card["discipline"]} roster before ZIP filtering, '
             'using personal values only; metadata is not independently verified.')
    support_line = 'Supporting record: ' + (' '.join(m['statement'] for m in support[:2]) if support else
                   'No above-reference support was established from comparable supplied values.')
    attention_line = 'Attention point: ' + (' '.join(m['statement'] for m in attention[:2]) if attention else
                     'No below-reference value was identified among the assessable metrics.' if any(m['relation']=='at_reference' for m in records) or support else
                     'Relative attention points cannot be assessed from comparable supplied records.')
    details = []
    if attention[2:]:
        details.append('Also review: ' + ' '.join(m['statement'] for m in attention[2:]))
    if gaps:
        imputed = [m for m in gaps if m['reason'] == 'imputed_value']
        other = [m for m in gaps if m['reason'] != 'imputed_value']
        if imputed:
            details.append('Evidence gaps - no personal values; scoring used imputation: ' + '; '.join(m['readable_name'] for m in imputed) + '. Missing records are not poor performance.')
        if other:
            details.append('Evidence gaps: ' + ' '.join(m['statement'] for m in other))
    adjusted = [m for m in records if m['adjustment_applied']]
    if adjusted:
        details.append('Scoring adjustments: ' + '; '.join(
            f'{m["readable_name"]} supplied {_display_value(m["supplied_value"],m["unit"])}, '
            f'scoring value {_display_value(m["scoring_value"],m["unit"])}, '
            f'engine score percentile {_display_value(m["capability_percentile"], "verified_proportion").rstrip("%")} '
            f'within {m["score_reference_size"]} scored records (including imputed peers)'
            for m in adjusted) + '.')
    return {'reference_scope': REFERENCE_SCOPE, 'provenance': origin, 'metrics': records,
            'support_metrics': [m['metric_key'] for m in support[:2]],
            'attention_metrics': [m['metric_key'] for m in attention[:2]],
            'scope_sentence': scope, 'support_sentence': support_line,
            'attention_sentence': attention_line, 'detail_sentences': details}


def policy_regions(full, distribution, config_dir):
    routing = full.get('threshold_routing', {}); threshold = routing.get('threshold_percentile')
    active = bool(routing.get('enabled') and routing.get('zip_history_enabled'))
    chart = distribution.get('chart_data') or {}; marker = chart.get('threshold_marker')
    fallback = None
    if config_dir is not None:
        try:
            raw = (Path(config_dir)/'assignment_parameters.json').read_bytes()
            if hashlib.sha256(raw).hexdigest() == full['parameter_versions']['assignment_parameters.json']['sha256']:
                fallback = bool(json.loads(raw).get('threshold_routing', {}).get('fallback_to_full_pool', True))
        except (KeyError, OSError, ValueError):
            pass
    note = 'These are model-based allocation groups, not clinical diagnoses. OT uses deterministic rotation within its recorded candidate pool.'
    if not active:
        reason = 'routing is disabled' if not routing.get('enabled') else 'ZIP filtering is disabled'
        return {'mode': 'disabled', 'threshold_percentile': threshold, 'left': None, 'right': None,
                'notice': f'No active ZIP/full-roster split: RN/PT use the full supplied roster because {reason}. OT follows the recorded disabled-control rule.',
                'note': 'Modeled need is not a clinical diagnosis.'}
    if not _valid_number(threshold):
        return {'mode': 'unavailable', 'threshold_percentile': None, 'left': None, 'right': None,
                'notice': 'Applied RN/PT threshold evidence is unavailable; no regions are inferred.', 'note': note}
    display = f'{threshold*100:.10g}'
    fallback_text = ('full-roster fallback when permitted and needed.' if fallback else
                     'coverage fallback is disabled; missing ZIP retains the existing full-roster rule.' if fallback is False else
                     'fallback permission is unavailable in the bound evidence.')
    left = {'title': 'Lower modeled-need group', 'boundary': f'At or below the RN/PT threshold (percentile {display})',
            'policy': 'RN/PT: ZIP-history first; ' + fallback_text}
    right = {'title': 'Higher modeled-need group', 'boundary': f'Above the RN/PT threshold (percentile {display})',
             'policy': 'RN/PT: select from the full supplied roster using the need-matching policy.'}
    mode = 'raw_regions'; notice = ''
    if not chart or chart.get('density_curve', {}).get('status') != 'ready':
        mode = 'policy_only'; notice = 'Reference shape unavailable or degenerate: these describe percentile policy only, not regions containing reference mass.'
    elif distribution.get('percentile_override_used') or distribution.get('raw_score_override_used'):
        mode = 'policy_only'; notice = 'Policy groups use the applied assignment percentile; the plotted marker keeps its raw-score reference position.'
    elif marker is None:
        mode = 'policy_only'; notice = 'No verified raw boundary is available; groups are defined by the applied percentile rule.'
    elif marker.get('kind') == 'quantile_guide':
        mode = 'quantile_guide'; notice = 'Approximate raw-score guide only: group membership follows the applied percentile rule, not an exact raw cutoff.'
    return {'mode': mode, 'threshold_percentile': threshold, 'comparison': 'strictly_greater_than',
            'fallback_to_full_pool': fallback, 'marker_kind': marker.get('kind') if marker else None,
            'left': left, 'right': right, 'notice': notice, 'note': note}
