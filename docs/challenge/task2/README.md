# Task 2: Document Extraction

`POST /extract`

Given a document image (receipt, invoice, form, financial statement, etc.) and a JSON schema describing the expected output structure, extract all relevant data into structured JSON. The scoring cares about getting the right values with the right structure, not about how the output reads.

Read the background:

- [customer_brief.md](customer_brief.md): what the customer needs
- [field_guide.md](field_guide.md): practical extraction tips
- [engineering_review.md](engineering_review.md): what judges look for

## Request contract

Your `/extract` endpoint receives a single uniform payload format:

- `document_id`
- `content_format`: always `"image_base64"`
- `content`: base64-encoded PNG bytes of the document image
- `json_schema`: JSON schema describing the expected output structure (each document has a different schema)

Your code only needs to base64-decode `content` and pass the bytes to a vision-capable model.

The shipped public set on disk uses `content_format: "image_path"` so the JSON file stays small (~80 KB). The local eval harness (`py/apps/eval/run_eval.py`) reads each PNG from `py/data/task2/images/` and inlines it as base64 before POSTing to your endpoint, so your handler never sees the `image_path` form. Production scoring does the same: the platform downloads each image from blob storage, applies per-submission anti-reconstruction perturbation (cohort-2 WS1.1), then base64-inlines before POSTing. Either way, your endpoint receives `image_base64`.

If you want to inspect a record manually, the disk layout is:

```python
from pathlib import Path
import json, base64

task2_dir = Path("py/data/task2")
records = json.loads((task2_dir / "public_eval_50.json").read_text())
image_bytes = (task2_dir / records[0]["content"]).read_bytes()
b64 = base64.b64encode(image_bytes).decode()  # <- this is what your /extract receives
```

See [../../../py/data/task2/input_schema.json](../../../py/data/task2/input_schema.json) for the formal schema.

## Response contract

Required output fields:

- `document_id`: must match the input
- All fields specified in the `json_schema` from the request

The output schema varies per document. One document might ask for `firstName`, `address`, `phone`. Another might ask for `tableData`, `institution`, `portfolioSummary`. Your endpoint must read the `json_schema` and return matching structured JSON.

See [../../../py/data/task2/output_schema.json](../../../py/data/task2/output_schema.json) for the formal schema.

## Resolution scoring

```
resolution = (0.70 x information_accuracy + 0.30 x text_fidelity) x 100
```

| Dimension | Weight | Metric |
|---|---|---|
| `information_accuracy` | 70% | Recursive field F1 with value normalization. Did you extract the correct data? |
| `text_fidelity` | 30% | Recursive field exact-match. Did you preserve exact formatting? |

**Information Accuracy** uses a format-stripping normalizer: `$1,234.56` becomes `1234.56`, `10%` becomes `10`. If you extract the right value in a different format, you still get credit.

**Text Fidelity** uses a light normalizer (lowercase, collapse whitespace). If you also match the exact formatting, you get the full score.

**Per-field scoring by type:**
- Strings: token F1 (information) / exact match (fidelity)
- Numbers: 1% relative tolerance
- Booleans: exact match
- Lists: set F1 with fuzzy element alignment (information) / strict set F1 (fidelity)
- Nested objects: recursive field-mean

## What's hard

Every document has a different schema, so you can't hardcode field names. Receipts, invoices, medical forms, financial statements, charts. Some have tables, some have nested sections. ~36% of documents are adversarial: photographed, scanned, handwritten, degraded image quality.

## Tips

- Read the `json_schema` from the request. It tells you exactly what fields to extract.
- Use a vision model (the input is an image, not text).
- Return `null` for fields you can't extract. Don't hallucinate.
- Tables are common. Financial data, medical forms, and invoices all have tabular content.
- **Vision calls hit AOAI 429s under sustained load.** A 500-item hidden eval batch is more than enough to trip throttling on a fresh deployment. Wrap your AOAI client in a retry loop that honors `Retry-After` (the OpenAI SDK does *not* do this by default), and keep your per-attempt timeout short enough that two retries fit inside the platform's 60 s deadline. A 429 that the platform can't recover from counts as `documents_errored` *and* contributes zero to every dimension. See [../../eval/fdebench.md, Platform behaviour to know about](../../eval/fdebench.md#platform-behaviour-to-know-about) for the full retry contract.
