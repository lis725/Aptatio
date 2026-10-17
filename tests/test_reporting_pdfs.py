"""PDFs consume saved JSON and preserve agency/recipient separation."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import reporting_interface as api
import hhvbp_distribution_pdf as pdf

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


def text(path):
    return ' '.join(' '.join(page.extract_text() or '' for page in PdfReader(path).pages).split())


class ReportingPDFTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        request = json.loads((ROOT/'examples/reporting_synthetic_request.json').read_text(encoding='utf-8'))
        cls.response = api.assign_from_request(request, selected_option_rank=2)

    def test_renderers_accept_reloaded_json_without_reallocation_or_mutation(self):
        saved = json.loads(json.dumps(self.response)); before = copy.deepcopy(saved)
        with tempfile.TemporaryDirectory() as temp, patch.object(api, 'assign_with_audit', side_effect=AssertionError('Reallocated')):
            agency = api.write_agency_pdf(saved, Path(temp)/'agency.pdf')
            recipients = api.write_clinician_pdfs(saved, Path(temp)/'clinicians')
            self.assertEqual(len(recipients), 3)
            for path in [agency, *recipients]:
                self.assertTrue(path.read_bytes().startswith(b'%PDF-'))
                self.assertIn(b'%%EOF', path.read_bytes()[-1024:])
            self.assertEqual(api.write_clinician_pdfs(api.select_option(saved, None), Path(temp)/'empty'), [])
        self.assertEqual(saved, before)

    @unittest.skipIf(PdfReader is None, 'Install pypdf to verify extracted PDF text and page counts.')
    def test_agency_pdf_contains_shared_narrative_and_all_option_reasons(self):
        with tempfile.TemporaryDirectory() as temp:
            path = api.write_agency_pdf(self.response, Path(temp)/'agency.pdf')
            rendered = text(path)
        content = self.response['agency_report']['content']
        self.assertIn(content['shared']['position_sentence'], rendered)
        self.assertIn('Density', rendered)
        self.assertIn('Need Score', rendered)
        for option in content['options']:
            self.assertIn(option['option_label'], rendered)
            for card in option['team'].values():
                self.assertIn(card['clinician_name'], rendered)
                for paragraph in card['paragraphs']:
                    self.assertIn(' '.join(paragraph.split()), rendered)

    @unittest.skipIf(PdfReader is None, 'Install pypdf for PDF content checks.')
    def test_alternative_pdf_is_independent_without_repeating_patient_graph(self):
        with tempfile.TemporaryDirectory() as temp:
            path = api.write_agency_pdf(self.response, Path(temp)/'alternative.pdf', option_rank=2)
            rendered = text(path)
        self.assertIn('Alternative 1', rendered)
        self.assertNotIn('Decision overview and patient position', rendered)
        self.assertNotIn('Known patient information', rendered)
        self.assertNotIn('Primary option', rendered)

    @unittest.skipIf(PdfReader is None, 'Install pypdf for PDF content checks.')
    def test_clinician_pdfs_are_one_page_and_match_selected_email_content(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = api.write_clinician_pdfs(self.response, temp)
            for item, path in zip(self.response['clinician_email']['items'], paths):
                self.assertEqual(len(PdfReader(path).pages), 1)
                rendered = text(path)
                for paragraph in item['paragraphs']:
                    self.assertIn(' '.join(paragraph.split()), rendered)
                self.assertIn(item['subject'], rendered)
                self.assertNotIn('12345', rendered)
                self.assertNotIn('synthetic_patient', rendered)

    def test_pdf_failure_retains_saved_json_and_does_not_publish_partial_pdf(self):
        def fail(document, *args, **kwargs):
            Path(document.filename).write_bytes(b'%PDF-partial')
            raise OSError('simulated PDF failure')
        with tempfile.TemporaryDirectory() as temp:
            response_path = api.write_response_json(self.response, Path(temp)/'response.json')
            target = Path(temp)/'agency.pdf'
            with patch.object(pdf.SimpleDocTemplate, 'build', new=fail):
                with self.assertRaises(OSError): api.write_agency_pdf(self.response, target)
            self.assertEqual(json.loads(response_path.read_text()), self.response)
            self.assertFalse(target.exists())
            self.assertEqual([p.name for p in Path(temp).iterdir()], ['response.json'])


if __name__ == '__main__':
    unittest.main()
