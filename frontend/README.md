# 科衡前端

React 19、TypeScript、Vite 7 实现企业名称入口与证据增强报告。报告展示技术生命周期、技术证据、经营与财务维度、技术—经营关联、待补充信息以及来源与方法。

## 启动与构建

需要 Node.js 22.12+。在本目录执行：

```powershell
npm ci
npm run dev -- --host 127.0.0.1
```

打开 <http://127.0.0.1:5173>。开发服务器将 `/api` 代理至 <http://127.0.0.1:8000>，后端启动方式见[项目说明](../README.md)。

```powershell
npm run build
```

生产部署时设置 `VITE_API_BASE_URL`（默认 `/api`），如 `https://example.com/api`，或配置同源反向代理。前端不保存供应商 API Key。

## 页面与接口

- 首页输入企业名称，调用 `POST /api/evidence-analysis` 创建任务。
- 分析期间调用 `GET /api/evidence-analysis/{task_id}` 获取进度与完成结果。
- 通过 `/?company_id=<编码后的企业ID>` 重新打开报告，调用 `GET /api/report/v2/company/{company_id}`。
- “刷新已保存报告”读取已有结果；“分析其他企业”清除企业查询参数。
- 来源区域提供事实、片段定位、引用关系和原始 URL。

主流程接口集中于 `src/api.ts`。旧 PDF 评分与内置样例不作为首页入口。正在运行的任务状态依赖后端进程；当前界面不提供聊天、用户权限或报告编辑功能。
