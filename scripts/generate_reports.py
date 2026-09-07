#!/usr/bin/env python3
"""Generate or claim-scan the current Aptatio Markdown/CSV/JSON reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hhvbp_reporting import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    assert_no_unsupported_claims,
    write_report_bundle,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the current CY2025 Reno, age-sensitivity, nontechnical, "
            "current-model, and sponsor Markdown/CSV/JSON report bundle."
        )
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument(
        "--reference-path",
        type=Path,
        default=PROJECT_ROOT / "data" / "reference" / "hhvbp_risk_reference.json",
    )
    parser.add_argument(
        "--generated-at",
        help="Optional ISO timestamp override; otherwise the deterministic dataset-config timestamp is used.",
    )
    parser.add_argument(
        "--check-claims-only",
        action="store_true",
        help="Scan existing Markdown/CSV/JSON files in --output-dir without regenerating them.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check_claims_only:
        paths = sorted(
            path
            for path in args.output_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".md", ".csv", ".json"}
        )
        if not paths:
            raise SystemExit(f"No report text artifacts found under {args.output_dir}")
        assert_no_unsupported_claims(paths)
        print(json.dumps({"claim_scan": "passed", "files_scanned": len(paths)}, sort_keys=True))
        return 0

    manifest = write_report_bundle(
        output_dir=args.output_dir,
        config_dir=args.config_dir,
        reference_path=args.reference_path,
        generated_at=args.generated_at,
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "artifact_count": len(manifest["artifacts"]) + 1,
                "unsupported_claim_scan": manifest["unsupported_claim_scan"]["status"],
                "pdf_or_latex_generated": manifest["pdf_or_latex_generated"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
