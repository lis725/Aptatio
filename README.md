# Aptatio: CS quick start

Inputs are unchanged: keep the same patient and clinician data from your database.
Use Python 3.12 and install dependencies with `pip install -r requirements.txt`.

## Call the new interface

Change the import from `backend_interface` to `reporting_interface`, keeping your
existing arguments (including `request_id`, `options`, and `value_key` if used):

```python
from reporting_interface import assign_from_components, write_response_json

response = assign_from_components(patient, clinicians, request_id=request_id, options=options)
write_response_json(response, "private-output/response.json")  # Optional file export
```

If you already assemble a full request dict, use
`from reporting_interface import assign_from_request` and call `assign_from_request(request)`.
The old `backend_interface` still returns the original assignment-only dict.

## What the output contains

The new call returns one dict, ready for your backend to serialize as JSON:

| Field | Contents |
| --- | --- |
| `assignments` | The original RN/PT/OT lists. Use this for your existing list display. |
| `agency_report` | Report text and option details in `content`; chart data and supporting information in `evidence`. |
| `clinician_email` | `items` is empty until a selection; then it contains only that option's clinician drafts (`subject`, `plain_text_body`, clinician ID). |

The response also includes `schema_version`, `decision_id`, and `request_id`.
Save the **complete response server-side**, so later PDF generation and selection use the same decision.

## Generate PDFs from the output JSON

```python
import json
from pathlib import Path
from reporting_interface import write_agency_pdf, select_option, write_clinician_pdfs, write_response_json

response = json.loads(Path("private-output/response.json").read_text(encoding="utf-8"))
write_agency_pdf(response, "private-output/agency_report.pdf")

# After the user selects a returned option (ranks start at 1):
response = select_option(response, 2)
write_response_json(response, "private-output/response.json")
write_clinician_pdfs(response, "private-output/clinicians")
# Email drafts: response["clinician_email"]["items"]
```

These calls reuse the saved result without rerunning assignment and do not send emails.
To generate an alternative-only agency PDF, pass `option_rank=2` to `write_agency_pdf`.
Wire the JSON fields and generated PDFs into your existing webpage/display/download endpoints.
Only share the selected clinician's draft/PDF with that clinician, not the full agency response.
