export interface CompareCell {
  value: string;
  evidence: string;
  source_layer: string;
  source_url?: string;
}

export interface CompareProduct {
  canonical_id: string;
  brand: string;
  model: string;
  best_report_id?: string;
  cells: Record<string, CompareCell>;
}

export interface CompareData {
  category: string;
  param_rows: string[];
  products: CompareProduct[];
}

export interface CategoryItem {
  name: string;
  slug: string;
  file: string;
  product_count: number;
}

export interface ParamLabel {
  label: string;
  why?: string;
}

/** 首页/发现页使用的轻量产品索引条目，来自 products/index.json（不含 cells/evidence）。 */
export interface IndexProduct {
  canonical_id: string;
  brand: string;
  model: string;
  category: string;
  report_count?: number;
  video_count?: number;
  first_seen?: string;
  latest_published?: string;
  cost_completeness?: number;
  bom_row_count?: number;
  launch_date?: string;
  launch_display?: string;
  launch_status?: "verified" | "year_only" | "reported" | "pending" | "legacy_unresolved";
  research_priority?: "official_current" | "recent_pending_check" | "historical_reference";
  priority_rank?: number;
  official_page_status?: "found" | "not_found" | "unknown";
  official_page_url?: string;
  card_image_path?: string;
}

export function researchPriorityRank(product: IndexProduct): number {
  return product.priority_rank ?? 3;
}

export function researchPriorityLabel(product: IndexProduct): string {
  switch (product.research_priority) {
    case "official_current":
      return "官网可查";
    case "recent_pending_check":
      return "近两年待核验";
    default:
      return "历史参考";
  }
}

export interface CompareProfiles {
  default_profile: string;
  profiles: Record<
    string,
    { label: string; categories: string[]; p0: string[]; p1: string[] }
  >;
  param_labels: Record<string, ParamLabel>;
}

/** productDisplayName 只需要这三个字段，用最小形状而非 CompareProduct，
 * 这样 IndexProduct（无 cells 字段）等轻量产品对象也能直接复用同一函数。 */
export function productDisplayName(p: {
  canonical_id: string;
  brand: string;
  model: string;
}): string {
  const b = (p.brand || "").trim();
  const m = (p.model || "").trim();
  return `${b} ${m}`.trim() || p.canonical_id;
}

export function profileForCategory(
  profiles: CompareProfiles,
  category: string
): { key: string; p0: string[]; p1: string[] } {
  for (const [key, prof] of Object.entries(profiles.profiles)) {
    if (key === "default") continue;
    if (prof.categories.includes(category)) {
      return { key, p0: prof.p0, p1: prof.p1 };
    }
  }
  const d = profiles.profiles.default;
  return { key: "default", p0: d.p0, p1: d.p1 };
}

/** 某参数是否属于该品类的对比字段集（p0 或 p1）。 */
export function paramAppliesToCategory(
  profiles: CompareProfiles,
  category: string,
  param: string
): boolean {
  const { p0, p1 } = profileForCategory(profiles, category);
  return p0.includes(param) || p1.includes(param);
}

/** 跨品类对比：合并多个品类 profile 的参数行，保持全局 param_labels 顺序。 */
export function unifiedParamRows(
  profiles: CompareProfiles,
  categories: string[],
  showP1: boolean
): string[] {
  const seen = new Set<string>();
  const merged: string[] = [];
  for (const cat of categories) {
    const { p0, p1 } = profileForCategory(profiles, cat);
    for (const param of [...p0, ...(showP1 ? p1 : [])]) {
      if (!seen.has(param)) {
        seen.add(param);
        merged.push(param);
      }
    }
  }
  const labelOrder = Object.keys(profiles.param_labels);
  return merged.sort((a, b) => {
    const ai = labelOrder.indexOf(a);
    const bi = labelOrder.indexOf(b);
    if (ai === -1 && bi === -1) return a.localeCompare(b);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });
}

export function emptyHint(value: string | undefined): boolean {
  return !value || value === "0" || value === "—";
}

export function sourceLabel(layer: string): string {
  const map: Record<string, string> = {
    technical: "拆解",
    channel: "渠道",
    official: "官方",
    review: "评测",
  };
  return map[layer] || layer;
}
