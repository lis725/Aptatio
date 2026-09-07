from __future__ import annotations

"""Thin backend-facing interface for the HHVBP assignment engine.

Typical production flow:
1. Query your database for one patient record.
2. Query your database for the clinician pool.
3. Call ``assign_from_components(patient, clinicians)``.
4. Return the result directly to the frontend.

The returned payload is intentionally minimal. By default it returns
integer clinician ids using the convention RN=1xx, PT=2xx, OT=3xx:
    {
      "RN": [101, 102, 103],
      "PT": [201, 202, 203],
      "OT": [301, 302, 303],
    }
"""

from pathlib import Path
from typing import Any
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
CONFIG_DIR = PROJECT_ROOT / "config"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hhvbp_assignment_service import assign_clinicians_simple  # noqa: E402


def create_request_payload(
    patient: dict,
    clinicians: list[dict],
    request_id: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict:
    """Create the request payload expected by the assignment engine.

    ``patient`` should already be a patient object acceptable to the engine,
    typically something like:
        {
          "patient_id": "P123",
          "local_externalities": {...}
        }

    ``clinicians`` should be the list pulled from the backend database.
    """
    request: dict[str, Any] = {
        "patient": patient,
        "clinicians": clinicians,
    }
    if request_id is not None:
        request["request_id"] = request_id
    if options:
        request["options"] = options
    return request


def assign_from_request(request: dict, value_key: str = "clinician_id") -> dict:
    """Run the assignment engine on a fully prepared request payload.

    Use this when the backend has already assembled the full request object.
    ``value_key`` can be:
      - ``clinician_id``   -> returns stable integer ids, e.g. 101 / 201 / 301
      - ``clinician_name`` -> returns labels like RN7 / PT4 / OT2
    """
    return assign_clinicians_simple(
        request=request,
        config_dir=CONFIG_DIR,
        value_key=value_key,
    )


def assign_from_components(
    patient: dict,
    clinicians: list[dict],
    request_id: str | None = None,
    options: dict[str, Any] | None = None,
    value_key: str = "clinician_name",
) -> dict:
    """Backend template entry point.

    This is the function the backend should usually call after fetching:
      - one patient dict
      - one clinician list

    It builds the request payload internally, runs the engine, and returns a
    frontend-ready result.
    """
    request = create_request_payload(
        patient=patient,
        clinicians=clinicians,
        request_id=request_id,
        options=options,
    )
    return assign_from_request(request=request, value_key=value_key)
