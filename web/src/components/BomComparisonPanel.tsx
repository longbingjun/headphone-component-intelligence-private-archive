import { useMemo, useState } from "react";
import type { ProductExtrasState, TeardownInventoryItem } from "../lib/productExtras";

export interface BomComparisonProduct {
  id: string;
  name: string;
  href: string;
  category?: string;
}

interface Props {
  open: boolean;
  products: BomComparisonProduct[];
  extras: Record<string, ProductExtrasState>;
  onClose: () => void;
}

type GroupKey = "all" | "core" | "structure" | "auxiliary";

const text = (value: unknown) => String(value ?? "").trim();

function groupOf(row: TeardownInventoryItem): Exclude<GroupKey, "all"> {
  const raw = text(row.classification || row.role).toLowerCase();
  if (["core", "key", "major"].includes(raw)) return "core";
  if (raw === "structure") return "structure";
  return "auxiliary";
}

const groupLabel: Record<GroupKey, string> = {
  all: "全部器件",
  core: "核心 / 关键 BOM",
  structure: "结构件",
  auxiliary: "辅助器件",
};

function normalizedRows(state: ProductExtrasState | undefined): TeardownInventoryItem[] {
  if (!state || state === "loading" || state === "error") return [];
  if (state.teardownInventory.length) return state.teardownInventory;
  return state.bomTable.map((row) => ({ ...row, classification: row.role || "auxiliary" }));
}

function rowSignature(row: TeardownInventoryItem) {
  return [row.brand, row.model, row.side, row.specification, row.fact_text].map(text).join("|");
}

function evidenceText(row: TeardownInventoryItem) {
  return text(row.fact_text || row.evidence?.text || row.evidence?.source_text || row.evidence_texts?.[0]);
}

function BomEntry({ row }: { row: TeardownInventoryItem }) {
  const [imageFailed, setImageFailed] = useState<Record<number, boolean>>({});
  const images = row.evidence_images || [];
  const specs = [text(row.brand), text(row.model), text(row.specification)].filter(Boolean);
  const quote = evidenceText(row);
  return (
    <article className="bom-compare-entry">
      <header>
        <strong>{specs.join(" · ") || "型号/参数未披露"}</strong>
        {row.side && <span>{row.side}</span>}
      </header>
      {row.qty_hint && <small>数量线索：{row.qty_hint}</small>}
      {(quote || images.length > 0) && <details>
        <summary>查看提取依据与公开来源</summary>
        {quote && <blockquote>{quote}</blockquote>}
        {images.length > 0 && <div className="bom-compare-images">{images.slice(0, 4).map((image, index) => {
          const local = text(image.public_path);
          const remote = text(image.url);
          const src = imageFailed[index] && remote ? remote : local || remote;
          return src ? <a key={`${src}-${index}`} href={remote || src} target="_blank" rel="noreferrer"><img src={src} alt={image.caption || image.alt || `${row.component || "器件"}图片证据`} loading="lazy" onError={() => setImageFailed((current) => ({ ...current, [index]: true }))} /></a> : null;
        })}</div>}
      </details>}
    </article>
  );
}

export default function BomComparisonPanel({ open, products, extras, onClose }: Props) {
  const [group, setGroup] = useState<GroupKey>("all");

  const rowsByProduct = useMemo(() => new Map(products.map((product) => [product.id, normalizedRows(extras[product.id])])), [products, extras]);
  const components = useMemo(() => {
    const groupOrder: Record<Exclude<GroupKey, "all">, number> = { core: 0, structure: 1, auxiliary: 2 };
    const map = new Map<string, { component: string; group: Exclude<GroupKey, "all"> }>();
    rowsByProduct.forEach((rows) => rows.forEach((row) => {
      const component = text(row.component) || "未命名器件";
      const nextGroup = groupOf(row);
      const current = map.get(component);
      if (!current || groupOrder[nextGroup] < groupOrder[current.group]) map.set(component, { component, group: nextGroup });
    }));
    return [...map.values()].sort((a, b) => groupOrder[a.group] - groupOrder[b.group] || a.component.localeCompare(b.component, "zh-CN"));
  }, [rowsByProduct]);

  const readyProducts = products.filter((product) => {
    const state = extras[product.id];
    return state && state !== "loading" && state !== "error";
  });
  const common = components.filter(({ component }) => readyProducts.length > 1 && readyProducts.every((product) => (rowsByProduct.get(product.id) || []).some((row) => (text(row.component) || "未命名器件") === component)));
  const partial = components.filter(({ component }) => readyProducts.some((product) => (rowsByProduct.get(product.id) || []).some((row) => (text(row.component) || "未命名器件") === component)) && !readyProducts.every((product) => (rowsByProduct.get(product.id) || []).some((row) => (text(row.component) || "未命名器件") === component)));
  const changed = common.filter(({ component }) => {
    const signatures = readyProducts.map((product) => (rowsByProduct.get(product.id) || []).filter((row) => (text(row.component) || "未命名器件") === component).map(rowSignature).sort().join("||"));
    return new Set(signatures).size > 1;
  });
  const visibleComponents = group === "all" ? components : components.filter((item) => item.group === group);

  if (!open) return null;
  return (
    <div className="bom-compare-overlay" role="dialog" aria-modal="true" aria-label="产品 BOM 对比" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="bom-compare-panel">
        <header className="bom-compare-header">
          <div><span>Component-aligned comparison</span><h2>产品 BOM 对比</h2><p>同名器件横向对齐；“共同”只表示均有记录，“差异”表示型号、位置或描述不同。</p></div>
          <button type="button" onClick={onClose}>关闭 ×</button>
        </header>

        <div className="bom-compare-summary">
          <article><span>参与对比</span><strong>{products.length}</strong><small>款产品</small></article>
          <article><span>共同器件类型</span><strong>{common.length}</strong><small>所有产品均有记录</small></article>
          <article><span>规格存在差异</span><strong>{changed.length}</strong><small>型号、位置或描述不同</small></article>
          <article><span>仅部分产品出现</span><strong>{partial.length}</strong><small>可能是差异或报告未披露</small></article>
        </div>

        <nav className="bom-compare-groups" aria-label="BOM 分类筛选">
          {(Object.keys(groupLabel) as GroupKey[]).map((key) => <button key={key} type="button" aria-pressed={group === key} className={group === key ? "is-active" : ""} onClick={() => setGroup(key)}>{groupLabel[key]}{key !== "all" && <small>{components.filter((item) => item.group === key).length}</small>}</button>)}
        </nav>

        <div className="bom-compare-table-wrap">
          <table>
            <thead><tr><th>器件类型</th>{products.map((product) => <th key={product.id}><a href={product.href}>{product.name}</a>{product.category && <small>{product.category}</small>}</th>)}</tr></thead>
            <tbody>{visibleComponents.map(({ component, group: componentGroup }) => <tr key={component}><th><strong>{component}</strong><small>{groupLabel[componentGroup]}</small>{common.some((item) => item.component === component) && <span>{changed.some((item) => item.component === component) ? "共同器件 · 规格不同" : "共同器件"}</span>}</th>{products.map((product) => {
              const rows = (rowsByProduct.get(product.id) || []).filter((row) => (text(row.component) || "未命名器件") === component);
              const state = extras[product.id];
              return <td key={`${component}-${product.id}`}>{state === "loading" || !state ? <small>正在加载…</small> : state === "error" ? <small>数据加载失败</small> : rows.length ? rows.map((row, index) => <BomEntry key={`${rowSignature(row)}-${index}`} row={row} />) : <span className="bom-compare-missing">报告未披露</span>}</td>;
            })}</tr>)}</tbody>
          </table>
        </div>
        <footer><span>“报告未披露”不等于产品没有该器件；请结合原始拆解证据判断。</span>{products.map((product) => {
          const state = extras[product.id];
          return <span key={product.id}><a href={`${product.href}#bom`}>{product.name} 完整 BOM</a>{state && state !== "loading" && state !== "error" && state.sourceUrl && <> · <a href={state.sourceUrl} target="_blank" rel="noreferrer">来源原文 ↗</a></>}</span>;
        })}</footer>
      </section>
    </div>
  );
}
