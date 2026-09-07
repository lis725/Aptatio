"""Render current Markdown reports to polished, verified-ready PDF files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    XPreformatted,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "reports" / "current"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "pdf"
PDF_MANIFEST_ARTIFACT_TYPE = "current_hhvbp_pdf_report_bundle_manifest"
PDF_MANIFEST_SCHEMA_VERSION = "2.0"

# These fields describe a PDF rendering of a source bundle. They are excluded
# from the source-bundle identity so linking a PDF cannot change the identity
# of the Markdown/CSV/JSON inputs it attests to.
SOURCE_MANIFEST_PDF_LINK_FIELDS = {
    "pdf_or_latex_generated",
    "pdf_generated",
    "latex_generated",
    "pdf_generated_at_utc",
    "pdf_report_count",
    "pdf_manifest_path",
    "pdf_manifest_file_sha256",
    "pdf_manifest_identity_sha256",
    "pdf_source_bundle_identity_sha256",
    "pdf_source_manifest_sha256_before_link",
    "pdf_link_status",
    "pdf_link_error",
}


def _canonical_payload_sha256(payload: dict, *, excluded_keys: set[str] | None = None) -> str:
    filtered = {
        key: value
        for key, value in payload.items()
        if key not in (excluded_keys or set())
    }
    encoded = json.dumps(
        filtered,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_bundle_identity_sha256(source_manifest: dict) -> str:
    """Return an identity that remains stable when PDF-link fields change."""

    return _canonical_payload_sha256(
        source_manifest,
        excluded_keys=SOURCE_MANIFEST_PDF_LINK_FIELDS,
    )


def pdf_manifest_identity_sha256(pdf_manifest: dict) -> str:
    """Hash the canonical PDF-manifest payload without its identity field."""

    return _canonical_payload_sha256(
        pdf_manifest,
        excluded_keys={"manifest_identity_sha256"},
    )


def ascii_safe(value: str) -> str:
    replacements = {
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-",
        "\u2212": "-", "\u00d7": "x", "\u2264": "<=", "\u2265": ">=", "\u2192": "->",
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2022": "-",
        "\u03c1": "rho", "\u03c4": "tau", "\u00a0": " ",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    # Helvetica/Courier core fonts are reliable for ASCII. Preserve spacing but
    # remove any remaining combining/non-ASCII glyphs that could render as boxes.
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


def inline_markup(value: str) -> str:
    text = escape(ascii_safe(value))
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r'<font name="Courier">\1</font>', text)
    return text


def styles_for_pdf():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "AptatioTitle", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=20, leading=24, textColor=colors.HexColor("#17365D"),
            alignment=TA_LEFT, spaceAfter=14,
        ),
        "h2": ParagraphStyle(
            "AptatioH2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=13, leading=16, textColor=colors.HexColor("#1F4E78"),
            spaceBefore=10, spaceAfter=6, keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "AptatioH3", parent=base["Heading3"], fontName="Helvetica-Bold",
            fontSize=10.5, leading=13, textColor=colors.HexColor("#2F5597"),
            spaceBefore=8, spaceAfter=4, keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "AptatioBody", parent=base["BodyText"], fontName="Helvetica",
            fontSize=9.2, leading=12.2, textColor=colors.HexColor("#222222"),
            spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "AptatioBullet", parent=base["BodyText"], fontName="Helvetica",
            fontSize=9.1, leading=12, leftIndent=14, firstLineIndent=-8,
            bulletIndent=2, spaceAfter=3,
        ),
        "code": ParagraphStyle(
            "AptatioCode", parent=base["Code"], fontName="Courier",
            fontSize=7.2, leading=9.2, leftIndent=6, rightIndent=6,
            backColor=colors.HexColor("#F3F5F7"), borderColor=colors.HexColor("#D9E2F3"),
            borderWidth=0.5, borderPadding=6, spaceBefore=4, spaceAfter=7,
        ),
        "table_head": ParagraphStyle(
            "AptatioTableHead", parent=base["BodyText"], fontName="Helvetica-Bold",
            fontSize=6.5, leading=8, textColor=colors.white, alignment=TA_CENTER,
        ),
        "table_cell": ParagraphStyle(
            "AptatioTableCell", parent=base["BodyText"], fontName="Helvetica",
            fontSize=6.2, leading=7.5, textColor=colors.HexColor("#222222"), alignment=TA_LEFT,
        ),
        "source": ParagraphStyle(
            "AptatioSource", parent=base["BodyText"], fontName="Helvetica-Oblique",
            fontSize=7.5, leading=9.5, textColor=colors.HexColor("#666666"), spaceBefore=10,
        ),
    }


def table_from_rows(rows: list[list[str]], available_width: float, styles: dict) -> Table:
    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    lengths = []
    for column in range(column_count):
        maximum = max(len(ascii_safe(row[column])) for row in normalized)
        lengths.append(min(max(maximum, 7), 38))
    total = float(sum(lengths))
    widths = [available_width * value / total for value in lengths]
    data = []
    for row_index, row in enumerate(normalized):
        style = styles["table_head"] if row_index == 0 else styles["table_cell"]
        data.append([Paragraph(inline_markup(cell), style) for cell in row])
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=True)
    # Long appendix tables benefit from slightly tighter vertical padding.  This
    # keeps a handful of trailing rows from becoming an otherwise-empty page,
    # while ordinary decision tables retain the more generous spacing.
    vertical_padding = 1.75 if len(rows) >= 40 else 3
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#B4C7E7")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), vertical_padding),
                ("BOTTOMPADDING", (0, 0), (-1, -1), vertical_padding),
            ]
        )
    )
    return table


def markdown_story(markdown: str, available_width: float, styles: dict, source_name: str) -> list:
    lines = markdown.splitlines()
    story: list = []
    paragraph_buffer: list[str] = []
    in_code = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_buffer:
            story.append(Paragraph(inline_markup(" ".join(paragraph_buffer)), styles["body"]))
            paragraph_buffer.clear()

    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        if line.startswith("```"):
            flush_paragraph()
            if in_code:
                story.append(XPreformatted(ascii_safe("\n".join(code_lines)), styles["code"] ))
                code_lines.clear()
                in_code = False
            else:
                in_code = True
            index += 1
            continue
        if in_code:
            code_lines.append(line)
            index += 1
            continue
        if not line.strip():
            flush_paragraph()
            index += 1
            continue
        if line.startswith("| "):
            flush_paragraph()
            table_lines: list[str] = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            rows: list[list[str]] = []
            for table_line in table_lines:
                cells = [cell.strip() for cell in table_line.strip("|").split("|")]
                if all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
                    continue
                rows.append(cells)
            if rows:
                story.append(table_from_rows(rows, available_width, styles))
                story.append(Spacer(1, 8))
            continue
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            style = styles["title"] if level == 1 else styles["h2"] if level == 2 else styles["h3"]
            story.append(Paragraph(inline_markup(heading.group(2)), style))
            index += 1
            continue
        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        numbered = re.match(r"^\s*(\d+)\.\s+(.*)$", line)
        if bullet or numbered:
            flush_paragraph()
            marker = "-" if bullet else f"{numbered.group(1)}."
            value = bullet.group(1) if bullet else numbered.group(2)
            story.append(Paragraph(f"{marker} {inline_markup(value)}", styles["bullet"]))
            index += 1
            continue
        paragraph_buffer.append(line.strip())
        index += 1

    flush_paragraph()
    if in_code and code_lines:
        story.append(XPreformatted(ascii_safe("\n".join(code_lines)), styles["code"]))
    story.append(
        Paragraph(
            inline_markup(
                f"Source: {source_name}. Public-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated."
            ),
            styles["source"],
        )
    )
    return story


def render_report(source: Path, destination: Path) -> dict:
    source_bytes = source.read_bytes()
    markdown = source_bytes.decode("utf-8")
    max_columns = 0
    for line in markdown.splitlines():
        if line.lstrip().startswith("|"):
            max_columns = max(max_columns, max(0, len(line.strip().strip("|").split("|"))))
    page_size = landscape(letter) if max_columns >= 8 else letter
    width, height = page_size
    margin = 0.55 * inch
    available_width = width - 2 * margin
    styles = styles_for_pdf()
    if source.name == "sponsor_report.md":
        # Keep the sponsor brief as a legible one-page handout.  The technical
        # reports retain the roomier default typography.
        styles["title"].fontSize = 18
        styles["title"].leading = 21
        styles["title"].spaceAfter = 9
        styles["h2"].fontSize = 12
        styles["h2"].leading = 14
        styles["h2"].spaceBefore = 7
        styles["h2"].spaceAfter = 4
        styles["body"].fontSize = 8.6
        styles["body"].leading = 10.8
        styles["body"].spaceAfter = 4
        styles["bullet"].fontSize = 8.5
        styles["bullet"].leading = 10.5
        styles["bullet"].spaceAfter = 2
        styles["source"].fontSize = 7.1
        styles["source"].leading = 8.5
        styles["source"].spaceBefore = 6
    title_match = re.search(r"^#\s+(.+)$", markdown, flags=re.MULTILINE)
    document_title = ascii_safe(title_match.group(1) if title_match else source.stem.replace("_", " ").title())

    destination.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(destination), pagesize=page_size,
        leftMargin=margin, rightMargin=margin, topMargin=0.62 * inch, bottomMargin=0.55 * inch,
        title=document_title, author="Aptatio Capstone", subject="Expanded HHVBP CY2025 public-only prototype",
    )

    def decorate(canvas, document) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D9E2F3"))
        canvas.setLineWidth(0.5)
        canvas.line(margin, height - 0.38 * inch, width - margin, height - 0.38 * inch)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(margin, height - 0.28 * inch, ascii_safe(document_title[:90]))
        canvas.drawRightString(width - margin, 0.3 * inch, f"Page {document.page}")
        canvas.restoreState()

    story = markdown_story(markdown, available_width, styles, source.name)
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return {
        "source": str(source.resolve()),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "pdf": str(destination.resolve()),
        "orientation": "landscape" if page_size == landscape(letter) else "portrait",
        "max_table_columns": max_columns,
        "pages": int(getattr(doc, "page", 0)),
        "bytes": destination.stat().st_size,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _artifact_hashes(source_manifest: dict) -> dict[str, str]:
    artifacts = source_manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("Source report manifest has no artifact list.")
    hashes: dict[str, str] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            raise ValueError("Source report manifest contains a malformed artifact record.")
        name = Path(artifact["path"]).name
        if name in hashes:
            raise ValueError(f"Source report manifest repeats artifact {name!r}.")
        digest = artifact.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Source report manifest has an invalid hash for {name!r}.")
        hashes[name] = digest
    return hashes


def _validate_report_records(
    source_manifest_path: Path,
    source_manifest: dict,
    records: list[dict],
    *,
    validate_pdf_files: bool,
) -> None:
    artifact_hashes = _artifact_hashes(source_manifest)
    seen_sources: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("PDF manifest contains a malformed report record.")
        source = Path(str(record.get("source", "")))
        if source.parent.resolve() != source_manifest_path.parent.resolve():
            raise ValueError(f"PDF source {source} is outside the bound report bundle.")
        if source.name in seen_sources:
            raise ValueError(f"PDF manifest repeats source {source.name!r}.")
        seen_sources.add(source.name)
        if not source.is_file():
            raise ValueError(f"PDF source {source} no longer exists.")
        current_source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if record.get("source_sha256") != current_source_hash:
            raise ValueError(f"PDF source {source.name!r} changed after rendering.")
        if artifact_hashes.get(source.name) != current_source_hash:
            raise ValueError(f"PDF source {source.name!r} does not match the source report manifest.")

        if validate_pdf_files:
            pdf_path = Path(str(record.get("pdf", "")))
            if not pdf_path.is_file():
                raise ValueError(f"Rendered PDF {pdf_path} no longer exists.")
            pdf_bytes = pdf_path.read_bytes()
            if record.get("bytes") != len(pdf_bytes):
                raise ValueError(f"Rendered PDF {pdf_path.name!r} has a stale byte count.")
            if record.get("sha256") != hashlib.sha256(pdf_bytes).hexdigest():
                raise ValueError(f"Rendered PDF {pdf_path.name!r} has a stale checksum.")


def build_pdf_manifest(
    source_manifest_path: Path,
    records: list[dict],
    *,
    created_at_utc: str,
) -> dict:
    """Build a PDF manifest bound to the current source bundle and source files."""

    if not source_manifest_path.is_file():
        raise FileNotFoundError(f"Source report manifest not found: {source_manifest_path}")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    _validate_report_records(
        source_manifest_path,
        source_manifest,
        records,
        validate_pdf_files=True,
    )
    manifest = {
        "artifact_type": PDF_MANIFEST_ARTIFACT_TYPE,
        "schema_version": PDF_MANIFEST_SCHEMA_VERSION,
        "created_at_utc": created_at_utc,
        "renderer": "reportlab_platypus",
        "source_report_manifest": {
            "path": str(source_manifest_path.resolve()),
            "artifact_type": source_manifest.get("artifact_type"),
            "schema_version": source_manifest.get("schema_version"),
            "generated_at_utc": source_manifest.get("generated_at_utc"),
            "manifest_file_sha256_at_render_start": hashlib.sha256(
                source_manifest_path.read_bytes()
            ).hexdigest(),
            "bundle_identity_sha256": source_bundle_identity_sha256(source_manifest),
        },
        "reports": records,
        "public_data_only": True,
        "production_validated": False,
    }
    manifest["manifest_identity_sha256"] = pdf_manifest_identity_sha256(manifest)
    return manifest


def _invalidate_source_bundle_pdf_link(source_manifest_path: Path, reason: str) -> None:
    """Ensure an interrupted or rejected PDF generation cannot remain current."""

    if not source_manifest_path.is_file():
        return
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    for key in SOURCE_MANIFEST_PDF_LINK_FIELDS:
        source_manifest.pop(key, None)
    source_manifest.update(
        {
            "pdf_or_latex_generated": False,
            "pdf_generated": False,
            "latex_generated": False,
            "pdf_link_status": "not_current",
            "pdf_link_error": reason,
        }
    )
    _write_json(source_manifest_path, source_manifest)


def link_source_bundle_manifest(
    source_manifest_path: Path,
    pdf_manifest_path: Path,
    *,
    created_at_utc: str,
    report_count: int,
) -> None:
    """Link PDFs only when manifests, source Markdown, and PDF bytes are current."""

    if not source_manifest_path.is_file():
        raise FileNotFoundError(f"Source report manifest not found: {source_manifest_path}")
    try:
        if not pdf_manifest_path.is_file():
            raise FileNotFoundError(f"PDF manifest not found: {pdf_manifest_path}")
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        pdf_manifest = json.loads(pdf_manifest_path.read_text(encoding="utf-8"))
        if pdf_manifest.get("artifact_type") != PDF_MANIFEST_ARTIFACT_TYPE:
            raise ValueError("PDF manifest artifact type is invalid.")
        if pdf_manifest.get("schema_version") != PDF_MANIFEST_SCHEMA_VERSION:
            raise ValueError("PDF manifest schema version is invalid.")
        if pdf_manifest.get("created_at_utc") != created_at_utc:
            raise ValueError("PDF manifest timestamp does not match the requested linkage.")
        records = pdf_manifest.get("reports")
        if not isinstance(records, list) or len(records) != int(report_count):
            raise ValueError("PDF manifest report count does not match the requested linkage.")

        manifest_identity = pdf_manifest_identity_sha256(pdf_manifest)
        if pdf_manifest.get("manifest_identity_sha256") != manifest_identity:
            raise ValueError("PDF manifest identity hash is stale or invalid.")
        binding = pdf_manifest.get("source_report_manifest")
        if not isinstance(binding, dict):
            raise ValueError("PDF manifest has no source-bundle binding.")
        if Path(str(binding.get("path", ""))).resolve() != source_manifest_path.resolve():
            raise ValueError("PDF manifest is bound to a different source report manifest.")
        source_manifest_file_hash = hashlib.sha256(source_manifest_path.read_bytes()).hexdigest()
        if binding.get("manifest_file_sha256_at_render_start") != source_manifest_file_hash:
            raise ValueError("Source report manifest changed after PDF rendering.")
        source_identity = source_bundle_identity_sha256(source_manifest)
        if binding.get("bundle_identity_sha256") != source_identity:
            raise ValueError("PDF manifest is bound to a stale source-bundle identity.")
        _validate_report_records(
            source_manifest_path,
            source_manifest,
            records,
            validate_pdf_files=True,
        )

        source_manifest.update(
            {
                "pdf_or_latex_generated": True,
                "pdf_generated": True,
                "latex_generated": False,
                "pdf_generated_at_utc": created_at_utc,
                "pdf_report_count": int(report_count),
                "pdf_manifest_path": str(pdf_manifest_path.resolve()),
                "pdf_manifest_file_sha256": hashlib.sha256(pdf_manifest_path.read_bytes()).hexdigest(),
                "pdf_manifest_identity_sha256": manifest_identity,
                "pdf_source_bundle_identity_sha256": source_identity,
                "pdf_source_manifest_sha256_before_link": source_manifest_file_hash,
                "pdf_link_status": "current",
            }
        )
        source_manifest.pop("pdf_link_error", None)
        _write_json(source_manifest_path, source_manifest)
    except Exception as exc:
        _invalidate_source_bundle_pdf_link(source_manifest_path, str(exc))
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--include-readme", action="store_true")
    args = parser.parse_args()
    sources = sorted(args.input_dir.glob("*.md"))
    if not args.include_readme:
        sources = [path for path in sources if path.name.lower() != "readme.md"]
    if not sources:
        raise FileNotFoundError(f"No Markdown reports found under {args.input_dir}")
    source_manifest_path = args.input_dir / "report_manifest.json"
    if not source_manifest_path.is_file():
        raise FileNotFoundError(f"Source report manifest not found: {source_manifest_path}")
    # Clear any prior link before replacing PDF bytes. If rendering is
    # interrupted, the source bundle remains explicitly not current.
    _invalidate_source_bundle_pdf_link(source_manifest_path, "pdf_regeneration_in_progress")
    records = [render_report(source, args.output_dir / f"{source.stem}.pdf") for source in sources]
    created_at_utc = datetime.now(timezone.utc).isoformat()
    manifest = build_pdf_manifest(
        source_manifest_path,
        records,
        created_at_utc=created_at_utc,
    )
    manifest_path = args.output_dir / "pdf_manifest.json"
    _write_json(manifest_path, manifest)

    # The Markdown/CSV/JSON generator has its own bundle manifest.  Once PDFs
    # exist, link the two records so the source bundle does not remain marked
    # as "PDF not generated".
    link_source_bundle_manifest(
        source_manifest_path,
        manifest_path,
        created_at_utc=created_at_utc,
        report_count=len(records),
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
