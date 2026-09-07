from __future__ import annotations

import hashlib
import inspect
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import backend_interface  # noqa: E402


PROTECTED_SHA256 = "4BE0C46769FC637C3F77D4008A6CB3624B17B7F628E59992F370BF7E6B2D84BB"


class BackendInterfaceContractTests(unittest.TestCase):
    def test_protected_file_is_byte_for_byte_unchanged(self) -> None:
        digest = hashlib.sha256((PROJECT_ROOT / "backend_interface.py").read_bytes()).hexdigest().upper()
        self.assertEqual(digest, PROTECTED_SHA256)

    def test_public_signatures_and_defaults_are_unchanged(self) -> None:
        create = inspect.signature(backend_interface.create_request_payload)
        request = inspect.signature(backend_interface.assign_from_request)
        components = inspect.signature(backend_interface.assign_from_components)

        self.assertEqual(list(create.parameters), ["patient", "clinicians", "request_id", "options"])
        self.assertIsNone(create.parameters["request_id"].default)
        self.assertIsNone(create.parameters["options"].default)
        self.assertEqual(list(request.parameters), ["request", "value_key"])
        self.assertEqual(request.parameters["value_key"].default, "clinician_id")
        self.assertEqual(
            list(components.parameters),
            ["patient", "clinicians", "request_id", "options", "value_key"],
        )
        self.assertIsNone(components.parameters["request_id"].default)
        self.assertIsNone(components.parameters["options"].default)
        self.assertEqual(components.parameters["value_key"].default, "clinician_name")

    def test_request_builder_truthiness_contract_is_unchanged(self) -> None:
        patient: dict = {}
        clinicians: list[dict] = []
        payload = backend_interface.create_request_payload(patient, clinicians, request_id="", options={})
        self.assertEqual(payload, {"patient": patient, "clinicians": clinicians, "request_id": ""})
        self.assertIs(payload["patient"], patient)
        self.assertIs(payload["clinicians"], clinicians)

    def test_default_response_schema_remains_simplified_rn_pt_ot(self) -> None:
        request = json.loads(
            (PROJECT_ROOT / "examples" / "example_assignment_request.json").read_text(encoding="utf-8")
        )
        result = backend_interface.assign_from_request(request)
        self.assertEqual(list(result), ["RN", "PT", "OT"])
        for discipline in ["RN", "PT", "OT"]:
            self.assertIsInstance(result[discipline], list)
            self.assertEqual(len(result[discipline]), 3)
        self.assertTrue(all(isinstance(value, int) for values in result.values() for value in values))

        names = backend_interface.assign_from_components(request["patient"], request["clinicians"])
        self.assertEqual(list(names), ["RN", "PT", "OT"])
        self.assertTrue(all(isinstance(value, str) for values in names.values() for value in values))

    def test_validation_exception_still_propagates_as_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Top-level request must be a JSON object"):
            backend_interface.assign_from_request(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
