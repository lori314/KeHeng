# KeHeng（科衡）

KeHeng 是一个本地运行的科技企业资料分析原型，用于把 PDF 文本、可定位证据、指标观察和报告串在一起，供竞赛演示与人工复核。它不是授信、投资或融资决策工具，也不提供企业事实的权威认定。

## 当前可运行模式

| 模式 | 实际行为 | 是否调用模型 |
| --- | --- | --- |
| 规则演示 | 上传 PDF，运行本地检索和技术指标规则；不生成产业分析；缺证据时保留未评分状态 | 否 |
| 真实模型 | 用同一上传与分析服务运行技术、产业模块并生成综合报告；Provider、提示词、检索及评分配置由后端控制 | 是，需后端配置 |
| 本机历史报告（可选） | 读取仓库作者本地保存的 NIO 历史结果；只读回放，公开快照没有这份私有运行记录 | 否 |

模型的结构化输出和引用存在性校验不等于人工确认或语义支持验证。企业资料未检出证据时不能据此推断企业不具备相关能力。规则演示只覆盖技术模块，不会伪造产业分数。真实模型配置缺失或调用失败时显示系统错误，不会静默回退到规则模式。

## 快速开始（Windows PowerShell）

要求 Python 3.12+、Node.js 22+ 和 npm。首次安装需要访问 Python 包索引与 npm registry；分析 PDF 和规则演示本身无需联网。

```powershell
# 从仓库根目录执行
py -3.12 -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-lock.txt

# 终端 1：后端
backend/.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

# 终端 2：前端
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

打开 <http://127.0.0.1:5173>。不要在前端配置或提交 API Key。

## 三种使用方式

### 1. 无密钥规则演示

网页选择“规则演示”，上传项目自有合成资料 `data/examples/public_test_company_technology_profile.pdf`。它描述完全虚构的启衡智造技术有限公司。此模式只运行技术规则分析，不请求云服务。

也可运行同一合成 PDF 的 CLI 端到端样例：

```powershell
backend/.venv/Scripts/python.exe scripts/run_technology_demo.py
```

每次命令在 `runtime/demo_runs/run-*` 创建新目录，不覆盖已有运行或 `data/examples/` 中的文件。结果包括技术分析、确定性评价和报告 JSON。

### 2. 本机历史真实模型报告回放（可选）

有权限的开发机可以从网页打开“NIO 已保存模型报告”。这只读取 `runtime/review/paired_model/` 中已经保存的响应，不会再请求模型。历史运行目录不随公开文件快照分发，因此此入口在干净克隆中不可用；它不是产品的主演示路径。

### 3. 配置真实模型

将 `backend/.env.example` 复制为 `backend/.env`，只在本地填写以下后端变量：

```dotenv
KEHENG_AGENT_MODE=llm
KEHENG_RETRIEVAL_MODE=hash
KEHENG_LLM_ENDPOINT=
KEHENG_LLM_MODEL=
KEHENG_LLM_API_KEY=
KEHENG_LLM_TIMEOUT_SECONDS=120
```

密钥仅保留在后端本地环境。重新启动后端，在网页选择“真实模型”。当前默认检索为 `hash`；`bm25` 可通过 `KEHENG_RETRIEVAL_MODE=bm25` 配置为候选方案。现有样本不足以证明 BM25 全面优于 hash。模型 ID、服务端点、温度和提示词版本应与每次实验结果一起记录。

实验脚本也只接受显式的 `KEHENG_LLM_API_KEY` 环境变量，不会从仓库根目录的 CSV 或其它个人文件自动发现密钥。

## 离线回归与测试

无需 API Key 的小规模固定回归：

```powershell
backend/.venv/Scripts/python.exe -m unittest tests.test_evaluation_engine tests.test_evaluation_cases tests.test_llm_extraction tests.test_iteration05_schema_repair tests.test_iteration06_config_safety -v
```

前端生产构建：

```powershell
cd frontend
npm ci
npm run build
```

完整真实企业检索对照需要本地准备年报 PDF；原始企业 PDF、人工复核表和模型 raw 不包含在公开样例中。`evaluation/` 中与真实企业对应的离线评测脚本需要先取得相应材料，详见 `data/DATA_SOURCES.md` 和各企业目录说明。无标签确认的候选结果不得称为正式准确率。

## 配置和结果

- Python 范围声明：`backend/requirements.txt`；本地验收的固定依赖：`backend/requirements-lock.txt`。
- 前端依赖锁：`frontend/package-lock.json`，使用 `npm ci`。
- 无密钥配置模板：`backend/.env.example`。
- API 默认地址：<http://127.0.0.1:8000>；Swagger：<http://127.0.0.1:8000/docs>。
- 所有演示及分析产物写入新的 `runtime/` 子目录；旧结果只读。
- 当前真实模型证据上下文使用 `balanced_sentences_v1`：最多 8 个片段、4,160 个证据文本字符，按查询组轮转并去重句子。预算单位是字符，不是 token。此策略通过离线历史上下文回放与合成回归；其对真实模型质量的收益尚未验证。历史 CATL 检索到但未送入上下文的问题未据此宣称修复。

## 数据和来源

`data/examples/` 和 `data/evaluation_cases/` 中样例为项目自有合成资料；来源属性和使用边界见随目录说明。真实企业年报的公开下载链接、发布日期及获取方式见 `data/DATA_SOURCES.md` 和 `data/real_cases/<company>/README.md`。本仓库候选公开文件不包含年报 PDF、人工复核 CSV、模型调用日志或未确认发布范围的企业原文摘录。

## 许可与限制

仓库现有 `LICENSE` 声明 MIT。发布前仍需由项目维护者确认版权归属与提交者授权，并核对依赖及可能新增材料的第三方许可。年报下载链接仅用于来源定位；公开可访问不等于可以再分发全文。系统目前只解析可提取文本的 PDF，扫描件、复杂表格、OCR、语义支持验证、 token 预算校准、专家金标准和生产级权限认证都未完成。
