# Iteration 06 公开候选文件与许可检查

## 快照范围

独立临时快照共 **204 个文件、1,477,201 字节**。逐文件相对路径、字节数和 SHA-256 见 [`iteration_06_public_file_manifest.json`](iteration_06_public_file_manifest.json)。它包含 README、MIT LICENSE、`.gitignore`、CI、后端/前端源码与依赖锁、合成数据、配置、提示词、测试和面向项目的技术文档。

候选快照排除了 `.git/`、`AGENTS.md`、`paper/`、`docs/review/`、`runtime/`、`tmp/`、个人环境、API Key CSV、真实企业人工复核 CSV、证据确认文件、真实企业 PDF、企业检索原文标注 JSON、`node_modules/`、`.venv/`、构建产物与 TypeScript 缓存。目录 `data/real_cases/` 只保留公开来源说明与 manifest。快照内的 7 个 PDF 全部为项目合成演示/测试资料。

## 敏感与大文件检查

扫描按候选文件实际内容执行：疑似凭据 token、非空 `KEHENG_LLM_API_KEY`、个人 Windows/Unix 用户目录绝对路径、API Key CSV 均为 **0 项**；大于 2 MiB 的文件为 **0 个**；候选中没有本机 runtime、审计目录或真实企业资料。工作区中的 `默认业务空间-apiKey-6836969.csv` 类型为本地 API 凭据 CSV，被现有 `.gitignore` 规则命中且未复制；`data/real_cases/human_review_v10.csv` 类型为企业人工复核标签，已补充 ignore 规则并排除。

## 来源与许可

- 合成 PDF 的作者属性、使用边界见 `data/examples/README.md` 和 `data/DATA_SOURCES.md`；评测固定案例也明确为合成资料。
- 六家真实企业年报 PDF 不在候选内。来源 URL 和获取方式见 `data/DATA_SOURCES.md` 及公司目录 README。上游公开可访问不等于全文再分发授权。
- 当前 `LICENSE` 文件声明 MIT，本轮保留原文；维护者/贡献者版权授权范围尚不能仅由仓库文件证实。
- 前端直接依赖包元数据：React、React DOM、`@types/react`、`@types/react-dom`、`@vitejs/plugin-react` 与 Vite 为 MIT；TypeScript 为 Apache-2.0。未发现单独打包的第三方图片、字体或图标素材。
- Python 直接依赖：FastAPI MIT、python-multipart Apache-2.0、pydantic-settings MIT、Uvicorn BSD-3-Clause、ChromaDB Apache-2.0、scikit-learn BSD-3-Clause、PyYAML MIT。PyMuPDF 标注为 GNU AGPL v3 或 Artifex Commercial License 双许可，需维护者确认所选合规路径。锁文件含 88 个固定 distribution；完整传递依赖许可证/NOTICE 尚未整理。

## 发布阻碍

**发布前必须解决：**确认谁有权按现有 MIT 文件发布本项目；确认 PyMuPDF 的授权适用方式；对锁定的传递依赖完成许可证/NOTICE 核对；根据逐文件清单建立 Git 首个受控版本。仓库当前无可用 commit，`git status` 显示内容整体未跟踪，本轮未 stage/commit/push。

**可后续完善：**增加多平台 Python lock/hash 校验、自动生成完整依赖许可证清单，以及在人工证据确认后运行正式准确率评测。现有公开候选是本地验收产物，不代表已获得发布授权。
