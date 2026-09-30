# 科衡 AI 模型设计说明

## Iteration 05 实施补充

技术摘要增加显式 `summary_status`。事实摘要必须声明 `supported` 并绑定当前上下文 E 编号；证据不足摘要使用 `insufficient_evidence`、固定拒答文本和空引用。Agent 拒绝没有证据的事实摘要，也拒绝带事实内容或随意引用的拒答。引用编号存在与原文语义支持仍是不同校验层次，格式通过不等于事实核验通过。历史 Estun/hash 响应未包含该状态字段，需由本轮定向真实复验验证新契约；不回写历史 raw。

模型上下文采用 `balanced_sentences_v1`：最多 8 个 chunk、4,160 字符，按原检索查询组轮转，对精确重复句去重，并只在完整句子边界内适配字符预算。它不访问金标准或目标答案；证据标签仅用于离线事后覆盖诊断。该改动尚未经新的真实模型请求确认。

## 1. 文档目的

本文定义科衡中大语言模型、检索增强生成、专业智能体和多指标评价的设计边界与治理要求。主体内容保持技术无关；v0.3 已按本设计实现 Technology Agent 指标提取与确定性评价的最小边界，但不代表正式模型或生产评价能力已经完成。

## 2. AI 能力定位

AI 用于提高资料阅读、证据整理、跨文档比较、专项分析和报告起草效率。模型输出是可复核的辅助意见，不是企业事实的权威来源，也不直接作出授信、投资或融资决策。

设计目标包括：

- 结论与证据一一关联，能够定位来源和生成条件。
- 对信息缺失、证据冲突和模型不确定性进行显式表达。
- 专项能力职责清楚，便于独立测试、替换和审计。
- 模型、提示词、工具和知识版本可追踪、可回归。
- 失败时安全降级，不输出伪装成确定事实的猜测。

## 3. 总体 AI 流程

```text
授权资料 → 解析与质量评估 → 权限化知识索引
                              ↓
用户/任务问题 → 检索与重排 → 证据包
                              ↓
                 专项 Agent 结构化分析
                              ↓
              冲突检查 / 引用校验 / 规则评价
                              ↓
                  报告整合 → 人工复核
```

每次运行应冻结并记录：输入资料集合、知识库版本、检索配置、模型标识与参数、提示词/工具版本、评价配置和输出模式版本。

## 4. RAG 设计

### 4.1 内容处理

文档处理应尽量保留标题、表格、页码、段落、图注和上下文关系。分块策略根据资料类型配置，避免将财务表格、专利权利要求或关键限定条件割裂。OCR 或表格识别结果应记录质量分数，低质量内容进入人工确认或在检索时降权。

### 4.2 索引与权限

索引单元至少包含任务/企业、资料标识、资料版本、位置、时间、来源类型、密级、解析版本和内容指纹。权限过滤必须在检索执行前生效，不依赖模型自我约束。删除、授权撤销或版本失效后，索引需按制度同步更新。

### 4.3 检索策略

候选方案为关键词与向量混合检索、元数据过滤和重排组合。查询改写不得改变原始分析目的；结果应保持分数和来源。检索参数应通过固定问答集评测，而不是仅凭单个示例调节。若没有满足最低证据要求的结果，应返回证据不足。

### 4.4 引用校验

生成结果中的引用应通过稳定标识指向原文位置。后处理至少检查来源存在、用户有权访问、引用内容支持相关结论、资料未失效。语义“支持程度”的自动判断只作为辅助，高影响结论仍需人工核验。

## 5. Agent 设计

### 5.1 通用契约

每个 Agent 后续应使用结构化输入输出，并至少包含：

### Iteration 03 已实现边界

产品 `rule_demo` 只运行技术规则提取，`real_model` 才运行技术与产业两个 LLM 模块；供应商配置由后端环境注入，缺失时明确失败。模块成功、模块故障、以及 Agent 对企业资料返回“证据不足”是三种不同状态。两模块可部分成功，综合评分只在所需模块均成功时生成。

当前模型输入来自同一检索服务的最终证据包；OpenAI-compatible transport 记录实际 chunk/prompt、原始响应、token/延迟观测。Agent 除检查引用 ID 存在外，还拒绝 rationale/摘要/优势/风险中的自由文本引用未绑定到对应结构化 evidence 列表的输出。此校验仍不能证明引用语义支持结论。

检索 baseline 为 1024 维本地字符哈希向量；BM25 配置是未完成金标准确认的候选方案，使用共享通用中英 query 定义。六企业八问已用于 query 和方法调整，原 holdout 企业不再称为完全未见测试集。真实模型配对实验需人工确认所选候选后再执行；本轮没有发起模型请求。

- 输入：任务目标、适用范围、证据包、已知事实、配置版本和禁止事项。
- 输出：发现项、结论类型、证据引用、分析理由、置信/不确定性、冲突和待核验项。
- 状态：成功、部分成功、证据不足、需要人工、超时或失败。
- 控制：工具白名单、最大步骤、超时、重试、成本预算和数据访问范围。

Agent 不得把自身生成的内容重新标记为外部证据，不得隐式修改评分规则，也不得绕过权限获取材料。

### 5.2 专项职责

| Agent | 核心职责 | 主要限制 |
| --- | --- | --- |
| Technology Agent | 技术路线、创新性、成熟度、知识产权与研发能力 | 不将专利数量简单等同技术价值，不作无法验证的技术断言 |
| Industry Agent | 市场、竞争、产业链、政策和商业化 | 区分市场口径与时间，不虚构份额或预测 |
| Finance Agent | 报表质量、增长、盈利、现金流、偿债和异常 | 不替代审计，原始口径与计算口径必须可追溯 |
| Report Agent | 汇总结构化发现、冲突、评价和限制 | 不新增证据外事实，不掩盖分歧或信息缺口 |

初期宜使用可控的工作流编排，只有在开放式探索确有收益且有足够安全约束时才增加自主规划能力。

v0.4 未实现新的 Report Agent 推理。当前 Report Generator 是确定性转换组件，只复制 Technology Agent 与 Evaluation Engine 已有内容，并校验所有摘要、指标、优势和风险引用的 E 编号能够定位到原始证据。

## 6. 多模态处理原则

首期支持范围需根据样本确定。图片、扫描件、图表和表格的解析结果应保留原始位置与识别置信度；关键数字需进行结构校验或人工复核。音视频若未来纳入，应先确认授权、转写质量和个人信息处理要求。模型无法可靠读取的内容不得被默认为空或正常。

## 7. 评价模型衔接

AI 分析与评分计算分离。Agent 提供带证据的结构化特征或判断；评价模块依据已审批的指标、权重和规则计算。对于需要模型判断的定性指标，应保存判断量表、证据、提示词与模型版本。缺失证据默认不等同低分，应根据正式规则标记未评分、区间或触发复核。

v0.3 中 Technology Agent 输出技术自主性、创新能力、知识产权能力和技术成熟度四项带 E 编号证据的指标观察，不输出最终技术总分。评价模块使用版本化固定权重确定性计算，并保留 `WeightProvider` 边界供后续 AHP 权重实现。当前量表与权重仅用于原型验证，缺失指标标记未评分并按可用权重重新归一化。

v0.6 增加 `backend/app/llm/` Provider 抽象层。Technology Agent 支持 `rule`、`llm` 和 `fallback` 三种模式，默认仍为 `rule`；LLM 只接收检索片段并返回四项指标观察、理由和 E 编号，不得返回最终综合分。正式 `llm` 模式必须显式注入 Provider，未配置或调用失败时返回明确错误，不自动使用 Mock 或切换规则；`MockLLMProvider` 仅由评测脚本和测试显式注入。API 适配器仅接受注入的传输函数，因此未配置 transport 时不会产生外部调用。只有显式选择 `fallback` 模式时，LLM 抽取失败才回到规则实现。

v0.7 新增 Industry Agent，当前采用离线规则抽取以控制范围，不改变 Technology Agent 或 LLM Provider。它只处理检索到的行业、市场、竞争和政策片段，输出四项产业指标及 E 编号来源；行业趋势不得被改写为企业成功，计划/预计/尚未等语义不得作为已完成事实。产业分由独立固定权重引擎计算，技术与产业分再按配置的 60%/40% 组合；Report Generator 只装配已有分析与评价结果，不引入行业常识或外部金融预测。

v0.9 接入阿里云百炼 OpenAI Compatible API。当前实验主模型为 `qwen3.7-plus`，调用端点为 `https://dashscope.aliyuncs.com/compatible-mode/v1`，温度为 0，提示词版本为 `v0.9-strict-json-technology-r1`。模型只负责从已检索片段抽取四项技术指标、理由和 E 编号；0--100 分制、对象数组契约和有效引用由提示词与后处理共同约束，最终分数仍由 Evaluation Engine 计算。`OpenAICompatibleHTTPTransport` 只在脚本显式注入密钥后调用外部 API，密钥不写入配置、实验记录或日志。真实 `llm` 失败必须记录为失败，不自动 Mock 或 rule fallback；Industry Agent 在本轮仍为本地规则实现。固定合成案例的原始请求/响应、指标、证据和错误记录保存在 `runtime/experiments/real_llm/`，真实公开企业案例的运行产物保存在 `runtime/real_cases/`。

v1.0 为 Provider 增加有限指数退避和错误分类，区分首次成功、重试后成功、schema 失败和 evidence 引用失败；请求记录输入证据数、字符数、输出长度和延迟。Industry Agent 增加显式 `rule`/`llm` 模式，复用 Provider 但保持产业指标契约，禁止行业常识补全企业事实。综合结果由确定性引擎增加 `fully_scored`、`partially_scored`、`insufficient_evidence` 和 `pipeline_failed` 状态；当任一一级维度整体不可评分时不计算 Overall Score。LLM 仍不直接决定任何最终评分。

## 8. 输出可解释性规范

每项重要输出应包括：

1. 结论及其适用范围和时间点。
2. 事实、推断、规则结果或人工意见的类型。
3. 可定位的支持证据和必要的反证。
4. 简洁的分析理由，不暴露或依赖不可审计的隐藏思维过程。
5. 不确定性来源、资料缺口和建议核验动作。
6. 模型、检索、提示词和配置版本。

系统不应把语言流畅度当作可信度，也不应输出无法解释来源的“综合置信分”。置信表达方法须通过校准测试后确定。

## 9. 模型选型与适配

选型需使用代表性脱敏样本比较：中文和专业文本能力、长文档处理、结构化输出、引用准确性、多模态能力、部署与数据策略、时延、吞吐、稳定性、成本、可观测性和退出难度。模型调用通过统一适配层，业务对象不直接依赖供应商字段。

在模型最终确定前，不在仓库中安装 SDK 或写死服务地址。模型升级必须通过固定评测集、回归测试和影响评审。

## 10. 评测框架

建议建立以下评测维度：

- 文档解析：文本、表格、位置和关键字段正确率。
- 检索：召回、排序、权限隔离、时效性和无答案识别。
- 专项分析：事实一致性、引用支持度、完整性、冲突识别和专业可用性。
- 报告：结构覆盖、前后一致、引用正确、风险与限制表达。
- 安全：提示词注入、敏感数据泄露、工具越权和跨任务信息混入。
- 运行：时延、成功率、重试、资源与成本。

自动指标与专家盲评结合；评测集按行业、企业阶段、资料质量和风险类型分层。数据集与生产资料隔离，并记录样本来源和版本。

## V2 Retrieval Planner（第三阶段）

新增 `prompts/retrieval_planner_prompt.md` 与 `backend/app/research/planner.py`。Retrieval Planner 是专用结构化规划模块，不复用 Technology Agent Prompt，不评估企业优劣或融资/风险，不撰写结论。首轮输入最低只需企业名称，输出企业身份、业务、技术、产品、人员五类候选 query 与信息缺口；提示词明确禁止把模型记忆中的人名、产品名、专利或技术当成事实。

后续输入包括已执行 query、当轮搜索标题和摘要、已验证发现词、缺口与轮数。模型提出的 `discovered_terms` 必须由程序在该轮 Tavily 搜索结果实际标题/摘要/正文中验证；只有包含原企业名和已验证词的 query 才会执行。相同 query 不重复调用。Planner 输出通过 Pydantic contract 校验，schema/JSON 格式错误允许一次修复，仍无效时明确失败。迭代以固定轮数、每轮 query 数、来源上限、Planner 主动停止和无信息增益阈值约束，trace 保存 planner reason 与停止原因；模型不能发起无限 Agent Loop。

Planner 的 OpenAI-compatible JSON adapter 使用现有 `KEHENG_LLM_ENDPOINT`、`KEHENG_LLM_MODEL`、`KEHENG_LLM_API_KEY` 与超时配置；真实调用只由 CLI 显式创建。Tavily Search/Extract 使用标准库 HTTP 与 `KEHENG_TAVILY_API_KEY`，不新增 SDK。API Key 仅驻留运行内存和出站认证请求，不写入模型输出、trace、任务结果、SQLite 或日志。Planner 收到企业名、queries、网页标题/搜索摘要、发现词和缺口，不发送抓取全文；这些信息会发往配置的模型服务。Tavily 接收 query，必要时接收 Extract URL。接入前应在业务部署环境确认外部服务条款、费用/额度和数据出域政策。

搜索摘要可辅助 query 规划与筛选；当没有获取到正文时，若仍有摘要，可按 `content_scope=search_snippet` 低等级标记进入知识库，不能与完整页面正文等同。正文 hash、来源 URL 和页/段 locator 随 SharedKnowledgeBase 保存。第三阶段完成时，尚无基于这些来源的企业价值判断、行业模板选择、金融推理、Chat 或最终报告；第四阶段语义处理见下文。

## 11. 风险与防护

| 风险 | 防护方向 |
| --- | --- |
| 幻觉和错误引用 | 证据约束、结构化输出、引用校验、拒答和人工复核 |
| 提示词注入 | 不信任文档指令、工具白名单、内容隔离、输出过滤 |
| 偏见与不公平 | 分组评测、指标审查、人工申诉/纠正和使用边界 |
| 模型/数据漂移 | 版本监控、定期回归、样本刷新和变更门禁 |
| 隐私与商业秘密泄露 | 最小化、脱敏、访问控制、合规模型部署和审计 |
| 自动化偏误 | 展示证据与反证、禁止模型直接决策、明确责任人 |

## 12. 待评审事项

- 首期模型部署和数据出域政策。
- 支持的模态、文档类型和上下文规模。
- Agent 的具体量表、工具权限和交互顺序。
- 引用正确性、拒答、置信度和专家可用性的验收阈值。
- 在线监控、人工反馈回流与模型再评测机制。

## v1.1 结构化输出稳定性

结构化调用优先使用 OpenAI Compatible JSON Schema mode（`response_format.type=json_schema`）：共享 `complete_contract` 将 Pydantic contract 的 JSON Schema 与稳定名称传给支持该能力的 adapter，并在本地再次执行 Pydantic 校验。旧 adapter/FakeModel 仍可使用 JSON object mode（`response_format.type=json_object`）并走相同的本地校验边界。首轮结构或契约错误最多触发一次格式修复；诊断只含字段位置、错误类型和脱敏消息，不保存原始模型输出。修复不改变事实、不重新评分、不调用 rule。
# Iteration 04 运行边界补充

- 真实模型配对评测允许显式 `--exploratory` 产生诊断输出，但未确认标签必须保留来源，且不得纳入正式准确率。
- 本地 API transport 自动重试设为 0；每模块最多一次格式修复。鉴权、余额/额度和限流错误会停止同一任务后续模块；连续传输错误停止后续配对。系统调用失败和业务证据不足分别记录。
- 离线检索主指标改为 PDF 哈希、页码与精确支持原文锚点，报告候选 chunk ID 命中和最终送模型上下文原文覆盖。等价改写仍待人工判断，不属于自动语义支持率。

## V2 Adaptive Technology Knowledge Processing（第四阶段）

结构化 LLM HTTP 边界已从 `research/planner.py` 提取到共享 `backend/app/llm/structured.py`，research 与 semantic 共同使用一个 OpenAI-compatible JSON adapter；各领域步骤独立维护 prompt 和 Pydantic contract，不复制 HTTP Provider。

语义加工顺序为三次分离调用：

1. `prompts/domain_template_classifier_prompt.md`：在当前 GENERAL chunk 限定上下文和 registry ID 白名单内执行多标签科技领域分类及可组合模板选择。应用按输入顺序为每项证据分配本次请求有效的 `E1...En`；模型只返回 `domain_evidence_refs` / `evidence_refs`，不接收或返回内部 chunk ID。应用将短引用映射回稳定 chunk ID 并按首次出现顺序去重；domain 引用按有效子集降级，模板引用逐模板校验并丢弃无法支撑的模板，统计写入 `ClassifierReport`。registry ID 仍严格失败。
2. `prompts/technology_fact_extractor_prompt.md`：按所选模板从 GENERAL 来源抽取原子科技事实；每个事实必须指向一个输入 `source_chunk_id`，时间、量值和标签仅在原文支持时填写。Citation 由应用从原始 KnowledgeChunk 复制。
3. `prompts/technology_interpreter_prompt.md`：将已构造的 TechnologyFact compact view 与模板 milestone 对照，输出 supported / limited_support / conflict / no_evidence 及 request-local `F<n>` fact ref。程序建立短引用到 TechnologyFact 的确定性映射，将合法引用转换回稳定 fact ID；unknown ref 按 observation fail-closed，不中断整个语义处理。系统仍严格校验 template/milestone，并为未返回的模板 milestone 填充 no_evidence。

三步各自最多一次结构修复。无效 registry ID 会使阶段失败；classifier 未知临时 evidence ref 按 `ClassifierReport` 记录并执行 fail-closed 降级，不向模型暴露稳定 ID；不存在输入中的 extractor source chunk ID 会使该原子事实作废并写处理警告；Interpreter 未知短 fact ref 由应用按 observation fail-closed，并记录引用质量报告，不再因单条 fact ref 错误中断整次解释。模型输出 Citation 不属于任何阶段契约。

模板规则是项目编写的解释边界，不是外部标准全文。Interpreter 结果不含成熟度或综合分数。应用按模板 fact_type 触发禁止推断约束，向 observation 写入 `blocked_inferences`；若模型 reason 命中模板禁止结论标记，则把状态降为 `limited_support` 并用规则允许的证据范围替换原 reason。例如 `tapeout` 不支持“已量产/良率稳定/客户采购”，benchmark 不支持生产部署，中试线不支持规模量产，II期不支持III期或获批，注册证不支持市场接受。来源质量按确定性类别给出；只有 `snippet_only`/`weak_web` 事实的 supported observation 自动降为 limited_support。

领域 registry 使用《战略性新兴产业分类（2018）》九个顶层领域的代码/名称；模板 registry 目前有 `software_ai`、`semiconductor_design`、`advanced_hardware`、`new_materials`、`biopharma`、`medical_device`。标准 registry 的 GB/T 37264-2018、GB/T 40518-2021、ISO 16290:2013 仅为 reference metadata；原文未入知识库，因此模型不得声称引用标准条款或伪造标准 citation。此阶段不做第三创新点金融推理、授信判断或旧 Evaluation Engine 评分。

显式 CLI 运行时，企业名、当前 GENERAL chunk 正文及 Citation 会发送至环境中配置的模型服务，抽取结果再传入 Interpreter；该模型数据流仅在命令触发时发生，未配置 provider 时零请求退出。运行输出保存在 `runtime/technology_semantic/`，与 SQLite Profile 一样按敏感派生数据管理。
## V2 第五阶段：科技金融联合推理

`financial_fact_extractor_prompt.md` 由 `FinancialFactExtractor` 调用，只抽取来源中明确记载的原子财务/经营事实。Finance mapping 不调用 LLM：`TechnologyFinanceMapper` 根据登记条件确定候选与适用规则，完整绑定候选匹配的科技事实、里程碑引用和财务事实；`tech_finance_mapper_prompt.md` 已标记为弃用/未使用，仅为历史参考保留。

FinancialFact 输出严格校验字段、标准 financial dimension 和当前输入 chunk ID；Citation、来源质量由程序克隆/计算。映射输出只接受已知 rule/scenario/fact/milestone ID，Observation 类型还必须属于 registry 对应 rule 的 output_type。任一 unsupported ID 或未登记场景会失败关闭。规则配置驱动六个科技模板的规则选择，不把模板映射硬编码为 Python 分支。

金融结论使用 supported / limited_support / conflict / insufficient_evidence 定性状态。`snippet_only` 金融证据使 observation 降级；没有披露的财务信息只生成 gap。Contract 采用 `extra=forbid`，不存在自动审批/拒绝、授信额度、信用评级、偿债能力结论或综合风险分字段；确定性 validator 检查 evidence references 与 registry 白名单。此边界由结构化输出类型、来源 ID 校验和规则关系校验共同保障，并非仅扫描自然语言。

科技阶段与企业生命周期分别建模：stage 来自有来源支持的技术 milestone；本版 enterprise_lifecycle 没有可用企业生命周期分类证据时为 `unknown`，不从 prototype、pilot 或临床阶段推断企业初创/成长/成熟。规则 registry 的来源 URL 和政策摘要只是 metadata，不能作为政策原文 citation。FinancialFact/profile 与其 Citation 一并写入 SQLite，会包含敏感企业资料，应按现有数据出域约束保护。

## V2 第六阶段：迭代研究的身份与相关性模型调用

`backend/app/research/orchestrator.py` 在深度检索前调用 `IdentityResolver`，提示词位于 `prompts/entity_resolver_prompt.md`。LLM 输出使用不含 `input_name` 的 `EntityResolutionDraft`；对非空身份检索结果，草稿 schema 强制 `evidence_urls` 至少包含一个 URL，最终 `EntityResolutionResult.input_name` 由调用方设置。输出经严格 Pydantic 契约及应用侧证据检查：身份声明不能脱离本轮检索 URL 和正文；ambiguous 至少包含两个证据绑定候选，unresolved 不允许附带身份声明。确定性身份校验失败返回脱敏的字段级 diagnostics，不记录 claim 原文或网页正文。研究服务维护仅含 query、计数、provider 和当前阶段的运行快照，供失败报告显示部分进度；不改变正常研究结果 contract。官网 host 比较将一个前导 `www.` 归一化，并保留精确 host/合法子域边界。身份未解析时不继续研究、不准入网页。

解析成功后，`EnterpriseRelevanceGate` 使用 `prompts/enterprise_relevance_prompt.md` 对非官网页面批量分类为 relevant / irrelevant / uncertain。模型只返回本批次的 `P1..Pn` 页码 ID，不返回 URL；应用将 ID 映射回原始 `SearchResult`。relevant / irrelevant 必须提供经 NFKC 与空白规范化后可在该页标题、摘要或正文中精确找到的 `evidence_quote`。缺失、重复、未知 ID 分别记录诊断，缺失或重复决策降级为 uncertain，未知 ID 忽略。证据片段不匹配时降级为 uncertain；uncertain 可不带片段。相关性结构化调用异常时，所有待判页面均保留为 uncertain，且不会被准入知识库。身份解析时已核验的 `evidence_urls` 与官网 host/子域名分别走可诊断的确定性 relevant 快速路径。每轮 `ResearchTraceEntry.relevance_diagnostics` 记录模型判定、降级、缺失/重复/未知引用、回退和快速路径计数。此 gate 不等同于网页事实核验或联网搜索质量评测。

## V2 第七阶段：科技语义 evidence intake 与上下文预算

`TechnologyKnowledgeProcessor` 使用两阶段 semantic evidence selection。第一阶段 `select_semantic_evidence` 继续按来源轮转、SourceQuality、全文优先、页/段定位与稳定 chunk ID，为 classifier 提供最多 18 个、每来源最多 2 个、每 chunk 3,200 字符、总计 48,000 字符的多源上下文。分类模板确定后，第二阶段 `select_template_fact_evidence` 从全部当前 GENERAL chunks 重新选择，而不是从 classifier 小集合筛选：以模板 registry 的 `selection_terms`、`important_objects`、里程碑标签和关联 TechnologyFactType 描述计算确定性相关度，再以质量、全文范围、来源多样性和稳定定位排序；最多 30 个、每来源 3 个、每 chunk 3,200 字符、总计 80,000 字符。5-gram Jaccard 近重复过滤在相关性排序后执行；正向匹配不足 6 个时按 classifier evidence 原顺序补足并记录 fallback 数量。模板 selection terms 是 retrieval/reranking 元数据，不是事实判断规则；不直接以英文 fact-type ID 匹配正文。LLM payload 的 Citation 保留来源定位但省略重复 excerpt，原 chunk/Citation 不改写，派生事实仍指向第二阶段选中的原始 chunk。`semantic_evidence_selection` 保留为 classifier selection 的兼容字段，profile 另存 `classifier_evidence_selection`、`fact_evidence_selection` 与两阶段处理数；runner Markdown 分开展示两阶段统计。Processor 版本为 `technology-semantic.v8`，模板 registry 为 `technology-templates.v2`。SourceQuality 按 `source-hosts.v2` 分类；交易所/法定披露 `exchange_disclosure` 映射为 `authoritative_public_record`。

身份解析契约将 resolved 必填 canonical name、ambiguous 至少两个候选、unresolved 禁止身份主张/候选作为 Pydantic 状态约束。模型返回的官网声明不能单独写入 Company；确定性 self-attestation 对每个 URL/hostname 检查固定 240 字符局部窗口，要求企业规范名称或已验证别名、强自指表达（如“本站”“本网站”“本公司运营的官方网站”）和指向当前页面 host 的 URL 同窗共现。普通“官网/公司网址/official website”只是弱 claim marker，跨 host URL 只进入 `official_website_external_candidate_hosts`，不视为强候选；无 scheme 的 `www.example.com` 也仅能产生候选 hint。多个不同 host 的强自证仍留空并记录 `official_website_candidate_conflict`。若 identity 阶段未确认官网，后续 research 在 relevance gate 与来源入库前复用同一严格发现逻辑；唯一强候选才可更新 Company，并记录 `official_website_resolution_source=self_attested_page`、证据 URL 和 `official_website_discovered_during=research`。强候选与未验证候选 host 以稳定去重列表记录在执行 trace/review，不保存正文，也不写入可信 Company 官网字段。`SourceTypeClassifier` 仅依据已验证 `Company.official_website` 的 host/子域匹配 `company_official`；候选 hint 不参与来源类型或 first-party 判定。

## V2 第八阶段：科技事实抽取 bounded batches

`TechnologyFactExtractor` 按 selector 返回顺序分段，每批最多 3 个 GENERAL chunks，并通过 `asyncio.Semaphore(2)` 限制并发。每批只携带当前 1–3 个 chunk 与共享 domain/template 上下文；其结构化响应单批上限为 36 facts、12 information gaps。按 batch index 和模型原始 fact 顺序合并，再依次验证 batch-local chunk 引用、全局 selected chunk 引用、domain/template tags 和稳定 ID。拒绝跨 batch 引用使用 `invalid_batch_chunk_reference` warning 与拒绝数审计；LLM 不能自行创建引用或 Citation。Prompt 指示每个 chunk 优先保留约 10 个有独立信息量的事实。

TechnologyFact 类型由 `configs/knowledge/technology_fact_types.yaml` 版本化登记。Extractor payload 附带类型 ID、category 和 description；契约字段仍为 `str`，应用只做 `strip` 与 `casefold` 后的精确白名单检查，不作类型猜测或自动映射。未登记类型直接拒绝、不生成 stable fact ID，并在 `FactExtractionReport` / trace 记录拒绝总数、类型分布及 `unregistered_fact_type` 类别；Profile 仅增加不含正文的 `technology_fact_type_rejected:<count>` warning。启动加载模板 registry 时，会校验模板 `evidence_types`、milestone `fact_type_hints` 与 inference rule `fact_type_any` 都已登记。

职责边界：TechnologyFact 只表示技术路线、技术成果及研发、验证、产品化、产业化里程碑；FinancialFact 表示财务、经营、商业、融资、客户订单金额和研发投入等经营维度事实。客户验证、试用、部署、验收和技术应用可以作为 TechnologyFact；订单/合同金额和收入由 Finance 处理。普通公司登记、股权、公司历史和非技术里程碑奖项不进入 TechnologyFact。同一 GENERAL 原始来源可被两类 extractor 分别使用，二者保存各自的事实类型、ID 与 Citation。

Technology→Finance 的 registry rule 若配置非空 `milestone_any`，只由 `supported` 技术里程碑触发；`limited_support`、`conflict` 和 `no_evidence` 不构成已发生的阶段。一般场景规则不依赖技术里程碑，仍可按其 FinancialFact 条件适用。Mapper 将候选里的 milestone ref/status 传给模型，但最终 milestone status 由程序从 TechnologySemanticProfile 确定；条件规则的 EvidenceBundle 必须保留实际触发 ref 及该 milestone 的 supporting/contradicting fact IDs。Finance 输出状态不得强于其技术触发里程碑；仅当所选 FinancialFact 全部为 `weak_web` / `snippet_only` 时按来源强度降为 `limited_support`，混有 authoritative/first-party 时不因弱来源单独降级。`MonitoringNode.status` 表示监测节点触发依据的证据强度，不表示所需监测目标已经实现。

每批超时、schema/invalid response、网络或服务错误时均不接纳该批事实。其它批次继续执行；部分成功输出带失败数量 gap，全部失败抛出首个按 batch 顺序遇到的 provider category。Profile 的 `classifier_report`、`fact_extraction_report` 和 runner trace 记录 classifier / extractor 统计与错误类别，不包含 prompt、raw response 或 secret。两阶段证据选择将当前语义处理器版本更新至 `technology-semantic.v8`；模板 selection metadata 将模板 registry 更新为 `technology-templates.v2`。TechnologyFact 的 derived chunk identity 绑定稳定 `fact_id`，避免不同语义事实因相同 SPO/locator 发生向量 ID 冲突；processor trace 分别记录 milestone 解释、派生 chunk 持久化和 semantic profile 持久化阶段。

Interpreter 每次请求按输入顺序生成 `F1...Fn`，模型只接收 subject/predicate/object、fact type、时间和量值、domain/template tags 与 source quality 类别；不接收内部 `fact_id`、company/source chunk ID、Citation、URL、locator、质量理由或 processor version。模型边界使用 `supporting_fact_refs` / `contradicting_fact_refs`，应用将合法短引用映射回稳定 fact ID，Profile 继续保存原有 ID。重复引用按首次出现顺序去重；unknown refs 被过滤并按单条 observation 降级；support/conflict 必须由可验证引用支撑，重叠引用不能同时进入两侧。`InterpreterReport` 在 Profile 中记录输入事实、模型/输出 observations、invalid/partial/full invalid refs、duplicate/overlap 和状态降级计数。template、milestone 与重复 observation 校验仍严格失败；现有弱来源和禁止推断 deterministic guards 继续执行。

## 结构化模型 thinking 配置

`Settings.llm_enable_thinking` 对应可选环境变量 `KEHENG_LLM_ENABLE_THINKING`。未设置（`None`）时，OpenAI-compatible structured request 不包含 `enable_thinking` 字段；显式配置布尔值时，将 `true`/`false` 原样放在请求体顶层。模型适配器不改变 timeout 或其他请求参数。所有脚本中的真实 structured model 构造都传递该配置；脱离 provider 的测试替身不受影响。

`FinancialFactExtractor` 将现有最多 60 个输入 chunks 按顺序切成每批最多 4 个，并用 `asyncio.Semaphore(2)` 限制并发。每批只发送 batch-local `C1...C4`、chunk 正文截断视图和 source-quality 类别/类型/scope；模型不得接收或返回真实 KnowledgeChunk ID 或 Citation。应用仅接受当前批次内有效 `source_ref`，确定性恢复原始 chunk、Citation 和 quality，并用 company、真实 chunk ID、规范事实字段、processor/registry 版本计算稳定 fact ID。无效引用及 registry 外 financial dimension 被拒绝并记录计数；不同批次按输入顺序合并、按稳定 ID 去重。失败批不接纳事实；部分失败保留成功批输出并添加明确 gap，全部失败才抛出结构化错误。`FinancialFactExtractionReport`、`FinancialFactExtractor.last_execution_trace` 和 Finance processor 阶段 trace 记录批次/输入 chunk/事实、dimension/source-quality 分布、拒绝计数和失败类别，不保存 prompt、raw response 或 secret。Finance processor version 为 `technology-finance.v4`。

Finance mapper 的规则适用性与证据绑定为确定性 registry-first 流程：含 `milestone_any` 的规则仅在指定模板/里程碑存在 `supported` observation 时成为候选；limited/conflict/no_evidence 不触发阶段规则。无里程碑条件的通用规则独立按 `financial_dimension_any` 匹配。候选的 scenario、输出类型和全部匹配 evidence IDs 均直接来自 registry 与当前结构化事实；mapper 不依赖模型、prompt 或 structured model adapter。后续 `_assemble()` 保留 milestone strength、弱证据降级、安全原因、Citation/事实溯源与验证逻辑。mapper/processor trace 和 review 中记录 candidate/applied rules、候选类别计数及 `mapping_mode=deterministic_registry`。

## Evidence Assertion（非模型处理）

`EvidenceAssertionProcessor` 不调用 LLM，亦无 assertion prompt。其输入仅为已持久化的 TechnologyFact 与 FinancialFact；近重复文本使用 predicate+object 的字符 3-gram Jaccard，阈值 0.88。科技数值仅在相同 fact type、subject、显式 event time 和单位槽内作结构化比较；金融数值以 dimension、subject、period/event time 为槽，CNY 常见单位可换算，未知单位或币种不跨单位归并或判冲突。自由文本不做语义矛盾判断。Representative 只表示稳定选择的显示代表，不表示冲突事实中哪一个为真。该层不改变 milestone 状态、Finance status 或旧评分。

## Evidence-First Report（非模型处理）

`EvidenceFirstReportAssembler` 不调用 LLM，也不新增 prompt 或推断；它校验 profile 链、确定性排序和选择代表事实、复制 Citation/Source provenance，并展开 Finance evidence bundle。Profile 引用缺失、source 无法解析或 profile 版本链不一致时返回错误，不降级成无来源事实。报告中的 representative 仅为展示排序，不代表事实优先级裁决；multi-source/conflict 状态直接沿用 Assertion profile。该层没有评分或自动授信结论。

### Assertion v2 的可解释归并规则

该层仍不调用 LLM。主体只接受 repository Company 中的 canonical name/aliases 精确匹配，以及“公司/本公司/该公司/企业/本企业/该企业”等有限代词；产品名不按包含关系归成企业。期间显式年份、半年和季度采用封闭格式表，其他表达保留原样且不据此推断年份。文本比较会先检查双方显式数字和英数型号 anchor；任何 guard 不同均拒绝 fuzzy merge。Technology object 使用 0.72 3-gram 阈值、满足长度条件的 containment 和 predicate 辅助门槛；Finance 非量化事实默认 0.78，industry/core_business 要求 exact、至少 0.75 containment ratio 或至少 0.85 Jaccard。无明确 period/event 的融资文本只允许近乎一致表述归并；无明确时间的不同财务值不判 conflict。

候选事实按 source quality、full_content、source ID、fact ID 排序；每个候选仅与 cluster 的首个代表 anchor 比较，禁止 transitive chaining。cluster grouping method 使用其中较弱的匹配方式。fuzzy cluster 超过 8 个事实只产生 `suspicious_fuzzy_cluster` warning，不改变来源质量、milestone 或 finance status。
