import { useEffect, useState, type FormEvent } from "react";

type NullableScore = number | null;
type AnalysisStatus = "processing" | "completed" | "partial" | "failed";
type ProductMode = "rule_demo" | "real_model";

interface ReportEvidence {
  evidence_id: string;
  document_name: string;
  page_number: number | null;
  chunk_id: string;
  excerpt: string;
  retrieval_score: number;
}

interface ReportFinding {
  content: string;
  evidence: ReportEvidence[];
}

interface IndicatorAssessment {
  score: NullableScore;
  evidence: string[];
  rationale: string;
}

interface ScoreExplanation {
  indicator_id?: string;
  indicator_name?: string;
  status: string;
  input_score?: number;
  weight?: number;
  weighted_score?: number | null;
  calculation?: string;
  indicator_version?: string;
  weight_version?: string;
  reason?: string;
}

interface TechnologyReport {
  task_id: string | null;
  enterprise_name: string;
  title: string;
  summary: string;
  technology_score: NullableScore;
  dimension_scores: Record<string, NullableScore>;
  strengths: ReportFinding[];
  risks: ReportFinding[];
  evaluation_details: {
    indicators: Record<string, IndicatorAssessment>;
    score_explanation: ScoreExplanation[];
    evidence_mapping: unknown[];
  };
  references: ReportEvidence[];
  industry_score?: NullableScore;
  overall_score?: NullableScore;
  assessment_status?: string;
  evidence_coverage?: Record<string, number>;
  industry_analysis?: { indicators?: Record<string, IndicatorAssessment>; evaluation_details?: Record<string, unknown> };
}

interface ModuleResult { status: string; reason?: string; failure?: { code: string; category?: string; message: string }; analysis?: Record<string, any>; result?: Record<string, any>; evaluation?: Record<string, any> }
interface ModuleFailure { code: string; category?: string; message: string; validation_errors?: Array<{ field: string; type: string; message: string }> }

interface AnalysisCreateResponse {
  task_id: string;
  status: AnalysisStatus;
}

interface AnalysisTaskResponse {
  task_id: string;
  status: AnalysisStatus;
  enterprise_name: string;
  file_name: string;
  report: TechnologyReport | null;
  error: { code: string; message: string } | null;
  modules: Record<string, ModuleResult>;
  module_failures: Record<string, ModuleFailure>;
  run_info: Record<string, any>;
  request_mode: ProductMode;
  result_status?: string;
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

const dimensionLabels: Record<string, string> = {
  innovation: "创新维度",
  ip: "知识产权",
  maturity: "技术成熟度",
};

const indicatorLabels: Record<string, string> = {
  technical_autonomy: "技术自主性",
  innovation_capability: "创新能力",
  intellectual_property: "知识产权能力",
  technical_maturity: "技术成熟度",
};

function formatScore(score: NullableScore) {
  return score === null ? "未评分" : score.toFixed(score % 1 === 0 ? 0 : 2);
}

function ModuleFailureNotice({ domain, failure }: { domain: string; failure: ModuleFailure }) {
  return <div className="inline-error"><p>{domain}模块失败：{failure.message}</p>{failure.validation_errors?.length ? <details><summary>结构化校验诊断</summary><ul>{failure.validation_errors.map((item, index) => <li key={`${item.field}-${index}`}>{item.field} · {item.type} · {item.message}</li>)}</ul></details> : null}</div>;
}

function AppHeader({ badge }: { badge: string }) {
  return (
    <header className="topbar">
      <a className="brand" href="#top" aria-label="科衡首页">
        <span className="brand-mark">科</span>
        <span>
          <strong>科衡</strong>
          <small>KeHeng · v0.7</small>
        </span>
      </a>
      <div className="product-title">科技企业智能尽调与价值评估系统</div>
      <span className="stage-badge">{badge}</span>
    </header>
  );
}

function EvidenceDisclosure({ evidence }: { evidence: ReportEvidence }) {
  return (
    <details className="evidence-disclosure">
      <summary>
        <span className="evidence-id">{evidence.evidence_id}</span>
        <span>{evidence.document_name}</span>
        <small>
          {evidence.page_number === null ? "页码未知" : `第 ${evidence.page_number} 页`}
        </small>
      </summary>
      <div className="evidence-content">
        <p>{evidence.excerpt}</p>
        <footer>
          <span>片段 {evidence.chunk_id}</span>
          <span>检索相关度 {evidence.retrieval_score.toFixed(4)}</span>
        </footer>
      </div>
    </details>
  );
}

function FindingSection({
  title,
  eyebrow,
  findings,
  tone,
}: {
  title: string;
  eyebrow: string;
  findings: ReportFinding[];
  tone: "positive" | "risk";
}) {
  return (
    <section className={`finding-section ${tone}`}>
      <div className="section-kicker">{eyebrow}</div>
      <h2>{title}</h2>
      {findings.length === 0 ? (
        <p className="empty-finding">当前材料未形成有充分证据的相关结论。</p>
      ) : (
        <div className="finding-list">
          {findings.map((finding, index) => (
            <article className="finding-card" key={`${tone}-${index}`}>
              <div className="finding-number">{String(index + 1).padStart(2, "0")}</div>
              <div>
                <h3>{finding.content}</h3>
                <div className="finding-evidence">
                  {finding.evidence.map((evidence) => (
                    <EvidenceDisclosure
                      evidence={evidence}
                      key={`${index}-${evidence.evidence_id}`}
                    />
                  ))}
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function DomainIndicatorPanel({
  title, indicators, evidenceById,
}: {
  title: string;
  indicators: Record<string, IndicatorAssessment>;
  evidenceById: Map<string, ReportEvidence>;
}) {
  const labels: Record<string, string> = { ...indicatorLabels, market_potential: "市场潜力", industry_growth: "行业成长性", competitive_position: "竞争位置", policy_environment: "政策环境" };
  if (!Object.keys(indicators).length) return null;
  return <section className="method-section"><h2>{title}</h2><div className="indicator-grid">{Object.entries(indicators).map(([id, item]) => <article key={id}><strong>{labels[id] ?? id} · {formatScore(item.score)}</strong><p>{item.rationale}</p>{item.evidence?.length ? item.evidence.map((ref) => { const evidence = evidenceById.get(ref); return evidence ? <EvidenceDisclosure key={`${id}-${ref}`} evidence={evidence} /> : <span className="invalid-reference" key={`${id}-${ref}`}>引用 {ref} 无法定位</span>; }) : <small>未检出可定位支持证据</small>}</article>)}</div></section>;
}

function PartialResultPage({ modules, failures, runInfo, onReset }: { modules: Record<string, ModuleResult>; failures: Record<string, ModuleFailure>; runInfo: Record<string, any>; onReset: () => void }) {
  const evidenceById = new Map<string, ReportEvidence>();
  for (const module of Object.values(modules)) for (const item of module.analysis?.evidence ?? module.result?.evidence ?? []) evidenceById.set(item.evidence_id, item as ReportEvidence);
  const tech = modules.technology?.analysis?.technology_indicators ?? modules.technology?.result?.technology_indicators ?? {};
  const industry = modules.industry?.analysis?.industry_indicators ?? modules.industry?.result?.industry_indicators ?? {};
  return <div className="report-shell"><AppHeader badge="部分模块结果"/><main id="top"><div className="report-actions"><strong>分析结果保留了成功模块</strong><button className="secondary-button" onClick={onReset}>返回</button></div><div className="inline-error">系统处理失败与企业资料证据不足分开显示。</div>{Object.entries(failures).map(([id, failure]) => <ModuleFailureNotice key={id} domain={id === "industry" ? "产业" : "技术"} failure={failure} />)}<DomainIndicatorPanel title="技术指标" indicators={tech} evidenceById={evidenceById}/><DomainIndicatorPanel title="产业指标" indicators={industry} evidenceById={evidenceById}/><details className="method-section"><summary>运行配置</summary><pre>{JSON.stringify(runInfo, null, 2)}</pre></details></main></div>;
}

function ReportPage({
  report,
  sourceLabel,
  modules = {},
  moduleFailures = {},
  runInfo = {},
  requestMode = "rule_demo",
  onReset,
}: {
  report: TechnologyReport;
  sourceLabel: string;
  modules?: Record<string, ModuleResult>;
  moduleFailures?: Record<string, ModuleFailure>;
  runInfo?: Record<string, any>;
  requestMode?: string;
  onReset: () => void;
}) {
  const isTechnologyReport = "score_explanation" in report.evaluation_details;
  const techEvaluation = isTechnologyReport ? report.evaluation_details : (report.evaluation_details as any).technology as TechnologyReport["evaluation_details"] | undefined;
  const explanations = (techEvaluation?.score_explanation ?? []).filter(
    (item) => item.indicator_id,
  );
  const technologyIndicators = (modules.technology?.analysis?.technology_indicators ?? modules.technology?.result?.technology_indicators ?? (isTechnologyReport ? report.evaluation_details.indicators : {})) as Record<string, IndicatorAssessment>;
  const industryIndicators = (modules.industry?.analysis?.industry_indicators ?? report.industry_analysis?.indicators ?? {}) as Record<string, IndicatorAssessment>;
  const evidenceById = new Map(report.references.map((item) => [item.evidence_id, item]));
  for (const module of Object.values(modules)) for (const item of module.analysis?.evidence ?? module.result?.evidence ?? []) evidenceById.set(item.evidence_id, item as ReportEvidence);
  const scoredCount = (items: Record<string, IndicatorAssessment>) => Object.values(items).filter((item) => item?.score !== null && item?.score !== undefined).length;
  const materialSuggestions: Record<string, string> = {
    technical_autonomy: "技术架构、研发权属说明及外部技术依赖清单",
    innovation_capability: "研发投入、研发人员和产品迭代记录",
    intellectual_property: "专利/软著清单、权属证明及法律状态",
    technical_maturity: "测试验证、客户试点、部署或交付材料",
    market_potential: "市场研究、客户需求、订单或商业化资料",
    industry_growth: "行业统计、市场趋势或增长预测来源",
    competitive_position: "市场份额、客户应用、行业排名或竞品分析",
    policy_environment: "适用政策文件、补贴依据及合规说明",
  };
  const allIndicators = [...Object.entries(technologyIndicators), ...Object.entries(industryIndicators)];
  const missingIndicators = allIndicators.filter(([, item]) => item?.score === null || item?.score === undefined);

  return (
    <div className="report-shell">
      <AppHeader badge={sourceLabel} />

      <main id="top">
        <div className="report-actions">
          <span>{report.task_id ? `任务 ${report.task_id}` : "内置虚构样例"}</span>
          <button className="secondary-button" type="button" onClick={onReset}>
            分析其他企业
          </button>
        </div>

        <section className="report-hero">
          <div className="report-intro">
            <p className="eyebrow">{runInfo.saved_result_replay ? "SAVED REAL MODEL RESULT · NO LIVE CALL" : requestMode === "real_model" ? "REAL MODEL ANALYSIS" : requestMode === "test_substitute" ? "TEST SUBSTITUTE RESULT" : "RULE DEMONSTRATION"}</p>
            <h1>{report.title}</h1>
            <p className="company-name">{report.enterprise_name}</p>
            <p className="report-summary">{report.summary}</p>
            {runInfo.saved_result_replay && <div className="report-boundary">历史保存结果回放 · 模型 {runInfo.saved_result_replay.model ?? "未知"} · {runInfo.saved_result_replay.run_date ?? "运行日期未知"} · 打开此报告不会调用模型。</div>}
            <div className="report-boundary">
              本报告基于已提供材料生成，仅用于辅助分析；所有结论均需人工复核。
            </div>
          </div>

          <aside className="score-card" aria-label="技术综合评分">
              <span>{report.overall_score !== undefined ? "综合参考分" : "技术参考分"}</span>
            <div className="score-value">
              {formatScore(report.overall_score ?? report.technology_score)}
              <small>/ 100</small>
            </div>
            <div className="score-rule" />
            <p>分数只覆盖已评分指标；技术 {scoredCount(technologyIndicators)}/{Object.keys(technologyIndicators).length || 4} 项，产业 {Object.keys(industryIndicators).length ? `${scoredCount(industryIndicators)}/${Object.keys(industryIndicators).length} 项` : "未运行"}。未评分项见下方清单。</p>
          </aside>
        </section>

        <section className="score-section" aria-labelledby="score-title">
          <div className="section-heading">
            <div>
              <p className="section-kicker">SCORE OVERVIEW</p>
              <h2 id="score-title">技术与产业结果</h2>
            </div>
            <p>维度分和指标分分别来自评价结果与 Technology Agent 结构化输出。</p>
          </div>

          <div className="dimension-grid">
            {Object.entries(report.dimension_scores ?? {}).map(([key, score]) => (
              <article key={key}>
                <span>{dimensionLabels[key] ?? key}</span>
                <strong>{formatScore(score)}</strong>
                <div className="score-track" aria-hidden="true">
                  <i style={{ width: `${score ?? 0}%` }} />
                </div>
              </article>
            ))}
          </div>

          <div className="indicator-panel">
            <div className="indicator-header">
              <span>指标</span>
              <span>指标分</span>
              <span>权重</span>
              <span>加权得分</span>
              <span>证据</span>
            </div>
            {explanations.map((item) => {
              const indicator = technologyIndicators[
                item.indicator_id ?? ""
              ];
              return (
                <div className="indicator-row" key={item.indicator_id}>
                  <div>
                    <strong>
                      {item.indicator_name ??
                        indicatorLabels[item.indicator_id ?? ""] ??
                        item.indicator_id}
                    </strong>
                    <small>{indicator?.rationale}</small>
                  </div>
                  <span>{formatScore(indicator?.score ?? null)}</span>
                  <span>{item.weight === undefined ? "—" : `${item.weight * 100}%`}</span>
                  <span>
                    {item.weighted_score === undefined || item.weighted_score === null
                      ? "—"
                      : item.weighted_score.toFixed(2)}
                  </span>
                  <div className="evidence-tags">
                    {indicator?.evidence.map((evidenceId) => (
                      <a href={`#reference-${evidenceId}`} key={evidenceId}>
                        {evidenceId}
                      </a>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
          {requestMode === "real_model" && <DomainIndicatorPanel title="产业指标" indicators={industryIndicators} evidenceById={evidenceById} />}
          <DomainIndicatorPanel title="技术指标证据" indicators={technologyIndicators} evidenceById={evidenceById} />
          {(modules.technology?.status === "failed" || modules.industry?.status === "failed" || Object.keys(moduleFailures).length > 0) && <section role="alert"><div className="inline-error">系统处理失败与资料缺少证据分开记录。</div>{Object.entries(moduleFailures).map(([key, failure]) => <ModuleFailureNotice key={key} domain={key === "industry" ? "产业" : "技术"} failure={failure} />)}</section>}
          {missingIndicators.length > 0 && <section className="method-section"><h2>待补充指标与材料建议</h2>{missingIndicators.map(([id, item]) => <p key={id}><strong>{indicatorLabels[id] ?? id}</strong>：当前未检出可支持评分的证据。建议补充：{materialSuggestions[id] ?? "可核验的业务材料"}。已有材料：{item?.rationale ?? "系统未生成该指标判断"}</p>)}</section>}
          <details className="method-section"><summary>运行配置与复现信息</summary><pre>{JSON.stringify({ requestMode, actualMode: runInfo.actual_mode, retrieval: runInfo.retrieval, sourceFingerprint: runInfo.source_fingerprint_sha256, model: runInfo.model_request_config }, null, 2)}</pre></details>
        </section>

        <div className="findings-grid">
          <FindingSection
            eyebrow="TECHNICAL STRENGTHS"
            findings={report.strengths}
            title="核心技术优势"
            tone="positive"
          />
          <FindingSection
            eyebrow="RISK FACTORS"
            findings={report.risks}
            title="风险因素"
            tone="risk"
          />
        </div>

        <section className="method-section" aria-labelledby="method-title">
          <div className="section-heading">
            <div>
              <p className="section-kicker">EVALUATION TRACE</p>
              <h2 id="method-title">评价依据</h2>
            </div>
            <p>完整保留指标版本、权重版本、计算公式及参与评分的证据编号。</p>
          </div>
          <div className="calculation-grid">
            {explanations.map((item) => (
              <article key={`calculation-${item.indicator_id}`}>
                <span>{item.indicator_name}</span>
                <strong>{item.calculation ?? item.reason ?? "未评分"}</strong>
                <small>
                  指标版本 {item.indicator_version ?? "—"} · 权重版本 {item.weight_version ?? "—"}
                </small>
              </article>
            ))}
          </div>
        </section>

        <section className="reference-section" aria-labelledby="reference-title">
          <div className="section-heading">
            <div>
              <p className="section-kicker">EVIDENCE INDEX</p>
              <h2 id="reference-title">证据索引</h2>
            </div>
            <p>点击证据条目可查看来源文档、页码、原文片段和检索相关度。</p>
          </div>
          <div className="reference-list">
            {report.references.map((evidence) => (
              <div id={`reference-${evidence.evidence_id}`} key={evidence.evidence_id}>
                <EvidenceDisclosure evidence={evidence} />
              </div>
            ))}
          </div>
        </section>
      </main>

      <footer className="site-footer">
        <span>KeHeng · 科衡</span>
        <span>AI 辅助分析不替代人工尽调与正式决策</span>
      </footer>
    </div>
  );
}

function UploadPage({
  enterpriseName,
  file,
  phase,
  taskId,
  mode,
  error,
  onEnterpriseNameChange,
  onModeChange,
  onFileChange,
  onSubmit,
  onLoadSample,
  onLoadSavedReplay,
  replayAvailable,
}: {
  enterpriseName: string;
  file: File | null;
  phase: "idle" | "uploading" | "processing" | "error";
  taskId: string | null;
  mode: ProductMode;
  error: string | null;
  onEnterpriseNameChange: (value: string) => void;
  onModeChange: (value: ProductMode) => void;
  onFileChange: (file: File | null) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onLoadSample: () => void;
  onLoadSavedReplay: () => void;
  replayAvailable: boolean;
}) {
  const isBusy = phase === "uploading" || phase === "processing";
  return (
    <div className="upload-shell">
      <AppHeader badge="技术评估工作台" />
      <main className="upload-main" id="top">
        <section className="upload-copy">
          <p className="eyebrow">EVIDENCE-BOUND DUE DILIGENCE</p>
          <h1>让技术价值判断<br />回到证据与规则</h1>
          <p>
            上传企业技术资料，科衡将依次完成文本解析、任务级知识检索、技术指标提取、
            确定性评价与可解释报告生成。
          </p>
          <div className="workflow-line">
            <span>PDF 解析</span><i />
            <span>RAG 检索</span><i />
            <span>指标提取</span><i />
            <span>确定性评分</span>
          </div>
        </section>

        <section className="upload-panel" aria-labelledby="upload-title">
          <div>
            <p className="section-kicker">CREATE ANALYSIS</p>
            <h2 id="upload-title">创建技术评估任务</h2>
          </div>
          <form onSubmit={onSubmit}>
            <fieldset className="mode-select"><legend>分析模式</legend><label><input type="radio" name="mode" checked={mode === "rule_demo"} disabled={isBusy} onChange={() => onModeChange("rule_demo")} />规则演示（无密钥，仅技术分析）</label><label><input type="radio" name="mode" checked={mode === "real_model"} disabled={isBusy} onChange={() => onModeChange("real_model")} />真实模型（技术 + 产业）</label><small>真实模型凭据只由后端读取；未配置时会明确提示，不会切回规则模式。</small></fieldset>
            <label>
              <span>企业名称</span>
              <input
                type="text"
                maxLength={120}
                placeholder="请输入待评估企业名称"
                value={enterpriseName}
                disabled={isBusy}
                onChange={(event) => onEnterpriseNameChange(event.target.value)}
              />
            </label>
            <label className="file-field">
              <span>技术资料 PDF</span>
              <input
                type="file"
                accept="application/pdf,.pdf"
                disabled={isBusy}
                onChange={(event) => onFileChange(event.target.files?.[0] ?? null)}
              />
              <strong>{file ? file.name : "选择一份文本型 PDF"}</strong>
              <small>当前版本最大 20 MB，扫描件暂不支持 OCR。</small>
            </label>
            <button className="primary-button" type="submit" disabled={isBusy}>
              {phase === "uploading"
                ? "正在上传…"
                : phase === "processing"
                  ? "正在分析…"
                  : mode === "real_model" ? "开始技术与产业分析" : "开始规则演示分析"}
            </button>
          </form>

          {isBusy && (
            <div className="task-status" role="status">
              <span className="status-spinner" />
              <div>
                <strong>{phase === "uploading" ? "正在提交资料" : "正在构建证据链并生成报告"}</strong>
                <small>{taskId ? `任务编号：${taskId}` : "请稍候，不要关闭页面"}</small>
              </div>
            </div>
          )}
          {error && <div className="inline-error" role="alert">{error}</div>}

          <div className="sample-entry">
            <span>暂时没有可上传资料？</span>
            <button type="button" onClick={onLoadSample} disabled={isBusy}>
              快速查看内置虚构样例
            </button>
            <a className="secondary-button" href="/api/analysis/demo-material">下载合成 PDF 后上传</a>
          </div>
          {replayAvailable && <div className="sample-entry replay-entry"><span>本机可选历史报告回放（仅读取已保存结果，不触发模型调用）</span><button type="button" onClick={onLoadSavedReplay} disabled={isBusy}>打开 NIO 已保存模型报告</button></div>}
        </section>
      </main>
      <footer className="site-footer">
        <span>KeHeng · 科衡</span>
        <span>资料在本地任务空间处理，分析结论需人工复核</span>
      </footer>
    </div>
  );
}

async function responseError(response: Response): Promise<string> {
  try {
    const payload = await response.json() as {
      detail?: string | { message?: string };
    };
    if (typeof payload.detail === "string") return payload.detail;
    if (payload.detail?.message) return payload.detail.message;
  } catch {
    // Fall through to the stable HTTP message below.
  }
  return `请求失败：HTTP ${response.status}`;
}

const wait = (milliseconds: number) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds));

function App() {
  const [enterpriseName, setEnterpriseName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<"idle" | "uploading" | "processing" | "error">("idle");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [report, setReport] = useState<TechnologyReport | null>(null);
  const [partialOnly, setPartialOnly] = useState(false);
  const [modules, setModules] = useState<Record<string, ModuleResult>>({});
  const [moduleFailures, setModuleFailures] = useState<Record<string, ModuleFailure>>({});
  const [runInfo, setRunInfo] = useState<Record<string, any>>({});
  const [mode, setMode] = useState<ProductMode>("rule_demo");
  const [actualMode, setActualMode] = useState<string>("rule_demo");
  const [reportSource, setReportSource] = useState("动态分析报告");
  const [error, setError] = useState<string | null>(null);
  const [replayAvailable, setReplayAvailable] = useState(false);

  const reset = () => {
    setEnterpriseName("");
    setFile(null);
    setPhase("idle");
    setTaskId(null);
    setReport(null);
    setPartialOnly(false);
    setModules({}); setModuleFailures({}); setRunInfo({});
    setError(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const pollTask = async (id: string) => {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const response = await fetch(`${API_BASE_URL}/analysis/${id}`);
      if (!response.ok) throw new Error(await responseError(response));
      const task = await response.json() as AnalysisTaskResponse;
      if (task.status === "completed" && task.report) {
        setReport(task.report as TechnologyReport);
        setModules(task.modules ?? {}); setModuleFailures(task.module_failures ?? {}); setRunInfo(task.run_info ?? {}); setActualMode(task.run_info?.actual_mode ?? task.request_mode);
        setReportSource(task.result_status === "partial" ? "部分模块完成" : "动态分析报告");
        return;
      }
      if ((task.status === "partial" || task.status === "failed") && task.report) {
        setReport(task.report as TechnologyReport); setModules(task.modules ?? {}); setModuleFailures(task.module_failures ?? {}); setRunInfo(task.run_info ?? {}); setActualMode(task.run_info?.actual_mode ?? task.request_mode); setReportSource("部分模块完成"); return;
      }
      if ((task.status === "partial" || task.status === "failed") && (Object.keys(task.module_failures ?? {}).length > 0 || task.result_status === "failed")) {
        setModules(task.modules ?? {}); setModuleFailures(task.module_failures ?? {}); setRunInfo(task.run_info ?? {}); setActualMode(task.run_info?.actual_mode ?? task.request_mode); setPartialOnly(true); setReportSource("模块处理失败"); return;
      }
      if (task.status === "failed") {
        throw new Error(task.error?.message ?? "分析任务失败，请重试。");
      }
      await wait(1000);
    }
    throw new Error("分析等待超时，请稍后使用任务编号重新查询。");
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    if (!enterpriseName.trim()) {
      setPhase("error");
      setError("请输入企业名称。");
      return;
    }
    if (!file) {
      setPhase("error");
      setError("请选择 PDF 技术资料。");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setPhase("error");
      setError("仅支持 PDF 文件。");
      return;
    }

    try {
      setPhase("uploading");
      const form = new FormData();
      form.append("enterprise_name", enterpriseName.trim());
      form.append("file", file);
      form.append("analysis_mode", mode);
      const response = await fetch(`${API_BASE_URL}/analysis/create`, {
        method: "POST",
        body: form,
      });
      if (!response.ok) throw new Error(await responseError(response));
      const task = await response.json() as AnalysisCreateResponse;
      setTaskId(task.task_id);
      setActualMode(mode);
      setPhase("processing");
      await pollTask(task.task_id);
    } catch (reason) {
      setPhase("error");
      setError(reason instanceof Error ? reason.message : "分析失败，请重试。");
    }
  };

  const loadSample = async () => {
    try {
      setError(null);
      setPhase("uploading");
      const response = await fetch("/technology_report.json");
      if (!response.ok) throw new Error(`样例加载失败：HTTP ${response.status}`);
      const sample = await response.json() as TechnologyReport;
      setReport(sample);
      setModules({}); setModuleFailures({}); setRunInfo({ actual_mode: "rule_demo", retrieval: { retriever: "hash" } }); setActualMode("rule_demo");
      setPartialOnly(false);
      setReportSource("内置虚构样例 · 规则展示数据");
      setPhase("idle");
    } catch (reason) {
      setPhase("error");
      setError(reason instanceof Error ? reason.message : "样例加载失败。");
    }
  };

  const loadSavedReplay = async () => {
    try {
      setError(null);
      setPhase("uploading");
      const response = await fetch(`${API_BASE_URL}/analysis/replays/iteration04-nio-hash`);
      if (!response.ok) throw new Error(await responseError(response));
      const saved = await response.json() as AnalysisTaskResponse;
      if (!saved.report) throw new Error("保存的报告内容为空。");
      setReport(saved.report);
      setModules(saved.modules ?? {});
      setModuleFailures(saved.module_failures ?? {});
      setRunInfo(saved.run_info ?? {});
      setActualMode("real_model");
      setPartialOnly(false);
      setReportSource("已保存真实模型结果 · 历史回放");
      setPhase("idle");
    } catch (reason) {
      setPhase("error");
      setError(reason instanceof Error ? reason.message : "读取保存报告失败。");
    }
  };

  useEffect(() => {
    void fetch(`${API_BASE_URL}/analysis/replays/iteration04-nio-hash/availability`)
      .then((response) => response.ok ? response.json() as Promise<{ available: boolean }> : { available: false })
      .then((result) => setReplayAvailable(Boolean(result.available)))
      .catch(() => setReplayAvailable(false));
    if (new URLSearchParams(window.location.search).get("replay") === "iteration04-nio-hash") {
      void loadSavedReplay();
    }
  }, []);

  if (report) {
    return <ReportPage report={report} sourceLabel={reportSource} modules={modules} moduleFailures={moduleFailures} runInfo={runInfo} requestMode={actualMode} onReset={reset} />;
  }
  if (partialOnly) return <PartialResultPage modules={modules} failures={moduleFailures} runInfo={runInfo} onReset={reset} />;
  return (
    <UploadPage
      enterpriseName={enterpriseName}
      file={file}
      phase={phase}
      taskId={taskId}
      mode={mode}
      error={error}
      onEnterpriseNameChange={setEnterpriseName}
      onModeChange={setMode}
      onFileChange={setFile}
      onSubmit={submit}
      onLoadSample={loadSample}
      onLoadSavedReplay={loadSavedReplay}
      replayAvailable={replayAvailable}
    />
  );
}

export default App;
