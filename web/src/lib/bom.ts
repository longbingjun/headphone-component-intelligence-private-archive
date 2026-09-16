/** BOM 表展示：角色文案与空值占位 */

const ROLE_LABELS: Record<string, string> = {
  major: "核心部件",
  minor: "外围部件",
  core: "核心BOM",
  key: "关键BOM",
  auxiliary: "辅助器件",
  structure: "结构件",
  unidentified_marking: "待识别丝印",
};

export function formatBomRole(role: string | undefined | null): string {
  const r = (role || "").trim();
  if (!r) return "—";
  return ROLE_LABELS[r] || r;
}

export function formatBomCell(value: string | undefined | null): string {
  const v = (value || "").trim();
  return v || "—";
}
