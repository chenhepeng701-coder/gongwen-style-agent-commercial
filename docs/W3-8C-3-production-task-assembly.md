# W3-8C-3：任务组装接入正式全文写作链

类型：本轮开发与离线验收记录，不是新的项目规则。External Review 待进行。

## 结论与边界

```text
W3_8C_3 = PASS（本轮开发及离线验收）
BASE_SHA = a6d93a54ac1d417eb0f8ae23d92569677e8e4f40
BRANCH = codex/w3-8c-3-production-task-assembly
PRODUCTION_TASK_ASSEMBLY_CONNECTED = YES
STABLE_ASSETS_REUSED = YES
REALTIME_BRIEF_MODEL_REMOVED_FROM_NORMAL_PATH = YES
MATERIAL_BRIEF_CALLS = 0
BRIEF_HTTP_ATTEMPTS = 0
WRITER_NORMAL_CALLS = 1（fake Provider）
FALLBACK_TO_LIVE_BRIEF = NO
FALLBACK_TO_OLD_WRITER = NO
EVIDENCE_AUTHORIZATION_CHANGED = NO
FACT_POLICY_CHANGED = NO
RETRIEVAL_POLICY_CHANGED = NO
WRITER_RULES_CHANGED = NO
FLOW4_CHANGED = NO
FRONTEND_CHANGED = NO
DIRECTED_TESTS = 21 passed
FULL_BACKEND_TESTS = 379 passed
REAL_EMBEDDING_CALL = 0
REAL_BRIEF_CALL = 0
REAL_WRITER_CALL = 0
EXTERNAL_NETWORK_CALL = 0
REAL_USER_DATA_TOUCHED = NO
```

以上调用次数来自生产 API 路径的合成资料测试。真实 Writer 质量、黄金样本对比和真实页面验证未运行，不能据此宣称文章质量通过。前端默认 route 不变；真实模型验证留到开发及 External Review 通过后另行授权。

## 当前真源、推理闸与最小改法

开始时工作树干净、暂存为空，main 与 HEAD 均为上述 BASE_SHA。先创建本地 `codex/w3-8c-3-production-task-assembly` 分支，未提交、未推送、未创建 PR，main 引用保持原 SHA。未发现嵌套仓库或 gitlink。ignored data/uploads、缓存和旧运行输出未用作测试数据。读取 Git ignored 状态时部分既有 tmp 目录返回权限提示，未尝试访问其内容。

当前源码证据：

1. `generator.generate_commercial_flow` 根据显式 `generation_route=full_article_drafting` 调用 `generate_full_draft`；current/FLOW3-V3.3 分支无需修改。
2. 原 `prepare_full_draft_material` 直接构建 Fact Anchor 和 packet，未组装阅读视图；`generate_full_draft` 在 `brief_json` 为空时调用材料摘要模型。这是本轮解决的问题。
3. `task_assembly.assemble_task_from_assets` 已复用 Material Digest、授权检索视图和原 Atomic/Fact Anchor owner，可生成兼容 Writer 的确定性阅读视图；无需另造组装器。
4. W3-8C-2A 的正式落盘及 manifest 校验 owner 是 `library/material_ingestion.py`。现有 `document_readiness` 会失败清理并改写状态，`load_stable_material_asset` 会补建缓存，均不能作为本轮生产只读入口。
5. 入库完整切块与检索选中块的元数据形状不同。检索通过 `_chunk_result` 做现有元数据归一化，再通过 `_selected_chunk` 投影；直接用选中块缓存键寻找入库资产会失配。

因此只改生产接线、组装产物说明，并在入库 owner 中新增只读访问入口。不改持久资产 schema、规则指纹或生成方式，不改检索和事实授权。最大风险是读取错误来源、暗中重建、扩大授权或回退到模型摘要；用真实本地入库/检索投影、文件快照、失败哨兵、完整注册表对比和 API 调用计数验证。

文章质量影响尚未验证，不做提升或不下降的承诺。已验证的是工程链路和证据合同不变。用户可见变化限于显式全文路径：材料未就绪时在 Writer 之前失败关闭；成功路径直接调用一次 Writer。已有正文之后的检查、风险交付和失败兜底没有修改。未恢复任何旧模式。

## 实现

```text
现有 retrieve_documents(task, persist_trace=False)
→ read_selected_material_assets(retrieval)
→ assemble_task_from_assets(request, retrieval, assets)
→ 原 build_full_draft_prompt(packet)
→ 原 Writer 单次调用
→ 原 candidate / Fact Safety / Quality / FLOW4 / delivery
```

### 稳定资产只读访问

`read_selected_material_assets` 在现有文档生命周期锁内要求 READY，复用 `_validate_manifest` 校验源文件指纹、规则指纹、Embedding 文件校验和、完整资产清单。只读取被选中文档，不扫描全库。

选中资产进一步核验文件校验和、envelope 校验和、原缓存键、schema、来源及当前规则。来源比对复用实际 `_chunk_result` / `_selected_chunk`，只计算用于比对的来源投影，不重新检索、排序、选文档或更改选中记录。300/800、1 主 + 2 辅、辅助 2 chunks 等策略均由原检索 owner 决定。

`_SelectedMaterialAsset` 是内存中的只读投影绑定：内部保存原 `StableMaterialAsset` 和原缓存键，外部查询键适配检索记录的形状。它不生成碎片/阅读单元、不持久化、不改原 payload；每次 `source_for` 仍验证原资产。原检索记录的 text、角色、限制、顺序保持不变，再交给已有任务组装器。

缺失、损坏、过期、未 READY、来源失配通过 `FullArticleContractError` 进入现有公开错误映射；没有 Writer 正文时返回 400，`fallback_used=false`。不会调用 readiness 的修复/失效动作，不回退实时 Brief。源文件只被原指纹函数按字节读取以验证新鲜度，不重新解析文档、切块或 Embedding。

Material Digest / SourceSubstrate 对当前已授权、被截断文本的既有局部片段处理继续保留，以免从完整块引入未授权尾部限定；这不属于重新解析原文件或重新 Semantic Chunk。本轮未修改这两个 owner。

### 确定性阅读视图与诊断

仍使用原 `brief_json` 合同承载阅读视图，没有删除旧 Brief schema。overview 显式声明 `deterministic_task_assembly`，不是模型生成摘要。语义状态、限制、Evidence ID、source_scope 和绑定 Overlay 不变；任务组装不取得事实授权权力。

正式路径不再存在 `brief_prompt()` 调用、摘要 Provider 调用或摘要模型响应解析。缺少组装视图会在获取 Provider 前以 `FULL_DRAFT_TASK_ASSEMBLY_REQUIRED` 失败，不能触发旧链。

诊断保存 `TASK_ASSEMBLY.json`（带 `kind=deterministic_task_assembly`），并在 `call_accounting.json` 标记材料准备方式。正常路径不生成 `BRIEF_REQUEST.json`、`BRIEF_RAW_RESPONSE.txt`、`BRIEF_RESPONSE.json` 或 `MATERIAL_BRIEF_BOUND.json`。读取兼容 JSON 字段用于诊断不属于模型响应解析。

Writer 模型门槛、temperature、retry_limit=0、preserve_whitespace 和单次调用保持原样。Writer 后的生产代码未修改。

## 文件范围

修改：

- `backend/app/services/generation/full_draft_generation.py`
- `backend/app/services/generation/task_assembly.py`
- `backend/app/services/library/material_ingestion.py`
- `backend/tests/test_material_digest_task_assembly.py`
- `backend/tests/test_material_ingestion.py`
- `backend/tests/test_stable_material_assets.py`

新增：

- `backend/tests/test_production_task_assembly.py`
- 本文档 `docs/W3-8C-3-production-task-assembly.md`

三个既有测试仅更新历史阶段的文件冻结名单，允许本轮明确授权的接线文件变化；未改事实或质量预期。新增测试从本轮 BASE_SHA 逐文件保护其余 generation、retrieval、FLOW4、模型/Provider、generation router 及全部 frontend。

## 实际验证与失败记录

全部复用 `tests/run_w3_8c_2a_offline.py`：配置导入前隔离 runtime，拒绝真实资料目录访问、真实模型、真实 Embedding 和外部 socket；仅允许 Windows asyncio socketpair 的内部 IPC。

- 修改前完整后端：358 passed，1 个既有 Starlette/AnyIO 弃用 warning，47.77 秒。
- 第一轮专项：18 passed、1 failed。失败来自合成夹具：尾部标记被原语义切块分成另一个合法选中块。调整合成长度，使尾部标记实际位于同块第 800 字之外；保留“标记不进入 Writer”断言，并补充“存在于完整块、不存在于授权 text”的断言。未修改生产检索或授权逻辑。
- 最终专项：21 passed，1 个相同 warning，28.40 秒。
- 最终完整后端：379 passed，1 个相同 warning，72.22 秒；包含现有 Fact Safety、Atomic、FLOW4、资产、任务组装、入库生命周期和 Writing Reference 回归。
- `git diff --check`：通过。
- 每次运行输出的 external_network、real_embedding、real_model、real_user_data_access 均为 0。完整后台的 181 次 asyncio_socketpair_ipc 是本机测试事件循环内部通信。

新增专项覆盖真实生产 API 成功与失败分支：使用合成文件、真实本地入库生成资产和假向量；生成阶段封禁解析、Semantic Chunk、Embedding、资产构建/修复、Brief、旧 FLOW3 入口；保留实际检索聚合选块、Task Assembly、Writer Prompt 和下游检查交付。只有底层检索结果传输及 Writer 输出使用 fake。

成功路径验证 Writer 只调用一次，所有合法 Evidence ID 与完整注册表一致，unavailable ID 和授权外尾部不进入 Prompt，Fact Anchor/semantic_state/limitations/source_scope 逐字段不变；验证 2 辅助文档、辅助 chunk 上限、300/800 上限；原绑定 Overlay 由禁止重算哨兵保护。候选、事实报告、质量报告实际落盘，无 checks_unavailable，原文与最终交付字节一致。另测 Writer 异常时不重试、不回退。

失败覆盖：manifest/asset 缺失或损坏、envelope 校验和、资产身份/schema、源文件变化、规则过期、Embedding 改动、未 READY、检索文档/chunk/原文/metadata 失配、无选中资产、缺失确定性阅读视图。检查失败前后资料库文件字节和 mtime 不变，未调用 Writer。

工作目录为 `D:\AI_Projects\gongwen-style-agent-commercial\backend`。实际命令：

```powershell
$env:PYTHONPATH = 'D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'
$env:PYTHONIOENCODING = 'utf-8'
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tests/run_w3_8c_2a_offline.py -q tests --basetemp tmp/w3-8c-3/baseline-01 --junitxml tmp/w3-8c-3/baseline.xml *> tmp/w3-8c-3/baseline.log
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tests/run_w3_8c_2a_offline.py -q tests/test_production_task_assembly.py --basetemp tmp/w3-8c-3/directed-01 --junitxml tmp/w3-8c-3/directed-01.xml *> tmp/w3-8c-3/directed-01.log
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tests/run_w3_8c_2a_offline.py -q tests/test_production_task_assembly.py --basetemp tmp/w3-8c-3/directed-02 --junitxml tmp/w3-8c-3/directed-02.xml *> tmp/w3-8c-3/directed-02.log
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tests/run_w3_8c_2a_offline.py -q tests --basetemp tmp/w3-8c-3/full-01 --junitxml tmp/w3-8c-3/full-01.xml *> tmp/w3-8c-3/full-01.log
```

复跑请使用新的 basetemp，避免 pytest 清理已有证据。日志、XML 和本地快照均在 ignored tmp 中，不纳入提交。

## Git、回退与交接

修改前六个既有文件已逐字节复制到 `backend/tmp/w3-8c-3/checkpoint/`。最终 HEAD 与 main 均为 BASE_SHA；暂存为空，六个跟踪文件修改、两个新文件待评审。遵循本轮用户指令，不创建 commit；没有 commit message 或已暂存文件。无依赖安装，无数据迁移，无服务启动。

下一步仅 External Review。真实 Writer/真实资料验证和前端默认入口切换均未授权。若评审要求改事实安全、检索架构、Writer 规则、FLOW4 或处理真实资料，应停止并报告。

需要回退时先确认没有叠加新编辑，再在项目根目录执行以下精确文件回退；本轮未执行回退：

```powershell
git restore --source=a6d93a54ac1d417eb0f8ae23d92569677e8e4f40 --worktree -- backend/app/services/generation/full_draft_generation.py backend/app/services/generation/task_assembly.py backend/app/services/library/material_ingestion.py backend/tests/test_material_digest_task_assembly.py backend/tests/test_material_ingestion.py backend/tests/test_stable_material_assets.py
Remove-Item -LiteralPath 'D:\AI_Projects\gongwen-style-agent-commercial\backend\tests\test_production_task_assembly.py','D:\AI_Projects\gongwen-style-agent-commercial\docs\W3-8C-3-production-task-assembly.md'
git status --short
```

大白话：全文新链已改为读取事先处理好的材料并确定性组装，然后只让 Writer 写一次；材料资产有问题就明确停止，不能临时再找模型做摘要。旧入口、检索、事实安全、Writer 规则和前端都未改变。
