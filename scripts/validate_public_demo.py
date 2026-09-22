"""Fail the public build when the portfolio data boundary is violated."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MANIFEST = DATA / "public_demo_manifest.json"
PRODUCTS = DATA / "curated" / "products"
REPORTS = DATA / "reports"
SELF = Path(__file__).resolve()
TEXT_SUFFIXES = {".py", ".md", ".json", ".yml", ".yaml", ".ts", ".tsx", ".astro", ".mjs", ".ini", ".txt", ".example"}

# Tokens are assembled from fragments so the validator does not flag itself.
FORBIDDEN_SOURCE_PATTERNS = {
    "organization identity": re.compile(r"@sho" + r"kz|gitlab\.sho" + r"kz", re.I),
    "employee identifier": re.compile(r"\b0" + r"17642\b|\b0" + r"14744\b"),
    "employee name": re.compile("龙" + "秉君|张" + "华"),
    "private network address": re.compile(r"(?<!\d)(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?!\d)"),
    "legacy secret variable": re.compile("DEFAULT_MODEL_" + "API_KEY|VIDEO_LLM_" + "API_KEY|VIDEO_" + "COOKIES_FILE|DEEPSEEK_" + "API_KEY"),
    "internal filesystem path": re.compile(r"D:\\intelligences-from-" + r"52audio-local|/data/" + r"52audio", re.I),
    "obvious secret value": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b|SESS" + r"DATA\s*=|bili_" + r"jct\s*=", re.I),
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fail(message: str) -> None:
    print(f"[public-demo] ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def walk(value, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}[{index}]")
    else:
        yield path, value


def main() -> None:
    manifest = load_json(MANIFEST)
    if manifest.get("dataset_kind") != "fixed_sanitized_public_demo":
        fail("dataset_kind must be fixed_sanitized_public_demo")
    if manifest.get("scheduled_collection") is not False:
        fail("scheduled_collection must be false")

    allowed_products = set(manifest["allowed_product_ids"])
    actual_products = {p.stem for p in PRODUCTS.glob("*.json") if p.name != "index.json"}
    if actual_products != allowed_products:
        fail(f"product whitelist mismatch: expected {sorted(allowed_products)}, got {sorted(actual_products)}")

    allowed_reports = set(manifest["allowed_report_ids"])
    actual_reports = {p.stem for p in REPORTS.glob("*.json")}
    if actual_reports != allowed_reports:
        fail(f"report whitelist mismatch: expected {sorted(allowed_reports)}, got {sorted(actual_reports)}")

    forbidden_key = re.compile(r"(^|\.)(content_html|full_text|transcript|cookie|api_key|secret)$", re.I)
    image_key = re.compile(r"(image|images|image_urls|report_image_urls|summary_image_urls)$", re.I)
    for path in [*PRODUCTS.glob("*.json"), *REPORTS.glob("*.json")]:
        if path.name == "index.json":
            continue
        payload = load_json(path)
        for field, value in walk(payload):
            if forbidden_key.search(field) and value:
                fail(f"forbidden source or secret field in {path.name}: {field}")
            if image_key.search(field) and value not in (None, "", []):
                fail(f"source image content must not ship in {path.name}: {field}")
            if field.lower().endswith("evidence_quote") and isinstance(value, str) and len(value) > 220:
                fail(f"evidence excerpt is too long in {path.name}: {field}")

    workflow_text = "\n".join(
        p.read_text(encoding="utf-8") for p in (ROOT / ".github" / "workflows").glob("*.yml")
    )
    if re.search(r"^\s*schedule\s*:", workflow_text, re.M):
        fail("public workflows must not contain a schedule trigger")

    for path in ROOT.rglob("*"):
        if not path.is_file() or path.resolve() == SELF:
            continue
        if any(part in {".git", ".venv", "node_modules", "site", "__pycache__"} for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"Dockerfile", "Dockerfile.video-worker", ".env.example"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in FORBIDDEN_SOURCE_PATTERNS.items():
            if pattern.search(text):
                fail(f"{label} found in {path.relative_to(ROOT).as_posix()}")

    print(f"[public-demo] OK: {len(actual_products)} products, {len(actual_reports)} source records")


if __name__ == "__main__":
    main()
