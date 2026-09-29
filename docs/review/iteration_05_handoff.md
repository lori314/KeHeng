# 科衡 Iteration 05 交接

## 本轮结果

- Estun/hash 的两份保存响应都是合法 JSON，且响应内容相同；唯一 schema 错误是 `summary_evidence_ids=[]` 不满足 `min_length=1`。根因是模型没有给事实摘要绑定上下文 E 编号；原提示词中“无法判断时返回空 evidence_ids”有歧义，旧修复提示又不包含字段错误。本轮澄清摘要/指标拒答规则，修复请求附上安全的字段级诊断，失败状态继续是 `schema_failure`，不转换成业务证据不足。
- 新上下文策略 `balanced_sentences_v1`：保留全局分数前 4 个候选，然后按查询组轮转；按完整句末/换行边界去重；最多 8 chunk、4,160 字符。字符不是 token。request history 现在保存实际发送 excerpt、对应来源和组装审计信息。预算未扩大，选择不读取标签/答案。
- CATL p.15“形成以自主研发为主、外部合作为辅的研发模式”只能说明两种研发关系并存。第四轮 B/D 已送入该句，C 虽 top-10 找到但旧上下文遗漏。当前 hash/BM25 重建的旧、新组装均保留 CATL 句，因此未证明历史 C 失败已修复。
- 新建的上下文离线重放比较见 `runtime/review/context_assembly/run-20260928T114819Z-8ee27edf/context_assembly_comparison.json`。同候选池比较中，Estun/BM25 的完整数字句旧、新都在（3,773→3,568 字符，重复句占比估计 4.72%→2.33%）；Estun/hash 均未召回该句（3,026→2,866 字符）。NIO/hash 与 BM25 候选标签句均不在上下文，但 NIO/hash 实际保存上下文另含 p.146 的 5,693 已授权专利、4,122 待审申请证据。候选标签尚未人工确认，不能作准确率结论。
- 四份企业/方法上下文比较、历史实际上下文核对与专利输入—判断—引用详例见 [`iteration_05_case_comparison.md`](iteration_05_case_comparison.md)。

## 页面与运行方式

当前 React 报告页已实际打开 NIO/hash 保存报告：技术覆盖 4/4、产业 3/4、参考总分 63.12；显示 `SAVED REAL MODEL RESULT · NO LIVE CALL`、qwen3.7-plus 和原运行时间；E6 可定位到年报 PDF 页序 146，未评分的市场潜力和补材料建议可见。API GET 只返回报告/模块/运行信息，不带 raw 响应，不产生模型调用。当前浏览器地址：`http://127.0.0.1:5173/?replay=iteration04-nio-hash`。

从仓库根目录在两个终端分别启动后端与前端：

```powershell
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

第二个终端：

```powershell
cd frontend
npm install
npm run dev
```

演示/复核命令：

```powershell
backend\.venv\Scripts\python.exe scripts\run_technology_demo.py
backend\.venv\Scripts\python.exe evaluation\replay_context_assembly.py
```

网页无密钥规则演示读取合成 PDF，可从“下载合成 PDF 后上传”取得；它仅产出技术规则结果。历史模型回放入口或固定 query-string 读取本机已保存 NIO/hash 报告，不调用模型；干净公开克隆没有 `runtime/` 私有结果时会显示不可用。配置后，真实模式仍通过同一上传 API/分析服务运行技术和产业 Agent。

## 验证与边界

- 真实模型历史原始响应离线 schema 复验：首响应和修复响应均命中同一字段错误；未改 raw，也没有重跑请求。
- 组装器回归覆盖查询组轮转、预算排除、句子去重、来源定位和 Estun 式分号复合指标句。
- 全量后端测试 61 项通过，`compileall` 通过，前端 `tsc -b && vite build` 通过；无密钥演示 CLI 写入 `runtime/demo_runs/run-20260928T113446Z-d1c21cb0/`（技术分 72.75、3 个证据）；保存报告 API 为 200，React 历史回放在浏览器确认呈现。没有新增付费模型请求。
- 未验证：新修复提示能否让真实模型成功、不同 tokenizer 下的 token 预算、其它 PDF 的版面/跨句支持效果。历史回放报告不是实时分析。

## 下一步优先项（最多 3 项）

1. 让人工复核确认 NIO p.146 与 Estun p.15，再更新正式准确率口径；AI 复核不作专家金标准。
2. 在明确的低调用预算下，仅重跑 Estun/hash 技术模块一次（主请求 1 次、最多格式修复 1 次），验证 schema 修复；保留原失败 raw 并比对上下文和字段诊断。
3. 取得更多按任务查询组覆盖的真实上下文案例，再决定是否调整 hash/BM25 默认；CATL 历史 C 失败仍需单独复测。

本轮实际代码与文档变更包括 `backend/app/llm/api_model.py`、`backend/app/rag/context_assembly.py`、两个 Agent、`evaluation/replay_context_assembly.py`、历史回放 API、React 报告页、回归测试、README 与系统/AI/测试说明。旧模型结果、PDF、人工标签未覆盖；API key CSV 保持在本地并由 `.gitignore` 排除。
