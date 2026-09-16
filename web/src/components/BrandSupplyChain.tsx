import { useEffect, useMemo, useRef, useState } from "react";

type BrandItem = { brand: string; key: string; products: number; components: number; rows: number };
type Manifest = { brands: BrandItem[]; scope_notes?: string[] };
type ProductIndexItem = { canonical_id: string; brand?: string; category?: string; model?: string };
type ProductIndex = { products?: ProductIndexItem[] };
type BrandBrowseItem = BrandItem & {
  analyzable: boolean;
  archiveProducts: number;
  categories: string[];
};
type EvidenceImage = { public_path?: string; url?: string; caption?: string; alt?: string };
type ImagePreview = { src: string; label: string; product: string; component: string };
type Parameter = { label?: string; value?: string };
type SupplyRow = {
  product_id: string;
  product_brand: string;
  product_model: string;
  product_category: string;
  component_key: string;
  component_label: string;
  component_name?: string;
  component_manufacturer?: string;
  component_manufacturer_canonical?: string;
  component_model?: string;
  usage_location_label?: string;
  source_published_at?: string;
  source_type?: "report" | "video";
  source_report_url?: string;
  evidence_quote?: string;
  evidence_image?: EvidenceImage;
  parameters?: Parameter[];
};
type Payload = {
  brand: string;
  grain: string;
  summary: { rows: number; products: number; components: number; suppliers: number; manufacturer_coverage: number };
  scope_notes: string[];
  rows: SupplyRow[];
};
type Selection =
  | { kind: "component"; componentKey: string }
  | { kind: "supplier"; supplier: string }
  | { kind: "product"; productId: string }
  | { kind: "cell"; componentKey: string; productId: string; supplier: string };

const UNKNOWN = "报告未披露";
const COMPONENT_ORDER = [
  "bluetooth_audio_soc", "battery", "speaker_driver", "microphone",
  "power_management_ic", "battery_protection_ic", "connector", "pcb",
  "enclosure", "structural_support",
];

const supplierOf = (row: SupplyRow) =>
  (row.component_manufacturer_canonical || row.component_manufacturer || "").trim() || UNKNOWN;
const yearOf = (row: SupplyRow) => (row.source_published_at || "").slice(0, 4);
const pct = (value: number) => `${Math.round(value * 100)}%`;
const uniq = (values: string[]) => [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
const supplierClass = (value: string) => {
  let hash = 0;
  for (const char of value) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return `brand-supply__supplier--${hash % 8}`;
};
const rowSignature = (row: SupplyRow) => [
  row.product_id, row.component_key, row.usage_location_label, supplierOf(row),
  row.component_model, row.source_report_url, row.evidence_quote,
].join("\u0000");

export default function BrandSupplyChain({
  manifestUrl,
  productsIndexUrl,
  dataBase,
  productBase,
  teardownBase,
}: {
  manifestUrl: string;
  productsIndexUrl: string;
  dataBase: string;
  productBase: string;
  teardownBase: string;
}) {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [productIndex, setProductIndex] = useState<ProductIndex | null>(null);
  const [brandKey, setBrandKey] = useState("");
  const [pendingBrandKey, setPendingBrandKey] = useState("");
  const [pendingBrandName, setPendingBrandName] = useState("");
  const [brandQuery, setBrandQuery] = useState("");
  const [brandPickerOpen, setBrandPickerOpen] = useState(false);
  const [brandDirectoryOpen, setBrandDirectoryOpen] = useState(false);
  const [brandBrowseQuery, setBrandBrowseQuery] = useState("");
  const [brandBrowseCategory, setBrandBrowseCategory] = useState("");
  const [brandBrowseInitial, setBrandBrowseInitial] = useState("");
  const [brandBrowseStatus, setBrandBrowseStatus] = useState<"all" | "analyzable" | "unavailable">("all");
  const [brandBrowseSort, setBrandBrowseSort] = useState<"products" | "name">("products");
  const [payload, setPayload] = useState<Payload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [componentFilter, setComponentFilter] = useState("");
  const [category, setCategory] = useState("");
  const [location, setLocation] = useState("");
  const [year, setYear] = useState("");
  const [search, setSearch] = useState("");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [imagePreview, setImagePreview] = useState<ImagePreview | null>(null);
  const [showMissingMatrix, setShowMissingMatrix] = useState(false);
  const [evidencePage, setEvidencePage] = useState(1);
  const [scrollToFingerprint, setScrollToFingerprint] = useState(false);
  const fingerprintRef = useRef<HTMLElement | null>(null);
  const drilldownRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!imagePreview) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setImagePreview(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [imagePreview]);

  useEffect(() => {
    if (!brandDirectoryOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setBrandDirectoryOpen(false);
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [brandDirectoryOpen]);

  useEffect(() => {
    if (!selection) return;
    const timer = window.setTimeout(() => {
      drilldownRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [selection]);

  useEffect(() => {
    if (!scrollToFingerprint || loading || !payload) return;
    const timer = window.setTimeout(() => {
      fingerprintRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      setScrollToFingerprint(false);
    }, 100);
    return () => window.clearTimeout(timer);
  }, [scrollToFingerprint, loading, payload]);

  useEffect(() => {
    Promise.all([
      fetch(manifestUrl, { cache: "no-store" }).then((response) => {
        if (!response.ok) throw new Error(`品牌供应链清单加载失败（${response.status}）`);
        return response.json() as Promise<Manifest>;
      }),
      fetch(productsIndexUrl, { cache: "no-store" }).then((response) => {
        if (!response.ok) throw new Error(`全站品牌清单加载失败（${response.status}）`);
        return response.json() as Promise<ProductIndex>;
      }),
    ])
      .then(([manifestValue, productIndexValue]) => {
        setManifest(manifestValue);
        setProductIndex(productIndexValue);
      })
      .catch((reason) => setError(String(reason)))
      .finally(() => setLoading(false));
  }, [manifestUrl, productsIndexUrl]);

  useEffect(() => {
    if (!brandKey) {
      setPayload(null);
      return;
    }
    setLoading(true);
    setError("");
    fetch(`${dataBase}${brandKey}.json`, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`品牌供应链数据加载失败（${response.status}）`);
        return response.json();
      })
      .then((value: Payload) => setPayload(value))
      .catch((reason) => setError(String(reason)))
      .finally(() => setLoading(false));
  }, [brandKey, dataBase]);

  const sourceRows = payload?.rows || [];
  const componentOptions = useMemo(() => {
    const labels = new Map<string, string>();
    sourceRows.forEach((row) => labels.set(row.component_key, row.component_label));
    return [...labels.entries()].sort((a, b) => a[1].localeCompare(b[1], "zh-CN"));
  }, [sourceRows]);
  const categories = useMemo(() => uniq(sourceRows.map((row) => row.product_category)), [sourceRows]);
  const locations = useMemo(() => uniq(sourceRows.map((row) => row.usage_location_label || "未知")), [sourceRows]);
  const years = useMemo(() => uniq(sourceRows.map(yearOf)).reverse(), [sourceRows]);

  const rows = useMemo(() => {
    const term = search.trim().toLocaleLowerCase("zh-CN");
    return sourceRows.filter((row) => {
      if (componentFilter && row.component_key !== componentFilter) return false;
      if (category && row.product_category !== category) return false;
      if (location && (row.usage_location_label || "未知") !== location) return false;
      if (year && yearOf(row) !== year) return false;
      if (!term) return true;
      return [row.product_model, row.component_label, row.component_name, supplierOf(row), row.component_model, row.evidence_quote]
        .join(" ").toLocaleLowerCase("zh-CN").includes(term);
    });
  }, [sourceRows, componentFilter, category, location, year, search]);

  const products = useMemo(() => {
    const grouped = new Map<string, { id: string; model: string; category: string; date: string }>();
    rows.forEach((row) => {
      const current = grouped.get(row.product_id);
      const date = row.source_published_at || "";
      if (!current || (date && date < current.date)) {
        grouped.set(row.product_id, { id: row.product_id, model: row.product_model, category: row.product_category, date });
      }
    });
    return [...grouped.values()].sort((a, b) => a.date.localeCompare(b.date) || a.model.localeCompare(b.model, "zh-CN"));
  }, [rows]);

  const components = useMemo(() => {
    const labels = new Map<string, string>();
    rows.forEach((row) => labels.set(row.component_key, row.component_label));
    return [...labels.entries()].sort((a, b) => {
      const ai = COMPONENT_ORDER.indexOf(a[0]);
      const bi = COMPONENT_ORDER.indexOf(b[0]);
      return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi) || a[1].localeCompare(b[1], "zh-CN");
    });
  }, [rows]);

  const cellRows = useMemo(() => {
    const grouped = new Map<string, SupplyRow[]>();
    rows.forEach((row) => {
      const key = `${row.component_key}\u0000${row.product_id}`;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key)!.push(row);
    });
    return grouped;
  }, [rows]);

  const matrixProducts = useMemo(() => showMissingMatrix ? products : products.filter((product) =>
    components.some(([componentKey]) => (cellRows.get(`${componentKey}\u0000${product.id}`) || []).some((row) => supplierOf(row) !== UNKNOWN))
  ), [showMissingMatrix, products, components, cellRows]);
  const matrixComponents = useMemo(() => showMissingMatrix ? components : components.filter(([componentKey]) =>
    products.some((product) => (cellRows.get(`${componentKey}\u0000${product.id}`) || []).some((row) => supplierOf(row) !== UNKNOWN))
  ), [showMissingMatrix, components, products, cellRows]);

  const supplierRanking = useMemo(() => {
    const grouped = new Map<string, Set<string>>();
    rows.forEach((row) => {
      const supplier = supplierOf(row);
      if (supplier === UNKNOWN) return;
      if (!grouped.has(supplier)) grouped.set(supplier, new Set());
      grouped.get(supplier)!.add(row.product_id);
    });
    return [...grouped.entries()]
      .map(([supplier, ids]) => ({ supplier, products: ids.size }))
      .sort((a, b) => b.products - a.products || a.supplier.localeCompare(b.supplier, "zh-CN"));
  }, [rows]);

  const knownCellCoverage = useMemo(() => {
    if (!cellRows.size) return 0;
    let known = 0;
    cellRows.forEach((group) => { if (group.some((row) => supplierOf(row) !== UNKNOWN)) known += 1; });
    return known / cellRows.size;
  }, [cellRows]);

  const insights = useMemo(() => {
    const items: string[] = [];
    if (cellRows.size) items.push(`供应商已识别的产品–器件单元占 ${pct(knownCellCoverage)}；其余保持“报告未披露”。`);

    const reuse = new Map<string, Set<string>>();
    rows.forEach((row) => {
      const supplier = supplierOf(row);
      if (supplier === UNKNOWN || !row.component_model) return;
      const key = `${row.component_label}｜${supplier}｜${row.component_model}`;
      if (!reuse.has(key)) reuse.set(key, new Set());
      reuse.get(key)!.add(row.product_id);
    });
    const mostReused = [...reuse.entries()].sort((a, b) => b[1].size - a[1].size)[0];
    if (mostReused && mostReused[1].size >= 2) items.push(`${mostReused[0]} 在 ${mostReused[1].size} 款产品中重复出现，形成可复用平台线索。`);

    const observedYears = uniq(rows.map(yearOf));
    const latest = observedYears.at(-1);
    if (latest) {
      const firstSeen = new Map<string, string>();
      rows.forEach((row) => {
        const supplier = supplierOf(row);
        const observed = yearOf(row);
        if (supplier === UNKNOWN || !observed) return;
        if (!firstSeen.has(supplier) || observed < firstSeen.get(supplier)!) firstSeen.set(supplier, observed);
      });
      const entrants = [...firstSeen.entries()].filter(([, first]) => first === latest).map(([supplier]) => supplier).slice(0, 3);
      if (entrants.length) items.push(`${latest} 年拆解样本首次观察到 ${entrants.join("、")}；这是样本新增关系，不等同于正式导入供应商。`);
    }

    for (const [componentKey, componentLabel] of components) {
      const categoryTop = new Map<string, string>();
      for (const item of categories) {
        const counts = new Map<string, Set<string>>();
        rows.filter((row) => row.component_key === componentKey && row.product_category === item).forEach((row) => {
          const supplier = supplierOf(row);
          if (supplier === UNKNOWN) return;
          if (!counts.has(supplier)) counts.set(supplier, new Set());
          counts.get(supplier)!.add(row.product_id);
        });
        const top = [...counts.entries()].sort((a, b) => b[1].size - a[1].size)[0];
        if (top) categoryTop.set(item, top[0]);
      }
      if (new Set(categoryTop.values()).size >= 2) {
        items.push(`${componentLabel} 在不同耳机类型中呈现不同的主要供应商组合，建议展开类型核对具体产品。`);
        break;
      }
    }
    return items.slice(0, 4);
  }, [rows, cellRows, knownCellCoverage, components, categories]);

  const selectedRows = useMemo(() => {
    if (!selection) return [];
    const found = rows.filter((row) => {
      if (selection.kind === "component") return row.component_key === selection.componentKey;
      if (selection.kind === "supplier") return supplierOf(row) === selection.supplier;
      if (selection.kind === "product") return row.product_id === selection.productId;
      return row.component_key === selection.componentKey && row.product_id === selection.productId && supplierOf(row) === selection.supplier;
    });
    const dedup = new Map<string, SupplyRow>();
    found.forEach((row) => dedup.set(rowSignature(row), row));
    return [...dedup.values()].sort((a, b) => (b.source_published_at || "").localeCompare(a.source_published_at || ""));
  }, [rows, selection]);

  const selectedComponent = selection && (selection.kind === "component" || selection.kind === "cell")
    ? selection.componentKey : "";

  const evolution = useMemo(() => {
    if (!selectedComponent) return [];
    const relationships = new Map<string, { productId: string; productModel: string; supplier: string; year: string; categories: Set<string>; models: Set<string> }>();
    rows.filter((row) => row.component_key === selectedComponent).forEach((row) => {
      const supplier = supplierOf(row);
      const key = `${row.product_id}\u0000${supplier}`;
      const observed = yearOf(row);
      const current = relationships.get(key) || { productId: row.product_id, productModel: row.product_model, supplier, year: observed, categories: new Set<string>(), models: new Set<string>() };
      if (observed && (!current.year || observed < current.year)) current.year = observed;
      current.categories.add(row.product_category);
      if (row.component_model) current.models.add(row.component_model);
      relationships.set(key, current);
    });
    const byYear = new Map<string, Map<string, { products: Map<string, string>; categories: Set<string>; models: Set<string> }>>();
    relationships.forEach((item) => {
      const observed = item.year || "未知年份";
      if (!byYear.has(observed)) byYear.set(observed, new Map());
      const suppliers = byYear.get(observed)!;
      if (!suppliers.has(item.supplier)) suppliers.set(item.supplier, { products: new Map(), categories: new Set(), models: new Set() });
      const group = suppliers.get(item.supplier)!;
      group.products.set(item.productId, item.productModel);
      item.categories.forEach((value) => group.categories.add(value));
      item.models.forEach((value) => group.models.add(value));
    });
    return [...byYear.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [rows, selectedComponent]);

  const selectionTitle = useMemo(() => {
    if (!selection) return "";
    if (selection.kind === "component") return `${components.find(([key]) => key === selection.componentKey)?.[1] || selection.componentKey} · 供应商演变`;
    if (selection.kind === "supplier") return `${selection.supplier} · 品牌内应用`;
    if (selection.kind === "product") return `${products.find((product) => product.id === selection.productId)?.model || selection.productId} · BOM 供应链`;
    const product = products.find((item) => item.id === selection.productId)?.model || selection.productId;
    const component = components.find(([key]) => key === selection.componentKey)?.[1] || selection.componentKey;
    return `${product} · ${component} · ${selection.supplier}`;
  }, [selection, components, products]);

  const resetFilters = () => {
    setComponentFilter(""); setCategory(""); setLocation(""); setYear(""); setSearch(""); setSelection(null);
  };

  const allBrands = useMemo<BrandBrowseItem[]>(() => {
    const archive = new Map<string, { products: Set<string>; categories: Set<string> }>();
    (productIndex?.products || []).forEach((product) => {
      const brand = (product.brand || "").trim();
      if (!brand || brand === "未知" || brand === "未知品牌") return;
      if (!archive.has(brand)) archive.set(brand, { products: new Set(), categories: new Set() });
      const group = archive.get(brand)!;
      if (product.canonical_id) group.products.add(product.canonical_id);
      if (product.category) group.categories.add(product.category);
    });
    const analyzableByBrand = new Map((manifest?.brands || []).map((item) => [item.brand, item]));
    const names = new Set([...archive.keys(), ...analyzableByBrand.keys()]);
    return [...names].map((brand) => {
      const analyzable = analyzableByBrand.get(brand);
      const archiveItem = archive.get(brand);
      return {
        brand,
        key: analyzable?.key || "",
        products: analyzable?.products || 0,
        components: analyzable?.components || 0,
        rows: analyzable?.rows || 0,
        analyzable: Boolean(analyzable),
        archiveProducts: archiveItem?.products.size || analyzable?.products || 0,
        categories: [...(archiveItem?.categories || [])].sort((a, b) => a.localeCompare(b, "zh-CN")),
      };
    }).sort((a, b) => b.archiveProducts - a.archiveProducts || a.brand.localeCompare(b.brand, "zh-CN"));
  }, [manifest, productIndex]);
  const analyzableBrandCount = useMemo(() => allBrands.filter((item) => item.analyzable).length, [allBrands]);
  const brandMatches = useMemo(() => {
    const needle = brandQuery.trim().toLocaleLowerCase("zh-CN");
    return allBrands.filter((item) => !needle || item.brand.toLocaleLowerCase("zh-CN").includes(needle));
  }, [allBrands, brandQuery]);
  const brandOptions = brandMatches.slice(0, 20);
  const brandBrowseCategories = useMemo(
    () => uniq(allBrands.flatMap((item) => item.categories)),
    [allBrands],
  );
  const brandBrowseInitials = useMemo(
    () => uniq(allBrands.map((item) => /^[a-z]/i.test(item.brand) ? item.brand[0].toUpperCase() : "中文"))
      .sort((a, b) => a === "中文" ? 1 : b === "中文" ? -1 : a.localeCompare(b)),
    [allBrands],
  );
  const directoryBrands = useMemo(() => {
    const needle = brandBrowseQuery.trim().toLocaleLowerCase("zh-CN");
    return allBrands.filter((item) => {
      if (needle && !item.brand.toLocaleLowerCase("zh-CN").includes(needle)) return false;
      if (brandBrowseCategory && !item.categories.includes(brandBrowseCategory)) return false;
      const initial = /^[a-z]/i.test(item.brand) ? item.brand[0].toUpperCase() : "中文";
      if (brandBrowseInitial && initial !== brandBrowseInitial) return false;
      if (brandBrowseStatus === "analyzable" && !item.analyzable) return false;
      if (brandBrowseStatus === "unavailable" && item.analyzable) return false;
      return true;
    }).sort((a, b) => brandBrowseSort === "name"
      ? a.brand.localeCompare(b.brand, "zh-CN")
      : b.archiveProducts - a.archiveProducts || a.brand.localeCompare(b.brand, "zh-CN"));
  }, [allBrands, brandBrowseQuery, brandBrowseCategory, brandBrowseInitial, brandBrowseStatus, brandBrowseSort]);
  const chooseBrand = (item: BrandBrowseItem) => {
    setPendingBrandKey(item.key);
    setPendingBrandName(item.brand);
    setBrandQuery(item.brand);
    setBrandPickerOpen(false);
    setBrandDirectoryOpen(false);
  };
  const startBrandAnalysis = () => {
    if (!pendingBrandKey || !selectedBrand?.analyzable) return;
    const brandChanged = pendingBrandKey !== brandKey;
    if (brandChanged) {
      setPayload(null);
      setLoading(true);
      setBrandKey(pendingBrandKey);
    }
    setShowMissingMatrix(false);
    resetFilters();
    setScrollToFingerprint(true);
  };
  const selectedBrand = useMemo(
    () => allBrands.find((item) => item.brand === pendingBrandName),
    [allBrands, pendingBrandName],
  );
  const evidencePageSize = 25;
  const evidencePageCount = Math.max(1, Math.ceil(selectedRows.length / evidencePageSize));
  const visibleEvidenceRows = selectedRows.slice((evidencePage - 1) * evidencePageSize, evidencePage * evidencePageSize);
  useEffect(() => setEvidencePage(1), [selection, componentFilter, category, location, year, search]);

  return (
    <div className="brand-supply">
      <section className="brand-supply-page__hero">
        <div className="brand-supply-page__hero-copy">
          <p className="home-eyebrow">Brand supply-chain intelligence</p>
          <h1>从品牌出发，看清器件供应链脉络</h1>
          <p>查看该品牌在不同耳机产品、器件类型与年份中的供应商关系，并追溯到具体产品和拆解报告证据。</p>
          <p className="brand-supply-page__scope"><strong>数据口径</strong> 基于我爱音频网拆解样本，呈现已披露或已识别的供应关系，不代表采购量或市场份额。</p>
        </div>
        <aside className="brand-supply-page__journey" aria-label="品牌供应链分析使用指引">
          <ol>
            <li className="is-primary">
              <span className="brand-supply-page__step-index">1</span>
              <div>
                <strong>搜索并选择耳机品牌</strong>
                <label className="brand-supply__brand-picker" htmlFor="brand-supply-brand-search">
                  <span>品牌名称</span>
                  <input id="brand-supply-brand-search" type="search" role="combobox" value={brandQuery} disabled={!allBrands.length} onFocus={() => setBrandPickerOpen(true)} onBlur={() => window.setTimeout(() => setBrandPickerOpen(false), 120)} onChange={(event) => { setBrandQuery(event.target.value); setPendingBrandKey(""); setPendingBrandName(""); setBrandPickerOpen(true); }} onKeyDown={(event) => { if (event.key === "Enter" && pendingBrandKey) startBrandAnalysis(); }} placeholder={allBrands.length ? "例如 SONY、倍思" : "正在加载品牌…"} aria-controls="brand-supply-brand-options" aria-expanded={brandPickerOpen} aria-autocomplete="list" autoComplete="off" />
                  {brandPickerOpen && allBrands.length > 0 && <div id="brand-supply-brand-options" className="brand-supply__brand-options" role="listbox">{brandOptions.map((item) => <button key={item.brand} type="button" role="option" className={item.analyzable ? "" : "is-unavailable"} aria-selected={pendingBrandName === item.brand} onMouseDown={(event) => event.preventDefault()} onClick={() => chooseBrand(item)}><strong>{item.brand}</strong><span>{item.analyzable ? `${item.products} 款可分析产品 · ${item.components} 类器件` : `${item.archiveProducts} 款已收录产品 · 暂无器件记录`}</span></button>)}{!brandOptions.length && <small>没有匹配的已识别品牌</small>}{brandMatches.length > 20 && <small className="brand-supply__brand-options-more">找到 {brandMatches.length} 个匹配品牌，当前显示前 20 个</small>}</div>}
                </label>
                <button
                  className="brand-supply__directory-action"
                  type="button"
                  aria-label={`浏览全部品牌，${allBrands.length} 个已收录品牌，${analyzableBrandCount} 个可分析品牌`}
                  onClick={() => { setBrandPickerOpen(false); setBrandDirectoryOpen(true); }}
                >
                  <span className="brand-supply__directory-action-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" focusable="false">
                      <rect x="3" y="3" width="7" height="7" rx="1.5" />
                      <rect x="14" y="3" width="7" height="7" rx="1.5" />
                      <rect x="3" y="14" width="7" height="7" rx="1.5" />
                      <rect x="14" y="14" width="7" height="7" rx="1.5" />
                    </svg>
                  </span>
                  <span className="brand-supply__directory-action-copy">
                    <strong>浏览全部品牌</strong>
                    <small><b>{allBrands.length}</b> 个已收录 · <b>{analyzableBrandCount}</b> 个可分析</small>
                  </span>
                  <span className="brand-supply__directory-action-arrow" aria-hidden="true">→</span>
                </button>
                {selectedBrand && !selectedBrand.analyzable ? <a className="brand-supply-page__start-button is-archive" href={`${teardownBase}?brand=${encodeURIComponent(selectedBrand.brand)}`}><span>查看品牌情报明细</span><small>{selectedBrand.archiveProducts} 款产品已收录，暂无可分析供应链记录</small><b aria-hidden="true">→</b></a> : <button className="brand-supply-page__start-button" type="button" disabled={!selectedBrand} onClick={startBrandAnalysis}><span>{brandKey === pendingBrandKey && payload ? "查看当前分析" : "开始分析"}</span><small>{selectedBrand ? `${selectedBrand.products} 款可分析产品 · ${selectedBrand.components} 类器件` : "请先从搜索结果中选择品牌"}</small><b aria-hidden="true">↓</b></button>}
              </div>
            </li>
            <li><span className="brand-supply-page__step-index">2</span><div><strong>按条件进一步收窄</strong><small>可选器件类型、耳机类型、使用位置和年份。</small></div></li>
            <li><span className="brand-supply-page__step-index">3</span><div><strong>查看演变并追溯证据</strong><small>点击具体器件，定位供应商、产品与原始报告。</small></div></li>
          </ol>
        </aside>
      </section>
      {brandDirectoryOpen && (
        <div className="brand-supply__directory-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setBrandDirectoryOpen(false); }}>
          <section className="brand-supply__directory" role="dialog" aria-modal="true" aria-labelledby="brand-directory-title">
            <header>
              <div><span>All recorded brands</span><h2 id="brand-directory-title">浏览全部品牌</h2><p>先了解网站收录范围，再选择一个品牌进入供应链分析。</p></div>
              <button type="button" onClick={() => setBrandDirectoryOpen(false)} aria-label="关闭全部品牌目录">×</button>
            </header>
            <div className="brand-supply__directory-summary" aria-label="品牌数据覆盖">
              <article><span>全站收录品牌</span><strong>{allBrands.length}</strong><small>拥有产品档案</small></article>
              <article><span>可分析品牌</span><strong>{analyzableBrandCount}</strong><small>具有可追溯器件记录</small></article>
              <article><span>暂无器件记录</span><strong>{allBrands.length - analyzableBrandCount}</strong><small>仍保留品牌与产品档案</small></article>
            </div>
            <div className="brand-supply__directory-controls">
              <label className="brand-supply__directory-search">搜索品牌<input type="search" value={brandBrowseQuery} onChange={(event) => setBrandBrowseQuery(event.target.value)} placeholder="输入中文名或英文名" autoFocus /></label>
              <label>数据状态<select value={brandBrowseStatus} onChange={(event) => setBrandBrowseStatus(event.target.value as "all" | "analyzable" | "unavailable")}><option value="all">全部品牌</option><option value="analyzable">可分析供应链</option><option value="unavailable">暂无器件记录</option></select></label>
              <label>耳机类型<select value={brandBrowseCategory} onChange={(event) => setBrandBrowseCategory(event.target.value)}><option value="">全部类型</option>{brandBrowseCategories.map((value) => <option key={value}>{value}</option>)}</select></label>
              <label>排序<select value={brandBrowseSort} onChange={(event) => setBrandBrowseSort(event.target.value as "products" | "name")}><option value="products">按收录产品数</option><option value="name">按品牌名称</option></select></label>
              <button type="button" onClick={() => { setBrandBrowseQuery(""); setBrandBrowseCategory(""); setBrandBrowseInitial(""); setBrandBrowseStatus("all"); setBrandBrowseSort("products"); }}>重置</button>
            </div>
            <div className="brand-supply__directory-initials" aria-label="按品牌首字母筛选"><button type="button" className={!brandBrowseInitial ? "is-active" : ""} onClick={() => setBrandBrowseInitial("")}>全部</button>{brandBrowseInitials.map((value) => <button type="button" key={value} className={brandBrowseInitial === value ? "is-active" : ""} onClick={() => setBrandBrowseInitial(value)}>{value}</button>)}</div>
            <div className="brand-supply__directory-result-title"><strong>当前显示 {directoryBrands.length} 个品牌</strong><span>产品数量来自我爱音频网拆解样本，不代表市场规模。</span></div>
            <div className="brand-supply__directory-grid">
              {directoryBrands.map((item) => <button type="button" key={item.brand} className={item.analyzable ? "" : "is-unavailable"} onClick={() => chooseBrand(item)}><span className="brand-supply__directory-card-title"><strong>{item.brand}</strong><em>{item.analyzable ? "可分析" : "暂无器件记录"}</em></span><span className="brand-supply__directory-card-stats"><b>{item.archiveProducts}</b> 款收录产品{item.analyzable && <> · <b>{item.components}</b> 类器件</>}</span><small>{item.categories.length ? item.categories.slice(0, 3).join(" · ") : "耳机类型待完善"}{item.categories.length > 3 ? ` 等 ${item.categories.length} 类` : ""}</small></button>)}
              {!directoryBrands.length && <p className="brand-supply__directory-empty">当前条件下没有匹配品牌，请调整搜索或筛选条件。</p>}
            </div>
          </section>
        </div>
      )}
      {error && <p className="brand-supply__error" role="alert">{error}</p>}
      {loading && !manifest && <p className="brand-supply__status">正在准备品牌供应链数据…</p>}
      {loading && manifest && brandKey && !payload && <p className="brand-supply__status">正在加载所选品牌的供应链数据…</p>}
      <section className="brand-supply__filters" aria-label="品牌供应链筛选">
        <label>器件类型<select value={componentFilter} disabled={!payload} onChange={(event) => { setComponentFilter(event.target.value); setSelection(null); }}><option value="">全部器件</option>{componentOptions.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label>耳机类型<select value={category} disabled={!payload} onChange={(event) => { setCategory(event.target.value); setSelection(null); }}><option value="">全部类型</option>{categories.map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>使用位置<select value={location} disabled={!payload} onChange={(event) => { setLocation(event.target.value); setSelection(null); }}><option value="">全部位置</option>{locations.map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>报告年份<select value={year} disabled={!payload} onChange={(event) => { setYear(event.target.value); setSelection(null); }}><option value="">全部年份</option>{years.map((value) => <option key={value}>{value}</option>)}</select></label>
        <label className="brand-supply__search">型号 / 供应商 / 产品搜索<input value={search} disabled={!payload} onChange={(event) => { setSearch(event.target.value); setSelection(null); }} placeholder="例如：AC7106F6、紫建、Bowie" /></label>
        <button type="button" onClick={resetFilters} disabled={!payload}>重置</button>
      </section>

      {!payload && !loading && (
        <section className="brand-supply__start"><strong>从上方搜索并选择一个耳机品牌</strong><span>品牌是必选项；器件类型、耳机类型、使用位置和年份均可稍后按需收窄。</span></section>
      )}
      {loading && payload && <p className="brand-supply__status">正在切换品牌…</p>}

      {payload && !loading && (
        <>
          <section className="brand-supply__summary" aria-label="品牌供应链覆盖概览">
            <article><span>有可分析 BOM 的产品</span><strong>{products.length}</strong><small>当前品牌与筛选范围</small></article>
            <article><span>器件类型</span><strong>{components.length}</strong><small>有报告证据</small></article>
            <article><span>已识别供应商</span><strong>{supplierRanking.length}</strong><small>归一化名称</small></article>
            <article><span>供应商识别覆盖</span><strong>{pct(knownCellCoverage)}</strong><small>已披露供应商的产品–器件单元 / 全部有器件记录单元</small></article>
          </section>

          <section className="brand-supply__signals">
            <div><span>Observed signals</span><h2>{payload.brand} 的供应链观察</h2></div>
            <ul>{insights.map((item) => <li key={item}>{item}</li>)}</ul>
          </section>

          <section ref={fingerprintRef} className="brand-supply__fingerprint">
            <div className="brand-supply__section-title">
              <div><span>Portfolio fingerprint</span><h2>品牌 BOM 供应链指纹</h2><p className="brand-supply__section-guide">请点击左侧具体器件的“查看供应商演变”，继续查看历年供应商、应用产品和报告证据。</p></div>
              <button type="button" onClick={() => setShowMissingMatrix((value) => !value)}>{showMissingMatrix ? "隐藏未披露行列" : "显示报告未披露行列"}</button>
            </div>
            <div className="brand-supply__supplier-strip" aria-label="主要供应商">
              <b>供应商高亮</b>
              {supplierRanking.slice(0, 12).map((item) => (
                <button key={item.supplier} type="button" className={`${supplierClass(item.supplier)} ${selection?.kind === "supplier" && selection.supplier === item.supplier ? "is-selected" : ""}`} onClick={() => setSelection(selection?.kind === "supplier" && selection.supplier === item.supplier ? null : { kind: "supplier", supplier: item.supplier })}>{item.supplier}<small>{item.products}款</small></button>
              ))}
            </div>
            <div className="brand-supply__fingerprint-wrap">
              <table>
                <thead><tr><th>器件 / 产品</th>{matrixProducts.map((product) => <th key={product.id}><button type="button" className={selection?.kind === "product" && selection.productId === product.id ? "is-selected" : ""} onClick={() => setSelection(selection?.kind === "product" && selection.productId === product.id ? null : { kind: "product", productId: product.id })}><small>{product.date.slice(0, 4) || "—"} · {product.category}</small><strong>{product.model}</strong></button></th>)}</tr></thead>
                <tbody>{matrixComponents.map(([componentKey, componentLabel]) => (
                  <tr key={componentKey}>
                    <th><button type="button" aria-expanded={selection?.kind === "component" && selection.componentKey === componentKey} className={`brand-supply__component-drill ${selection?.kind === "component" && selection.componentKey === componentKey ? "is-selected" : ""}`} title={`查看${componentLabel}的供应商演变`} onClick={() => setSelection(selection?.kind === "component" && selection.componentKey === componentKey ? null : { kind: "component", componentKey })}><strong>{componentLabel}</strong><small><span>{selection?.kind === "component" && selection.componentKey === componentKey ? "正在查看演变" : "查看供应商演变"}</span><b aria-hidden="true">{selection?.kind === "component" && selection.componentKey === componentKey ? "↓" : "→"}</b></small></button></th>
                    {matrixProducts.map((product) => {
                      const grouped = cellRows.get(`${componentKey}\u0000${product.id}`) || [];
                      const suppliers = uniq(grouped.map(supplierOf).filter((supplier) => supplier !== UNKNOWN));
                      const highlighted = selection?.kind === "supplier" && suppliers.includes(selection.supplier);
                      return <td key={product.id} className={highlighted ? "is-highlighted" : ""}>{suppliers.length ? suppliers.slice(0, 2).map((supplier) => <button key={supplier} type="button" className={`${supplierClass(supplier)} ${selection?.kind === "cell" && selection.componentKey === componentKey && selection.productId === product.id && selection.supplier === supplier ? "is-selected" : ""}`} onClick={() => setSelection(selection?.kind === "cell" && selection.componentKey === componentKey && selection.productId === product.id && selection.supplier === supplier ? null : { kind: "cell", componentKey, productId: product.id, supplier })}>{supplier}</button>) : <span>—</span>}{suppliers.length > 2 && <small>+{suppliers.length - 2}</small>}</td>;
                    })}
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <p className="brand-supply__caption">默认只显示至少披露一家供应商的器件行与产品列，降低空白噪声；可切换查看完整“报告未披露”矩阵。颜色只用于区分供应商。</p>
          </section>

          {selection && (
            <section ref={drilldownRef} className="brand-supply__drilldown" aria-live="polite">
              <div className="brand-supply__section-title">
                <div><span>Selected relationship</span><h2>{selectionTitle}</h2></div>
                <button type="button" onClick={() => setSelection(null)}>← 返回供应链指纹</button>
              </div>

              {selectedComponent && (
                <div className="brand-supply__evolution">
                  <h3>供应商首次观察应用轨迹</h3>
                  <p>每款产品–供应商关系只计入首次被拆解报告观察到的年份，不表示真实采购切换。</p>
                  <div>{evolution.map(([observedYear, suppliers]) => (
                    <article key={observedYear}><header><strong>{observedYear}</strong><span>{new Set([...suppliers.values()].flatMap((group) => [...group.products.keys()])).size} 款产品</span></header>{[...suppliers.entries()].sort((a, b) => b[1].products.size - a[1].products.size).map(([supplier, group]) => <section key={supplier} className={`brand-supply__evolution-supplier ${supplierClass(supplier)}`}><button type="button" onClick={() => setSelection({ kind: "supplier", supplier })}><strong>{supplier}</strong><span>{group.products.size} 款</span></button><p><b>报告中的器件型号</b>{group.models.size ? [...group.models].slice(0, 3).join("、") : "未披露"}</p><b className="brand-supply__evolution-products-title">应用产品</b><ul>{[...group.products.entries()].sort((a, b) => a[1].localeCompare(b[1], "zh-CN")).map(([productId, productModel]) => <li key={productId}><a href={`${productBase}${productId}/`}>{productModel}</a></li>)}</ul></section>)}</article>
                  ))}</div>
                </div>
              )}

              <div className="brand-supply__selection-summary">
                {components.map(([componentKey, componentLabel]) => {
                  const componentRows = selectedRows.filter((row) => row.component_key === componentKey);
                  if (!componentRows.length) return null;
                  return <span key={componentKey}><b>{componentLabel}</b>{uniq(componentRows.map(supplierOf)).join("、")} · {new Set(componentRows.map((row) => row.product_id)).size} 款</span>;
                })}
              </div>

              <div className="brand-supply__evidence-wrap">
                <table>
                  <thead><tr><th>耳机类型</th><th>产品</th><th>器件</th><th>供应商 / 型号</th><th>位置与参数</th><th>关联证据图</th><th>报告与文字证据</th></tr></thead>
                  <tbody>{visibleEvidenceRows.map((row, index) => {
                    const image = row.evidence_image;
                    const imageLabel = image?.caption || image?.alt || `${row.component_label}关联证据图`;
                    return <tr key={`${rowSignature(row)}-${index}`}><td>{row.product_category}</td><td><a href={`${productBase}${row.product_id}/`}>{row.product_model}</a><small>{row.source_published_at || "日期未披露"}</small></td><td><strong>{row.component_label}</strong><small>{row.component_name || ""}</small></td><td><strong>{supplierOf(row)}</strong><small>{row.component_model || "型号未披露"}</small></td><td><strong>{row.usage_location_label || "未知位置"}</strong>{(row.parameters || []).length ? <ul>{row.parameters!.slice(0, 5).map((item, itemIndex) => <li key={`${item.label}-${itemIndex}`}>{item.label}：{item.value}</li>)}</ul> : <small>参数未披露</small>}</td><td>{image?.public_path ? <button type="button" className="brand-supply__evidence-image" title="点击在页面中放大" onClick={() => setImagePreview({ src: image.public_path!, label: imageLabel, product: row.product_model, component: row.component_label })}><img src={image.public_path} alt={imageLabel} loading="lazy" onError={(event) => { const button = event.currentTarget.closest("button"); button?.classList.add("is-missing"); event.currentTarget.remove(); }} /><span>点击放大</span></button> : <small>本地图片待同步</small>}</td><td><details><summary>查看证据 · {row.source_type === "video" ? "拆解视频" : "拆解报告"}</summary><blockquote>{row.evidence_quote || "证据句未提供"}</blockquote>{row.source_report_url && <a href={row.source_report_url} target="_blank" rel="noreferrer">{row.source_type === "video" ? "查看原视频 ↗" : "查看原报告 ↗"}</a>}</details></td></tr>;
                  })}</tbody>
                </table>
              </div>
              {evidencePageCount > 1 && <nav className="sourcing-pagination" aria-label="品牌供应链证据分页"><button type="button" disabled={evidencePage <= 1} onClick={() => setEvidencePage((value) => Math.max(1, value - 1))}>上一页</button><span>第 {evidencePage} / {evidencePageCount} 页</span><button type="button" disabled={evidencePage >= evidencePageCount} onClick={() => setEvidencePage((value) => Math.min(evidencePageCount, value + 1))}>下一页</button></nav>}
            </section>
          )}

          <section className="brand-supply__quality"><strong>统计边界</strong><ul>{payload.scope_notes.map((note) => <li key={note}>{note}</li>)}</ul><p>当前粒度：{payload.grain}。页面用于发现供应链线索，结论仍需回到原报告证据确认。</p></section>
          {imagePreview && <div className="brand-supply__lightbox" role="dialog" aria-modal="true" aria-label="关联证据图预览" onMouseDown={(event) => { if (event.target === event.currentTarget) setImagePreview(null); }}><div><button type="button" className="brand-supply__lightbox-close" onClick={() => setImagePreview(null)} aria-label="关闭图片预览">×</button><img src={imagePreview.src} alt={imagePreview.label} /><section><strong>{imagePreview.product} · {imagePreview.component}</strong><span>{imagePreview.label}</span><small>这是器件事实所在原文段落附近的关联报告原图；用于证据追溯，不代表已经通过视觉模型确认图片只包含该器件。</small></section></div></div>}
        </>
      )}
    </div>
  );
}
