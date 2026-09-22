# W3-8C-2A 上传材料处理与就绪状态

文件类型：本轮实现说明与验收记录；不替代 AGENTS.md。

生命周期行为已由 [W3-8C-2A-R1](W3-8C-2A-R1-ingestion-lifecycle-repair.md) 修复。下方原轮测试数量是历史记录；当前修复验收以 R1 报告为准。

## 边界与基线

- BASE_SHA：`90b5a2141580610e9c60e1805db7a034affb57b5`。
- 分支：`codex/w3-8c-2a-ingestion-readiness`；从精确基线创建，创建时工作区干净。
- 不 commit、不 push、不创建 PR、不修改 main。基线 SHA 本身作为本轮回退参照。
- 唯一获准网络操作为同步基线的 `git fetch origin main`；开发测试禁止外部网络与真实模型调用。
- 不读取或处理真实资料库，不修改生成、Writer、Brief、事实语义、检索规则及前端。

## 当前证据与推理闸

原上传路由在 `index_file` 后返回；原 `generate_embeddings` 自行解析、语义切块，并通过 `zip(chunks, vectors)` 写入，缺少数量一致性校验。W3-8C-1 的稳定资产 owner 已支持完整 chunk 的确定性缓存，但没有资料库持久化目录或上传接线。这些是当前源码事实，不是历史报告推断。

本轮问题是材料处理和完整性状态缺少闭环。新概念由 library 的 `material_ingestion.py` 唯一负责；上传/重试路由调用它，文档查询消费状态。语义切块算法仍归原 semantic chunker 所有，资产语义仍归原 stable_material_assets 所有；没有新增同职责的解析或事实规则。

最小改法：建立共享 prepared chunks 合同；给现有 Embedding 增加该输入和硬校验；新增文档资产清单与后台编排；接上传、状态查询、单文档重试、删除。最大回归风险是分块漂移、部分结果被误标 READY、并发删除后复活、旧 JSON 读取失败。这些由合成数据、fake Embedding 和生命周期测试验证。

用户可见变化仅为上传自动处理、状态字段和重试入口。文章质量不属于本轮验收；不会由材料 READY 推导成稿质量通过。事实准入、任务权限及旧模式没有变化。后台机制采用项目已有 FastAPI/Starlette；单进程适用范围与中断恢复限制见后文。

## Owner 与数据合同

- `library/material_ingestion.py`：调度、去重、阶段状态、清单验证、重试及删除。
- `library/semantic_preparation.py`：一次解析/语义切块的不可变 JSON 合同；消费者各自获得相同内容的独立副本。
- `library/embedder.py`：消费完整 prepared chunks；旧单文档/batch Embedding 接口仍可用。
- `generation/stable_material_assets.py`：原有稳定资产构建、校验及复用，源码未修改。
- `library/indexer.py`：文档索引更新、共享文档生命周期锁。

新增 `LibraryDocument` 字段：

| 字段 | 含义 |
| --- | --- |
| ingestion_status | INDEXED / PROCESSING / READY / FAILED；旧 JSON 默认 INDEXED |
| ingestion_stage | queued / semantic_chunk / embedding / stable_assets / validation / complete 等阶段 |
| ingestion_error | 公开 error_type/message，不返回原始模型错误或材料文本 |
| ingestion_lease_expires_at | R1 新增：UTC epoch 秒；等待启动租约为 300 秒，终态清零；旧 JSON 默认 0 |
| processed_at | 成功处理时间（UTC ISO 格式）；失败为空 |

`has_embedding` 仅描述向量化，不能代表 READY。新处理失败时清除此标记及向量文件、manifest。旧 Embedding 入口在请求向量前取消未启动的处理 token、清理 manifest，并使状态失效；成功后为 INDEXED，必须显式 process 完成资产处理才可 READY。

## API 与后台执行

- `POST /library/upload`：保存并建索引，落盘 PROCESSING 后返回；同步 worker 由 BackgroundTasks 在响应发送后在线程池执行。
- `POST /library/documents/{doc_id}/process`：202，返回 PROCESSING 文档；FAILED 或 READY 均可重处理。活动任务去重，不自动重试任何外部调用。
- 原文档列表/详情接口返回新增状态。READY 查询执行只读产物校验；失效时将状态降为 FAILED，供显式重试。
- 未知文档重试为 404。旧向量化/删除占用同一文档时，重试返回结构化 409 MATERIAL_PROCESSING_BUSY，避免长时间等锁。
- 删除通过同一文档锁与处理串行化，取消尚未开始的后台任务，并清理向量、旧卡片/风格文件及整份文档资产目录，最后移除索引。原始上传文件保持旧删除语义。

新上传链不会调用 scene classification、material cards、style cards。旧接口仍保留。

## READY 与持久化

目录：`data/library/material_assets/{document_id}/`。

文档级 `manifest.json` 包含：version、document_id、chunk_count、stable_asset_cache_keys、stable_asset_version、source_fingerprint、rule_fingerprint、processed_at、embedding_sha256，以及每个 chunk_id/cache_key/sha256。

READY 提交前必须满足：索引仍存在；切块非空；向量数量与完整 chunks 相等；每个向量非空、维度一致且数值有限；所有完整 chunks 均有可校验稳定资产；清单覆盖无遗漏；源文件/索引身份/规则版本未变化；向量文件、资产文件、chunk ID 和全文逐项一致。清单和索引采用原子文件替换；索引 READY 是最后一步。

模型每个 batch 以及最终汇总都检查数量，防止不同 batch 的数量错误相互抵消。多/少向量不会写出新的残缺向量文件。

资产全部基于完整 `chunk_text`。没有任务 title、primary/auxiliary、rank、score、selection_reason、Evidence ID、section assignment 或 300/800 授权截断。重复处理使用相同源/规则绑定缓存 key，缓存内容与文件时间不变；本次处理时间和向量文件可重新生成。

部分失败可能保留已完成缓存，但文档为 FAILED，它们不能证明完成。重试复用完整缓存；损坏的当前源缓存只在该文档目录内进行本地重建，不重试外部请求。删除清理整个文档目录，不留下该文档缓存或清单。

## 验证方式与历史测试调整

所有测试使用合成文本、隔离的 `backend/tmp/w3-8c-2a/` 和 fake Embedding。离线 runner 在导入配置前改写 runtime 根目录；拦截真实 data/uploads/output/.env 的读取和外部 socket；真实 Provider 与 Embedding API 默认是失败哨兵。仅允许 Python 在 Windows 上创建 asyncio socketpair 所需的进程内回环 IPC，不允许任意 localhost 请求。

本轮测试覆盖上传响应时序、共享切块、数量错误、资产中途失败、幂等复用、损坏修复、源/规则变化、中断恢复、旧数据读取、旧 embed 路由、重复任务合并、并发删除和慢任务隔离。新链额外设置 Writer/Brief/scene/card/style 失败哨兵。

W3-8C-1 的历史测试曾冻结整个 library router。本轮明确授权修改该路由，因此移除此单一历史边界，保留生成/事实/检索的原字节比较；本轮增加对整个 generation/retrieval、旧提取器和 frontend 的基线字节比较。没有改变事实验收预期，没有隐藏失败。

原轮验收记录：当时报告 `W3_8C_2A = PASS`，下列测试尚未覆盖 External Review 后确认的生命周期缺陷；修复及补测结果见 R1 报告。

- 完整 backend：**345 passed，1 warning，40.32 秒**。
- 其中定向范围：本轮 **26** 项、W3-8A-P **31** 项、W3-8B **44** 项、W3-8C-1 **71** 项，合计 **172** 项，均通过。
- 开发测试审计：external_network=0、real_embedding=0、real_model=0、real_user_data_access=0；本轮 fixture 的 Writer/Brief/scene/card/style 哨兵均为 0。
- 唯一警告：Starlette TestClient 引用 AnyIO 已弃用别名；未升级依赖。
- 11 个变更/新增文件实际 UTF-8 解码无替换字符；10 个 Python 文件 AST 解析通过；`git diff --check` 通过。
- 初轮 ASGI 时序夹具漏传 `scope.type`，导致 1 failed、2 teardown errors；修正夹具后重跑通过。未调整业务预期或跳过失败用例。
- 真实模型、真实资料库、真实页面与文章生成未运行，均按工单边界排除。

最终命令（工作目录 `backend`，使用已安装依赖，没有安装新包）：

```powershell
$env:PYTHONPATH = 'D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tests/run_w3_8c_2a_offline.py -q tests --basetemp tmp/w3-8c-2a/full-02 --junitxml tmp/w3-8c-2a/final-results.xml 2>&1 | Tee-Object -FilePath tmp/w3-8c-2a/final-results.log
```

复跑时必须为 `--basetemp` 指定新的目录，避免 pytest 清理已有测试目录。日志与 XML 均在 Git ignored 的测试目录内，不暂存。

结果文件：`backend/tmp/w3-8c-2a/final-results.xml`、`backend/tmp/w3-8c-2a/final-results.log`。

最终 Git：HEAD 仍为 BASE_SHA，6 个跟踪文件修改、5 个新增文件，暂存为空；无新 commit、无 push、无 PR。本地 main 保持 `87711c3c63e5ab280b31c4eaacfd43a7f2914ee7`，仅 origin/main 经授权 fetch 更新至 BASE_SHA。

## 运行限制与回退

后台任务、去重表与生命周期锁是进程内机制，适用于当前本地单进程服务，不是持久化队列。R1 为等待启动的任务增加 300 秒租约；查询、重试和后台启动都会检查过期登记，即使旧 token 留在内存也可撤销并转 FAILED。实际运行的 worker 持有文档锁并登记运行 token，各阶段刷新租约；正常慢任务不因等待启动租约到期而被抢占。进程中断后不会自动扫描真实库或重发 Embedding；遗留 PROCESSING 在查询或重试时转 FAILED。多 worker 部署不在本轮范围。

READY 查询需要读取当前源文件与产物并计算指纹；大库查询成本可后续单独衡量。本轮没有引入新依赖或队列。原上传文件的保留遵循既有删除语义，不擅自扩展为删除用户原文。

回退方式（本轮未执行）：停止服务后，核对本轮文件清单，只恢复六个已跟踪修改文件至 BASE_SHA；仅删除本轮五个新增代码/测试/文档文件。不得清理真实 data/uploads 或其他用户改动。本轮没有提交，暂存区应保持为空。

只在确认这些文件没有叠加后续工作后，从项目根目录执行以下回退命令：

```powershell
git restore --source=90b5a2141580610e9c60e1805db7a034affb57b5 --worktree -- backend/app/models/library.py backend/app/routers/library.py backend/app/services/library/embedder.py backend/app/services/library/indexer.py backend/app/utils/runtime_paths.py backend/tests/test_stable_material_assets.py
Remove-Item -LiteralPath 'backend/app/services/library/material_ingestion.py','backend/app/services/library/semantic_preparation.py','backend/tests/run_w3_8c_2a_offline.py','backend/tests/test_material_ingestion.py','docs/W3-8C-2A-ingestion-readiness.md'
git status --short
```
