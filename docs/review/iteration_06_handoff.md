# 科衡 Iteration 06 交接

## 定向真实复验

使用原百炼兼容配置和 `qwen3.7-plus`，只运行 Estun/hash 技术模块。复用了第四轮保存的**实际** 6 段上下文（3,021 个证据字符，PDF 页序 111、189、73、7、5、66），未重检索、未运行产业模块，也没有查询模型列表。新旧提示词 SHA-256 前缀为 `cbb23cb7cb60` → `ee9b2ac295f4`；本轮温度 0、HTTP 自动重试 0、最多格式修复 1 次。实际 HTTP 生成请求 **1 次**，修复 0 次，耗时 18.46 秒；prompt/completion/total token 为 3,082/1,036/4,118（其中 reasoning 820）；响应未提供费用字段，因此不估价。

模型返回 `summary_status=insufficient_evidence`、固定拒答文本和 `summary_evidence_ids=[]`；四项指标均为 `null`、空引用，知识产权也没有输出专利数量。schema 和当前任务 ID 校验通过。**具体“判断—引文”例子：**“当前检索片段不能支持技术事实摘要” → 无事实引文，结构化摘要引用为空；六段实际上下文没有专利文字，所以 IP 为证据不足。没有可诚实提供的正面“判断—原文”配对，也不能由此推断年报原文不存在专利信息。

历史失败根因仍是：当时 schema 用 `min_length=1` 强制摘要至少引用一个 E ID，而提示词又把通用“无法判断返回空 evidence_ids”与摘要引用要求并列，合法拒答因此被当结构失败。本轮新增 `summary_status`：事实摘要必须绑定本任务证据；拒答必须使用精确拒答文本和空 ID；ID 有效仅证明结构/任务域正确，不证明语义支持。真实请求后又将 `summary_evidence_ids` 收紧为必填字段、允许空数组；本轮响应已显式包含此字段，因此该调用后的 schema 收尾不改变复验结果。新请求和 raw 保存在 `runtime/review/targeted_replay/estun-hash-tech-20260928T120200Z-22e0a01d/`；旧结果未覆盖。

## 干净环境验收

| 项目 | 结果 |
| --- | --- |
| 新建 Python 3.12 venv，安装 `backend/requirements-lock.txt` | 通过；首次 `python` 命令别名不可用，改用机器 Python 3.12 可执行文件创建新环境后安装成功 |
| 前端 `npm ci` | 通过；69 个包，npm audit 报告 0 vulnerabilities |
| 前端 `npm run build` | 通过（`tsc -b && vite build`） |
| 无密钥启动后端/前端 | 通过；真实模型环境变量为空 |
| 合成 PDF 经 React 文件选择和真实上传 API | 通过；生成 `RULE DEMONSTRATION` 报告，技术模块完成，产业 `not_run`，页面显示技术 4/4 与“产业未运行” |
| 真实模式缺少模型配置 | 通过；任务 `failed / model_not_configured`，明确列出需配置的后端变量，不回退规则模式 |
| 干净克隆历史回放入口 | 通过；无本机结果时 UI 隐藏入口，可用性 API 返回 false |
| 最终公开文件快照离线回归 | 通过，29 项 API、合成评价、Provider/Agent、拒答与配置安全测试 |

实际 React 规则演示报告截图已随本交接在对话中展示。该截图标明 `RULE DEMONSTRATION`，来自项目合成企业资料，不是模型结果。公开文件清单见 `iteration_06_public_file_manifest.json`。

## 默认配置与公开候选

- 默认检索冻结为 `hash`；BM25 仍可配置，但候选标签和样本不足以证明其全面更好。
- 默认模型上下文组装为 `balanced_sentences_v1`，最多 8 段、4,160 个**字符**，查询组轮转、句子边界裁剪与重复句去重。离线历史上下文比较/回归已完成；token 等价性及真实模型收益未验证。**CATL 历史检索找到但没有送入模型的截断问题不宣称已修复。**
- 发布候选文件清单 204 个文件、1,477,201 字节；无 `.git`、运行产物、评审目录、个人配置、`node_modules`、构建缓存、人工复核 CSV 或真实企业 PDF。7 个随附 PDF 均为合成演示/评测资料。API key CSV 与真实企业人工复核 CSV 在本地被 `.gitignore` 命中，不进入候选快照。
- 扫描候选文件未发现疑似 token、非空 API Key 配置、个人绝对路径或超过 2 MiB 文件。真实年报下载链接和获取说明位于 `data/DATA_SOURCES.md` 与各企业 README；公开链接不等于允许再分发原文。
- 现有 `LICENSE` 声明 MIT；本轮没有更换或另选许可证。发布前必须确认贡献者对该声明有授权，并解决 `PyMuPDF` 的 AGPLv3/Artifex 双许可适用方式；依赖清单还有完整传递依赖 NOTICE/许可证核对事项。前端直接依赖许可证已从包元数据核对（MIT / Apache-2.0）。
- `git status` 显示仓库文件整体为未跟踪状态，且没有可记录的 Git commit。所有变更均保留；发布前需由维护者根据清单明确纳入范围，本轮没有 stage、commit 或 push。

## 本轮实际改动与剩余阻碍

- 修正提示词、Pydantic schema、Agent 拒答校验与测试；无证据拒答不再因缺一个 E ID 而失败，事实摘要无引用/不符合精确拒答的内容仍拒绝。
- 实验 runner 不再从本地 API Key CSV 自动发现凭据，只接受显式 `KEHENG_LLM_API_KEY`；新增回归测试。`.gitignore` 新增真实人工复核 CSV 与证据确认文件规则。
- 新增 Python 固定依赖文件、前端/后端离线 CI、数据来源说明，重写 README；干净克隆无历史结果时隐藏 NIO 本地回放按钮。
- **发布前必须处理：**维护者确认现有 MIT 权利声明和 PyMuPDF 双许可的合规路径；复核剩余传递依赖许可/NOTICE；以 Git 文件清单建立首个受控版本。
- **可后续完善：**需要 Estun 年报相关页进入检索上下文后，才有理由做一次新的 IP 模型复验；需要人工确认候选证据后才能形成正式准确率。当前复验只证明拒答契约工作，不证明检索覆盖改善或语义引用正确率。

## 常用命令

```powershell
py -3.12 -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-lock.txt
backend/.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

cd frontend
npm ci
npm run dev -- --host 127.0.0.1

cd ..
backend/.venv/Scripts/python.exe -m unittest tests.test_analysis_api tests.test_evaluation_engine tests.test_evaluation_cases tests.test_llm_extraction tests.test_iteration05_schema_repair tests.test_iteration06_config_safety -v
```
