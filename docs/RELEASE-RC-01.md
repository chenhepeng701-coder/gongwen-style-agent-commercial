# RELEASE-RC-01 阶段记录与外审交接

本文是本工单执行记录，不是项目规则真源，也不是 External Review。

## 当前结论

```text
RELEASE_VERSION = 0.1.0
BASE_SHA = e50981945773ac7263a5f90fb7f69569ff584098
TOP_LEVEL_NAV_GUARD = PASS (离线组件行为测试)
PHASE_2_REPAIR = PASS
PRE_RELEASE_AUDIT = PASS
READY_FOR_PHASE_3 = YES
INSTALLER_IMPLEMENTATION = PASS (Phase 3 development candidate)
PACKAGE_BUILD = PASS (development candidate only)
READY_FOR_EXTERNAL_REVIEW = YES
READY_FOR_COMMERCIAL_RELEASE = NO
```

首次审计按用户工单 Phase 2 的明确要求停止：“如果 BLOCK：停止本工单并报告证据。”用户随后授权 Phase 2 Repair，并在其验收后明确授权 Phase 3。下面保留各阶段历史记录；最新 Phase 3 执行记录与外审边界见本文末尾。当前没有提交、推送、创建 PR 或合并。

## PRECHECK 与 Git 边界

- 项目：`D:\AI_Projects\gongwen-style-agent-commercial`。
- 初始分支为 `main`，main / HEAD 均等于指定 BASE。
- 从 BASE 创建 `codex/release-rc-01`；本地回滚引用 `codex/checkpoint-release-rc-01` 同样指向 BASE。
- 修改前 tracked / staged 均无变更。只检查原有未跟踪文件的路径，不读取内容、不修改、不移动、不删除、不暂存。
- 已列出 ignored 目录；tracked 文件的父目录中未发现嵌套 `.git`，Git index 没有子模块项。没有遍历 ignored 运行目录来审计其中的历史临时仓库。
- Git 初始写入因沙箱 `.git` 只读失败，经工具权限审批后创建本地分支和 checkpoint；没有 commit。
- 修改前后 HEAD 均为 BASE；commit message 不适用；暂存文件范围为空。

原有 11 个未跟踪文件保持原位：

```text
P1-TOPIC-FIRST-RANKING.patch
P2-AUXILIARY-EVIDENCE-ADMISSION-20260926-012405.patch
P2-NEW-TEST-FILES.zip
backend/low-vs-xhigh-runs.zip
backend/manual_writer_flash_result.json
backend/manual_writer_flash_stream_result.json
backend/manual_writer_result.json
backend/proxy_check.txt
backend/writer_flash_stream_test.json
backend/writer_flash_test.json
backend/writer_test.json
```

上述路径列入本轮离线 runner 的 protected user file manifest。未读取这些文件，因此不声称完成内容哈希核验。

## Phase 1：问题、Owner、实现与验证

当前代码确认：`ArticleGenPage.handleGenerate` 持有 pending Promise；App 中 articleGen / batchTask / genResult / editor 使用同一个 ArticleGenPage 类型，其他顶层页面会卸载它。唯一顶层页面状态写入点是 `App.handleNavigate`。资料库、我的文稿、账户、备份及页面导航均接入这一入口。

最小实现保留原请求 owner，在开始和最外层 finally 通知 App 是否 pending；App 按目标页面组件身份拦截离开工作区的导航。复用 ConfirmDialog，提示工单指定文字，仅提供“继续等待”和“查看生成状态”。后者只导航 genResult。生成完成时解除守卫并关闭提示框。ArticleGenPage 的 effect 仅随 loading 注册 beforeunload，使用浏览器原生警告机制并在结束/卸载时清理。

业务边界：不更改 API、payload、生成次数、取消策略、后端或生成合同；不新增队列、WebSocket 或后台任务系统。用户可见变化仅是生成期间的离开保护。文章质量和事实安全 owner 未修改，没有引入废弃模式。

最大回归风险是父组件卸载、重复提交、pending 未清理和成功/失败结果处理变化。新增测试执行实际 App、Layout、Sidebar、ArticleGenPage 与请求处理器，用内存 Promise 隔离 API；覆盖这四类风险。没有把这些合成测试称为真实生成或浏览器验收。

本轮文件：

1. `frontend/src/App.jsx`：顶层导航守卫和提示框。
2. `frontend/src/pages/ArticleGenPage.jsx`：pending 通知与 beforeunload 生命周期。
3. `frontend/tests/pages/App.generation-navigation-guard.test.mjs`：新增 9 项离线集成测试。
4. `docs/RELEASE-RC-01.md`：本记录。

实际运行（cwd 为 frontend）：

```powershell
node --test tests/pages/GenerationPage.waiting-ux.test.mjs tests/pages/ArticleGenPage.fact-safety-review.test.mjs tests/pages/ArticleGenPage.genre-scene-routing.test.mjs tests/pages/ArticleGenPage.quality-test-draft.test.mjs
node --test tests/pages/App.generation-navigation-guard.test.mjs
node --test tests/pages/*.test.mjs tests/utils/*.test.mjs
npm.cmd run build
```

- 既有相关测试：30/30 PASS。
- 新测试：9/9 PASS。测试开发时首次出现 6 个失败，原因是测试选择器计入 aria-hidden 图标；只修正测试选择器，未据此改变生产行为。
- 新测试加入前的全量前端：119/119 PASS；加入后的最终全量：128/128 PASS，0 skip。
- 生产 build：PASS，生成 `frontend/dist-commercial/index.html` 及引用的 CSS/JS。现有大于 500 kB 的 bundle 警告保留，不扩展处理。
- `git diff --check` 通过；两个生产文件已按严格 UTF-8 解码核验。

证据：`tmp/release-rc-01/phase1-frontend-tests.log`、`phase2-frontend-build.log`。

## Phase 2：首次发布审计（修复前历史记录）

### 已验证的边界

- 相对 BASE 的生产 diff 仅为上述两个前端文件；所有后端及文种配置、API/payload 构造和 Word 导出 owner 均未变。
- tracked 路径与密钥特征审查未发现真实 .env、私钥、运行 data/uploads/output、用户导出文档、缓存、截图或构建产物进入 Git。命中的敏感信息测试常量属于明确的离线合成测试夹具；未打印其值。此审查不等于未构建安装包的内容验收。
- `frontend/src/api.js` 保持同源 `/api`。生产源码未检出 Vite HMR、开发端口或硬编码本地开发服务依赖；构建后的 index 引用 `/assets/`。静态托管响应尚未实现或验证。
- Vite 配置的 `emptyOutDir: false` 保留。后续若获准进入打包，必须从洁净 staging 构建，不能把现有 dist 目录整体当作安装包输入。
- 正式 `/api/generation/generate` 仍进入 `generate_commercial_flow`；official_draft 的 full_article_drafting 路由继续进入 `generate_full_draft`，执行 Registry/Gate、RAG、Stable Assets、Task Assembly/Atomic、Writer、Fact、FLOW4、Must-Deliver 与现有 artifact/Word 导出。
- 工作总结保留当前商业准入；上游其他 21 文种继续非 production_ready，Commercial Gate 和 UI disabled / “待商业验收”保持原状。未提升 maturity。
- 最新 main 已有真实模型 E2E 为用户工单提供事实。本轮未找到绑定当前 BASE 的 tracked 验收记录，独立核验状态为 UNKNOWN；未因此重复调用模型。

主代理复核命令包括：

```powershell
git diff --exit-code e50981945773ac7263a5f90fb7f69569ff584098 -- backend frontend/src/components/WritingCatalogSelects.jsx frontend/src/utils/batchTaskParams.js frontend/src/utils/genreCatalog.js
git diff --check
git diff --cached --name-only
```

上述后端/合同范围 diff 为空、退出码 0；暂存区为空。

### 后端环境与完整离线测试

仓库 `.venv/Scripts/python.exe` 和系统 `py -3.12` 均指向不可启动的 Windows Store Python 路径。没有要求用户手动修复，也没有安装新依赖。改用 Codex 已提供的 Python 3.12.14，并把现有仓库 `.venv/Lib/site-packages` 加入当前 Python 进程路径。8 项 requirements 的版本和实际 import 均验证匹配。

实际运行（cwd 为 backend；已先创建仅含上述 11 条路径的 protected-user-files.json）：

```powershell
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -c "import sys; sys.path.insert(0,r'D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'); sys.path.insert(0,'tests'); sys.argv=['release-rc-01','-q','tests']; from run_multi_genre_offline import main; raise SystemExit(main(workspace_relative='tmp/release-rc-01',task_name='RELEASE-RC-01',count_label='RELEASE_RC_01_OFFLINE_COUNTS',require_user_manifest=True))"
```

沿用现有 runner，不修改测试和预期；运行目录隔离在本轮 ignored tmp 内。结果为退出码 1：**5 failed, 1588 passed, 2 skipped, 1 warning，325.05 秒**。

```json
{"asyncio_socketpair_ipc":422,"external_network":0,"forbidden_data_access":0,"real_embedding":0,"real_model":0}
```

两个跳过入口位于 P3A/P3B 的私有真实 Run 回放测试，本轮没有授权回放；仓库代码对此明确 skip。完整命令未添加 -rs，因此没有额外运行 skip 摘要。1 个 warning 为 Starlette/AnyIO 的弃用警告。

### 五项失败与基线归类

| 测试 | 本轮首个失败断言 | 分类与证据 |
| --- | --- | --- |
| `test_genre_ui_scene_scope.py::test_ui_scene_scope_and_protected_owners_match_required_merge_base` | `App.jsx` 不符合历史精确哈希 | 本轮授权导航修改新增此处不匹配；同一测试对 RELEASE BASE 的 `ArticleGenPage.jsx` 已有旧哈希冲突，不能简单称为一个全新失败或纯粹旧失败。 |
| `test_genre_ui_scene_scope.py::test_historical_directory_guards_cover_new_files_before_and_after_future_staging` 的 material_ingestion 参数 | `App.css` 不等于历史 BASE | 当前 CSS 等于 RELEASE BASE，既有冲突。 |
| 同上测试的 production_task_assembly 参数 | `App.css` 不等于历史 BASE | 当前 CSS 等于 RELEASE BASE，既有冲突。 |
| `test_material_ingestion.py::test_protected_generation_retrieval_and_legacy_owners_unchanged` | `App.css` 不等于历史 BASE | 当前 CSS 等于 RELEASE BASE，既有冲突。 |
| `test_production_task_assembly.py::test_protected_owners_unchanged_from_task_base` | `App.css` 不等于历史 BASE | 当前 CSS 等于 RELEASE BASE，既有冲突。 |

精确对照使用 Git show 读取指定版本，LF 归一后计算 SHA-256；对 App.jsx / ArticleGenPage.jsx 的 BASE 字节，还在本轮 tmp 快照上直接调用现有 `assert_integration_content`。没有改写当前源码、测试或 pin。

- BASE 的 App.jsx：`c3ace3f5a94c441ac0c3db483cd0a9b170fe218e992e5b45c1997cbb86bfb32d`，匹配原 pin；本轮内容不匹配。
- BASE 的 ArticleGenPage.jsx：`1c8ea5155f8a42789d3b76be38fbe412426c3839e3e906fe85b1fd23b2cca24a`，已不匹配原 pin `f019cd8ad773c851aae6a15e7854469bad1ad74b5a69392dad4f536343ab0668`。
- 当前/BASE 的 App.css：`bcc9a4f055ee4fb440910950c0f36b0ef7d707798dd260b8f771db34ac6a5ee8`。
- 两个历史 BASE（`90b5a2141580610e9c60e1805db7a034affb57b5`、`a6d93a54ac1d417eb0f8ae23d92569677e8e4f40`）的 CSS：`1f2e4f880889b803e4c7149fbe5380d5085897960d0ae81920a4eb4702ff2007`。

这些是实际字节/断言诊断，不是干净 main 完整 suite 重跑。失败不是单靠终端中文显示异常判断的，也没有归因于 Provider 或网络。没有发现允许忽略这五项失败的 xfail/expected-failure 声明。

完整证据：`tmp/release-rc-01/phase2-backend-tests.log`、`phase2-baseline-content-diagnostic.json`。失败仍计入 Gate，不以历史问题为由转成 PASS。

## 首次停止状态、未知与接续（修复前历史记录）

已完成 PRECHECK、Phase 1 的离线行为验证和 Phase 2 审计。完整安装版目标未实现。未运行真实浏览器验证、模型/Embedding 调用、安装版生成/编辑/Word E2E、PyInstaller、Installer、开发机安装版验收、External Review、干净机验收。Phase 3–8 均未开始。

已确认阻断是五项历史范围/内容锁定测试未通过。不能据此推断本轮导航守卫破坏了后端生成语义；也不能把前端通过与后端大多数通过替代完整发布 Gate。

推荐下一步：由用户明确授权处理该发布审计阻断后，先界定已合并等待 UX 与本轮导航保护应如何纳入范围保护；保留历史事实、安全与篡改负例约束，完成针对性与全量验证。不要盲目刷新所有哈希、扩大 allowlist、跳过失败或撤销已合并 UX 来取得绿色结果。未确定修复方案前不改后端测试；Phase 2 PASS 前不进入 Phase 3。

首次停止时需要用户决定是否授权处理历史范围锁定冲突；该授权已在后续 Phase 2 Repair 指令中取得。“继续按照工单进行”当时仅按恢复中断工作理解，没有视为取消 Phase 2 的停止条件。

最终 Git 状态应仅为：两个已有前端文件修改、上述新测试与本文未跟踪、原有 11 个无关未跟踪文件。测试日志、诊断快照、构建产物均 ignored，不属于提交或安装包输入。未修改任何用户已有 dirty 内容。

如需要回滚，先确认这些文件没有后续用户编辑并保存本轮补丁。然后仅恢复两个生产文件到 checkpoint；新增测试和本文可在确认后移入本轮备份目录。以下是可执行路径，不在本轮执行：

```powershell
Set-Location -LiteralPath 'D:\AI_Projects\gongwen-style-agent-commercial'
git diff --binary --output=tmp/release-rc-01/phase1-before-rollback.patch -- frontend/src/App.jsx frontend/src/pages/ArticleGenPage.jsx
git restore --source=codex/checkpoint-release-rc-01 -- frontend/src/App.jsx frontend/src/pages/ArticleGenPage.jsx
# 新增文件另行确认后归档；不使用 git clean/reset 或全仓 restore。
```

禁止触碰原有 11 个未跟踪文件、真实资料/密钥/私有 Writing Reference、既有运行输出；禁止提交、push、PR、merge，直到工单相应 Gate 获得明确授权。当前没有需要保留运行的本轮测试服务或后台进程。

## Phase 2 Repair：授权范围与最终实现

用户明确授权只处理上述五项失败涉及的精确锁冲突，重跑 Phase 2；禁止删除/跳过/放宽 guard、整体推进 BASE、改变生成业务、进入 Phase 3 或提交/push/PR/merge。首次失败记录继续保留，不被本次结果覆盖。

### 修改前核验与推理闸

- `HEAD`、`main` 仍为 RELEASE BASE，分支仍为 `codex/release-rc-01`，暂存区为空。
- Git first-parent 与 merge 记录确认已合并 PR #21 为 `e50ecb0388eb6dd2ad465e39752331cb5b58e25c`，PR #22 为 `e50981945773ac7263a5f90fb7f69569ff584098`；二者均为当前 main 的祖先。
- 五个历史相关文件为 App.css、ArticleGenPage.jsx、GenerationPage.jsx、GenerationWaitingIndicator.jsx、GenerationPage.waiting-ux.test.mjs。后两个分别在 #22、#21 新增；其余修改及测试后续变化均进入了 #21/#22。
- 除 ArticleGenPage.jsx 保留原 Phase 1 增量，另四个历史文件与 #22 main 的 LF 归一字节完全相同。
- App.jsx / ArticleGenPage.jsx 当前哈希与本轮首次 Phase 1 验收记录完全相同：`e27f337...58f1` / `f809320...8b58`；没有在 Repair 中再次修改生产代码。
- 实际问题是范围保护未登记已合并等待 UX 和已验收导航保护；owner 是测试范围 manifest 与两条历史目录 guard，不是生产生成模块。方案只授权精确文件内容，不提供目录通配或动态计算期望值。
- 最大风险是为了消除旧哈希冲突而放宽保护；因此保留原路径集合、所有历史 BASE、后端 hash/AST 保护，并验证每个新增路径单字节变更仍被拒绝。
- 文章质量、事实权限、请求 payload、Writer 调用和用户可见行为均没有额外变化；不恢复旧模式。

修复前备份保存在 `tmp/release-rc-01/before-phase2-repair/`；Phase 1 tracked diff 保存于 `phase2-repair-before.patch`。这不是 commit，也不复制无关工作区内容。

### 核验后的精确锁与文件边界

| 类别 | 路径 | 锁定来源 |
| --- | --- | --- |
| 历史 A | `frontend/src/App.css` | #22 main：`bcc9a4f055ee4fb440910950c0f36b0ef7d707798dd260b8f771db34ac6a5ee8` |
| 历史 A + Phase 1 | `frontend/src/pages/ArticleGenPage.jsx` | #22 main 的 `1c8ea515...ca24a` 加已验收 Phase 1 增量，最终锁 `f809320467dd6badd318ec13617c70569c30bd80f4d17ffa7a0f3c325a398b58` |
| 历史 A | `frontend/src/pages/GenerationPage.jsx` | #22 main：`dcc83278ffe8c7d0f86ffeb42e3e92f7737382d4ba14f341cc023d7c98a8b42a` |
| 历史 A | `frontend/src/components/GenerationWaitingIndicator.jsx` | #22 main：`72c16d54c540c71e695a37d405afb83c7354461decb6bc4125af1f5f3739ddb7` |
| 历史 A | `frontend/tests/pages/GenerationPage.waiting-ux.test.mjs` | #22 main：`14fbfc1f766bbcac158540c7600ffe3bd7e30efc4a210c5af00837fe4a369c1d` |
| Phase 1 B | `frontend/src/App.jsx` | 已验收导航保护：`e27f337faa06031a762a1516b39bf45d272ed0e10ca5150595ef706c86cb58f1` |
| Phase 1 测试 | `frontend/tests/pages/App.generation-navigation-guard.test.mjs` | 已通过的 9 项回归测试：`17eeea5a10fb91c1d470d00ca4444df10f54f6001828475cb00477ae5cd0e152` |

历史相关锁计 5 项，本轮 Phase 1 相关锁计 3 项；ArticleGenPage 同时涉及两类，合计 7 个不同文件。未发现 C 类无法解释的生产差异。

Repair 最终修改范围：

1. `backend/tests/genre_ui_scene_scope.py`：仅更新原字典的 App / Article 两个值及原因说明；原字典 key 集合、guard 函数、BASE 和其他声明保持不变。
2. `backend/tests/release_rc_01_scope.py`（新增）：仅为另外五个明确路径提供固定 SHA-256 检查；先验证路径资格，再读文件；不授权后端路径。
3. `backend/tests/test_material_ingestion.py`、`backend/tests/test_production_task_assembly.py`：仅在原目录 guard 对这五个路径调用上述精确检查；其余原判断和断言逐一保留。
4. `backend/tests/test_release_rc_01_scope.py`（新增）：30 项范围回归，覆盖五路径正例、单字节拒绝、非法路径在 I/O 前拒绝、四个历史 blob 对照、两条原 guard 在当前索引/模拟暂存后调用和逐路径篡改拒绝。
5. 本文更新；Phase 1 的三个文件保持原验收版本。

### 保留的中间失败与纠正

首版修复把五个新增路径直接加入原 manifest；原 5 个失败定向测试通过。但完整 suite 的 `test_business_domain_catalog.py::test_scope_reseal_keeps_all_existing_path_lists_and_non_domain_router_ast` 拒绝扩大原 key 集合：结果为 **1 failed, 1597 passed, 2 skipped，328.82 秒**。

该失败没有被忽略或改预期。等待整轮结束后，恢复原 key 集合，仅保留两个获准哈希更新；五个新增锁进入独立测试 manifest。跨模块原测试保持原样。中间日志为 `phase2-repair-targeted-tests.log`、`phase2-repair-backend-full-tests.log`。

### 最终复验与证据

所有 backend 测试继续使用前文的现有离线 runner、相同 Python/依赖与 protected-user-files.json；只改变 pytest 选择参数和本轮日志名称，没有改 runner。任务名为 `RELEASE-RC-01-PHASE2-REPAIR`。

最终定向选择：

```text
-q
tests/test_genre_ui_scene_scope.py::test_ui_scene_scope_and_protected_owners_match_required_merge_base
tests/test_genre_ui_scene_scope.py::test_historical_directory_guards_cover_new_files_before_and_after_future_staging
tests/test_material_ingestion.py::test_protected_generation_retrieval_and_legacy_owners_unchanged
tests/test_production_task_assembly.py::test_protected_owners_unchanged_from_task_base
tests/test_business_domain_catalog.py::test_scope_reseal_keeps_all_existing_path_lists_and_non_domain_router_ast
```

原 5 项加新增暴露的跨模块约束，共 **6/6 PASS**，56.34 秒，所有外部调用/受保护文件访问计数为 0。日志 `phase2-repair-targeted-tests-final.log`。

新增 guard 测试单独以 `-q tests/test_release_rc_01_scope.py` 运行：**30/30 PASS**，0 skipped，112.28 秒；外网、模型、Embedding、受保护文件访问计数均为 0。日志 `phase2-repair-new-scope-tests.log`。主代理已核对源代码和实际日志，未仅采用子代理口头结果。

重新执行的前端命令与结果：

```powershell
node --test tests/pages/*.test.mjs tests/utils/*.test.mjs
npm.cmd run build
```

最终前端 **128/128 PASS**，0 skipped；build PASS，沿用已说明的 bundle 大小警告。日志 `phase2-repair-frontend-tests-final.log`、`phase2-repair-frontend-build-final.log`。

边界诊断实际执行：

```powershell
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B tmp/release-rc-01/check-phase2-repair-boundary.py
git diff --check
```

诊断通过。AST 对照确认原 scope 除两个获准值以外无变化；两个目录 guard 仅新增固定 import 和精确检查分支，移除这两处明确增量后与 BASE AST 完全一致。后端生产/Vendor/依赖零变更；原历史列表没有扩大，原字典没有删除 key。结果见 `phase2-repair-boundary-final.json`；历史来源及 Phase 1 哈希复核见 `phase2-repair-provenance.json`。

最终完整后端命令选择 `-q -rs tests`，退出码 0：**1623 passed、0 failed、2 skipped、1 warning，428.30 秒**。日志 `phase2-repair-backend-full-tests-final.log`。其中 30 项为新增范围保护测试；修复前的 5 项失败均通过，首次试验暴露的 business-domain 路径列表守卫也保持原样并通过。

```json
{"asyncio_socketpair_ipc":422,"external_network":0,"forbidden_data_access":0,"real_embedding":0,"real_model":0}
```

`-rs` 确认两个 skip 分别是 `test_p3a_semantic_state_recognition.py:278`、`test_p3b_claim_evidence_boundary.py:355` 的显式私有 Run 回放授权条件。没有添加 skip/xfail，未授权真实回放未执行。1 个 warning 仍为 Starlette/AnyIO 弃用提示。

最终六类要求全部有证据：原 5 失败测试及新增暴露约束定向通过，backend full offline 0 failed，frontend full 128/128，frontend production build PASS，`git diff --check` PASS，scope/boundary PASS。历史业务链路、Genre Gate、maturity、Must-Deliver、Word 合同、依赖和供应商均没有变化。

```text
PHASE_2_REPAIR = PASS
FAILED_TESTS_BEFORE = 5
FAILED_TESTS_AFTER = 0
HISTORICAL_LOCKS_UPDATED = 5 paths (PR #21 / #22)
CURRENT_PHASE1_LOCKS_UPDATED = 3 paths (App / ArticleGenPage / navigation test)
GUARDS_REMOVED = NO
GUARDS_WEAKENED = NO
UNEXPLAINED_PRODUCTION_DIFF = 0
BACKEND_FULL_TESTS = PASS (1623 passed, 0 failed, 2 authorized-condition skips)
FRONTEND_FULL_TESTS = PASS (128 passed)
FRONTEND_BUILD = PASS
BOUNDARY_POLLUTION = NONE
PRE_RELEASE_AUDIT = PASS
READY_FOR_PHASE_3 = YES
```

历史和 Phase 1 两组计数共享 ArticleGenPage，不代表新增八个不同路径。全部七个文件继续精确锁定；没有整体推进旧 baseline，没有动态从工作树生成期望值，没有修改业务逻辑以迎合测试。

### Repair Git、回滚与停止边界

修改前后 commit 仍为 BASE，commit message 不适用，暂存区为空。最终应审查文件为 Phase 1 的两个生产文件和导航测试、上述五个 backend 测试文件及本文。原有 11 个无关 untracked 保留；日志、诊断脚本、快照和构建输出不进入提交或安装包。

若仅回滚 Repair，先确认没有后续编辑并保存补丁，再恢复三个已跟踪测试文件到 checkpoint；两个新增测试 helper/测试文件可在用户确认后移入 `tmp/release-rc-01/` 的备份位置，文档原版已保存在 before-phase2-repair。不要回滚已验收的 Phase 1 生产文件。下列命令只记录，不执行：

```powershell
Set-Location -LiteralPath 'D:\AI_Projects\gongwen-style-agent-commercial'
git diff --binary --output=tmp/release-rc-01/repair-before-rollback.patch -- backend/tests/genre_ui_scene_scope.py backend/tests/test_material_ingestion.py backend/tests/test_production_task_assembly.py
git restore --source=codex/checkpoint-release-rc-01 -- backend/tests/genre_ui_scene_scope.py backend/tests/test_material_ingestion.py backend/tests/test_production_task_assembly.py
```

即使 Phase 2 最终 PASS，也只输出 READY_FOR_PHASE_3，不自动进入 Phase 3。安装包、External Review、干净机与真实安装后 E2E 仍全部未运行；不得据此宣称商业发布完成。

## Phase 3：安装版实现与开发机验收

用户在验收 Phase 2 Repair 后明确授权继续 Phase 3A 至 3H。上述 Phase 2 停止记录保留为历史；当前停止点为 Phase 4 External Review Gate。

按顺序完成 Runtime Root、现有模型配置安装适配、Writing Reference 用户配置、FastAPI 正式静态托管、原生 Windows Launcher、PyInstaller onedir、NSIS 安装程序和开发机安装版验收。安装版使用 `%LOCALAPPDATA%\GongwenWritingAssistant` 保存用户数据，程序资源单独只读；只监听 `127.0.0.1`，通过系统浏览器使用同源 `/api`。用户无需 Python、Node、npm、Vite、Git 或终端命令。

模型配置继续使用原 ModelConfigPage 和 settings API；Writing Reference 复用原 schema 和验证逻辑，只将验证段提取为共享函数。生成、RAG、Writer、Fact Safety、FLOW3、FLOW4、Must-Deliver、Genre Gate 和 Word 导出 owner 均未修改。21 个未商业验收文种继续 disabled。

Release Build 逐个选取 Git tracked 生产文件和本工单明确新增文件，使用 fresh stage 构建前端，不复制整个工作目录。构建输入清单 406 项、实际包文件清单 470 项均记录 SHA-256。上游 Vendor 的完整性合同要求保留其精确锁定的测试和构建器源码作为只读校验资产；不执行这些文件，详见外审交接中的例外说明。

### 当前候选与验证结果

- 候选：`tmp/rc01-89d578d6/公文写作助手-Setup.exe`，0.1.0，22,224,232 bytes。
- 构建时间：`2026-09-27 14:29:21 UTC`；SHA-256：`6f77ba7e71f4814a6f16a507283310a744bb9075e06d770720f9a4e9e5080071`。
- backend full offline：1671 passed、0 failed、2 skipped；两个 skip 仍为未授权的私有真实 Run 回放。外网、真实模型、真实 Embedding、受保护资料访问计数均为 0。
- frontend full：131 passed；production build：PASS。既有 bundle 大小警告不在修复范围。
- 最终候选 packaging smoke：4 passed；PyInstaller 与 NSIS 均成功。
- 最终候选开发机验收：安装、自动服务/前端、单实例、仅 loopback、模型配置及本机 fake 连接、Reference 校验、资料至 READY、Word 导出、正常退出、重启持久化、卸载保留运行数据全部 PASS。
- 正式浏览器页面已核验已配置状态和文种禁用状态，无脚本错误。没有真实模型生成或干净机验收。
- `git diff --check` 和 scope/boundary 检查通过；Phase 1 精确哈希保持不变。当前 HEAD/main 仍为 BASE，暂存区为空，原有 11 个无关 untracked 保留且未读取。

所有命令、日志、文件清单与审查入口见 [Phase 4 外审交接](RELEASE-RC-01-EXTERNAL-REVIEW.md) 和 [构建说明](../packaging/windows/README.md)。首次 Phase 3 全量暴露的 21 个 scope 锁连带失败已使用独立精确哈希 seam 修复，保留原失败证据；没有删除 guard、跳过测试或整体推进历史 baseline。

```text
PHASE_3_IMPLEMENTATION = PASS
RUNTIME_ROOT = PASS
MODEL_CONFIGURATION = PASS (offline / development installation)
WRITING_REFERENCE_CONFIGURATION = PASS
STATIC_FRONTEND_HOSTING = PASS
WINDOWS_LAUNCHER = PASS
SINGLE_INSTANCE = PASS
PYINSTALLER_BUILD = PASS
WINDOWS_INSTALLER_BUILD = PASS
DEV_MACHINE_PACKAGE_ACCEPTANCE = PASS
BACKEND_FULL_TESTS = PASS (1671 passed, 0 failed, 2 authorization-condition skips)
FRONTEND_FULL_TESTS = PASS (131 passed)
FRONTEND_BUILD = PASS
PACKAGING_SMOKE_TESTS = PASS (4 passed)
SECRETS_BUNDLED = NO
USER_DATA_BUNDLED = NO
PRIVATE_REFERENCE_BUNDLED = NO
BOUNDARY_POLLUTION = NONE
READY_FOR_EXTERNAL_REVIEW = YES
READY_FOR_COMMERCIAL_RELEASE = NO
```

### Phase 3 Git、回滚与停止边界

修改前后 commit：`e50981945773ac7263a5f90fb7f69569ff584098`；commit message 不适用；staged 文件为空。没有 commit / push / PR / merge。当前安装包是未提交工作树的开发候选，不得作为合并 main 后的最终 Release。

Phase 3 开始前的已验收状态已保存在 `tmp/release-rc-01/phase3-checkpoint/accepted-phases.patch`、同目录 `RELEASE-RC-01.md` 和 `status.txt`。需要回滚时，只撤销 Phase 3 增量：恢复本阶段七个 tracked 生产文件及 business-domain guard；从另外两个 guard 中仅移除 Phase 3 helper 引用与精确检查分支，保留 Phase 2 修复；新文件按本阶段清单逐项核对后再处理。不得恢复整个工作树，也不得覆盖 Phase 1 / Phase 2 或原有无关文件。实际回滚操作须在用户确认后执行。

现在停止开发，等待用户明确提供 `EXTERNAL_REVIEW = PASS` 且 `BLOCKING_FINDINGS = 0`。本执行记录不是 External Review；尚未执行发行签名、干净机验收、安装后真实模型 E2E 或商业发布。
