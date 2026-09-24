# Architecture and data boundaries

## Runtime topology

| Layer | Responsibility | Production option |
|---|---|---|
| Web | Browse products, source components, inspect brand supply chains and compare products | Astro static output served by the application or a web server |
| API | Health, media proxy and runtime operations | FastAPI |
| Scheduler | Incremental source discovery, normalization and site refresh | Dedicated process/container |
| Video worker | Creator-subtitle/OCR extraction for products without a teardown report | Dedicated process/container; no ASR |
| Structured storage | Products, reports, BOM rows, suppliers, evidence and task state | PostgreSQL |
| Object storage | Report images and extracted key frames | MinIO/S3-compatible storage |

## Data lifecycle

1. Discover a public teardown report or video.
2. Resolve product identity and check whether a report-backed product already exists.
3. Prefer report evidence. Process a video only when no matching teardown report exists.
4. Extract source-grounded facts using deterministic rules; optionally use a text model for unresolved semantic fields.
5. Normalize product categories, brands, supplier aliases and component records while retaining source evidence.
6. Persist normalized entities in PostgreSQL and binary assets in MinIO.
7. Validate coverage and references, then generate the searchable web views.

## Hallucination controls

- Every extracted fact retains evidence text and a source URL.
- Code rules handle stable identifiers and known aliases before LLM fallback.
- A model may propose unresolved values but does not merge legal entities automatically.
- Unknown values remain unknown; the UI must not convert missing data to zero.
- Release validation checks orphan references, category coverage and schema compatibility.

## Portfolio edition

The public repository ships with 8 sanitized product records. Original article prose and images are deliberately omitted. Production adapters remain in the codebase to demonstrate the architecture, but no private infrastructure configuration or secret is included.
