# 科衡 Iteration 02 交接

**系统现状：**默认产品链路仍是本地 rule 单域技术分析；本轮只为离线复算、检索对照和新运行数据安全加工具，未接入产品 BM25，也未重跑真实模型。

**修复与证据：**实验/演示现在写唯一新 run 目录；指定已存在目录立即报错。真实案例人工复核 CSV 不再被 runner 重置，演示不再写 `data/examples` 或 `frontend/public`。v1.1 历史 raw 已离线复算：技术覆盖 68/85=80%，条件 MAE 7.6471（n=68），gold 有分但模型空 17/85；明确证据不足 gold 中仍给分 1/35。产业覆盖 60/60，条件 MAE 6.3333。引用 ID 可解析 355/355、自由文本 ID 任务内且绑定 1702/1702、原文页码可定位 55/55；都不是语义支持率。

**检索结果：**8 个问题/9 个候选 chunk 全待人工确认。开发/保留公司分组的暂定 chunk R@3/5/10：A 原向量原 query 为 `0/0/0.25`、`0/0/0`；B 向量+双语 query 为 `0/0.25/0.25`、`0/0.125/0.375`；C BM25 为 `0.25/0.25/0.25`、`0.375/0.375/0.875`；D RRF 为 `0/0/0.5`、`0.125/0.375/0.5`。保留集上下文候选覆盖 C=0.625、D=0.5；页级 R@10 的 D=1.0 但 chunk R@10=0.5。不能把暂定结果称为已验证提升。SMIC 两个候选仍失败；Siasun 只在 top-10、未进 top-8；NIO IP、Estun IP、寒武纪市场、CATL 政策/技术分别有 BM25 或双语检索候选进入上下文，但真实模型输出未重跑。

**验证：**新增 unittest 5/5 通过；Python compileall 通过；`npm run build` 通过；rule 演示通过（分数 72.75、3 条证据）。旧 pytest 套件未能运行：本地 `.venv` 缺 pytest。本轮 0 次模型调用。

**本轮改动：**安全 run 目录和未来代码/prompt/模型请求/数据快照哈希；v1.1 历史复算；四路离线检索脚本、双语通用指标 query 定义、六家公司候选评测集；防覆盖测试；本交接与审计文档。旧 raw、gold、PDF、人工复核 CSV 未改。

**下一轮最多三项：**

1. 人工确认 `pending_evidence_review.csv` 中问题、引文页、chunk 边界和多片段要求；为至少一个不可回答问题明确标记，区分“原文不存在”与“检索未召回”。
2. 确认标签后复跑四路检索；现有暂定结果支持先验证 C（BM25）作为可配置候选，暂不保留更复杂的 RRF，除非复核后结果改变。
3. 标签确认后再做一次真实模型基线：6 家×1 次 comprehensive run；预计每家公司技术/产业各 1 次，共 12 次主调用，若每个 Agent 各触发一次格式修复，上限约 24 次。此计划尚未执行。

**待人工材料：**候选证据表的标签与支持片段确认；需要共同支持的片段组确认；显式不可回答样例。不得覆盖人工复核 CSV。

**关键产物：**[详细审计](iteration_02_audit.md)；[历史复算脚本](../../evaluation/recompute_history.py)；[检索对照脚本](../../evaluation/run_retrieval_comparison.py)；[候选待确认表](../../runtime/review/retrieval/run-20260928T050135Z-1845fc0e/pending_evidence_review.csv)；[逐案例/逐方法机器结果](../../runtime/review/retrieval/run-20260928T050135Z-1845fc0e/retrieval_comparison.json)；[复算 v2 结果](../../runtime/review/recompute/run-20260928T045859Z-6cc9d628/recomputed_metrics_v2.json)。

**复算命令：**`backend/.venv/Scripts/python.exe evaluation/recompute_history.py`  
**检索命令：**`backend/.venv/Scripts/python.exe evaluation/run_retrieval_comparison.py`
