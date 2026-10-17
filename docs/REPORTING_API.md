# CS integration: one response JSON

## What changes for the database inputs?

**No required input changes.** Continue querying the same patient object and
clinician list from the database. Physical input files are not required by the
Python API. The existing five assignment example JSON files, configuration files,
reference data and `backend_interface.py` are unchanged.

| Existing input | Use |
| --- | --- |
| `patient` | The existing patient object, including age/age group, ZIP and local externality factors. |
| `clinicians` | The existing list of clinician IDs/names, disciplines, metrics, metric scales, count proxies and available ZIP history. |
| `request_id` | Optional existing request identifier; do not generate a new one merely to render a report. It can affect deterministic OT rotation. |
| `options` | Existing allocation controls, unchanged. |
| Existing top-level ZIP-history input | Still accepted by the full-request API; retain the existing names and structure. |

Keep the existing engine's supported field names/aliases. The report does not
require a new patient assessment, a separate clinician profile file, a PDF
template input, or new database columns. See
[the existing input dictionary](data_dictionary.md) and
[the synthetic reporting example](../examples/reporting_synthetic_request.json).
The synthetic example is optional demo data, not a replacement input contract.

The allocation policy is the published GitHub policy: all disciplines retain
their threshold routing; OT rotates deterministically within its routed pool.
The separate local experiment that always prioritizes OT ZIP history is not
included in this reporting release. Reporting only adds opt-in evidence capture
after allocation; the old API does not enable it.

## Switch the backend call

The old interface still returns only `{"RN": [...], "PT": [...], "OT": [...]}`.
To obtain all content, change the import and read `response["assignments"]` for
the existing list widget:

```python
from reporting_interface import assign_from_components, write_response_json

# These are the same database-derived objects used before.
response = assign_from_components(patient, clinicians, request_id=request_id, options=options)
assignments = response["assignments"]  # Same lists as the old call.
write_response_json(response, "private-output/response.json")
# Or return response directly through your framework's JSON response helper.
```

`assign_from_components` retains the old default `value_key="clinician_name"`.
`assign_from_request(request)` retains `value_key="clinician_id"`. Pass that
argument explicitly if the frontend expects IDs. Both functions accept the same
arguments as their old counterparts, plus optional keyword-only
`selected_option_rank`. Do not call both old and new APIs for one decision.

The JSON writer is explicit: ordinary API calls do not write files. It writes
UTF-8 atomically and rejects NaN/Infinity. The full response is already a Python
dict; the framework serializes it once as a JSON object, not a JSON string inside
another JSON response.

## Response contract: `aptatio_reporting.v1`

There are three content sections plus version/decision metadata:

```json
{
  "schema_version": "aptatio_reporting.v1",
  "decision_id": "opaque UUID",
  "request_id": "the original request ID or null",
  "assignments": {"RN": [101, 102, 103], "PT": [201, 202, 203], "OT": [301, 301, 301]},
  "agency_report": {
    "content": {"shared": {}, "options": [], "recipient_context": {}},
    "evidence": {}
  },
  "clinician_email": {
    "status": "not_selected",
    "selected_option_rank": null,
    "items": [],
    "delivery_performed": false
  }
}
```

This is an abbreviated structural illustration, not a complete runnable response.
Generate a real complete response using the commands below. Do not strip fields
from the stored response: PDF rendering and later selection require the bound
evidence. Do not treat `agency_report` as an HTML string.

| Frontend use | JSON path |
| --- | --- |
| Existing RN/PT/OT lists | `assignments` |
| Shared report narrative | `agency_report.content.shared` |
| Patient-factor explanations | `agency_report.content.shared.factor_interpretations` |
| Each option's clinician reasons and scorecards | `agency_report.content.options[].team` |
| Graph coordinates and markers | `agency_report.evidence.patient_distribution.chart_data` |
| Patient profile display | `agency_report.evidence.patient_profile_summary` |
| Source/route/metric evidence and availability flags | `agency_report.evidence` and each content scorecard |
| Selected clinician email | `clinician_email.items[].subject` and `.plain_text_body` |

`option_rank` is **1-based**, the returned option rank, not a clinician ID or a
zero-based array index. Each selected email item also has `discipline`,
`clinician_id`, `clinician_name`, `decision_id`, `selected_option_rank`, an opaque
`request_reference`, `status`, `preview`, and the curated PDF paragraphs.
Null clinician slots produce no email. A repeated clinician produces one draft
for the selected option. Zero returned options produce empty assignment lists
and no emails. Validate `schema_version` when loading saved data.

## Select later, without changing inputs or allocating again

Store the **complete response server-side**, keyed by its `decision_id`. When the
user selects a returned option, load that saved response and call:

```python
import json
from pathlib import Path
from reporting_interface import select_option, write_response_json

response = json.loads(Path("private-output/response.json").read_text(encoding="utf-8"))
response = select_option(response, 2)
write_response_json(response, "private-output/response.json")
```

Before selection, `clinician_email.items` is empty. After selection its status is
`drafts_ready`, with drafts only for that option's nonempty clinician slots.
Selecting the same option again reuses draft references/content. Selecting a
different option creates fresh drafts; `select_option(response, None)` clears
them. The original object is not mutated. No patient or clinician input needs to
be fetched again for this operation.

Only accept a rank and decision identifier from the browser, then load the
server-held response. The binding checks detect accidentally mixed decisions;
they are not signatures or authorization for arbitrary client-edited JSON.
Access control, storage, download URLs and any acceptance workflow belong to the
CS application. This package does not send messages or mark a request accepted.

## PDF generation from the same JSON

```python
from reporting_interface import write_agency_pdf, write_clinician_pdfs

write_agency_pdf(response, "private-output/agency_report.pdf")
write_agency_pdf(response, "private-output/alternative_1.pdf", option_rank=2)
write_clinician_pdfs(response, "private-output/clinicians")
```

The agency PDF includes the graph, shared patient context and all option reasons.
An alternative PDF contains only that option's reasons; option 1 includes shared
context. Clinician PDFs use the same curated content as the selected emails and
opaque filenames. No PDFs are generated unless these functions or `--pdf-dir`
are requested. An unavailable reference is labeled honestly rather than replaced
with a made-up curve. A PDF error leaves the already saved JSON available.

The fixed template is Python layout code in
[`hhvbp_distribution_pdf.py`](../src/hhvbp_distribution_pdf.py); wording is built
in [`hhvbp_report_content.py`](../src/hhvbp_report_content.py). It consumes the
computed JSON sections, lays out paragraphs/tables/vector charts and builds the
PDF. Recipient wording/layout is in
[`hhvbp_clinician_brief.py`](../src/hhvbp_clinician_brief.py).

## Optional evidence: no new required input

Existing clinician metrics are sufficient to generate reports and draft emails.
If compatible branch/national/state/organization benchmarks are absent, their
comparison is unavailable; this does not mean a clinician has a zero/poor score.
Original personal values can still display when their unit is known. Unknown
units and imputed scoring values are not promoted to measured percentages.

To add verified comparisons later, the full-request API optionally accepts
`scorecard_evidence`. Supply uniquely bound personal records (matching the
original request's clinician ID, discipline and original metric values) as well
as benchmark records. This is separate from algorithm configuration constants:

```python
request["scorecard_evidence"] = {
    "source_kind": "database_scorecards",
    "clinicians": [{
        "clinician_id": 101, "discipline": "RN",
        "metrics": {"PPH": {
            "value": 0.17, "unit": "proportion",
            "definition": "same metric definition", "reporting_period": "same period",
            "source": {"record_id": "your-personal-record-id"}
        }}
    }],
    "benchmarks": [{
        "scope": "national", "metric_key": "PPH", "value": 0.30, "unit": "proportion",
        "definition": "same metric definition", "reporting_period": "same period",
        "source": {"record_id": "your-benchmark-record-id"}
    }]
}
```

These numbers are illustrative. Use actual database records. Include matching
`branch_id`/`state_id` metadata for branch/state comparisons; missing metadata
remains unverified. Conflicting periods, definitions, scopes, units or ambiguous
records suppress comparative claims. Caller-provided provenance is retained but
not independently verified.
No archived CSVs, local ZIP files, private source workbooks or internet lookups
are required. Optional `clinician_coverage_preferences` can document explicit
preferences for recipient wording; recorded prior ZIP service alone is never
treated as a preference or current availability.

The full response is for the agency application. It includes patient context and
evidence. Do not forward it to a clinician. Use only that clinician's selected
email item or PDF. The recipient content builder excludes patient identity,
location, the full patient profile and other clinicians' information. Synthetic
or debug inputs remain labeled as previews.

## CLI and verification

Runtime dependencies remain the existing `requirements.txt` (Python 3.12).
For all PDF text tests, also install the optional test dependency `pypdf`.

```sh
python -B run_reporting.py --request examples/reporting_synthetic_request.json --output private-output/response.json
python -B run_reporting.py --response private-output/response.json --selected-option 2 --output private-output/selected.json --pdf-dir private-output/pdf
python -B -m unittest discover -s tests -p 'test*.py' -v
```

The second command loads the first decision; it does not rerun assignment.
Generated JSON/PDFs and private inputs are runtime artifacts, not repository
source files. The release deliberately excludes local review ZIPs, generated
sample outputs, forecast experiments, duplicate adapters and retired renderers.
