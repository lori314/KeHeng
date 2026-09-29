# KeHeng Backend

科衡后端采用 FastAPI 模块化单体结构。v0.5 增加统一分析服务和进程内任务状态：浏览器上传 PDF 后，由同一服务依次完成解析、任务独立知识库、Technology Agent、确定性评价和报告生成。报告生成器只复制既有分数、指标、优势、风险和证据，并拒绝断裂的 E 编号引用。当前实现不调用外部 AI API，也不构成正式尽调或融资评分。

## 本地启动

在 `backend/` 目录执行：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

启动后可访问：

- `GET http://127.0.0.1:8000/health`
- `GET http://127.0.0.1:8000/`
- `POST http://127.0.0.1:8000/analysis/create`
- `GET http://127.0.0.1:8000/analysis/{task_id}`
- `POST http://127.0.0.1:8000/report/generate`
- `http://127.0.0.1:8000/docs`

运行配置通过 `KEHENG_` 前缀环境变量覆盖，例如 `KEHENG_ALLOWED_ORIGINS`。模型密钥不得写入仓库或 YAML 配置。

## 模块边界

- `app/api/`：HTTP 路由。
- `app/core/`：配置和横切能力。
- `app/models/`：领域枚举及后续持久化模型边界。
- `app/schemas/`：公共 API 数据模式。
- `app/rag/`：解析、向量化、检索和知识库抽象接口。
- `app/agents/`：专业 Agent 的输入输出契约与占位类。
- `app/evaluation/`：评价模块网关；正式指标配置保留在仓库根目录 `evaluation/`。
- `app/report/`：报告校验与渲染边界。
- `app/services/analysis_service.py`：PDF 到报告的唯一应用编排入口。
- `app/services/analysis_tasks.py`：v0.5 单进程任务状态与执行管理。

所有未实现能力均应保持显式失败，不得用看似真实的模拟分析结果替代。

## 分析接口

`POST /analysis/create` 使用 `multipart/form-data`，字段为 `enterprise_name` 和 `file`。接口校验 PDF 扩展名、媒体类型、文件签名、空文件和大小，成功后返回 `task_id` 与 `processing`。随后轮询 `GET /analysis/{task_id}`；完成时返回 `report`，损坏 PDF 或无可提取文本时返回 `failed` 和稳定错误码。

每个任务的 Chroma 数据写入独立目录，并继续使用 `task_id` 过滤。任务状态当前只保存在运行进程中，服务重启后不能恢复；这是 v0.5 比赛 Demo 的明确边界。

## 运行 v0.5 示例

安装 `requirements.txt` 后，在仓库根目录执行：

```powershell
backend\.venv\Scripts\python.exe scripts\run_technology_demo.py
backend\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

输入文件为 `data/examples/public_test_company_technology_profile.pdf`，输出为 `data/examples/technology_analysis.json`、`data/examples/evaluation_result.json` 与 `data/examples/technology_report.json`。脚本调用与 API 相同的统一分析服务，本地运行数据写入已忽略的 `runtime/`。
