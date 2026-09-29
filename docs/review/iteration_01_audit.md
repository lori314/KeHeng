# Iteration 01：实现与实验基线审计

日期：2026-09-28（Windows 11，Python 3.12.10）

## 审计边界与 Git 状态

已阅读 AGENTS.md、README、模块说明、需求/设计/数据/模型/测试文档、实现、测试、实验脚本、提示词、配置及案例 manifest。仓库刚初始化且无 commit（master，git ls-files 为 0）；开始时所有项目文件均未跟踪，全部视为既有工作并保留。runtime 中已有旧实验原始记录、Chroma 和企业运行产物，均被 .gitignore 排除，本轮只读，不覆盖。未修改代码、配置、提示词、论文或旧实验结果；只新增本目录三份交付。没有启动真实模型或付费请求。

## 实际实现

| 阶段 | 入口、输入输出 | 当前实现/配置 | 状态 |
|---|---|---|---|
| 上传 | backend/app/api/routes/analysis.py:create_analysis；企业名+multipart PDF → task_id/processing | 扩展名、MIME、%PDF-签名、空文件、20 MB 校验；写 runtime/analysis/uploads/<task_id>/source.pdf；FastAPI BackgroundTasks | 已验证：API 测试覆盖正常、空、伪 PDF、损坏、无文本 |
| 编排/任务 | backend/app/services/analysis_tasks.py:AnalysisTaskManager.process | 内存任务状态；重启丢失；安全错误码；意外异常通用失败 | 已验证单测 |
| 解析 | backend/app/rag/document_parser.py:PdfDocumentParser.load | PyMuPDF 文本提取、按页保存页码、SHA-256/parser_version；无 OCR；密码/无文本失败 | 已验证：六家年报 hash 与 manifest 一致且有文本 |
| 切分 | PdfDocumentParser.split/_split_page | 每页独立切分，不跨页；主流程 520 字符 chunk、80 字符 overlap；按标点/换行回退 | 代码可见，主流程配置已核 |
| 向量化/存储 | backend/app/rag/embedding.py、knowledge_base.py | sklearn HashingVectorizer 字符 2–4 gram、1024 维、L2，版本 local-char-ngram-v1；Chroma cosine。不是语义 embedding 模型 | 已验证 |
| 查询/检索 | TechnologyAgent._queries/_retrieve、IndustryAgent._queries/_retrieve | 各 4 条固定中文 query；每条 top_k=3，最多 12 块；LLM 前按相关分排序、压缩重复，最多 8 块/18,000 字符。无 BM25、query rewrite、阈值、reranker | 已验证代码和旧 Chroma 检索 |
| 技术 Agent | backend/app/agents/technology_agent.py | Rule 是关键词判断/人工固定分档；LLM 输出四项技术指标分或 null、rationale/证据，不输出总分。提示词 prompts/technology_agent_prompt.md | 已验证代码/契约/测试 |
| 产业 Agent | backend/app/agents/industry_agent.py | Rule 是关键词/固定分档；LLM 抽取市场潜力、行业成长、竞争位置、政策环境。普通上传 API 不调用它 | 已验证 |
| 评价 | evaluation/scoring.py、industry_scoring.py、composite_scoring.py | YAML 指标/固定权重；范围及结构化证据 ID 存在性校验后确定性计算；缺失不当 0，单域按已有权重重归一；综合任一维度没分则 overall=null | 已验证，36 tests 通过 |
| 报告 | backend/app/report/generator.py、comprehensive.py | 装配结果，检查重复/不存在的 E ID、已评分项缺证据；不重算。未核对摘录原文、页码、结论支持 | 已验证代码/测试 |
| 前端 | frontend/src/App.tsx | 上传+轮询展示 TechnologyReport；内置假报告标“内置虚构样例”，动态标“动态分析报告”；展示片段/页码/检索分 | 已验证 build |

### 关键边界

- Settings 默认 agent_mode=rule。API manager 未注入 Provider；设 KEHENG_AGENT_MODE=llm 会显式失败 provider_not_configured。configs/model.yaml 未被应用加载。
- 真实模型 Provider 在 evaluation/run_v09/v10/v11_experiments.py 单独创建，不是普通上传默认行为。llm 失败时显式失败；fallback 会回退规则，但报告不标实际模式，容易误认为模型结果。
- 空上传拒绝；空检索回 null；有效指标缺少结构化证据会转 unscored；无默认分。加密、扫描 PDF 不支持。综合缺域则总分 null。
- E ID 只校验是否在本次输出列表/模型本轮候选中存在；未检查摘录原文、页码或推论支持。自由文本 rationale 引用也未验证。SMIC 存档中 rationale 有孤儿证据号而该项 evidence=[]。
- task_id 落实在每任务 Chroma 路径、存储 ID 与强制 where 过滤；测试验证隔离。正常模型只收本任务召回块。可是 Agent/ReportEvidence 不带来源 task_id，报告层不核证据所属任务；不是完整血缘强制。
- 前端只显示上传 API 的技术单域报告；没有 rule/LLM/fallback 和模型/prompt 版本。综合服务为 opt-in，不是上传主链路。Finance/Report agents 多为空壳；运行状态仅单进程内存。设计文档中不少生产级描述仍为计划。
- 失败情况：损坏 PDF、无文本、Provider/schema 错误均失败；Provider 请求有有限 retry；LLM 模式不静默回规则。fallback 除外。

## 已有实验

本地找到 runtime/experiments/v11 的 config/raw_runs/metrics/rule_vs_llm/error_cases，以及 v10、real_llm 同类记录和合成 PDF/expected JSON。runtime 被忽略，不随仓库发行。

v1.1 config 记录 Bailian OpenAI-compatible、qwen3.7-plus、temperature=0、JSON mode、Technology prompt v1.1-structured-json-technology、Industry prompt v1.1-structured-json-industry、最多一次格式修复且不回退。raw_runs 有 6 案例各 run 1–5：case_001/002/003 是 Technology-only，其余 case_tech_* 是技术+产业；30 流程、45 个 Agent API 调用；30/30 首轮成功、repair=0。

配置无 Git commit/SHA、逐次代码版本、prompt 内容 hash；当前仓库无 commit，无法把 30 次对应到可核验源码版本。真实案例五家为 v0.9 Technology + v1.0 Industry，CATL 为 v1.1。v1.0 另有 30 次但成功率 73.33%、8 次 schema_failure；v0.9 real_llm 只有 18 次；版本不得混算。

使用当前 v1.1 _metrics 对 raw_runs/固定金标准离线重算，与 metrics.json 一致，无模型调用：

- Technology MAE n=68。120 个位置中 35 个 gold=null（case_002 20、case_003 10、tech_low_industry_high 5）；85 个 gold 非空位置中，17 个 actual=null 被排除（case_003 5、tech_low_industry_high 12）；68 个参与 MAE。故 MAE 只反映 gold/actual 同时非空交集。case_003 成熟度 gold=null，模型仍五次输出 10/15，均不计误差。
- Industry n=60=3 个综合案例×5 次×4 项，60 项 gold 与 output 都有分数。
- “有效证据率 100%”只检查报告摘要、已评分指标、优势/风险、评价 mapping 的结构化 E ID 是否存在于 evidence 列表。复核为 294/294 Technology、120/120 Industry 断言通过；unscored rationale 与自由文本引文不查，原文/页码/结论支持也不查。
- 格式 30/30 首轮成功是真实重复格式结果；repair 未触发。不同维度的分数稳定另算：case_001 技术 range=3.75；case_003=5.00；技术强/产业强 tech 1.00、industry 5.25、overall 2.70；技术强/产业弱 2.50/1.00/1.90；技术弱/产业强仅 1 个技术/综合结果可比、industry 5.00；case_002 无分。v1.1 metrics 的全局 score range 混合案例，不能叫同案例波动。
- 原始数据足以用当前函数复算汇总，但没有读取旧 raw 的离线 CLI；run_v11 默认查模型并发起新调用。
- Runner 在完成模型流程后读取 expected；代码未见 expected 直接进入 Provider prompt。PDF 和金标准为同组开发固定案例，提示词多轮迭代，没有盲测集/调参日志。未见直接泄漏，但无法证明测试集未参与调优，应称开发回归集。

## 开源复现差距

| 检查项 | 结果 |
|---|---|
| README 安装/运行 | Windows 命令结构合理，但没在干净环境执行 pip install。README demo 会覆盖 data/examples JSON 和 frontend/public 报告；本轮未运行以免覆盖旧文件。 |
| 锁定依赖 | 前端有 package-lock 且 build 成功；Python 仅版本范围，无精确锁/哈希/项目元数据。 |
| 无密钥样例 | 默认 rule 模式可离线跑合成 PDF；静态假报告与动态报告有标签，但没有安全写新路径的统一 CLI。 |
| 真实模型配置 | 不统一：model.yaml 未加载，API 无 Provider 注入，实验脚本用 KEHENG_LLM_* 和本地密钥发现。 |
| 小规模复现 | 有 PDF、gold、旧 raw JSON；没有离线 raw 复算命令。v1.1 runner 有真实 API 成本。 |
| 数据区分 | 合成案例有说明；真实 manifests 有来源 URL/机构/日期/hash，原年报被忽略；runtime 输出被忽略。human_review_v10.csv 有 6 行标签/评语，无联系类个人信息列；来源/标注方法/公开许可需人工核验。 |
| 秘密文件 | 根目录有疑似 API key CSV，仅报告位置、不展示内容：默认业务空间-apiKey-6836969.csv；匹配 .gitignore。runtime、.env、sources PDF 有排除规则。因无 Git 跟踪文件，不能声称 index 中安全；首个提交前需核暂存内容。 |
| 许可证 | 有 MIT LICENSE。企业年报有公开来源但不随 MIT 授权；paper 含竞赛模板改造 sty/bst、字体，模板 README 有致谢，未找到逐项再分发许可；人工复核 CSV 标注许可不足。 |

## 基线验证与本轮改动

| 命令/动作 | 环境/结果 |
|---|---|
| backend/.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py" -v | Python 3.12.10；36 tests / 3.271 秒，全部 OK；含 API/隔离/rule+Mock/评分/报告/Provider 契约，无真实模型网络测试。 |
| frontend 下 npm run build | Node 24.11.1、npm 11.6.2；TypeScript 与 Vite 成功。 |
| 独立 TechnologyAssessmentService rule E2E | task_id=audit-sample-20260928，合成 PDF，runtime_root=runtime/review/iteration_01/sample_e2e；报告成功，72.75 分、3 证据、未评分 0。 |
| v1.1 raw 离线复算 | 与旧 metrics 相同；没有模型请求。 |
| 原始 PDF 检查 | 6 份 SHA 匹配 manifest；可提取文本页：寒武纪239/239、中芯156/159、新松218/218、埃斯顿218/218、CATL229/229、蔚来414/414。 |

未运行：全新环境安装、真实付费模型、浏览器手工上传、LaTeX 编译。无离线基线阻塞问题，未改代码。本轮新增三份 review 文档；Chroma 空 collection 的临时创建与清理影响 SQLite 元数据时间戳，向量内容未变，见本文件副作用记录。案例细节见 iteration_01_real_case_diagnosis.md。

## 补充核验

综合评分状态逻辑位于 evaluation/composite_scoring.py：任一技术/产业域分数为 null → insufficient_evidence 且 overall=null；两域均有分、两域覆盖率都为 1.0 → fully_scored；两域有分但任一覆盖率低于 1.0 → partially_scored，仍用 60/40 权重计算总分。pipeline_failed 是流程错误，不是证据覆盖阈值。技术和产业单域分别按其已有指标权重归一化。

运行风险：evaluation/run_real_cases.py 的 _write_human_review 会直接覆盖 data/real_cases/human_review_v10.csv，并把人工复核列重新写为空；不要将真实案例 runner 当成只读报告刷新命令。

## 运行时读取的副作用记录

对旧 Chroma 的只读召回探查最初通过未指定 collection_name 的构造器打开数据库，因其 get_or_create 行为，短暂创建了空的 keheng_evidence_v02 collection。发现后已将该六个数据库中该名称且 count=0 的 collection 删除；原有 keheng_task_evidence 计数复核不变：556、1386、589、701、593、2912。旧数据向量没有被改写；Chroma SQLite 元数据文件因此有本轮访问/清理写入时间。除此之外本轮只新增三份文档和被忽略的 build/runtime 输出。
