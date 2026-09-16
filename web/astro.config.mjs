import { defineConfig } from "astro/config";
import react from "@astrojs/react";
import tailwindcss from "@tailwindcss/vite";

const defaultBase =
  process.env.NODE_ENV === "development"
    ? "/"
    : "/headphone-component-intelligence/";
const repoBase = process.env.PUBLIC_BASE_PATH || defaultBase;
const outDir = process.env.ASTRO_OUT_DIR || "../site";

export default defineConfig({
  site: process.env.PUBLIC_SITE_URL || "https://longbingjun.github.io",
  base: repoBase,
  integrations: [react()],
  vite: { plugins: [tailwindcss()] },
  outDir,
  build: {
    format: "directory",
    // ARC reserves/filters a generic top-level `assets` directory while
    // assembling its runtime image. Keep Astro's conventional directory so
    // hashed CSS and client JavaScript survive the builder -> runtime copy.
    assets: "_astro",
  },
  trailingSlash: "ignore",
});
