export const SELLING_POINT_TAG_COLORS: Record<string, { bg: string; text: string }> = {
  佩戴体验: { bg: "#ede9fe", text: "#6d28d9" },
  音质体验: { bg: "#fef3c7", text: "#a16207" },
  通话体验: { bg: "#dbeafe", text: "#1d4ed8" },
  续航充电: { bg: "#dcfce7", text: "#15803d" },
  连接与智能: { bg: "#cffafe", text: "#0e7490" },
  耐用防护: { bg: "#ffedd5", text: "#c2410c" },
  外观设计: { bg: "#fce7f3", text: "#be185d" },
  降噪: { bg: "#e0e7ff", text: "#3730a3" },
  开放佩戴: { bg: "#e0f2f1", text: "#0f766e" },
  空间音频: { bg: "#fce7f3", text: "#be185d" },
  长续航: { bg: "#dcfce7", text: "#15803d" },
  游戏低延迟: { bg: "#fef3c7", text: "#b45309" },
  音质认证: { bg: "#fef9c3", text: "#a16207" },
  舒适佩戴: { bg: "#f3e8ff", text: "#7c3aed" },
  防水防尘: { bg: "#e0f2fe", text: "#0369a1" },
  旗舰定位: { bg: "#ffe4e6", text: "#be123c" },
  其他: { bg: "#f1f5f9", text: "#475569" },
};

export function sellingPointTagStyle(tag: string) {
  const key = (tag || "").trim() || "其他";
  return SELLING_POINT_TAG_COLORS[key] || SELLING_POINT_TAG_COLORS["其他"];
}

export function collectSellingPointTags(sp: { tag?: string; tags?: string[] }): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of [sp.tag, ...(sp.tags || [])]) {
    const t = (raw || "").trim();
    if (!t || seen.has(t)) continue;
    seen.add(t);
    out.push(t);
  }
  if (!out.length) out.push("其他");
  return out;
}
