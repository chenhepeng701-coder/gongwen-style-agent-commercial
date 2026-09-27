# BUSINESS-DOMAIN-TAXONOMY-01 实现与验收记录

本文件是本次实现及 FIX-01 审查修复的说明与验收证据，不是项目规则真源，也不构成文章质量验收。原“纪检/巡察 → 纪检 / 廉政”判断已被 ChatGPT Review 否定；下述当前语义已按 FIX-01 更正。

## 基线与边界

- 项目：`D:\AI_Projects\gongwen-style-agent-commercial`。
- Required BASE、开始时 HEAD、main：`4333864dc24829a5853d783fc749c7ef9d8ada21`。
- 分支：`codex/business-domain-taxonomy-01`；从上述 BASE 创建，作为本轮修改前定位点。
- 开始时无 tracked/staged 修改；原有 11 个 untracked 文件保留，列表与 SHA256 记录于 ignored `tmp/business-domain-taxonomy-01/`。
- 根仓库之外发现的 `.git` 位于 ignored `tmp/baseline-02b-r2r3/` 测试夹具目录，本轮不使用、不修改。
- 按当前工单不执行 add、commit、push、PR、merge、External Review；HEAD 保持 BASE。

## 真源审计与推理闸

1. BASE 前端静态列表有 14 项，包括旧混合值“纪检/巡察”和独立分类“纪检 / 廉政”。前一版误把二者当作同义词；实际它们不能静默归并。请求边界原来直接传递业务领域。
2. 已验证的创建与消费链：单篇/批量/Excel/历史回放 → `ArticleGenRequest` → `generation.generate_article` → `generate_commercial_flow` → `generate_full_draft` → `prepare_full_draft_material` → Retrieval/Task Assembly → Writer trusted task JSON。
3. `build_official_draft_task` 被 Retrieval 和 Task Assembly 共用；在 HTTP Router 入站统一解析，后续不需要分别转换。
4. 原 Excel 显式未知领域返回行级错误；空单元格或缺列继承 defaults。直接 API 接受未知字符串，历史对象保留未知字符串。三者的兼容语义必须分别保持。
5. BASE 资料分类器明确区分“纪检 / 廉政”和“巡察整改”，不能仅因旧混合值包含“纪检”就推断它等于前者。FIX-01 保留两个独立 canonical，混合旧值既不是 canonical，也不是任一项的 alias。
6. 唯一新 owner 是 `backend/app/services/business_domain_catalog.py`，位于共享服务层，与 generation 相邻。它提供名称、展示名称、别名及解析；不属于 Genre capability 或事实权限层。
7. 前端复用既有 `loadWritingCatalogs` 与 `catalogLoading/catalogError` 状态。工具函数只解释后端元数据，不保存另一张业务领域表。
8. 最小改法是新增只读目录 API、Router 的一次归一化，以及前端现有入口接线。无同职责后端任务目录可直接复用。
9. 主要风险是异步目录加载与历史回放竞态、未知值丢失、Excel 兼容退化以及 Retrieval/Writer 分别转换。通过真实页面 hook/事件测试、HTTP 合同、冻结对象、内存 XLSX、实际 Task Assembly/Prompt 及 BASE parity 阻断。
10. 用户可见变化为后端目录驱动选项及目录加载状态；FIX-01 将两个明确领域分开提供，混合历史值原样保留。没有模型质量提升结论；不改变事实规则，不恢复旧生成模式，不修改默认 generation_route 债务。

## Catalog 合同

`GET /api/generation/business-domains` 返回 `{"items": [{"value": "...", "display_name": "...", "aliases": []}]}`，无写接口。

当前 14 个 canonical 值：党建、纪检 / 廉政、巡察整改、意识形态、基层治理、乡村振兴、安全生产、营商环境、生态环保、教育医疗、经济发展、作风建设、办公室综合、其他。

- “纪检 / 廉政” → 原值；“巡察整改” → 原值；“纪检/巡察” → 原值。
- 混合旧值不按标题、正文或其他字段猜测，不创建第三个 canonical，不清空、不触发新增 4xx。
- 已知值的空白、半角/全角斜杠排版变体按同一通用 resolver 处理。
- 未知值原样保留，包括原始空白；`None`、空串沿用原语义。既有任务构建器仍执行原有文本清理。
- FIX-01 仅按明确授权加入 classifier 已有的“巡察整改”；没有把“作风建设”并入纪检，没有新增“组织人事”“综合政务”等其他资料分类器选项，也未统一全部 classifier/UI taxonomy。
- 不迁移用户资料、索引或旧日志，不改变资料自动分类器。业务领域仍只是辅助检索与写作上下文。

## 前端、历史与 Excel

- 单篇、批量 defaults、批量行共享 `BusinessDomainSelect`，仅展示服务端 canonical 选项与 display_name。
- 保留原“安全生产”默认选择偏好；它不构成选项真源。
- 三张目录共同加载；业务领域目录缺失、空、无效或加载失败均阻止生成和 Excel 导入，显示明确提示，可重新加载，没有静态降级。
- App 历史回放、页面内历史恢复及晚到的目录均只消费后端名称元数据；混合旧值按非目录值保留，复制出新任务对象，不修改 history object。
- 未知历史值作为禁选的历史项显示且可以原值重放；不提供自定义领域输入框。
- 显式 Excel canonical/有效 alias 正常接受并归一化。“纪检/巡察”旧混合单元格及已有空白/斜杠排版变体原样保留；其他显式未知单元格仍是行级错误。空/缺失列继续继承 defaults，未知历史 defaults 原样保留。
- Excel 的 `PRESERVED_LEGACY_DOMAIN_CELLS` 仅登记已明确授权的旧单元格兼容放行，不提供 canonical 名称或别名映射，也不成为下拉选项。这样无需放宽所有未知 Excel 值，也无需修改 UI 或公共归一化代码。
- `buildTaskGenerationPayload` 必须取得有效业务领域目录；正式后端仍独立做同一次请求边界解析，直接 API 不依赖前端校验。

## 保护边界

后端既有生产文件仅修改 `backend/app/routers/generation.py`：一个只读 endpoint、一个局部 import、一次 `req.business_domain` 赋值。

P1 coefficient/formula/topic-first ranking、P2 admission、P3-A/P3-B、Atomic Evidence、Fact Anchor、Fact Safety、FLOW4、Commercial Gate、Provider、Vendor 22 Genre Prompt、Scene/Genre taxonomy 均无生产代码变更。

`genre_ui_scene_scope.py` 仅为当前精确 frontend/Router 内容重新计算哈希。所有原路径列表、字典键和历史 BASE 保持一致；Router AST 仅识别精确目录函数及精确归一化语句，其他节点仍与基线比较。新增负向测试证明错误 endpoint、清空 domain 或绕过交付检查不能因重新封印而获准。

## 验证与复现

所有数据为本地合成夹具；真实 Writer/Embedding/外部网络均禁用。复用未经修改的 `run_multi_genre_offline.py` 防护，独立 runner 要求初始用户文件保护清单，运行输出全部写入 ignored 临时目录。此 runner 不读取用户资料库或历史 Runs。

本机 `.venv` 引用的 Windows Store Python 启动失败；改用已安装的 Codex Python 3.12.14，并通过进程级 `PYTHONPATH` 加载现有 `.venv` 依赖。没有安装包或修改环境配置。

后端执行方式（在 `backend` 下）：

```powershell
$env:PYTHONPATH = 'D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'
& 'C:/Users/peng chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -B tests/run_business_domain_offline.py -q tests
```

定向测试使用同一命令，将末尾 `tests` 替换为：

```text
tests/test_business_domain_catalog.py tests/test_genre_ui_cross_stack.py
tests/test_genre_ui_scene_integration.py tests/test_genre_ui_scene_scope.py
tests/test_scene_contract.py tests/test_commercial_genre_parity.py
tests/test_topic_first_document_ranking.py tests/test_multi_genre_retrieval.py
```

前端命令（在 `frontend` 下）：

```powershell
node --test tests/utils/genreCatalog.test.mjs tests/utils/batchTaskParams.test.mjs tests/utils/batchExcelImport.test.mjs tests/pages/ArticleGenPage.genre-scene-routing.test.mjs
$taxonomyTests = @(rg --files tests -g '*.test.mjs')
node --test @taxonomyTests
npm run build
```

以下为 FIX-01 **之前**已运行的工程结果；它们不能证明原错误语义正确，也不代表 FIX-01 之后已重跑全量后端：

| 检查 | 结果 | 本地日志 |
| --- | --- | --- |
| 修改前前端相关基线 | 49 passed | `tmp/business-domain-taxonomy-01/frontend-baseline.log` |
| 修改前后端 Genre/Scene/Scope/工作总结 parity 基线 | 179 passed | `tmp/business-domain-taxonomy-01/backend-baseline.log` |
| 修复前前端定向 | 60 passed | `tmp/business-domain-taxonomy-01/frontend-targeted.log` |
| 修复前前端全量 | 103 passed | `tmp/business-domain-taxonomy-01/frontend-full.log` |
| 修复前前端构建 | PASS；121 modules | `tmp/business-domain-taxonomy-01/frontend-build.log` |
| 后端 Catalog/真实 HTTP/跨栈/Scene 定向 | 37 passed | `tmp/business-domain-taxonomy-01/backend-domain-targeted.log` |
| 后端相关回归（含 P1、Genre/Scene、保护边界、工作总结 parity） | 243 passed | `tmp/business-domain-taxonomy-01/backend-targeted.log` |
| 补充持久化历史回放与最终 scope 验证 | 4 passed | `tmp/business-domain-taxonomy-01/backend-final-delta.log` |
| 修复前后端全量离线 | 1587 passed、2 skipped；316.55 秒 | `tmp/business-domain-taxonomy-01/backend-full.log` |

初次前端构建报告本轮默认参数重复声明，已修正；之后完整测试和构建重新通过。剩余构建提示是单个 bundle 超过 500 kB，不影响构建，本工单不处理打包拆分。后端已完成批次只有 Starlette/AnyIO 弃用提示，防护计数 real_model、real_embedding、external_network、forbidden_data_access 均为 0。

两个 skipped 为 `test_p3a_semantic_state_recognition.py::test_real_run_offline_replay` 与 `test_p3b_claim_evidence_boundary.py::test_real_run_replay`，均因原工单不授权私有历史 Run 回放而按原测试设计跳过。该全量记录对应 FIX-01 之前的代码。

页面验证使用实际 React 页面源、hook 状态与事件处理、服务端实际目录的跨栈测试及内存 API fixture；没有启动浏览器或服务。工作总结同一合成资料/同一模型条件与 Required BASE 对比了完整 Prompt、一次 Writer/无重试、Fact Safety、质量结果、原文交付及 FLOW4 TXT/DOCX。

## 文件清单

生产文件（9）：

```text
backend/app/services/business_domain_catalog.py       新增共享目录 owner
backend/app/routers/generation.py                     目录 GET 与请求边界解析
frontend/src/api.js                                  目录请求
frontend/src/components/WritingCatalogSelects.jsx    共用领域下拉
frontend/src/pages/ArticleGenPage.jsx                 单篇/批量/历史入口接线
frontend/src/pages/BatchTaskPage.jsx                  defaults/行级下拉
frontend/src/utils/genreCatalog.js                   通用目录加载与元数据消费
frontend/src/utils/batchExcelImport.js               旧模板与别名归一化
frontend/src/utils/batchTaskParams.js                defaults/请求 payload
```

测试、防护和 fixture 文件（10）：

```text
backend/tests/run_business_domain_offline.py
backend/tests/test_business_domain_catalog.py
backend/tests/genre_ui_scene_scope.py
backend/tests/test_genre_ui_cross_stack.py
backend/tests/test_scene_contract.py
frontend/tests/fixtures/genreCatalog.mjs
frontend/tests/pages/ArticleGenPage.genre-scene-routing.test.mjs
frontend/tests/utils/genreCatalog.test.mjs
frontend/tests/utils/batchTaskParams.test.mjs
frontend/tests/utils/batchExcelImport.test.mjs
```

说明文件：`docs/BUSINESS-DOMAIN-TAXONOMY-01.md`。未将临时日志、原模型响应、缓存、用户资料或用户 untracked 文件加入本任务文件。

## FIX-01 修复边界与验证

修复开始时重新确认分支及 HEAD 未变。以 ignored `tmp/business-domain-taxonomy-01/fix-01/before-hashes.json` 和 `before-content.json` 记录当时未提交实现，作为本次修复的比较点；不 reset、clean、checkout 或创建 commit。

本次相对修复开始时仅修改两个生产文件：

1. `backend/app/services/business_domain_catalog.py`：移除错误 alias，加入独立“巡察整改”，更新说明。`normalize_business_domain` 函数体不变。
2. `frontend/src/utils/batchExcelImport.js`：仅放行并保留明确的旧混合单元格，其他未知单元格的拒绝策略不变。

Router、请求模型、通用前端 normalizer、历史 restore 工具、页面和 Catalog Select 均没有本轮增量修改。公共请求归一化代码未再次修改，因此按 FIX-01 测试边界只跑定向及受影响回归，不把先前全量后端结果冒充本轮结果。

测试/fixture/精确 hash 文件共 8 个：`backend/tests/test_business_domain_catalog.py`、`backend/tests/test_genre_ui_cross_stack.py`、`backend/tests/genre_ui_scene_scope.py`、`frontend/tests/fixtures/genreCatalog.mjs`、`frontend/tests/utils/genreCatalog.test.mjs`、`frontend/tests/utils/batchTaskParams.test.mjs`、`frontend/tests/utils/batchExcelImport.test.mjs`、`frontend/tests/pages/ArticleGenPage.genre-scene-routing.test.mjs`。Scope 仅重新封印对应的 6 个前端文件哈希，不改路径列表或 AST 豁免。

最小修复前新合同检查实际失败：后端 2 项（缺独立分类、错误映射），前端 1 项（移除错误 alias 后，旧混合单元格被拒绝）。这三项是已复现的 blocker，不是被隐藏的基线故障。日志位于 `fix-01/backend-before-fix.log` 与 `fix-01/frontend-before-fix.log`。

修复后第一次后端定向为 24 passed、1 failed：新增“巡察整改”暴露了原测试把不同业务领域的 `atomic_evidence.required_evidence` 排序也要求一致的错误假设。源码 `_task_tokens` / `_select_core_evidence` 原本就使用领域词计算此内部顺序，且明确不删减证据。测试现在严格比较 **同输入 Required BASE** 的完整 Anchor/Packet，同时只允许跨领域比较中的该列表顺序不同，仍逐项比较完整事实注册表、证据内容、限制和权限；没有改 Atomic 生产代码。首次失败保留于 `fix-01/backend-targeted.log`，最终结果另存，不覆盖失败证据。

FIX-01 已运行结果（完整日志前缀 `tmp/business-domain-taxonomy-01/fix-01/`）：

| 检查 | 本轮结果 | 日志 |
| --- | --- | --- |
| 最小后端 Catalog/API/跨栈/历史/同输入 BASE 验证 | 25 passed | `backend-targeted-final.log` |
| 前端受影响测试 | 60 passed | `frontend-targeted.log` |
| 前端全量 | 103 passed | `frontend-full.log` |
| 前端构建 | PASS；121 modules；仅原有大包提示 | `frontend-build.log` |
| 受影响后端回归（Genre/Scene/Scope） | 147 passed；仅 Starlette/AnyIO 弃用提示 | `backend-regression.log` |
| 后端全量 | NOT_RUN：公共请求归一化代码未再次修改 | — |

本轮后端继续使用上文同一离线 runner、Python 和现有依赖，增加进程级 `PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`，避免 Windows 控制台错误信息乱码；源文件仍按严格 UTF-8 核验。定向参数为 `tests/test_business_domain_catalog.py tests/test_genre_ui_cross_stack.py`；回归参数为 `tests/test_genre_ui_scene_scope.py tests/test_genre_ui_scene_integration.py tests/test_scene_contract.py`。前端三个命令与上文相同。无真实模型、Embedding、Scene LLM 或外部网络调用。

## 初版验收声明（已被 Review blocker 否定，保留追溯）

```text
BUSINESS_DOMAIN_TAXONOMY_IMPLEMENTATION = PASS
BUSINESS_DOMAIN_SINGLE_SOURCE = YES
BACKEND_CATALOG_OWNER = YES
FRONTEND_STATIC_DOMAIN_SOURCE_REMOVED = YES
KNOWN_DUPLICATE_RESOLVED = YES
LEGACY_ALIAS_NORMALIZED = YES
UNKNOWN_HISTORY_VALUE_PRESERVED = YES
HISTORY_OBJECT_UNMUTATED = YES
SINGLE_ARTICLE_INTEGRATION = PASS
BATCH_INTEGRATION = PASS
EXCEL_INTEGRATION = PASS
HISTORY_RESTORE = PASS
RETRIEVAL_CANONICAL_DOMAIN = PASS
WRITER_CANONICAL_DOMAIN = PASS
RETRIEVAL_WRITER_DOMAIN_EQUAL = YES
P1_CHANGED = NO
P2_CHANGED = NO
P3A_CHANGED = NO
P3B_CHANGED = NO
FACT_SAFETY_CHANGED = NO
COMMERCIAL_GATE_CHANGED = NO
VENDOR_CHANGED = NO
REAL_MODEL_CALLS = 0
EMBEDDING_CALLS = 0
SCENE_LLM_CALLS = 0
EXTERNAL_NETWORK_CALLS = 0
READY_FOR_CHATGPT_REVIEW = YES
```

Git 核查：16 个本任务 tracked 文件修改，4 个本任务新文件；暂存区为空，无新增 commit，HEAD/main 仍为 Required BASE。原有 11 个用户 untracked 文件 SHA256 全部不变。`git diff --check` 通过，20 个任务文件严格 UTF-8 解码通过且无替换字符。审查对象是当前分支工作区 diff，不是已提交的 PR。

## FIX-01 六遍自检与当前验收

1. PASS 1：后端 resolver、实际 API 与前端 resolver 均保留“纪检/巡察”，目录中不存在将它映射到“纪检 / 廉政”或“巡察整改”的 alias。
2. PASS 2：实际只读目录包含两个独立 canonical“纪检 / 廉政”和“巡察整改”，总数为 14；旧混合值不作为可选 canonical。
3. PASS 3：冻结 history object、持久化历史回放、实际页面恢复、批量 defaults/payload、Excel 行解析与内存 XLSX 均验证旧值保留，原历史对象不变。
4. PASS 4：正式请求通过真实 Router 和 Task Assembly，离线 synthetic Writer 边界捕获结果；Retrieval、task JSON、Writer trusted task context 均为“纪检/巡察”。两个明确 canonical 各自通过同样链路验证。
5. PASS 5：`git diff -- backend/app/services/retrieval/official_document_classifier.py` 为空；定向测试逐文件对比既有后端 owner 与 Required BASE，P1/P2/P3/Fact Safety/Commercial Gate/Vendor 均无变化。现有 classifier 分别返回“纪检 / 廉政”和“巡察整改”。
6. PASS 6：相对 FIX-01 开始时仅改 2 个生产文件、8 个测试/fixture/hash 文件及本说明。公共请求归一化、历史 restore 工具、页面和 Catalog Select 无本轮增量；未扩大 scope allowlist。

本轮定向与回归 runner 均报告 `real_model=0`、`real_embedding=0`、`external_network=0`、`forbidden_data_access=0`，无 Scene LLM 调用。最终文件/Git/UTF-8/用户文件校验结果存于 ignored `tmp/business-domain-taxonomy-01/fix-01/final-check.json`。

```text
BUSINESS_DOMAIN_FIX_01 = PASS
CANONICAL_DOMAIN_COUNT = 14
AMBIGUOUS_LEGACY_MAPPING_REMOVED = YES
DISCIPLINE_CANONICAL = 纪检 / 廉政
INSPECTION_CANONICAL = 巡察整改
AMBIGUOUS_LEGACY_VALUE = 纪检/巡察
AMBIGUOUS_LEGACY_PRESERVED = YES
AMBIGUOUS_LEGACY_SILENTLY_CANONICALIZED = NO
HISTORY_PRESERVATION = PASS
EXCEL_PRESERVATION = PASS
BATCH_PRESERVATION = PASS
RETRIEVAL_DOMAIN_PRESERVATION = PASS
WRITER_DOMAIN_PRESERVATION = PASS
RETRIEVAL_WRITER_DOMAIN_EQUAL = YES
OFFICIAL_DOCUMENT_CLASSIFIER_CHANGED = NO
P1_CHANGED = NO
P2_CHANGED = NO
P3A_CHANGED = NO
P3B_CHANGED = NO
FACT_SAFETY_CHANGED = NO
COMMERCIAL_GATE_CHANGED = NO
VENDOR_CHANGED = NO
REAL_MODEL_CALLS = 0
EMBEDDING_CALLS = 0
SCENE_LLM_CALLS = 0
EXTERNAL_NETWORK_CALLS = 0
READY_FOR_CHATGPT_REREVIEW = YES
```

## 回滚与后续

修改尚未暂存或提交，main 和 HEAD 不变。若需撤回 FIX-01，比较并使用 `tmp/business-domain-taxonomy-01/fix-01/before-content.json` 中 11 个文件的完整快照，恢复至本次修复开始时的未提交实现；不要把它们直接回退至 Required BASE，那会丢弃主工单实现。先核对快照、当前 diff 及用户文件清单，不使用整体 clean/reset/checkout。用户未授权执行回滚，本轮不执行。

初版曾报告 `READY_FOR_CHATGPT_REVIEW`，随后 ChatGPT Review 指出上述 blocker。FIX-01 完成后仅交付 `READY_FOR_CHATGPT_REREVIEW`；External Review、Git Closeout、暂存和提交均不执行。此验证不包含真实 Provider 或黄金样本文章质量验收，也不声称文章质量改善。
