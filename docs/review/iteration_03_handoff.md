# 科衡 Iteration 03 交接

## 产品与入口

- 网页默认是无密钥**规则演示**：实际只跑技术模块，不编造产业结论。选**真实模型**后由后端配置运行技术、产业两个模块并计算综合结果；缺少 `KEHENG_LLM_ENDPOINT`、`KEHENG_LLM_MODEL` 或 `KEHENG_LLM_API_KEY` 会明确失败，不会回退。
- 网页 API 与配对 runner 共用 `TechnologyAssessmentService.run_product`。模块成功/失败、证据不足分开保存；任务保留请求/执行模式、PDF 哈希、检索/评分/prompt/源码指纹。真实请求还记录发送的 chunk/prompt、原始响应、token/延迟及失败观测。密钥不进入前端或报告。
- 产品可配置 `KEHENG_RETRIEVAL_MODE=hash|bm25`。Hash 是现有 baseline；BM25 是实验候选，使用共享 BM25 接口、`task_id` 过滤与通用双语 query，不宣称已验证提升。普通界面不暴露 RRF 等实验选项。
- 报告展示技术/产业指标、各自覆盖分母、原文/文档/页码、无效引用提示和按实际缺项生成的材料建议。部分模块失败会保留另一模块。模式、检索与版本信息收在运行详情中。

## 验证

- `backend/.venv/Scripts/python.exe -m unittest discover -s tests -v`：**48 项通过**。含规则模式、双模块成功、单/全部失败、真实配置缺失、BM25 task 隔离、Agent 孤儿引用拒绝、SMIC 历史孤儿回归。
- `backend/.venv/Scripts/python.exe -m compileall -q backend/app evaluation/run_retrieval_comparison.py evaluation/run_paired_model_experiment.py evaluation/create_iteration03_evidence_review.py`：通过。
- `frontend/npm run build`：TypeScript 与 Vite 构建通过。
- API 根路径和前端开发服务器均实际启动成功；截图为运行中的上传页，默认选择**规则演示**，不是模型结果或测试替身结果：`docs/review/iteration_03_ui_rule_demo.png`。
- 真实模型 API 请求：**0 次**。配对 runner 的 `--prepare-only` 实际生成了计划：`runtime/review/paired_model/run-20260928T054422Z-eab055da/paired_model_plan.json`；所需 NIO 英文、Estun 中文两条标签仍未人工确认。

## 离线检索结果

最终运行：`runtime/review/retrieval/run-20260928T053923Z-46c195da/`。六家现有 PDF 与 manifest 哈希匹配；0 模型调用。八题共 9 个候选支持 chunk，确认标签 **0**，所以所有 Recall/MRR/上下文覆盖均为**暂定候选统计**。每格为 chunk R@3/R@5/R@10、MRR、相同 top-8/4160 字符上下文候选覆盖：

| 分组 | A 原 hash+原查询 | B hash+双语查询 | C BM25+双语查询 | D RRF+双语查询 |
|---|---|---|---|---|
| 开发 4 问 | 0/0/.25；.025；0 | 0/.25/.25；.0625；.25 | .25/.25/.25；.2955；.25 | 0/0/.50；.0852；.50 |
| 留出 4 问 | 0/0/0；0；0 | 0/.125/.375；.0938；.375 | .375/.375/.50；.2442；.375 | .125/.375/.50；.2018；.50 |

平均 top-10 文本字符/检索毫秒：开发 A 4851/335、B 4699/199、C 4646/1280、D 4793/1479；留出 A 4853/374、B 4735/137、C 4387/1173、D 4712/1310。BM25 查询耗时较高，尤其当前 Python 原型。页级命中单独统计；页命中不等于目标句或 chunk 命中。候选、逐问题、分语言/指标/公司和页级明细在 `retrieval_comparison.json`。

候选改善/失败：NIO 英文 IP 为 B/D 进入上下文、C 未命中；Estun 中文 IP 为 C top-3 且进入上下文，A 仅 top-10、未进入上下文；寒武纪市场候选 C top-3 并进入上下文；CATL 两条政策候选 D 在预算内完整覆盖，B/C 仅一半；SMIC 两题、新松竞争力、CATL 技术自主性四法均未找到目标 chunk。均待人工确认，检索未召回不等于原文不存在。

本轮查看了留出组结果，但未用其调整 query 或 RRF 参数；本 handoff 已据此讨论配置，后续不要再称这些公司为完全未见测试集。建议继续保留 hash 为默认，把 BM25 当候选，确认标签后运行配对实验；暂不保留 RRF 为产品方案。

## 引用统计差异

- Iteration 01 报告的 `294 Technology + 120 Industry = 414` 是当时检查的引用断言数，扫描域包括摘要、已评分指标、优势/风险和评价映射；没有保留下逐项导出与去重脚本，**无法把多出的 59 项精确映射到单个字段**。
- Iteration 02 的 `355/355` 是单独扫描 `runtime/experiments/v11/raw_runs.json` 中 30 次合成运行的结构化引用出现次数：指标 evidence 186 + 评价映射 92 + `report.references` 77；按出现次数计，不跨重复运行去重。`1702/1702` 是同一 30 次运行自由文本 ID 出现次数，逐条检查任务内存在及 supports 绑定；`55/55` 是该 raw 范围内保存 excerpt 的原文/页码定位，不是语义支持率。
- 这三项历史聚合没有包含单独保存的六家真实企业结果。SMIC 真实文件 `runtime/real_cases/smic/technology_analysis.json` 中，技术分析自由文本有 15 次 ID 提及，15 次 E ID 都存在于该任务证据；只有 14 次与对应 `supports` 绑定。innovation rationale 提及 E1，但该指标 `evidence=[]`，E1 的 `supports` 只有 `technology_summary`。这是统计数据范围差异，不是把历史 raw 修到通过；本轮加入实样回归，并令在线 Agent 拒绝同类未绑定 rationale 引用。

## 配对实验与需人工补充

默认准备 NIO（英文）+ Estun（中文），对比旧 hash 与 BM25，两个配置使用**相同通用双语 query**，其余模型、提示词、解析、评分和上下文预算一致。共 2 家 × 2 检索配置 × 技术/产业 = **8 次主调用，最多 16 次 HTTP 请求**（每次格式修复最多 1 次）；保存实际上下文、raw、用量、延迟、失败。主要观察证据支持、指标覆盖和不受支持却给分，不以总分升高判断效果。未执行付费调用。

人工先复核 `docs/review/iteration_03_evidence_review.md`，将 `nio_intellectual_property_en` 和 `estun_intellectual_property_zh` 写入独立 `data/real_cases/iteration03_evidence_confirmations.json`，明确人工 reviewer 与 `human_confirmed` 状态。复核包中的其他六题也保持待确认；候选自动初审不是人工标签。

## 命令与关键路径

```powershell
# 后端：单独终端，从仓库根目录执行
.\backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
# 前端：另一终端
npm --prefix frontend run dev
# 离线对照/配对计划：仓库根目录执行
.\backend\.venv\Scripts\python.exe -m evaluation.run_retrieval_comparison
.\backend\.venv\Scripts\python.exe -m evaluation.run_paired_model_experiment --prepare-only
# 人工确认并配置模型后才运行，添加 --execute
.\backend\.venv\Scripts\python.exe -m evaluation.run_paired_model_experiment --companies nio,estun --confirmation-manifest data/real_cases/iteration03_evidence_confirmations.json --execute
```

- 产品/检索/配对代码：`backend/app/services/analysis_service.py`、`backend/app/rag/bm25.py`、`backend/app/rag/query_definitions.py`、`evaluation/run_paired_model_experiment.py`
- 8 题复核包：`docs/review/iteration_03_evidence_review.md`
- 实际运行界面截图：`docs/review/iteration_03_ui_rule_demo.png`
- 本交接：`docs/review/iteration_03_handoff.md`
