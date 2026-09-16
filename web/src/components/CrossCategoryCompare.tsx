import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  CompareData,
  CompareProduct,
  CompareProfiles,
  IndexProduct,
  ParamLabel,
} from "../lib/types";
import {
  emptyHint,
  paramAppliesToCategory,
  productDisplayName,
  sourceLabel,
  unifiedParamRows,
} from "../lib/types";
import { withBase } from "../lib/paths";
import { useProductExtras } from "../lib/productExtras";
import { ScenariosCell, SellingPointTagsCell, SellingPointsCell } from "./CompareExtrasCells";
import BomComparisonPanel from "./BomComparisonPanel";

interface CategoryMeta {
  name: string;
  slug: string;
  file: string;
  product_count: number;
}

interface CompareProductWithCategory extends CompareProduct {
  category: string;
}

interface Props {
  categories: CategoryMeta[];
  profiles: CompareProfiles;
  productIndexUrl: string;
  compareDataBaseUrl: string;
}

const CATEGORY_COLORS: Record<string, { bg: string; text: string }> = {
  头戴式耳机: { bg: "#f3e8ff", text: "#7c3aed" },
  耳夹式耳机: { bg: "#dcfce7", text: "#15803d" },
  耳挂式耳机: { bg: "#ccfbf1", text: "#0f766e" },
  全入耳式耳机: { bg: "#eef3ff", text: "#1f3fbf" },
  半入耳式耳机: { bg: "#e0e7ff", text: "#4338ca" },
  待细分耳机: { bg: "#fef3c7", text: "#92400e" },
  有线耳机: { bg: "#f1f5f9", text: "#475569" },
  颈挂式蓝牙耳机: { bg: "#fff3e0", text: "#b45309" },
  骨传导耳机: { bg: "#ffe4e6", text: "#be123c" },
};

function categoryTagStyle(name: string) {
  return CATEGORY_COLORS[name] || { bg: "#f1f5f9", text: "#475569" };
}

export default function CrossCategoryCompare({
  categories,
  profiles,
  productIndexUrl,
  compareDataBaseUrl,
}: Props) {
  const link = (path: string) => withBase(path);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [products, setProducts] = useState<CompareProductWithCategory[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [drawer, setDrawer] = useState<{
    product: CompareProductWithCategory;
    param: string;
    label: string;
  } | null>(null);
  const [bomPanelOpen, setBomPanelOpen] = useState(false);

  const categoryByFile = useMemo(() => {
    const map = new Map<string, CategoryMeta>();
    categories.forEach((c) => map.set(c.name, c));
    return map;
  }, [categories]);

  const syncUrl = useCallback((ids: string[]) => {
    const url = new URL(window.location.href);
    if (ids.length) url.searchParams.set("ids", ids.join(","));
    else url.searchParams.delete("ids");
    window.history.replaceState({}, "", url.toString());
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const ids = (params.get("ids") || "").split(",").filter(Boolean);
    setSelectedIds(ids);
  }, []);

  useEffect(() => {
    if (!selectedIds.length) {
      setProducts([]);
      setLoadError("");
      return;
    }

    let cancelled = false;
    setLoading(true);
    setLoadError("");

    (async () => {
      try {
        const indexRes = await fetch(productIndexUrl);
        if (!indexRes.ok) throw new Error(`产品索引加载失败: ${indexRes.status}`);
        const indexData = (await indexRes.json()) as { products: IndexProduct[] };
        const indexById = new Map(indexData.products.map((p) => [p.canonical_id, p]));

        const byCategory = new Map<string, string[]>();
        const missing: string[] = [];
        selectedIds.forEach((id) => {
          const meta = indexById.get(id);
          if (!meta) {
            missing.push(id);
            return;
          }
          const list = byCategory.get(meta.category) || [];
          list.push(id);
          byCategory.set(meta.category, list);
        });

        const fetched: CompareProductWithCategory[] = [];
        await Promise.all(
          Array.from(byCategory.entries()).map(async ([categoryName, ids]) => {
            const catMeta = categoryByFile.get(categoryName);
            if (!catMeta) return;
            const res = await fetch(`${compareDataBaseUrl}/${catMeta.file}`);
            if (!res.ok) throw new Error(`${categoryName} 对比数据加载失败: ${res.status}`);
            const data = (await res.json()) as CompareData;
            const idSet = new Set(ids);
            data.products.forEach((p) => {
              if (idSet.has(p.canonical_id)) {
                fetched.push({ ...p, category: categoryName });
              }
            });
          })
        );

        if (cancelled) return;

        const order = new Map(selectedIds.map((id, i) => [id, i]));
        fetched.sort(
          (a, b) => (order.get(a.canonical_id) ?? 999) - (order.get(b.canonical_id) ?? 999)
        );

        setProducts(fetched);
        if (missing.length) {
          setLoadError(`未找到 ${missing.length} 款产品，其余 ${fetched.length} 款已加载。`);
        }
      } catch (err) {
        if (!cancelled) {
          console.error(err);
          setLoadError(err instanceof Error ? err.message : "对比数据加载失败");
          setProducts([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedIds, productIndexUrl, compareDataBaseUrl, categoryByFile]);

  // 扩展参数默认且始终展示，不再需要用户手动展开。
  const paramRows = useMemo(() => {
    const cats = [...new Set(products.map((p) => p.category))];
    return unifiedParamRows(profiles, cats, true);
  }, [products, profiles]);

  const extras = useProductExtras(products.map((p) => p.canonical_id));

  const removeProduct = (id: string) => {
    setSelectedIds((prev) => {
      const next = prev.filter((x) => x !== id);
      syncUrl(next);
      return next;
    });
  };

  const labelFor = (key: string): ParamLabel =>
    profiles.param_labels[key] || { label: key };

  const uniqueCategories = useMemo(
    () => [...new Set(products.map((p) => p.category))],
    [products]
  );
  const comparisonTitle = selectedIds.length === 0
    ? "选择产品对比方式"
    : uniqueCategories.length === 1
      ? `${uniqueCategories[0]}产品对比`
      : "跨品类产品对比";
  const comparisonDescription = selectedIds.length === 0
    ? "可以比较同一类型耳机，也可以跨形态观察技术路线差异；请先选择适合当前任务的入口。"
    : uniqueCategories.length === 1
      ? "当前产品来自同一耳机类型，适合核对参数、BOM 与卖点差异。"
      : "当前产品来自多个耳机类型；不适用字段会明确显示为「不适用」。";

  return (
    <div className="space-y-5">
      <div className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-[var(--shadow-glass)]">
        <h1 className="m-0 text-2xl font-bold">{comparisonTitle}</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          {comparisonDescription}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <a
            href={link("/")}
            className="rounded-full border border-[var(--line)] bg-[var(--surface)] px-3 py-1 text-sm text-[var(--primary)] no-underline hover:bg-[var(--surface-2)]"
          >
            ← 返回工作台{selectedIds.length ? "（保留已选产品）" : "选品"}
          </a>
        </div>

        {selectedIds.length > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-[var(--muted)]">已选 {products.length} 款</span>
            {uniqueCategories.map((cat) => {
              const color = categoryTagStyle(cat);
              const count = products.filter((p) => p.category === cat).length;
              return (
                <span
                  key={cat}
                  style={{ background: color.bg, color: color.text }}
                  className="rounded-full px-2.5 py-0.5 text-xs font-medium"
                >
                  {cat} ×{count}
                </span>
              );
            })}
            {loading && <span className="text-xs text-[var(--primary)]">加载中…</span>}
            {loadError && <span className="text-xs text-[var(--warn)]">{loadError}</span>}
          </div>
        )}

        {products.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {products.map((p) => {
              const color = categoryTagStyle(p.category);
              return (
                <button
                  key={p.canonical_id}
                  type="button"
                  onClick={() => removeProduct(p.canonical_id)}
                  className="inline-flex items-center gap-1.5 rounded-full bg-[var(--primary-soft)] px-3 py-1 text-sm text-[var(--primary-dark)] hover:bg-[var(--primary-soft-strong)]"
                >
                  <span
                    style={{ background: color.bg, color: color.text }}
                    className="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                  >
                    {p.category}
                  </span>
                  {productDisplayName(p)} ×
                </button>
              );
            })}
          </div>
        )}
      </div>

      {selectedIds.length === 0 ? (
        <section className="grid gap-4 lg:grid-cols-2" aria-label="产品对比方式">
          <article className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-6 shadow-[var(--shadow-glass)]">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--primary-soft)] font-bold text-[var(--primary-dark)]">A</span>
            <h2 className="mb-0 mt-4 text-xl font-bold">同类型耳机对比</h2>
            <p className="mt-2 text-sm leading-6 text-[var(--muted)]">适合比较相同产品形态下的规格、BOM、卖点和报告证据。选择一种耳机类型后，再挑选具体产品。</p>
            <div className="mt-4 flex flex-wrap gap-2">
              {categories.map((item) => <a key={item.name} href={link(`/category/${encodeURIComponent(item.name)}`)} className="rounded-full border border-[var(--line)] bg-[var(--surface-2)] px-3 py-1.5 text-sm text-[var(--primary-dark)] no-underline hover:border-[var(--primary)] hover:bg-[var(--primary-soft)]">{item.name} · {item.product_count} 款</a>)}
            </div>
          </article>
          <article className="rounded-2xl border border-[var(--primary)] bg-[linear-gradient(135deg,var(--primary-soft),var(--surface))] p-6 shadow-[var(--shadow-glass)]">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--primary)] font-bold text-white">B</span>
            <h2 className="mb-0 mt-4 text-xl font-bold">跨品类对比</h2>
            <p className="mt-2 text-sm leading-6 text-[var(--muted)]">适合把入耳、耳夹、耳挂、头戴或骨传导等不同形态放在同一张表中，观察结构和技术路线差异。</p>
            <ol className="mt-4 space-y-2 text-sm text-[var(--text)]"><li>1. 返回工作台</li><li>2. 在不同耳机类型中勾选产品</li><li>3. 点击“进行跨品类对比”</li></ol>
            <a href={link("/#product-browser")} className="mt-5 inline-flex rounded-full bg-[var(--primary)] px-5 py-2.5 text-sm font-semibold text-white no-underline shadow-md hover:bg-[var(--primary-strong)]">返回工作台跨类型选品 →</a>
          </article>
        </section>
      ) : products.length > 0 ? (
        <div className="overflow-x-auto rounded-2xl border border-[var(--line)] bg-[var(--surface)] shadow-[var(--shadow-glass)]">
          <table className="w-full min-w-[640px] border-collapse text-sm">
            <thead>
              <tr className="bg-[var(--surface-2)]">
                <th className="sticky left-0 z-10 min-w-[120px] border-b border-[var(--line)] bg-[var(--surface-2)] px-4 py-3 text-left font-semibold">
                  参数
                </th>
                {products.map((p) => {
                  const color = categoryTagStyle(p.category);
                  return (
                    <th
                      key={p.canonical_id}
                      className="min-w-[140px] border-b border-[var(--line)] px-4 py-3 text-left font-semibold"
                    >
                      <span
                        style={{ background: color.bg, color: color.text }}
                        className="mb-1 inline-block rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                      >
                        {p.category}
                      </span>
                      <a
                        href={link(`/product/${p.canonical_id}`)}
                        className="block text-[var(--primary-dark)] no-underline hover:underline"
                      >
                        {productDisplayName(p)}
                      </a>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              <tr className="bg-[var(--surface-2)]">
                <td className="sticky left-0 z-10 border-b border-[var(--line)] bg-[var(--surface-2)] px-4 py-3 align-top font-medium text-[var(--muted)]">
                  卖点标签
                </td>
                {products.map((p) => (
                  <td
                    key={p.canonical_id + "-selling-point-tags"}
                    className="min-w-[220px] border-b border-[var(--line)] px-4 py-3 align-top"
                  >
                    <SellingPointTagsCell state={extras[p.canonical_id]} />
                  </td>
                ))}
              </tr>
              <tr className="bg-[var(--surface-2)]">
                <td className="sticky left-0 z-10 border-b border-[var(--line)] bg-[var(--surface-2)] px-4 py-3 align-top font-medium text-[var(--muted)]">
                  适用场景
                </td>
                {products.map((p) => (
                  <td
                    key={p.canonical_id + "-scenarios"}
                    className="min-w-[220px] border-b border-[var(--line)] px-4 py-3 align-top"
                  >
                    <ScenariosCell state={extras[p.canonical_id]} />
                  </td>
                ))}
              </tr>
              <tr className="bg-[var(--surface-2)]">
                <td className="sticky left-0 z-10 border-b border-[var(--line)] bg-[var(--surface-2)] px-4 py-3 align-top font-medium text-[var(--muted)]">
                  核心卖点摘要
                </td>
                {products.map((p) => (
                  <td
                    key={p.canonical_id + "-selling-points"}
                    className="min-w-[220px] border-b border-[var(--line)] px-4 py-3 align-top"
                  >
                    <SellingPointsCell state={extras[p.canonical_id]} />
                  </td>
                ))}
              </tr>
              {paramRows.map((param) => {
                const meta = labelFor(param);
                return (
                  <tr key={param} className="hover:bg-[var(--surface-2)]">
                    <td
                      className="sticky left-0 z-10 border-b border-[var(--line)] bg-[var(--surface)] px-4 py-3 font-medium text-[var(--muted)]"
                      title={meta.why}
                    >
                      <span className="block">{meta.label}</span>
                      {param === "bom_rows" && products.length > 1 && (
                        <button
                          type="button"
                          onClick={() => setBomPanelOpen(true)}
                          className="mt-2 rounded-lg border border-[var(--primary)] bg-[var(--surface)] px-2 py-1 text-xs font-medium text-[var(--primary)] hover:bg-[var(--primary-soft)]"
                        >横向比较 BOM</button>
                      )}
                    </td>
                    {products.map((p) => {
                      const applies = paramAppliesToCategory(profiles, p.category, param);
                      if (!applies) {
                        return (
                          <td
                            key={p.canonical_id + param}
                            className="border-b border-[var(--line)] px-4 py-3 align-top text-[var(--muted)]"
                          >
                            <span className="text-xs">不适用</span>
                          </td>
                        );
                      }
                      const cell = p.cells[param] || {
                        value: "",
                        evidence: "",
                        source_layer: "",
                        source_url: "",
                      };
                      const missing = emptyHint(cell.value);
                      if (param === "bom_rows") {
                        return (
                          <td key={p.canonical_id + param} className="border-b border-[var(--line)] px-4 py-3 align-top">
                            <a
                              href={link(`/product/${p.canonical_id}#bom`)}
                              className="font-medium text-[var(--primary)] hover:underline"
                            >
                              {cell.value} 项 → 查看 BOM
                            </a>
                          </td>
                        );
                      }
                      return (
                        <td
                          key={p.canonical_id + param}
                          className="border-b border-[var(--line)] px-4 py-3 align-top"
                        >
                          {missing ? (
                            <span className="rounded-full bg-[var(--warn-soft)] px-2 py-0.5 text-xs text-[var(--warn)]">
                              待补充
                            </span>
                          ) : (param === "price_cny" || param === "launch_date") && cell.source_url ? (
                            <a
                              href={cell.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              title={param === "launch_date" ? "打开上市时间来源（新标签页）" : "打开价格信息来源（新标签页）"}
                              className="group inline-flex flex-wrap items-center gap-x-1 font-medium text-[var(--primary)] no-underline hover:underline"
                            >
                              <span>{cell.value}</span>
                              <span className="text-xs text-[var(--muted)] group-hover:text-[var(--primary)]">
                                查看来源 ↗
                              </span>
                            </a>
                          ) : (
                            <button
                              type="button"
                              onClick={() =>
                                setDrawer({ product: p, param, label: meta.label })
                              }
                              className="group text-left"
                            >
                              <span className="font-medium text-[var(--text)] group-hover:text-[var(--primary)]">
                                {cell.value}
                              </span>
                              {cell.source_layer && (
                                <span className="ml-1 text-xs text-[var(--muted)]">
                                  [{sourceLabel(cell.source_layer)}]
                                </span>
                              )}
                            </button>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : loading ? (
        <div className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-10 text-center text-[var(--muted)]">
          对比数据加载中…
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-[var(--line)] bg-[var(--surface)] p-10 text-center text-[var(--muted)]">
          {loadError || "未能加载所选产品的对比数据。"}
        </div>
      )}

      {drawer && (
        <div
          className="fixed inset-0 z-[100] flex justify-end bg-black/60"
          onClick={() => setDrawer(null)}
          role="presentation"
        >
          <div
            className="h-full w-full max-w-md overflow-y-auto bg-[var(--surface)] p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="mb-4 text-sm text-[var(--muted)]"
              onClick={() => setDrawer(null)}
            >
              ← 关闭
            </button>
            <h2 className="text-lg font-bold">{drawer.label}</h2>
            <p className="text-sm text-[var(--muted)]">
              {productDisplayName(drawer.product)} · {drawer.product.category}
            </p>
            <p className="mt-4 text-2xl font-semibold text-[var(--primary-dark)]">
              {drawer.product.cells[drawer.param]?.value || "—"}
            </p>
            <p className="mt-2 text-xs text-[var(--muted)]">
              来源：
              {sourceLabel(drawer.product.cells[drawer.param]?.source_layer || "technical")}
            </p>
            {drawer.product.cells[drawer.param]?.evidence && (
              <blockquote className="mt-4 rounded-xl bg-[var(--surface-2)] p-4 text-sm leading-relaxed text-[var(--muted)]">
                {drawer.product.cells[drawer.param].evidence}
              </blockquote>
            )}
            <div className="mt-6 flex flex-col gap-2 text-sm">
              <a href={link(`/product/${drawer.product.canonical_id}`)}>查看产品成本档案 →</a>
              {drawer.product.cells[drawer.param]?.source_url && (
                <a href={drawer.product.cells[drawer.param].source_url} target="_blank" rel="noopener noreferrer">查看来源原文 ↗</a>
              )}
            </div>
          </div>
        </div>
      )}
      <BomComparisonPanel
        open={bomPanelOpen}
        products={products.map((product) => ({
          id: product.canonical_id,
          name: productDisplayName(product),
          href: link(`/product/${product.canonical_id}`),
          category: product.category,
        }))}
        extras={extras}
        onClose={() => setBomPanelOpen(false)}
      />
    </div>
  );
}
