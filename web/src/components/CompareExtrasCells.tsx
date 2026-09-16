/**
 * 对比表里“核心卖点”“物料清单”两个特殊行的单元格渲染。抽成独立组件供
 * CompareWorkbench（单品类）与 CrossCategoryCompare（跨品类）共用，避免重复实现。
 */
import { collectSellingPointTags, sellingPointTagStyle } from "../lib/sellingPointTags";
import type { ProductExtrasState } from "../lib/productExtras";
import { conciseSellingPoint, pickBomHighlights, pickTopSellingPoints } from "../lib/productExtras";

function LoadingList() {
  return (
    <div className="flex flex-col gap-1.5" aria-hidden="true">
      <div className="h-3 w-full animate-pulse rounded bg-[var(--line)]" />
      <div className="h-3 w-4/5 animate-pulse rounded bg-[var(--line)]" />
      <div className="h-3 w-2/3 animate-pulse rounded bg-[var(--line)]" />
    </div>
  );
}

export function SellingPointsCell({ state }: { state: ProductExtrasState | undefined }) {
  if (!state || state === "loading") {
    return <LoadingList />;
  }
  if (state === "error") {
    return <span className="text-xs text-[var(--warn)]">卖点数据加载失败</span>;
  }
  const points = pickTopSellingPoints(state.sellingPoints, 6);
  if (points.length === 0) {
    return <span className="text-xs text-[var(--muted)]">暂无卖点数据</span>;
  }
  return (
    <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
      {points.map((sp, idx) => {
        const tag = collectSellingPointTags(sp)[0];
        const style = sellingPointTagStyle(tag);
        return (
          <li key={idx} className="flex items-start gap-1.5 text-xs leading-relaxed text-[var(--text)]">
            <span
              className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full"
              style={{ background: style.text }}
              title={tag}
            />
            <span>
              {tag && (
                <span
                  className="mr-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
                  style={{ background: style.bg, color: style.text }}
                >
                  {tag}
                </span>
              )}
              {conciseSellingPoint(sp.text)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export function SellingPointTagsCell({ state }: { state: ProductExtrasState | undefined }) {
  if (!state || state === "loading") {
    return <LoadingList />;
  }
  if (state === "error") {
    return <span className="text-xs text-[var(--warn)]">卖点数据加载失败</span>;
  }
  const tags = Array.from(
    new Set(state.sellingPoints.flatMap((sp) => collectSellingPointTags(sp)).filter(Boolean))
  );
  if (!tags.length) {
    return <span className="text-xs text-[var(--muted)]">暂无卖点标签</span>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {tags.map((tag) => {
        const style = sellingPointTagStyle(tag);
        return (
          <span
            key={tag}
            className="rounded-full px-2 py-0.5 text-xs font-semibold"
            style={{ background: style.bg, color: style.text }}
          >
            {tag}
          </span>
        );
      })}
    </div>
  );
}

export function ScenariosCell({ state }: { state: ProductExtrasState | undefined }) {
  if (!state || state === "loading") return <LoadingList />;
  if (state === "error") return <span className="text-xs text-[var(--warn)]">场景数据加载失败</span>;
  if (!state.scenarios.length) return <span className="text-xs text-[var(--muted)]">暂无场景数据</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {state.scenarios.map((scenario) => (
        <span key={scenario} className="rounded-full bg-[var(--primary-soft)] px-2 py-0.5 text-xs text-[var(--primary-dark)]">
          {scenario}
        </span>
      ))}
    </div>
  );
}

/** 单品类对比仍保留原有的 BOM 摘要卡片；跨品类对比不再使用它。 */
export function BomHighlightsCell({
  state,
  productHref,
}: {
  state: ProductExtrasState | undefined;
  productHref: string;
}) {
  if (!state || state === "loading") return <LoadingList />;
  if (state === "error") return <span className="text-xs text-[var(--warn)]">物料数据加载失败</span>;
  const { items, remaining, total } = pickBomHighlights(state.bomTable, 7);
  if (!items.length) return <span className="text-xs text-[var(--muted)]">暂无物料数据</span>;
  return (
    <div className="flex flex-col gap-1.5">
      <ul className="m-0 flex list-none flex-col gap-1 p-0">
        {items.map((item) => (
          <li key={item.key} className="text-xs leading-relaxed text-[var(--text)]">{item.label}</li>
        ))}
      </ul>
      {remaining > 0 && <span className="text-[11px] text-[var(--muted)]">等 {total} 项</span>}
      <a href={`${productHref}#bom`} className="text-[11px] text-[var(--primary)] hover:underline">查看全部物料 →</a>
    </div>
  );
}
