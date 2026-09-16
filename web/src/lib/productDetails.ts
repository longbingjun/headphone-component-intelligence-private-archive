/**
 * 对比表「核心卖点 / 物料清单」区块的数据来源：完整产品 JSON
 * （web/public/data/products/{canonical_id}.json）里的 market.selling_points /
 * bom_table。CompareData（/data/compare/{file}.json）只包含扁平的 cells，不含这两项，
 * 所以这里在客户端按需并发拉取，并用模块级 Map 做跨组件实例的请求缓存（同一次会话内，
 * 同一个产品无论在哪个对比视图被选中，都只请求一次）。
 */
import { useEffect, useRef, useState } from "react";
import { withBase } from "./paths";

export interface SellingPointItem {
  text: string;
  tag?: string;
  tags?: string[];
}

export interface BomRowItem {
  component: string;
  brand: string;
  model: string;
  role: string;
  side: string;
}

export interface ProductExtras {
  sellingPoints: SellingPointItem[];
  bomTable: BomRowItem[];
}

export type ProductExtrasState =
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "ready"; data: ProductExtras };

const extrasCache = new Map<string, Promise<ProductExtras>>();

function fetchProductExtras(canonicalId: string): Promise<ProductExtras> {
  const cached = extrasCache.get(canonicalId);
  if (cached) return cached;

  const pending = fetch(withBase(`/data/products/${canonicalId}.json`))
    .then((res) => {
      if (!res.ok) throw new Error(`fetch product detail failed: ${res.status}`);
      return res.json();
    })
    .then((full: { market?: { selling_points?: SellingPointItem[] }; bom_table?: BomRowItem[] }) => {
      const sellingPoints = ((full.market?.selling_points || []) as SellingPointItem[]).filter((sp) =>
        (sp.text || "").trim()
      );
      const bomTable = ((full.bom_table || []) as BomRowItem[]).filter(
        (r) => (r.component || "").trim() || (r.model || "").trim()
      );
      return { sellingPoints, bomTable };
    })
    .catch((err) => {
      extrasCache.delete(canonicalId);
      throw err;
    });

  extrasCache.set(canonicalId, pending);
  return pending;
}

/**
 * 按 canonical_id 并发拉取核心卖点/物料清单数据，返回 id -> 状态的字典。
 * 已经请求过（无论成功失败）的 id 不会重复发起网络请求，只有新增的 id 才会触发 fetch。
 */
export function useProductExtras(ids: string[]): Record<string, ProductExtrasState> {
  const [results, setResults] = useState<Record<string, ProductExtrasState>>({});
  const requested = useRef<Set<string>>(new Set());
  const key = ids.join(",");

  useEffect(() => {
    const toFetch = ids.filter((id) => id && !requested.current.has(id));
    if (!toFetch.length) return;
    toFetch.forEach((id) => requested.current.add(id));
    setResults((prev) => {
      const next = { ...prev };
      toFetch.forEach((id) => {
        next[id] = { status: "loading" };
      });
      return next;
    });
    toFetch.forEach((id) => {
      fetchProductExtras(id)
        .then((data) => {
          setResults((prev) => ({ ...prev, [id]: { status: "ready", data } }));
        })
        .catch((err) => {
          requested.current.delete(id);
          setResults((prev) => ({
            ...prev,
            [id]: { status: "error", error: err instanceof Error ? err.message : "load failed" },
          }));
        });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return results;
}

const SELLING_POINT_MAX_LEN = 34;

/** 卖点句子本身已经是拆句后的精炼短句，这里再做一次纯前端截断，保证对比表里
 * 一行只占一屏而不是大段落。 */
export function summarizeSellingPointText(text: string, maxLen = SELLING_POINT_MAX_LEN): string {
  const clean = (text || "").replace(/\s+/g, " ").trim();
  if (clean.length <= maxLen) return clean;
  return `${clean.slice(0, maxLen)}...`;
}

export interface BomPickResult {
  shown: BomRowItem[];
  remainingCount: number;
}

/** 核心部件（major）优先展示，数量不够时用外围部件（minor）补足，最多 max 项，
 * 剩余数量用于渲染"等 N 项"提示。 */
export function pickBomHighlights(bomTable: BomRowItem[], max = 7): BomPickResult {
  const majors = bomTable.filter((r) => r.role === "major");
  const minors = bomTable.filter((r) => r.role !== "major");
  const shown = majors.length >= max ? majors.slice(0, max) : [...majors, ...minors].slice(0, max);
  return { shown, remainingCount: Math.max(0, bomTable.length - shown.length) };
}

export function formatBomLine(row: BomRowItem): string {
  const component = (row.component || "").trim();
  const brandModel = [row.brand, row.model].map((v) => (v || "").trim()).filter(Boolean).join(" ");
  if (component && brandModel) return `${component} - ${brandModel}`;
  return component || brandModel || "—";
}
