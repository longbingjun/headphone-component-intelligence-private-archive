# Public demo data policy

## Included

- A small number of reviewed product identities and factual BOM fields.
- Normalized brand, supplier, component and model names.
- Short evidence excerpts only when necessary to explain extraction provenance.
- Publication metadata and direct links to the original public source.
- Original, portfolio-created raster and SVG illustrations; these are illustrative rather than source-site or product photographs.

## Excluded

- Source-site images and copied article pages.
- Full article text, complete subtitles or transcripts.
- Cookies, API keys, internal URLs and production credentials.
- Company databases, MinIO objects and production logs.
- Scheduled collection or incremental crawling in the public deployment.

The machine-readable boundary is declared in `data/public_demo_manifest.json` and checked by `scripts/validate_public_demo.py` before every Pages build.
