# Iteration 01 交接

## 当前系统实际是什么

可运行的本地 FastAPI + React MVP：文本 PDF 按页解析；字符 2–4 gram、1024 维哈希向量存 Chroma，按 task_id 检索；上传 API 默认 Technology rule 抽取、固定权重评分、结构化报告。真实 LLM 与技术+产业综合在独立实验脚本/opt-in 服务中，不是普通上传默认链路。

## 最影响结果的 5 个问题及证据

1. 固定中文 char-ngram query、top-3，无 BM25/重排；SMIC/NIO 英文年报页与 query top-k 错位，Estun 专利 p15 未进入 IP top-3。
2. “证据率 100%”只查结构化 E ID 存在性：旧 v1.1 可重算 294/294 Tech、120/120 Industry；不查原文、页码、蕴含、rationale 自由文本引用。SMIC rationale 有孤儿 E1，而该指标 evidence=[]。
3. Technology MAE 7.6471/n=68 只用 gold 和实际分数都非空项；85 个非空 gold 中 17 个模型 null 被排除；case_003 五次成熟度给分但 gold=null，也不计误差。
4. 上传 API 默认规则单域、无 Provider 注入；fallback 可退回规则但不标模式，前端无法区分 rule/LLM。
5. 原始实验能离线重算，但无 code commit/hash、prompt hash；v1.1 runner 会查模型并调用 API，不是离线复算命令。仓库无 commit、文件未跟踪。

## 已执行验证

- Python 3.12.10：36/36 unittest 通过。
- Node 24.11.1、npm 11.6.2：frontend npm run build 通过。
- 合成 PDF 独立 rule E2E：72.75 分、3 条证据，写入新 runtime/review/iteration_01。
- v1.1 raw 离线复算与旧汇总一致；未调用模型。
- 六份年报 SHA 匹配 manifest；材料未修改。

## 本轮实际修改

新增三份审计文档；无代码、论文或旧实验向量内容修改。Chroma SQLite 元数据时间戳受空 collection 清理影响，细节见下文。README demo 会覆盖 data/examples 和 frontend/public 的 JSON，因此本轮没有运行。

## 下一轮最多三项

1. 固定评测 PDF/gold/prompt/model/code 哈希，并提供离线 raw 复算 CLI。
2. 校验自由文本引用、PDF 原文/页码，分别记录召回、抽取、评分失败。
3. 建立六家双语失败页回归集，先离线召回与人工核证，再决定是否少量模型复验。

## 需要人工补充

- 确认 data/real_cases/human_review_v10.csv 的来源、复核方法与发布授权。
- 确认竞赛模板、字体、bst/sty 等第三方素材许可。
- 补 v0.9/v1.0/v1.1 精确 prompt 快照及对应 Git SHA；否则继续标明历史代码版本未找到。
- 核实根目录疑似 API key CSV 是否有效；本轮仅识别路径、确认 gitignore 匹配，未展示内容。

## 关键产物

- docs/review/iteration_01_audit.md
- docs/review/iteration_01_real_case_diagnosis.md
- docs/review/iteration_01_handoff.md
- 本地历史记录：runtime/experiments/v11/、runtime/real_cases/（忽略目录，不适合直接公开）
- 额外写入风险：evaluation/run_real_cases.py 会重写人工复核 CSV 并重置复核列，不能当只读重跑；README demo 同样覆盖静态 JSON。
- 本轮有一次 Chroma 检索探查短暂创建了空辅助 collection；发现后已删除六个 count=0 collection，并确认旧 collection 文档计数不变（556/1386/589/701/593/2912）。Chroma SQLite 元数据文件时间戳因此更新，既有向量未变。
