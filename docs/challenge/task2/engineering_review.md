# Task 2: Engineering Review

What judges look for in your extraction code. Tier 2 doesn't affect the leaderboard, but it's how finalists get picked.

## Code Quality (25% of Tier 2)

| Dimension | What judges look for |
|---|---|
| Structure (25%) | Image processing separated from LLM interaction. Schema parsing in its own module. Normalization rules factored out, not inline. |
| Type safety (20%) | Dynamic output shaped by `json_schema`. Schema validation on output. Optional fields typed as `T | None`. |
| Error handling (15%) | Malformed LLM output handled. Fallback logic for unreadable fields. Per-field failure instead of whole-extraction failure. |
| Testing (25%) | Tests with sample documents. Normalization rules unit-tested (currency stripping, number parsing). Edge cases tested (empty image, missing fields, tables). |
| Readability (15%) | Extraction prompt clearly structured with target schema. Normalization rules documented. |

## Architecture Design (30% of Tier 2)

| Dimension | What judges look for |
|---|---|
| AI pipeline (30%) | Vision model integration. Clear decision on single-pass vs. multi-pass extraction. Post-processing pipeline (LLM output → normalization → validation → response). |
| Decomposition (25%) | Image → OCR/Vision → Extractor → Normalizer → Validator layering. Each stage has a clear responsibility and interface. |
| API design (20%) | `POST /extract` handles the full document contract. Proper error responses for invalid images. Consistent output schema. |
| Trade-off reasoning (15%) | Awareness of accuracy-vs-cost trade-off in model selection. Vision model choice justified for document complexity. |
| Scalability (10%) | Can process documents concurrently. Token budget management for large images. |

## Engineering Maturity (20% of Tier 2)

| Dimension | What judges look for |
|---|---|
| Deployment (30%) | Same as all tasks: Dockerfile, env config, README. |
| Config and secrets (25%) | Model endpoint and API keys in env vars. Token limits configurable. |
| Observability (20%) | Structured logging with `document_id`. Extraction success/failure rates logged. |
| Security (25%) | Document content treated as untrusted input. Input size limits enforced. |

## Tips

- Documents are images, so you need a vision-capable model.
- Each document has a different schema. Your prompt should include the `json_schema` so the model knows what to extract.
- Tables are the hardest part. Financial statements and medical forms have complex tabular data.
- The scorer is schema-agnostic; it compares your output field-by-field against gold.
