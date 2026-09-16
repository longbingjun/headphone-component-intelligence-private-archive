import { useEffect, useMemo, useRef, useState } from "react";
import type { IndexProduct } from "../lib/types";
import { productDisplayName } from "../lib/types";
import { withBase } from "../lib/paths";

const UNKNOWN_BRAND_KEY = "__unknown__";
const TOP_BRAND_VISIBLE = 14;
const WORKSPACE_STATE_KEY = "cost-workspace-state-v1";

interface CategorySummary {
  name: string;
  slug: string;
  product_count: number;
}

interface BrandSummary {
  name: string;
  count: number;
}

interface CategorySlice {
  category: string;
  products: IndexProduct[];
}

interface Props {
  categories: CategorySummary[];
  totalCount: number;
  brands: BrandSummary[];
  unknownBrandCount: number;
  initialSlices: CategorySlice[];
  fullIndexUrl: string;
}

interface WorkspaceState {
  search: string;
  category: string;
  year: string;
  brand: string;
  selectedIds: string[];
  showMoreBrands: boolean;
  scrollY: number;
}

interface ProductFilters {
  search: string;
  category: string;
  year: string;
  brand: string;
}

type FacetDimension = "category" | "year" | "brand";

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

function productMatchesFilters(
  product: IndexProduct,
  filters: ProductFilters,
  omit?: FacetDimension
) {
  const q = filters.search.trim().toLowerCase();
  if (q) {
    const haystack = `${product.brand || ""} ${product.model || ""} ${product.category || ""}`.toLowerCase();
    if (!haystack.includes(q) && !product.canonical_id.toLowerCase().includes(q)) return false;
  }
  if (omit !== "category" && filters.category && product.category !== filters.category) return false;
  if (omit !== "year" && filters.year && (product.first_seen || "").slice(0, 4) !== filters.year) return false;
  if (omit !== "brand") {
    const brand = (product.brand || "").trim();
    if (filters.brand === UNKNOWN_BRAND_KEY && brand) return false;
    if (filters.brand && filters.brand !== UNKNOWN_BRAND_KEY && brand !== filters.brand) return false;
  }
  return true;
}

function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-[var(--primary-soft)] px-3 py-1 text-sm text-[var(--primary-dark)]">
      {label}
      <button
        type="button"
        onClick={onRemove}
        aria-label="移除筛选"
        className="text-[var(--primary-dark)] hover:text-[var(--primary)]"
      >
        ×
      </button>
    </span>
  );
}

function ProductSelectionCard({
  product,
  isSelected,
  onToggle,
  className = "",
}: {
  product: IndexProduct;
  isSelected: boolean;
  onToggle: () => void;
  className?: string;
}) {
  const color = categoryTagStyle(product.category);
  const displayName = productDisplayName(product);

  return (
    <article
      className={`relative min-w-0 rounded-2xl border bg-[var(--surface)] p-3 shadow-[var(--shadow-glass)] transition hover:-translate-y-0.5 hover:shadow-md ${
        isSelected
          ? "border-[var(--primary)] ring-1 ring-[var(--primary)]"
          : "border-[var(--line)]"
      } ${className}`}
    >
      <label className="absolute right-3 top-3 z-10 cursor-pointer rounded-md bg-[var(--surface)]/90 p-1 shadow-sm">
        <input
          type="checkbox"
          checked={isSelected}
          onChange={onToggle}
          className="block h-4 w-4"
          aria-label={`选择 ${displayName}`}
        />
      </label>
      <a href={withBase(`/product/${product.canonical_id}`)} className="block no-underline">
        <div className="flex items-center justify-between gap-2 pr-8">
          <span
            style={{ background: color.bg, color: color.text }}
            className="truncate rounded-full px-2 py-0.5 text-xs font-medium"
          >
            {product.category}
          </span>
          <span className="shrink-0 text-xs text-[var(--muted)]">{product.first_seen || ""}</span>
        </div>
        <div className="mt-2 flex h-36 items-center justify-center overflow-hidden rounded-xl bg-[var(--surface-2)]">
          {product.card_image_path ? (
            <img
              src={withBase(product.card_image_path)}
              alt={displayName}
              loading="lazy"
              className="h-full w-full object-contain"
            />
          ) : (
            <span className="text-xs text-[var(--muted)]">图片待补</span>
          )}
        </div>
        <h4 className="mb-0 mt-2 line-clamp-2 min-h-10 text-sm font-semibold leading-5 text-[var(--text)]">
          {displayName}
        </h4>
        <p className="mb-0 mt-2 text-xs text-[var(--muted)]">
          {product.report_count ? `${product.report_count} 篇拆解报告` : "暂无拆解报告"}
        </p>
      </a>
    </article>
  );
}

export default function ProductBrowser({
  categories,
  totalCount,
  brands,
  unknownBrandCount,
  initialSlices,
  fullIndexUrl,
}: Props) {
  const [fullIndex, setFullIndex] = useState<IndexProduct[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [yearFilter, setYearFilter] = useState("");
  const [brandFilter, setBrandFilter] = useState("");
  const [showMoreBrands, setShowMoreBrands] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [workspaceStateReady, setWorkspaceStateReady] = useState(false);
  const restoreScrollY = useRef(0);

  // 首屏只内嵌了每个品类的一个小切片（用于默认状态的卡片行），完整的 1027 款
  // 产品轻量索引在挂载后才通过 fetch 拉取，用于搜索/筛选——绝不在构建期把全量
  // 索引内嵌进首页 HTML。
  useEffect(() => {
    let cancelled = false;
    fetch(fullIndexUrl)
      .then((res) => {
        if (!res.ok) throw new Error(`fetch product index failed: ${res.status}`);
        return res.json();
      })
      .then((data: { products: IndexProduct[] }) => {
        if (!cancelled) setFullIndex(data.products);
      })
      .catch((err) => {
        console.error("加载完整产品索引失败", err);
        if (!cancelled) setLoadFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [fullIndexUrl]);

  // 工作台是用户选择和比较产品的上级页面。使用 sessionStorage 保存本标签页中的
  // 搜索、筛选、勾选和滚动位置，确保进入产品档案或对比页后返回不会丢失上下文。
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(WORKSPACE_STATE_KEY);
      if (raw) {
        const stored = JSON.parse(raw) as Partial<WorkspaceState>;
        setSearch(typeof stored.search === "string" ? stored.search : "");
        setCategoryFilter(typeof stored.category === "string" ? stored.category : "");
        setYearFilter(typeof stored.year === "string" ? stored.year : "");
        setBrandFilter(typeof stored.brand === "string" ? stored.brand : "");
        setSelected(Array.isArray(stored.selectedIds) ? stored.selectedIds.filter((id): id is string => typeof id === "string") : []);
        setShowMoreBrands(Boolean(stored.showMoreBrands));
        restoreScrollY.current = Number.isFinite(stored.scrollY) ? Math.max(0, Number(stored.scrollY)) : 0;
      }
    } catch {
      /* 无效缓存不阻塞工作台 */
    } finally {
      setWorkspaceStateReady(true);
    }
  }, []);

  useEffect(() => {
    if (!workspaceStateReady) return;
    const save = () => {
      const state: WorkspaceState = {
        search,
        category: categoryFilter,
        year: yearFilter,
        brand: brandFilter,
        selectedIds: selected,
        showMoreBrands,
        scrollY: window.scrollY,
      };
      try { sessionStorage.setItem(WORKSPACE_STATE_KEY, JSON.stringify(state)); } catch { /* ignore */ }
    };
    save();
    window.addEventListener("pagehide", save);
    return () => window.removeEventListener("pagehide", save);
  }, [workspaceStateReady, search, categoryFilter, yearFilter, brandFilter, selected, showMoreBrands]);

  useEffect(() => {
    if (!workspaceStateReady || !fullIndex || restoreScrollY.current <= 0) return;
    const target = restoreScrollY.current;
    restoreScrollY.current = 0;
    const timer = window.setTimeout(() => window.scrollTo({ top: target, behavior: "auto" }), 60);
    return () => window.clearTimeout(timer);
  }, [workspaceStateReady, fullIndex]);

  const isFiltering = Boolean(search.trim() || categoryFilter || yearFilter || brandFilter);

  const filters = useMemo<ProductFilters>(() => ({
    search,
    category: categoryFilter,
    year: yearFilter,
    brand: brandFilter,
  }), [search, categoryFilter, yearFilter, brandFilter]);

  const categoryFacets = useMemo(() => {
    if (!fullIndex) return categories.map((item) => ({ name: item.name, count: item.product_count }));
    const counts = new Map(categories.map((item) => [item.name, 0]));
    fullIndex.filter((product) => productMatchesFilters(product, filters, "category")).forEach((product) => counts.set(product.category, (counts.get(product.category) || 0) + 1));
    return categories.map((item) => ({ name: item.name, count: counts.get(item.name) || 0 }));
  }, [fullIndex, categories, filters]);

  const yearFacets = useMemo(() => {
    const pool = fullIndex || initialSlices.flatMap((slice) => slice.products);
    const counts = new Map<string, number>();
    pool.filter((product) => productMatchesFilters(product, filters, "year")).forEach((product) => {
      const value = (product.first_seen || "").slice(0, 4);
      if (/^\d{4}$/.test(value)) counts.set(value, (counts.get(value) || 0) + 1);
    });
    return [...counts.entries()].map(([value, count]) => ({ value, count })).sort((a, b) => b.value.localeCompare(a.value));
  }, [fullIndex, initialSlices, filters]);

  const brandFacet = useMemo(() => {
    if (!fullIndex) return { brands, unknown: unknownBrandCount };
    const counts = new Map<string, number>();
    let unknown = 0;
    fullIndex.filter((product) => productMatchesFilters(product, filters, "brand")).forEach((product) => {
      const brand = (product.brand || "").trim();
      if (brand) counts.set(brand, (counts.get(brand) || 0) + 1);
      else unknown += 1;
    });
    const items = [...counts.entries()].map(([name, count]) => ({ name, count }));
    if (brandFilter && brandFilter !== UNKNOWN_BRAND_KEY && !counts.has(brandFilter)) items.push({ name: brandFilter, count: 0 });
    items.sort((a, b) => Number(b.name === brandFilter) - Number(a.name === brandFilter) || b.count - a.count || a.name.localeCompare(b.name, "zh-CN"));
    return { brands: items, unknown };
  }, [fullIndex, brands, unknownBrandCount, filters, brandFilter]);

  const visibleBrands = showMoreBrands ? brandFacet.brands : brandFacet.brands.slice(0, TOP_BRAND_VISIBLE);
  const allCategoryCount = fullIndex ? fullIndex.filter((product) => productMatchesFilters(product, filters, "category")).length : totalCount;

  const filteredPool = useMemo(() => {
    if (!fullIndex) return [];
    return fullIndex.filter((product) => productMatchesFilters(product, filters));
  }, [fullIndex, filters]);

  // 已选产品可能来自默认卡片行（只有切片数据）或已加载的完整索引，两边都要能查到，
  // 这样在完整索引加载完成前从卡片行产生的选择也不会丢失展示信息。
  const productById = useMemo(() => {
    const map = new Map<string, IndexProduct>();
    initialSlices.forEach((slice) => slice.products.forEach((p) => map.set(p.canonical_id, p)));
    (fullIndex || []).forEach((p) => map.set(p.canonical_id, p));
    return map;
  }, [initialSlices, fullIndex]);

  const selectedProducts = useMemo(
    () => selected.map((id) => productById.get(id)).filter((p): p is IndexProduct => Boolean(p)),
    [selected, productById]
  );

  const selectedCategories = [...new Set(selectedProducts.map((product) => product.category).filter(Boolean))];
  const isSameCategoryComparison = selected.length > 0 && selectedProducts.length === selected.length && selectedCategories.length === 1;

  const compareHref =
    selected.length > 0
      ? isSameCategoryComparison
        ? withBase(`/category/${encodeURIComponent(selectedCategories[0])}?ids=${selected.join(",")}`)
        : withBase(`/compare?ids=${selected.join(",")}`)
      : withBase("/compare");

  const toggleProduct = (id: string) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const clearSelection = () => setSelected([]);

  const selectAllFiltered = () => {
    setSelected(filteredPool.map((p) => p.canonical_id));
  };

  const toggleBrandFilter = (name: string) => {
    setBrandFilter((prev) => (prev === name ? "" : name));
  };

  return (
    <div className="space-y-5">
      {/* 搜索栏 */}
      <form
        onSubmit={(e) => e.preventDefault()}
        className="flex flex-wrap gap-3 rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-4 shadow-[var(--shadow-glass)]"
      >
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="输入品牌/型号关键词…"
          className="min-w-[220px] flex-1 rounded-xl border border-[var(--line)] px-3 py-2 text-sm outline-none focus:border-[var(--primary)]"
        />
        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="rounded-xl border border-[var(--line)] px-3 py-2 text-sm"
        >
          <option value="">类型: 不限</option>
          {categoryFacets.map((c) => (
            <option key={c.name} value={c.name} disabled={c.count === 0 && categoryFilter !== c.name}>
              {c.name} ({c.count})
            </option>
          ))}
        </select>
        <select
          value={yearFilter}
          onChange={(e) => setYearFilter(e.target.value)}
          aria-label="首次收录年份"
          className="rounded-xl border border-[var(--line)] px-3 py-2 text-sm"
        >
          <option value="">年份: 不限</option>
          {yearFacets.map((item) => (
            <option key={item.value} value={item.value}>{item.value} 年 ({item.count})</option>
          ))}
        </select>
        <button
          type="submit"
          className="rounded-xl bg-[var(--primary)] px-4 py-2 text-sm font-medium text-white hover:bg-[var(--primary-strong)]"
        >
          搜索
        </button>
      </form>

      <div className="flex flex-col gap-5 lg:flex-row">
        {/* 侧边栏 */}
        <aside className="shrink-0 lg:w-60">
          <div className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-4 shadow-[var(--shadow-glass)]">
            <button
              type="button"
              onClick={() => setCategoryFilter("")}
              className={`block w-full rounded-lg px-2.5 py-1.5 text-left text-sm font-semibold ${
                categoryFilter === ""
                  ? "bg-[var(--primary-soft)] text-[var(--primary-dark)]"
                  : "text-[var(--text)] hover:bg-[var(--surface-2)]"
              }`}
            >
              全部产品 ({allCategoryCount})
            </button>
            <div className="mt-1 flex flex-col gap-1">
              {categoryFacets.map((c) => (
                <button
                  key={c.name}
                  type="button"
                  disabled={c.count === 0 && categoryFilter !== c.name}
                  onClick={() => setCategoryFilter((prev) => (prev === c.name ? "" : c.name))}
                  className={`block w-full rounded-lg px-2.5 py-1.5 text-left text-sm ${
                    categoryFilter === c.name
                      ? "bg-[var(--primary-soft)] font-semibold text-[var(--primary-dark)]"
                      : "text-[var(--muted)] hover:bg-[var(--surface-2)] disabled:cursor-not-allowed disabled:opacity-40"
                  }`}
                >
                  {c.name} ({c.count})
                </button>
              ))}
            </div>

            <div className="mt-5 border-t border-[var(--line)] pt-4">
              <h3 className="m-0 text-sm font-semibold text-[var(--muted)]">品牌</h3>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {visibleBrands.map((b) => (
                  <button
                    key={b.name}
                    type="button"
                    onClick={() => toggleBrandFilter(b.name)}
                    className={`rounded-full border px-2.5 py-1 text-xs ${
                      brandFilter === b.name
                        ? "border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary-dark)] font-semibold"
                        : "border-[var(--line)] bg-[var(--surface)] text-[var(--muted)] hover:bg-[var(--surface-2)]"
                    }`}
                  >
                    {b.name} ({b.count})
                  </button>
                ))}
              </div>
              {brandFacet.brands.length > TOP_BRAND_VISIBLE && (
                <button
                  type="button"
                  onClick={() => setShowMoreBrands((current) => !current)}
                  className="mt-2 text-xs text-[var(--primary)] underline"
                >
                  {showMoreBrands ? "收起品牌 ▲" : `更多品牌（${Math.max(0, brandFacet.brands.length - TOP_BRAND_VISIBLE)}）▾`}
                </button>
              )}
              <button
                type="button"
                onClick={() => toggleBrandFilter(UNKNOWN_BRAND_KEY)}
                className={`mt-3 block w-fit rounded-full border px-2.5 py-1 text-xs ${
                  brandFilter === UNKNOWN_BRAND_KEY
                    ? "border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary-dark)] font-semibold"
                    : "border-dashed border-[var(--line)] bg-[var(--surface)] text-[var(--muted)] hover:bg-[var(--surface-2)]"
                }`}
              >
                未知品牌 ({brandFacet.unknown})
              </button>
            </div>
          </div>
        </aside>

        {/* 主内容区 */}
        <div className="min-w-0 flex-1 space-y-5">
          {!isFiltering ? (
            <div className="flex flex-col gap-6">
              {initialSlices.map((slice) => {
                const color = categoryTagStyle(slice.category);
                const catInfo = categories.find((c) => c.name === slice.category);
                return (
                  <section
                    key={slice.category}
                    className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-[var(--shadow-glass)]"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <h3 className="m-0 flex items-center gap-2 text-lg font-bold">
                        <span
                          style={{ background: color.bg, color: color.text }}
                          className="rounded-full px-2.5 py-0.5 text-xs font-semibold"
                        >
                          {slice.category}
                        </span>
                        <span className="text-[var(--text)]">
                          共 {catInfo?.product_count ?? slice.products.length} 款
                        </span>
                      </h3>
                      <a
                        href={withBase(`/category/${encodeURIComponent(slice.category)}`)}
                        className="text-sm text-[var(--primary)] no-underline hover:underline"
                      >
                        查看全部对比 →
                      </a>
                    </div>
                    <div className="mt-4 flex gap-3 overflow-x-auto pb-1">
                      {slice.products.map((p) => (
                        <ProductSelectionCard
                          key={p.canonical_id}
                          product={p}
                          isSelected={selected.includes(p.canonical_id)}
                          onToggle={() => toggleProduct(p.canonical_id)}
                          className="w-56 flex-shrink-0"
                        />
                      ))}
                      {slice.products.length === 0 && (
                        <p className="text-sm text-[var(--muted)]">该品类暂无产品数据</p>
                      )}
                    </div>
                  </section>
                );
              })}
            </div>
          ) : (
            <div className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-[var(--shadow-glass)]">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  {search.trim() && (
                    <FilterChip label={`搜索: ${search.trim()}`} onRemove={() => setSearch("")} />
                  )}
                  {categoryFilter && (
                    <FilterChip
                      label={`类型: ${categoryFilter}`}
                      onRemove={() => setCategoryFilter("")}
                    />
                  )}
                  {yearFilter && (
                    <FilterChip
                      label={`首次收录年份: ${yearFilter}`}
                      onRemove={() => setYearFilter("")}
                    />
                  )}
                  {brandFilter && (
                    <FilterChip
                      label={`品牌: ${brandFilter === UNKNOWN_BRAND_KEY ? "未知品牌" : brandFilter}`}
                      onRemove={() => setBrandFilter("")}
                    />
                  )}
                </div>
                <span className="text-sm text-[var(--muted)]">
                  {fullIndex ? (
                    `共匹配 ${filteredPool.length} 款`
                  ) : loadFailed ? (
                    <span className="text-[var(--warn)]">产品索引加载失败，请刷新重试</span>
                  ) : (
                    "产品索引加载中…"
                  )}
                </span>
              </div>

              <div className="mt-3 flex items-center gap-4 text-sm">
                <button
                  type="button"
                  onClick={selectAllFiltered}
                  disabled={!fullIndex}
                  className="text-[var(--primary)] underline disabled:cursor-not-allowed disabled:text-[var(--muted)] disabled:no-underline"
                >
                  全选当前结果
                </button>
                <button type="button" onClick={clearSelection} className="text-[var(--muted)] underline">
                  清空
                </button>
              </div>

              <div className="mt-4 grid max-h-[720px] grid-cols-1 gap-3 overflow-y-auto pr-1 sm:grid-cols-2 xl:grid-cols-3">
                {!fullIndex && !loadFailed && (
                  <p className="col-span-full text-sm text-[var(--muted)]">产品索引加载中…</p>
                )}
                {fullIndex && filteredPool.length === 0 && (
                  <p className="col-span-full text-sm text-[var(--muted)]">
                    没有找到匹配的产品，试试更换关键词或筛选条件。
                  </p>
                )}
                {filteredPool.map((p) => (
                  <ProductSelectionCard
                    key={p.canonical_id}
                    product={p}
                    isSelected={selected.includes(p.canonical_id)}
                    onToggle={() => toggleProduct(p.canonical_id)}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 底部选中悬浮条 */}
      {selected.length > 0 && (
        <>
          <div className="h-20" aria-hidden="true" />
          <div className="fixed inset-x-0 bottom-0 z-40 border-t border-[var(--line)] bg-[var(--surface-solid)]/95 shadow-[0_-8px_28px_rgba(0,0,0,0.45)] backdrop-blur-md">
            <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-3 px-5 py-3">
              <span className="text-sm font-semibold text-[var(--text)]">已选 {selected.length} 款</span>
              <div className="flex max-h-16 max-w-[45%] flex-wrap gap-1.5 overflow-y-auto">
                {selectedProducts.map((p) => (
                  <button
                    key={p.canonical_id}
                    type="button"
                    onClick={() => toggleProduct(p.canonical_id)}
                    className="rounded-full bg-[var(--primary-soft)] px-2.5 py-1 text-xs text-[var(--primary-dark)] hover:bg-[var(--primary-soft-strong)]"
                  >
                    {productDisplayName(p)} ×
                  </button>
                ))}
              </div>
              <button
                type="button"
                onClick={clearSelection}
                className="text-sm text-[var(--muted)] underline"
              >
                清空
              </button>
              <div className="ml-auto flex flex-wrap gap-2">
                <a
                  href={compareHref}
                  className="rounded-full bg-[var(--primary)] px-4 py-2 text-sm font-medium text-white no-underline hover:bg-[var(--primary-strong)]"
                >
                  {isSameCategoryComparison ? `进行同类对比 ${selected.length} 款` : `进行跨品类对比 ${selected.length} 款`} →
                </a>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
