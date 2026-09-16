import { useEffect, useMemo, useRef, useState } from "react";

type Mode = "component" | "supplier";
type ComponentResultView = "supplier" | "model";
type ComponentScopeMode = "cross" | "focused";

interface AnalysisImage {
  public_path?: string;
  url?: string;
  alt?: string;
  caption?: string;
}

interface AnalysisFeature {
  label?: string;
  value?: string;
  evidence_quote?: string;
}

interface AnalysisRow {
  row_id: string;
  component_key: string;
  component_label: string;
  component_name?: string;
  component_manufacturer?: string;
  component_manufacturer_canonical?: string;
  manufacturer_basis?: string;
  manufacturer_status?: "reported" | "not_disclosed" | "invalid_extraction";
  component_model?: string;
  component_model_normalized?: string;
  model_status?: "reported" | "not_disclosed" | "invalid_parameter_value";
  material?: string;
  parameters?: AnalysisFeature[];
  semantic_features?: AnalysisFeature[];
  usage_location_label?: string;
  product_id: string;
  product_brand?: string;
  product_brand_status?: "resolved" | "unresolved";
  product_model?: string;
  product_category?: string;
  source_type?: string;
  source_report_id?: string;
  source_video_id?: string;
  source_report_url?: string;
  source_published_at?: string;
  evidence_quote?: string;
  evidence_image?: AnalysisImage | null;
}

interface ComponentManifestItem {
  key: string;
  label: string;
  rows: number;
  products: number;
  file: string;
}

interface ComponentManifest {
  default_component?: string;
  components: ComponentManifestItem[];
  scope_notes?: string[];
}

interface ComponentPayload {
  component_key: string;
  component_label: string;
  view_mode?: "standard" | "structural";
  grain: string;
  scope_notes: string[];
  rows: AnalysisRow[];
}

interface SupplierManifestItem {
  supplier: string;
  key: string;
  products: number;
  components: number;
  models: number;
  brands: number;
  model_known_products?: number;
  brand_known_products?: number;
  rows: number;
}

interface SupplierManifest {
  suppliers: SupplierManifestItem[];
  scope_notes?: string[];
}

interface SupplierPayload {
  supplier: string;
  supplier_key: string;
  grain: string;
  scope_notes: string[];
  rows: AnalysisRow[];
}

interface Props {
  manifestUrl: string;
  componentDataBase: string;
  supplierManifestUrl: string;
  supplierDataBase: string;
  productBase: string;
}

interface SupplierStat {
  supplier: string;
  products: number;
  models: number;
  brands: number;
  brandNames: string[];
  categories: string[];
  modelKnownProducts: number;
  supplierOnlyProducts: number;
  brandKnownProducts: number;
  parameterizedProducts: number;
  rows: number;
}

interface ComponentStat {
  key: string;
  label: string;
  products: number;
  models: number;
  brands: number;
  modelKnownProducts: number;
  brandKnownProducts: number;
  rows: number;
}

interface ModelStat {
  key: string;
  supplier: string;
  model: string;
  products: number;
  brands: number;
  brandNames: string[];
  categories: string[];
  locations: string[];
  firstYear: string;
  latestYear: string;
  parameterizedProducts: number;
  rows: AnalysisRow[];
}

interface SelectedModelLead {
  key: string;
  source: "scope";
}

interface ComponentFilterState {
  category: string;
  location: string;
  supplier: string;
  brand: string;
  year: string;
  query: string;
}

interface FacetOption {
  value: string;
  products: number;
}

interface DetailFilterState {
  category: string;
  location: string;
  model: string;
  brand: string;
  year: string;
  parameterState: string;
  query: string;
}

const UNKNOWN = "报告未披露";
const ALL_EVIDENCE = "__all_evidence__";
const TRUSTED_CATEGORIES = new Set([
  "耳夹式耳机", "耳挂式耳机", "全入耳式耳机", "半入耳式耳机",
  "头戴式耳机", "骨传导耳机", "颈挂式蓝牙耳机", "有线耳机",
]);
const TRUSTED_LOCATIONS = new Set(["耳机", "充电盒"]);
const TREND_COLORS = ["#3159d8", "#2f9f8f", "#c58a22", "#7b61b9", "#d66f45"];
const GENERIC_MODEL_NAMES = new Set([
  "电池", "锂电池", "麦克风", "mems麦克风", "memsmicrophone", "microphone", "蓝牙音频soc", "soc",
  "电源管理ic", "电池保护ic", "扬声器", "喇叭", "发声单元", "连接器", "排线", "pcb", "电路板", "外壳", "壳体", "支架", "转轴",
]);

const text = (value: unknown) => String(value ?? "").trim();
const unique = (values: string[]) => [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
const uniqueProducts = (rows: AnalysisRow[]) => new Set(rows.map((row) => row.product_id).filter(Boolean)).size;
const sourceCounts = (rows: AnalysisRow[]) => {
  const reports = new Set<string>();
  const videos = new Set<string>();
  rows.forEach((row) => {
    const isVideo = row.source_type === "video";
    const key = text(isVideo ? row.source_video_id : row.source_report_id)
      || text(row.source_report_url)
      || `${row.product_id}\u0000${yearOf(row)}\u0000${isVideo ? "video" : "report"}`;
    (isVideo ? videos : reports).add(key);
  });
  return { reports: reports.size, videos: videos.size };
};
const looksLikeParameterDescription = (value: unknown) => {
  const raw = text(value);
  if (!/(?:mAh|mWh|Wh|\d(?:\.\d+)?\s*V\b)/i.test(raw)) return false;
  const remainder = raw
    .replace(/\d+(?:\.\d+)?\s*(?:mAh|mWh|Wh|V)/gi, "")
    .replace(/(?:锂离子|锂聚合物|锂电池|电池|钢壳|扣式|软包|圆柱|聚合物|额定|标称|容量|电压)/g, "")
    .replace(/[\s/\\,，、;；:：·+\-()（）]/g, "");
  return remainder.length === 0;
};
const looksLikeComponentDescription = (row: AnalysisRow) => {
  const normalize = (value: unknown) => text(value).toLocaleLowerCase("zh-CN").replace(/[\s/_-]+/g, "");
  const model = normalize(row.component_model);
  if (!model) return false;
  return GENERIC_MODEL_NAMES.has(model) || [row.component_label, row.component_name].some((value) => normalize(value) === model);
};
const hasReportedModel = (row: AnalysisRow) => {
  const reported = row.model_status ? row.model_status === "reported" : Boolean(text(row.component_model));
  return reported && !looksLikeParameterDescription(row.component_model) && !looksLikeComponentDescription(row);
};
const hasInvalidModelField = (row: AnalysisRow) => row.model_status === "invalid_parameter_value" || looksLikeParameterDescription(row.component_model) || looksLikeComponentDescription(row);
const modelKey = (row: AnalysisRow) => hasReportedModel(row)
  ? text(row.component_model_normalized) || text(row.component_model).replace(/[\s_-]+/g, "").toLocaleLowerCase("zh-CN")
  : "";
const hasResolvedBrand = (row: AnalysisRow) => {
  const brand = text(row.product_brand);
  return Boolean(brand) && !["未知", "未知品牌"].includes(brand) && row.product_brand_status !== "unresolved";
};
const parameterizedProducts = (rows: AnalysisRow[]) => uniqueProducts(rows.filter((row) => (row.parameters || []).some((item) => text(item.label) && text(item.value))));
const reportedModels = (rows: AnalysisRow[]) => {
  const values = new Map<string, string>();
  rows.filter(hasReportedModel).forEach((row) => {
    const key = modelKey(row);
    if (key && !values.has(key)) values.set(key, text(row.component_model));
  });
  return [...values.values()].sort((a, b) => a.localeCompare(b, "zh-CN"));
};
const supplierOf = (row: AnalysisRow) => row.manufacturer_status === "invalid_extraction"
  ? ""
  : text(row.component_manufacturer_canonical || row.component_manufacturer);
const productName = (row: AnalysisRow) => [text(row.product_brand), text(row.product_model)].filter(Boolean).join(" ") || "未知产品";
const yearOf = (row: AnalysisRow) => text(row.source_published_at).slice(0, 4);
const normalizeLookupText = (value: unknown) => text(value)
  .normalize("NFKC")
  .toLocaleLowerCase("zh-CN")
  .replace(/[\s\-_/.,，。·:：;；()（）[\]【】]+/g, "");

function rowsMatchingSearch(rows: AnalysisRow[], query: string) {
  const needle = query.trim().toLocaleLowerCase("zh-CN");
  if (!needle) return rows;
  return rows.filter((row) => [
    supplierOf(row),
    row.component_model,
    row.product_brand,
    row.product_model,
    row.evidence_quote,
    ...featureItems(row).flatMap((item) => [item.label, item.value]),
  ].join(" ").toLocaleLowerCase("zh-CN").includes(needle));
}

function filterComponentRows(
  rows: AnalysisRow[],
  filters: ComponentFilterState,
  omit?: keyof ComponentFilterState,
) {
  const filtered = rows.filter((row) => {
    if (omit !== "category" && filters.category && row.product_category !== filters.category) return false;
    if (omit !== "location" && filters.location && row.usage_location_label !== filters.location) return false;
    if (omit !== "supplier" && filters.supplier && supplierOf(row) !== filters.supplier) return false;
    if (omit !== "brand" && filters.brand && row.product_brand !== filters.brand) return false;
    if (omit !== "year" && filters.year && yearOf(row) !== filters.year) return false;
    return true;
  });
  return omit === "query" ? filtered : rowsMatchingSearch(filtered, filters.query);
}

function facetOptions(rows: AnalysisRow[], valueOf: (row: AnalysisRow) => string, selected = "") {
  const groups = new Map<string, AnalysisRow[]>();
  rows.forEach((row) => {
    const value = text(valueOf(row));
    if (!value) return;
    groups.set(value, [...(groups.get(value) || []), row]);
  });
  const options = [...groups.entries()].map(([value, groupedRows]): FacetOption => ({
    value,
    products: uniqueProducts(groupedRows),
  }));
  if (selected && !groups.has(selected)) options.push({ value: selected, products: 0 });
  return options.sort((a, b) => b.products - a.products || a.value.localeCompare(b.value, "zh-CN"));
}

const NOT_DISCLOSED_MODEL = "__not_disclosed__";
const emptyDetailFilters = (): DetailFilterState => ({
  category: "",
  location: "",
  model: "",
  brand: "",
  year: "",
  parameterState: "",
  query: "",
});
const detailModelValue = (row: AnalysisRow) => hasReportedModel(row) ? modelKey(row) : NOT_DISCLOSED_MODEL;
const detailModelLabel = (row: AnalysisRow) => hasReportedModel(row) ? text(row.component_model) : "型号未披露";

function filterDetailRows(rows: AnalysisRow[], filters: DetailFilterState, omit?: keyof DetailFilterState) {
  const scoped = rows.filter((row) => {
    if (omit !== "category" && filters.category && text(row.product_category) !== filters.category) return false;
    if (omit !== "location" && filters.location && text(row.usage_location_label) !== filters.location) return false;
    if (omit !== "model" && filters.model && detailModelValue(row) !== filters.model) return false;
    if (omit !== "brand" && filters.brand && text(row.product_brand) !== filters.brand) return false;
    if (omit !== "year" && filters.year && yearOf(row) !== filters.year) return false;
    return true;
  });
  const parameterStates = parameterDisclosureStates(scoped);
  return scoped.filter((row) => {
    if (omit !== "parameterState" && filters.parameterState && parameterStates.get(row.product_id) !== filters.parameterState) return false;
    if (omit !== "query" && filters.query.trim()) {
      const needle = filters.query.trim().toLocaleLowerCase("zh-CN");
      const haystack = [
        productName(row), supplierOf(row), detailModelLabel(row), row.evidence_quote,
        ...featureItems(row).flatMap((item) => [item.label, item.value]),
      ].join(" ").toLocaleLowerCase("zh-CN");
      if (!haystack.includes(needle)) return false;
    }
    return true;
  });
}

function parameterDisclosureStates(rows: AnalysisRow[]) {
  const grouped = new Map<string, AnalysisRow[]>();
  rows.forEach((row) => grouped.set(row.product_id, [...(grouped.get(row.product_id) || []), row]));
  const states = new Map<string, "complete" | "partial" | "missing">();
  grouped.forEach((productRows, productId) => {
    const disclosed = productRows.filter((row) => featureItems(row).length > 0).length;
    states.set(productId, disclosed === 0 ? "missing" : disclosed === productRows.length ? "complete" : "partial");
  });
  return states;
}

function parameterDisclosureCounts(rows: AnalysisRow[]) {
  const states = [...parameterDisclosureStates(rows).values()];
  return {
    complete: states.filter((value) => value === "complete").length,
    partial: states.filter((value) => value === "partial").length,
    missing: states.filter((value) => value === "missing").length,
  };
}

function modelStatsOf(rows: AnalysisRow[]) {
  const groups = new Map<string, AnalysisRow[]>();
  rows.filter(hasReportedModel).forEach((row) => {
    const normalizedModel = modelKey(row);
    if (!normalizedModel) return;
    const key = `${supplierOf(row) || "__supplier_unknown__"}\u0000${normalizedModel}`;
    groups.set(key, [...(groups.get(key) || []), row]);
  });
  return [...groups.entries()].map(([key, groupedRows]): ModelStat => {
    const years = unique(groupedRows.map(yearOf));
    return {
      key,
      supplier: supplierOf(groupedRows[0]),
      model: text(groupedRows[0].component_model),
      products: uniqueProducts(groupedRows),
      brands: unique(groupedRows.filter(hasResolvedBrand).map((row) => text(row.product_brand))).length,
      brandNames: unique(groupedRows.filter(hasResolvedBrand).map((row) => text(row.product_brand))),
      categories: unique(groupedRows.map((row) => text(row.product_category))),
      locations: unique(groupedRows.map((row) => text(row.usage_location_label))),
      firstYear: years[0] || "",
      latestYear: years.at(-1) || "",
      parameterizedProducts: parameterizedProducts(groupedRows),
      rows: groupedRows,
    };
  });
}

function observedRange(item: ModelStat) {
  if (!item.firstYear) return "报告日期未披露";
  if (item.firstYear === item.latestYear) return `我爱音频网观察：${item.firstYear}年`;
  return `我爱音频网观察：${item.firstYear}—${item.latestYear}年`;
}

function featureItems(row: AnalysisRow): AnalysisFeature[] {
  const seen = new Set<string>();
  const output: AnalysisFeature[] = [];
  [...(row.parameters || []), ...(row.semantic_features || [])].forEach((item) => {
    const label = text(item.label);
    const value = text(item.value);
    const key = `${label}\u0000${value}`;
    if (!label || !value || seen.has(key)) return;
    seen.add(key);
    output.push({ ...item, label, value });
  });
  return output;
}

function modelDisclosure(models: number, covered: number, products: number) {
  if (!covered) return "型号均未披露 · 仍可作为生产商线索";
  return `明确型号 ${models} 种 · 涉及 ${covered}/${products} 款产品`;
}

function brandDisclosure(brands: number, covered: number, products: number) {
  if (!covered) return "耳机品牌尚未识别";
  return `已识别耳机品牌 ${brands} 个 · 涉及 ${covered}/${products} 款产品`;
}

function parameterDisclosure(covered: number, products: number) {
  if (!covered) return "参数均未披露";
  return `参数披露 ${covered}/${products} 款产品`;
}

function modelDisplay(row: AnalysisRow) {
  if (hasReportedModel(row)) return text(row.component_model);
  if (hasInvalidModelField(row) && text(row.component_model)) {
    return "型号未披露（原字段为参数描述）";
  }
  return "型号未披露";
}

function ScopeChips({ items }: { items: string[][] }) {
  return (
    <div className="sourcing-scope" aria-label="当前筛选范围">
      {items.map(([label, value]) => <span key={label} className={label.startsWith("当前查看") ? "is-drilldown" : ""}><b>{label}</b>{value}</span>)}
    </div>
  );
}

function componentScopeLabels(
  component: string,
  scopeMode: ComponentScopeMode,
  category: string,
  location: string,
  brand: string,
  year: string,
) {
  const values = [
    ["器件", component || "未选择"],
    ["分析范围", scopeMode === "cross" ? "跨耳机类型" : "聚焦某类耳机"],
    ["耳机类型", scopeMode === "cross" ? "全部类型" : category || "待选择"],
    ["使用位置", location || "全部位置"],
    ["时间口径", year || "历史样本累计"],
  ];
  if (brand) values.push(["耳机品牌", brand]);
  return values;
}

function compactPagination(current: number, total: number) {
  const pages = [...new Set([1, total, current - 1, current, current + 1]
    .filter((page) => page >= 1 && page <= total))]
    .sort((a, b) => a - b);
  const items: Array<{ key: string; page?: number }> = [];
  pages.forEach((page, index) => {
    if (index > 0 && page - pages[index - 1] > 1) {
      items.push({ key: `gap-${pages[index - 1]}-${page}` });
    }
    items.push({ key: `page-${page}`, page });
  });
  return items;
}

function ModelLeadGrid({
  items,
  selected,
  onSelect,
  emptyText,
  source,
}: {
  items: ModelStat[];
  selected: SelectedModelLead | null;
  onSelect: (item: ModelStat) => void;
  emptyText: string;
  source: SelectedModelLead["source"];
}) {
  if (!items.length) return <p className="sourcing-empty">{emptyText}</p>;
  return (
    <div className="sourcing-model-grid">
      {items.map((item, index) => {
        const active = selected?.key === item.key && selected.source === source;
        const visibleBrands = item.brandNames.slice(0, 5);
        const remainingBrands = Math.max(0, item.brandNames.length - visibleBrands.length);
        return <button key={item.key} type="button" className={active ? "is-selected" : ""} aria-pressed={active} onClick={() => onSelect(item)}>
          <span>#{index + 1}</span>
          <div className="sourcing-model-primary">
            <small>{item.supplier || "生产商未披露"}</small>
            <strong>{item.model}</strong>
            <b>{item.products} 款耳机产品</b>
            <div className="sourcing-model-brands" aria-label={`已识别品牌：${item.brandNames.join("、") || "无"}`}>
              <small>已识别品牌</small>
              {visibleBrands.length ? <div>{visibleBrands.map((value) => <i key={value}>{value}</i>)}{remainingBrands > 0 && <i>+{remainingBrands}</i>}</div> : <em>耳机品牌尚未识别</em>}
            </div>
          </div>
          <div className="sourcing-model-meta">
            {item.categories.length >= 2 && <mark>跨 {item.categories.length} 类耳机应用</mark>}
            <div className="sourcing-model-tags">{item.categories.map((value) => <i key={value}>{value}</i>)}</div>
            <small>位置：{item.locations.join("、") || "未识别"}</small>
            <small>参数披露 {item.parameterizedProducts}/{item.products} 款</small>
            <small>{observedRange(item)}</small>
            <em>查看应用与证据 →</em>
          </div>
        </button>;
      })}
    </div>
  );
}

function EvidenceTable({
  rows,
  productBase,
  title,
  scope,
  onImage,
}: {
  rows: AnalysisRow[];
  productBase: string;
  title: string;
  scope: string[][];
  onImage: (image: AnalysisImage, row: AnalysisRow) => void;
}) {
  const [sort, setSort] = useState("date_desc");
  const [page, setPage] = useState(1);
  const pageSize = 25;
  const sorted = useMemo(() => [...rows].sort((a, b) => {
    if (sort === "date_asc") return text(a.source_published_at).localeCompare(text(b.source_published_at));
    if (sort === "brand_asc") return productName(a).localeCompare(productName(b), "zh-CN");
    if (sort === "model_asc") return text(a.component_model).localeCompare(text(b.component_model), "zh-CN");
    if (sort === "complete_desc") return featureItems(b).length - featureItems(a).length || text(b.source_published_at).localeCompare(text(a.source_published_at));
    return text(b.source_published_at).localeCompare(text(a.source_published_at)) || productName(a).localeCompare(productName(b), "zh-CN");
  }), [rows, sort]);
  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const visibleRows = sorted.slice((page - 1) * pageSize, page * pageSize);
  useEffect(() => setPage(1), [rows, sort]);

  const exportCsv = () => {
    const header = ["器件类型", "耳机类型", "耳机品牌", "产品", "使用位置", "器件生产商", "器件型号原文", "型号状态", "参数", "来源日期", "来源链接", "证据原文"];
    const body = sorted.map((row) => [
      row.component_label,
      row.product_category,
      row.product_brand,
      row.product_model,
      row.usage_location_label,
      supplierOf(row),
      row.component_model,
      hasInvalidModelField(row) ? "invalid_parameter_value" : (row.model_status || (row.component_model ? "reported" : "not_disclosed")),
      featureItems(row).map((item) => `${item.label}：${item.value}`).join("；"),
      row.source_published_at,
      row.source_report_url,
      row.evidence_quote,
    ]);
    const csv = [header, ...body].map((record) => record.map((value) => `"${text(value).replaceAll('"', '""')}"`).join(",")).join("\r\n");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }));
    link.download = "component-sourcing-evidence.csv";
    link.click();
    URL.revokeObjectURL(link.href);
  };

  return (
    <section className="sourcing-evidence">
      <div className="sourcing-section-title">
        <div><span>Source-backed records</span><h2>{title}</h2></div>
        <div className="sourcing-table-controls">
          <label>排序
            <select value={sort} onChange={(event) => setSort(event.target.value)}>
              <option value="date_desc">来源日期：新到旧</option>
              <option value="date_asc">来源日期：旧到新</option>
              <option value="brand_asc">产品品牌：A-Z</option>
              <option value="model_asc">器件型号：A-Z</option>
              <option value="complete_desc">参数完整度：高到低</option>
            </select>
          </label>
          <button type="button" onClick={exportCsv}>导出当前 CSV</button>
        </div>
      </div>
      <ScopeChips items={scope} />
      <p className="sourcing-caption">共 {sorted.length} 条记录 · {uniqueProducts(sorted)} 款产品；当前显示第 {page}/{pageCount} 页。导出 CSV 会保留全部结果。</p>
      <div className="sourcing-table-wrap">
        <table>
          <thead><tr><th>耳机品牌 / 产品</th><th>器件</th><th>生产商 / 型号</th><th>位置与参数</th><th>图片证据</th><th>报告与文字证据</th></tr></thead>
          <tbody>
            {visibleRows.map((row, index) => {
              const image = row.evidence_image || undefined;
              const imageSrc = text(image?.public_path || image?.url);
              const sourceName = row.source_type === "video" ? "拆解视频" : "拆解报告";
              return (
                <tr key={`${row.row_id}-${index}`}>
                  <td><strong>{text(row.product_brand) || "未知品牌"}</strong><a href={`${productBase}${row.product_id}/`}>{text(row.product_model) || "未知产品"}</a><small>{row.product_category || "类型未识别"}</small></td>
                  <td><strong>{row.component_label || row.component_name || "器件"}</strong><small>{row.component_name || ""}</small></td>
                  <td><strong>{supplierOf(row) || UNKNOWN}</strong><small>{modelDisplay(row)}</small>{hasInvalidModelField(row) && text(row.component_model) && <small>原字段：{row.component_model}</small>}</td>
                  <td><strong>{text(row.usage_location_label) || "位置未识别"}</strong>{featureItems(row).length ? <ul>{featureItems(row).slice(0, 6).map((item, itemIndex) => <li key={`${item.label}-${itemIndex}`}>{item.label}：{item.value}</li>)}</ul> : <small>参数未披露</small>}</td>
                  <td>{imageSrc ? <button className="sourcing-evidence-image" type="button" onClick={() => onImage(image!, row)}><img src={imageSrc} alt={image?.caption || image?.alt || `${row.component_label}图片证据`} loading="lazy" /><span>点击放大</span></button> : <small>暂无对应图片</small>}</td>
                  <td><details><summary>查看证据 · {sourceName}</summary><blockquote>{text(row.evidence_quote) || "证据句未提供"}</blockquote>{row.source_report_url && <a href={row.source_report_url} target="_blank" rel="noreferrer">{row.source_type === "video" ? "查看原视频" : "查看原报告"} ↗</a>}</details><small>{text(row.source_published_at) || "日期未披露"}</small></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {pageCount > 1 && <nav className="sourcing-pagination" aria-label="证据记录分页"><button type="button" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button><span>第 {page} / {pageCount} 页</span><button type="button" disabled={page >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>下一页</button></nav>}
      {!sorted.length && <p className="sourcing-empty">当前筛选条件下没有可追溯记录。</p>}
    </section>
  );
}

function BatterySpecCell({ rows }: { rows: AnalysisRow[] }) {
  const specs = unique(rows.map((row) => {
    const identity = [supplierOf(row) || "生产商未披露", modelDisplay(row)].join(" · ");
    const parameters = featureItems(row).slice(0, 5).map((item) => `${item.label}：${item.value}`).join("；");
    return parameters ? `${identity}\n${parameters}` : `${identity}\n参数未披露`;
  }));
  if (!specs.length) return <small className="sourcing-spec-empty">当前范围未披露</small>;
  return <ul className="sourcing-product-specs">{specs.slice(0, 4).map((spec) => {
    const [identity, parameters] = spec.split("\n");
    return <li key={spec}><strong>{identity}</strong><span>{parameters}</span></li>;
  })}{specs.length > 4 && <li><span>另有 {specs.length - 4} 组报告记录，请在本行“图片与报告证据”中核对</span></li>}</ul>;
}

function BatteryProductMatrix({ rows, productBase, onImage }: { rows: AnalysisRow[]; productBase: string; onImage: (image: AnalysisImage, row: AnalysisRow) => void }) {
  const [page, setPage] = useState(1);
  const pageSize = 25;
  const products = useMemo(() => {
    const groups = new Map<string, AnalysisRow[]>();
    rows.forEach((row) => groups.set(row.product_id, [...(groups.get(row.product_id) || []), row]));
    return [...groups.entries()].sort((a, b) => {
      const dateA = a[1].map((row) => text(row.source_published_at)).sort().at(-1) || "";
      const dateB = b[1].map((row) => text(row.source_published_at)).sort().at(-1) || "";
      return dateB.localeCompare(dateA) || productName(a[1][0]).localeCompare(productName(b[1][0]), "zh-CN");
    });
  }, [rows]);
  const pageCount = Math.max(1, Math.ceil(products.length / pageSize));
  const visibleProducts = products.slice((page - 1) * pageSize, page * pageSize);
  useEffect(() => setPage(1), [rows]);

  return (
    <section className="sourcing-product-matrix">
      <div className="sourcing-section-title">
        <div><span>Product specification & evidence</span><h3>产品规格与证据</h3></div>
        <small>同一产品的耳机与充电盒并排呈现；需要核验时再展开图片和报告原文</small>
      </div>
      <p className="sourcing-caption">共 {products.length} 款产品；每个单元格保留该位置的生产商、型号和成组参数。</p>
      <div className="sourcing-battery-table-wrap">
        <table>
          <thead><tr><th>耳机品牌 / 产品</th><th>耳机电池</th><th>充电盒电池</th><th>位置未识别</th><th>图片与报告证据</th></tr></thead>
          <tbody>{visibleProducts.map(([productId, productRows]) => {
            const sample = productRows[0];
            const counts = sourceCounts(productRows);
            const imageCount = unique(productRows.map((row) => text(row.evidence_image?.public_path || row.evidence_image?.url))).length;
            return <tr key={productId}>
              <td><strong>{text(sample.product_brand) || "未知品牌"}</strong><a href={`${productBase}${productId}/`}>{text(sample.product_model) || "未知产品"}</a><small>{text(sample.product_category) || "类型未识别"} · {text(sample.source_published_at) || "日期未披露"}</small></td>
              <td><BatterySpecCell rows={productRows.filter((row) => text(row.usage_location_label) === "耳机")} /></td>
              <td><BatterySpecCell rows={productRows.filter((row) => text(row.usage_location_label) === "充电盒")} /></td>
              <td><BatterySpecCell rows={productRows.filter((row) => !["耳机", "充电盒"].includes(text(row.usage_location_label)))} /></td>
              <td><details className="sourcing-product-evidence"><summary>查看图片与原文（{productRows.length} 条）</summary><p>{counts.reports} 篇报告 · {counts.videos} 条视频 · {imageCount} 张关联图片</p><div>{productRows.map((row, index) => {
                const image = row.evidence_image;
                return <article key={`${row.row_id}-${index}`}>
                  <div><strong>{text(row.usage_location_label) || "位置未识别"} · {supplierOf(row) || "生产商未披露"}</strong><small>{modelDisplay(row)} · {featureItems(row).map((item) => `${item.label}：${item.value}`).join("；") || "参数未披露"}</small></div>
                  {image?.public_path ? <button type="button" className="sourcing-evidence-image" onClick={() => onImage(image, row)}><img src={image.public_path} alt={text(image.alt) || `${productName(row)} ${row.component_label}证据`} loading="lazy" /><span>点击放大</span></button> : <small>暂无对应图片</small>}
                  <details><summary>查看文字证据</summary><blockquote>{text(row.evidence_quote) || "证据句未提供"}</blockquote>{row.source_report_url && <a href={row.source_report_url} target="_blank" rel="noreferrer">查看{row.source_type === "video" ? "原视频" : "原报告"} ↗</a>}</details>
                </article>;
              })}</div></details></td>
            </tr>;
          })}</tbody>
        </table>
      </div>
      {pageCount > 1 && <nav className="sourcing-pagination" aria-label="产品规格分页"><button type="button" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button><span>第 {page} / {pageCount} 页</span><button type="button" disabled={page >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>下一页</button></nav>}
    </section>
  );
}

function AnnualObservationTrend({
  rows,
  seriesOf,
  seriesUnit,
  title,
  subtitle,
  selectedYear,
  onSelectYear,
}: {
  rows: AnalysisRow[];
  seriesOf: (row: AnalysisRow) => string;
  seriesUnit: string;
  title: string;
  subtitle: string;
  selectedYear: string;
  onSelectYear: (year: string) => void;
}) {
  const analysis = useMemo(() => {
    const validRows = rows.filter((row) => /^\d{4}$/.test(yearOf(row)) && text(seriesOf(row)));
    const years = unique(validRows.map(yearOf));
    const seriesRows = new Map<string, AnalysisRow[]>();
    validRows.forEach((row) => {
      const key = text(seriesOf(row));
      seriesRows.set(key, [...(seriesRows.get(key) || []), row]);
    });
    const series = [...seriesRows.entries()]
      .map(([label, groupedRows]) => ({
        label,
        products: uniqueProducts(groupedRows),
        values: years.map((year) => uniqueProducts(groupedRows.filter((row) => yearOf(row) === year))),
      }))
      .sort((a, b) => b.products - a.products || a.label.localeCompare(b.label, "zh-CN"))
      .slice(0, 5);
    const yearStats = years.map((year) => {
      const yearRows = validRows.filter((row) => yearOf(row) === year);
      const groups = new Map<string, AnalysisRow[]>();
      yearRows.forEach((row) => {
        const key = text(seriesOf(row));
        groups.set(key, [...(groups.get(key) || []), row]);
      });
      const leaders = [...groups.entries()]
        .map(([label, groupedRows]) => ({ label, products: uniqueProducts(groupedRows) }))
        .sort((a, b) => b.products - a.products || a.label.localeCompare(b.label, "zh-CN"));
      return { year, products: uniqueProducts(yearRows), groups: groups.size, leaders };
    });
    return { years, series, yearStats };
  }, [rows, seriesOf]);

  if (!analysis.years.length) {
    return <section className="sourcing-annual"><div className="sourcing-section-title"><div><span>Annual observation</span><h2>{title}</h2></div></div><p className="sourcing-empty">当前范围没有可用于年度观察的报告日期。</p></section>;
  }

  const activeYear = analysis.years.includes(selectedYear) ? selectedYear : analysis.years.at(-1)!;
  const activeYearStat = analysis.yearStats.find((item) => item.year === activeYear)!;
  const maxValue = Math.max(1, ...analysis.series.flatMap((item) => item.values));
  const width = 920;
  const height = 286;
  const padding = { top: 24, right: 24, bottom: 44, left: 48 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const x = (index: number) => padding.left + (analysis.years.length === 1 ? plotWidth / 2 : index / (analysis.years.length - 1) * plotWidth);
  const y = (value: number) => padding.top + plotHeight - value / maxValue * plotHeight;
  const yTicks = unique(["0", String(Math.ceil(maxValue / 2)), String(maxValue)]).map(Number).sort((a, b) => a - b);
  const activeYearIndex = analysis.years.indexOf(activeYear);

  return (
    <section className="sourcing-annual">
      <div className="sourcing-section-title">
        <div><span>Annual observation</span><h2>{title}</h2></div>
        <small>{subtitle}</small>
      </div>
      <div className="sourcing-annual-layout">
        <div className="sourcing-trend-chart">
          <div className="sourcing-trend-legend" aria-label={`图中展示的${seriesUnit}`}>
            {analysis.series.map((item, index) => <span key={item.label}><i style={{ backgroundColor: TREND_COLORS[index] }} />{item.label}</span>)}
          </div>
          <div className="sourcing-trend-scroll">
            <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}，纵轴为每年拆解样本中涉及的去重产品款数`}>
              {yTicks.map((tick) => <g key={tick}><line x1={padding.left} y1={y(tick)} x2={width - padding.right} y2={y(tick)} className="sourcing-trend-grid" /><text x={padding.left - 10} y={y(tick) + 4} textAnchor="end">{tick}</text></g>)}
              {activeYearIndex >= 0 && <rect x={x(activeYearIndex) - 21} y={padding.top} width="42" height={plotHeight} className="sourcing-trend-focus" />}
              {analysis.series.map((item, seriesIndex) => {
                const points = item.values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
                return <g key={item.label}>
                  <polyline points={points} fill="none" stroke={TREND_COLORS[seriesIndex]} strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />
                  {item.values.map((value, index) => <circle key={`${item.label}-${analysis.years[index]}`} cx={x(index)} cy={y(value)} r={analysis.years[index] === activeYear ? 5 : 3.5} fill="#fff" stroke={TREND_COLORS[seriesIndex]} strokeWidth="2"><title>{analysis.years[index]}年 · {item.label} · {value}款产品</title></circle>)}
                </g>;
              })}
              {analysis.years.map((year, index) => <text key={year} x={x(index)} y={height - 15} textAnchor="middle" className={year === activeYear ? "is-active" : ""}>{year}</text>)}
            </svg>
          </div>
          <p>纵轴为当年报告中涉及的唯一产品数；折线只展示当前累计覆盖靠前的 5 个{seriesUnit}。</p>
        </div>
        <aside className="sourcing-year-focus">
          <span>年度切片</span>
          <h3>{activeYear} 年</h3>
          <p>{activeYearStat.products} 款产品 · {activeYearStat.groups} 个{seriesUnit}</p>
          <ol>{activeYearStat.leaders.slice(0, 5).map((item) => <li key={item.label}><strong>{item.label}</strong><span>{item.products} 款</span></li>)}</ol>
        </aside>
      </div>
      <div className="sourcing-year-selector" aria-label="选择年度切片">
        {analysis.yearStats.map((item) => <button key={item.year} type="button" className={item.year === activeYear ? "is-active" : ""} aria-pressed={item.year === activeYear} onClick={() => onSelectYear(item.year)}><strong>{item.year}</strong><span>{item.products} 款产品</span><small>{item.groups} 个{seriesUnit}</small></button>)}
      </div>
      <p className="sourcing-trend-note">这是我爱音频网拆解样本的年度观察，不代表市场份额、实际采购量或供应商出货排名。</p>
    </section>
  );
}

function SupplierRankingExplorer({
  rows,
  componentLabel,
  scope,
  selectedYear,
  onSelectYear,
  highlightedSupplier,
  onSelectSupplier,
}: {
  rows: AnalysisRow[];
  componentLabel: string;
  scope: string[][];
  selectedYear: string;
  onSelectYear: (year: string) => void;
  highlightedSupplier: string;
  onSelectSupplier: (supplier: string) => void;
}) {
  const analysis = useMemo(() => {
    const validRows = rows.filter((row) => supplierOf(row));
    const supplierGroups = new Map<string, AnalysisRow[]>();
    validRows.forEach((row) => {
      const supplier = supplierOf(row);
      supplierGroups.set(supplier, [...(supplierGroups.get(supplier) || []), row]);
    });
    const cumulative = [...supplierGroups.entries()].map(([supplier, groupedRows]) => ({
      supplier,
      products: uniqueProducts(groupedRows),
      categories: unique(groupedRows.map((row) => text(row.product_category))),
    })).sort((a, b) => b.products - a.products || a.supplier.localeCompare(b.supplier, "zh-CN"));
    const years = unique(validRows.map(yearOf).filter((value) => /^\d{4}$/.test(value)));
    const yearly = years.map((year) => {
      const groups = new Map<string, AnalysisRow[]>();
      validRows.filter((row) => yearOf(row) === year).forEach((row) => {
        const supplier = supplierOf(row);
        groups.set(supplier, [...(groups.get(supplier) || []), row]);
      });
      const rankings = [...groups.entries()].map(([supplier, groupedRows]) => ({
        supplier,
        products: uniqueProducts(groupedRows),
      })).sort((a, b) => b.products - a.products || a.supplier.localeCompare(b.supplier, "zh-CN"));
      return { year, products: uniqueProducts(validRows.filter((row) => yearOf(row) === year)), rankings };
    });
    return { cumulative, years, yearly, validRows };
  }, [rows]);

  if (!analysis.cumulative.length) {
    return <section className="sourcing-rankings"><p className="sourcing-empty">当前筛选范围没有披露可确认的供应商名称。</p></section>;
  }

  const activeYear = analysis.years.includes(selectedYear) ? selectedYear : analysis.years.at(-1) || "";
  const activeYearIndex = analysis.years.indexOf(activeYear);
  const activeYearStat = analysis.yearly.find((item) => item.year === activeYear);
  const previousYearStat = activeYearIndex > 0 ? analysis.yearly[activeYearIndex - 1] : undefined;
  const previousRanks = new Map(previousYearStat?.rankings.slice(0, 5).map((item, index) => [item.supplier, index + 1]) || []);
  const cumulativeTop = analysis.cumulative.slice(0, 10);
  const annualTop = activeYearStat?.rankings.slice(0, 5) || [];
  const cumulativeMax = Math.max(1, ...cumulativeTop.map((item) => item.products));
  const annualMax = Math.max(1, ...annualTop.map((item) => item.products));
  const highlightedRank = highlightedSupplier
    ? analysis.cumulative.findIndex((item) => item.supplier === highlightedSupplier) + 1
    : 0;
  const highlightedItem = highlightedRank > 0 ? analysis.cumulative[highlightedRank - 1] : undefined;

  const movementLabel = (supplier: string, currentRank: number) => {
    const previousRank = previousRanks.get(supplier);
    if (!previousYearStat) return "首个可比年份";
    if (!previousRank) return "上年未进入榜单";
    const change = previousRank - currentRank;
    if (change > 0) return `较上年 ↑${change}`;
    if (change < 0) return `较上年 ↓${Math.abs(change)}`;
    return "与上年持平";
  };

  return (
    <section className="sourcing-rankings">
      <div className="sourcing-section-title">
        <div><span>Supplier observation</span><h2>{componentLabel}供应商样本观察</h2></div>
        <small>供应商选择只用于高亮，不会把比较榜单收缩到一家</small>
      </div>
      <div className="sourcing-ranking-warning" role="note"><strong>请按样本范围解读</strong><span>仅为我爱音频网拆解样本观察，不代表市场份额、采购量或供应商出货排名。</span></div>
      <ScopeChips items={scope} />
      <div className="sourcing-ranking-grid">
        <article>
          <header><div><span>All-period TOP 10</span><h3>历史累计供应商排名</h3></div><small>按涉及的唯一产品款数排序</small></header>
          <div className="sourcing-ranked-bars">
            {cumulativeTop.map((item, index) => <button key={item.supplier} type="button" className={highlightedSupplier === item.supplier ? "is-highlighted" : ""} aria-pressed={highlightedSupplier === item.supplier} onClick={() => onSelectSupplier(item.supplier)}><b>#{index + 1}</b><strong>{item.supplier}</strong><i><span style={{ width: `${item.products / cumulativeMax * 100}%` }} /></i><em>{item.products} 款</em><small>{item.categories.length} 类耳机</small></button>)}
          </div>
          {highlightedItem && highlightedRank > 10 && <button className="sourcing-outside-rank" type="button" onClick={() => onSelectSupplier(highlightedItem.supplier)}>已选供应商：#{highlightedRank} {highlightedItem.supplier} · {highlightedItem.products} 款产品</button>}
        </article>
        <article>
          <header><div><span>Annual TOP 5</span><h3>{activeYear || "年度"} 年供应商排名</h3></div><small>当年真实前五，不沿用累计榜名单</small></header>
          <div className="sourcing-year-selector" aria-label="选择年度供应商排名">{analysis.yearly.map((item) => <button key={item.year} type="button" className={item.year === activeYear ? "is-active" : ""} aria-pressed={item.year === activeYear} onClick={() => onSelectYear(item.year)}><strong>{item.year}</strong><span>{item.products} 款产品</span><small>{item.rankings.length} 个供应商名称</small></button>)}</div>
          <div className="sourcing-ranked-bars sourcing-ranked-bars--annual">
            {annualTop.map((item, index) => <button key={item.supplier} type="button" className={highlightedSupplier === item.supplier ? "is-highlighted" : ""} aria-pressed={highlightedSupplier === item.supplier} onClick={() => onSelectSupplier(item.supplier)}><b>#{index + 1}</b><strong>{item.supplier}</strong><i><span style={{ width: `${item.products / annualMax * 100}%` }} /></i><em>{item.products} 款</em><small>{movementLabel(item.supplier, index + 1)}</small></button>)}
          </div>
        </article>
      </div>
      <p className="sourcing-ranking-note">统计范围会响应器件类型、耳机类型、使用位置、耳机品牌和关键词条件。一个产品在同一供应商下只计一次；同一产品若披露多个供应商，会分别计入对应供应商。</p>
    </section>
  );
}

export default function ComponentSourcingExplorer({
  manifestUrl,
  componentDataBase,
  supplierManifestUrl,
  supplierDataBase,
  productBase,
}: Props) {
  const [mode, setMode] = useState<Mode>("component");
  const [manifest, setManifest] = useState<ComponentManifest | null>(null);
  const [supplierManifest, setSupplierManifest] = useState<SupplierManifest | null>(null);
  const [loadError, setLoadError] = useState("");

  const [componentKey, setComponentKey] = useState("");
  const [componentPayload, setComponentPayload] = useState<ComponentPayload | null>(null);
  const [componentLoading, setComponentLoading] = useState(false);
  const [componentScopeMode, setComponentScopeMode] = useState<ComponentScopeMode>("cross");
  const [category, setCategory] = useState("");
  const [location, setLocation] = useState("");
  const [componentBrand, setComponentBrand] = useState("");
  const [componentYear, setComponentYear] = useState("");
  const [year, setYear] = useState("");
  const [query, setQuery] = useState("");
  const [selectedCandidate, setSelectedCandidate] = useState("");
  const [selectedModelLead, setSelectedModelLead] = useState<SelectedModelLead | null>(null);
  const [filterDrawerOpen, setFilterDrawerOpen] = useState(false);
  const [candidateQuery, setCandidateQuery] = useState("");
  const [candidateSort, setCandidateSort] = useState<"coverage" | "name">("coverage");
  const [candidatePage, setCandidatePage] = useState(1);
  const [componentEvidenceOpen, setComponentEvidenceOpen] = useState(false);
  const [detailFiltersOpen, setDetailFiltersOpen] = useState(false);
  const [showAllModels, setShowAllModels] = useState(false);
  const [modelLeadQuery, setModelLeadQuery] = useState("");
  const [componentResultView, setComponentResultView] = useState<ComponentResultView>("supplier");
  const [detailFilters, setDetailFilters] = useState<DetailFilterState>(emptyDetailFilters);

  const [supplierQuery, setSupplierQuery] = useState("");
  const [supplierPage, setSupplierPage] = useState(1);
  const [selectedSupplierKey, setSelectedSupplierKey] = useState("");
  const [supplierPayload, setSupplierPayload] = useState<SupplierPayload | null>(null);
  const [supplierLoading, setSupplierLoading] = useState(false);
  const [supplierCategory, setSupplierCategory] = useState("");
  const [supplierLocation, setSupplierLocation] = useState("");
  const [supplierYear, setSupplierYear] = useState("");
  const [selectedSupplierComponent, setSelectedSupplierComponent] = useState("");
  const [supplierTreeCategory, setSupplierTreeCategory] = useState("");
  const [supplierTreeBrandQuery, setSupplierTreeBrandQuery] = useState("");
  const [imagePreview, setImagePreview] = useState<{ image: AnalysisImage; row: AnalysisRow } | null>(null);
  const componentResultsRef = useRef<HTMLElement | null>(null);
  const componentDetailRef = useRef<HTMLElement | null>(null);
  const componentDetailHeadingRef = useRef<HTMLHeadingElement | null>(null);

  // 首页使用指南可以直接进入指定研究路径；页面内切换时同步 URL，方便复制链接和刷新恢复。
  useEffect(() => {
    const requestedMode = new URLSearchParams(window.location.search).get("mode");
    if (requestedMode === "supplier" || requestedMode === "component") setMode(requestedMode);
  }, []);

  const selectMode = (nextMode: Mode) => {
    setMode(nextMode);
    const url = new URL(window.location.href);
    url.searchParams.set("mode", nextMode);
    url.hash = "";
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  };

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetch(manifestUrl, { cache: "no-store" }).then((response) => {
        if (!response.ok) throw new Error(`器件索引加载失败：${response.status}`);
        return response.json() as Promise<ComponentManifest>;
      }),
      fetch(supplierManifestUrl, { cache: "no-store" }).then((response) => {
        if (!response.ok) throw new Error(`供应商索引加载失败：${response.status}`);
        return response.json() as Promise<SupplierManifest>;
      }),
    ]).then(([componentData, supplierData]) => {
      if (cancelled) return;
      setManifest(componentData);
      setSupplierManifest(supplierData);
    }).catch((error) => {
      console.error(error);
      if (!cancelled) setLoadError("分析数据加载失败，请先运行数据准备脚本。");
    });
    return () => { cancelled = true; };
  }, [manifestUrl, supplierManifestUrl]);

  useEffect(() => {
    if (!componentKey) {
      setComponentPayload(null);
      return;
    }
    let cancelled = false;
    setComponentLoading(true);
    fetch(`${componentDataBase}${componentKey}.json`, { cache: "no-store" }).then((response) => {
      if (!response.ok) throw new Error(`器件数据加载失败：${response.status}`);
      return response.json() as Promise<ComponentPayload>;
    }).then((payload) => {
      if (cancelled) return;
      setComponentPayload(payload);
      setComponentScopeMode("cross"); setCategory(""); setLocation(""); setComponentBrand(""); setComponentYear(""); setYear(""); setQuery(""); setSelectedCandidate(""); setSelectedModelLead(null); setComponentResultView("supplier"); setFilterDrawerOpen(false); setCandidateQuery(""); setCandidatePage(1); setComponentEvidenceOpen(false); setDetailFiltersOpen(false); setShowAllModels(false); setModelLeadQuery(""); setDetailFilters(emptyDetailFilters());
    }).catch((error) => {
      console.error(error);
      if (!cancelled) setLoadError("所选器件数据加载失败。");
    }).finally(() => { if (!cancelled) setComponentLoading(false); });
    return () => { cancelled = true; };
  }, [componentKey, componentDataBase]);

  useEffect(() => {
    if (!selectedSupplierKey) {
      setSupplierPayload(null);
      return;
    }
    let cancelled = false;
    setSupplierLoading(true);
    fetch(`${supplierDataBase}${selectedSupplierKey}.json`, { cache: "no-store" }).then((response) => {
      if (!response.ok) throw new Error(`供应商数据加载失败：${response.status}`);
      return response.json() as Promise<SupplierPayload>;
    }).then((payload) => {
      if (cancelled) return;
      setSupplierPayload(payload);
      setSupplierCategory(""); setSupplierLocation(""); setSupplierYear("");
      setSupplierTreeCategory(""); setSupplierTreeBrandQuery("");
      const counts = new Map<string, Set<string>>();
      payload.rows.forEach((row) => {
        if (!counts.has(row.component_key)) counts.set(row.component_key, new Set());
        counts.get(row.component_key)!.add(row.product_id);
      });
      const first = [...counts.entries()].sort((a, b) => b[1].size - a[1].size)[0]?.[0] || "";
      setSelectedSupplierComponent(first);
    }).catch((error) => {
      console.error(error);
      if (!cancelled) setLoadError("所选供应商数据加载失败。");
    }).finally(() => { if (!cancelled) setSupplierLoading(false); });
    return () => { cancelled = true; };
  }, [selectedSupplierKey, supplierDataBase]);

  const componentFilters = useMemo<ComponentFilterState>(() => ({
    category: componentScopeMode === "focused" ? category : "",
    location,
    supplier: "",
    brand: componentBrand,
    year: componentYear,
    query,
  }), [componentScopeMode, category, location, componentBrand, componentYear, query]);
  const rawComponentRows = componentPayload?.rows || [];
  const componentRows = useMemo(() => filterComponentRows(rawComponentRows, componentFilters), [componentPayload, componentFilters]);
  const componentRankingRows = useMemo(() => filterComponentRows(rawComponentRows, { ...componentFilters, supplier: "" }), [componentPayload, componentFilters]);
  const componentBaseRows = useMemo(() => filterComponentRows(rawComponentRows, componentFilters, "category"), [componentPayload, componentFilters]);
  const categoryFacetRows = componentBaseRows;
  const locationFacetRows = useMemo(() => filterComponentRows(rawComponentRows, componentFilters, "location"), [componentPayload, componentFilters]);
  const brandFacetRows = useMemo(() => filterComponentRows(rawComponentRows, componentFilters, "brand"), [componentPayload, componentFilters]);
  const yearFacetRows = useMemo(() => filterComponentRows(rawComponentRows, componentFilters, "year"), [componentPayload, componentFilters]);
  const componentCategories = useMemo(() => facetOptions(categoryFacetRows, (row) => text(row.product_category), category), [categoryFacetRows, category]);
  const componentLocations = useMemo(() => facetOptions(locationFacetRows, (row) => text(row.usage_location_label), location), [locationFacetRows, location]);
  const componentBrands = useMemo(() => facetOptions(brandFacetRows.filter(hasResolvedBrand), (row) => text(row.product_brand), componentBrand), [brandFacetRows, componentBrand]);
  const componentYears = useMemo(() => facetOptions(yearFacetRows, yearOf, componentYear), [yearFacetRows, componentYear]);
  const componentScopeReady = Boolean(componentPayload && (componentScopeMode === "cross" || category));

  const supplierStats = useMemo<SupplierStat[]>(() => {
    const groups = new Map<string, AnalysisRow[]>();
    componentRows.forEach((row) => {
      const supplier = supplierOf(row);
      if (!supplier) return;
      const current = groups.get(supplier) || [];
      current.push(row); groups.set(supplier, current);
    });
    return [...groups.entries()].map(([supplier, rows]) => {
      const modelKnownProductIds = new Set(rows.filter(hasReportedModel).map((row) => row.product_id));
      return {
        supplier,
        products: uniqueProducts(rows),
        models: unique(rows.map(modelKey)).length,
        brands: unique(rows.filter(hasResolvedBrand).map((row) => text(row.product_brand))).length,
        brandNames: unique(rows.filter(hasResolvedBrand).map((row) => text(row.product_brand))),
        categories: unique(rows.map((row) => text(row.product_category))),
        modelKnownProducts: modelKnownProductIds.size,
        supplierOnlyProducts: uniqueProducts(rows.filter((row) => !modelKnownProductIds.has(row.product_id))),
        brandKnownProducts: uniqueProducts(rows.filter(hasResolvedBrand)),
        parameterizedProducts: parameterizedProducts(rows),
        rows: rows.length,
      };
    }).sort((a, b) => b.products - a.products || b.models - a.models || a.supplier.localeCompare(b.supplier, "zh-CN"));
  }, [componentRows]);

  const visibleSupplierStats = useMemo(() => {
    const needle = candidateQuery.trim().toLocaleLowerCase("zh-CN");
    const matched = supplierStats.filter((item) => !needle || item.supplier.toLocaleLowerCase("zh-CN").includes(needle));
    if (candidateSort === "name") return [...matched].sort((a, b) => a.supplier.localeCompare(b.supplier, "zh-CN"));
    return matched;
  }, [supplierStats, candidateQuery, candidateSort]);
  const candidatePageSize = 6;
  const candidatePageCount = Math.max(1, Math.ceil(visibleSupplierStats.length / candidatePageSize));
  const pagedSupplierStats = visibleSupplierStats.slice((candidatePage - 1) * candidatePageSize, candidatePage * candidatePageSize);
  const selectedSupplierStat = selectedCandidate && selectedCandidate !== ALL_EVIDENCE
    ? supplierStats.find((item) => item.supplier === selectedCandidate)
    : undefined;

  const candidateRows = useMemo(() => selectedCandidate === ALL_EVIDENCE ? componentRows : componentRows.filter((row) => supplierOf(row) === selectedCandidate), [componentRows, selectedCandidate]);
  const commonModelStats = useMemo(() => modelStatsOf(componentRows).sort((a, b) => b.products - a.products || b.brands - a.brands || b.latestYear.localeCompare(a.latestYear) || a.model.localeCompare(b.model, "zh-CN")), [componentRows]);
  const reusableModelStats = useMemo(() => commonModelStats.filter((item) => item.products >= 2), [commonModelStats]);
  const singleProductModelCount = commonModelStats.length - reusableModelStats.length;
  const modelLeadNeedle = normalizeLookupText(modelLeadQuery);
  const searchedModelStats = useMemo(() => {
    if (!modelLeadNeedle) return [];
    return commonModelStats.filter((item) => normalizeLookupText(`${item.supplier} ${item.model}`).includes(modelLeadNeedle));
  }, [commonModelStats, modelLeadNeedle]);
  const searchedReusableModelStats = searchedModelStats.filter((item) => item.products >= 2);
  const searchedSingleProductModelStats = searchedModelStats.filter((item) => item.products === 1);
  const displayedReusableModelStats = modelLeadNeedle
    ? searchedReusableModelStats
    : showAllModels ? reusableModelStats : reusableModelStats.slice(0, 9);
  const quarantinedReuseRows = useMemo(() => componentBaseRows.filter((row) => hasInvalidModelField(row) || (hasReportedModel(row) && (!TRUSTED_CATEGORIES.has(text(row.product_category)) || !TRUSTED_LOCATIONS.has(text(row.usage_location_label))))), [componentBaseRows]);
  const selectedModelRows = useMemo(() => {
    if (!selectedModelLead) return [];
    const match = commonModelStats.find((item) => item.key === selectedModelLead.key);
    if (!match) return [];
    return match.rows;
  }, [selectedModelLead, commonModelStats]);
  const detailBaseRows = selectedModelLead ? selectedModelRows : candidateRows;
  const detailRows = useMemo(() => filterDetailRows(detailBaseRows, detailFilters), [detailBaseRows, detailFilters]);
  const detailCategoryRows = useMemo(() => filterDetailRows(detailBaseRows, detailFilters, "category"), [detailBaseRows, detailFilters]);
  const detailLocationRows = useMemo(() => filterDetailRows(detailBaseRows, detailFilters, "location"), [detailBaseRows, detailFilters]);
  const detailModelRows = useMemo(() => filterDetailRows(detailBaseRows, detailFilters, "model"), [detailBaseRows, detailFilters]);
  const detailBrandRows = useMemo(() => filterDetailRows(detailBaseRows, detailFilters, "brand"), [detailBaseRows, detailFilters]);
  const detailYearRows = useMemo(() => filterDetailRows(detailBaseRows, detailFilters, "year"), [detailBaseRows, detailFilters]);
  const detailParameterRows = useMemo(() => filterDetailRows(detailBaseRows, detailFilters, "parameterState"), [detailBaseRows, detailFilters]);
  const detailCategories = useMemo(() => facetOptions(detailCategoryRows, (row) => text(row.product_category), detailFilters.category), [detailCategoryRows, detailFilters.category]);
  const detailLocations = useMemo(() => facetOptions(detailLocationRows, (row) => text(row.usage_location_label), detailFilters.location), [detailLocationRows, detailFilters.location]);
  const detailBrands = useMemo(() => facetOptions(detailBrandRows.filter(hasResolvedBrand), (row) => text(row.product_brand), detailFilters.brand), [detailBrandRows, detailFilters.brand]);
  const detailYears = useMemo(() => facetOptions(detailYearRows, yearOf, detailFilters.year), [detailYearRows, detailFilters.year]);
  const detailModels = useMemo(() => {
    const labels = new Map<string, string>();
    detailBaseRows.forEach((row) => labels.set(detailModelValue(row), detailModelLabel(row)));
    return facetOptions(detailModelRows, detailModelValue, detailFilters.model).map((item) => ({ ...item, label: labels.get(item.value) || item.value }));
  }, [detailBaseRows, detailModelRows, detailFilters.model]);
  const detailParameterCounts = parameterDisclosureCounts(detailParameterRows);
  const activeDetailFilterCount = Object.values(detailFilters).filter((value) => Boolean(text(value))).length;
  const currentSources = sourceCounts(componentRows);
  const currentBrandNames = unique(componentRows.filter(hasResolvedBrand).map((row) => text(row.product_brand))).length;

  const matchedSupplierOptions = useMemo(() => {
    const needle = supplierQuery.trim().toLocaleLowerCase("zh-CN");
    return (supplierManifest?.suppliers || []).filter((item) => !needle || item.supplier.toLocaleLowerCase("zh-CN").includes(needle));
  }, [supplierManifest, supplierQuery]);
  const supplierPageSize = 8;
  const supplierPageCount = Math.max(1, Math.ceil(matchedSupplierOptions.length / supplierPageSize));
  const supplierOptions = matchedSupplierOptions.slice((supplierPage - 1) * supplierPageSize, supplierPage * supplierPageSize);
  const supplierPageItems = compactPagination(supplierPage, supplierPageCount);

  const supplierCategories = useMemo(() => unique((supplierPayload?.rows || []).map((row) => text(row.product_category))), [supplierPayload]);
  const supplierLocations = useMemo(() => unique((supplierPayload?.rows || []).map((row) => text(row.usage_location_label))), [supplierPayload]);
  const supplierRows = useMemo(() => (supplierPayload?.rows || []).filter((row) => {
    if (supplierCategory && row.product_category !== supplierCategory) return false;
    if (supplierLocation && row.usage_location_label !== supplierLocation) return false;
    return true;
  }), [supplierPayload, supplierCategory, supplierLocation]);

  const componentStats = useMemo<ComponentStat[]>(() => {
    const groups = new Map<string, AnalysisRow[]>();
    supplierRows.forEach((row) => {
      const current = groups.get(row.component_key) || [];
      current.push(row); groups.set(row.component_key, current);
    });
    return [...groups.entries()].map(([key, rows]) => ({
      key,
      label: rows[0]?.component_label || key,
      products: uniqueProducts(rows),
      models: unique(rows.map(modelKey)).length,
      brands: unique(rows.filter(hasResolvedBrand).map((row) => text(row.product_brand))).length,
      modelKnownProducts: uniqueProducts(rows.filter(hasReportedModel)),
      brandKnownProducts: uniqueProducts(rows.filter(hasResolvedBrand)),
      rows: rows.length,
    })).sort((a, b) => b.products - a.products || a.label.localeCompare(b.label, "zh-CN"));
  }, [supplierRows]);

  useEffect(() => {
    if (!componentStats.length) setSelectedSupplierComponent("");
    else if (!componentStats.some((item) => item.key === selectedSupplierComponent)) setSelectedSupplierComponent(componentStats[0].key);
  }, [componentStats, selectedSupplierComponent]);

  useEffect(() => {
    setSupplierTreeCategory("");
    setSupplierTreeBrandQuery("");
  }, [selectedSupplierComponent, supplierCategory, supplierLocation]);

  const supplierComponentRows = useMemo(() => supplierRows.filter((row) => row.component_key === selectedSupplierComponent), [supplierRows, selectedSupplierComponent]);
  const supplierTreeCategories = useMemo(() => unique(supplierComponentRows.map((row) => text(row.product_category))), [supplierComponentRows]);
  const supplierTreeBrands = useMemo(() => unique(supplierComponentRows.filter(hasResolvedBrand).map((row) => text(row.product_brand))), [supplierComponentRows]);
  const supplierTreeRows = useMemo(() => {
    const brandNeedle = supplierTreeBrandQuery.trim().toLocaleLowerCase("zh-CN");
    return supplierComponentRows.filter((row) => {
      if (supplierTreeCategory && text(row.product_category) !== supplierTreeCategory) return false;
      if (brandNeedle && !text(row.product_brand).toLocaleLowerCase("zh-CN").includes(brandNeedle)) return false;
      return true;
    });
  }, [supplierComponentRows, supplierTreeCategory, supplierTreeBrandQuery]);
  const supplierTree = useMemo(() => {
    const categories = new Map<string, Map<string, AnalysisRow[]>>();
    supplierTreeRows.forEach((row) => {
      const categoryName = text(row.product_category) || "类型未识别";
      const brand = text(row.product_brand) || "未知品牌";
      if (!categories.has(categoryName)) categories.set(categoryName, new Map());
      const brands = categories.get(categoryName)!;
      const current = brands.get(brand) || [];
      current.push(row); brands.set(brand, current);
    });
    return [...categories.entries()].sort((a, b) => uniqueProducts(b[1] ? [...b[1].values()].flat() : []) - uniqueProducts(a[1] ? [...a[1].values()].flat() : []));
  }, [supplierTreeRows]);

  const selectedComponentLabel = manifest?.components.find((item) => item.key === componentKey)?.label || componentPayload?.component_label || "";
  const selectedSupplierComponentLabel = componentStats.find((item) => item.key === selectedSupplierComponent)?.label || "";
  const rankingScope = componentScopeLabels(selectedComponentLabel, componentScopeMode, category, location, componentBrand, componentYear);
  const selectedModelStat = selectedModelLead
    ? commonModelStats.find((item) => item.key === selectedModelLead.key)
    : undefined;
  const currentScopeLabels: string[][] = [...rankingScope];
  if (selectedCandidate === ALL_EVIDENCE) currentScopeLabels.push(["当前查看", "范围内全部产品与证据"]);
  else if (selectedCandidate) currentScopeLabels.push(["当前查看供应商", selectedCandidate]);
  if (selectedModelStat) currentScopeLabels.push(["当前查看型号", `${selectedModelStat.supplier || "生产商未披露"} · ${selectedModelStat.model}`]);
  const hasComponentDetail = Boolean(selectedCandidate || selectedModelLead);
  const showComponentDetail = hasComponentDetail && componentEvidenceOpen;
  const componentDetailTitle = selectedModelStat
    ? `${selectedModelStat.supplier || "生产商未披露"} · ${selectedModelStat.model} · 应用与报告证据`
    : selectedCandidate === ALL_EVIDENCE
      ? `${selectedComponentLabel} · 当前范围全部报告证据`
      : `${selectedCandidate} · 型号、产品与报告证据`;
  const detailScopeLabels = [
    ...(detailFilters.category ? [["二级耳机类型", detailFilters.category]] : []),
    ...(detailFilters.location ? [["二级使用位置", detailFilters.location]] : []),
    ...(detailFilters.model ? [["二级器件型号", detailModels.find((item) => item.value === detailFilters.model)?.label || detailFilters.model]] : []),
    ...(detailFilters.brand ? [["二级耳机品牌", detailFilters.brand]] : []),
    ...(detailFilters.year ? [["二级报告年份", detailFilters.year]] : []),
    ...(detailFilters.parameterState ? [["参数披露", ({ complete: "全部位置已披露", partial: "部分位置披露", missing: "全部未披露" } as Record<string, string>)[detailFilters.parameterState] || detailFilters.parameterState]] : []),
  ];
  const componentDetailScope = selectedModelStat
    ? [...componentScopeLabels(selectedComponentLabel, componentScopeMode, category, location, componentBrand, componentYear), ["器件型号", selectedModelStat.model]]
    : [...componentScopeLabels(selectedComponentLabel, componentScopeMode, category, location, componentBrand, componentYear), ...(selectedCandidate && selectedCandidate !== ALL_EVIDENCE ? [["证据下钻", selectedCandidate]] : [])];
  componentDetailScope.push(...detailScopeLabels);

  const scrollBehavior = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" as const : "smooth" as const;
  const returnToComponentResults = () => {
    if (window.location.hash === "#component-evidence") window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    window.setTimeout(() => componentResultsRef.current?.scrollIntoView({ behavior: scrollBehavior(), block: "start" }), 0);
  };

  useEffect(() => {
    if (!showComponentDetail) return;
    const timer = window.setTimeout(() => {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#component-evidence`);
      componentDetailRef.current?.scrollIntoView({ behavior: scrollBehavior(), block: "start" });
      componentDetailHeadingRef.current?.focus({ preventScroll: true });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [showComponentDetail, selectedCandidate, selectedModelLead?.key]);

  useEffect(() => {
    setDetailFilters(emptyDetailFilters());
    setDetailFiltersOpen(false);
  }, [selectedCandidate, selectedModelLead?.key]);

  useEffect(() => {
    setCandidatePage(1);
  }, [candidateQuery, candidateSort, componentRows]);

  useEffect(() => {
    setSupplierPage(1);
  }, [supplierQuery]);

  useEffect(() => {
    if (supplierPage > supplierPageCount) setSupplierPage(supplierPageCount);
  }, [supplierPage, supplierPageCount]);

  useEffect(() => {
    if (candidatePage > candidatePageCount) setCandidatePage(candidatePageCount);
  }, [candidatePage, candidatePageCount]);

  useEffect(() => {
    if (!filterDrawerOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFilterDrawerOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [filterDrawerOpen]);

  useEffect(() => {
    if (componentResultView !== "supplier" || !componentScopeReady || selectedModelLead || selectedCandidate === ALL_EVIDENCE) return;
    if (!supplierStats.length) {
      if (selectedCandidate) setSelectedCandidate("");
      return;
    }
    if (!supplierStats.some((item) => item.supplier === selectedCandidate)) {
      setSelectedCandidate(supplierStats[0].supplier);
      setComponentEvidenceOpen(false);
    }
  }, [componentResultView, componentScopeReady, selectedModelLead, selectedCandidate, supplierStats]);

  return (
    <div className="component-analysis sourcing-explorer">
      <section className="component-analysis__hero sourcing-hero sourcing-hero--intelligence">
        <div className="sourcing-hero__copy">
          <p className="home-eyebrow">Procurement intelligence</p>
          <h1>从器件线索，追到供应商与产品证据</h1>
          <p>选择一个器件类型，沿着“供应商—品牌—产品—拆解证据”逐层定位可联系、可核验的竞品应用。</p>
          <div className="sourcing-hero__tags"><span>产品样本去重</span><span>原文与图片可追溯</span><span>不等同市场份额</span></div>
        </div>
        <div className="sourcing-hero__radar" aria-label="器件连接供应商、品牌、产品、参数、图片与拆解报告的情报关系图">
          <svg viewBox="0 0 520 300" role="img" aria-labelledby="sourcing-radar-title sourcing-radar-desc">
            <title id="sourcing-radar-title">器件供应链情报关系</title>
            <desc id="sourcing-radar-desc">从器件出发，连接供应商、耳机品牌、产品、参数、图片和拆解报告。</desc>
            <circle className="sourcing-radar__ring" cx="260" cy="150" r="54" />
            <circle className="sourcing-radar__ring" cx="260" cy="150" r="104" />
            <circle className="sourcing-radar__ring" cx="260" cy="150" r="142" />
            <path className="sourcing-radar__link" d="M260 150 L132 82 M260 150 L388 78 M260 150 L420 173 M260 150 L343 260 M260 150 L153 252 M260 150 L92 166" />
            <path className="sourcing-radar__link is-accent" d="M132 82 L72 39 M132 82 L58 121 M388 78 L458 38 M420 173 L487 197 M343 260 L404 280 M153 252 L92 278" />
            <g className="sourcing-radar__pulse"><circle className="sourcing-radar__node is-center" cx="260" cy="150" r="42" /><text className="is-center" x="260" y="150">{selectedComponentLabel || "器件"}</text></g>
            <g className="sourcing-radar__node-group"><circle className="sourcing-radar__node" cx="132" cy="82" r="36" /><text x="132" y="82">供应商</text></g>
            <g className="sourcing-radar__node-group"><circle className="sourcing-radar__node" cx="388" cy="78" r="34" /><text x="388" y="78">品牌</text></g>
            <g className="sourcing-radar__node-group"><circle className="sourcing-radar__node" cx="420" cy="173" r="36" /><text x="420" y="173">产品</text></g>
            <g className="sourcing-radar__node-group"><circle className="sourcing-radar__node" cx="343" cy="260" r="34" /><text x="343" y="260">参数</text></g>
            <g className="sourcing-radar__node-group"><circle className="sourcing-radar__node" cx="153" cy="252" r="34" /><text x="153" y="252">图片</text></g>
            <g className="sourcing-radar__node-group"><circle className="sourcing-radar__node" cx="92" cy="166" r="36" /><text x="92" y="166">报告</text></g>
          </svg>
        </div>
      </section>

      <nav className="sourcing-path-tabs" aria-label="器件采购探索路径" role="tablist">
        <button type="button" role="tab" className={mode === "component" ? "is-active" : ""} aria-selected={mode === "component"} onClick={() => selectMode("component")}><strong>按器件找供应商</strong><small>选择器件类型，探索常见型号、跨类型复用与供应商线索</small></button>
        <button type="button" role="tab" className={mode === "supplier" ? "is-active" : ""} aria-selected={mode === "supplier"} onClick={() => selectMode("supplier")}><strong>按供应商看能力</strong><small>先选择供应商，再展开器件、品牌与产品关系</small></button>
      </nav>

      {loadError && <p className="sourcing-error" role="alert">{loadError}</p>}

      {mode === "component" && <>
        <section className="sourcing-component-workflow" aria-label="按器件找供应商使用指引">
          <div className="sourcing-workflow-heading"><div><span>Three-step guide</span><h2>按器件找供应商</h2></div><p>先确定器件，再主动选择“跨类型探索”或“聚焦同类”；不需要记住具体料号。</p></div>
          <div className="sourcing-workflow-grid">
            <label className={`sourcing-workflow-step ${componentKey ? "is-complete" : "is-current"}`}><b>1</b><span><strong>选择器件类型</strong><small>例如电池、SoC、麦克风</small></span><select value={componentKey} onChange={(event) => { setComponentKey(event.target.value); setComponentPayload(null); setSelectedCandidate(""); setSelectedModelLead(null); }}><option value="">请选择器件类型</option>{manifest?.components.map((item) => <option key={item.key} value={item.key}>{item.label}（覆盖 {item.products} 款产品）</option>)}</select></label>
            <fieldset className={`sourcing-workflow-step sourcing-scope-step ${componentKey ? "is-current" : ""}`} disabled={!componentPayload}>
              <legend><b>2</b><span><strong>选择分析范围</strong></span></legend>
              <div className="sourcing-scope-choice" role="radiogroup" aria-label="分析范围">
                <button type="button" role="radio" className={componentScopeMode === "cross" ? "is-active" : ""} aria-checked={componentScopeMode === "cross"} onClick={() => { setComponentScopeMode("cross"); setCategory(""); setYear(""); setSelectedCandidate(""); setSelectedModelLead(null); }}><strong>跨耳机类型探索</strong></button>
                <button type="button" role="radio" className={componentScopeMode === "focused" ? "is-active" : ""} aria-checked={componentScopeMode === "focused"} onClick={() => { setComponentScopeMode("focused"); setCategory(""); setYear(""); setSelectedCandidate(""); setSelectedModelLead(null); }}><strong>聚焦某类耳机</strong></button>
              </div>
              {componentScopeMode === "focused" && <label>选择耳机类型<select value={category} onChange={(event) => { setCategory(event.target.value); setYear(""); setSelectedCandidate(""); setSelectedModelLead(null); }}><option value="">请选择一种耳机类型</option>{componentCategories.map((item) => <option key={item.value} value={item.value} disabled={!item.products}>{item.value}（{item.products} 款）</option>)}</select></label>}
            </fieldset>
            <article className={`sourcing-workflow-step ${componentScopeReady ? "is-current" : ""}`}><b>3</b><span><strong>选择候选并核对证据</strong><small>先看供应商或明确型号，再回到参数、产品、图片和原文</small></span><em>{componentScopeReady ? "可以开始探索 ↓" : "完成前两步后显示结果"}</em></article>
          </div>
        </section>

        {componentPayload && <>
          <section className={`sourcing-scope-bar sourcing-scope-bar--${componentScopeMode}`} aria-label="当前器件研究范围">
            <div className="sourcing-scope-bar__title"><span>Current scope</span><strong>当前研究范围</strong></div>
            <div className="sourcing-scope-bar__context">
              <div className="sourcing-scope-bar__mode" aria-label={componentScopeMode === "cross" ? "当前为跨耳机类型探索" : "当前为聚焦某类耳机"}>
                <b aria-hidden="true">{componentScopeMode === "cross" ? "↔" : "◎"}</b>
                <div>
                  <small>当前分析方式</small>
                  <strong>{componentScopeMode === "cross" ? "跨耳机类型探索" : "聚焦同类耳机"}</strong>
                  <p>{componentScopeMode === "cross" ? "覆盖全部耳机类型，寻找跨产品线复用与供应线索" : category ? `仅比较${category}，核对同类产品中的供应商与替代线索` : "选择一种耳机类型后，再进行同类产品比较"}</p>
                </div>
              </div>
              <ScopeChips items={currentScopeLabels} />
            </div>
            {componentScopeReady && <div className="sourcing-scope-bar__metrics">
              <small>研究范围总计</small>
              <span><b>{uniqueProducts(componentRows)}</b> 款产品</span>
              <span><b>{supplierStats.length}</b> 个供应商名称</span>
              <span><b>{currentBrandNames}</b> 个耳机品牌</span>
              <span><b>{currentSources.reports} / {currentSources.videos}</b> 报告 / 视频</span>
            </div>}
            <button type="button" className="sourcing-scope-bar__adjust" onClick={() => setFilterDrawerOpen(true)}>调整筛选</button>
          </section>

          {filterDrawerOpen && <div className="sourcing-filter-drawer-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setFilterDrawerOpen(false); }}>
            <aside className="sourcing-filter-drawer" role="dialog" aria-modal="true" aria-labelledby="sourcing-filter-drawer-title">
              <header><div><span>Refine scope</span><h2 id="sourcing-filter-drawer-title">调整研究范围</h2><p>以下条件都可留空，选项中的产品数会随其他条件联动更新。</p></div><button type="button" aria-label="关闭筛选抽屉" onClick={() => setFilterDrawerOpen(false)}>×</button></header>
              <div className="sourcing-filter-drawer__fields">
                <label>使用位置（可选）<select value={location} onChange={(event) => { setLocation(event.target.value); setYear(""); setSelectedCandidate(""); setSelectedModelLead(null); setComponentEvidenceOpen(false); }}><option value="">全部位置（{uniqueProducts(locationFacetRows)} 款）</option>{componentLocations.map((item) => <option key={item.value} value={item.value} disabled={!item.products}>{item.value}（{item.products} 款）</option>)}</select></label>
                <label>耳机品牌（可选）<select value={componentBrand} onChange={(event) => { setComponentBrand(event.target.value); setYear(""); setSelectedCandidate(""); setSelectedModelLead(null); setComponentEvidenceOpen(false); }}><option value="">全部已识别品牌（{uniqueProducts(brandFacetRows.filter(hasResolvedBrand))} 款）</option>{componentBrands.map((item) => <option key={item.value} value={item.value} disabled={!item.products}>{item.value}（{item.products} 款）</option>)}</select></label>
                <label>报告年份（可选）<select value={componentYear} onChange={(event) => { setComponentYear(event.target.value); setYear(""); setSelectedCandidate(""); setSelectedModelLead(null); setComponentEvidenceOpen(false); }}><option value="">全部历史年份（{uniqueProducts(yearFacetRows)} 款）</option>{componentYears.map((item) => <option key={item.value} value={item.value} disabled={!item.products}>{item.value}（{item.products} 款）</option>)}</select></label>
                <label>型号 / 产品 / 参数搜索<input type="search" value={query} onChange={(event) => { setQuery(event.target.value); setYear(""); setSelectedCandidate(""); setSelectedModelLead(null); setComponentEvidenceOpen(false); }} placeholder="例如：AC7106F6、500mAh、产品名" /></label>
              </div>
              <section className="sourcing-filter-drawer__summary" aria-label="当前筛选结果">
                <div><span>产品</span><strong>{uniqueProducts(componentRows)}</strong></div><div><span>供应商名称</span><strong>{supplierStats.length}</strong></div><div><span>耳机品牌</span><strong>{currentBrandNames}</strong></div><div><span>报告 / 视频</span><strong>{currentSources.reports} / {currentSources.videos}</strong></div>
              </section>
              <p className="sourcing-filter-caveat">供应商和品牌按当前规范化名称字段去重，仍可能包含别名或待清洗记录。</p>
              <footer><button type="button" onClick={() => { setLocation(""); setComponentBrand(""); setComponentYear(""); setYear(""); setQuery(""); setSelectedCandidate(""); setSelectedModelLead(null); setComponentEvidenceOpen(false); }}>清空可选条件</button><button type="button" className="is-primary" onClick={() => setFilterDrawerOpen(false)}>查看筛选结果</button></footer>
            </aside>
          </div>}
        </>}

        {!componentKey && <section className="sourcing-next-step"><b>1</b><p><strong>先选择要研究的器件</strong><small>只需要选择“电池、SoC、麦克风”等器件类型，不要求你记住具体型号。</small></p></section>}
        {componentLoading && <p className="sourcing-loading">正在加载器件记录…</p>}
        {componentPayload && componentScopeMode === "focused" && !category && <section className="sourcing-next-step"><b>2</b><p><strong>请选择一种耳机类型</strong><small>聚焦模式只比较同类产品；如果想发现跨产品线线索，请切回“跨耳机类型探索”。</small></p></section>}

        {componentPayload && componentScopeReady && <>
          <details className="sourcing-ranking-disclosure sourcing-ranking-disclosure--prominent">
            <summary><span>Supplier observation</span><strong>{selectedComponentLabel}供应商样本观察</strong><small>满足当前筛选条件的历史累计 TOP 10 与 2018–2026 年度 TOP 5 供应商排名（根据我爱音频网历史样本观察）</small><i aria-hidden="true" /></summary>
            <SupplierRankingExplorer
              rows={componentRankingRows}
              componentLabel={selectedComponentLabel}
              scope={rankingScope}
              selectedYear={year}
              onSelectYear={setYear}
              highlightedSupplier={selectedCandidate && selectedCandidate !== ALL_EVIDENCE ? selectedCandidate : ""}
              onSelectSupplier={(supplier) => {
                setSelectedCandidate(supplier);
                setSelectedModelLead(null);
                setComponentResultView("supplier");
                setComponentEvidenceOpen(false);
                returnToComponentResults();
              }}
            />
          </details>
          <section className="sourcing-results" ref={componentResultsRef}>
            <div className="sourcing-section-title"><div><span>Procurement leads</span><h2>当前范围寻源结果</h2></div><small>供应商用于联系询盘；具体型号用于核对竞品应用</small></div>
            <nav className="sourcing-result-tabs" aria-label="寻源结果查看方式" role="tablist">
              <button type="button" role="tab" className={componentResultView === "supplier" ? "is-active" : ""} aria-selected={componentResultView === "supplier"} onClick={() => { setComponentResultView("supplier"); setSelectedModelLead(null); setComponentEvidenceOpen(false); }}><strong>按供应商查看</strong><small>先找可以联系的候选供应商</small></button>
              <button type="button" role="tab" className={componentResultView === "model" ? "is-active" : ""} aria-selected={componentResultView === "model"} onClick={() => { setComponentResultView("model"); setSelectedCandidate(""); setComponentEvidenceOpen(false); }}><strong>按具体型号查看</strong><small>核对明确料号与跨类型应用</small></button>
            </nav>

            {componentResultView === "supplier" ? <div className="sourcing-result-panel sourcing-supplier-master-detail">
              <section className="sourcing-candidate-directory" aria-label="候选供应商完整目录">
                <div className="sourcing-section-title sourcing-result-heading"><div><span>Complete directory</span><h3>候选供应商完整目录</h3></div><small>共 {supplierStats.length} 个已披露名称</small></div>
                <div className="sourcing-candidate-directory__controls">
                  <label>搜索供应商<input type="search" value={candidateQuery} onChange={(event) => setCandidateQuery(event.target.value)} placeholder="输入供应商名称" /></label>
                  <label>排序<select value={candidateSort} onChange={(event) => setCandidateSort(event.target.value as "coverage" | "name")}><option value="coverage">样本覆盖产品数</option><option value="name">供应商名称</option></select></label>
                </div>
                <div className="sourcing-candidate-list">
                  {pagedSupplierStats.map((item) => {
                    const active = selectedCandidate === item.supplier;
                    const rank = supplierStats.findIndex((supplier) => supplier.supplier === item.supplier) + 1;
                    return <button key={item.supplier} type="button" aria-pressed={active} className={active ? "is-selected" : ""} onClick={() => { setSelectedCandidate(item.supplier); setSelectedModelLead(null); setComponentEvidenceOpen(false); }}><span>#{rank || "—"}</span><strong>{item.supplier}</strong><b>{item.products} 款产品</b><small>{item.categories.length} 类耳机 · {item.modelKnownProducts ? `${item.models} 种已识别型号` : "型号未披露"}</small></button>;
                  })}
                </div>
                {!visibleSupplierStats.length && <p className="sourcing-empty">没有匹配的供应商名称，请调整搜索词。</p>}
                <div className="sourcing-pagination"><span>第 {candidatePage} / {candidatePageCount} 页 · {visibleSupplierStats.length} 家</span><button type="button" disabled={candidatePage <= 1} onClick={() => setCandidatePage((value) => Math.max(1, value - 1))}>上一页</button><button type="button" disabled={candidatePage >= candidatePageCount} onClick={() => setCandidatePage((value) => Math.min(candidatePageCount, value + 1))}>下一页</button></div>
              </section>

              <aside className="sourcing-candidate-detail" aria-live="polite">
                {selectedSupplierStat ? <>
                  <div className="sourcing-candidate-detail__head"><div><span>Selected supplier</span><h3>{selectedSupplierStat.supplier}</h3><p>样本覆盖排序 #{supplierStats.findIndex((item) => item.supplier === selectedSupplierStat.supplier) + 1}</p></div><b>{selectedSupplierStat.products}<small>款耳机产品</small></b></div>
                  <div className="sourcing-candidate-detail__metrics"><div><span>耳机类型</span><strong>{selectedSupplierStat.categories.length}</strong></div><div><span>已识别型号</span><strong>{selectedSupplierStat.modelKnownProducts ? selectedSupplierStat.models : "未披露"}</strong></div><div><span>已识别品牌</span><strong>{selectedSupplierStat.brands || "未识别"}</strong></div><div><span>参数披露产品</span><strong>{selectedSupplierStat.parameterizedProducts}/{selectedSupplierStat.products}</strong></div></div>
                  <section className="sourcing-candidate-detail__brands"><span>样本中的耳机品牌</span><div>{selectedSupplierStat.brandNames.slice(0, 10).map((brand) => <i key={brand}>{brand}</i>)}{selectedSupplierStat.brandNames.length > 10 && <i>+{selectedSupplierStat.brandNames.length - 10}</i>}{!selectedSupplierStat.brandNames.length && <em>品牌尚未识别</em>}</div></section>
                  <ul className="sourcing-candidate-detail__facts"><li>{modelDisclosure(selectedSupplierStat.models, selectedSupplierStat.modelKnownProducts, selectedSupplierStat.products)}</li><li>{parameterDisclosure(selectedSupplierStat.parameterizedProducts, selectedSupplierStat.products)}</li>{selectedSupplierStat.supplierOnlyProducts > 0 && <li>{selectedSupplierStat.supplierOnlyProducts} 款产品仅披露生产商，可作为线下询盘线索</li>}</ul>
                  <button type="button" className="sourcing-candidate-detail__primary" onClick={() => setComponentEvidenceOpen(true)}>查看该供应商的产品规格与证据 <span>→</span></button>
                </> : <div className="sourcing-candidate-detail__empty"><b>选择一家候选供应商</b><p>右侧将显示样本覆盖、品牌、型号披露和参数证据，再决定是否继续下钻。</p></div>}
              </aside>
              <div className="sourcing-candidate-actions sourcing-candidate-actions--wide"><button type="button" onClick={() => { setSelectedCandidate(ALL_EVIDENCE); setSelectedModelLead(null); setComponentEvidenceOpen(true); }}>查看当前范围全部产品与证据</button></div>
            </div> : <div className="sourcing-result-panel">
              <div className="sourcing-section-title sourcing-result-heading"><div><span>Observed component models</span><h3>当前范围常见厂商 / 型号</h3></div><small>跨类型应用已收进卡片标签，不代表已经验证可互换</small></div>
              <div className="sourcing-model-search" role="search">
                <label htmlFor="sourcing-model-lead-search"><strong>搜索器件型号或供应商</strong><small>仅搜索当前研究范围，不会改变前置筛选条件</small></label>
                <div>
                  <input id="sourcing-model-lead-search" type="search" value={modelLeadQuery} onChange={(event) => setModelLeadQuery(event.target.value)} placeholder="例如：AC7106F6 / 杰理科技" />
                  {modelLeadQuery && <button type="button" onClick={() => setModelLeadQuery("")}>清除</button>}
                </div>
                <p aria-live="polite">{modelLeadNeedle
                  ? `找到 ${searchedModelStats.length} 组已识别型号，其中 ${searchedReusableModelStats.length} 组涉及多款产品、${searchedSingleProductModelStats.length} 组仅见于 1 款产品。`
                  : `默认展示涉及至少 2 款产品的 ${reusableModelStats.length} 组复用线索；可搜索全部 ${commonModelStats.length} 组已识别型号。`}</p>
              </div>
              <ModelLeadGrid
                items={displayedReusableModelStats}
                selected={selectedModelLead}
                source="scope"
                emptyText={modelLeadNeedle
                  ? (searchedSingleProductModelStats.length ? "没有涉及多款产品的匹配型号；下方列出仅见于 1 款产品的匹配结果。" : "当前范围没有匹配的器件型号或供应商，请尝试缩短关键词。")
                  : "当前范围没有披露可确认的器件型号，可切换到供应商视图查看询盘线索。"}
                onSelect={(item) => {
                  const active = selectedModelLead?.source === "scope" && selectedModelLead.key === item.key;
                  setSelectedModelLead(active ? null : { key: item.key, source: "scope" });
                  setSelectedCandidate("");
                  setComponentEvidenceOpen(!active);
                  if (active) returnToComponentResults();
                }}
              />
              {modelLeadNeedle && searchedSingleProductModelStats.length > 0 && <section className="sourcing-single-model-results" aria-label="仅一款产品使用的型号搜索结果">
                <header><div><span>Single-product matches</span><h4>仅见于 1 款产品的匹配结果</h4></div><small>可核对证据，但不作为跨产品复用线索</small></header>
                <div>{searchedSingleProductModelStats.map((item) => {
                  const active = selectedModelLead?.source === "scope" && selectedModelLead.key === item.key;
                  return <button key={item.key} type="button" className={active ? "is-selected" : ""} aria-pressed={active} onClick={() => {
                    setSelectedModelLead(active ? null : { key: item.key, source: "scope" });
                    setSelectedCandidate("");
                    setComponentEvidenceOpen(!active);
                    if (active) returnToComponentResults();
                  }}>
                    <span><strong>{item.model}</strong><small>{item.supplier || "生产商未披露"}</small></span>
                    <span><b>{productName(item.rows[0])}</b><small>{item.locations.join("、") || "位置未识别"} · {item.latestYear || "年份未披露"}</small></span>
                    <em>仅 1 款样本 · 查看证据 →</em>
                  </button>;
                })}</div>
              </section>}
              {!modelLeadNeedle && singleProductModelCount > 0 && <p className="sourcing-single-model-note">另有 {singleProductModelCount} 组型号仅在 1 款产品中出现，不制作候选卡片；可通过上方搜索或下方全部证据继续核对。</p>}
              <div className="sourcing-candidate-actions">
                {!modelLeadNeedle && reusableModelStats.length > 9 && <button type="button" onClick={() => setShowAllModels(!showAllModels)}>{showAllModels ? "收起常见厂商 / 型号" : `查看全部 ${reusableModelStats.length} 组复用型号线索`}</button>}
                <button type="button" onClick={() => { setSelectedCandidate(ALL_EVIDENCE); setSelectedModelLead(null); setComponentEvidenceOpen(true); }}>查看当前搜索范围全部证据</button>
              </div>
            </div>}
          </section>
          {showComponentDetail && <section id="component-evidence" className="sourcing-detail" ref={componentDetailRef}>
            <div className="sourcing-detail-head"><div><span>Product specs & evidence</span><h2 ref={componentDetailHeadingRef} tabIndex={-1}>产品规格与报告证据</h2><p>{componentDetailTitle}。按产品组织参数、器件位置、图片和报告原文。</p></div><button className="sourcing-detail-head__collapse" type="button" onClick={() => { setComponentEvidenceOpen(false); returnToComponentResults(); }}>收起整个证据区</button></div>
            <div className="sourcing-detail-toolbar">
              <div className="sourcing-detail-toolbar__summary"><strong>当前 {uniqueProducts(detailRows)} 款产品</strong><span>{detailRows.length} 条位置记录 · {parameterizedProducts(detailRows)} 款披露结构化参数</span>{!detailFiltersOpen && activeDetailFilterCount === 0 && <small>结果较多？可按耳机类型、位置、品牌、年份和参数继续收窄。</small>}</div>
              <button className="sourcing-detail-toolbar__filter" type="button" aria-expanded={detailFiltersOpen} onClick={() => setDetailFiltersOpen((value) => !value)}>{detailFiltersOpen ? "收起筛选条件" : activeDetailFilterCount ? `已筛选 ${activeDetailFilterCount} 项 · 调整条件` : "继续筛选产品与规格"}</button>
            </div>
            {detailFiltersOpen && <section className="sourcing-detail-refinement" aria-label="候选结果二级筛选">
              <div className="sourcing-section-title"><div><span>Refine candidate</span><h3>继续锁定目标产品和规格</h3></div><small>选项计数按其他二级条件联动更新</small></div>
              <div className="sourcing-detail-filters">
                <label>耳机类型<select value={detailFilters.category} onChange={(event) => setDetailFilters((current) => ({ ...current, category: event.target.value }))}><option value="">全部类型（{uniqueProducts(detailCategoryRows)} 款）</option>{detailCategories.map((item) => <option key={item.value} value={item.value} disabled={!item.products}>{item.value}（{item.products} 款）</option>)}</select></label>
                <label>使用位置<select value={detailFilters.location} onChange={(event) => setDetailFilters((current) => ({ ...current, location: event.target.value }))}><option value="">全部位置（{uniqueProducts(detailLocationRows)} 款）</option>{detailLocations.map((item) => <option key={item.value} value={item.value} disabled={!item.products}>{item.value}（{item.products} 款）</option>)}</select></label>
                {!selectedModelStat && <label>器件型号<select value={detailFilters.model} onChange={(event) => setDetailFilters((current) => ({ ...current, model: event.target.value }))}><option value="">全部型号状态（{uniqueProducts(detailModelRows)} 款）</option>{detailModels.map((item) => <option key={item.value} value={item.value} disabled={!item.products}>{item.label}（{item.products} 款）</option>)}</select></label>}
                <label>耳机品牌<select value={detailFilters.brand} onChange={(event) => setDetailFilters((current) => ({ ...current, brand: event.target.value }))}><option value="">全部已识别品牌（{uniqueProducts(detailBrandRows.filter(hasResolvedBrand))} 款）</option>{detailBrands.map((item) => <option key={item.value} value={item.value} disabled={!item.products}>{item.value}（{item.products} 款）</option>)}</select></label>
                <label>报告年份<select value={detailFilters.year} onChange={(event) => setDetailFilters((current) => ({ ...current, year: event.target.value }))}><option value="">全部年份（{uniqueProducts(detailYearRows)} 款）</option>{detailYears.map((item) => <option key={item.value} value={item.value} disabled={!item.products}>{item.value}（{item.products} 款）</option>)}</select></label>
                <label>参数披露<select value={detailFilters.parameterState} onChange={(event) => setDetailFilters((current) => ({ ...current, parameterState: event.target.value }))}><option value="">全部状态（{uniqueProducts(detailParameterRows)} 款）</option><option value="complete" disabled={!detailParameterCounts.complete}>全部位置已披露（{detailParameterCounts.complete} 款）</option><option value="partial" disabled={!detailParameterCounts.partial}>部分位置披露（{detailParameterCounts.partial} 款）</option><option value="missing" disabled={!detailParameterCounts.missing}>全部未披露（{detailParameterCounts.missing} 款）</option></select></label>
                <label className="sourcing-detail-search">产品 / 型号 / 参数关键词<input type="search" value={detailFilters.query} onChange={(event) => setDetailFilters((current) => ({ ...current, query: event.target.value }))} placeholder="例如：500mAh、3.7V、产品名" /></label>
                <button type="button" onClick={() => setDetailFilters(emptyDetailFilters())}>重置二级条件</button>
              </div>
              <p className="sourcing-detail-result-line">当前找到 <strong>{uniqueProducts(detailRows)}</strong> 款产品 · <strong>{detailRows.length}</strong> 条{selectedComponentLabel}位置记录 · <strong>{parameterizedProducts(detailRows)}</strong> 款披露结构化参数</p>
              <ScopeChips items={componentDetailScope} />
            </section>}
            {detailRows.length > 0 && (componentKey === "battery"
              ? <BatteryProductMatrix rows={detailRows} productBase={productBase} onImage={(image, row) => setImagePreview({ image, row })} />
              : <EvidenceTable rows={detailRows} productBase={productBase} title={componentDetailTitle} scope={componentDetailScope} onImage={(image, row) => setImagePreview({ image, row })} />)}
            {!detailRows.length && <p className="sourcing-empty">当前二级条件没有匹配记录，请减少一个筛选条件后重试。</p>}
          </section>}
          <details className="sourcing-quality"><summary>数据覆盖与待核验边界</summary><ul>{componentPayload.scope_notes.map((note) => <li key={note}>{note}</li>)}<li>跨类型复用统计已排除 {quarantinedReuseRows.length} 条型号、耳机类型或位置异常记录；这些记录仍可在全部证据中追溯。</li><li>年度变化按我爱音频网报告日期计算，不等同于器件上市、采购或供应商切换日期。</li></ul><p>当前粒度：{componentPayload.grain}。</p></details>
        </>}
      </>}

      {mode === "supplier" && <div className="sourcing-supplier-layout">
        <aside className="sourcing-supplier-picker">
          <div className="sourcing-supplier-directory-head"><span>Supplier directory</span><h2>供应商能力目录</h2><p>共 {supplierManifest?.suppliers.length || 0} 个规范供应商名称，按样本覆盖产品款数排序。</p></div>
          <label>搜索供应商<input type="search" value={supplierQuery} onChange={(event) => setSupplierQuery(event.target.value)} placeholder="例如：紫建电子、杰理科技" /></label>
          <small>目录展示器件范围、产品和品牌覆盖；不代表供应商完整经营范围。</small>
          <div className="sourcing-supplier-list">
            {supplierOptions.map((item) => <button key={item.key} type="button" className={selectedSupplierKey === item.key ? "is-selected" : ""} aria-pressed={selectedSupplierKey === item.key} onClick={() => setSelectedSupplierKey(item.key)}><strong>{item.supplier}</strong><span>{item.components} 类器件 · {item.products} 款产品 · {item.brands} 个耳机品牌</span><small>明确型号涉及 {item.model_known_products || 0}/{item.products} 款产品</small></button>)}
          </div>
          {!matchedSupplierOptions.length && <p className="sourcing-empty">没有匹配的已识别供应商。</p>}
          {matchedSupplierOptions.length > 0 && <div className="sourcing-supplier-pagination">
            <span>第 {supplierPage} / {supplierPageCount} 页 · 共 {matchedSupplierOptions.length} 家</span>
            <nav aria-label="供应商能力目录分页">
              <button type="button" aria-label="上一页" disabled={supplierPage <= 1} onClick={() => setSupplierPage((value) => Math.max(1, value - 1))}>‹</button>
              {supplierPageItems.map((item) => item.page
                ? <button key={item.key} type="button" aria-current={item.page === supplierPage ? "page" : undefined} onClick={() => setSupplierPage(item.page!)}>{item.page}</button>
                : <span key={item.key}>…</span>)}
              <button type="button" aria-label="下一页" disabled={supplierPage >= supplierPageCount} onClick={() => setSupplierPage((value) => Math.min(supplierPageCount, value + 1))}>›</button>
            </nav>
          </div>}
        </aside>
        <div className="sourcing-supplier-main">
          {!selectedSupplierKey && <section className="sourcing-next-step"><b>1</b><p><strong>选择一家供应商</strong><small>这里展示的是当前拆解样本中的已确认关系，不是供应商完整产品目录。</small></p></section>}
          {supplierLoading && <p className="sourcing-loading">正在加载供应商器件档案…</p>}
          {supplierPayload && <>
            <section className="sourcing-supplier-head"><div><span>Supplier capability</span><h2>{supplierPayload.supplier}</h2><p>历史样本累计：在当前拆解报告中观察到的器件、品牌和落地产品。</p></div><div><strong>{componentStats.length}</strong><span>类器件</span><strong>{uniqueProducts(supplierRows)}</strong><span>款产品</span></div></section>
            <section className="sourcing-supplier-filters">
              <label>耳机类型<select value={supplierCategory} onChange={(event) => { setSupplierCategory(event.target.value); setSupplierYear(""); }}><option value="">全部类型</option>{supplierCategories.map((item) => <option key={item}>{item}</option>)}</select></label>
              <label>使用位置<select value={supplierLocation} onChange={(event) => { setSupplierLocation(event.target.value); setSupplierYear(""); }}><option value="">全部位置</option>{supplierLocations.map((item) => <option key={item}>{item}</option>)}</select></label>
            </section>
            <ScopeChips items={[["供应商", supplierPayload.supplier], ["耳机类型", supplierCategory || "全部"], ["使用位置", supplierLocation || "全部"], ["时间口径", "历史样本累计"]]} />
            <AnnualObservationTrend rows={supplierRows} seriesOf={(row) => text(row.component_label || row.component_name)} seriesUnit="器件类型" title={`${supplierPayload.supplier}器件应用历年观察变化`} subtitle="每年观察到的产品与器件结构，不能理解为供应商完整业务变化" selectedYear={supplierYear} onSelectYear={setSupplierYear} />
            <section className="sourcing-capabilities">
              <div className="sourcing-section-title"><div><span>Component portfolio</span><h2>器件能力版图</h2></div><small>点击器件查看品牌、产品与证据</small></div>
              <div className="sourcing-capability-grid">
                {componentStats.map((item) => <button key={item.key} type="button" className={selectedSupplierComponent === item.key ? "is-selected" : ""} aria-pressed={selectedSupplierComponent === item.key} onClick={() => setSelectedSupplierComponent(item.key)}><strong>{item.label}</strong><b>{item.products} 款产品</b><small>{modelDisclosure(item.models, item.modelKnownProducts, item.products)}</small><small>{brandDisclosure(item.brands, item.brandKnownProducts, item.products)}</small></button>)}
              </div>
            </section>
            {selectedSupplierComponent && <>
              <section className="sourcing-tree">
                <div className="sourcing-section-title"><div><span>Application tree</span><h2>{selectedSupplierComponentLabel}应用产品</h2></div><small>供应商 → 器件 → 耳机类型 → 品牌 → 产品</small></div>
                <div className="sourcing-tree-controls">
                  <label>树内耳机类型<select value={supplierTreeCategory} onChange={(event) => setSupplierTreeCategory(event.target.value)}><option value="">全部类型（{uniqueProducts(supplierComponentRows)} 款）</option>{supplierTreeCategories.map((item) => <option key={item} value={item}>{item}（{uniqueProducts(supplierComponentRows.filter((row) => text(row.product_category) === item))} 款）</option>)}</select></label>
                  <label>搜索并定位耳机品牌<input type="search" list="supplier-tree-brands" value={supplierTreeBrandQuery} onChange={(event) => setSupplierTreeBrandQuery(event.target.value)} placeholder="输入品牌名称后只显示对应分支" /><datalist id="supplier-tree-brands">{supplierTreeBrands.map((item) => <option key={item} value={item} />)}</datalist></label>
                  {(supplierTreeCategory || supplierTreeBrandQuery) && <button type="button" onClick={() => { setSupplierTreeCategory(""); setSupplierTreeBrandQuery(""); }}>清除树内定位</button>}
                </div>
                <p className="sourcing-tree-result">当前定位到 <strong>{uniqueProducts(supplierTreeRows)}</strong> 款产品；搜索品牌时只保留匹配分支。</p>
                {supplierTree.map(([categoryName, brands], categoryIndex) => {
                  const categoryRows = [...brands.values()].flat();
                  return <details key={categoryName} open={categoryIndex === 0 || Boolean(supplierTreeBrandQuery)}><summary><strong>{categoryName}</strong><span>{uniqueProducts(categoryRows)} 款产品</span></summary><div>{[...brands.entries()].sort((a, b) => a[0].localeCompare(b[0], "zh-CN")).map(([brand, rows]) => {
                    const products = new Map<string, AnalysisRow[]>();
                    rows.forEach((row) => { const current = products.get(row.product_id) || []; current.push(row); products.set(row.product_id, current); });
                    return <section key={brand}><h3>{brand}</h3><ul>{[...products.entries()].map(([productId, productRows]) => <li key={productId}><a href={`${productBase}${productId}/`}>{text(productRows[0].product_model) || "未知产品"}</a><span>{reportedModels(productRows).join("、") || "型号未披露"} · {unique(productRows.map((row) => text(row.usage_location_label))).join("、") || "位置未识别"} · {text(productRows[0].source_published_at) || "日期未披露"}</span></li>)}</ul></section>;
                  })}</div></details>;
                })}
                {!supplierTree.length && <p className="sourcing-empty">没有匹配的品牌分支，请清除一个树内条件后重试。</p>}
              </section>
              <EvidenceTable rows={supplierComponentRows} productBase={productBase} title={`${supplierPayload.supplier} · ${selectedSupplierComponentLabel}证据`} scope={[["供应商", supplierPayload.supplier], ["器件", selectedSupplierComponentLabel], ["耳机类型", supplierCategory || "全部"], ["使用位置", supplierLocation || "全部"], ["时间口径", "历史样本累计"]]} onImage={(image, row) => setImagePreview({ image, row })} />
            </>}
            <section className="sourcing-quality"><strong>统计边界</strong><ul>{supplierPayload.scope_notes.map((note) => <li key={note}>{note}</li>)}</ul><p>当前粒度：{supplierPayload.grain}。</p></section>
          </>}
        </div>
      </div>}

      {imagePreview && <div className="sourcing-lightbox" role="dialog" aria-modal="true" aria-label="器件图片证据预览" onMouseDown={(event) => { if (event.target === event.currentTarget) setImagePreview(null); }}><div><button type="button" onClick={() => setImagePreview(null)} aria-label="关闭图片预览">×</button><img src={text(imagePreview.image.public_path || imagePreview.image.url)} alt={imagePreview.image.caption || imagePreview.image.alt || "器件图片证据"} /><section><strong>{productName(imagePreview.row)} · {imagePreview.row.component_label}</strong><span>{imagePreview.image.caption || imagePreview.image.alt || "报告关联原图"}</span><small>用于证据追溯，不代表已经通过视觉模型确认图片只包含该器件。</small></section></div></div>}
    </div>
  );
}
