import { useEffect, useMemo, useState } from "react";
import { withBase } from "../lib/paths";

interface DigestImage {
  local_path?: string;
  caption?: string;
}

interface DigestFinding {
  title?: string;
  text: string;
}

interface RoundupReport {
  id: string;
  title: string;
  published_at?: string;
  url?: string;
  category?: string;
  kind?: string;
  summary?: string;
  digest?: {
    overview?: string;
    key_findings?: DigestFinding[];
    image_highlights?: DigestImage[];
  };
}

interface Props {
  reports: RoundupReport[];
  years?: string[];
}

const REPORTS_PER_PAGE = 4;

export default function RoundupInsights({ reports, years = [] }: Props) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(reports.length / REPORTS_PER_PAGE));
  const pageReports = useMemo(
    () => reports.slice(page * REPORTS_PER_PAGE, (page + 1) * REPORTS_PER_PAGE),
    [page, reports],
  );
  const active = reports.find((report) => report.id === activeId) || null;

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setActiveId(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, []);

  useEffect(() => {
    setPage((current) => Math.min(current, pageCount - 1));
  }, [pageCount]);

  if (!reports.length) return null;
  return (
    <section className="roundup-insights" aria-labelledby="roundup-insights-title">
      <div className="roundup-insights__heading">
        <div>
          <p className="eyebrow">RESEARCH DIGEST</p>
          <h2 id="roundup-insights-title">年度洞察与汇总报告</h2>
          <p>汇总类原始报告独立呈现；点击卡片可查看基于原文图文的研究摘要。</p>
        </div>
        <div className="roundup-insights__years" aria-label="覆盖年度与报告数量">
          <b>{reports.length} 篇</b>
          {years.slice(0, 4).map((year) => <span key={year}>{year}</span>)}
        </div>
      </div>

      <div className="roundup-insights__grid">
        {pageReports.map((report, index) => (
          <button key={report.id} type="button" className={`roundup-card ${page === 0 && index === 0 ? "roundup-card--featured" : ""}`} onClick={() => setActiveId(report.id)}>
            <div className="roundup-card__meta"><span>{report.kind || "汇总报告"}</span><time>{report.published_at || "日期待补"}</time></div>
            <h3>{report.title}</h3>
            <p>{report.digest?.overview || report.summary || "原文摘要将在构建完成后显示。"}</p>
            <div className="roundup-card__foot"><span>{report.category || "耳机研究"}</span><b>查看摘要 <i>→</i></b></div>
          </button>
        ))}
      </div>

      {pageCount > 1 && (
        <nav className="roundup-pagination" aria-label="汇总报告分页">
          <button type="button" onClick={() => setPage((current) => Math.max(0, current - 1))} disabled={page === 0} aria-label="上一页">‹</button>
          {Array.from({ length: pageCount }, (_, index) => (
            <button key={index} type="button" onClick={() => setPage(index)} className={page === index ? "is-active" : ""} aria-label={`第 ${index + 1} 页`} aria-current={page === index ? "page" : undefined}>{index + 1}</button>
          ))}
          <button type="button" onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))} disabled={page === pageCount - 1} aria-label="下一页">›</button>
        </nav>
      )}

      {active && (
        <div className="roundup-drawer-layer" role="presentation" onMouseDown={() => setActiveId(null)}>
          <aside className="roundup-drawer" role="dialog" aria-modal="true" aria-labelledby="roundup-drawer-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="roundup-drawer__top"><span>{active.kind || "汇总报告"} · {active.published_at || "日期待补"}</span><button type="button" aria-label="关闭摘要" onClick={() => setActiveId(null)}>×</button></div>
            <h2 id="roundup-drawer-title">{active.title}</h2>
            <p className="roundup-drawer__overview">{active.digest?.overview || active.summary || "该报告的图文摘要仍在生成中，请先阅读原文。"}</p>
            {!!active.digest?.key_findings?.length && <section className="roundup-drawer__findings"><h3>核心发现</h3>{active.digest.key_findings.map((finding, index) => <article key={`${finding.title}-${index}`}><strong>{finding.title || "关键发现"}</strong><p>{finding.text}</p></article>)}</section>}
            {!!active.digest?.image_highlights?.length && <section className="roundup-drawer__images"><h3>原文图示</h3><div>{active.digest.image_highlights.map((image, index) => <figure key={`${image.local_path}-${index}`}>{image.local_path && <img src={withBase(image.local_path)} alt={image.caption || "原文配图"} loading="lazy" />}<figcaption>{image.caption || "原文配图"}</figcaption></figure>)}</div></section>}
            <a className="roundup-drawer__source" href={active.url || "#"} target="_blank" rel="noopener noreferrer">阅读我爱音频网原文 <span>↗</span></a>
          </aside>
        </div>
      )}
    </section>
  );
}
