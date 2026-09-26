# MULTI-GENRE-FOUNDATION

本文件为本轮架构迁移说明和验收证据，不替代 `AGENTS.md`。本轮只验证架构与离线行为保持，不声称重新完成真实模型文章质量验收。

## 基线与边界

- 项目：`D:\AI_Projects\gongwen-style-agent-commercial`。
- BASE_SHA / 开始 HEAD：`0d789f7b563acd6f95b71b795d18fbf5a5072651`。
- 分支：`codex/multi-genre-foundation`。
- 起始已跟踪文件无改动，staged 为 0；11 个既有用户未跟踪文件保留。
- 本次明确禁止 add/commit/push/PR/merge，优先于一般完成提交规则。回滚检查点为上述 BASE_SHA，不创建 checkpoint commit。
- 正式整篇链：`/api/generation/generate` → `ArticleGenRequest` → `ArticleGenerator.generate_commercial_flow` → `generate_full_draft` → retrieval → stable assets → task assembly → Writer → canonical facts → quality → FLOW4 → delivery。
- `generation_route=current` 的既有结构化链、旧 Planner/SectionWriter、前端 UI 不属于本轮迁移范围；不新增或恢复旧模式。
- 真实资料库、真实 Run、Atomic classifier、Stable Asset fingerprint、数据库、Provider 和依赖均不修改；全部测试禁止真实模型、Embedding 和外部网络。

## 硬编码审计

| 位置 | 基线证据 | 本轮处理 |
| --- | --- | --- |
| `models/generation.py::validate_full_draft_route` | `full_article_drafting` 要求显式“工作总结” | 改由唯一 Registry 解析身份、生产能力和导出适用性 |
| `document_retriever.py::_chunk_coverage_role` | future/problem/achievement/major_work/context 写死 | 分类规则移入工作总结 Retrieval Profile |
| `_primary_coverage_selection` / `_select_chunks` | 固定覆盖角色和 work_summary 布尔分支 | 共享覆盖算法消费 Profile 的角色和预算 |
| `_fact_chunk_bonus` / `_rank_chunks_for_document` | 内容角色、完成态线索、未来段降权等固定偏好 | 纯阅读偏好迁入 Profile；不变更 Atomic 的状态或授权 |
| `_aggregate_documents` / `_select_documents` | P1 topic-first、负向标题门、主辅选择 | 保留共享算法与公式；用 AST 和完整输出等价保护 |
| `task_assembly.py` | 阅读卡按原文动作词分组，未发现总结专属结构假设 | 原文件保持不变，直接复用当前任务身份与合法证据链 |
| `official_document_drafting.py` | summary pinned rules、文种名、S02/S05 boundary | 规则描述符由 Adapter 提供，事实安全模板由共享 Writer 持有 |
| `full_draft_material.py::materials_markdown` | 无条件提供 S02/S05 | 按 Adapter 的参考适用策略加载；不变更 packet、证据或 Brief 合同 |
| `work_summary_quality_gate.py::evaluate_full_article_quality` | 工作总结合同与通用长度/身份计算混合 | 工作总结观测保留，通用部分提取到共享质量边界 |
| `full_draft_generation.py` / `delivery_decision.py` | 直接导入工作总结质量函数 | 改用同一个共享 Registry dispatcher；交付处仍复验报告 |
| FLOW4 / artifact | 已有整篇 body、报告身份和导出合同可共享 | 文件及合同不变 |
| Frontend/API | UI 不需要开放第二文种；公共请求不得开启 offline | 不改 UI；真实 HTTP 测试验证实验/未知文种 fail-closed |

资料分类器 `official_document_classifier.py` 的文种知识仍用于资料/来源分类，不是正式生成能力注册表。它及 ingestion/parser owner 保持不变。

## 架构与唯一 Owner

```text
Request → Genre Registry（身份、能力）
             ↓
Shared Retrieval Core ← Genre Retrieval Profile
             ↓
Stable Assets → Task Assembly → Atomic / P2 / P3-A → Frozen Registry
             ↓
Shared Writer Boundary ← Genre Drafting Rules / Reference Applicability
             ↓
Immutable FullArticleCandidate → P3-B canonical Fact Safety
             ↓
Shared Quality Boundary ← Genre Structure / Quality Contract
             ↓
FLOW4 → Must-Deliver → Artifact
```

| Owner | 职责 |
| --- | --- |
| `generation/genres/registry.py` | 唯一文种身份注册、别名、生产/离线能力解析、组合根 |
| `generation/genres/contracts.py` | 冻结的 Adapter、DraftingRules、StructureContract、Capabilities 合同 |
| `generation/genres/retrieval.py` | 检索阅读 Profile 合同，无事实授权接口 |
| `retrieval/document_retriever.py` | 向量候选、P1 评分、主辅选择、覆盖算法、trace |
| `generation/official_document_drafting.py` | 规则完整性加载、共享事实边界和整篇 Prompt 组装 |
| `generation/full_article_quality.py` | canonical FACT 认证、全文身份、实测长度、共用检查收据、文种质量分派 |
| 既有 Atomic / FACT / FLOW4 / delivery owners | 证据、安全权威、正文及 Must-Deliver；不由 Adapter 接管 |

### Registry 合同

`resolve_genre(document_type, purpose=...)` 只从一个 `GENRE_REGISTRY` 解析。注册属于受信任的应用组合，HTTP 请求不能提交 Adapter 或回调。重复身份在写入任何映射前拒绝。

- `inspection`：只查询注册信息，不授予生产能力。
- `production`：必须 `VERIFIED_CORE_GENRE` 且 `production_ready=True`。
- `offline`：必须声明离线架构验证能力。
- 缺失/未注册文种：`GENRE_NOT_REGISTERED`。
- 实验文种请求生产：`GENRE_OFFLINE_ONLY`。
- 旧内部无参 Prompt helper 的 `default_genre()` 仅作兼容，正式 Prompt 和 quality 必须有明确任务文种，不从标题猜测。

公共 `ArticleGenRequest` 默认生产校验。离线测试只能通过进程内 Pydantic validation context 指定 `genre_purpose=offline`，JSON 同名字段无效。`generate_full_draft` 会丢弃 context 重新验证，因此构造好的 offline request 也不能进入生产服务。

低层 RAG 仍服务既有 `current` 路线：默认只采用 production-ready Profile，未知及实验文种保留原 relevance-only 行为。离线证明显式注入已解析的实验 Profile。这避免仅注册实验文种就改变旧生产路径。

### Adapter 合同

Adapter 实际提供并被消费的配置：identity/aliases、retrieval profile、drafting rules/完整性锁、structure contract、quality contract/evaluator、export formats、production/offline capability、限定的事实规则适用声明。

`fact_policy_extensions` 仅允许 `PRESERVE_PLAN_MODALITY` 等已支持的收紧/适用声明。本轮只声明该现有共享规则适用于汇报建议；没有 bypass、UNKNOWN 自动可写、扩充 evidence、跨 scope 或事实检查回调。文种回调不获得证据授权接口。

新增文种的步骤：增加 Adapter/Profile/规则/质量观测实现 → 在唯一组合根注册 → 增加离线合同测试 → 单独完成正式验收后才允许 production_ready。无需修改 P1 公式、共享 Writer 事实边界、Atomic、FACT 或 Must-Deliver。

## 工作总结迁移

`genres/work_summary.py` 是首个 `VERIFIED_CORE_GENRE`，支持“工作总结”和 `work_summary` 身份。既有工作总结质量算法保留在原质量 owner，由 Adapter 的只读回调调用，不强行把工作总结成熟度标准泛化。

工作总结检索角色保持 `achievement_status / major_work / problem / next_step / context`；coverage 优先次序、6/8/24/2 预算、内容角色系数及未来段/占位线索降权保持。文档评分公式、负标题门、主辅文档选择和片段 800/300 截断保持。

上游规则身份保持：

```text
zhaohui-yang/official-document-drafting@6b4166a50b96733684b36da6336ea85a847cf3be/offline/default/summary
SHA256 = 80c99367127e6eabca3a5329ec745bcd5bad46734b10aa577a478f4627f858b1
```

原 prompt、`src/adapters/offline/build.py` 和 `LICENSE` 三项字节锁均保留。专项直接从 BASE_SHA 加载原实现，比较完整 Prompt 字典（含 hash）、HOST_BOUNDARY 以及完整 Bound Quality Report；不是仅比较新 dispatcher 和新函数。损坏三项锁中的任一文件均 fail-closed。

## 检索与事实边界

共享 core 继续拥有 embedding/search、raw candidate、topic/title/content 评分、semantic aggregate、source-role 文档兼容评分、negative title gate、primary/auxiliary 原则与 trace。文种 Profile 提供 coverage roles、分类、chunk 阅读偏好及其 source-role 适用性和覆盖预算。

文档级来源分类和 P1 评分不因新增文种改写；文种的材料阅读偏好在文档选定后的 chunk 选择层生效。Profile 回调与原检索材料隔离，不能通过副作用改写原始正文/metadata 后送入 Atomic。

Atomic、Auxiliary admission、Semantic State、Claim↔Evidence discovery、canonical FACT、主体/管辖边界、结果/因果检查、精确引用保持原 owner。共享质量层先认证真实 FACT 报告，再允许文种观测；随后复算结构、长度和 FACT 三项共用收据，拒绝缺项、重复、伪造、过期或错误文种报告。质量观测不能把风险事实标为通过。

## 第二文种离线证明

- 文种：`report / 汇报材料`。
- 状态：`EXPERIMENTAL`；`PRODUCTION_READY=NO`；`OFFLINE_ARCHITECTURE_PROOF=YES`。
- 规则：项目自有 `genres/rules/report_offline.md`，有独立 hash，不伪称存在第二套上游 pinned 文种。
- Coverage/structure：`situation / issues / recommendations`。
- 质量：`report_offline_quality_v1`，明确需人工复核，不宣称商业成熟度通过。
- 不读取工作总结 S02/S05 私有参考包。

证明测试先只注册工作总结，确认汇报材料无法解析；然后仅调用注册，运行真实 request validation → shared retrieval（合成 search fixture）→ 合成 Stable Assets → 原 Task Assembly/Atomic → 独立规则组装 → canonical FACT → 独立质量 → FLOW4 → delivery。测试前后比较共享源码 hash，证明新增注册不要求编辑共享代码。

离线正文是明确编写的 fixture，不调用 Writer，也不把它当真实文章质量证据。fixture 故意把正在推进写成已完成，验证 P3 风险仍被发现且正文原样以 `SUCCESS_WITH_REVIEW` 完整交付。Evidence snapshot、candidate、rendered、delivered body identity 均有断言。

## 测试与中间失败

所有运行均通过 `backend/tests/run_multi_genre_offline.py`：审计 hook 阻断网络和真实资料目录，HTTP transport/Embedding sentinel 阻断真实调用；pytest runtime/cache 位于 ignored `tmp/multi-genre-foundation/`。Windows asyncio socketpair 仅限本地事件循环 IPC，不算外部网络。

项目 `.venv/Scripts/python.exe` 指向失效的 WindowsApps Python 路径，两次直接启动均未执行测试。改用已有 bundled Python 3.12.14，复用 `.venv/Lib/site-packages`，没有安装依赖。

中间运行记录：

| 运行 | 结果 | 说明 |
| --- | --- | --- |
| 首轮 backend | 781 passed / 6 failed / 2 skipped | 后半段存在并行写盘，6 项为历史范围/hash guard；不能视为固定原树完整基线 |
| 检索首次专项 | 20 passed | 工作总结不同候选规模、旧文种完整输出基线等价 |
| foundation + retrieval | 31 passed | 第二文种贯通及生产拒绝 |
| 三组多文种专项首次 | 48 passed | 含真实 HTTP fail-closed |
| Writer/quality/reference/material-view | 108 passed | BASE Prompt/Quality 完全相等，锁损坏及收据伪造负例 |
| shared-safety 中间回归 | 300 passed / 2 failed / 2 skipped / 5 deselected | 2 个 P2 mutation 间接触发尚未固定的迁移 pin；5 项临时排除仅用于诊断，最终全套不排除 |
| 检索收尾专项 | 24 passed | 增加实验文种生产隔离、Profile 副作用隔离、source-role 阅读适用性 |
| 多文种最终专项（含 scope） | 81 passed / 0 failed / 0 skipped | 精确迁移 pin、原历史 pin 及未来单字节篡改拒绝全部通过 |

历史 guard 不删除、不改旧 SHA 或历史 BASE。`tests/multi_genre_scope.py` 给本次候选增加精确内容 pin，另外保留 P1 核心 AST、受保护 owner 字节比较、完整工作总结行为比较与 future byte mutation 必须失败的测试。合法迁移不等于放开旧保护。

内部审查还修复了：实验 Profile 通过旧 current 路线自动生效；共享质量原先仍信任 Adapter 自报通用检查。修复及验证纳入最终回归，不隐藏这两项中间发现。

固定版本最终回归：**868 passed / 0 failed / 2 skipped**，共收集 870 项，耗时 134.40 秒。81 项新专项包含在完整套件中，不重复累计。唯一 warning 为已有 Starlette/AnyIO `BlockingPortal` 别名弃用提示。

完整套件覆盖新专项、P1、P2、P3-A、P3-B、RAG、Task Assembly、Writer material view、full article、Fact Safety、Delivery、FLOW4、work summary quality 和 Stable Asset guards。所有执行计数均为：

```text
REAL_MODEL_CALLS = 0
EMBEDDING_CALLS = 0
EXTERNAL_NETWORK_CALLS = 0
FORBIDDEN_REAL_DATA_ACCESS = 0
```

两项跳过名称为 `test_real_run_offline_replay`（P3-A）及 `test_real_run_replay`（P3-B）；没有跳过离线事实规则测试，没有临时 deselect 留到最终回归。Windows 本地 socketpair IPC 共 223 次，仅用于测试事件循环。

实际最终命令（PowerShell，backend 目录）：

```powershell
Set-Location -LiteralPath 'D:\AI_Projects\gongwen-style-agent-commercial\backend'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = "$PWD/.venv/Lib/site-packages"
& 'C:/Users/peng chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -B tests/run_multi_genre_offline.py -q tests/test_multi_genre_foundation.py tests/test_multi_genre_retrieval.py tests/test_multi_genre_drafting_quality.py tests/test_multi_genre_scope.py --junitxml=../tmp/multi-genre-foundation/test-results/multi-genre-01.xml
& 'C:/Users/peng chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -B tests/run_multi_genre_offline.py -q tests --junitxml=../tmp/multi-genre-foundation/test-results/backend-final-01.xml
```

证据位于 ignored `tmp/multi-genre-foundation/test-results/`：`multi-genre-01.log/.xml`、`backend-final-01.log/.xml`；中间运行保留 `backend-baseline`、`shared-safety-01`、`foundation-first`、`multi-genre-first` 日志。详细命令和失败分类也保存在 `tmp/multi-genre-foundation/tests-status.md`。

## 架构验收结论

| 条件 | 结论与依据 |
| --- | --- |
| Shared Core / 唯一 Registry | YES；核心只依赖合同/注册表，具体 Adapter 仅由组合根导入 |
| 工作总结迁移 | YES；正式整篇路由、检索规则、Prompt、结构/质量均经 Adapter |
| 工作总结行为保持 | YES（离线）；相同输入与 BASE 的完整 retrieval/Prompt/quality report 对照，既有生产 route 离线回归 |
| Retrieval / drafting / structure / quality 可插拔 | YES；报告 Profile 不同角色和偏好，独立规则与质量合同，并经实际消费 |
| 第二文种无共享核心改动即可注册 | YES；测试隔离注册表中先拒绝、再注册，并走完整离线链且共享源码 hash 不变 |
| 实验文种不能进入正式链 | YES；模型校验、真实 HTTP、服务重新校验、旧 current 检索隔离均有测试 |
| Adapter 不能改 Evidence authority | YES；无权限开关/授权回调，检索策略副作用隔离，canonical FACT 必须先认证，共用收据复算 |
| P1 / P2 / P3-A / P3-B | PRESERVED；P1 公式/共享函数/常量 AST 不变，P2/P3 owners 字节不变且回归通过 |
| Must-Deliver / 全文 identity | PRESERVED；风险正文完整交付，RAW/Candidate/Delivered 路径及报告绑定回归通过 |
| Stable Asset owner / 重处理 | UNCHANGED / NO；受保护源文件、fingerprint、资产与 Task Assembly guards 通过 |
| 全部专项 / 相邻离线回归 | PASS；最终 868/0/2，两个 skip 仅为真实 Run 回放 |
| ChatGPT Code Review | READY；尚未暂存、提交、推送或创建 PR |

`ARCHITECTURE_STATUS=OFFLINE_VERIFIED_READY_FOR_REVIEW`。当前代码阻断项为 NONE；这不是新的真实文章质量验收或第二文种商业发布结论。

## Stable Asset 影响

Stable Asset、source substrate/parser、material ingestion/fingerprint 及 Atomic owner 未修改。测试只使用临时目录中的合成资产，不重新处理真实 39 篇资料库、不读取或改写真实 Run。`STABLE_ASSET_REPROCESS_REQUIRED=NO` 是基于这些 owner 和合同未变的工程结论，不是本轮重新逐篇验收资料库的声明。

## 已知限制与未运行验证

1. 工作总结生产行为通过离线 BASE 等价与回归证明保持。本轮没有真实 Writer/Embedding 调用，没有新真实稿或黄金样本人工比较，也不宣称文章质量提升。
2. P3-A/P3-B 历史真实 Run 回放按本次无真实资料访问边界跳过；对应离线安全链和调用链仍回归。
3. 汇报材料只有离线架构证明，生产路由拒绝；没有自动开放其他文种或前端入口。
4. `**一、主要做法**` 的 display-only heading fallback 留作 backlog。`article_blocks` 还参与事实审查 units、candidate 验证和 offset；本次不改 canonical body、事实审查分段或前端结构投影。
5. 不迁移未触达的 `current` 结构化 Planner/旧辅助函数，不把它们宣称为新的通用 Adapter 主链。
6. Atomic 内既有 `work_summary_evidence_group.writable_fact_diagnostic` 名称及 `next_step/problem` 的保守 UNKNOWN 上下文补标属于遗留共享事实实现，本轮不改其代码或授权。汇报 Profile 使用独立角色，仍服从 canonical 状态判定；这不构成其他文种已经完成正式语义验收的证据。

## 回滚范围与 Git

本轮没有提交。改动共 33 个任务文件：18 个生产代码/规则文件、14 个测试/执行器文件、1 个验收文档。原 11 个用户未跟踪文件仍保留。所有任务文件均以严格 UTF-8 解码验证；`git diff --check` 通过。回滚必须只针对本轮文件，不运行 clean、reset 或覆盖用户文件的批量命令。

已跟踪的生产修改（7）：

```text
backend/app/models/generation.py
backend/app/services/generation/delivery_decision.py
backend/app/services/generation/full_draft_generation.py
backend/app/services/generation/full_draft_material.py
backend/app/services/generation/official_document_drafting.py
backend/app/services/generation/work_summary_quality_gate.py
backend/app/services/retrieval/document_retriever.py
```

新增生产代码/资源（11）：

```text
backend/app/services/generation/full_article_quality.py
backend/app/services/generation/genres/__init__.py
backend/app/services/generation/genres/contracts.py
backend/app/services/generation/genres/registry.py
backend/app/services/generation/genres/retrieval.py
backend/app/services/generation/genres/work_summary.py
backend/app/services/generation/genres/work_summary_retrieval.py
backend/app/services/generation/genres/report.py
backend/app/services/generation/genres/report_retrieval.py
backend/app/services/generation/genres/report_quality.py
backend/app/services/generation/genres/rules/report_offline.md
```

测试/执行器（14，含 6 个新增文件）：

```text
backend/tests/multi_genre_scope.py
backend/tests/run_multi_genre_offline.py
backend/tests/test_multi_genre_foundation.py
backend/tests/test_multi_genre_retrieval.py
backend/tests/test_multi_genre_drafting_quality.py
backend/tests/test_multi_genre_scope.py
backend/tests/p1_ranking_approval.py
backend/tests/p3b_boundary_scope.py
backend/tests/test_material_ingestion.py
backend/tests/test_p3b_claim_evidence_boundary.py
backend/tests/test_production_task_assembly.py
backend/tests/test_rag_observability.py
backend/tests/test_topic_first_document_ranking.py
backend/tests/test_writing_reference_pack_02b1.py
```

其他文件只有本验收文档。`tmp/multi-genre-foundation/` 下的 plan/status/decisions、scope-audit、日志和 XML 均 ignored，不进入提交。scope-audit 列出逐文件 hash、UTF-8 检查及 staged 状态。

最终 Git 状态：修改前和修改后 HEAD 均为 BASE_SHA；15 个已跟踪任务文件修改、18 个任务新文件（其中 1 个为本文档），另有原 11 个用户未跟踪文件。`STAGED_FILES=NONE`、`COMMIT_CREATED=NO`、`PUSHED=NO`、`PR_CREATED=NO`。没有 commit message，因为本轮按工单禁止创建 commit。

可执行回滚方式：先保存本轮 diff 供检查；对本报告清单中的已跟踪修改文件逐一执行 `git restore --source=0d789f7b563acd6f95b71b795d18fbf5a5072651 -- <明确路径>`，只恢复工作区，不暂存。新增文件必须逐一路径核对并取得删除确认后移除；不删除整个 backend/app/services/generation 或 tests 目录，不触碰原 11 个用户未跟踪文件。以上仅为回滚说明，本轮未执行回滚。

待 ChatGPT Code Review 后，由用户决定后续暂存、提交及真实质量验收；本轮不预先执行。
