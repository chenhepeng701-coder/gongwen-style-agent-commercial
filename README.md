# 公文写作助手商业版

本仓库是从实际生产工作区剥离出的干净生产基线。仓库只包含运行代码、前后端依赖清单、必要规则资产、哈希锁定的第三方上游片段以及脱敏合成测试夹具。

不随仓库分发：用户资料、知识库/向量索引、API Key、私有 Writing Reference 包、模型原始响应、生成稿、日志和运行时输出。

## 环境要求

- Windows 10/11
- Python 3.12
- Node.js 22 与 npm 10（锁文件安装）

## 后端安装

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` 是本机环境，已被 Git 忽略，不得提交。

## 前端安装

```powershell
cd frontend
npm ci
```

`node_modules` 和构建产物已被 Git 忽略，不得提交。

## 环境变量

复制仓库根目录的 `.env.example` 为根目录 `.env` 或 `backend/.env`，只在本机填写：

- `GONGWEN_LLM_API_KEY`：当前聊天模型供应商密钥；
- `GONGWEN_EMBEDDING_API_KEY`：Embedding 供应商密钥；
- `WRITING_REFERENCE_PACK_PATH`：私有 Writing Reference JSON 的本机绝对路径；
- `QUALITY_TEST_MODE`：可选，默认关闭；
- `ENABLE_WRITING_STYLE_LIBRARY`：可选，默认关闭。

`.env`、API Key 和本机绝对私有路径不得提交。

## 私有 Writing Reference

`full_article_drafting` 正式路径要求管理员显式配置 `WRITING_REFERENCE_PACK_PATH`。私有包必须放在源码目录之外，不得复制到仓库。未配置、路径非法、文件不可读或 schema 不合法时保持 fail-closed，在检索和模型调用前明确失败。

仓库只保留 loader/schema、配置入口、合成 fixture 和离线合同测试，不包含真实 S02/S05 摘录或原 `reference_outbound.json`。

## 启动后端

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

首次启动会创建被忽略的 `backend/data`、`backend/uploads` 和 `backend/output` 运行目录。

## 启动前端

```powershell
cd frontend
npm run dev
```

默认 Vite 地址为 `http://127.0.0.1:5177`；后端默认端口为 8000。

## 离线测试

以下测试使用合成数据，不调用外部模型或 API：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_writing_reference_pack_02b1.py tests/test_flow3_v3_atomic_evidence_generation.py tests/test_fact_safety_delivery_policy_reset.py tests/test_flow4_dev002_integrity_gate.py tests/test_flow4_dev003_renderer.py tests/test_flow4_dev004_artifact_delivery.py tests/test_m2m3_writing_reference_dev21.py
```

前端生产构建：

```powershell
cd frontend
npm ci
npm run build
```

## 数据边界

- 用户上传资料和知识库只存在本机运行目录，不随仓库分发；
- Embedding 与向量运行数据不入库；
- 私有参考包及其真实内容不入库；
- 日志、RAW SSE、模型原始响应、生成稿、临时 DOCX/TXT 不入库；
- 仓库不包含旧 Git 历史，不配置远程仓库；
- 第三方固定上游仅保留生产实际读取且经 SHA256 锁定的最小文件，其许可证随文件保留。

`BASELINE_MANIFEST.json` 记录纳入文件来源与哈希；`EXCLUDED_MANIFEST.json` 记录主要排除路径族。
