import fs from "node:fs";
import path from "node:path";

export interface SampleCoverage {
  generated_at: string;
  data_version: string;
  schema_version: string;
  product_count: number;
  supplier_name_count: number;
  brand_name_count: number;
  report_count: number;
  video_count: number;
}

const EXPECTED_SCHEMA_VERSION = "0008_data_migration_history";

let cachedCoverage: SampleCoverage | null = null;

function readJson(filePath: string): Record<string, any> {
  if (!fs.existsSync(filePath)) return {};
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf-8"));
  } catch {
    return {};
  }
}

export function getSampleCoverage(): SampleCoverage {
  if (cachedCoverage) return cachedCoverage;

  const dataDir = path.join(process.cwd(), "public", "data");
  const summary = readJson(path.join(dataDir, "coverage-summary.json"));
  if (Number(summary.product_count) > 0) {
    const release = summary._release || {};
    if (release.schema_version !== EXPECTED_SCHEMA_VERSION) {
      throw new Error(`Web data schema mismatch: expected ${EXPECTED_SCHEMA_VERSION}, received ${release.schema_version || "missing"}`);
    }
    cachedCoverage = {
      generated_at: String(summary.generated_at || ""),
      data_version: String(release.data_version || ""),
      schema_version: String(release.schema_version || ""),
      product_count: Number(summary.product_count || 0),
      supplier_name_count: Number(summary.supplier_name_count || 0),
      brand_name_count: Number(summary.brand_name_count || 0),
      report_count: Number(summary.report_count || 0),
      video_count: Number(summary.video_count || 0),
    };
    return cachedCoverage;
  }

  // Backward-compatible fallback for a checkout whose publishing data predates
  // coverage-summary.json. This is cached at module scope so a static build with
  // thousands of routes does not repeatedly parse the larger manifests.
  const products = readJson(path.join(dataDir, "products", "index.json"));
  const suppliers = readJson(path.join(dataDir, "supplier-sourcing", "manifest.json"));
  const teardown = readJson(path.join(dataDir, "teardown_details.json"));
  const productRows = Array.isArray(products.products) ? products.products : [];
  const brandNames = new Set(
    productRows.map((item: any) => String(item?.brand || "").trim()).filter(Boolean),
  );

  cachedCoverage = {
    generated_at: String(products.generated_at || teardown.generated_at || ""),
    data_version: String(products?._release?.data_version || "legacy"),
    schema_version: String(products?._release?.schema_version || "legacy"),
    product_count: productRows.length,
    supplier_name_count: Array.isArray(suppliers.suppliers) ? suppliers.suppliers.length : 0,
    brand_name_count: brandNames.size,
    report_count: Number(teardown.report_count || 0),
    video_count: Number(teardown.video_count || 0),
  };
  return cachedCoverage;
}
