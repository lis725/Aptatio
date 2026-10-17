"""Input/assignment compatibility, frozen selection, evidence and recipient privacy."""
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backend_interface as legacy
import reporting_interface as api
import hhvbp_assignment_service as engine
import hhvbp_distribution_explanations as distribution
from hhvbp_report_content import distribution_content


def request():
    return json.loads((ROOT / 'examples/reporting_synthetic_request.json').read_text(encoding='utf-8'))


class ReportingInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.response = api.assign_from_request(request())

    def test_all_five_existing_inputs_work_unchanged_and_preserve_full_engine_output(self):
        paths = [ROOT / 'examples/example_assignment_request.json', *sorted((ROOT / 'examples').glob('threshold_*.json'))]
        self.assertEqual(len(paths), 5)
        for path in paths:
            with self.subTest(path=path.name):
                value = json.loads(path.read_text(encoding='utf-8'))
                before = copy.deepcopy(value)
                old = engine.assign_with_audit(value, config_dir=api.CONFIG_DIR)
                captured = engine.assign_with_audit(value, config_dir=api.CONFIG_DIR, include_explanations=True)
                self.assertTrue(captured.pop('explanation_evidence')['selected_slots'])
                self.assertEqual(old, captured)
                response = api.assign_from_request(value)
                self.assertEqual(response['assignments'], legacy.assign_from_request(value))
                self.assertEqual(response['agency_report']['evidence']['status'], 'complete')
                self.assertEqual(value, before)

    def test_components_keep_existing_defaults_and_database_objects(self):
        value = request(); before = copy.deepcopy(value)
        for key in ('clinician_name', 'clinician_id'):
            old = legacy.assign_from_components(value['patient'], value['clinicians'], value_key=key)
            new = api.assign_from_components(value['patient'], value['clinicians'], value_key=key)
            self.assertEqual(new['assignments'], old)
        self.assertEqual(value, before)
        default = api.assign_from_components(value['patient'], value['clinicians'])
        self.assertIsInstance(default['assignments']['RN'][0], str)

    def test_one_allocation_and_no_implicit_email_selection(self):
        value = request(); value.pop('request_id')
        with patch.object(api, 'assign_with_audit', wraps=api.assign_with_audit) as allocate:
            response = api.assign_from_request(value)
        self.assertEqual(allocate.call_count, 1)
        self.assertNotIn('request_id', value)
        self.assertEqual(set(response), {'schema_version', 'decision_id', 'request_id', 'assignments', 'agency_report', 'clinician_email'})
        self.assertEqual(response['clinician_email'], {'status':'not_selected', 'selected_option_rank':None, 'items':[], 'delivery_performed':False})

    def test_json_roundtrip_selection_is_frozen_idempotent_and_does_not_allocate(self):
        before = copy.deepcopy(self.response)
        with tempfile.TemporaryDirectory() as directory:
            path = api.write_response_json(self.response, Path(directory) / 'response.json')
            restored = json.loads(path.read_text(encoding='utf-8'))
        with patch.object(api, 'assign_with_audit', side_effect=AssertionError('Allocated again')):
            selected = api.select_option(restored, 2)
            self.assertEqual(api.select_option(selected, 2), selected)
            self.assertEqual(api.select_option(selected, None), before)
        self.assertEqual(self.response, before)
        self.assertEqual(selected['assignments'], before['assignments'])
        self.assertEqual(selected['agency_report'], before['agency_report'])
        team = before['agency_report']['content']['options'][1]['team']
        self.assertEqual({(x['discipline'], x['clinician_id']) for x in selected['clinician_email']['items']},
                         {(d, c['clinician_id']) for d,c in team.items()})
        self.assertTrue(all(x['preview'] and not x['delivery_performed'] for x in selected['clinician_email']['items']))

    def test_invalid_ranks_and_mixed_decisions_are_rejected(self):
        selected = api.select_option(self.response, 1)
        for rank in (True, False, '1', 1.0, 0, -1, 1000):
            with self.subTest(rank=rank), self.assertRaises(ValueError):
                api.select_option(selected, rank)
        for mutate in (
            lambda r: r.update(decision_id='different'),
            lambda r: r['assignments']['RN'].__setitem__(0, 999),
            lambda r: r['agency_report']['content']['options'][0]['team']['RN'].update(clinician_id=999),
            lambda r: r['clinician_email']['items'][0].update(selected_option_rank=2),
        ):
            damaged = copy.deepcopy(selected); mutate(damaged)
            with self.assertRaises(ValueError): api.select_option(damaged, 2)

    def test_no_clinicians_for_null_slots_and_zero_options(self):
        value = request(); value['clinicians'] = [p for p in value['clinicians'] if p['discipline'] != 'OT']
        response = api.assign_from_request(value, selected_option_rank=1)
        self.assertEqual({x['discipline'] for x in response['clinician_email']['items']}, {'RN','PT'})
        self.assertTrue(all(x is None for x in response['assignments']['OT']))
        value['options'] = {'top_k_groups': 0}
        response = api.assign_from_request(value)
        self.assertEqual(response['assignments'], {'RN':[], 'PT':[], 'OT':[]})
        with self.assertRaises(ValueError): api.select_option(response, 1)

    def test_no_local_archive_lookup_and_missing_benchmarks_remain_missing(self):
        with patch('zipfile.ZipFile', side_effect=AssertionError('Local archive accessed')):
            response = api.assign_from_request(request(), selected_option_rank=1)
        self.assertEqual(response['agency_report']['content']['scorecard_source_audit']['status'], 'not_supplied')
        rows = response['agency_report']['content']['options'][0]['team']['RN']['scorecard']['metrics']
        self.assertTrue(any(r['status'] == 'supplied' for r in rows))
        self.assertTrue(all(r['comparisons']['national']['status'] == 'missing' for r in rows))

    def test_optional_supplied_benchmarks_do_not_change_assignments(self):
        value = request()
        value['scorecard_evidence'] = {
            'clinicians': [{'clinician_id': p['clinician_id'], 'discipline': p['discipline'],
                           'metrics': {'PPH': {'value':p['metrics']['PPH'], 'unit':'proportion'}}}
                          for p in value['clinicians'] if p['discipline'] != 'OT'],
            'benchmarks':[{'scope':'national', 'metric_key':'PPH', 'value':.30, 'unit':'proportion'}],
        }
        response = api.assign_from_request(value)
        self.assertEqual(response['assignments'], self.response['assignments'])
        rows = response['agency_report']['content']['options'][0]['team']['RN']['scorecard']['metrics']
        pph = next(r for r in rows if r['metric_key'] == 'PPH')
        self.assertEqual(pph['national_display'], '30%')

    def test_missing_personal_metric_is_not_reported_as_an_observed_score(self):
        value = request()
        for person in value['clinicians']:
            if person['discipline'] == 'RN': person['metrics']['Communications'] = None
        response = api.assign_from_request(value)
        rows = response['agency_report']['content']['options'][0]['team']['RN']['scorecard']['metrics']
        row = next(r for r in rows if r['metric_key'] == 'Communications')
        self.assertNotEqual(row['status'], 'supplied')
        self.assertEqual(row['gap_reason'], 'imputed_value')

    def test_recipient_whitelist_excludes_patient_location_and_other_teams(self):
        response = copy.deepcopy(self.response)
        content = response['agency_report']['content']
        secret = 'PATIENT_PRIVATE_MARKER'
        content['shared']['patient_name'] = secret
        content['shared']['narrative'] = secret
        for option in content['options']:
            for card in option['team'].values():
                card['paragraphs'].append(secret)
                for row in card.get('scorecard', {}).get('metrics', []):
                    row['personal_evidence']['source'] = {'locator': secret}
        selected = api.select_option(response, 2)
        text = json.dumps(selected['clinician_email'])
        self.assertNotIn(secret, text)
        self.assertNotIn('12345', text)
        self.assertNotIn('synthetic_patient', text)
        for item in selected['clinician_email']['items']:
            body = item['plain_text_body']
            for option in content['options']:
                for d, card in option['team'].items():
                    if card['clinician_id'] != item['clinician_id']:
                        self.assertNotIn(card['clinician_name'], body)

    def test_missing_reference_keeps_assignments_without_fabricating_a_chart(self):
        with patch.object(distribution, '_read_bound_reference', side_effect=distribution.DistributionEvidenceUnavailable('REFERENCE_UNREADABLE')):
            response = api.assign_from_request(request())
        self.assertEqual(response['assignments'], self.response['assignments'])
        self.assertIsNone(response['agency_report']['evidence']['patient_distribution']['chart_data'])

    def test_percentile_rounding_preserves_full_precision_classification(self):
        full = {'threshold_routing': {'enabled':True, 'zip_history_enabled':True, 'threshold_percentile':.42}}
        for value, group in ((.82733273,'higher'), (math.nextafter(.42,0.),'lower'), (.42,'lower'), (math.nextafter(.42,1.),'higher')):
            content = distribution_content(full, {'patient_distribution': {'assignment_percentile':value}})
            self.assertEqual(content['classification_basis']['assignment_percentile'], value)
            self.assertEqual(content['needs_group'], group)
            if value == .82733273:
                self.assertEqual(content['position_sentence'], 'This patient is at percentile 82.7, above the threshold.')

    def test_json_write_failure_keeps_existing_file_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'response.json'; path.write_text('previous', encoding='utf-8')
            with patch.object(api.os, 'replace', side_effect=OSError('write failed')):
                with self.assertRaises(OSError): api.write_response_json(self.response, path)
            self.assertEqual(path.read_text(), 'previous')
            self.assertEqual([p.name for p in Path(directory).iterdir()], ['response.json'])

    def test_nonfinite_json_is_rejected_before_overwriting_output(self):
        value = copy.deepcopy(self.response)
        value['agency_report']['content']['shared']['invalid'] = float('nan')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'response.json'
            with self.assertRaises(ValueError): api.write_response_json(value, path)
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
