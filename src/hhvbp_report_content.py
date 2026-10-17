"""Frozen decision-bound content for agency views and recipient adapters.

Reporting only: no allocation, scoring, new outcome model, or filesystem exports.
Legacy distribution evidence is retained untouched beside this additive namespace.
"""
from __future__ import annotations

import copy
import math

from hhvbp_factor_interpretation import build_factor_interpretations
from hhvbp_scorecard_evidence import build_scorecard_context, clinician_scorecard, display_value, normalize_record

SCHEMA_VERSION = 'sponsor_report_content.v1'


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _percentile(value):
    if not _number(value):
        return 'unavailable'
    return f'{value * 100:.6f}'.rstrip('0').rstrip('.')


def distribution_content(full, explanation):
    """Narrate actual full-precision classification independently of curve geometry."""
    dist = explanation['patient_distribution']
    routing = full.get('threshold_routing', {})
    threshold = routing.get('threshold_percentile')
    used = dist.get('assignment_percentile')
    plotted = dist.get('patient_percentile')
    active = bool(routing.get('enabled') and routing.get('zip_history_enabled'))
    group = ('higher' if used > threshold else 'lower') if active and _number(used) and _number(threshold) else 'unknown'
    reference = dist.get('reference') or {}
    chart = dist.get('chart_data')
    if chart and reference:
        population = ('The curve simulates the distribution of Need Scores across the patient population. '
                      'Need Score represents the modeled level of clinical care a patient requires. '
                      'A higher Need Score indicates greater care needs and a stronger need for clinicians '
                      'with higher ratings in relevant VBP abilities to support better VBP performance.')
    else:
        population = 'The reference distribution is unavailable; the recorded assignment is retained without inventing a curve.'
    override = bool(dist.get('percentile_override_used') or dist.get('raw_score_override_used'))
    if group != 'unknown':
        boundary = (f'We set the threshold at the {_percentile(threshold)}th percentile. '
                    'For this allocation policy, patients above the threshold are classified as high-risk '
                    'and matched with suitable clinicians from the full supplied roster across the local region. '
                    'Patients at or below the threshold are classified as low-risk, with priority given to '
                    'clinicians who have previously served their ZIP-code area.')
        relation = 'above' if used > threshold else 'equal to' if used == threshold else 'below'
        if dist.get('percentile_override_used'):
            position = (f'The plotted reference position is percentile {_percentile(plotted)}. An explicit override uses '
                        f'percentile {_percentile(used)} for allocation, {relation} the threshold.')
        else:
            position = f'This patient is at percentile {used * 100:.1f}, {relation} the threshold.'
        outlook = (f'The current model indicates {"more" if group == "higher" else "less"} challenging VBP-related '
                   'care needs; this is not a calibrated outcome prediction.')
    else:
        boundary = 'No active lower/higher ZIP-routing split is applied by the current controls.'
        position = (f'The plotted reference position is percentile {_percentile(plotted)}.' if _number(plotted)
                    else 'The patient reference position is not available.')
        outlook = 'A policy-group VBP outlook is not inferred when the required active threshold evidence is absent.'
    routes = explanation.get('presentation', {}).get('decision_paragraph', 'Actual RN/PT routing evidence is unavailable.')
    ot_route = explanation.get('routing_summary', {}).get('OT', {}).get('evidence', {}).get('route')
    ot = {
        'zip_history': 'OT uses the recorded ZIP-history pool, with rotation when more than one OT is eligible.',
        'global_high_risk': 'OT uses the full roster above the need threshold, with recorded rotation inside that pool.',
        'zip_history_fallback_full_pool': 'OT used its separately recorded full-roster fallback because ZIP-history selection was unavailable.',
        'routing_disabled_full_pool': 'OT used the full roster because routing is disabled.',
        'zip_history_disabled_full_pool': 'OT used the full roster because ZIP filtering is disabled.',
    }.get(ot_route, 'OT follows its separately recorded routing or absence rule.')
    return {
        'needs_group': group, 'active_policy_split': active, 'override_active': override,
        'percentile_override_used': bool(dist.get('percentile_override_used')),
        'raw_score_override_used': bool(dist.get('raw_score_override_used')),
        'classification_basis': {'assignment_percentile': used, 'reference_percentile': plotted,
                                 'threshold_percentile': threshold, 'comparison': 'strictly_greater_than',
                                 'source': 'same_decision.threshold_routing'},
        'population_sentence': population, 'boundary_sentence': boundary, 'position_sentence': position,
        'outlook_sentence': outlook, 'actual_route_sentence': routes, 'ot_sentence': ot,
        'narrative': ' '.join([population, boundary, position, outlook, routes, ot]),
        'legend': 'Lower modeled need does not mean clinically healthy. A percentile is a relative position, not an event probability.',
        'reference_provenance': copy.deepcopy(reference),
    }


def selection_text(discipline, card):
    """Individual prose is independent of primary/alternative score-position labels."""
    if card.get('clinician_id') is None:
        return card.get('summary') or f'No {discipline} clinician was returned for this slot.'
    evidence = card.get('routing_evidence', {})
    selection = card.get('selection_evidence', {})
    route = evidence.get('route')
    reason = {
        'zip_history': 'the selected pool contains clinicians with recorded service in the requested ZIP',
        'global_high_risk': 'the applied need percentile exceeds the configured threshold, so the full supplied roster is used',
        'routing_disabled_full_pool': 'routing is disabled, so the full supplied roster is used',
        'zip_history_disabled_full_pool': 'ZIP filtering is disabled, so the full supplied roster is used',
    }.get(route)
    if route == 'zip_history_fallback_full_pool':
        reason = ('the patient ZIP is missing, so the recorded full-roster fallback applies'
                  if evidence.get('fallback_reason') == 'patient_zip_missing' else
                  'recorded ZIP coverage is insufficient, so the permitted full-roster fallback applies')
    if reason is None:
        return 'The selection is retained; its detailed routing evidence is unavailable.'
    if discipline == 'OT':
        basis = 'the only OT in the selected candidate pool' if evidence.get('selected_pool_size') == 1 else 'the recorded OT rotation'
        return f'Selection uses {basis}; {reason}. Recorded service does not establish current availability.'
    return f'Selected by the current need-matching policy because {reason}.'


def _roster_records(card, scorecard, relation):
    """Supplement absent benchmarks only with an explicitly identified real roster."""
    rows = {m['metric_key']: m for m in scorecard.get('metrics', [])}
    output = []
    for item in card.get('report_appraisal', {}).get('metrics', []):
        key = item['metric_key']; row = rows.get(key, {})
        if item.get('relation') != relation or row.get('status') != 'supplied':
            continue
        if any(c.get('relation') in ('attention', 'favorable', 'equal')
               for c in row.get('comparisons', {}).values() if isinstance(c, dict)):
            continue
        reference = item.get('reference', {})
        if reference.get('status') != 'comparable' or not _number(reference.get('median')):
            continue
        unit = (row.get('personal_evidence') or {}).get('comparable_unit')
        if unit in (None, 'unknown'):
            continue
        median = display_value(normalize_record({'value': reference['median'], 'unit': unit}))
        own = row['clinician_display']
        if own == median:
            continue
        orientation = 'lower' if row.get('direction') == 'lower_is_better' else 'higher'
        prefix = 'Roster-relative support' if relation == 'support' else 'Roster-relative area to review'
        text = (f'{prefix}: {row["label"]} {own} versus {median}, the median of '
                f'{reference["distinct_clinician_count"]} personally supplied same-discipline roster records '
                f'before ZIP filtering ({orientation} is favorable). This is not a branch or national average.')
        output.append({'metric_key': key, 'statement': text})
    return output


def _benchmark_sentence(row):
    """All available primary scopes survive mixed comparisons; secondary values stay in the table."""
    parts = []
    for scope in ('branch', 'national'):
        item = row['comparisons'][scope]
        if item['status'] == 'missing':
            continue
        if item['relation'] in ('favorable', 'attention'):
            side = 'above' if item['full_precision_difference'] > 0 else 'below'
            label = 'favorable' if item['relation'] == 'favorable' else 'area to review'
            text = f'{item["gap_display"]} {side}; {label}'
        elif item['relation'] == 'equal':
            text = 'approximately equal at displayed precision' if item['status'] == 'approximately_equal' else 'equal'
        else:
            text = item['statement'].rstrip('.')
        parts.append(f'{scope} {row[scope+"_display"]} ({text})')
    if not parts or row.get('relation') == 'mixed':
        for scope in ('state', 'organization'):
            item = row['comparisons'][scope]
            if item['status'] != 'missing':
                parts.append(f'{scope} {row[scope+"_display"]}: {item["statement"]}')
    orientation = 'lower' if row.get('direction') == 'lower_is_better' else 'higher'
    return f'{row["label"]} {row["clinician_display"]} ({orientation} is favorable): ' + '; '.join(parts) + '.'


def _appraisal_paragraphs(old, scores, priority_keys):
    rows = {m['metric_key']: m for m in scores.get('metrics', [])}
    # Every metric was assessed; select concise support and material attention for the main page.
    supporting = [key for key in scores.get('supporting_metric_keys', []) if rows[key]['relation'] == 'favorable']
    attention = list(scores.get('attention_metric_keys', []))
    attention.sort(key=lambda key: (key != 'Communications', key not in priority_keys))
    roster_support = _roster_records(old, scores, 'support')
    roster_attention = _roster_records(old, scores, 'attention')
    paragraphs = []; highlighted = []
    if supporting:
        key = supporting[0]; highlighted.append(key)
        paragraphs.append('Supplied-record support: ' + _benchmark_sentence(rows[key]))
    elif roster_support:
        highlighted.append(roster_support[0]['metric_key']); paragraphs.append(roster_support[0]['statement'])
    if attention:
        key = attention[0]; highlighted.append(key)
        prefix = 'Mixed supplied comparisons: ' if rows[key]['relation'] == 'mixed' else 'Area to review: '
        paragraphs.append(prefix + _benchmark_sentence(rows[key]))
    elif roster_attention:
        highlighted.append(roster_attention[0]['metric_key']); paragraphs.append(roster_attention[0]['statement'])
    # Any Communications gap is visible even when it was not a leading contribution.
    gaps = [m for m in rows.values() if m['status'] != 'supplied']
    gaps.sort(key=lambda m: m['metric_key'] != 'Communications')
    if gaps:
        gap = gaps[0]; highlighted.append(gap['metric_key'])
        reason = {'imputed_value': 'no personal value was supplied; scoring used imputation',
                  'missing_personal_value': 'no personal value was supplied',
                  'identity_conflict': 'source identity could not be uniquely bound to this selected record',
                  'source_value_mismatch': 'source value does not match the original value in this decision',
                  'invalid_unit_or_value': 'the original value or its declared unit is invalid'}.get(gap.get('gap_reason'), 'comparable original personal evidence is unavailable')
        paragraphs.append(f'Evidence gap: {gap["label"]}: {reason}. This does not establish poor performance.')
    incompatible = [m for m in rows.values() if any(c.get('status') == 'incompatible' for c in m['comparisons'].values())]
    if incompatible and not (supporting or attention):
        fields = sorted({f for m in incompatible for c in m['comparisons'].values() for f in c.get('incompatible_fields', [])})
        paragraphs.append('Benchmark comparison is not established because supplied ' + ', '.join(field.replace('_', ' ') for field in fields) + ' are incompatible.')
    if not supporting and not roster_support:
        paragraphs.append('No favorable benchmark or roster comparison is established; selection follows the recorded need-matching policy.')
    highlighted = list(dict.fromkeys(highlighted + priority_keys + list(rows)))[:3]
    return paragraphs, highlighted, roster_attention


def build_report_content(full, response, *, request, config_dir):
    explanation = response['distribution_explanation']
    shared = distribution_content(full, explanation)
    scored_people = [c for c in request.get('clinicians', []) if c.get('discipline') in ('RN', 'PT')]
    shared['synthetic_demo'] = bool(request.get('report_demo_synthetic') or (scored_people and all('synthetic' in str(c.get('data_origin', '')).lower() for c in scored_people)))
    shared['factor_interpretations'] = build_factor_interpretations(full, config_dir, request=request)
    priorities = explanation['patient_profile_summary'].get('modeled_priority_metrics', [])
    priority_keys = [m['metric_key'] for m in priorities if m.get('weighted_patient_need', 0) > 0]
    priority_names = [m.get('readable_name', m['metric_key']) for m in priorities if m.get('weighted_patient_need', 0) > 0]
    context = build_scorecard_context(request)
    shared['scorecard_scope_note'] = ('Synthetic demonstration scorecards and benchmarks; not production measurements. ' if shared['synthetic_demo'] else '') + 'Need matching does not always select the maximum matching score. Comparisons describe supplied historical records, not predicted improvement. Benchmarks explain records; they do not drive allocation.'
    options = []
    for option in explanation['options']:
        team = {}
        for discipline, old in option['team'].items():
            card = {key: old.get(key) for key in ('decision_id', 'option_rank', 'discipline', 'clinician_id', 'clinician_name', 'status')}
            card['selection_text'] = selection_text(discipline, old)
            if old.get('clinician_id') is None or discipline == 'OT':
                card.update(needs_text='', scorecard={'metrics': [], 'status': 'not_performance_ranked' if discipline == 'OT' else 'null_slot'},
                            paragraphs=[card['selection_text']], attention_text='', evidence_gaps=[])
            else:
                scores = clinician_scorecard(context, old, priority_keys=priority_keys)
                card['scorecard'] = scores
                card['needs_text'] = ('The current assessment emphasizes ' + ', '.join(priority_names[:2]) +
                                      '; the supplied records below help explain this match.') if priority_names else 'The recommendation follows the recorded policy; no positive model priority is available.'
                paragraphs, highlighted, roster_attention = _appraisal_paragraphs(old, scores, priority_keys)
                card['highlighted_metric_keys'] = highlighted
                card['roster_attention'] = [r['statement'] for r in roster_attention]
                card['paragraphs'] = [card['selection_text'], card['needs_text']] + paragraphs
                card['evidence_gaps'] = scores.get('gap_statements', [])
                card['technical_selection'] = copy.deepcopy(old.get('selection_evidence', {}))
            team[discipline] = card
        options.append({'option_rank': option['option_rank'], 'option_label': option['option_label'], 'team': team})
    content = {'schema_version': SCHEMA_VERSION, 'decision_id': response['decision_id'],
               'shared': shared, 'options': options, 'scorecard_source_audit': context.get('source_audit', {}),
               'audience': 'agency', 'status': 'recommendations_only_not_invitations'}
    from hhvbp_clinician_brief import bind_recipient_context
    content['recipient_context'] = bind_recipient_context(request, response)
    return content


def get_agency_report_view(response, option_rank=None):
    """Reusable CS content: combined/common once, alternatives without shared context."""
    content = response.get('report_content')
    if not isinstance(content, dict) or content.get('decision_id') != response.get('decision_id'):
        raise ValueError('A result-bound report_content namespace is required.')
    options = content['options']
    legacy = {o['option_rank']: o for o in response['distribution_explanation']['options']}
    if len(options) != len(legacy) or len({o['option_rank'] for o in options}) != len(options):
        raise ValueError('Report options must remain bound to the original decision.')
    for option in options:
        original = legacy.get(option['option_rank'])
        if original is None or set(option['team']) != set(original['team']):
            raise ValueError('Report option does not match the original decision.')
        for discipline, card in option['team'].items():
            if any(card.get(key) != original['team'][discipline].get(key) for key in
                   ('decision_id', 'option_rank', 'discipline', 'clinician_id', 'clinician_name')):
                raise ValueError('Report clinician identity does not match the original decision.')
    if option_rank is not None:
        if isinstance(option_rank, bool) or not isinstance(option_rank, int):
            raise ValueError('option_rank must be an integer rank from this result.')
        options = [o for o in options if o['option_rank'] == option_rank]
        if len(options) != 1:
            raise ValueError('The selected option does not exist in this decision.')
    include_shared = option_rank is None or option_rank == 1
    shared = None
    if include_shared:
        exp = response['distribution_explanation']
        shared = {**content['shared'], 'patient_distribution': exp['patient_distribution'],
                  'patient_profile_summary': exp['patient_profile_summary'],
                  'policy_regions': exp.get('report_refinement', {}).get('policy_regions', {})}
    return copy.deepcopy({'schema_version': SCHEMA_VERSION, 'decision_id': response['decision_id'],
                          'view_kind': 'combined' if option_rank is None else 'top_match' if include_shared else 'alternative',
                          'selected_option_rank': option_rank, 'shared': shared, 'options': options})
