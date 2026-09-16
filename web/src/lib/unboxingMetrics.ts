// \u5356\u70b9\u6807\u7b7e\u914d\u8272\u7edf\u4e00\u4ece sellingPointTags.ts \u53d6\uff0c\u907f\u514d\u4e0e\u8be5\u6587\u4ef6\u91cd\u590d\u5b9a\u4e49\u3002
export {
  SELLING_POINT_TAG_COLORS,
  sellingPointTagStyle,
  collectSellingPointTags,
} from "./sellingPointTags";

export type UnboxModuleInput = {
  description?: string;
  accessories?: string[];
  appearance_images?: unknown[];
  image_count?: number;
  teardown_image_count?: number;
};

export type UnboxMetric = {
  label: string;
  value: number;
  detail: string;
};

export type UnboxModuleMetrics = {
  overall: number;
  bars: UnboxMetric[];
};

function clampPct(n: number): number {
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function descScore(description?: string): number {
  const text = (description || "").trim();
  if (!text) return 0;
  return clampPct((text.length / 120) * 100);
}

function imageScore(resolvedCount: number, imageCount?: number, fallbackTotal?: number): number {
  const total = Math.max(imageCount || 0, fallbackTotal || 0, 1);
  return clampPct((resolvedCount / total) * 100);
}

export function computeUnboxModuleMetrics(
  mod: UnboxModuleInput,
  resolvedImageCount: number,
  moduleKey: "packaging" | "charging_case" | "earbuds"
): UnboxModuleMetrics {
  const desc = descScore(mod.description);
  const images = imageScore(
    resolvedImageCount,
    mod.image_count,
    (mod.appearance_images || []).length
  );

  const bars: UnboxMetric[] = [
    {
      label: "\u6587\u5b57\u63cf\u8ff0",
      value: desc,
      detail: mod.description?.trim() ? `${mod.description.trim().length} \u5b57` : "\u6682\u65e0\u63cf\u8ff0",
    },
    {
      label: "\u56fe\u7247\u8986\u76d6",
      value: images,
      detail: `${resolvedImageCount}/${Math.max(mod.image_count || 0, (mod.appearance_images || []).length, resolvedImageCount)} \u5f20\u5df2\u7f13\u5b58`,
    },
  ];

  if (moduleKey === "packaging") {
    const acc = mod.accessories || [];
    const accScore = acc.length ? clampPct((acc.length / 5) * 100) : 0;
    bars.push({
      label: "\u914d\u4ef6\u6e05\u5355",
      value: accScore,
      detail: acc.length ? `${acc.length} \u9879` : "\u672a\u8bb0\u5f55\u914d\u4ef6",
    });
  } else {
    const teardown = mod.teardown_image_count || 0;
    const total = Math.max(mod.image_count || 0, 1);
    bars.push({
      label: "\u62c6\u89e3\u56fe\u5360\u6bd4",
      value: clampPct((teardown / total) * 100),
      detail: `${teardown}/${total} \u5f20\u4e3a\u62c6\u89e3\u56fe`,
    });
  }

  const overall = clampPct(bars.reduce((sum, b) => sum + b.value, 0) / bars.length);
  return { overall, bars };
}

export const UNBOX_MODULE_COLORS: Record<
  string,
  { bg: string; text: string; border: string; bar: string }
> = {
  "\u5305\u88c5": {
    bg: "#fff3e0",
    text: "#b45309",
    border: "#ffe1b0",
    bar: "#f59e0b",
  },
  "\u5145\u7535\u76d2": {
    bg: "#eef3ff",
    text: "#1f3fbf",
    border: "#c7d6ff",
    bar: "#335cff",
  },
  "\u8033\u673a": {
    bg: "#f3e8ff",
    text: "#7c3aed",
    border: "#ddc9ff",
    bar: "#a78bfa",
  },
};

export function unboxModuleStyle(title: string) {
  return UNBOX_MODULE_COLORS[title] || {
    bg: "#f1f5f9",
    text: "#475569",
    border: "#e2e8f0",
    bar: "#94a3b8",
  };
}