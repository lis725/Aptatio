"""Explicit file export for the reporting API; never sends clinician messages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import reporting_interface as reporting


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--request', type=Path, help='Existing assignment input JSON')
    source.add_argument('--response', type=Path, help='Previously saved reporting JSON (no reallocation)')
    parser.add_argument('--output', type=Path, required=True, help='Complete output JSON destination')
    parser.add_argument('--value-key', choices=['clinician_id', 'clinician_name'], default='clinician_id')
    parser.add_argument('--selected-option', type=int, help='1-based returned option rank; omission leaves email empty for new requests')
    parser.add_argument('--pdf-dir', type=Path, help='Optionally render agency and selected clinician PDFs')
    args = parser.parse_args()
    source_path = args.request or args.response
    value = json.loads(source_path.read_text(encoding='utf-8'))
    response = reporting.assign_from_request(value, value_key=args.value_key) if args.request else value
    if args.selected_option is not None:
        response = reporting.select_option(response, args.selected_option)
    # Save computed JSON first so a PDF error never loses the assignment result.
    path = reporting.write_response_json(response, args.output)
    print(f'Response JSON: {path}')
    if args.pdf_dir:
        print(f'Agency PDF: {reporting.write_agency_pdf(response, args.pdf_dir / "agency_report.pdf")}')
        for path in reporting.write_clinician_pdfs(response, args.pdf_dir / 'clinicians'):
            print(f'Clinician PDF: {path}')


if __name__ == '__main__':
    main()
