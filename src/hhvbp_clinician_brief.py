"""Recipient-only drafts from one frozen agency decision; no allocation or sending.

The content builder uses an explicit whitelist. Original patient inputs, location,
source locators, other team members and arbitrary agency prose never reach the
recipient artifact. Coverage is bound once, before the original request is lost.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import UUID, uuid4

from hhvbp_assignment_service import (
    RequestValidationError, clinician_match_keys_from_values, ensure_clinician_id,
    extract_patient_zip, extract_zip_history_rows, first_present_value,
    parse_zip_history_value,
)

DISCIPLINES = ('RN', 'PT', 'OT')
LABELS = {
    'PPH': 'Potentially preventable hospitalization', 'DTC': 'Discharge to community',
    'DFS': 'Discharge function', 'Oral_Meds': 'Oral medication management',
    'Dyspnea': 'Improvement in dyspnea', 'Recommend': 'Willingness to recommend',
    'Agency_Rating': 'Overall agency rating', 'Care_Issues': 'Specific care issues',
    'Communications': 'Communications', 'Care_of_Patients': 'Care of patients',
}
SCOPES = ('branch', 'national', 'state', 'organization')
MISSING = 'Not provided in supplied scorecard'


def _key(discipline, identifier):
    return f'{discipline}:{identifier}'


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
                                    separators=(',', ':')).encode()).hexdigest()


def _row_keys(row):
    return clinician_match_keys_from_values(
        clinician_id=first_present_value(row, ['clinician_id', 'Clinician ID', 'id', 'ID']),
        clinician_name=first_present_value(row, ['clinician_name', 'Clinician Name', 'name', 'Name']),
        blinded_name=first_present_value(row, ['blinded_name', 'Blinded Name', 'Blinded Names', 'blinded']),
        discipline=first_present_value(row, ['discipline', 'Discipline']),
    )


def _coverage_record(values, patient_zip, patient_status, locators, ambiguous=False):
    """No actual ZIP or invalid token is returned, even in technical evidence."""
    zips, invalid, supplied = set(), 0, False
    for value in values:
        # A matched separate row without its required history list is malformed.
        if value is None:
            invalid += 1
        parsed, audit = parse_zip_history_value(value)
        supplied |= bool(audit['input_present'])
        zips.update(parsed)
        invalid += len(audit['invalid_zip_tokens'])
    if patient_status != 'valid':
        status, reason = 'unverified', 'patient_location_unavailable'
    elif ambiguous:
        status, reason = 'unverified', 'ambiguous_identity_record'
    elif invalid:
        status, reason = 'unverified', 'material_parse_failure'
    elif not supplied or not zips:
        status, reason = 'unverified', 'history_missing_or_empty'
    elif patient_zip in zips:
        status, reason = 'within', 'listed_in_valid_supplied_record'
    else:
        status, reason = 'outside', 'not_listed_in_valid_supplied_record'
    return {'status': status, 'reason': reason, 'valid_code_count': len(zips),
            'invalid_token_count': invalid, 'source_locators': locators,
            'source_sha256': _hash(values), 'scope': 'supplied_record_only'}


def bind_recipient_context(request: dict, response: dict) -> dict:
    """Freeze coverage facts alongside the same decision, without raw location.

    Inline preferred_zip_codes and separate clinician_coverage_preferences rows
    are an optional explanation-only contract. They never affect allocation.
    """
    try:
        zipcode = extract_patient_zip(request.get('patient', {}))
        patient_status = 'valid' if zipcode is not None else 'missing'
    except (RequestValidationError, TypeError, ValueError):
        zipcode, patient_status = None, 'invalid'
    records = {}
    for row in request.get('clinicians', []):
        if not isinstance(row, dict):
            continue
        discipline = str(row.get('discipline', '')).upper().strip()
        try:
            identifier = ensure_clinician_id(row)
        except (RequestValidationError, TypeError, ValueError):
            continue
        key = _key(discipline, identifier)
        rec = records.setdefault(key, {'discipline': discipline, 'clinician_id': identifier,
            'keys': set(), 'history': [], 'preferred': [], 'history_locators': [],
            'preferred_locators': [], 'ambiguous': False})
        rec['keys'].update(_row_keys(row))
        for kind, fields in [('history', ['zip_codes_treated', 'Zip Codes Treated']),
                             ('preferred', ['preferred_zip_codes'])]:
            value = first_present_value(row, fields)
            if value is not None:
                rec[kind].append(value)
                source_field = next(field for field in fields
                                    if field in row and row[field] not in (None, ''))
                # Identity selectors stay valid when the supplied roster is reordered.
                rec[kind + '_locators'].append(
                    f'request.clinicians[discipline={discipline},clinician_id={identifier}].{source_field}')
    collections = [('history', extract_zip_history_rows(request)),
                   ('preferred', request.get('clinician_coverage_preferences', []))]
    for kind, rows in collections:
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            keys = _row_keys(row)
            discipline = first_present_value(row, ['discipline', 'Discipline'])
            matches = [rec for rec in records.values() if keys & rec['keys'] and
                       (discipline is None or str(discipline).strip().upper() == rec['discipline'])]
            if len(matches) != 1:
                for rec in matches:
                    rec['ambiguous'] = True
                continue
            rec = matches[0]
            fields = ['zip_codes_treated', 'Zip Codes Treated', 'zip_codes', 'ZIP Codes'] if kind == 'history' else ['preferred_zip_codes']
            rec[kind].append(first_present_value(row, fields))
            rec[kind + '_locators'].append(f'request.{"zip_history_rows" if kind == "history" else "clinician_coverage_preferences"}[{index}]')
    bound = {}
    for key, rec in records.items():
        bound[key] = {'discipline': rec['discipline'], 'clinician_id': rec['clinician_id'],
            'history': _coverage_record(rec['history'], zipcode, patient_status,
                                       rec['history_locators'], rec['ambiguous']),
            'preferred': _coverage_record(rec['preferred'], zipcode, patient_status,
                                         rec['preferred_locators'], rec['ambiguous'])}
    return {'schema_version': 'recipient_context.v1', 'decision_id': response['decision_id'],
            'coverage_by_clinician': bound}


def _safe_display(value):
    """Only numeric displays/known absence labels; never forward source prose."""
    text = str(value or '').strip()
    if re.fullmatch(r'(?:[~<>\u2248]\s*)?-?\d+(?:\.\d+)?(?:%|\s+(?:points?|percentage points|days?|visits?|score units|units|score|\(unit not supplied\)))?', text):
        return text
    if text in (MISSING, 'Not supplied', 'Unavailable', 'Not comparable', 'Not recorded',
                'No supplied personal value', 'Not provided'):
        return text
    return 'Unavailable'


def _metric_rows(scorecard, attention_fallback_keys=()):
    candidates = {row.get('metric_key'): row for row in scorecard.get('metrics', [])
                  if row.get('metric_key') in LABELS}
    supports = [key for key in scorecard.get('supporting_metric_keys', []) if key in candidates]
    attention = [key for key in list(scorecard.get('attention_metric_keys', [])) + list(attention_fallback_keys) if key in candidates]
    # Include one real attention point even if Communications was not a leading contribution.
    order = list(dict.fromkeys(supports[:2] + attention[:1] + list(candidates)))[:3]
    rows = []
    for key in order:
        row = candidates[key]
        safe = {'metric_key': key, 'label': LABELS[key],
                'clinician_display': _safe_display(row.get('clinician_display')),
                'direction': row.get('direction') if row.get('direction') in ('lower_is_better', 'higher_is_better') else 'unknown',
                'comparisons': {}}
        for scope in SCOPES:
            comparison = row.get('comparisons', {}).get(scope, {})
            status = comparison.get('status')
            safe[scope + '_display'] = _safe_display(row.get(scope + '_display', MISSING))
            safe['comparisons'][scope] = {
                'status': status if status in ('comparable', 'approximately_equal', 'missing', 'incompatible', 'unassessable') else 'unassessable',
                'relation': comparison.get('relation') if comparison.get('relation') in ('favorable', 'attention', 'equal') else 'unknown',
                'gap_display': _safe_display(comparison.get('gap_display')),
            }
        rows.append(safe)
    return rows


# These labels describe recorded measures, never a patient's diagnosis or treatment.
RECORD_LABELS = {**{k: v.lower() + ' result' for k, v in LABELS.items()},
                 'DFS': 'discharge-function result',
                 'PPH': 'potentially preventable hospitalization result'}
SUBJECTS = {'RN': 'Request for your support with a nursing assignment',
            'PT': 'Could you take this physical therapy assignment?',
            'OT': 'Could you cover this occupational therapy assignment?'}
SERVICES = {'RN': 'nursing', 'PT': 'physical therapy', 'OT': 'occupational therapy'}
# Meanings follow config/metric_parameter_library.json canonical measure labels.
# Only a positive recorded priority AND the discipline's recorded measure can use a goal.
GOALS = {'Oral_Meds': 'support oral medication management',
         'PPH': 'help avoid preventable hospital stays',
         'DFS': 'support progress with everyday activities'}
ACTION = ('Please let us know whether you can accept, need to decline, '
          'or need more information before deciding.')
PREVIEW_BANNER = 'PREVIEW ONLY - uses fictional test data. Do not send.'


def _coverage_sentence(coverage):
    history = coverage.get('history', {}).get('status')
    sentence = {
        'within': 'Our records show that you have provided care in this area before.',
        'outside': ('Our records do not show previous care by you in this area, '
                    'so we would like to check whether you can cover it.'),
    }.get(history, 'We do not have enough information to confirm your previous service in this area.')
    # Preference is a separate explicit source, not an inference from service history.
    preferred = coverage.get('preferred', {}).get('status')
    if preferred == 'within':
        sentence += ' The area is also listed in your recorded preferences.'
    elif preferred == 'outside':
        sentence += ' The area is not listed in your recorded preferences; please let us know whether you would consider it.'
    return sentence


def _usable_record(original, row):
    return (original.get('status') == 'supplied'
            and original.get('personal_evidence', {}).get('comparable_unit') not in (None, 'unknown')
            and row['clinician_display'] not in (MISSING, 'Unavailable', 'Not supplied', 'Not recorded'))


def _comparison_scopes(row):
    scopes = [scope for scope in SCOPES if row['comparisons'][scope]['status'] in
              ('comparable', 'approximately_equal') and row[scope + '_display'] not in
              (MISSING, 'Unavailable', 'Not supplied', 'Not recorded')]
    primary = [scope for scope in scopes if scope in ('branch', 'national')]
    # Never omit a contrary secondary benchmark while presenting supporting comparisons.
    positive_primary = any(row['comparisons'][scope]['relation'] == 'favorable' for scope in primary)
    return primary + [scope for scope in scopes if scope not in primary and
                      (not primary or positive_primary and row['comparisons'][scope]['relation'] == 'attention')]


def _reviewable(comparison):
    # Display-precision guard, not a new clinical or statistical significance threshold.
    return (comparison['status'] == 'comparable' and comparison['relation'] == 'attention'
            and comparison.get('gap_display') != '<1 percentage points')


def _record_sentence(row, *, attention=False):
    label = RECORD_LABELS[row['metric_key']]
    if row['metric_key'] == 'PPH' and row['clinician_display'].endswith('%'):
        label = 'potentially preventable hospitalization rate'
    prefix = ('One point to review is the ' + label + ' in your record: ') if attention else ('Your recorded ' + label + ' is ')
    sentence = prefix + row['clinician_display']
    scopes = _comparison_scopes(row)
    scope_labels = {'branch': 'the branch average', 'national': 'the national average',
                    'state': 'the state average', 'organization': 'the organization value'}
    if scopes:
        sentence += ', compared with ' + ' and '.join(row[s + '_display'] + ' for ' + scope_labels[s] for s in scopes)
    if row['direction'] == 'lower_is_better':
        sentence += '; a lower result is favorable'
    sentence += '.'
    if attention:
        sentence += ' Please flag any errors or missing context so the team can review the record and discuss any support needed.'
    return sentence


def _curated_content(response, shared, card, actual, coverage, discipline, preview, status):
    """Deterministic, allowlisted communication; all source evidence stays in the response."""
    scorecard = card.get('scorecard', {})
    originals = {r['metric_key']: r for r in scorecard.get('metrics', []) if r.get('metric_key') in LABELS}
    safe_rows = {key: _metric_rows({'metrics': [row]})[0] for key, row in originals.items()}
    available = {key: row for key, row in safe_rows.items() if _usable_record(originals[key], row)}
    origin = actual.get('selection_evidence', {}).get('data_origin', {}).get('classification')
    synthetic = bool(shared.get('synthetic_demo') or origin == 'synthetic_declared' or 'synthetic' in
                     str(scorecard.get('technical_details', {}).get('source_kind', '')).lower())
    mixed = origin == 'mixed_declared'
    override = bool(shared.get('override_active'))
    forced_preview = bool(preview or synthetic or mixed or override)
    banner = (PREVIEW_BANNER if synthetic else
              'PREVIEW ONLY - includes fictional test data. Do not send.' if mixed else
              'PREVIEW ONLY - uses a test assessment. Do not send.' if override else
              'PREVIEW ONLY - review copy. Do not send.' if preview else
              'Draft - not sent' if status == 'draft' else 'Request copy - delivery not verified')
    name = str(card.get('clinician_name') or '').strip()
    greeting = 'Hi ' + name + ',' if name else 'Hello,'
    request = 'Would you be available to take on a new ' + SERVICES.get(discipline, 'home-health') + ' assignment?'
    need = ('The patient information available suggests that additional support may be helpful.'
            if shared.get('needs_group') == 'higher' and not override else '')
    route = actual.get('routing_evidence', {}).get('route')
    coverage_text = _coverage_sentence(coverage)
    reason = ('We considered a wider group of clinicians for this request. '
              if route == 'zip_history_fallback_full_pool' else '') + coverage_text
    # Selection remains a service/needs-based request even without a personal advantage.
    support_keys = [key for key in scorecard.get('supporting_metric_keys', []) if key in available]
    candidates = list(dict.fromkeys(support_keys + list(available)))
    attention_keys = [key for key in scorecard.get('attention_metric_keys', []) if key in available and any(
        _reviewable(c) for c in available[key]['comparisons'].values())]
    attention_keys.sort(key=lambda key: key != 'Communications')
    attention_key = attention_keys[0] if attention_keys else None
    # At most two facts plus one material review point, without calling mixed results a strength.
    selected = [key for key in candidates if key != attention_key and
                (key in support_keys or not any(_reviewable(c) for c in available[key]['comparisons'].values()))][:2]
    supporting = [_record_sentence(available[key]) for key in selected]
    attention = _record_sentence(available[attention_key], attention=True) if attention_key else ''
    selected_keys = selected + ([attention_key] if attention_key else [])
    review_refs = []
    # Existing roster attention remains usable only with a real supplied personal result,
    # explicit comparison status, known units, and a visible difference after rounding.
    if not attention and discipline != 'OT':
        from hhvbp_scorecard_evidence import display_value, normalize_record
        for item in actual.get('report_appraisal', {}).get('metrics', []):
            key = item.get('metric_key'); ref = item.get('reference', {})
            if key not in available or item.get('relation') != 'attention' or ref.get('status') != 'comparable':
                continue
            row = available[key]
            if any(c['status'] in ('comparable', 'approximately_equal', 'incompatible') for c in row['comparisons'].values()):
                continue
            unit = originals[key]['personal_evidence']['comparable_unit']
            median = _safe_display(display_value(normalize_record({'value': ref.get('median'), 'unit': unit})))
            if median in (row['clinician_display'], MISSING, 'Unavailable'):
                continue
            attention = (f"One point to review is your {RECORD_LABELS[key]}: {row['clinician_display']}, "
                         f"compared with {median}, the median in the available records of other {discipline} clinicians. "
                         'This is not a branch or national average. Please flag any errors or missing context for the team to review.')
            review_refs = ['report_appraisal.metrics.' + key + '.reference']
            if key not in selected_keys:
                selected_keys.append(key)
            break
    priorities = response.get('distribution_explanation', {}).get('patient_profile_summary', {}).get('modeled_priority_metrics', [])
    positive = [p.get('metric_key') for p in priorities if isinstance(p.get('weighted_patient_need'), (int, float))
                and p['weighted_patient_need'] > 0]
    goal_keys = [key for key in selected if key in positive and key in GOALS
                 and (key != 'Oral_Meds' or discipline == 'RN')][:2]
    if override:
        goal_keys = []
    goal = ('Our aim is to ' + ' and '.join(GOALS[key] for key in goal_keys) + '.' if goal_keys else
            "Our aim is to support the patient's care as part of the home-health team.")
    qualifications = []
    gaps = [key for key in scorecard.get('evidence_gap_metric_keys', []) if key in LABELS]
    gaps.sort(key=lambda key: key != 'Communications')
    if discipline != 'OT':
        if not selected_keys:
            qualifications.append('We do not have enough personal records to describe your previous results for this request.')
        elif not any(_comparison_scopes(available[key]) for key in selected_keys):
            qualifications.append('A suitable branch or national comparison is not available.')
        if gaps:
            qualifications.append('A personal ' + RECORD_LABELS[gaps[0]] + ' is not available for review; missing information is not a poor result.')
        if not synthetic and selected_keys:
            qualifications.append("Please confirm the source and reporting period of these records; the figures do not predict this patient's outcome.")
    # OT never uses the RN/PT scorecard or fabricates an OT performance story.
    if discipline == 'OT':
        supporting, attention, selected_keys, goal_keys, qualifications = [], '', [], [], []
        goal = ("We are looking for someone to support the patient's care as part of the home-health team. "
                'Please let us know about any coverage or scheduling concerns before deciding.')
    parts = [request + (' ' + need if need else ''), reason]
    if supporting:
        parts.append(' '.join(supporting))
    parts.append(goal)
    if attention:
        parts.append(attention)
    if qualifications:
        parts.append(' '.join(qualifications))
    parts.append(ACTION)
    refs = {'request': ['selected_option_rank', 'clinician_id', 'discipline'],
            'need_context': ['shared.needs_group'] if need else [],
            'selection_route': route if route in ('zip_history', 'global_high_risk', 'zip_history_fallback_full_pool',
                'routing_disabled_full_pool', 'zip_history_disabled_full_pool') else 'unknown',
            'coverage': ['recipient_context.history.status', 'recipient_context.preferred.status'],
            'records': {key: _comparison_scopes(available[key]) for key in selected_keys},
            'care_goals': goal_keys, 'attention': [attention_key] if attention_key else review_refs,
            'missing_records': gaps, 'needs_group': shared.get('needs_group') if shared.get('needs_group') in ('higher','lower') else 'unknown',
            'override_active': override, 'synthetic_demo': synthetic,
            'data_origin_classification': origin if origin in ('synthetic_declared','mixed_declared','supplied_label_unverified') else 'unknown'}
    return {'subject': SUBJECTS.get(discipline, 'Could you consider this care assignment?'),
            'greeting': greeting, 'paragraphs': parts, 'closing': 'Thank you,\nThe agency scheduling team',
            'status_banner': banner, 'preview': forced_preview, 'evidence_references': refs,
            'compact_metric_rows': [available[key] for key in selected_keys]}


def build_clinician_briefs(response: dict, *, selected_option_rank=None, preview=False,
                           status='draft') -> list[dict]:
    """Whitelist recipient content only after validating an explicit option.

    Preview without a rank uses the first returned option and labels every
    artifact as a preview. Status is caller-supplied wording, never a side effect.
    """
    if status not in ('draft', 'requested'):
        raise ValueError('Recipient content supports draft/requested wording only; it does not record acceptance.')
    if selected_option_rank is None and not preview:
        raise ValueError('An explicit selected_option_rank is required for recipient drafts.')
    if isinstance(selected_option_rank, bool) or (selected_option_rank is not None and not isinstance(selected_option_rank, int)):
        raise ValueError('selected_option_rank must be an integer.')
    content = response.get('report_content', {})
    decision = response.get('decision_id')
    context = content.get('recipient_context', {})
    if not decision or content.get('decision_id') != decision or context.get('decision_id') != decision:
        raise ValueError('Recipient content must be bound to this frozen decision.')
    options = content.get('options', [])
    legacy = response.get('distribution_explanation', {}).get('options', [])
    if selected_option_rank is None:
        selected_option_rank = options[0]['option_rank'] if options else None
    selected = [option for option in options if option.get('option_rank') == selected_option_rank]
    original = [option for option in legacy if option.get('option_rank') == selected_option_rank]
    if len(selected) != 1 or len(original) != 1:
        raise ValueError('Selected option is not a unique returned option.')
    shared = content.get('shared', {})
    briefs, seen = [], set()
    for discipline in DISCIPLINES:
        card = selected[0].get('team', {}).get(discipline, {})
        actual = original[0].get('team', {}).get(discipline, {})
        identifier = card.get('clinician_id')
        for field in ('clinician_id', 'clinician_name', 'discipline', 'option_rank', 'decision_id'):
            if card.get(field) != actual.get(field):
                raise ValueError('Selected recipient identity is not aligned with the frozen option.')
        if identifier is None:
            continue
        key = _key(discipline, identifier)
        if key in seen:
            continue
        seen.add(key)
        coverage = context.get('coverage_by_clinician', {}).get(key, {})
        roster_attention = [row for row in actual.get('report_appraisal', {}).get('metrics', [])
                            if row.get('relation') == 'attention' and row.get('metric_key') in LABELS]
        rows = [] if discipline == 'OT' else _metric_rows(card.get('scorecard', {}),
                                                        [row['metric_key'] for row in roster_attention])
        curated = _curated_content(response, shared, card, actual, coverage, discipline, preview, status)
        reference = str(uuid4())  # Generated after selection; never an allocator seed.
        paragraphs = curated['paragraphs']
        body_parts = [curated['status_banner'], curated['greeting']] + paragraphs + [curated['closing']]
        brief = {'schema_version': 'clinician_assignment_request.v1', 'decision_id': decision,
            'selected_option_rank': selected_option_rank, 'clinician_id': identifier,
            'discipline': discipline, 'clinician_name': str(card.get('clinician_name') or discipline),
            'request_reference': reference, 'status': status, 'preview': curated['preview'],
            'preview_requested': bool(preview),
            'status_source': 'caller_supplied_content_only', 'coverage_status': coverage.get('history', {}).get('status', 'unverified'),
            'preferred_coverage_status': coverage.get('preferred', {}).get('status', 'unverified'),
            'subject': curated['subject'], 'plain_text_body': '\n\n'.join(body_parts),
            'paragraphs': paragraphs, 'greeting': curated['greeting'], 'closing': curated['closing'],
            'status_banner': curated['status_banner'], 'compact_metric_rows': curated['compact_metric_rows'],
            'evidence_references': curated['evidence_references'],
            'metrics': rows, 'allowed_action_labels': ['Accept', 'Decline'],
            'pdf_artifact_reference': None, 'delivery_performed': False}
        briefs.append(brief)
    return briefs


def write_clinician_brief_pdf(brief: dict, directory: str | Path) -> Path:
    """Write an opaque filename; only recipient whitelist fields are rendered."""
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate
    from hhvbp_distribution_pdf import (MARGIN, MUTED, PAGE_WIDTH,
                                        RULE, _p, _styles, report_fonts)
    reference = str(UUID(brief['request_reference']))
    target = Path(directory).resolve() / f'assignment_request_{reference}.pdf'
    target.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    # Local styles only; the accepted agency renderer and its styles are unchanged.
    styles['title'].fontSize = 16
    styles['title'].leading = 20
    styles['body'].fontSize = 11
    styles['body'].leading = 15
    styles['body'].spaceAfter = 10
    story = [_p(brief['status_banner'], styles, 'sub'), _p(brief['subject'], styles, 'title'),
             _p(brief['greeting'], styles)]
    # Identical curated facts and order to the email, with no second technical summary.
    story.extend(_p(paragraph, styles) for paragraph in brief['paragraphs'])
    story.append(_p(brief['closing'], styles))
    story.append(_p('Request reference: ' + reference, styles, 'small'))
    descriptor, temporary = tempfile.mkstemp(prefix='.recipient-', suffix='.pdf', dir=target.parent)
    os.close(descriptor)
    try:
        document = SimpleDocTemplate(temporary, pagesize=letter, leftMargin=MARGIN,
            rightMargin=MARGIN, topMargin=34, bottomMargin=38, title='Aptatio assignment request',
            author='Aptatio', subject='Assignment request ' + reference, pageCompression=1)
        regular, _ = report_fonts()
        def footer(canvas, doc):
            canvas.saveState()
            canvas.setStrokeColor(RULE)
            canvas.line(MARGIN, 30, PAGE_WIDTH - MARGIN, 30)
            canvas.setFillColor(MUTED)
            canvas.setFont(regular, 9)
            canvas.drawString(MARGIN, 17, 'Aptatio | ' + ('Preview' if brief.get('preview') else brief['status'].title()))
            canvas.drawRightString(PAGE_WIDTH - MARGIN, 17, f'Page {doc.page}')
            canvas.restoreState()
        document.build(story, onFirstPage=footer, onLaterPages=footer)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target
