# 示例数据说明

本目录中的 `public_test_company_technology_profile.pdf` 是为 KeHeng 创建的公开测试材料，描述的“启衡智造技术有限公司”完全虚构，企业、人员、产品、专利、客户和测试数据均不对应真实主体。

该 PDF 仅用于验证以下技术链路：文本型 PDF 解析、按页切分、本地向量检索、`task_id` 隔离、Technology Agent 结构化输出和证据定位。不得将示例分析结果用于真实融资、授信、投资或企业评价。

`technology_analysis.json` 由 `scripts/run_technology_demo.py` 基于该 PDF 生成，包含 Technology Agent 提取的四项技术指标与 E 编号证据。`evaluation_result.json` 由确定性评价引擎生成，包含固定权重计算、维度分、逐项解释和证据映射。`technology_report.json` 由报告生成器装配前两项结果，并同步到 `frontend/public/` 供 Web 页面展示。三份 JSON 均可重复覆盖。

`report_template.md` 描述标准报告的章节、指标表和证据索引占位结构；当前 v0.4 API 输出结构化 JSON，不执行 Markdown 模板渲染。
