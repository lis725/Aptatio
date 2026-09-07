from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from generate_pdf_reports import (  # noqa: E402
    build_pdf_manifest,
    link_source_bundle_manifest,
    pdf_manifest_identity_sha256,
    render_report,
    source_bundle_identity_sha256,
)


class PdfReportingTests(unittest.TestCase):
    def test_pdf_render_records_pages_bytes_and_checksum(self) -> None:
        source = PROJECT_ROOT / "reports" / "current" / "nontechnical_report.md"
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "nontechnical_report.pdf"
            record = render_report(source, destination)
            self.assertTrue(destination.read_bytes().startswith(b"%PDF-"))
            self.assertGreaterEqual(record["pages"], 1)
            self.assertEqual(record["bytes"], destination.stat().st_size)
            self.assertEqual(record["sha256"], hashlib.sha256(destination.read_bytes()).hexdigest())
            self.assertEqual(record["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())

    def test_pdf_manifest_is_linked_into_source_bundle_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "sponsor_report.md"
            source.write_text("# Sponsor report\n\nCurrent test content.\n", encoding="utf-8")
            source_manifest = root / "report_manifest.json"
            pdf_manifest = root / "pdf_manifest.json"
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            source_manifest.write_text(
                json.dumps(
                    {
                        "artifact_type": "current_hhvbp_report_bundle_manifest",
                        "schema_version": "1.0",
                        "generated_at_utc": "2026-07-15T00:00:00Z",
                        "artifacts": [
                            {"path": source.name, "sha256": source_digest, "bytes": source.stat().st_size}
                        ],
                        "pdf_or_latex_generated": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            record = render_report(source, root / "sponsor_report.pdf")
            pdf_payload = build_pdf_manifest(
                source_manifest,
                [record],
                created_at_utc="2026-07-15T00:00:00Z",
            )
            pdf_manifest.write_text(json.dumps(pdf_payload, indent=2) + "\n", encoding="utf-8")
            link_source_bundle_manifest(
                source_manifest,
                pdf_manifest,
                created_at_utc="2026-07-15T00:00:00Z",
                report_count=1,
            )
            payload = json.loads(source_manifest.read_text(encoding="utf-8"))
            self.assertTrue(payload["pdf_or_latex_generated"])
            self.assertTrue(payload["pdf_generated"])
            self.assertFalse(payload["latex_generated"])
            self.assertEqual(payload["pdf_report_count"], 1)
            self.assertEqual(payload["pdf_manifest_path"], str(pdf_manifest.resolve()))
            self.assertEqual(payload["pdf_link_status"], "current")
            self.assertEqual(payload["pdf_manifest_identity_sha256"], pdf_payload["manifest_identity_sha256"])
            self.assertEqual(payload["pdf_manifest_file_sha256"], hashlib.sha256(pdf_manifest.read_bytes()).hexdigest())
            self.assertEqual(
                payload["pdf_source_bundle_identity_sha256"],
                source_bundle_identity_sha256(payload),
            )
            self.assertEqual(
                pdf_payload["manifest_identity_sha256"],
                pdf_manifest_identity_sha256(pdf_payload),
            )

    def test_changed_source_fails_closed_and_clears_current_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "current_model_report.md"
            source.write_text("# Current model\n\nOriginal.\n", encoding="utf-8")
            source_manifest = root / "report_manifest.json"
            source_manifest.write_text(
                json.dumps(
                    {
                        "artifact_type": "current_hhvbp_report_bundle_manifest",
                        "schema_version": "1.0",
                        "artifacts": [
                            {
                                "path": source.name,
                                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                                "bytes": source.stat().st_size,
                            }
                        ],
                        "pdf_or_latex_generated": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            record = render_report(source, root / "current_model_report.pdf")
            pdf_payload = build_pdf_manifest(
                source_manifest,
                [record],
                created_at_utc="2026-07-15T00:00:00Z",
            )
            pdf_manifest = root / "pdf_manifest.json"
            pdf_manifest.write_text(json.dumps(pdf_payload, indent=2) + "\n", encoding="utf-8")
            source.write_text("# Current model\n\nChanged after rendering.\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "changed after rendering"):
                link_source_bundle_manifest(
                    source_manifest,
                    pdf_manifest,
                    created_at_utc="2026-07-15T00:00:00Z",
                    report_count=1,
                )
            payload = json.loads(source_manifest.read_text(encoding="utf-8"))
            self.assertFalse(payload["pdf_or_latex_generated"])
            self.assertFalse(payload["pdf_generated"])
            self.assertEqual(payload["pdf_link_status"], "not_current")


if __name__ == "__main__":
    unittest.main()
