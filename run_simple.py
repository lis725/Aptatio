from __future__ import annotations

"""Simple local runner for manual testing.

Usage:
    python3 run_simple.py

This reads the bundled example request JSON, calls the backend interface,
and prints the final minimal assignment output to the terminal. By default,
it returns integer clinician ids such as 101, 201, and 301.
"""

import argparse
import json
from pathlib import Path

from backend_interface import assign_from_request

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_REQUEST_PATH = PROJECT_ROOT / "examples" / "example_assignment_request.json"


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the simplified HHVBP assignment output.")
    parser.add_argument(
        "--request",
        default=str(DEFAULT_REQUEST_PATH),
        help="Path to the request JSON file. Defaults to the bundled example request.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to save the simplified output JSON.",
    )
    parser.add_argument(
        "--value-key",
        choices=["clinician_name", "clinician_id"],
        default="clinician_name",
        help="Return clinician ids or clinician names.",
    )
    args = parser.parse_args()

    request = load_json(args.request)
    response = assign_from_request(request=request, value_key=args.value_key)

    rendered = json.dumps(response, indent=2, ensure_ascii=False)
    print(rendered)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"Saved to: {output_path}")


if __name__ == "__main__":
    main()
