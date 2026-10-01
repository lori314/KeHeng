import { useCallback, useEffect, useState, type FormEvent } from "react";
import { createEvidenceAnalysis, getEvidenceAnalysis, getEvidenceReport } from "./api";
import type { EvidenceAnalysisTaskResponse, EvidenceFirstReport, EvidenceStage, ReportCitation, ReportEvidenceBundle, ReportFact } from "./types";

const stageLabels: Record<EvidenceStage, string> = {
  queued: "等待开始", research: "公开资料检索与主体核验", technology_semantic: "技术证据识别",
  technology_finance: "技术—经营证据映射", evidence_assertions: "多源证据整理", evidence_first_report: "尽调报告生成",
  complete: "分析完成", failed: "分析失败",
};
const pipeline: EvidenceStage[] = ["research", "technology_semantic", "technology_finance", "evidence_assertions", "evidence_first_report"];
const factTypeLabels: Record<string, string> = {
  product_launch: "产品发布", technical_capability: "技术能力", technical_specification: "技术规格", patent_grant: "专利授权",
  patent_portfolio: "专利组合", tapeout: "流片", silicon_validation: "芯片验证", customer_validation: "客户验证",
  mass_production: "规模量产", deployment: "部署应用",
};
const qualityLabels: Record<string, string> = { first_party: "企业一方来源", authoritative_public_record: "权威公开记录", weak_web: "普通网页", snippet_only: "搜索摘要" };
const statusClass: Record<string, string> = { supported: "supported", limited_support: "limited", conflict: "conflict", no_evidence: "none" };

function Header() { return <header className="topbar"><a className="brand" href="/" aria-label="科衡首页"><span className="brand-mark">科</span><span><strong>科衡</strong><small>KeHeng · Evidence V2</small></span></a><div className="product-title">科技企业证据增强智能尽调系统</div></header>; }
function Footer() { return <footer className="site-footer"><span>KeHeng · 科衡</span><span>公开证据增强分析不替代人工尽调与正式决策</span></footer>; }

function StartPage({ name, onName, onSubmit, busy, error }: { name: string; onName: (v: string) => void; onSubmit: (e: FormEvent) => void; busy: boolean; error: string | null }) {
  return <div className="app-shell"><Header/><main className="start-main"><section className="start-hero"><p className="eyebrow">EVIDENCE FIRST · TECHNOLOGY DUE DILIGENCE</p><h1>让科技企业尽调<br/><em>回到证据与规则</em></h1><p className="hero-copy">输入企业名称，科衡将检索公开资料，识别技术领域与技术阶段，整理经营和财务事实，并构建可追溯的技术—金融证据链。</p><form className="start-form" onSubmit={onSubmit}><label htmlFor="enterprise-name">企业名称</label><input id="enterprise-name" value={name} onChange={(e) => onName(e.target.value)} maxLength={300} placeholder="请输入企业法定名称" disabled={busy}/><button className="primary-button" type="submit" disabled={busy}>{busy ? "正在启动…" : "开始证据尽调"}</button><small>仅需企业名称即可启动。系统将基于公开资料构建证据链；未找到公开证据不代表事项不存在。</small></form>{error && <div className="inline-error" role="alert">{error}</div>}</section><section className="workflow" aria-label="分析流程">{["公开资料检索", "技术证据识别", "技术—经营映射", "证据聚合", "尽调报告"].map((item, i) => <div className="workflow-item" key={item}><span>{String(i + 1).padStart(2, "0")}</span><strong>{item}</strong>{i < 4 && <b aria-hidden="true">→</b>}</div>)}</section><div className="start-note"><span className="note-mark">i</span><p>系统整理公开信息并展示证据边界。所有技术与经营判断均应结合原始材料进行人工核验。</p></div></main><Footer/></div>;
}

function ProgressPage({ task, onReset }: { task: EvidenceAnalysisTaskResponse; onReset: () => void }) {
  const current = task.current_stage === "queued" ? 0 : pipeline.indexOf(task.current_stage);
  return <div className="app-shell"><Header/><main className="progress-main"><p className="eyebrow">EVIDENCE WORKFLOW</p><h1>正在构建证据链</h1><p className="company-name">{task.enterprise_name}</p><p className="stage-current" role="status">{stageLabels[task.current_stage]}</p><ol className="stage-list">{pipeline.map((stage, i) => { const done = current > i; const active = current === i; return <li className={done ? "done" : active ? "active" : "pending"} key={stage}><span className="stage-symbol" aria-hidden="true">{done ? "✓" : active ? "●" : "○"}</span><span>{stageLabels[stage]}</span></li>; })}</ol><p className="muted">任务编号：{task.task_id}</p><button className="secondary-button" type="button" onClick={onReset}>返回首页</button></main><Footer/></div>;
}

function CitationDisclosure({ citation }: { citation: ReportCitation }) {
  const location = citation.page_number !== null ? `第 ${citation.page_number} 页` : citation.paragraph_number !== null ? `第 ${citation.paragraph_number} 段` : citation.locator_text ?? "未提供精确位置";
  return <details className="citation"><summary><span>{citation.source_title ?? citation.source_type}</span><small>{qualityLabels[citation.source_quality] ?? citation.source_quality} · {location}</small></summary><div className="citation-body">{citation.excerpt && <blockquote>{citation.excerpt}</blockquote>}<div className="citation-meta"><span>chunk · {citation.chunk_id}</span><span>citation · {citation.citation_id}</span></div>{citation.source_url && <a href={citation.source_url} target="_blank" rel="noreferrer">打开原始来源 ↗</a>}</div></details>;
}

function FactCard({ fact }: { fact: ReportFact }) {
  return <article className="fact-card"><div className="fact-type">{factTypeLabels[fact.fact_type] ?? fact.fact_type}</div><h3>{fact.subject} · {fact.predicate}</h3><p>{fact.object_value}</p><div className="fact-meta">{fact.period && <span>{fact.period}</span>}{fact.event_time && <span>{new Date(fact.event_time).toLocaleDateString("zh-CN")}</span>}<span>{qualityLabels[fact.source_quality] ?? fact.source_quality}</span></div><CitationDisclosure citation={fact.evidence}/></article>;
}

function EvidenceBundleDisclosure({ bundle }: { bundle: ReportEvidenceBundle }) {
  const count = bundle.technology_facts.length + bundle.financial_facts.length + bundle.milestones.length;
  return <details className="bundle"><summary>查看证据依据（{count}）</summary><div className="bundle-content">{bundle.technology_facts.map((f) => <FactCard key={f.fact_id} fact={f}/>)}{bundle.milestones.map((m) => <p key={`${m.template_id}-${m.milestone_id}`}>{m.milestone_name} · {m.status_label}</p>)}{bundle.financial_facts.map((f) => <FactCard key={f.fact_id} fact={f}/>)}{bundle.rule_ids.length > 0 && <small>规则：{bundle.rule_ids.join("、")}</small>}</div></details>;
}

function Facts({ title, facts, empty }: { title: string; facts: ReportFact[]; empty: string }) {
  const groups = facts.reduce<Record<string, ReportFact[]>>((all, fact) => { (all[fact.fact_type] ??= []).push(fact); return all; }, {});
  return <section className="report-section" id="technology-facts"><div className="section-head"><p className="section-kicker">EVIDENCE RECORD</p><h2>{title}</h2><span>{facts.length} 条</span></div>{facts.length === 0 ? <p className="empty-state">{empty}</p> : Object.entries(groups).map(([type, items]) => <div className="fact-group" key={type}><h3>{factTypeLabels[type] ?? type}<small>{items.length}</small></h3><div className="fact-grid">{items.map((fact) => <FactCard key={fact.fact_id} fact={fact}/>)}</div></div>)}</section>;
}

function TechnologyTimeline({ milestones }: { milestones: EvidenceFirstReport["technology_milestones"] }) {
  const blocked = [...new Set(milestones.flatMap((m) => m.blocked_inferences))];
  return <section className="report-section" id="technology-stage"><div className="section-head"><p className="section-kicker">TECHNOLOGY LIFECYCLE</p><h2>技术生命周期</h2><span>{milestones.length} 个里程碑</span></div>{milestones.length === 0 ? <p className="empty-state">当前没有可展示的技术生命周期节点。</p> : <ol className="timeline">{milestones.map((m, i) => <li className={`milestone ${statusClass[m.status] ?? "none"}`} key={`${m.template_id}-${m.milestone_id}`}><span className="timeline-index">{String(i + 1).padStart(2, "0")}</span><article><div className="milestone-meta"><span>{m.template_name}</span><strong>{m.status_label}</strong></div><h3>{m.milestone_name}</h3><p>{m.reason}</p>{(m.supporting_facts.length > 0 || m.contradicting_facts.length > 0 || m.blocked_inferences.length > 0) && <details><summary>支持事实、冲突事实与推断边界</summary>{m.supporting_facts.map((f) => <FactCard key={f.fact_id} fact={f}/>)}{m.contradicting_facts.map((f) => <FactCard key={f.fact_id} fact={f}/>)}{m.blocked_inferences.map((b) => <p className="blocked" key={b}>不能据此推出：{b}</p>)}</details>}</article></li>)}</ol>}{blocked.length > 0 && <aside className="blocked-panel"><h3>不能据此推出</h3>{blocked.map((item) => <p key={item}>• {item}</p>)}</aside>}</section>;
}

function EvidenceAssertions({ report }: { report: EvidenceFirstReport }) {
  const a = report.evidence_assertions;
  return <section className="report-section" id="assertions"><div className="section-head"><p className="section-kicker">ASSERTION REVIEW</p><h2>证据归一与冲突检查</h2></div><div className="stat-strip">{[["原子事实", a.atomic_fact_count], ["证据断言", a.assertion_count], ["单来源", a.single_source_count], ["多来源", a.multi_source_support_count], ["冲突", a.conflict_count]].map(([label, value]) => <div key={String(label)}><strong>{value}</strong><span>{label}</span></div>)}</div>{a.multi_source_support_count === 0 && <p className="quiet-note">当前没有形成可安全归并的跨来源同一断言；系统保留各来源事实，未为提高聚合率而强行合并。</p>}{a.representative_assertions.length > 0 && <div className="assertion-grid">{a.representative_assertions.map((item) => <article className="assertion-card" key={item.assertion_id}><div>{item.assertion_type} · {item.evidence_status}</div><p>{item.representative_fact.subject} · {item.representative_fact.predicate}：{item.representative_fact.object_value}</p><small>{item.grouping_method} · {item.member_fact_count} 条事实 · {item.supporting_source_count} 个来源</small><CitationDisclosure citation={item.representative_fact.evidence}/></article>)}</div>}</section>;
}

function FinancialProfile({ report }: { report: EvidenceFirstReport }) {
  const profile = report.financial_profile;
  const shown = profile.dimensions.filter((d) => d.fact_count > 0);
  return <section className="report-section" id="finance"><div className="section-head"><p className="section-kicker">BUSINESS & FINANCIAL FACTS</p><h2>经营与财务事实</h2><span>{profile.fact_count} 条事实 · {shown.length} 个维度</span></div>{profile.fact_count === 0 ? <p className="empty-state">当前未形成可展示的经营与财务事实。</p> : <div className="dimension-grid">{shown.map((dimension, i) => <details className="dimension-card" open={i < 4} key={dimension.dimension_id}><summary><span>{dimension.dimension_name}</span><small>{dimension.fact_count} 条事实</small></summary><div>{dimension.representative_facts.map((fact) => <FactCard key={fact.fact_id} fact={fact}/>)}</div></details>)}</div>}</section>;
}

function TechnologyFinance({ report }: { report: EvidenceFirstReport }) {
  const f = report.technology_finance_links;
  return <section className="report-section" id="tech-finance"><div className="section-head"><p className="section-kicker">TECHNOLOGY × FINANCE</p><h2>技术—经营关联</h2></div><p className="section-intro">规则引擎根据已验证的技术阶段和经营事实，识别资金活动、风险关注及后续监测事项。</p><h3 className="subhead">适用规则 <small>{f.applicable_rules.length}</small></h3>{f.applicable_rules.length === 0 ? <p className="quiet-note">当前证据未满足已配置技术—金融映射规则的触发条件。</p> : <ul className="rule-list">{f.applicable_rules.map((rule) => <li key={rule.rule_id}><strong>{rule.rule_title}</strong><small>{rule.scenario_name ?? ""} · {rule.rule_id}</small></li>)}</ul>}<h3 className="subhead">资金活动关注</h3>{f.funding_activities.length === 0 ? <p className="empty-state">当前没有可展示的资金活动关注事项。</p> : f.funding_activities.map((activity, i) => <article className="observation" key={`${activity.scenario_id}-${i}`}><strong>{activity.status_label}</strong><p>{activity.activities.join("、")}</p><p>{activity.reason}</p><EvidenceBundleDisclosure bundle={activity.evidence}/></article>)}<h3 className="subhead">风险观察</h3>{f.risks.length === 0 ? <p className="quiet-note">当前规则未形成有证据支撑的技术—金融风险观察。</p> : f.risks.map((risk, i) => <article className="observation" key={`${risk.risk_theme}-${i}`}><strong>{risk.status_label} · {risk.risk_theme}</strong><p>{risk.reason}</p><EvidenceBundleDisclosure bundle={risk.evidence}/></article>)}<h3 className="subhead">后续监测</h3>{f.monitoring_nodes.length === 0 ? <p className="empty-state">当前没有配置后续监测节点。</p> : f.monitoring_nodes.map((node) => <article className="observation" key={node.node_id}><div className="milestone-meta"><strong>{node.name}</strong><span className={`status-pill ${statusClass[node.status] ?? "none"}`}>{node.status_label}</span></div><p>{node.status_explanation}</p><p>{node.reason}</p><small>建议后续获取：{node.required_evidence_types.join("、") || "—"}</small><EvidenceBundleDisclosure bundle={node.evidence}/></article>)}</section>;
}

function InformationGaps({ report }: { report: EvidenceFirstReport }) {
  const gaps = report.information_gaps;
  return <section className="report-section" id="gaps"><div className="section-head"><p className="section-kicker">INFORMATION GAPS</p><h2>待补充与待核验信息</h2><span>{gaps.deduplicated_financial_dimension_count} 个需要补证的财务/经营维度</span></div>{gaps.financial_gaps.map((gap) => <article className="gap-card" key={gap.dimension_id}><h3>{gap.dimension_name}</h3><p>{gap.description}</p><p><strong>建议补充：</strong>{gap.requested_fields.join("、") || "待补充相关材料"}</p>{gap.source_rule_ids.length > 0 && <small>触发规则：{gap.source_rule_ids.join("、")}</small>}</article>)}<h3 className="subhead">技术证据缺口</h3>{gaps.semantic_gaps.length === 0 ? <p className="empty-state">当前没有单独列示的技术证据缺口。</p> : <ul>{gaps.semantic_gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul>}</section>;
}

function SourceSummary({ report }: { report: EvidenceFirstReport }) {
  const s = report.source_summary;
  const weak = s.weak_web_count + s.snippet_only_count;
  const quality = Object.entries(s.source_quality_distribution);
  const max = Math.max(1, ...quality.map(([, n]) => n));
  return <section className="report-section" id="sources"><div className="section-head"><p className="section-kicker">SOURCE PROVENANCE</p><h2>来源与方法</h2><span>{s.evidence_source_count} 个证据来源</span></div>{weak > s.evidence_source_count / 2 && <p className="source-warning">当前报告主要由普通网页或搜索摘要支撑，适合线索整理与初步尽调；关键判断仍需补充一方或权威材料。</p>}<div className="source-bars">{quality.map(([name, count]) => <div className="source-bar" key={name}><div><span>{qualityLabels[name] ?? name}</span><strong>{count}</strong></div><i><b style={{ width: `${count / max * 100}%` }}/></i></div>)}</div><p className="source-types">来源类型：{Object.entries(s.source_type_distribution).map(([type, count]) => `${type} ${count}`).join(" · ") || "暂无"}</p><details className="method-details"><summary>方法与证据边界</summary><p>{report.methodology.positioning}</p>{report.methodology.evidence_boundary.map((item) => <p key={item}>{item}</p>)}{report.methodology.warnings.map((item) => <p className="quiet-note" key={item}>{item}</p>)}<details><summary>技术版本信息</summary><p>报告版本 · {report.report_version} · 报告 ID · {report.report_id}</p><pre>{JSON.stringify({ processors: report.methodology.processor_versions, registries: report.methodology.registry_versions }, null, 2)}</pre></details></details><p className="generated-at">报告生成时间：{new Date(report.generated_at).toLocaleString("zh-CN")}</p></section>;
}

function ReportPage({ report, onReset, onRefresh, refreshing, refreshError }: { report: EvidenceFirstReport; onReset: () => void; onRefresh: () => void; refreshing: boolean; refreshError: string | null }) {
  const facts = report.technology_profile.representative_facts;
  const overview = report.company_overview;
  return <div className="app-shell report-shell"><Header/><main><div className="report-actions"><span>V2 Evidence-First Report · {report.report_version}</span><div><button className="secondary-button" type="button" onClick={onRefresh} disabled={refreshing}>{refreshing ? "正在刷新…" : "刷新已保存报告"}</button><button className="secondary-button" type="button" onClick={onReset}>分析其他企业</button></div></div>{refreshError && <p className="inline-error" role="alert">{refreshError}</p>}<section className="report-hero"><div><p className="eyebrow">科技企业证据增强尽调报告</p><h1>{report.company_name}</h1><p className="company-name">{overview.primary_domain_names.join(" · ")}</p><div className="tag-row">{overview.selected_template_names.map((name) => <span key={name}>{name}</span>)}</div></div><div className="report-stats">{[[report.technology_profile.fact_count, "技术事实"], [report.financial_profile.fact_count, "财务事实"], [report.source_summary.evidence_source_count, "证据来源"], [report.technology_milestones.length, "技术里程碑"]].map(([value, label]) => <div key={String(label)}><strong>{value}</strong><span>{label}</span></div>)}</div></section><section className="company-overview report-section"><div><p className="section-kicker">COMPANY OVERVIEW</p><h2>企业概览</h2></div><dl><div><dt>规范名称</dt><dd>{overview.canonical_name}</dd></div><div><dt>别名</dt><dd>{overview.aliases.join("、") || "暂无"}</dd></div><div><dt>主体状态</dt><dd>{overview.resolution_status}</dd></div><div><dt>官方网站</dt><dd>{overview.official_website ? <a href={overview.official_website} target="_blank" rel="noreferrer">{overview.official_website} ↗</a> : "未完成强自证验证"}</dd></div><div><dt>领域</dt><dd>{overview.primary_domain_names.join("、") || "暂无"}</dd></div><div><dt>技术模板</dt><dd>{overview.selected_template_names.join("、") || "暂无"}</dd></div></dl>{!report.source_summary.official_website_verified && <p className="quiet-note">当前未取得企业官网的强自证来源，普通网页证据不会被提升为一方来源。</p>}</section><nav className="report-nav" aria-label="报告章节导航">{[["technology-stage", "技术阶段"], ["technology-facts", "技术证据"], ["finance", "经营财务"], ["tech-finance", "技术—经营"], ["gaps", "待补充"], ["sources", "来源"]].map(([id, label]) => <a href={`#${id}`} key={id}>{label}</a>)}</nav><TechnologyTimeline milestones={report.technology_milestones}/><Facts title="技术证据" facts={facts} empty="当前未抽取到满足证据边界的技术事实。"/><EvidenceAssertions report={report}/><FinancialProfile report={report}/><TechnologyFinance report={report}/><InformationGaps report={report}/><SourceSummary report={report}/></main><Footer/></div>;
}

function EntityNotResolved({ onReset }: { onReset: () => void }) { return <div className="app-shell"><Header/><main className="terminal-state"><p className="eyebrow">ENTITY RESOLUTION</p><h1>未能确认唯一企业主体</h1><p>当前公开检索结果不足以唯一确认企业身份，因此系统没有继续生成技术与经营判断。建议使用更完整的企业法定名称后重试。</p><button className="primary-button" type="button" onClick={onReset}>重新输入企业名称</button></main><Footer/></div>; }
function FailedPage({ task, onReset }: { task: EvidenceAnalysisTaskResponse; onReset: () => void }) { return <div className="app-shell"><Header/><main className="terminal-state"><p className="eyebrow">ANALYSIS INTERRUPTED</p><h1>分析流程未完成</h1><p>{task.error?.message ?? "分析任务失败，请稍后重试。"}</p><details><summary>阶段、错误类别与代码</summary><p>阶段：{task.error?.stage ?? task.current_stage}</p><p>类别：{task.error?.category ?? "未提供"}</p><p>代码：{task.error?.code ?? "未提供"}</p></details><button className="primary-button" type="button" onClick={onReset}>重新输入企业名称</button></main><Footer/></div>; }
const wait = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

function App() {
  const [name, setName] = useState(""); const [task, setTask] = useState<EvidenceAnalysisTaskResponse | null>(null);
  const [report, setReport] = useState<EvidenceFirstReport | null>(null); const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null); const [refreshing, setRefreshing] = useState(false); const [refreshError, setRefreshError] = useState<string | null>(null);
  const [reportRequested, setReportRequested] = useState(false);
  const reset = useCallback(() => { setName(""); setTask(null); setReport(null); setBusy(false); setReportRequested(false); setError(null); setRefreshError(null); history.replaceState(null, "", window.location.pathname); window.scrollTo({ top: 0, behavior: "smooth" }); }, []);
  const loadCompanyReport = useCallback(async (id: string) => { setRefreshing(true); setRefreshError(null); try { setReport(await getEvidenceReport(id)); } catch (e) { setRefreshError(e instanceof Error ? e.message : "读取已保存报告失败。"); } finally { setRefreshing(false); } }, []);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const id = params.get("company_id");
    if (id) { setReportRequested(true); void loadCompanyReport(id); }
    else if (params.get("sample") === "1") void fetch("/evidence_report_sample.json").then((response) => {
      if (!response.ok) throw new Error("合成报告样例不可用。");
      return response.json() as Promise<EvidenceFirstReport>;
    }).then(setReport).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "样例加载失败。"));
  }, [loadCompanyReport]);
  const poll = async (id: string) => {
    for (let attempt = 0; attempt < 900; attempt += 1) {
      const current = await getEvidenceAnalysis(id); setTask(current);
      if (current.status === "completed" && current.result_status === "entity_not_resolved") { setBusy(false); return; }
      if (current.status === "failed") { setBusy(false); return; }
      if (current.status === "completed" && current.report) { setReport(current.report); setBusy(false); if (current.company_id) history.replaceState(null, "", `?company_id=${encodeURIComponent(current.company_id)}`); return; }
      await wait(1000);
    }
    throw new Error("分析仍在进行，等待时间已达到上限。可稍后重试或重新打开已保存报告。");
  };
  const submit = async (event: FormEvent) => { event.preventDefault(); setError(null); if (!name.trim()) { setError("请输入企业名称。"); return; } setBusy(true); setTask(null); setReport(null); try { const created = await createEvidenceAnalysis(name); await poll(created.task_id); } catch (e) { setBusy(false); setError(e instanceof Error ? e.message : "创建分析任务失败。"); } };
  if (report) return <ReportPage report={report} onReset={reset} onRefresh={() => void loadCompanyReport(report.company_id)} refreshing={refreshing} refreshError={refreshError}/>;
  if (reportRequested) return <div className="app-shell"><Header/><main className="terminal-state"><p className="eyebrow">PERSISTED REPORT</p><h1>{refreshError ? "报告暂不可用" : "正在读取已保存报告"}</h1>{refreshError ? <p role="alert">{refreshError}</p> : <p role="status">正在从知识库读取该企业的 V2 报告…</p>}<button className="secondary-button" type="button" onClick={reset}>返回首页</button></main><Footer/></div>;
  if (task?.status === "completed" && task.result_status === "entity_not_resolved") return <EntityNotResolved onReset={reset}/>;
  if (task?.status === "failed") return <FailedPage task={task} onReset={reset}/>;
  if (task && busy) return <ProgressPage task={task} onReset={reset}/>;
  return <StartPage name={name} onName={setName} onSubmit={(e) => void submit(e)} busy={busy} error={error}/>;
}

export default App;
