/**
 * 对比表「核心卖点 / 物料清单」区块用到的客户端数据获取与展示辅助函数。
 *
 * CompareData（/data/compare/{file}.json）里的 cells 是按 compare_profiles 参数扁平化的
 * 技术参数，不包含 market.consumer_claims / bom_table 这类结构化列表——这两项只存在于完整的
 * 产品档案 /data/products/{canonical_id}.json 里；卖点以经过证据校验的
 * market.consumer_claims 为唯一来源。这里提供一个按 canonical_id 缓存的
 * fetch + React hook，供 CompareWorkbench / CrossCategoryCompare 共用，避免重复请求、
 * 避免每次选择变化都全量重新拉取。
 */
import { useEffect, useState } from "react";
import { withBase } from "./paths";

export interface SellingPointItem {
  text?: string;
  tag?: string;
  tags?: string[];
  evidence?: { confidence?: number };
}

export interface BomRowItem {
  component?: string;
  brand?: string;
  model?: string;
  role?: string;
  side?: string;
  qty_hint?: string;
  evidence?: { source_text?: string; text?: string; confidence?: number };
}

export interface BomEvidenceImage {
  url?: string;
  public_path?: string;
  alt?: string;
  caption?: string;
}

export interface TeardownInventoryItem extends BomRowItem {
  classification?: string;
  classification_label?: string;
  classification_reason?: string;
  specification?: string;
  fact_text?: string;
  evidence_images?: BomEvidenceImage[];
  evidence_texts?: string[];
}

export interface ProductExtras {
  sellingPoints: SellingPointItem[];
  scenarios: string[];
  bomTable: BomRowItem[];
  teardownInventory: TeardownInventoryItem[];
  sourceUrl: string;
}

export type ProductExtrasState = ProductExtras | "loading" | "error";

const extrasCache = new Map<string, ProductExtras>();
const inflightRequests = new Map<string, Promise<ProductExtras>>();

function fetchExtras(canonicalId: string): Promise<ProductExtras> {
  const cached = extrasCache.get(canonicalId);
  if (cached) return Promise.resolve(cached);
  const pending = inflightRequests.get(canonicalId);
  if (pending) return pending;

  const promise = fetch(withBase(`/data/products/${canonicalId}.json`))
    .then((res) => {
      if (!res.ok) throw new Error(`fetch product ${canonicalId} failed: ${res.status}`);
      return res.json();
    })
    .then((data: {
      market?: { consumer_claims?: SellingPointItem[]; scenarios?: string[] };
      bom_table?: BomRowItem[];
      teardown_inventory?: TeardownInventoryItem[];
      related_intelligence?: { kind?: string; source_url?: string }[];
    }) => {
      const extras: ProductExtras = {
        sellingPoints: (data.market?.consumer_claims || []).filter((sp) => (sp.text || "").trim()),
        scenarios: (data.market?.scenarios || []).filter(Boolean),
        bomTable: data.bom_table || [],
        teardownInventory: data.teardown_inventory || [],
        sourceUrl: (data.related_intelligence || []).find((item) => item.kind === "report")?.source_url || "",
      };
      extrasCache.set(canonicalId, extras);
      return extras;
    })
    .finally(() => {
      inflightRequests.delete(canonicalId);
    });

  inflightRequests.set(canonicalId, promise);
  return promise;
}

/**
 * 按当前需要的 canonical_id 列表拉取产品详情（卖点/BOM），结果按 id 缓存在组件 state 中。
 * 已经拉取过的 id 不会重复请求；新增的 id 会在挂载/依赖变化后并发拉取。
 */
export function useProductExtras(ids: string[]): Record<string, ProductExtrasState> {
  const idsKey = ids.join(",");
  const [state, setState] = useState<Record<string, ProductExtrasState>>({});

  useEffect(() => {
    let cancelled = false;
    const list = idsKey ? idsKey.split(",") : [];

    list.forEach((id) => {
      if (!id) return;
      const cached = extrasCache.get(id);
      if (cached) {
        setState((prev) => (prev[id] ? prev : { ...prev, [id]: cached }));
        return;
      }
      setState((prev) => (prev[id] ? prev : { ...prev, [id]: "loading" }));
      fetchExtras(id)
        .then((extras) => {
          if (cancelled) return;
          setState((prev) => ({ ...prev, [id]: extras }));
        })
        .catch((err) => {
          console.error(`加载产品 ${id} 的卖点/物料数据失败`, err);
          if (cancelled) return;
          setState((prev) => ({ ...prev, [id]: "error" }));
        });
    });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey]);

  return state;
}

/** 卖点分点展示：按证据置信度排序，只取最有代表性的前 N 条，并做字符截断。 */
export function pickTopSellingPoints(points: SellingPointItem[], limit = 5): SellingPointItem[] {
  return [...points]
    .filter((p) => (p.text || "").trim())
    .sort((a, b) => (b.evidence?.confidence ?? 0) - (a.evidence?.confidence ?? 0))
    .slice(0, limit);
}

export function truncateText(text: string | undefined, max = 44): string {
  const t = (text || "").trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max).trimEnd()}…`;
}

/**
 * 对比页不直接堆放采集原文：保留一条卖点中最有信息量的首句，并去掉来源性套话。
 * 这是稳定、可复现的规则摘要；后续如接入离线模型，可在构建阶段替换这一层。
 */
export function conciseSellingPoint(text: string | undefined, max = 72): string {
  const normalized = (text || "")
    .replace(/\s+/g, " ")
    .replace(/^(?:在|从)?(?:外观|音频体验|续航方面|功能方面)[，,:：]?/, "")
    .replace(/(?:下面就来看看|我爱音频网此前).*$/, "")
    .trim();
  const firstSentence = normalized.split(/[。！？]/)[0]?.trim() || normalized;
  return truncateText(firstSentence, max);
}

export interface BomHighlightItem {
  key: string;
  label: string;
}

export interface BomHighlights {
  items: BomHighlightItem[];
  remaining: number;
  total: number;
}

function formatBomLabel(r: BomRowItem): string {
  const comp = (r.component || "").trim() || "未命名部件";
  const spec = [r.brand, r.model]
    .map((s) => (s || "").trim())
    .filter(Boolean)
    .join(" ");
  return spec ? `${comp} · ${spec}` : comp;
}

/** 物料清单展示：优先列核心部件（role=major），数量不够再用外围部件补齐，超出上限折叠。 */
export function pickBomHighlights(rows: BomRowItem[], limit = 7): BomHighlights {
  const clean = rows.filter((r) => (r.component || "").trim() || (r.model || "").trim());
  const majors = clean.filter((r) => r.role === "major");
  const minors = clean.filter((r) => r.role !== "major");
  const ordered = [...majors, ...minors];
  const items = ordered.slice(0, limit).map((r, i) => ({
    key: `${r.component || "part"}-${i}`,
    label: formatBomLabel(r),
  }));
  const total = clean.length;
  return { items, remaining: Math.max(0, total - items.length), total };
}

