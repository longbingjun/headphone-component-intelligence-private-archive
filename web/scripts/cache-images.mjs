#!/usr/bin/env node
/**
 * Download only images referenced by the generated web payload, then convert
 * them to bounded WebP assets. Source URLs remain in ETL data for traceability;
 * GitHub Pages receives display-sized derivatives only.
 */
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import sharp from "sharp";

const PRODUCTS_DIR = path.join(process.cwd(), "public", "data", "products");
const ROUNDUP_INSIGHTS_PATH = path.join(process.cwd(), "public", "data", "roundup_insights.json");
const IMAGES_DIR = process.env.IMAGE_CACHE_DIR
  ? path.resolve(process.env.IMAGE_CACHE_DIR)
  : path.join(process.cwd(), "public", "images");
const REFERER = "https://www.52audio.com/";
const CONCURRENCY = 6;
const MAX_EDGE = Number(process.env.IMAGE_MAX_EDGE || 1440);
const WEBP_QUALITY = Number(process.env.IMAGE_WEBP_QUALITY || 78);
const MAX_FAILURE_RATIO = Number(process.env.IMAGE_MAX_FAILURE_RATIO || 0.05);
const FETCH_TIMEOUT_MS = Number(process.env.IMAGE_FETCH_TIMEOUT_MS || 30000);
const FETCH_RETRIES = Number(process.env.IMAGE_FETCH_RETRIES || 2);
const IMAGE_URL_RE = /^https?:\/\/\S+\.(jpe?g|png|webp|gif)(\?\S*)?$/i;

export function localImageFilename(url) {
  const hash = crypto.createHash("sha1").update(url).digest("hex").slice(0, 16);
  return `${hash}.webp`;
}

function collectImageUrls() {
  const urls = new Set();
  const walk = (node) => {
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (!node || typeof node !== "object") return;
    for (const value of Object.values(node)) {
      if (typeof value === "string" && IMAGE_URL_RE.test(value)) urls.add(value);
      else walk(value);
    }
  };

  if (fs.existsSync(PRODUCTS_DIR)) {
    for (const file of fs.readdirSync(PRODUCTS_DIR).filter((name) => name.endsWith(".json") && name !== "index.json")) {
      try {
        walk(JSON.parse(fs.readFileSync(path.join(PRODUCTS_DIR, file), "utf-8")));
      } catch (error) {
        console.warn(`[cache-images] skipped invalid product ${file}: ${error.message}`);
      }
    }
  }
  if (fs.existsSync(ROUNDUP_INSIGHTS_PATH)) {
    try {
      walk(JSON.parse(fs.readFileSync(ROUNDUP_INSIGHTS_PATH, "utf-8")));
    } catch (error) {
      console.warn(`[cache-images] skipped invalid roundup data: ${error.message}`);
    }
  }
  return [...urls];
}

function pruneStaleImages(expectedNames) {
  if (!fs.existsSync(IMAGES_DIR)) return 0;
  let removed = 0;
  for (const name of fs.readdirSync(IMAGES_DIR)) {
    if (expectedNames.has(name)) continue;
    const target = path.join(IMAGES_DIR, name);
    if (!fs.statSync(target).isFile()) continue;
    fs.unlinkSync(target);
    removed += 1;
  }
  return removed;
}

async function downloadOne(url) {
  const dest = path.join(IMAGES_DIR, localImageFilename(url));
  if (fs.existsSync(dest) && fs.statSync(dest).size > 0) return { url, status: "cached" };
  let lastReason = "unknown error";
  for (let attempt = 0; attempt <= FETCH_RETRIES; attempt += 1) {
    try {
      const response = await fetch(url, {
        headers: {
          Referer: REFERER,
          "User-Agent": "Mozilla/5.0 (compatible; 52audio-intel-bot/2.0)",
        },
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const source = Buffer.from(await response.arrayBuffer());
      await sharp(source, { animated: false })
        .rotate()
        .resize({ width: MAX_EDGE, height: MAX_EDGE, fit: "inside", withoutEnlargement: true })
        .webp({ quality: WEBP_QUALITY, effort: 4 })
        .toFile(dest);
      return { url, status: "downloaded" };
    } catch (error) {
      lastReason = error.message;
      if (attempt < FETCH_RETRIES) {
        await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
      }
    }
  }
  return { url, status: "failed", reason: lastReason };
}

async function runPool(items, worker, concurrency) {
  const results = new Array(items.length);
  let index = 0;
  async function next() {
    while (index < items.length) {
      const current = index++;
      results[current] = await worker(items[current]);
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, next));
  return results;
}

async function main() {
  fs.mkdirSync(IMAGES_DIR, { recursive: true });
  const urls = collectImageUrls();
  const expectedNames = new Set(urls.map(localImageFilename));
  const removed = pruneStaleImages(expectedNames);
  console.log(`[cache-images] ${urls.length} referenced images; pruned ${removed} stale files`);

  const results = await runPool(urls, downloadOne, CONCURRENCY);
  const summary = { downloaded: 0, cached: 0, failed: 0 };
  const failures = [];
  for (const result of results) {
    summary[result.status] += 1;
    if (result.status === "failed") failures.push(result);
  }
  console.log(`[cache-images] downloaded ${summary.downloaded}, cached ${summary.cached}, failed ${summary.failed}`);
  failures.slice(0, 10).forEach((item) => console.warn(`  - ${item.url} (${item.reason})`));
  const failureRatio = urls.length ? summary.failed / urls.length : 0;
  if (failureRatio > MAX_FAILURE_RATIO) {
    throw new Error(
      `image failure ratio ${(failureRatio * 100).toFixed(2)}% exceeds ${(MAX_FAILURE_RATIO * 100).toFixed(2)}%`,
    );
  }
}

main().catch((error) => {
  console.error(`[cache-images] fatal: ${error.message}`);
  process.exitCode = 1;
});
