# Backend Interface Contract

## Protected file

The repository contains `backend_interface.py` at its root. The brief's `backend/backend_interface.py` path does not exist. The actual integration file was treated as protected throughout.

- Baseline SHA-256: `4BE0C46769FC637C3F77D4008A6CB3624B17B7F628E59992F370BF7E6B2D84BB`
- Final expected SHA-256: `4BE0C46769FC637C3F77D4008A6CB3624B17B7F628E59992F370BF7E6B2D84BB`
- Size: 2,895 bytes

The checksum is enforced by `tests/test_backend_interface_contract.py`.

## Entry points

```python
create_request_payload(
    patient: dict,
    clinicians: list[dict],
    request_id: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict

assign_from_request(
    request: dict,
    value_key: str = "clinician_id",
) -> dict

assign_from_components(
    patient: dict,
    clinicians: list[dict],
    request_id: str | None = None,
    options: dict[str, Any] | None = None,
    value_key: str = "clinician_name",
) -> dict
```

Defaults, request construction truthiness, import paths, and uncaught `ValueError` behavior are unchanged. The simplified response remains exactly the three-list schema:

```json
{
  "RN": [...],
  "PT": [...],
  "OT": [...]
}
```

## Recorded example behavior

Pre-change ID output for `examples/example_assignment_request.json`:

```json
{"RN":[105,108,102],"PT":[204,203,207],"OT":[302,303,301]}
```

Post-fix ID output:

```json
{"RN":[108,104,105],"PT":[203,205,204],"OT":[302,303,301]}
```

This value change is intentional and internal: RN/PT capability normalization is now discipline-specific, mirrored anchoring uses the persisted risk percentile, and the hard-ZIP percentile policy is the default. The response schema and backend API did not change.

## Rich audit pathway

`assign_clinicians()` and the internal alias `assign_with_audit()` expose raw risk, risk percentile, raw-equivalent threshold, discipline routes/pool sizes/fallbacks, selected/full-pool ranks, imputation sources, and provenance. `backend_interface.py` continues to call `assign_clinicians_simple()` and does not expose this richer object by default.

No interface-change proposal was required.
