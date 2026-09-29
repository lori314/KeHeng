# KeHeng Frontend

科衡前端 v0.5 使用 React、TypeScript 和 Vite，提供企业名称、PDF 上传、分析状态轮询和动态报告展示。页面展示技术综合评分、维度及指标分、核心优势、风险因素、评价过程和原始证据，但不在浏览器中计算评分。

## 本地启动

```powershell
npm install
npm run dev
```

开发地址默认为 `http://127.0.0.1:5173`。

开发服务器把 `/api` 代理到 `http://127.0.0.1:8000`。正常流程调用后端 `/analysis` 接口；`public/technology_report.json` 只用于用户主动选择的“内置虚构样例”。生产环境可通过 `VITE_API_BASE_URL` 指定 API 地址。

## 构建检查

```powershell
npm run build
```

当前任务状态由后端单进程维护，页面刷新后不会自动恢复轮询；当前不包含用户权限、报告编辑或扫描件 OCR。
