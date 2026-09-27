# GENRE-UI-SCENE-ROUTING-INTEGRATION

文件类型：本次集成的实施与验收记录，不是项目规则真源。项目规则以根目录 `AGENTS.md` 为准。

当前状态：原集成的离线记录保留如下；后续审查发现的服务端 Scene 合同缺口及修正见文末 `SCENE-CONTRACT-01`。本轮不进行真实文章商业验收，不提升其他 21 个文种的商业成熟度。代码保留在工作区，按要求未暂存、未提交、未推送。

## 基线与边界

- TASK：`GENRE-UI-SCENE-ROUTING-INTEGRATION`。
- 精确 BASE / 起始 HEAD：`d9b6e68d698e9d7b97dc92f23f8feffd80360c5a`。
- 分支：`codex/genre-ui-scene-routing-integration`。
- 首次核验时所需对象不在本地；用户在外部 PowerShell 同步并建立目标分支后，Agent 重新核验 HEAD 与本地 main 均为精确 BASE。Agent 未 fetch、未重新创建分支。
- 起始 tracked / staged 修改均为零，原有 11 个用户未跟踪文件保留。
- 本工单明确禁止 `git add / commit / push / PR / merge`，覆盖一般提交要求。精确 BASE 是回滚参照，不创建 checkpoint commit。
- 不调用真实 Writer、Embedding 或场景分类模型，不使用真实资料库，不增加依赖、数据库结构、生成路线或“补充事实”。

## 当前源码审计与推理闸

正式链路由当前源码确认：`/api/generation/generate` → `ArticleGenRequest` → `generate_commercial_flow` → `generate_full_draft` → `CommercialGenreRuntime` → Retrieval → Stable Assets → Task Assembly → Writer → canonical Fact Safety → FLOW4 / Must-Deliver。前端必须提交已经获准的正式整篇路由，后端继续独立准入校验。

发现的集成缺口：

1. `ArticleGenPage.jsx` 的 14 项 `DOCUMENT_TYPE_OPTIONS` 同时包含真实文种、场景材料和“其他”；`WRITING_SCENE_OPTIONS` 又从该列表截取，未复用 taxonomy。
2. `batchTaskParams.js` 根据“工作总结”名称和 count 判定 `full_article_drafting`，其他情况静默走 `current`。
3. 页面把 scene 放在 shared 参数，但 payload helper 只接受 task 自身的 scene；批量行未持有独立 scene。草稿、历史、Excel 与页面之间因而没有闭合。
4. `document_retriever.py::prepare_retrieval_request` 明确排除 scene。正式归一化任务保存的是既有 `scene_compat`，直接读取 `task.scene` 会漏掉正式调用链。
5. `task_assembly.py::assemble_task` 已把 `request.scene` 放入 `FullDraftMaterialPacket.task_json`，Writer 已消费该 JSON。此入口应复用并加贯通验证，不建立 Scene Prompt 系统。
6. 成功生成日志原先取结果的 scene；必须核查手选 scene 的保存与历史重跑，不能只验证 HTTP 输入。

| 推理闸 | 结论 |
| --- | --- |
| 实际问题与证据 | UI 文种真源、生产资格、行级场景和检索信号未接通；以上均为当前源码证据 |
| 已验证 / 未验证 | 当前分支、硬编码、Scene taxonomy、Writer JSON 入口已验证；实际模型文章质量不在本轮验证范围 |
| 概念创建者 | Registry 创建文种身份与能力；既有 Scene taxonomy 创建可选场景；用户选择任务上下文 |
| 调用者 / 消费者 | 页面与批量工具提交；Request/Gate 校验；Retrieval 与 Task Assembly/Writer 消费 |
| 唯一 owner / 同职责实现 | Registry、Scene taxonomy、既有检索准备函数、Task Assembly 已存在；不重复建设 |
| 最小改法 | 只读目录、能力驱动 UI、兼容归一化、行级 scene、辅助 query 接入与原 JSON 链路验证 |
| 最大风险 / 阻断验证 | 非生产文种越权、scene 污染事实或排名、空 scene 改变总结行为；以真实 owner 的离线合同、精确 BASE 比较及负例阻断 |
| 文章质量 / 事实安全 | 不提升质量评级、不修改事实授权；仅为手选 scene 提供任务与检索上下文 |
| 用户可见行为 | 显示所有注册文种及可用状态，增加场景选择，明确阻断未验收文种和不支持的篇数 |
| 旧模式 | 不恢复废弃 article_mode，不让新生产请求静默 fallback 到 current |

## 字段职责与 owner

| 字段 | 职责 |
| --- | --- |
| title | 任务主题、首要检索语义、Writer 任务 |
| document_type | Registry 文种身份、Adapter/固定规则、检索限定 |
| business_domain | 业务领域检索限定与 Writer 上下文 |
| level / org_type / writing_purpose | 辅助检索上下文与 Writer 上下文 |
| scene | 用户明确选择的辅助 query 信号与 Writer 上下文；不是文种或事实 |
| reference_scope | 资料范围；不作为文章事实或语义检索词 |
| style / 补充要求 | Writer 写作要求；不参与 RAG 语义检索、不成为 Evidence |
| target_words | 现有篇幅合同；不参与语义检索 |

文种选项唯一来源为后端 Registry，场景选项唯一来源为既有 `/api/retrieval/scene/taxonomy`。Commercial Gate 仍是生产资格权威；目录只读展示其判断，客户端任何 flag 均不授予资格。

## 只读 Catalog 与生产路由

新增 `GET /api/generation/genres`。`GenreRegistry.all()` 返回按注册顺序去重的不可变 Adapter 元组；`genres/catalog.py` 从该 Registry 派生响应，逐项调用既有 `resolve_genre(..., purpose="production")`，该调用仍受原 Commercial Gate 约束。

响应包含 `genres` 与 `legacy_mappings`。每个文种只暴露 `genre_id`、`document_type`、`display_name`、`aliases`、`capability_status`、`production_ready`、`commercial_maturity`、`export_formats`、`generation_route`。不暴露文件路径、Prompt、Evidence 或可变配置接口。未满足门禁者 route 为 null；客户端宣称 ready 或 maturity 不会改变后端门禁。缺失 legacy 目标的隔离 Registry 仍可正常返回自己的文种，不凭兼容表补造文种。

当前真实目录为 22 项：工作总结仍为 `PRODUCTION_ENABLED`；其他 21 项为 `ENGINEERING_READY`，`production_ready=false`。界面显示“已可生产”或“已接入，待商业验收”；普通用户不看到内部成熟度枚举。非生产选项可见但禁用，目录获取失败时禁用生成并提供重新加载入口，不恢复旧静态列表。

`genreCatalog.js` 只消费接口；`buildTaskGenerationPayload` 按该接口能力选择 `full_article_drafting`，页面预检与 payload 构建均拒绝不支持的文种、导出格式和 count。`count > 1` 不拆成隐式请求，不降级 current；提示拆分为独立任务，每项 count=1。未来后端真正提升其他文种后，现有 UI 无需按文种名称加分支。

既有 `current` API 合同没有删除或重设计；本次生产 UI 不再选择或回退该路线。正式整篇请求继续由 `ArticleGenRequest` 与 `CommercialGenreRuntime` 独立复验。

## 场景、历史与 Excel

场景读取既有 `GET /api/retrieval/scene/taxonomy` 的 `items[].name`（当前 16 项）；没有复制 taxonomy，没有调用其 LLM 分类入口。“自动 / 未指定”发送 null。单篇绑定首行 scene，批量默认 scene 只在新建或显式应用默认值时复制到行，生成时始终读取各行 scene。

草稿与历史恢复在目录到达前后都保持任务元数据；目录延迟返回与保存默认设置延迟返回不能覆盖用户已经编辑或恢复的任务。合法历史场景继续展示；旧日志中“scene 等于旧文种且不是 taxonomy 场景”的文种回显，在历史恢复边界按自动处理，避免空场景总结重跑改变 Prompt。党课讲稿等 taxonomy 中的同名场景不被清除。

原 Excel 实现只有导入，没有任务模板导出链路。本轮保持原 9 列位置兼容，增加“写作场景”/scene 表头；每行显式场景和显式自动值独立保留。未知场景给出导入错误；未知/歧义旧文种保留展示，实际生成前要求重新选择。没有为满足措辞另增 Excel 导出功能。

成功生成时，HTTP 保存边界用规范化后的 `req.scene` 更新结果与历史快照的 scene 元数据，空值统一保存为 `""`，不再继承旧结果中的文种值。此处由下述 `SCENE-CONTRACT-01` 修正；不修改正文、正文 hash、FACT、Delivery 结论或日志数据库 schema。

## 旧值兼容规则

兼容规则在后端 `genres/catalog.py`，前端只消费；它不是第二份文种目录。所有目标必须由 Registry 解析，附加场景必须仍存在于既有 taxonomy。

| 旧值 | canonical 文种 | 补充场景（已有明确场景优先） |
| --- | --- | --- |
| 领导讲话稿 | 讲话稿 | 无 |
| 工作汇报 | 汇报材料 | 无 |
| 实施方案 | 工作方案 | 无 |
| 党课讲稿 | 讲话稿 | 党课讲稿 |
| 民主生活会对照检查材料 | 讲话稿 | 民主生活会对照检查 |
| 组织生活会个人发言材料 | 讲话稿 | 组织生活会 |

Registry 本身已有的别名直接解析。对旧列表中的工作报告、工作计划、预案、述职报告、调研报告、心得体会 / 研讨发言、其他，本轮不建立推测映射；保留显示，生成前选择 canonical 文种。固定上游 `work-plan` 别名只有工作方案、实施方案、方案，法定 `report` 只有报告，故不把工作计划或工作报告凭名称相近归入它们。

## Scene → Retrieval / Writer 与事实边界

检索生产修改仅在 `_field_payload` 与 `prepare_retrieval_request`：读取显式 scene 或已有 `OfficialDraftTask.scene_compat`，过滤自动哨兵；旧的“仅 scene 被兼容解释成 document_type”不再额外注入场景。非空手选 scene 加入辅助 query 行和 semantic_fields，支持向量候选发现。没有新增 P1 score 分量或系数；相同候选集的排名、主辅选取和 selected chunks 保持相同。空 scene 的完整检索响应（除原本变化的时间戳）及 search 调用参数与精确 BASE 相等。

`style` 始终排除于 query_fields 与语义检索；target_words 和 reference_scope 保持原角色。Scene 不进入 Evidence 准入、semantic state、source_scope 或 FACT 授权。Task Assembly、Material Packet、固定上游 System Prompt、Host Boundary、Writer 调用策略均未修改；原 task JSON 中的 scene 被 Writer 原样消费。相同合法素材下只改 scene，Evidence Registry、写作证据视图、限制和 unavailable 列表均不变。

空 scene 的完整 Prompt 字典与精确 BASE 逐值一致。额外执行安全正文与有风险正文两条合成正式路线，比较同一 BASE 下的 Prompt、单次 Writer 策略、FACT、Quality、正文身份、TXT/DOCX 与 FLOW4；这只证明工程等价，不是真实 Writer 或真实文章质量验收。

## 保护 owner 与 Stable Asset

本轮没有修改 P1 排名函数/权重、P2、P3-A、P3-B、Atomic classifier、canonical FACT、Must-Deliver、Commercial Gate、Commercial Runtime、Task Assembly、固定工作总结 Prompt、Vendor snapshot、Stable Asset 指纹或 parser/ingestion owner。因此不需要 Stable Asset 重处理。

历史测试中把整个 Registry、检索文件、HTTP handler 和前端文件按字节冻结的 guard，允许本轮明确授权的集成改动后继续执行。原历史 BASE、hash 和保护名单全部保持；新增本轮独立精确 hash，额外比较完整模块 AST：Registry 仅可新增 all()，检索仅可改变两处 query preparation，HTTP 仅可新增 GET 和明确 scene 元数据保存。其余节点和所有评分常量保持一致。重新封存 hash 也不能绕过 AST 保护；未来单字节修改与 Vendor 原始字节/换行改变有拒绝负例。

## 测试执行与中间失败

全部后端测试通过 `tests/run_genre_ui_scene_offline.py` 复用原 `run_multi_genre_offline.py` 的防护，不修改真实文件/网络/模型/Embedding guards。隔离目录为根目录 ignored `tmp/genre-ui-scene-routing-integration/`；启动时要求原 11 文件保护清单。原文件只做完整性 hash 验证，不解析或打印内容。

已经实际运行的主要检查：

| 检查 | 结果 |
| --- | --- |
| 修改前前端全套 | 66 passed，0 failed |
| 首轮后端整合回归 | 1400 passed，21 failed，2 skipped；196.19s；受限调用均 0。运行期间生产改动落盘，不能称为纯修改前基线 |
| 后端新接口/Scene 集成专项首轮 | 36 passed；3.66s；受限调用均 0 |
| 精确 BASE 完整正式链路新增专项 | 2 passed，36 deselected（仅本次定向命令）；5.49s；受限调用均 0 |
| 实际 API → 前端 helper / React 渲染跨栈检查 | 1 passed；1.71s；真实目录 22、可生产 1，taxonomy 16 |
| 历史保护链第二轮 | 243 passed；45.20s；受限调用均 0 |
| 最终前端专项 | 43 passed，0 failed，0 skipped；715.81ms |
| 最终前端完整测试 | 86 passed，0 failed，0 skipped；1179.19ms |
| 最终前端生产构建 | PASS；Vite 5.4.21，121 modules，4.12s |
| 最终后端 28 文件专项 | 1314 passed，2 existing skips，0 failed；217.00s；受限调用均 0 |
| 最终完整后端离线套件 | 1545 passed，2 existing skips，0 failed；255.41s；无 deselect；受限调用均 0 |
| 最终工作区完整性检查 | 11 个原文件 SHA-256 全部不变；staged 空；31 个任务文件 UTF-8 / Python AST 通过；未发现嵌套源码仓库 |
| 差异格式检查 | `git diff --check` 通过 |

首轮 21 项失败涉及历史整文件或检索准备函数断言，以及篡改负例被无关的新接口改动提前截断；修复是明确本轮授权 seam、保留原保护真相和负例，不改事实标准。保护链首次修复回归 242 项通过、1 项因 Git 对中文路径默认引用而失败；已改用 `git ls-tree -z` 并对 Vendor 使用 raw bytes 比较。

后续扩大保护测试发现已合并基线的测试缺口：历史测试枚举当前 `git ls-files`，但 PR #18 新增的 `upstream_adapters.py`、`upstream_genre_contracts.py`、`upstream_genre_quality.py`、`upstream_suite.py` 不存在于其更早历史 BASE，导致 `git show` exit 128。已从精确 d9b6e68 的测试源码与失败日志确认。修复对这四个文件与本次精确 merge BASE 做只读完整内容比较，既不迁移历史 BASE，也不允许改动四个 owner。另增加模拟未来新文件被 Git 跟踪的负例/贯通测试，无需实际暂存。前端最终修改完成前出现的旧 hash 失败，已由前端定稿后的精确 hash 消除。

一次定向命令误写不存在的测试文件名 `test_atomic_semantic_classification.py`，exit 4、零测试；已修正为实际文件列表并完整重跑，不记为通过。最终验证没有临时 deselect 或新增 skip。

前端专项使用现有 Node 测试、React server rendering 与已有 esbuild；跨栈测试通过进程内 FastAPI TestClient 取得真实 API JSON，再由前端真实 helper 和 select 组件消费，不维护测试专用 22 文种表。不安装任何依赖。

后端仅有的两个跳过点是既有的真实私有 Run 重放测试：`test_p3a_semantic_state_recognition.py:278` 要求显式 `--real-run`；`test_p3b_claim_evidence_boundary.py:355` 要求真实 Run 路径。本工单禁止该数据访问，未提供授权或路径，未增加任何永久 skip。既有离线 P3-A/P3-B 其余测试和保护负例均实际执行。

### 可复制命令与证据位置

本机 backend `.venv` 的 Python 启动器不可用；实际复用现有 bundled Python 与 `.venv/Lib/site-packages`，没有安装或升级依赖。后端使用以下命令，路径均限定当前项目：

```powershell
Set-Location -LiteralPath 'D:\AI_Projects\gongwen-style-agent-commercial\backend'
$env:PYTHONPATH='D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
$taskPython='C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$taskTests = @(
  'tests/test_genre_ui_scene_integration.py'
  'tests/test_genre_ui_cross_stack.py'
  'tests/test_genre_ui_scene_scope.py'
  'tests/test_commercial_genre_infrastructure.py'
  'tests/test_commercial_genre_parity.py'
  'tests/test_commercial_genre_scope.py'
  'tests/test_upstream_suite_contracts.py'
  'tests/test_upstream_suite_flows.py'
  'tests/test_upstream_suite_integrity.py'
  'tests/test_multi_genre_foundation.py'
  'tests/test_multi_genre_drafting_quality.py'
  'tests/test_multi_genre_retrieval.py'
  'tests/test_multi_genre_scope.py'
  'tests/test_topic_first_document_ranking.py'
  'tests/test_auxiliary_evidence_admission.py'
  'tests/test_p3a_semantic_state_recognition.py'
  'tests/test_p3b_claim_evidence_boundary.py'
  'tests/test_production_task_assembly.py'
  'tests/test_material_digest_task_assembly.py'
  'tests/test_writer_material_view.py'
  'tests/test_flow3_v3_atomic_evidence_generation.py'
  'tests/test_fact_safety_delivery_policy_reset.py'
  'tests/test_flow4_dev002_integrity_gate.py'
  'tests/test_flow4_dev003_renderer.py'
  'tests/test_flow4_dev004_artifact_delivery.py'
  'tests/test_stable_material_assets.py'
  'tests/test_material_ingestion.py'
  'tests/test_rag_observability.py'
)
& $taskPython -B tests/run_genre_ui_scene_offline.py -q -rs @taskTests
& $taskPython -B tests/run_genre_ui_scene_offline.py -q -rs tests
```

运行器要求本次已经创建并验证的 `tmp/genre-ui-scene-routing-integration/protected-user-files.json`；原用户文件另由前后 hash 清单核对。不得为跳过防护而直接使用裸 pytest。Windows asyncio 的本地 `socketpair` IPC 与外部网络调用分开计数。

实际证据保留在 ignored `tmp/genre-ui-scene-routing-integration/`：`backend-baseline.log`、`frontend-targeted.log`、`frontend-final.log`、`frontend-build.log`、`backend-targeted-final.log`、`backend-full-final.log`、`final-scope-audit.json`、`protected-user-hashes.before.json`、`protected-user-hashes.after.json`、`test-commands.txt`。前端修改前的 66 项结果保留于本次工具输出，没有另存为日志文件。这些本地运行产物不进入 Git。没有保存真实模型响应或用户资料内容。

前端实际执行命令（退出码均为 0）：

```powershell
Set-Location -LiteralPath 'D:\AI_Projects\gongwen-style-agent-commercial\frontend'
node --test tests/utils/genreCatalog.test.mjs tests/utils/batchTaskParams.test.mjs tests/utils/batchExcelImport.test.mjs tests/pages/ArticleGenPage.genre-scene-routing.test.mjs *> ../tmp/genre-ui-scene-routing-integration/frontend-targeted.log
node --test tests/**/*.test.mjs *> ../tmp/genre-ui-scene-routing-integration/frontend-final.log
npm run build *> ../tmp/genre-ui-scene-routing-integration/frontend-build.log
```

## 变更清单

本任务共 31 个文件：后端生产 4、前端生产 8、测试及测试支持 18、文档 1；其中 19 个已跟踪文件修改、12 个任务新增文件。原有 11 个用户未跟踪文件不包含在此清单中。

后端生产：

```text
backend/app/routers/generation.py
backend/app/services/generation/genres/catalog.py
backend/app/services/generation/genres/registry.py
backend/app/services/retrieval/document_retriever.py
```

前端生产：

```text
frontend/src/App.jsx
frontend/src/api.js
frontend/src/components/WritingCatalogSelects.jsx
frontend/src/pages/ArticleGenPage.jsx
frontend/src/pages/BatchTaskPage.jsx
frontend/src/utils/batchExcelImport.js
frontend/src/utils/batchTaskParams.js
frontend/src/utils/genreCatalog.js
```

测试与测试支持：

```text
backend/tests/commercial_genre_scope.py
backend/tests/genre_ui_scene_scope.py
backend/tests/multi_genre_scope.py
backend/tests/run_genre_ui_scene_offline.py
backend/tests/test_genre_ui_cross_stack.py
backend/tests/test_genre_ui_scene_integration.py
backend/tests/test_genre_ui_scene_scope.py
backend/tests/test_material_digest_task_assembly.py
backend/tests/test_material_ingestion.py
backend/tests/test_multi_genre_retrieval.py
backend/tests/test_production_task_assembly.py
backend/tests/test_stable_material_assets.py
backend/tests/upstream_suite_scope.py
frontend/tests/fixtures/genreCatalog.mjs
frontend/tests/pages/ArticleGenPage.genre-scene-routing.test.mjs
frontend/tests/utils/batchExcelImport.test.mjs
frontend/tests/utils/batchTaskParams.test.mjs
frontend/tests/utils/genreCatalog.test.mjs
```

文档：`docs/GENRE-UI-SCENE-ROUTING-INTEGRATION.md`。

## 验证限制与非阻断事项

- 本轮没有调用真实 Writer / Embedding，也没有真实文章、黄金样本、资料库或商业验收；不声明文章质量提升或其他 21 文种生产可用。
- 未启动浏览器做人工交互/截图验收。已执行真实 React SSR、动态选项/能力/表单源码连线检查、API 到前端的跨栈验证及生产构建。
- Vite 构建有单个 JS chunk 654.08 kB、超过 500 kB 的体积提示；没有构建失败。本任务不扩大到 bundle 拆分。
- 后端有一条现有 Starlette 使用 anyio `BlockingPortal` 别名的弃用警告；未新增依赖或升级依赖。
- count > 1 明确要求拆分独立任务；未扩展正式生产路由现有 count=1 合同。
- 历史整文件保护继续采用精确 hash；以后合法修改仍应明确维护其授权范围和行为测试，不以重新封存 hash 绕开 protected owner。

## 六遍自检

| 自检 | 结果 | 证据与边界 |
| --- | --- | --- |
| PASS 1 — UI | PASS | 单篇为标题、文种/场景、层级/单位、领域/目的、字数、资料范围、可选补充要求；没有补充事实、子类型、专题或额外分类层；真实 React SSR 与前端完整 86 项通过 |
| PASS 2 — REGISTRY | PASS | 实际 API → 前端跨栈测试显示 22 项；生产与非生产状态来自 Registry/Gate；旧独立文种列表移除；小规模测试 fixture 不承担目录真源 |
| PASS 3 — SCENE | PASS | 同一既有 taxonomy 接入单篇、批量、历史、Excel；scene 进入 request、辅助 query、trusted task JSON；不改 Evidence 与 FACT；自动值不分类且空场景 parity 通过 |
| PASS 4 — ROUTING | PASS | 能力驱动正式路线；未来合成生产文种无需名称分支；非生产文种、歧义旧值、目录失败均明确阻断；后端独立拒绝伪造生产 flag；没有 current fallback |
| PASS 5 — SAFETY | PASS | P1 排名 AST/常量、P2/P3、Commercial Gate、FACT、Must-Deliver、Stable Asset 与 Vendor 保护通过；篡改负例继续拒绝；真实模型/Embedding/外网/禁止数据访问全为 0 |
| PASS 6 — REGRESSION | PASS | 前端专项 43/全套 86/构建通过，后端专项 1314/全套 1545 通过；仅 2 个原真实 Run skip；安全/风险正文完整链与精确 BASE 等价；原用户文件不变，无 Git 写操作 |

`BLOCKING_FINDINGS = NONE`。本结论仅表示原工单工程 Done Contract 满足、可以交由 ChatGPT 复核；不代表新增文种完成商业验收。

## 最终 Git 状态

- 修改前和修改后 HEAD 均为 `d9b6e68d698e9d7b97dc92f23f8feffd80360c5a`，分支保持 `codex/genre-ui-scene-routing-integration`。
- 19 个已跟踪文件修改、12 个本任务新增文件；原有 11 个用户未跟踪文件仍为未跟踪且 hash 不变。工作区有意保留本任务差异，不能称为 clean。
- `STAGED_FILES = NONE`；`COMMIT_CREATED = NO`；commit message 不适用；`PUSHED = NO`；`PR_CREATED = NO`；未 merge、未 fetch、未另建分支。
- 未修改依赖清单/锁文件、数据库 schema、`.gitignore`、AGENTS 或 Vendor。构建与测试产物都在既有忽略范围内。
- 最终只读核验使用 `git rev-parse HEAD`、`git branch --show-current`、`git diff --cached --name-only`、`git diff --name-only -z`、`git ls-files --others --exclude-standard -z`、`git diff --check` 与本次本地完整性脚本；没有调用暂存/提交/远程写命令。

## 回滚原则

本轮不创建提交；仅允许按最终变更清单逐文件核对本轮改动与精确 BASE。需要撤销时，应先保存本轮差异，再对清单中已跟踪文件恢复 BASE 内容；新增文件先归档并逐项处理。禁止 `git clean`、`git reset --hard`、目录整体删除，禁止触及原有 11 个用户文件、真实资料库或固定 Vendor 快照。实际执行删除或破坏性回滚前仍需明确授权。本轮不执行回滚。

## SCENE-CONTRACT-01 — 服务端 Scene 合同修正（2026-09-27）

本节是后续修正记录，取代上文关于“服务端自动值已完全贯通”的旧结论，不构成新的项目规则。

当前源码确认：正式 HTTP 请求进入 `ArticleGenRequest` 后，由 `generate_article` 调用 `generate_commercial_flow`；正式整篇路线继续进入 `generate_full_draft`、`prepare_full_draft_material`、Retrieval、Stable Assets、Task Assembly、Writer、FACT/Quality、FLOW4。原 Retriever 只在 query 内过滤 auto sentinel，Task Assembly 却从 `request.scene` 直接生成可信任务 JSON。HTTP 元数据保存又只覆盖非空请求，允许旧结果适配器返回的文种值泄入空 Scene 的新记录。

推理闸与修改边界：

- 问题、原始请求流向及两处不一致均由源码和新增失败测试确认。没有真实模型文章质量结论，也不据此猜测质量变化。
- Scene 概念由既有 `scene_classifier.py` taxonomy 创建，用户选择；正式 API 调用规范化函数，Retrieval、Task Assembly、Writer、response/history 消费同一请求值。
- 唯一语义 owner 为 `scene_classifier.py::normalize_generation_scene`，复用 `get_scene_taxonomy()`，没有新增场景列表、分类调用或 Prompt。正式边界只调用一次。已有 Retriever 防御过滤保留，不改变检索架构或 P1。
- 最小修复仅改 `generation.py` HTTP 边界和上述 taxonomy owner；`None`、空字符串、auto、自动、未指定、自动/未指定、自动识别场景（含既有空格及大小写兼容）归为 `None`。合法选择保留，非法新请求返回 HTTP 422 和统一错误结构 `INVALID_SCENE`，不会进入生成器或写历史。
- response/history 的新 Scene 一律来自规范化请求，字符串模型的空值写 `""`。历史模型不收紧、不迁移旧日志；已有 UI 恢复 owner 仍把 `scene=工作总结` 的旧文种回显恢复为空，新 API 直接提交该值则拒绝。没有旧值兼容开关可供新请求绕过校验。
- 最大风险为空场景工作总结的 Prompt/正文或历史重跑变化。贯通测试读取实际 Provider 替身收到的 trusted task JSON 和实际磁盘历史；独立精确 BASE 比较阻断 Prompt、Writer 策略、FACT、Quality、FLOW4、TXT/DOCX 变化。Scene 与相同素材的 Registry、证据写作视图、限制、不可用证据及 Atomic 输入相等，不授予 FACT 权限。
- 用户可见变化仅为 auto 正规化、非法 Scene 明确报错及新历史字段清洁。文种生产资格、路由、事实安全、交付权威和 Writer 架构不变，不恢复旧模式，不修改前端。

生产文件：`backend/app/routers/generation.py`、`backend/app/services/retrieval/scene_classifier.py`。

测试文件：新增 `backend/tests/test_scene_contract.py`；调整 `test_genre_ui_scene_integration.py` 的旧错误期望；`genre_ui_scene_scope.py` / `test_genre_ui_scene_scope.py` 增加独立修正哈希和精确 AST seam。原集成及更早审批哈希不移动；taxonomy、分类逻辑、HTTP 其余语义和保护 owner 仍按 BASE 比较，并有重新封存哈希也不能越界的负例。

验证命令与证据：使用内置 Python 和仓库现有依赖（虚拟环境的原 Windows Store 启动器已不可用），不安装任何依赖。全部测试由未修改的 `run_genre_ui_scene_offline.py` / `run_multi_genre_offline.py` 防护执行，沿用原用户文件保护清单。测试输出保存在 ignored `tmp/scene-contract-01-checkpoint/`。

```powershell
Set-Location 'D:\AI_Projects\gongwen-style-agent-commercial\backend'
$env:PYTHONPATH='D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
$scenePython='C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $scenePython -B tests/run_genre_ui_scene_offline.py -q tests/test_scene_contract.py tests/test_genre_ui_scene_integration.py tests/test_genre_ui_scene_scope.py tests/test_genre_ui_cross_stack.py tests/test_commercial_genre_parity.py tests/test_production_task_assembly.py tests/test_material_ingestion.py tests/test_material_digest_task_assembly.py tests/test_stable_material_assets.py tests/test_multi_genre_retrieval.py
& $scenePython -B tests/run_genre_ui_scene_offline.py -q tests
```

修改前相关基线为 156 passed。新增贯通测试先遇到测试断言误读 retrieval 字段，修正后复现空字符串与 `scene_compat=None` 不一致；修复后首轮 59 passed / 1 failed，剩余失败是历史恢复测试输入温度与既有替身夹具不一致，已修正输入，未改变生产逻辑或 Writer 策略。

最终结果：`SCENE_CONTRACT_FIX = PASS`，`READY_FOR_CHATGPT_REVIEW = YES`。

| 验证 | 实际结果 | 日志（相对于上述 checkpoint） |
| --- | --- | --- |
| 修改前相关基线 | 156 passed；44.73s | `baseline-tests.log` |
| 新 Scene 专项、UI/Scene 集成、精确 BASE / commercial parity、受影响后端回归 | 366 passed；146.38s | `targeted-tests.log` |
| 完整 backend offline suite | 1569 passed、2 skipped；256.22s | `backend-full-tests.log` |
| 真实模型 / Embedding / 外部网络 / 禁止数据访问 | 各次最终测试计数全部为 0 | 各日志末尾 `GENRE_UI_SCENE_OFFLINE_COUNTS` |
| 本轮差异和编码 / Git | UTF-8 解码正常；`git diff --check` 通过；其他已跟踪差异与起始 binary diff 相等；staged 为空 | `scene-contract-only.patch`、`verification.json` |

两项跳过仍为需明确授权的 P3-A / P3-B 私有真实 Run 重放。唯一 warning 为既有 Starlette/AnyIO 弃用提示，不安装或升级依赖。工作总结 production route、完整 Retrieval（空 Scene）、Prompt、Writer 单次调用策略、正文身份、Fact Safety、Quality、FLOW4、TXT/DOCX 与 Required BASE 的离线比较通过。P1/P2/P3-A/P3-B、Atomic Evidence、Fact Safety、FLOW4、Commercial Gate、Vendor、文种成熟度以及 Writer/RAG 架构没有本轮变更。

Git 与回滚：本次起止 HEAD 为 `d9b6e68d698e9d7b97dc92f23f8feffd80360c5a`，沿用当前分支和原 dirty worktree，禁止暂存/提交/远程操作。本地 checkpoint 保存修改前相关文件、原 `git status` 和 binary diff，位于 `tmp/scene-contract-01-checkpoint/`。撤销本修正时须对照此 checkpoint 逐块还原，仅移除本次增量；不得按上文原集成回滚方法直接恢复 HEAD，以免覆盖上一工单未提交成果。本轮没有执行回滚或删除操作。未进行真实 Writer、真实资料、真实页面或文章质量验收；前端未修改，因此没有重跑前端全套或构建。
