# 科衡后端

FastAPI 后端提供企业名称分析、任务状态查询与已保存报告读取。公开资料研究、技术语义处理、科技金融映射和证据断言共用来源可追溯的知识库。

## 启动

安装依赖并配置本地 `backend/.env`，详见[根目录运行说明](../README.md)。从仓库根目录启动：

```powershell
backend/.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

接口文档：<http://127.0.0.1:8000/docs>；健康检查：`GET /health`。运行配置来自 `app/core/config.py`，使用 `KEHENG_` 前缀环境变量及后端本地 `.env`。

## 主流程接口

| 接口 | 输入或结果 |
|---|---|
| `POST /api/evidence-analysis` | JSON：`{"enterprise_name":"企业名称"}`；创建任务 |
| `GET /api/evidence-analysis/{task_id}` | 任务状态、阶段、完成报告或安全错误信息 |
| `GET /api/report/v2/company/{company_id}` | 已保存的企业证据报告 |

真实主流程要求配置兼容的结构化模型和 Tavily。任务状态保存在进程内；共享知识库及企业结果保存在仓库根目录 `runtime/knowledge/`，不提交到 Git。

## 主要模块

- `app/api/`、`app/schemas/`：HTTP 路由与输入输出契约。
- `app/services/evidence_analysis_tasks.py`：任务管理及正式运行目录。
- `app/services/evidence_analysis_service.py`：完整企业分析编排。
- `app/knowledge/`：来源、片段、主体、共享存储和证据断言。
- `app/research/`：迭代检索、来源识别与企业主体解析。
- `app/knowledge/semantic/`、`app/finance/`：技术语义、经营财务事实及金融映射。
- `app/report_v2/`：证据报告组装；`app/agents/`、`app/report/` 保留旧版分析能力。

旧 PDF `/analysis/*` 与评分相关入口保留兼容用途，不是当前首页流程。历史文档与代码不一致时，以代码为准，见[文档导航](../docs/README.md)。
