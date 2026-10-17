"""Complete, JSON-serializable reporting output; legacy backend_interface is unchanged.

Use only server-held responses for subsequent selection/export. Identity checks
detect accidental mixing, not malicious edits to an entire response.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from backend_interface import CONFIG_DIR, create_request_payload
from hhvbp_assignment_service import assign_with_audit, build_simple_assignment_output
from hhvbp_distribution_explanations import SCHEMA_VERSION as EVIDENCE_VERSION, build_distribution_explanations
from hhvbp_report_content import build_report_content, get_agency_report_view
from hhvbp_clinician_brief import build_clinician_briefs

SCHEMA_VERSION = 'aptatio_reporting.v1'
DISCIPLINES = ('RN', 'PT', 'OT')


def _internal_response(response: dict) -> dict:
    """Reconstruct the frozen renderer input without storing a duplicate snapshot."""
    if response.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('Unsupported reporting response schema_version.')
    agency = response['agency_report']
    snapshot = {
        'schema_version': EVIDENCE_VERSION,
        'decision_id': response['decision_id'], 'request_id': response.get('request_id'),
        'assignments': response['assignments'],
        'distribution_explanation': agency['evidence'], 'report_content': agency['content'],
    }
    get_agency_report_view(snapshot)  # Validate decision/option/clinician binding.
    options = snapshot['distribution_explanation']['options']
    expected = {d: [o['team'][d]['assignment_value'] for o in options] for d in DISCIPLINES}
    if snapshot['assignments'] != expected:
        raise ValueError('Assignment lists do not match the frozen report options.')
    if not snapshot['decision_id'] or any(
        o['option_rank'] != index or any(c.get('decision_id') != snapshot['decision_id']
                                       for c in o['team'].values())
        for index, o in enumerate(options, 1)
    ):
        raise ValueError('Report options must belong to one ordered decision.')
    return snapshot


def _validate_email(response: dict, snapshot: dict) -> None:
    email = response['clinician_email']
    rank = email['selected_option_rank']
    if email.get('delivery_performed') is not False or not isinstance(email.get('items'), list):
        raise ValueError('The reporting API produces unsent email drafts only.')
    if rank is None:
        if email['items'] or email.get('status') != 'not_selected':
            raise ValueError('Email drafts must be empty before an option is selected.')
        return
    view = get_agency_report_view(snapshot, rank)
    team = view['options'][0]['team']
    expected = {(d, c['clinician_id']) for d, c in team.items() if c['clinician_id'] is not None}
    actual = []
    for item in email['items']:
        key = (item.get('discipline'), item.get('clinician_id'))
        if (key not in expected or item.get('decision_id') != snapshot['decision_id']
                or item.get('selected_option_rank') != rank or item.get('status') != 'draft'
                or item.get('delivery_performed') is not False):
            raise ValueError('Email recipient does not match the selected decision and option.')
        actual.append(key)
    if email.get('status') != 'drafts_ready' or len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError('Expected exactly one draft per selected nonempty clinician slot.')


def assign_from_request(request: dict, value_key: str = 'clinician_id', *,
                        selected_option_rank: int | None = None) -> dict:
    """Accept the existing request unchanged; allocate once, then build all content."""
    full = assign_with_audit(request=request, config_dir=CONFIG_DIR, include_explanations=True)
    assignments = build_simple_assignment_output(full['recommended_groups'], value_key=value_key)
    decision_id = str(uuid4())  # Never supplied to allocation or OT rotation.
    evidence = build_distribution_explanations(
        full, decision_id=decision_id, assignments=assignments,
        value_key=value_key, config_dir=CONFIG_DIR, request=request,
    )
    snapshot = {'schema_version': EVIDENCE_VERSION, 'decision_id': decision_id,
                'request_id': full.get('request_id'), 'assignments': assignments,
                'distribution_explanation': evidence}
    content = build_report_content(full, snapshot, request=request, config_dir=CONFIG_DIR)
    response = {
        'schema_version': SCHEMA_VERSION, 'decision_id': decision_id,
        'request_id': full.get('request_id'), 'assignments': assignments,
        'agency_report': {'content': content, 'evidence': evidence},
        'clinician_email': {'status': 'not_selected', 'selected_option_rank': None,
                            'items': [], 'delivery_performed': False},
    }
    _internal_response(response)
    return select_option(response, selected_option_rank) if selected_option_rank is not None else response


def assign_from_components(patient: dict, clinicians: list[dict], request_id: str | None = None,
                           options: dict | None = None, value_key: str = 'clinician_name', *,
                           selected_option_rank: int | None = None) -> dict:
    """Same DB-derived arguments/defaults as backend_interface, with a larger output."""
    request = create_request_payload(patient, clinicians, request_id=request_id, options=options)
    return assign_from_request(request, value_key=value_key, selected_option_rank=selected_option_rank)


def select_option(response: dict, selected_option_rank: int | None) -> dict:
    """Select/clear an option on a saved response, without allocation or input JSON.

    Repeating the same selection reuses its existing draft references and content.
    A different selection creates fresh drafts; this function never sends them.
    """
    if selected_option_rank is not None and (isinstance(selected_option_rank, bool) or not isinstance(selected_option_rank, int)):
        raise ValueError('selected_option_rank must be an integer rank or None.')
    snapshot = _internal_response(response)
    _validate_email(response, snapshot)
    result = copy.deepcopy(response)
    if selected_option_rank == response['clinician_email']['selected_option_rank']:
        return result
    if selected_option_rank is None:
        result['clinician_email'] = {'status': 'not_selected', 'selected_option_rank': None,
                                    'items': [], 'delivery_performed': False}
    else:
        get_agency_report_view(snapshot, selected_option_rank)
        items = build_clinician_briefs(snapshot, selected_option_rank=selected_option_rank, status='draft')
        result['clinician_email'] = {'status': 'drafts_ready', 'selected_option_rank': selected_option_rank,
                                    'items': items, 'delivery_performed': False}
    return result


def write_response_json(response: dict, output_path: str | Path) -> Path:
    """Write the complete response atomically as one UTF-8 JSON file."""
    snapshot = _internal_response(response)
    _validate_email(response, snapshot)
    data = json.dumps(response, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    target = Path(output_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         prefix='.aptatio-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def write_agency_pdf(response: dict, output_path: str | Path, *, option_rank: int | None = None) -> Path:
    """Render the complete agency report, or one option, from the saved JSON."""
    from hhvbp_distribution_pdf import write_distribution_pdf
    return write_distribution_pdf(_internal_response(response), output_path, option_rank=option_rank)


def write_clinician_pdfs(response: dict, output_dir: str | Path) -> list[Path]:
    """Render only the selected clinician drafts; return [] before selection."""
    from hhvbp_clinician_brief import write_clinician_brief_pdf
    snapshot = _internal_response(response)
    _validate_email(response, snapshot)
    return [write_clinician_brief_pdf(item, output_dir) for item in response['clinician_email']['items']]
