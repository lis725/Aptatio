"""Result-bound model-direction annotations; never score or allocate a patient.

The reference is the deployed favorability transform's fixed midpoint. It is
neither a clinical neutral category nor an outcome/percentage benchmark.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from hhvbp_local_externality import (
    RAW_DIRECTION, SEVEN_FACTOR_ORDER, adjust_favorability,
    build_expert_prior_for_metric, deployed_weights_for_metric,
    get_metric_favorability, resolve_display_calibration,
)

DIRECTION_STYLE = {
    'favorable': ('Favorable factor', 'up', '#217A42'),
    'adverse': ('Less favorable factor', 'down', '#B63838'),
    'neutral': ('Neutral at model reference', 'neutral', '#68717A'),
    'mixed': ('Mixed/model-dependent', 'mixed', '#68717A'),
    'unknown': ('Direction not established', 'unknown', '#68717A'),
}
LEGEND = (
    'Green up means a favorable factor; red down means a less favorable factor in '
    'the current model relative to its favorability midpoint. '
    'These are model annotations, not changes '
    'over time, clinical judgments, causal effects or predicted VBP score changes.'
)
SOURCE_FUNCTIONS = [
    'src/hhvbp_local_externality.py:adjust_favorability',
    'src/hhvbp_local_externality.py:get_metric_favorability',
    'src/hhvbp_local_externality.py:deployed_weights_for_metric',
    'src/hhvbp_local_externality.py:resolve_display_calibration',
]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('Expected a finite numeric model parameter.')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Expected a finite numeric model parameter.')
    return value


def _bound_config(full, config_dir):
    configs, provenance, problems = {}, {}, []
    for name in ('hhvbp_global_config.json', 'metric_parameter_library.json'):
        expected = full.get('parameter_versions', {}).get(name, {}).get('sha256')
        record = {'source_locator': None if config_dir is None else name,
                  'decision_sha256': expected, 'sha256': None, 'binding_status': 'unavailable'}
        try:
            if config_dir is None or not isinstance(expected, str):
                raise ValueError('Result-bound configuration hash or directory is unavailable.')
            raw = (Path(config_dir) / name).read_bytes()
            record['sha256'] = hashlib.sha256(raw).hexdigest()
            if record['sha256'] != expected:
                record['binding_status'] = 'mismatch'
                raise ValueError('Configuration bytes do not match this decision.')
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError('Configuration is not an object.')
            configs[name] = parsed
            record['binding_status'] = 'verified'
        except (OSError, ValueError, TypeError, KeyError) as error:
            problems.append(f'{name}: {error}')
        provenance[name] = record
    return configs, provenance, problems


def _supplied_value(request, key, normalized_value):
    if not isinstance(request, dict) or not isinstance(request.get('patient'), dict):
        return normalized_value, 'result.patient.local_externalities.' + key, 'normalized_result_only'
    patient = request['patient']
    local = patient.get('local_externalities')
    local = local if isinstance(local, dict) else {}
    if key == 'age_group':
        candidates = [(patient, 'age_group', 'request.patient.age_group'),
                      (local, 'age_group', 'request.patient.local_externalities.age_group'),
                      (patient, 'age', 'request.patient.age'),
                      (patient, 'patient_age', 'request.patient.patient_age'),
                      (local, 'age', 'request.patient.local_externalities.age')]
    else:
        source = local if 'local_externalities' in patient else patient
        prefix = 'request.patient.local_externalities.' if 'local_externalities' in patient else 'request.patient.'
        candidates = [(source, key, prefix + key)]
    for source, field, locator in candidates:
        if source.get(field) is not None:
            return copy.deepcopy(source[field]), locator, 'supplied'
    return None, None, 'not_supplied'


def _metric_context(global_cfg, key, metric_cfg):
    """Reuse the deployed transform/weights, without running need or assignment."""
    weight = _number(metric_cfg['composite_weight'])
    if weight < 0:
        raise ValueError('Negative composite weight has no supported report orientation.')
    if weight == 0:
        return {'metric_key': key, 'composite_weight': weight, 'active': False}
    prior = build_expert_prior_for_metric(global_cfg, metric_cfg)
    deployed = deployed_weights_for_metric(metric_cfg, prior)
    weights = {row['externality']: _number(row['runtime_weight']) for _, row in deployed.iterrows()}
    if any(value < 0 for value in weights.values()):
        raise ValueError('Negative factor weight has no supported report orientation.')
    favorability = get_metric_favorability(global_cfg, metric_cfg)
    gap = _number(metric_cfg['gap_scale'])
    # Derive the fixed point from the real deployed zero-gap transform. Do not
    # assume that every probability, score or other model has a neutral 0.5.
    anchor = _number(adjust_favorability(0., 0.))
    if (adjust_favorability(1., 0.) != anchor or adjust_favorability(anchor, gap) != anchor):
        raise ValueError('The deployed favorability transform has no verified fixed midpoint.')
    best = sum(100. * weights[factor] * adjust_favorability(
        _number(favorability[factor][global_cfg['profiles']['best_case'][factor]]), gap)
        for factor in global_cfg['factor_order'])
    worst = sum(100. * weights[factor] * adjust_favorability(
        _number(favorability[factor][global_cfg['profiles']['worst_case'][factor]]), gap)
        for factor in global_cfg['factor_order'])
    calibration = resolve_display_calibration(key, metric_cfg, global_cfg,
                                              {'latent_mean': best}, {'latent_mean': worst})
    raw_direction = RAW_DIRECTION.get(key)
    if raw_direction not in ('higher_is_better', 'lower_is_better'):
        raise ValueError('Metric raw direction is unavailable in the deployed model.')
    # A calibrated display may reverse the numeric metric (as it does for PPH).
    # An uncalibrated latent score remains a favorability scale, not a raw rate.
    orientation = 1.
    if calibration['use_display_scale']:
        _number(calibration['national_average'])
        orientation = _number(calibration['direction']) * (1. if raw_direction == 'higher_is_better' else -1.)
    return {'metric_key': key, 'composite_weight': weight, 'active': True,
            'weights': weights, 'favorability': favorability, 'gap_scale': gap, 'anchor': anchor,
            'raw_direction': raw_direction, 'display_scale_applied': calibration['use_display_scale'],
            'display_transform_direction': calibration['direction'] if calibration['use_display_scale'] else None,
            'outlook_orientation': orientation,
            'orientation_basis': ('deployed display transform and metric raw direction' if calibration['use_display_scale']
                                  else 'uncalibrated latent favorability scale; not a raw metric rate')}


def _factor_metric(factor, value, key, cfg, context):
    evidence = {'metric_key': key, 'composite_weight': context['composite_weight'],
                'active': context['active'], 'direction': 'inactive'}
    if not context['active']:
        evidence['reason'] = 'Zero composite weight; excluded from direction combination.'
        return evidence
    factor_weight = context['weights'][factor]
    evidence['factor_runtime_weight'] = factor_weight
    if factor_weight == 0:
        evidence.update(active=False, reason='Zero deployed factor weight; excluded from direction combination.')
        return evidence
    raw = _number(context['favorability'][factor][value])
    if not 0 <= raw <= 1:
        raise ValueError('Configured category favorability lies outside the unit interval.')
    transformed = _number(adjust_favorability(raw, context['gap_scale']))
    deviation = (transformed - context['anchor']) * context['outlook_orientation']
    # Exact equality is intentional: there is no invented fuzzy neutral category.
    direction = 'favorable' if deviation > 0 else 'adverse' if deviation < 0 else 'neutral'
    override = value in cfg.get('favorability_overrides', {}).get(factor, {})
    evidence.update(direction=direction, category_favorability=raw,
                    transformed_favorability=transformed, reference_anchor=context['anchor'],
                    gap_scale=context['gap_scale'], metric_raw_direction=context['raw_direction'],
                    display_scale_applied=context['display_scale_applied'],
                    display_transform_direction=context['display_transform_direction'],
                    outlook_orientation=context['outlook_orientation'],
                    orientation_basis=context['orientation_basis'],
                    favorability_source=('metric_parameter_library.json#/metrics/' + key + '/favorability_overrides/' + factor + '/' + str(value)
                                         if override else 'hhvbp_global_config.json#/base_favorability/' + factor + '/' + str(value)),
                    metric_override_applied=override,
                    comparison='transformed category versus deployed fixed midpoint; not a causal benefit estimate')
    return evidence


def build_factor_interpretations(full, config_dir, request=None):
    """Return seven auditable factor rows from one already selected result.

    Opposing active metric signs remain mixed. Any unavailable active semantics
    prevent a binary claim; no new weighted aggregation or assignment occurs.
    """
    profile = full.get('patient', {}).get('local_externalities', {})
    profile = profile if isinstance(profile, dict) else {}
    configs, provenance, problems = _bound_config(full, config_dir)
    global_cfg = configs.get('hhvbp_global_config.json', {})
    library = configs.get('metric_parameter_library.json', {})
    if not problems and global_cfg.get('factor_order') != SEVEN_FACTOR_ORDER:
        problems.append('Bound configuration does not contain the deployed seven-factor order.')
    metrics = library.get('metrics', {})
    if not isinstance(metrics, dict) or not metrics:
        problems.append('Bound metric definitions and active composite weights are unavailable.')
        metrics = {}
    contexts = {}
    if not problems:
        for key, cfg in metrics.items():
            try:
                contexts[key] = _metric_context(global_cfg, key, cfg)
            except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError) as error:
                contexts[key] = {'error': str(error)}
    factors = []
    for factor in SEVEN_FACTOR_ORDER:
        value = profile.get(factor)
        supplied, locator, supplied_status = _supplied_value(request, factor, value)
        missing = list(problems)
        evidence = []
        if not problems:
            for key, cfg in metrics.items():
                try:
                    context = contexts[key]
                    if 'error' in context:
                        raise ValueError(context['error'])
                    evidence.append(_factor_metric(factor, value, key, cfg, context))
                except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError) as error:
                    missing.append(f'{key}: {error}')
                    evidence.append({'metric_key': key, 'active': None, 'direction': 'unknown', 'reason': str(error)})
        active = [entry for entry in evidence if entry.get('active') is True]
        signs = {entry['direction'] for entry in active}
        if {'favorable', 'adverse'} <= signs:
            direction = 'mixed'
        elif missing or not active:
            direction = 'unknown'
            if not missing and not active:
                missing.append('No metric has both a positive composite weight and a positive deployed weight for this factor.')
        elif 'favorable' in signs:
            direction = 'favorable'
        elif 'adverse' in signs:
            direction = 'adverse'
        else:
            direction = 'neutral'
        label, symbol, color = DIRECTION_STYLE[direction]
        anchors = sorted({entry['reference_anchor'] for entry in active})
        factors.append({'factor_key': factor, 'supplied_value': supplied, 'supplied_locator': locator,
                        'supplied_value_status': supplied_status, 'normalized_value': value,
                        'direction': direction, 'label': label, 'textual_interpretation': label,
                        'symbol': symbol, 'color': color,
                        'reference': {'kind': 'deployed_favorability_fixed_midpoint',
                                      'anchor': anchors[0] if len(anchors) == 1 else None,
                                      'meaning': 'Model-scale reference only; not a clinically neutral patient category.'},
                        'metric_evidence': evidence, 'missing_semantics': missing,
                        'config_provenance': provenance})
    return {'schema_version': 'factor_interpretations.v1',
            'status': 'available' if all(row['direction'] != 'unknown' and not row['missing_semantics'] for row in factors) else 'partial_or_unavailable',
            'legend': LEGEND,
            'anchor': {'kind': 'deployed_favorability_fixed_midpoint',
                       'derivation': 'adjust_favorability(0, 0), verified equal to adjust_favorability(1, 0) and fixed under each active gap_scale',
                       'meaning': 'Mathematical midpoint in this deployed transform, not universal or clinical neutrality.'},
            'combination_rule': 'Opposing active metric signs remain mixed; otherwise require known semantics for all active measures. Neutral measures do not cancel a signed direction. No new weighted aggregate is calculated.',
            'provenance': {'configuration': provenance, 'source_functions': SOURCE_FUNCTIONS,
                           'excluded_from_direction': ['reliability/kappa/variance', 'patient ZIP', 'matching scores', 'percentile overrides'],
                           'display_scale_constants_are_not_benchmarks': True},
            'factors': factors}
