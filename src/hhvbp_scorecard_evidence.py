"""Source-bound descriptive scorecards. Never called by assignment/scoring code.

Optional request.scorecard_evidence supplies clinician records and scoped benchmark
records. Only the current request supplies scorecards; no local archives or
network sources are accessed. No imputation/config constant is a source.
Whole percentages use Decimal ROUND_HALF_UP; 0<p<0.005 renders '<1%' and
0.995<=p<1 renders '>99%' so rounding never invents zero/perfection.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from hhvbp_local_externality import METRIC_ORDER

SCHEMA_VERSION = 'supplied_scorecard.v1'
SCOPES = ('branch', 'national', 'state', 'organization')
MISSING = 'Not provided in supplied scorecard'
LABELS = {'PPH': 'Potentially preventable hospitalization', 'DTC': 'Discharge to community',
          'DFS': 'Discharge function', 'Oral_Meds': 'Oral medication management',
          'Dyspnea': 'Improvement in dyspnea', 'Care_of_Patients': 'Care of patients',
          'Communications': 'Communications', 'Care_Issues': 'Care issues',
          'Agency_Rating': 'Agency rating', 'Recommend': 'Willingness to recommend'}



def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, default=str).encode()).hexdigest()


def _decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value).strip())
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _unit(unit):
    name = str(unit or '').strip().lower()
    return {'fraction': 'proportion', 'percent': 'percentage', '%': 'percentage',
            'normalized_score': 'score', 'normalized_value': 'score', 'auto': 'unknown'}.get(name, name or 'unknown')


def normalize_record(record):
    """Preserve supplied precision; only explicit fraction/percentage units convert."""
    item = copy.deepcopy(record) if isinstance(record, dict) else {'value': record}
    raw = item.get('value'); unit = _unit(item.get('unit')); numeric = _decimal(raw)
    if unit == 'percentage' and isinstance(raw, str) and raw.strip().endswith('%'):
        numeric = _decimal(raw.strip()[:-1])
    normalized = numeric / 100 if numeric is not None and unit == 'percentage' else numeric
    valid = normalized is not None
    if unit in ('proportion', 'percentage'):
        valid = valid and 0 <= normalized <= 1
    item.update(raw_value=raw, unit=unit, normalized_value=float(normalized) if valid else None,
                normalized_decimal=str(normalized) if valid else None,
                comparable_unit='proportion' if unit in ('proportion','percentage') else unit,
                value_status='supplied' if valid else 'missing_or_invalid',
                evidence_sha256=_digest(record))
    return item


def display_value(record):
    record = normalize_record(record) if 'normalized_decimal' not in record else record
    value = _decimal(record.get('normalized_decimal'))
    if value is None:
        return MISSING
    unit = record.get('comparable_unit')
    if unit == 'proportion':
        percent = value * 100
        rounded = percent.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        if value > 0 and rounded == 0:
            return '<1%'
        if value < 1 and rounded == 100:
            return '>99%'
        return f'{rounded}%'
    number = format(value.normalize(), 'f')
    if unit in ('unknown', 'score'):
        return number + (' (unit not supplied)' if unit == 'unknown' else ' score units')
    return f'{number} {unit}'


def _gap_display(gap, percentage):
    value = abs(gap * 100 if percentage else gap)
    rounded = value.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    amount = '<1' if value and rounded == 0 else str(rounded) if percentage else format(value.normalize(), 'f')
    return amount + (' percentage points' if percentage else ' units')


def compare_records(personal, benchmark, direction, scope):
    """No visible advantage for equal rounded displays; known conflicts suppress claims."""
    result = {'scope': scope, 'status': 'missing', 'relation': 'unknown',
              'gap_percentage_points': None, 'gap_display': None, 'statement': MISSING}
    if not benchmark:
        return result
    result.update(benchmark_evidence_sha256=benchmark.get('evidence_sha256'),
                  personal_evidence_sha256=personal.get('evidence_sha256'))
    if personal.get('normalized_value') is None or benchmark.get('normalized_value') is None:
        result.update(status='unassessable', statement='Personal value or benchmark is unavailable.')
        return result
    incompatible = []
    pu, bu = personal.get('comparable_unit'), benchmark.get('comparable_unit')
    if pu != bu or pu == 'unknown':
        incompatible.append('unit')
    for field in ('definition','reporting_period','branch_id','state_id'):
        if field == 'branch_id' and scope != 'branch':
            continue
        if field == 'state_id' and scope != 'state':
            continue
        a, b = personal.get(field), benchmark.get(field)
        if a is not None and b is not None and str(a) != str(b):
            incompatible.append(field)
    for row in (personal, benchmark):
        if row.get('direction') and row['direction'] != direction:
            incompatible.append('direction')
    if direction not in ('lower_is_better','higher_is_better'):
        incompatible.append('direction')
    if incompatible:
        result.update(status='incompatible', incompatible_fields=sorted(set(incompatible)),
                      statement='Comparison unavailable: incompatible ' + ', '.join(field.replace('_', ' ') for field in sorted(set(incompatible))) + '.')
        return result
    p = Decimal(personal['normalized_decimal']); b = Decimal(benchmark['normalized_decimal']); gap = p-b
    result.update(status='comparable', full_precision_difference=float(gap),
                  gap_percentage_points=float(gap*100) if pu == 'proportion' else None,
                  gap_display=_gap_display(gap, pu == 'proportion'),
                  qualification='Supplied scorecard comparison; historical period/source metadata may be incomplete.')
    name = scope.capitalize() + (' average' if scope != 'organization' else ' value')
    if not gap or display_value(personal) == display_value(benchmark):
        result.update(status='approximately_equal' if gap else 'comparable', relation='equal',
                      statement=f'Approximately equal to the {scope} value at the displayed precision.' if gap else f'Equal to the supplied {scope} value.')
    else:
        favorable = gap < 0 if direction == 'lower_is_better' else gap > 0
        result.update(relation='favorable' if favorable else 'attention',
                      statement=f'{result["gap_display"]} {"above" if gap > 0 else "below"} the {scope} value; '
                                + ('favorable' if favorable else 'an area to review')
                                + f' ({"lower" if direction == "lower_is_better" else "higher"} is favorable).')
    return result






def build_scorecard_context(request, *, source_bundle=None):
    """Build once per frozen decision. Optional evidence cannot change request/selection.

    scorecard_evidence = {clinicians:[{clinician_id,discipline,metrics:{key:
       {value,unit,definition,reporting_period,branch_id,state_id,source}}}],
       benchmarks:[{scope,metric_key,value,unit,...same metadata...}],source_kind:...}
    A caller may supply source metadata; immutable record hashes always bind values.
    The adapter does not fetch caller-provided paths or trust them as verified files.
    """
    copied = copy.deepcopy(request or {})
    explicit = source_bundle if source_bundle is not None else copied.get('scorecard_evidence')
    audit = {'status':'caller_supplied', 'limitation':'Caller-declared source metadata is not independently verified.'}
    if explicit is None:
        audit = {'status': 'not_supplied', 'limitation': 'Only request clinician metrics are available; no benchmark records were supplied.'}
    bundle = copy.deepcopy(explicit) if isinstance(explicit, dict) else {}
    if not isinstance(bundle.get('clinicians',[]), list) or not isinstance(bundle.get('benchmarks',[]),list):
        bundle = {}; audit={'status':'invalid_schema','limitation':'Expected clinician and benchmark lists.'}
    return {'schema_version':SCHEMA_VERSION, 'request_clinicians':copied.get('clinicians',[]),
            'metric_metadata':copied.get('metric_metadata',{}), 'bundle':bundle,
            'source_audit':audit, 'bundle_sha256':_digest(bundle)}


def _person_record(context, card):
    def matches(r):
        return isinstance(r,dict) and str(r.get('clinician_id')) == str(card.get('clinician_id')) and str(r.get('discipline','')).upper() == card.get('discipline')
    from hhvbp_assignment_service import ensure_clinician_id
    def request_matches(r):
        if not isinstance(r, dict) or str(r.get('discipline','')).strip().upper()!=card.get('discipline'):
            return False
        try:
            return str(ensure_clinician_id(r))==str(card.get('clinician_id'))
        except ValueError:
            return False
    people = [r for r in context['request_clinicians'] if request_matches(r)]
    records = [r for r in context['bundle'].get('clinicians',[]) if matches(r)]
    person = people[0] if len(people)==1 else None
    source = records[0] if len(records)==1 else None
    return person, source, len(records)>1 or len(people)>1


def clinician_scorecard(context, card, priority_keys=()):
    """Result-bound original values and all applicable descriptive comparisons."""
    base = {'schema_version':SCHEMA_VERSION, 'decision_id':card.get('decision_id'),
            'option_rank':card.get('option_rank'),'clinician_id':card.get('clinician_id'),
            'discipline':card.get('discipline'), 'metrics':[], 'supporting_metric_keys':[],
            'attention_metric_keys':[], 'evidence_gap_metric_keys':[], 'supporting_statements':[],
            'attention_statements':[], 'gap_statements':[], 'source_audit':context['source_audit'],
            'bundle_sha256':context['bundle_sha256'], 'scope_note':'Supplied scorecard values (source/period unverified)' + '; descriptive differences are not predicted improvement and were not added to the allocation score.',
            'display_rule':'Nearest whole percent, decimal half-up; small nonzero <1%, non-perfect rounded 100% >99%.',
            'benchmark_order':['branch','national']}
    if card.get('discipline') == 'OT' or card.get('clinician_id') is None:
        base['status']='not_performance_ranked'
        return base
    person, source, identity_conflict = _person_record(context,card)
    order = list(dict.fromkeys(list(priority_keys)+METRIC_ORDER))
    for metric in card.get('metric_evidence',[]):
        key=metric['metric_key']; weight=metric.get('effective_discipline_weight')
        if not isinstance(weight,(int,float)) or weight<=0 or key not in LABELS:
            continue
        metadata=context.get('metric_metadata',{}).get(key,{}) if isinstance(context.get('metric_metadata'),dict) else {}
        metadata=metadata if isinstance(metadata,dict) else {}
        raw = person.get('metrics',{}).get(key) if person else None
        local = person.get('metric_metadata',{}).get(key,{}) if person and isinstance(person.get('metric_metadata'),dict) else {}
        local=local if isinstance(local,dict) else {}
        supplied = {**metadata, **local, 'value':raw,
                    'unit':local.get('unit',metadata.get('unit',person.get('metric_scale') if person else None)),
                    'source':{'locator':'request.clinicians[clinician_id='+str(card.get('clinician_id'))+'].metrics.'+key,
                              'provenance':'caller_supplied_personal_value'}}
        source_metric = source.get('metrics',{}).get(key) if source and isinstance(source.get('metrics'),dict) else None
        if isinstance(source_metric,dict):
            supplied={**{k:v for k,v in source.items() if k in ('definition','reporting_period','branch_id','state_id')},**source_metric}
        record=normalize_record(supplied)
        expected=metric.get('normalized_supplied_value')
        aligned=(not identity_conflict and metric.get('value_source')=='observed' and expected is not None
                 and record.get('normalized_value') is not None and abs(record['normalized_value']-expected)<=1e-12)
        gap_reason = ('identity_conflict' if identity_conflict or person is None else
                      'imputed_value' if str(metric.get('value_source','')).startswith('imputed') else
                      'missing_personal_value' if raw is None or expected is None else
                      'invalid_unit_or_value' if record.get('normalized_value') is None else
                      'source_value_mismatch' if not aligned else None)
        # Unknown source scale can be displayed as supplied, never converted to %.
        if not aligned:
            record['normalized_value']=None;record['normalized_decimal']=None
        comparisons={}; benchmark_records={}
        for scope in SCOPES:
            candidates = [b for b in context['bundle'].get('benchmarks',[]) if isinstance(b,dict) and b.get('scope')==scope and b.get('metric_key')==key]
            benchmark=normalize_record(candidates[0]) if len(candidates)==1 and source is not None else None
            comparison=compare_records(record,benchmark,metric.get('direction'),scope)
            if len(candidates)>1:
                comparison.update(status='incompatible',relation='unknown',statement='Multiple benchmark records require an unambiguous binding.')
            comparisons[scope]=comparison;benchmark_records[scope]=benchmark
        label=LABELS[key]
        row={'metric_key':key,'label':label,'clinician_display':display_value(record),
             'status':'supplied' if aligned else 'evidence_gap','gap_reason':gap_reason,'direction':metric.get('direction'),
             'comparisons':comparisons, 'personal_evidence':record,'benchmark_evidence':benchmark_records,
             'original_value':raw, 'normalized_supplied_value':expected,
             'scoring_value':metric.get('value_for_scoring'),'scoring_adjustment_applied':metric.get('scoring_adjustment_applied'),
             'capability_roster_percentile':metric.get('capability_percentile'),
             'identity_binding':'conflicting_or_missing_identity' if identity_conflict or person is None else source.get('source_binding','explicit_id_and_discipline') if source else 'request_id_and_discipline_only'}
        for scope in SCOPES:
            row[scope+'_display']=display_value(benchmark_records[scope]) if benchmark_records[scope] else MISSING
        relations={c['relation'] for c in comparisons.values()}
        row['relation']=('mixed' if {'favorable','attention'}<=relations else 'attention' if 'attention' in relations else
                         'favorable' if 'favorable' in relations else 'equal' if 'equal' in relations else 'unknown')
        if not aligned:
            row['interpretation'] = {'imputed_value':'No personal value was supplied; scoring used imputation. Missing records are not poor performance.',
                'missing_personal_value':'No personal value was supplied. Missing records are not poor performance.',
                'identity_conflict':'Personal scorecard binding is ambiguous; a unique ID and discipline are required.',
                'invalid_unit_or_value':'The supplied source value or its declared scale is invalid; comparison is unavailable.',
                'source_value_mismatch':'The source value does not match the original value in this frozen decision; comparison is unavailable.'}.get(gap_reason,'Personal scorecard evidence is unavailable.')
        else:
            usable=[f'{scope.capitalize()} {row[scope+"_display"]}: {c["statement"]}' for scope,c in comparisons.items() if c['status'] not in ('missing','unassessable')]
            row['interpretation']=' '.join(usable) if usable else 'No compatible supplied benchmark comparison is established.'
        row['statement']=f'{label}: supplied personal value {row["clinician_display"]}. ' + row['interpretation']
        base['metrics'].append(row)
    base['metrics'].sort(key=lambda r:order.index(r['metric_key']) if r['metric_key'] in order else len(order))
    support=[r for r in base['metrics'] if r['relation']=='favorable']
    attention=[r for r in base['metrics'] if r['relation'] in ('attention','mixed')]
    attention.sort(key=lambda r: r['metric_key'] != 'Communications')
    gaps=[r for r in base['metrics'] if r['status']=='evidence_gap']
    base.update(status='available' if any(r['status']=='supplied' for r in base['metrics']) else 'evidence_limited',
                supporting_metric_keys=[r['metric_key'] for r in support],
                attention_metric_keys=[r['metric_key'] for r in attention],
                evidence_gap_metric_keys=[r['metric_key'] for r in gaps],
                supporting_statements=[r['statement'] for r in support[:2]],
                attention_statements=[r['statement'] for r in attention[:2]],
                gap_statements=[r['label']+': '+r['interpretation'] for r in gaps])
    if any(r['state_display']!=MISSING for r in base['metrics']):base['benchmark_order'].append('state')
    if any(r['organization_display']!=MISSING for r in base['metrics']):base['benchmark_order'].append('organization')
    base['highlighted_metric_keys']=list(dict.fromkeys(base['supporting_metric_keys'][:2]+base['attention_metric_keys'][:1]))
    if not base['highlighted_metric_keys']:
        base['highlighted_metric_keys']=[r['metric_key'] for r in base['metrics'][:3]]
    if 'synthetic' in str(context['bundle'].get('source_kind','')).lower():
        base['scope_note']='Synthetic demonstration scorecard values; descriptive differences are not predicted improvement and were not added to the allocation score.'
    base['technical_details']={'source_audit':context['source_audit'],'bundle_sha256':context['bundle_sha256'],
                               'metadata_qualification':'Unknown period, definition or source authenticity stays unverified. Known conflicts suppress comparisons.',
                               'source_kind':context['bundle'].get('source_kind','caller_supplied'),
                               'identity_source':{k:copy.deepcopy(v) for k,v in (source or {}).items() if k in ('clinician_id','discipline','source_row','source_binding','alias_evidence')}}
    return base
