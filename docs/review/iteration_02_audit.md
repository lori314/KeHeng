# Iteration 02：评测修正与离线检索改进

日期：2026-09-28  
范围：保留原实验与输入，只改运行器安全、复算/对照脚本、测试和本审计文档。不调用云模型，不修改论文、不发布仓库。

## 数据保护与可复算基线

- 运行输出目录通过 `evaluation/run_safety.py` 建立唯一 `run-*` 子目录；显式指定的已存在目录一律报错。v0.9/v1.0/v1.1 runner 在检查输出路径后才发现密钥或访问模型。真实案例 CSV 只写入新 run 下的 `human_review_suggestions.csv`，不再写 `data/real_cases/human_review_v10.csv`。真实案例子目录不允许复用。
- `scripts/run_technology_demo.py` 保留原无参数命令，但分析、评价、报告及工作库均写到新的 `runtime/demo_runs/run-*`；不写 `data/examples` 或 `frontend/public` 的旧样例。原命令兼容为“照常运行、输出路径改为打印出的 run 目录”。`--output-dir` 可指定新路径；存在时失败。
- 离线复算命令：`backend/.venv/Scripts/python.exe evaluation/recompute_history.py`。它只读 `runtime/experiments/v11/raw_runs.json`、旧 `metrics.json` 和当前固定 gold，生成独立新目录，不导入模型客户端、不查询模型列表、不请求 API。
- 最近复算目录：`runtime/review/recompute/run-20260928T045859Z-6cc9d628/`。旧统计另存为 `legacy_metrics_v1_1_copy.json`；v2 指标、case×indicator×run 明细、引用明细和 manifest 分别写入新文件。raw 与旧 metrics 的 SHA-256 分别为 `f72ae8620f12e09edebfbd3f19fc2f04051e3d1776e11c1c96385bc71979b003`、`edb06447e22448d0bbbeef64433d10d5c64991f970260bc24e81f4ca76b13ba9`。
- 仓库当前无 Git commit。历史 commit 与历史 prompt 哈希明确记为 `null`；manifest 另存当前显式源码、配置、gold/PDF、prompt 文件的路径和 SHA-256，不将当前指纹冒充历史快照。后续 v0.9/v1.0/v1.1 与真实案例新运行会记录代码、prompt、gold/PDF/manifest 快照指纹、模型请求配置与数据 snapshot ID，不含密钥。

### 历史指标重算

先读 gold 文件和案例 README：case_002 明确四项均保持未评分；case_003 的预期指标把两项缺乏证据的项目设为 `null` / `evidence_required=false`，另两项有分；复合低技术案例明确专利信息缺失应未评分。复算只把同时满足预期 `score=null`、`evidence_required=false` 且 README 明确材料缺失/未评分的项目视为“明确证据不足”。未来没有这种说明的 null 会记为 `unlabelled_null`，不会当作必须拒答或改成 0。

| 域 | gold 有分槽位 | 模型返回有分 | 覆盖率 | 双方有分条件 MAE | gold 有分但模型为空 | 明确证据不足却给分 |
|---|---:|---:|---:|---:|---:|---:|
| 技术 | 85 | 68 | 80.00% | 7.6471（n=68） | 17/85（20.00%） | 1/35（2.86%） |
| 产业 | 60 | 60 | 100.00% | 6.3333（n=60） | 0/60 | 0/0（无此域明确拒答 gold） |

因此论文所报 MAE 数值是“双方有分条件 MAE”，不是全部预期评分槽位上的误差；技术 MAE 排除了 17 个 gold 有分而模型返回空值的槽位。历史 raw 里未发现输出字段缺失、JSON/流水线失败槽位；这不代表缺失可忽略，v2 明细会保留这些分类。历史 30 次运行共用 6 个案例×5 次运行，复算按 run 保存细项，企业/案例数仍为 6，不把重复 run 算独立企业样本。

### 引用的三层计数

复算扫描结构化指标引用、评价映射和报告 references；自由文本扫描 Agent 摘要、指标理由、strengths/risks 与报告文本；页码定位将保存 excerpt 经空白/NFKC 归一后与对应案例原 PDF 页文本比对。

- 结构化引用 ID 可在同一任务证据集中解析：355/355。
- 自由文本 ID 属于同任务证据并与相应 `supports` 路径或报告 references 绑定：1702/1702。
- 原文与页码可定位：55/55。
- 未做人工蕴含审查；语义支持率为 `null`。上述三项不能作为语义正确率或证据支持率。

精确明细见 `citation_audit_by_run.json` 与 `per_case_indicator_run.csv`。这些检查只覆盖历史存下来的 artifact，无法还原当次送模型的完整检索上下文。

## 六家公司离线检索对照

命令：`backend/.venv/Scripts/python.exe evaluation/run_retrieval_comparison.py`。本轮最终输出在 `runtime/review/retrieval/run-20260928T050135Z-1845fc0e/`。运行器实际比较六份 PDF SHA 与 manifests，均匹配。

只读使用六份现有年报 PDF；SHA-256 与 manifests 一致。四法共享当前解析器和 520 字符、80 字符重叠、页内切分；每个公司各自建 run 私有 Chroma collection，task_id 和 document_id 均精确过滤。A/B/D 的向量法沿用实际 `LocalHashingEmbeddingProvider`：中文/英文字符 2–4 gram、1024 维、余弦距离；无语义模型。C/D 的本地 BM25 对文本做 NFKC 与大小写折叠，拉丁文本按词，连续 CJK 字符按重叠二元组切分；`k1=1.5, b=0.75`。无外部依赖、无联网、无模型调用。

每个评测问题固定 4 个 query、每 query 各方法至多取 10 个候选（40 个候选槽位），按 chunk ID 去重。A 使用当前 Agent 原始 4 条 domain query；B 使用统一指标定义产生的 4 条中英 query；C 与 B 查询相同；D 对同一 query 的向量与 BM25 排名用 RRF `k=60` 求和，再跨 query 合并。RRF 参数未用保留集调整。结果同时给 chunk recall 与页级候选命中；同页命中只表示目标页有任意 chunk 入选，不表示目标原文段被取到。最终模型上下文另在每法相同 top-8 chunk / 4160 字符预算下计算候选 chunk 覆盖完整度。表中均为按“问题”宏平均，不把重复运行或多个企业记录当成独立企业样本。

标注单位是“公司+指标/问题”。已知 SMIC/NIO 中英错位及 Estun 专利 p15 放开发集（SMIC 2、NIO 1、Estun 1）；寒武纪、新松、CATL 按公司整体留出（4 个问题，其中 CATL 政策需两个片段）。共 8 个问题、9 个目标 chunk、6 家企业；3 家开发、3 家保留。当前确认标签为 0，全部是 `candidate_unconfirmed`，因此以下只称“暂定候选召回”。没有已确认不可回答问题，故不可回答项 n=0，且未混入 Recall 分母。逐项是否多片段、候选句、页码和待确认原因见 `pending_evidence_review.csv`。

| 分组 | 方法 | 暂定 chunk R@3 / R@5 / R@10 | MRR | top-8/4160 字符候选覆盖 | top-10 文本字符均值 | query 检索耗时均值 ms |
|---|---|---:|---:|---:|---:|---:|
| 开发（4 问题） | A 原向量+原 query | 0 / 0 / 0.250 | 0.0278 | 0.000 | 4918 | 345.7 |
| 开发 | B 原向量+双语 query | 0 / 0.250 / 0.250 | 0.0625 | 0.250 | 4699 | 224.2 |
| 开发 | C BM25+双语 query | 0.250 / 0.250 / 0.250 | 0.2894 | 0.250 | 4830 | 30.1 |
| 开发 | D RRF+双语 query | 0 / 0 / 0.500 | 0.0852 | 0.500 | 4793 | 254.4 |
| 保留（4 问题） | A 原向量+原 query | 0 / 0 / 0 | 0 | 0 | 4853 | 374.8 |
| 保留 | B 原向量+双语 query | 0 / 0.125 / 0.375 | 0.0938 | 0.375 | 4779 | 91.3 |
| 保留 | C BM25+双语 query | 0.375 / 0.375 / 0.875 | 0.4417 | 0.625 | 4264 | 10.5 |
| 保留 | D RRF+双语 query | 0.125 / 0.375 / 0.500 | 0.2018 | 0.500 | 4712 | 102.0 |

全部均值也按语言、指标和公司写在 `retrieval_summary.json`；逐问题每法 R@3/@5/@10、首个命中排名、页级命中、上下文覆盖、字符数、耗时、top-10 片段及元数据在 `retrieval_comparison.json` / `retrieval_per_case.csv`。

### 六家案例的检索层观察

| 公司 / 指标候选 | 暂定观察（top-8 证据上下文） | 限制 |
|---|---|---|
| SMIC / competitive position（英文 p21） | A–D 均未找到目标 chunk，仍失败。 | query 需要改进，但候选标签待人工确认；不得说原文缺失。 |
| SMIC / IP（英文 p21） | A–D 均未把目标 chunk 放入 top-10 或上下文。 | 本评测只命中一项知识产权表格候选，未覆盖该年报其他 IP 页。 |
| NIO / IP（英文 p67） | B 与 D 的目标 chunk 进入 top-8；A/C 未进入。 | 这是检索层候选改善，不是 Agent 已抽取或评分。 |
| Estun / IP（中文 p15） | C 命中 top-3 且进入上下文；D 命中 top-10 且进上下文；A 虽 top-10 命中但未进 top-8；B 未命中。 | 支持明确数值的候选段，待人工确认切分片段完整性。 |
| Cambricon / market potential（中文 p16） | B/C/D 进入上下文，A 未命中；C top-3 命中。 | Gartner 市场预测须与公司披露区分，待人工确认支持边界。 |
| Siasun / competitive position（中文 p23） | 仅 C 在 top-10 找到候选，未进入 top-8；其余未命中。 | “市场份额不断提升”没有数值，是否足够支持竞争力需人工决定。 |
| CATL / policy environment（中文 p12+p13） | 仅 D 在上下文完整覆盖两个候选片段；B/C 各覆盖一半；A 未命中。 | 两页合并要求仍待确认，不能把同页召回当成两个政策均已命中。 |
| CATL / technical autonomy（中文 p15） | C 进入上下文；A/B/D 未进入上下文。 | “自主研发为主、外部合作为辅”措辞的评分相关性待人工确认。 |

真实案例的模型分析结果没有重跑，本表不宣称某指标最终评价已修复。特别是 SMIC 仍未召回、Siasun 候选只在上下文预算外、CATL 的技术指标只有 BM25 候选改善。六家已有模型输出中的 null 仍应保持原状。

## 代码改动与验证

- 新增安全输出目录、当前源码/输入 provenance：`evaluation/run_safety.py`、`evaluation/provenance.py`；更新 v0.9/v1.0/v1.1、真实案例与演示 runner。旧默认命令兼容，但输出切到 run 目录。没有重置人工标签或修改 gold。
- 新增离线复算：`evaluation/recompute_history.py`；新统计不替换 v1.1 metrics。
- 新增指标定义、候选标注与对照：`evaluation/retrieval_indicator_definitions.json`、`evaluation/retrieval_eval_cases.json`、`evaluation/run_retrieval_comparison.py`。
- 新增 5 个 `unittest`：`tests/test_iteration02_offline.py`，均通过。覆盖既有目录防覆盖、真实案例人工复核 CSV 不被重置、缺失及证据不足却给分统计、task_id 隔离、孤儿引用、中英文 BM25 关键词回归。
- `backend/.venv/Scripts/python.exe -m compileall -q`（本轮 Python 改动文件）：通过。
- `npm run build`（frontend）：通过，Vite 7.3.6 / TypeScript 编译成功。
- `backend/.venv/Scripts/python.exe scripts/run_technology_demo.py`：通过；rule 技术流程写入 `runtime/demo_runs/run-20260928T045521Z-7594cc1a/`，输出技术分 72.75、3 条证据。该 demo 不验证真实模型。
- 仓库现有 pytest 风格测试尝试运行：`backend/.venv/Scripts/python.exe -m pytest tests -q`，失败原因是该虚拟环境未安装 pytest（`No module named pytest`）；没有联网安装新测试依赖。本轮新增 unittest 不依赖 pytest，已实际运行。
- 未调用付费/真实模型，不做批量真实模型复验；未发布、未改论文。旧 raw、旧 metrics、PDF、gold 与人工复核 CSV 均未由本轮运行写入。

## 已知限制

1. 人工候选标签尚未确认；小样本只能用于开发与保留企业上的暂定检索诊断，不能校准通用阈值或声称提升。
2. 只标了 8 个问题，单企业 1–2 个指标，且无确认的不可回答问题。多片段只有 CATL 政策一项。
3. BM25 当前是离线对照实现，尚未接入产品检索配置；本轮结果显示 RRF 整体未胜过 BM25，尤其页级与 chunk 级差异明显。
4. 引用核验检查 ID、绑定元数据和页内原文位置，不判断结论是否被证据语义支持。
5. pytest 未安装，因此原有 36 项 pytest 套件未能运行；只运行了新增 unittest 和前端构建。
