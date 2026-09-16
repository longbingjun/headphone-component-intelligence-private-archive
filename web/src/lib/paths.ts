/** GitHub Pages 项目站前缀，与 astro.config.mjs 中 base 保持一致 */
export const baseUrl = import.meta.env.BASE_URL;

export function withBase(path: string): string {
  if (!path || path === "/") return baseUrl;
  const clean = path.startsWith("/") ? path.slice(1) : path;
  return `${baseUrl}${clean}`;
}
