# 科衡 Iteration 04 交接

## 结论

修复了离线检索评测把指定 chunk ID 当成唯一正确命中的问题。现在以 PDF 哈希、页码、支持原文及字符位置作为锚点，分别统计旧 ID 命中、原文命中和最终模型上下文中原文是否完整保留。NFKC/大小写/空白规范化不改变数字、否定、时间和单位；等价改写不自动算支持。

8 题旧/新判定和 A/B/C/D 离线结果在 `iteration_04_comparison.md`。CATL 技术自主性 B/D 的支持句完整进入上下文；C top-10 找到但预算截断。CATL 政策按两条已标注证据和“至少一条有效政策”分开。候选标签仍未人工确认，留出集曾在 Iteration 03 查看，不能称完全未见测试集。

## 真实模型配对运行

最初本地设置缺失时的运行是零请求预检。随后按用户指示读取工作区 CSV 的 API key 与 OpenAI-compatible 地址到单进程环境，通过百炼 `/models` 列表核对并选择 `qwen3.7-plus`。未输出或持久化 API key；环境进程退出后未保留。共 4 次只读模型列表 GET，配对运行 9 次 HTTP 请求（8 次模块主请求 + 1 次格式修复），合计 13/16；SDK/transport 重试为 0，没有停止条件。

NIO/hash、NIO/BM25、Estun/hash、Estun/BM25 均已运行。7/8 模块成功；Estun/hash 技术模块在格式修复后仍 `schema_failure`，不是业务证据不足。usage 总计 41,075 tokens（prompt 23,334 / completion 17,741），总耗时约 383.27 秒。供应商响应未给出货币费用，未估算。该运行是单次 B 对 C 检索器探索性比较；人工标签仍待确认，`formal_accuracy_eligible=false`。

NIO/hash 的最终上下文含 p.146 原文“5,693 issued patents and 4,122 pending patent applications”，输出正确区分授权与待审；NIO/BM25 未将该数字句送入上下文，输出较概括且未编造数字。Estun/hash 上下文未含专利 p.15，且技术结构化输出失败；Estun/BM25 将 p.15 的新增/累计、授权/发明/申请数据完整送入上下文，输出的累计 590 项授权专利、252 项发明专利及 425 项软件著作权与原文相符，156 项待授权申请未被输出。逐项例证见 `iteration_04_model_comparison.md`。

本样本方向不一致，建议暂不改 hash 默认；BM25 保持候选方案。两家各一次、标签未确认且一格模型失败，不能宣称检索普遍提升，也不计算非 IP 指标的准确率。

## 核验

- 离线 A/B/C/D 复算：`runtime/review/retrieval/run-20260928T093820Z-aaed46af/retrieval_comparison.json`，无模型请求。
- 最新配对原始结果、实际发送上下文、raw 响应、usage/延迟：`runtime/review/paired_model/run-20260928T110017Z-2bc83078/paired_model_results.json`。
- Estun PDF 页序 15 与上下文摘录核验：同目录 `ip_review_extract.md`、`estun_hash_context.md`。
- 已从本轮保存结果生成并在本地网页打开 NIO/hash 报告核验页：`runtime/review/iteration04_source_pages/nio_hash_qwen37plus_iteration04_preview.html`。页面标记探索性真实模型结果；产业覆盖 3/4，市场潜力未评分，不表示企业没有市场潜力。
- 此次未改代码。此前本轮后端 54 项测试与编译通过；本次模型配对不替代测试，也没有重复模型调用查看报告。

## 后续最重要的问题

1. 先人工确认 NIO p.146、Estun p.15 的证据及题目，再决定是否纳入正式准确率。
2. 调查 Estun/hash 的结构化失败，并让模型系统错误持续与业务证据不足分开显示。
3. 在确认标签后再扩展配对；记录正式费用/账单并补充不受支持给分复核。

## 关键产物

- 回传对照：`docs/review/iteration_04_model_comparison.md`
- 评测口径和 8 题变化：`docs/review/iteration_04_comparison.md`
- 原始模型运行：`runtime/review/paired_model/run-20260928T110017Z-2bc83078/paired_model_results.json`
- 网页报告核验页：`runtime/review/iteration04_source_pages/nio_hash_qwen37plus_iteration04_preview.html`
- 下一轮若重跑已执行格，先复用本次成功结果；本轮主调用预算已用满，不应重复本次配对。
