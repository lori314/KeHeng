# KeHeng（科衡）

KeHeng（科衡）是科技企业证据增强智能尽调原型。当前 Web 主流程从企业名称启动 V2 Evidence Analysis Task，检索公开资料并形成带来源、定位信息与边界说明的 Evidence-First Report。系统用于辅助尽调，不替代人工核验或正式决策。

## 当前 Web 主流程

输入企业名称 → 创建 Evidence Analysis Task → 公开资料检索 → 技术语义处理 → 科技金融映射 → 证据断言 → Evidence-First Report。报告优先展示事实、来源、证据定位、有限支持状态和待补充信息，不提供综合评分。

旧 PDF 上传及评分 API 仍保留在后端供兼容使用，但不再作为前端主入口。V2 需要配置真实 LLM 与 Web Search provider；缺少 provider 时 API 会返回配置错误，不会静默切换到合成或规则结果。

## 快速开始（Windows PowerShell）

要求 Python 3.12+、Node.js 22+ 和 npm。首次安装需要访问 Python 包索引与 npm registry；V2 分析需要访问配置的 LLM 与 Web Search 服务。

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

## 旧版兼容流程

后端仍保留 PDF 上传与 score-centric API，合成 PDF CLI 示例也可单独运行；这些入口不是当前 Web 主产品流程：

```powershell
backend/.venv/Scripts/python.exe scripts/run_technology_demo.py
```

每次命令在 `runtime/demo_runs/run-*` 创建新目录，不覆盖已有运行或 `data/examples/` 中的文件。结果包括技术分析、确定性评价和报告 JSON。

## 配置 V2 服务

将 `backend/.env.example` 复制为 `backend/.env`，只在本地填写以下后端变量：

```dotenv
KEHENG_LLM_ENDPOINT=
KEHENG_LLM_MODEL=
KEHENG_LLM_API_KEY=
KEHENG_LLM_TIMEOUT_SECONDS=120
KEHENG_WEB_SEARCH_PROVIDER=tavily
KEHENG_TAVILY_API_KEY=
```

密钥仅保留在后端本地环境。重新启动后端后，首页输入企业名称即可启动 V2 分析。V2 API 为 `POST /api/evidence-analysis`、`GET /api/evidence-analysis/{task_id}` 和只读报告 `GET /api/report/v2/company/{company_id}`。

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
- V2 名称检索分析任务：`POST /api/evidence-analysis` 创建、`GET /api/evidence-analysis/{task_id}` 轮询；完成时响应含 V2 evidence-first report。
- V2 报告刷新：`GET /api/report/v2/company/{company_id}`。旧 PDF `/analysis/*` 入口保持原 score-centric demo 流程，不自动切换。
- 所有演示及分析产物写入新的 `runtime/` 子目录；旧结果只读。
- 当前真实模型证据上下文使用 `balanced_sentences_v1`：最多 8 个片段、4,160 个证据文本字符，按查询组轮转并去重句子。预算单位是字符，不是 token。此策略通过离线历史上下文回放与合成回归；其对真实模型质量的收益尚未验证。历史 CATL 检索到但未送入上下文的问题未据此宣称修复。

## 数据和来源

`data/examples/` 和 `data/evaluation_cases/` 中样例为项目自有合成资料；来源属性和使用边界见随目录说明。真实企业年报的公开下载链接、发布日期及获取方式见 `data/DATA_SOURCES.md` 和 `data/real_cases/<company>/README.md`。本仓库候选公开文件不包含年报 PDF、人工复核 CSV、模型调用日志或未确认发布范围的企业原文摘录。

## 许可与限制

仓库现有 `LICENSE` 声明 MIT。发布前仍需由项目维护者确认版权归属与提交者授权，并核对依赖及可能新增材料的第三方许可。年报下载链接仅用于来源定位；公开可访问不等于可以再分发全文。系统目前只解析可提取文本的 PDF，扫描件、复杂表格、OCR、语义支持验证、 token 预算校准、专家金标准和生产级权限认证都未完成。
