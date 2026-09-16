import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  CompareData,
  CompareProduct,
  CompareProfiles,
  ParamLabel,
} from "../lib/types";
import {
  emptyHint,
  productDisplayName,
  profileForCategory,
  sourceLabel,
} from "../lib/types";
import { withBase } from "../lib/paths";
import { useProductExtras } from "../lib/productExtras";
import { BomHighlightsCell, SellingPointsCell } from "./CompareExtrasCells";
import BomComparisonPanel from "./BomComparisonPanel";

const STORAGE_KEY = "cost-compare-selection-v5";

interface Props {
  category: string;
  /**
   * 首屏数据：可能只是完整品类数据的一个切片（用于避免把整个品类的对比数据
   * 序列化进页面 HTML）。当 totalCount 大于此切片长度时，组件会在挂载后
   * 通过 fetch 从 compareDataUrl 拉取完整数据并替换。
   */
  compareData: CompareData;
  /** 完整品类对比数据的静态 JSON 地址（例如 /data/compare/xxx.json） */
  compareDataUrl?: string;
  /** 该品类的完整产品数，用于判断 compareData 是否已经是全量、以及首屏文案展示 */
  totalCount?: number;
  profiles: CompareProfiles;
  allCategories: { name: string; slug: string }[];
}

function loadStored(category: string): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const map = JSON.parse(raw) as Record<string, string[]>;
    return map[category] || [];
  } catch {
    return [];
  }
}

function saveStored(category: string, ids: string[]) {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const map = raw ? (JSON.parse(raw) as Record<string, string[]>) : {};
    map[category] = ids;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    /* ignore */
  }
}

function defaultSelection(products: CompareProduct[]): string[] {
  const scored = [...products].sort((a, b) => {
    const aFilled = Object.values(a.cells).filter((c) => !emptyHint(c.value)).length;
    const bFilled = Object.values(b.cells).filter((c) => !emptyHint(c.value)).length;
    return bFilled - aFilled;
  });
  return scored.slice(0, 4).map((p) => p.canonical_id);
}

export default function CompareWorkbench({
  category,
  compareData: initialCompareData,
  compareDataUrl,
  totalCount,
  profiles,
  allCategories,
}: Props) {
  const link = (path: string) => withBase(path);
  const { p0, p1 } = profileForCategory(profiles, category);
  const [search, setSearch] = useState("");
  const [brandFilter, setBrandFilter] = useState<string>("");
  const [onlyWithReport, setOnlyWithReport] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [compareData, setCompareData] = useState<CompareData>(initialCompareData);
  const [fullDataLoading, setFullDataLoading] = useState(
    Boolean(compareDataUrl) && initialCompareData.products.length < (totalCount ?? 0)
  );
  const [drawer, setDrawer] = useState<{
    product: CompareProduct;
    param: string;
    label: string;
  } | null>(null);
  const [bomPanelOpen, setBomPanelOpen] = useState(false);

  // 首屏只内嵌了该品类对比数据的一个切片（避免把上百款产品的完整数据序列化进页面 HTML），
  // 挂载后再通过 fetch 拉取该品类的完整静态 JSON，加载完成后无缝替换成全量数据。
  useEffect(() => {
    if (!compareDataUrl) return;
    if (initialCompareData.products.length >= (totalCount ?? 0)) return;
    let cancelled = false;
    fetch(compareDataUrl)
      .then((res) => {
        if (!res.ok) throw new Error(`fetch compare data failed: ${res.status}`);
        return res.json();
      })
      .then((full: CompareData) => {
        if (!cancelled) {
          setCompareData(full);
          setFullDataLoading(false);
        }
      })
      .catch((err) => {
        console.error("加载完整产品列表失败", err);
        if (!cancelled) setFullDataLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compareDataUrl]);

  // 扩展参数默认且始终展示，不再需要用户手动展开。
  const paramRows = useMemo(() => [...p0, ...p1], [p0, p1]);

  const brands = useMemo(() => {
    const set = new Set<string>();
    compareData.products.forEach((p) => {
      const b = (p.brand || "").trim() || "未知品牌";
      set.add(b);
    });
    return Array.from(set).sort((a, b) => a.localeCompare(b, "zh-CN"));
  }, [compareData.products]);

  const filteredPool = useMemo(() => {
    return compareData.products.filter((p) => {
      const name = productDisplayName(p).toLowerCase();
      const q = search.trim().toLowerCase();
      if (q && !name.includes(q) && !p.canonical_id.includes(q)) return false;
      if (brandFilter && ((p.brand || "").trim() || "未知品牌") !== brandFilter) return false;
      if (onlyWithReport && !p.best_report_id) return false;
      return true;
    });
  }, [compareData.products, search, brandFilter, onlyWithReport]);

  const selectedProducts = useMemo(
    () => compareData.products.filter((p) => selected.includes(p.canonical_id)),
    [compareData.products, selected]
  );

  const extras = useProductExtras(selectedProducts.map((p) => p.canonical_id));

  // 初始化选择：只在挂载时基于首屏切片计算一次，避免完整数据加载完成后
  // 默认选中的产品发生跳变。已选中的产品 id 是否存在于当前 compareData
  // 由下方 selectedProducts 的 filter 负责，不在这里做存在性校验。
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get("ids");
    let ids: string[] = [];
    if (fromUrl) {
      ids = fromUrl.split(",").filter(Boolean);
    }
    if (!ids.length) ids = loadStored(category);
    if (!ids.length) ids = defaultSelection(initialCompareData.products);
    setSelected(ids);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category]);

  const syncUrl = useCallback(
    (ids: string[]) => {
      const url = new URL(window.location.href);
      if (ids.length) url.searchParams.set("ids", ids.join(","));
      else url.searchParams.delete("ids");
      window.history.replaceState({}, "", url.toString());
      saveStored(category, ids);
    },
    [category]
  );

  const toggleProduct = (id: string) => {
    setSelected((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
      syncUrl(next);
      return next;
    });
  };

  const clearSelection = () => {
    setSelected([]);
    syncUrl([]);
  };

  const selectAllFiltered = () => {
    const ids = filteredPool.map((p) => p.canonical_id);
    setSelected(ids);
    syncUrl(ids);
  };

  const labelFor = (key: string): ParamLabel =>
    profiles.param_labels[key] || { label: key };

  return (
    <div className="space-y-5">
      {/* 品类切换 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-[var(--muted)]">品类</span>
        {allCategories.map((c) => (
          <a
            key={c.slug}
            href={link(`/category/${encodeURIComponent(c.name)}`)}
            className={`rounded-full border px-3 py-1 text-sm no-underline hover:no-underline ${
              c.name === category
                ? "border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary-dark)] font-semibold"
                : "border-[var(--line)] bg-[var(--surface)] text-[var(--muted)]"
            }`}
          >
            {c.name}
          </a>
        ))}
      </div>

      <div className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-[var(--shadow-glass)]">
        <h1 className="m-0 text-2xl font-bold">{category}</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          共 {totalCount ?? compareData.products.length} 款产品 · 已选 {selectedProducts.length} 款参与对比
          {fullDataLoading && (
            <span className="ml-2 text-xs text-[var(--primary)]">完整产品列表加载中…</span>
          )}
        </p>

        {/* 筛选栏 */}
        <div className="mt-4 flex flex-wrap gap-3">
          <input
            type="search"
            placeholder="搜索品牌 / 型号…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="min-w-[200px] flex-1 rounded-xl border border-[var(--line)] px-3 py-2 text-sm outline-none focus:border-[var(--primary)]"
          />
          <select
            value={brandFilter}
            onChange={(e) => setBrandFilter(e.target.value)}
            className="rounded-xl border border-[var(--line)] px-3 py-2 text-sm"
          >
            <option value="">全部品牌</option>
            {brands.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-2 text-sm text-[var(--muted)]">
            <input
              type="checkbox"
              checked={onlyWithReport}
              onChange={(e) => setOnlyWithReport(e.target.checked)}
            />
            仅有拆解报告
          </label>
        </div>

        {/* 已选 chips */}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-[var(--muted)]">已选对比</span>
          {selectedProducts.length === 0 && (
            <span className="text-sm text-[var(--warn)]">请在下方的列表中勾选产品</span>
          )}
          {selectedProducts.map((p) => (
            <button
              key={p.canonical_id}
              type="button"
              onClick={() => toggleProduct(p.canonical_id)}
              className="rounded-full bg-[var(--primary-soft)] px-3 py-1 text-sm text-[var(--primary-dark)] hover:bg-[var(--primary-soft-strong)]"
            >
              {productDisplayName(p)} ×
            </button>
          ))}
          <button
            type="button"
            onClick={clearSelection}
            className="text-sm text-[var(--muted)] underline"
          >
            清空
          </button>
          <button
            type="button"
            onClick={selectAllFiltered}
            className="text-sm text-[var(--primary)] underline"
          >
            全选当前筛选结果
          </button>
        </div>

        {/* 产品勾选列表 */}
        <details className="mt-4 rounded-xl border border-[var(--line)] bg-[var(--surface-2)] p-3" open>
          <summary className="cursor-pointer text-sm font-semibold text-[var(--text)]">
            选择要对比的产品（{filteredPool.length} 款可选）
          </summary>
          <div className="mt-3 max-h-48 overflow-y-auto grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {filteredPool.map((p) => (
              <label
                key={p.canonical_id}
                className="flex cursor-pointer items-start gap-2 rounded-lg border border-transparent bg-[var(--surface)] px-2 py-1.5 text-sm hover:border-[var(--line)]"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(p.canonical_id)}
                  onChange={() => toggleProduct(p.canonical_id)}
                  className="mt-0.5"
                />
                <span>
                  <span className="font-medium">{productDisplayName(p)}</span>
                  {!p.best_report_id && (
                    <span className="ml-1 text-xs text-[var(--warn)]">无报告</span>
                  )}
                </span>
              </label>
            ))}
          </div>
        </details>
      </div>

      {/* 对比表 */}
      {selectedProducts.length > 0 ? (
        <div className="overflow-x-auto rounded-2xl border border-[var(--line)] bg-[var(--surface)] shadow-[var(--shadow-glass)]">
          <table className="w-full min-w-[640px] border-collapse text-sm">
            <thead>
              <tr className="bg-[var(--surface-2)]">
                <th className="sticky left-0 z-10 min-w-[120px] border-b border-[var(--line)] bg-[var(--surface-2)] px-4 py-3 text-left font-semibold">
                  参数
                </th>
                {selectedProducts.map((p) => (
                  <th
                    key={p.canonical_id}
                    className="min-w-[140px] border-b border-[var(--line)] px-4 py-3 text-left font-semibold"
                  >
                    <a
                      href={link(`/product/${p.canonical_id}`)}
                      className="text-[var(--primary-dark)] no-underline hover:underline"
                    >
                      {productDisplayName(p)}
                    </a>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr className="bg-[var(--surface-2)]">
                <td className="sticky left-0 z-10 border-b border-[var(--line)] bg-[var(--surface-2)] px-4 py-3 align-top font-medium text-[var(--muted)]">
                  核心卖点
                </td>
                {selectedProducts.map((p) => (
                  <td
                    key={p.canonical_id + "-selling-points"}
                    className="min-w-[220px] border-b border-[var(--line)] px-4 py-3 align-top"
                  >
                    <SellingPointsCell state={extras[p.canonical_id]} />
                  </td>
                ))}
              </tr>
              <tr className="bg-[var(--surface-2)]">
                <td className="sticky left-0 z-10 border-b border-[var(--line)] bg-[var(--surface-2)] px-4 py-3 align-top font-medium text-[var(--muted)]">
                  <span className="block">物料清单</span>
                  {selectedProducts.length > 1 && (
                    <button
                      type="button"
                      onClick={() => setBomPanelOpen(true)}
                      className="mt-2 rounded-lg border border-[var(--primary)] bg-[var(--surface)] px-2 py-1 text-xs font-medium text-[var(--primary)] hover:bg-[var(--primary-soft)]"
                    >
                      横向比较 BOM
                    </button>
                  )}
                </td>
                {selectedProducts.map((p) => (
                  <td
                    key={p.canonical_id + "-bom"}
                    className="min-w-[220px] border-b border-[var(--line)] px-4 py-3 align-top"
                  >
                    <BomHighlightsCell
                      state={extras[p.canonical_id]}
                      productHref={link(`/product/${p.canonical_id}`)}
                    />
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
                      {meta.label}
                    </td>
                    {selectedProducts.map((p) => {
                      const cell = p.cells[param] || {
                        value: "",
                        evidence: "",
                        source_layer: "",
                        source_url: "",
                      };
                      const missing = emptyHint(cell.value);
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
      ) : (
        <div className="rounded-2xl border border-dashed border-[var(--line)] bg-[var(--surface)] p-10 text-center text-[var(--muted)]">
          请在上方勾选至少一款产品，对比表将只显示您选择的产品列。
        </div>
      )}

      {/* 证据抽屉 */}
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
            <p className="text-sm text-[var(--muted)]">{productDisplayName(drawer.product)}</p>
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
        products={selectedProducts.map((product) => ({
          id: product.canonical_id,
          name: productDisplayName(product),
          href: link(`/product/${product.canonical_id}`),
          category,
        }))}
        extras={extras}
        onClose={() => setBomPanelOpen(false)}
      />
    </div>
  );
}
