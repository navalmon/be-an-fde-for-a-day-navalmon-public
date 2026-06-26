# Task 2: Field Guide

This is a document-to-record task. Shape the output for machines, not humans.

Read the `json_schema` from the request and extract every field it specifies. Tables are common in financial and medical documents. About 36% of the eval set is handwritten or low-quality scans. Watch the types: numbers should be parsed as numbers, not strings.

Return `null` for fields you can't extract; do not hallucinate.
