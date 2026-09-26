# Multi-Genre Commercial Infrastructure 工程记录

文件类型：本次基础设施任务的审计与验收证据，不是新的项目规则真源。项目规则仍由根目录 `AGENTS.md` 持有。

当前记录状态：**工程离线验收通过，准备 ChatGPT Review**。这是基础设施行为保持证据，不是新增真实文章质量验收或商业质量评级。

## 任务与基线

- TASK：`MULTI-GENRE-COMMERCIAL-INFRASTRUCTURE`。
- 项目：`D:\AI_Projects\gongwen-style-agent-commercial`。
- BASE / 本次起始 HEAD：`7672e69ac60ea96b5610a59f77cc4c4c58894cfa`。
- 分支：`codex/multi-genre-commercial-infrastructure`。
- 当前 HEAD 与 BASE 相同；本次工单明确禁止 `git add / commit / push / PR / merge`，覆盖一般开发提交规则。
- 首次会话曾因本地缺少指定 BASE 而停止。随后由用户在外部 PowerShell 同步 main 并创建目标分支；Agent 恢复后只本地核验，没有重新 fetch 或执行其他网络操作。
- 初始暂存文件为 0，原有 11 个用户未跟踪文件保留；只记录其路径清单用于防护，没有读取其内容。

本轮不增加产品文种，不将汇报材料商业化，不引入开源项目、模板、依赖、数据库变更或真实资料重处理，不宣称文章质量提高。

## 初始调用链审计与适用范围

当前正式整篇路由与既有 `current` 结构化路径必须区分。这一结论由 BASE 源码重新确认，而非沿用历史摘要。

正式整篇链为：

```text
/api/generation/generate
→ ArticleGenRequest（full_article_drafting / official_draft）
→ generator.ArticleGenerator.generate_commercial_flow 的早期分派
→ full_draft_generation.generate_full_draft
→ 任务归一化 → Retrieval → 已处理 Stable Assets
→ Task Assembly → Atomic / P2 / P3-A → 已冻结合法 Registry
→ deterministic reading brief / FullDraftMaterialPacket
→ 固定宿主事实边界 + 文种 Drafting Rules → 单次完整文章 Writer
→ 原始 UTF-8 正文保存 → immutable FullArticleCandidate
→ exact citation proposal → P3-B canonical Fact Safety
→ shared full-article quality → FLOW4 → Must-Deliver → TXT / DOCX
```

`full_draft_generation.py` 的模块合同为 `No Planner/SectionWriter/fallback`。BASE `prepare_full_draft_material` 明确只复用 `build_official_draft_task`，不进入 `prepare_flow3_v33_structured_generation`。`atomic_evidence.py::build_full_draft_anchor_input` 明确是 plan-free source-scope projection，不生成正文计划、标题、主次、次序、预算或 Writer task。

另一条既有 `current` 路径仍通过 `prepare_flow3_v33_structured_generation` 消费 `PlanningEngine`、`FrozenArticlePlan`、工作总结 evidence groups、Writer plan control、结构化质量恢复。这些实现不是本轮正式整篇链的前置步骤，也没有被接入、复制或变成 fallback。

### A/B/C/D 分类

| 类别 | 已确认位置与行为 | 本轮处置 |
| --- | --- | --- |
| A `SHARED_SAFETY_CORE` | Retrieval P1 文档评分、主辅选择；Atomic / P2 admission / P3-A；P3-B、canonical FACT、subject/scope、精确引用 | 原 owner 保留，不改事实授权和分类语义 |
| A | immutable candidate、正文身份、公共 quality receipts、FLOW4、Must-Deliver | 原 owner 保留；Runtime 只请求已有校验和交付 |
| A | Atomic 引用 `work_summary_evidence_group.writable_fact_diagnostic` | 虽含文种名称，已经是共享事实实现依赖，不因字符串迁移而改动 |
| B `COMMERCIAL_GENRE_CAPABILITY` | Registry / retrieval profile / drafting rules / structure / quality / export | 复用 Foundation 合同，将完整性与一致性纳入 Gate |
| B | 正式整篇规划、证据阅读、Writer 控制与调用预算 | 明确为 Writer 单次调用内自主谋篇、动作词阅读索引、冻结 source scope、一次调用；通过商业合同与 Runtime 消费 |
| B | quality recovery 适用性、runtime identity、delivery applicability | 显式声明当前正式路线禁用自动恢复、0 轮/0 恢复调用，记录 Runtime 身份 |
| C `WORK_SUMMARY_ONLY_SEMANTICS` | work-summary scenario/business blocks、capacity length mode、section/chain/paragraph grouping、成熟度观测、章节修复 Prompt | 保留在原 work-summary owner，不机械改成通用算法 |
| D `LEGACY / OUT-OF-SCOPE` | `generator` 中 `is_work_summary_task` 的 PlanningEngine、evidence groups、Writer plan control 分支；`work_summary_writer_call_policy`；structured quality loop | 保留既有 `current` 路径，不拉入正式整篇链 |
| D | `new_draft_prompt_v3`、`section_expansion`、Atomic 中 `planner_owner == work_summary_planner_v1` 的 Content Plan/selection 分支 | 不因本轮增加文种接口而改动，不恢复废弃文章模式 |

审计搜索包括 `is_work_summary_task`、`document_type == "工作总结"`、`work_summary_*`、`PlanningEngine` 与 `FrozenArticlePlan`。完整路径搜索记录保存在 ignored `tmp/multi-genre-commercial-infrastructure/work-summary-call-sites.txt`。

## 修改前推理闸

| 问题 | 结论 |
| --- | --- |
| 1. 实际问题 | Foundation 的 `production_ready` 不能证明完整商业能力，正式调用策略缺少统一准入合同 |
| 2. 当前证据 | BASE 的 Registry、正式 route、Task Assembly、Prompt、quality、旧 structured owners 与离线 fixtures |
| 3. 已验证事实 | full-draft 不用 Frozen Plan，不做质量重写；工作总结是生产参考，report 只有离线证明 |
| 4. 推测 | 其他未来文种可能需要的规划或恢复算法，尚无本轮实现或验收证据 |
| 5. 修改前待确认 | 最终整套离线回归与精确迁移 pin，现已闭合；真实写作质量不在本轮确认范围 |
| 6. 概念创建 | 文种能力由受信任 Adapter 组合创建，不从请求 JSON 创建 |
| 7. 调用者 | Registry production resolution、正式 full-draft route、Task Assembly / Prompt 边界 |
| 8. 消费者 | 共享 Commercial Runtime、既有质量与交付 owner |
| 9. 唯一 owner | 身份 Registry、能力 Commercial Contract、准入 Commercial Gate、调用生命周期 Runtime |
| 10. 同职责模块 | 复用 Foundation Registry/quality boundary，不创建第二注册表或安全引擎 |
| 11. 最小改法 | 新增窄合同与准入，原调用点消费策略和动作词 grouping；不迁移旧算法 |
| 12. 最大回归风险 | 误接旧 Planner/recovery、改变 packet/Prompt/正文或放宽证据边界 |
| 13. 风险阻断 | 独立执行 BASE 完整对象比较、负控制、真实本地 FLOW4、受保护 owner 字节与 P1 AST guards |
| 14. 文章质量 | 不调整写法算法；仅离线行为保持，不声称质量提升 |
| 15. 事实安全 | 文种没有 admission、state、scope、canonical verdict 或 body rewrite 授权接口 |
| 16. 用户可见行为 | 工作总结保持；不完整生产配置更早拒绝；不开放 report 或改 UI |
| 17. 旧路线 | 不恢复 lead_article/full_article/appreciation_article，不复制 structured pipeline |

## 新增商业边界与 Owner

| Owner | 职责与实际消费位置 |
| --- | --- |
| `generation/genres/registry.py` | 唯一身份解析和受信任组合；production resolve 调用商业 Gate |
| `generation/genres/commercial_contract.py` | 冻结 CommercialGenreContract、maturity、planning/evidence/writer/recovery/delivery 合同 |
| `generation/genres/commercial_gate.py` | 完整性、一致性、准入状态；不执行文种回调或事实检查 |
| `generation/commercial_genre_runtime.py` | 正式准入、规则字节验证、packet 校验、单次 Writer 预算、禁止恢复、交付正文一致性、受控诊断 |
| `generation/genres/work_summary_commercial.py` | 工作总结商业参考能力；保留原规划指令和动作词 key 算法 |
| `generation/task_assembly.py` | 创建并校验 reading brief；调用受限 grouping capability，不交出 Evidence authority |
| `generation/official_document_drafting.py` | 固定安全边界和整篇 Prompt 组装；消费文种 planning instruction，原规则字节锁不变 |
| `generation/full_article_quality.py` | 既有共享质量 Runtime：认证 FACT、文章身份、公共收据、文种 evaluator 分派和报告完整性 |
| 既有 Atomic / FACT / FLOW4 / delivery owners | 证据、安全评级、完整正文与交付的原有权威 |

正式 Runtime 请求能力，不识别“这是工作总结所以执行特例”。具体 Adapter 仍由唯一 Registry 组合根注册。`work_summary_commercial` 不是整条 pipeline 的副本。

### 合同所表达的真实能力

- `planning`：`writer_internal`，指令由文种提供；现有任务/篇幅/事实边界内单次 Writer 自主谋篇，不预设 Frozen Plan。
- `evidence_organization`：仅收到不可变动作词 tuple，返回去重且源自输入的 key；不获得 Evidence、source_scope、semantic state 或 admission 对象。Task Assembly 持有全部已 admitted IDs，并验证 brief。
- `writer_control`：`frozen_registry_scope` / `immutable_raw`；文种不能重分证据权限或清理正文。
- `writer_calls`：当前 Runtime 支持 1 次 Writer、0 次材料模型调用、0 次 retry、保留原始空白；逐次调用消费 Runtime 预算。精确 Flash 模型 low reasoning 仍为既有 Provider 配置行为。
- `recovery`：当前正式整篇 `disabled / max_rounds=0 / writer_calls=0`。
- `delivery`：既有 shared full-article FLOW4、原始正文身份、与 Adapter 一致的 TXT/DOCX 格式。
- 检索、写法规则、结构与质量 evaluator 沿用已有 Adapter 字段，不重复创建同职责描述符。

### Fail-Closed Commercial Gate

商业成熟度区分 `REGISTERED`、`OFFLINE_PROOF`、`ENGINEERING_READY`、`PRODUCTION_ENABLED`、`COMMERCIALLY_VERIFIED`。注册、离线证明和工程就绪都不授予生产权限。

生产准入要求：注册身份一致、完整商业合同、受支持的 planning/writer/recovery/delivery 策略、既有 retrieval/drafting/structure/quality 能力有效、规则锁一致、导出格式一致，且商业成熟度为允许生产的状态。Runtime 在 reference/material/Writer I/O 之前调用已有规则加载器验证实际 pinned bytes。

`production_ready=True` 仍不是授权边界。常规 report 保持 `GENRE_OFFLINE_ONLY`；即使修改其 status/production-like flag，缺失商业合同时仍被拒绝。请求 payload 中 `production_ready`、maturity、commercial/capability 等字段无法创建 Adapter 或授予权限；服务重新校验 request，不信任离线构造 context。

`work_summary` 标记为 `PRODUCTION_ENABLED`，没有升级为 `COMMERCIALLY_VERIFIED`。既有工作总结质量 owner 的 `QUALITY_SCORE_STATUS` 和 `COMMERCIAL_QUALITY_LEVEL` 仍为 `NOT_CALIBRATED`；正式完整文章的成熟度和用户修改成本仍要求人工复核，既有报告仍含 `MANUAL_REVIEW_REQUIRED`。基础设施生产准入不等于文章质量合格。

report 的离线架构证明仍消费自己的 retrieval/rules/structure/quality，商业合同为空。低层离线 helper 保留既有行为；这不是生产 Gate 的默认能力补全。新合成文种测试通过独立 Registry 组合能力，验证无需更改共享算法，但不注册为产品文种。

## 事实、质量、恢复与正文身份

共享 `full_article_quality` 先认证 canonical FACT，再调用文种 evaluator；检查 candidate/sidecar/FACT 身份未变，报告 owner、contract、hash 与文章身份匹配，并复算结构、长度、FACT 公共收据。风险事实不能被文种 evaluator 标为通过；缺失、重复或伪造公共检查不能获得安全结论。

本轮保持该既有共享质量 owner 与 `work_summary_quality_gate` 的字节和行为。Genre evaluator 提供文种内容观测，不自授最终安全评级。

`WORK_SUMMARY_RECOVERY_PARITY = NOT_APPLICABLE_WITH_EVIDENCE` 适用于正式整篇链：BASE route 没有 quality recovery 调用，BASE Prompt 明确“不实施第二轮自修”，既有 full-article quality report 的 `automatic_rewrite=False`。本轮将这个已有事实变成 Gate/Runtime 显式禁用策略；文种变更 recovery mode、轮数或调用预算会被拒绝。没有恢复正文或跳过 FACT 重验的授权入口。

旧 `current` 路线继续保留原 `run_work_summary_commercial_quality_loop`：最多 2 轮受限修复，合并后校验 Frozen Plan、structured binding、Writer execution projection，再调用 canonical `apply_fact_safety_gate`、ANALYSIS 与 structured 重验；它不在正式整篇 Runtime 内，相关 owner 不变。本轮不宣称已为其他文种实现同等修复算法。

Must-Deliver 和正文身份仍由原 owner 持有。Writer 返回后第一步保存原始 UTF-8；candidate、事实检查、FLOW4、TXT 与 Delivered Body 保留同一正文。事实风险或检查异常只改变复核交付状态，不丢弃已形成完整文章。Runtime 的正文一致性检查沿用原错误 `FULL_DRAFT_BODY_CHANGED_DURING_DELIVERY`。

## 可观测性与隐私

新增诊断扩展既有 `call_accounting.json`，没有第二状态文件真源。诊断包括 runtime / genre / capability version / maturity / admission、retrieval、planning、evidence organization、drafting rules hash、structure、Writer control/call policy、quality evaluator、recovery 和 delivery identity；canonical FACT 状态与既有 delivery status 同处该调用记录。

诊断仅输出受格式约束的 identity/枚举/hash，不输出规划指令、动作词、原文、证据、Prompt 或文章正文。原 RAG/Atomic trace 脱敏边界不变。

离线执行器在 ignored 工作目录运行，阻断真实模型 transport、Embedding、外部网络和真实资料目录；商业执行器还要求初始 11 个用户文件路径 manifest 存在，否则 fail closed。Windows asyncio socketpair 仅为本地事件循环 IPC，单独计数。测试使用合成材料和临时本地资产，不重新处理真实资料库。

## 行为保持证据与测试记录

`test_commercial_genre_parity.py` 从确切 BASE 通过本地 `git show` 加载并独立执行原模块，不把新 dispatcher 与自身比较：

1. 7 种检索候选规模完整比较排序、选片、文本、限制、trace 和 search 参数。
2. 4 类任务/资料变化完整比较 Atomic anchor、packet、brief、evidence writing view、candidate input；全部 admitted IDs 保留。
3. BASE 原 Prompt 函数对比整个 Prompt 字典及 hash，规则身份与三项完整性锁保持。
4. BASE shared quality dispatcher 和 BASE work-summary evaluator 各自对比完整 bound report。
5. 3 类模型 × 有/无明显事实风险的 6 个正式 route fixture，对比单次 Writer 参数、完整结果、原始/交付正文、candidate、citations、FACT、quality、identity，以及实际本地 TXT/DOCX 段落与样式。
6. 旧 structured owners 字节不变；FrozenArticlePlan、evidence grouping、execution policy、Writer control 实际输出与 BASE 对照相等。
7. BASE 与当前正式 route 的 AST 都不调用旧 Frozen Plan / quality recovery。

所有 fixture 正文均明确为离线合成数据；不构成真实文章质量或黄金样本验收。

### 已执行与中间失败

| 运行/证据 | 结果 | 解释/后续 |
| --- | --- | --- |
| `parity-initial.xml` | 32 passed，10.59 秒 | BASE 完整行为比较，外部调用/真实资料计数为 0 |
| `parity-runtime.xml` | 32 passed，7.77 秒 | 当前 Runtime 落盘后复核，4 项计数为 0；本地 asyncio IPC 12 次 |
| `adjacent-01.log/.xml` | 85 passed / 1 failed | 历史范围/hash guard 尚未录入本次精确迁移 pin；保留失败，不将其当行为通过 |
| `scope-initial.log/.xml` | 36 passed / 1 failed / 1 skipped | 迁移 pin 暂为空时按设计 fail closed；1 个 skip 来自 mutation 参数列表为空，固定 pin 后必须实际执行，不能永久保留此 skip |
| `runner-guard.log/.xml` | 1 passed | 商业离线执行器防护专项 |
| `commercial-01.log/.xml` | 83 passed，12.63 秒 | 商业合同、负控制、完整 BASE parity；4 项计数为 0 |
| `targeted-final.log/.xml` | 163 passed，14.90 秒 | 加入基础能力/规则锁/回调签名负例，连同原 Foundation 三组专项；0 failed / 0 skipped，4 项计数为 0 |
| `scope-final.log/.xml` | 80 passed，22.21 秒 | 10 项精确 pin、38 项受保护 owner、历史保护及未来单字节篡改；0 failed / 0 skipped，4 项计数为 0 |
| `backend-final.log/.xml` | **1027 passed / 0 failed / 2 skipped，155.04 秒** | 全部 backend tests，无 deselect；4 项计数为 0，本地 asyncio socketpair IPC 253 次 |

内部复核还处理了三个收尾问题：Runtime 直接构造最初可能绕过其 factory 的准入，现构造本身也复验注册对象、合同身份及规则字节；FACT 状态最初在 quality 成功后才记录，现紧随 canonical FACT 返回记录，quality 异常仍能准确投影已执行的事实检查；仅检查 callable 不能阻断回调参数不兼容，Gate 现以 `inspect.signature.bind` 检查真实调用形状，不执行回调。对应负例与质量异常 Must-Deliver 测试均已进入最终专项。

已执行的 parity 命令（PowerShell，backend 目录）：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = "$PWD/.venv/Lib/site-packages"
& 'C:/Users/peng chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -B tests/run_multi_genre_offline.py -q tests/test_commercial_genre_parity.py --junitxml=../tmp/multi-genre-commercial-infrastructure/parity-initial.xml
& 'C:/Users/peng chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -B tests/run_multi_genre_offline.py -q tests/test_commercial_genre_parity.py --junitxml=../tmp/multi-genre-commercial-infrastructure/parity-runtime.xml
```

最终收尾命令（相同 backend 工作目录与环境变量）：

```powershell
& 'C:/Users/peng chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -B tests/run_commercial_genre_offline.py -q tests/test_commercial_genre_infrastructure.py tests/test_commercial_genre_parity.py tests/test_multi_genre_foundation.py tests/test_multi_genre_retrieval.py tests/test_multi_genre_drafting_quality.py --junitxml=../tmp/multi-genre-commercial-infrastructure/targeted-final.xml
& 'C:/Users/peng chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -B tests/run_commercial_genre_offline.py -q tests --junitxml=../tmp/multi-genre-commercial-infrastructure/backend-final.xml
```

PowerShell 同时将 stdout/stderr 保存到同名 `.log`。163 项重点测试和 80 项 scope 专项有覆盖交集且都包含在完整套件内，不重复累计。完整套件覆盖 retrieval、task assembly、planning、Writer material/prompt、full article、work-summary quality、Fact Safety、delivery、FLOW4、Stable Asset、P1、P2、P3-A、P3-B。

最终两个 skip 仅为既有 `test_real_run_offline_replay`（P3-A）和 `test_real_run_replay`（P3-B），没有新增真实回放，也没有把 skip 当作已执行验证。原 scope 空 pin 引起的参数占位 skip 已消除。唯一 warning 是既有 Starlette/AnyIO `BlockingPortal` 弃用提示。

```text
REAL_MODEL_CALLS = 0
EMBEDDING_CALLS = 0
EXTERNAL_NETWORK_CALLS = 0
FORBIDDEN_REAL_DATA_ACCESS = 0
```

### 受保护 owner 与历史 scope guards

`tests/commercial_genre_scope.py` 逐个列出本次受保护 owner，与 BASE 字节比较（仅归一化 Git 行尾）；包括 Atomic、FACT、P3、Stable Asset、source parser/fingerprint、material ingestion、共享 Retrieval、FLOW4、immutable article、旧工作总结规划/质量/恢复 owner。P1 核心 AST 检查继续运行。

Task Assembly 的少量分派改动是明确允许的 seam，不是证据权限变更。历史 guard 保留原 BASE 和原 hash；本次候选使用新增精确内容 pin，并以 BASE 完整输出 parity 和未来字节篡改失败证明约束仍有效。不会以目录 wildcard 或运行时自动取 hash 放宽保护。

10 项生产文件精确 pin 已固定；38 个受保护 owner 与 BASE 的原字节比较、单字节变异负例、历史 pin 不变均已通过最终 scope 专项。完整回归结果见测试记录。

## Stable Asset 影响与限制

- Stable Asset、source substrate/parser、material ingestion/fingerprint、Atomic 的 owner 不在修改范围。最终 scope 与完整套件均通过，代码和合同不要求重处理真实资料库。
- 没有真实 Writer、Embedding、外部服务或重新处理真实资料；不宣称真实质量校准、黄金样本比较或商业评分完成。
- 本轮正式 planning 能力是已存在的 Writer 内部谋篇；不虚构 Frozen Plan 已被正式整篇路线使用。
- 当前 Runtime 只支持已验证的一次 Writer / 零重写策略。未来恢复需要共享 lifecycle、版本变更和 canonical FACT 重验的独立实现及验收，不能靠文种开关启用。
- `**一、标题**` display heading fallback 仍为 backlog；没有改 canonical body、事实审查分段或前端投影。
- report 仍为离线证明；没有开放其他产品文种或前端入口。

## 文件清单、Git 与回滚边界

以下是本轮已确认任务文件；原有 11 个用户未跟踪文件不在此清单内。

生产修改（6）：

```text
backend/app/services/generation/full_draft_generation.py
backend/app/services/generation/genres/contracts.py
backend/app/services/generation/genres/registry.py
backend/app/services/generation/genres/work_summary.py
backend/app/services/generation/official_document_drafting.py
backend/app/services/generation/task_assembly.py
```

生产新增（4）：

```text
backend/app/services/generation/commercial_genre_runtime.py
backend/app/services/generation/genres/commercial_contract.py
backend/app/services/generation/genres/commercial_gate.py
backend/app/services/generation/genres/work_summary_commercial.py
```

测试修改（6）：

```text
backend/tests/multi_genre_scope.py
backend/tests/run_multi_genre_offline.py
backend/tests/test_material_ingestion.py
backend/tests/test_multi_genre_foundation.py
backend/tests/test_production_task_assembly.py
backend/tests/test_rag_observability.py
```

测试新增（5）：

```text
backend/tests/commercial_genre_scope.py
backend/tests/run_commercial_genre_offline.py
backend/tests/test_commercial_genre_infrastructure.py
backend/tests/test_commercial_genre_parity.py
backend/tests/test_commercial_genre_scope.py
```

文档新增：`docs/MULTI-GENRE-COMMERCIAL-INFRASTRUCTURE.md`。证据与运行缓存位于 ignored `tmp/multi-genre-commercial-infrastructure/`；不是要提交的运行产物。

最终核验以 ignored `final_verify.py` 和 `final-scope-verification.json` 保存：HEAD/main 仍为 BASE，分支正确，staged files = none；22 个任务文件（10 生产、11 测试/runner、1 文档）均能严格 UTF-8 解码，`git diff --check` 通过；原 11 个用户未跟踪路径仍保留且不在修改清单。

Git 状态为 12 个已跟踪任务文件修改、10 个任务新文件、另有 11 个原用户未跟踪文件。修改前后 commit 均为 BASE；没有 staged 范围。只读 `git submodule status` 因现有 PortableGit 缺少 `basename/sed/git-sh-setup` 无法运行，已用 `git ls-files --stage` 的 160000 模式检查和 `.gitmodules` 跟踪检查确认无 gitlink/submodule 配置；未安装工具或更改环境。

本轮没有 commit message，因为用户禁止创建 commit；没有 push / PR / merge。

可执行回滚只针对上列任务文件：先保存本次 diff 和新增任务文件供复核，再对逐项确认的已跟踪文件使用 `git restore --source=7672e69ac60ea96b5610a59f77cc4c4c58894cfa -- <明确任务路径>` 恢复工作区，不操作 index。新增文件必须逐一路径确认并取得用户删除授权后移除。禁止 `git clean`、批量 reset、目录删除或覆盖原 11 个用户文件。本轮未执行回滚。

## Done Contract 工程结论

| 验收条件 | 结论与证据 |
| --- | --- |
| Foundation 保留、Runtime/Contract/Gate 存在且 fail closed | YES；原 Foundation 专项、新准入负例、真实 HTTP/service 双重校验通过 |
| Work-summary 生产参考能力与行为保持 | YES；完整 BASE 检索、packet、谋篇/分组、Writer 参数、Prompt、quality、FACT、正文及 FLOW4/DOCX 对照 |
| Retrieval / planning / evidence group / Writer control / Prompt / quality parity | 全部 YES；包括当前正式语义及未修改旧 structured owner 的独立对照 |
| Recovery parity | NOT_APPLICABLE_WITH_EVIDENCE；正式链原本禁用，Gate 和 Runtime 继续零轮、零重写；旧 structured owner 保留 |
| Report offline proof / commercial ready / gate bypass | YES / NO / CANNOT_BYPASS；仅改 flag、请求伪造或不完整合同均拒绝 |
| 新合成文种无需共享算法分支 | YES；仅测试 Registry 组合，独立 planning/grouping 实际执行，共享源码 hash 前后相同 |
| Genre 不可覆盖 FACT / recovery 不可绕过 FACT 重验 | YES；恶意 quality verdict 被拒绝；陈旧正文报告被拒绝；没有可授权恢复的入口 |
| Fact Safety / Must-Deliver / body identity | SHARED / SHARED / PRESERVED；风险和质量检查异常仍交付完整原文 |
| P1 / P2 / P3-A / P3-B 改动 | 全部 NO；字节/AST/相邻与完整回归证据 |
| Stable Asset owner / 重处理 | UNCHANGED / NOT_REQUIRED |
| Runtime 身份及脱敏 | YES；既有 accounting 投影能力身份与真实状态，不输出原文 |
| 重点 / 完整 offline suite | PASS / PASS；全部最终范围无临时 deselect |
| add / commit / push / PR | NONE / NO / NO / NO |
| 阻断 / 已知限制 | 无工程阻断；真实质量未重新验收，report不商业开放，显示标题问题继续backlog |

`READY_FOR_CHATGPT_REVIEW = YES` 仅表示本工单基础设施工程与离线验收已闭合，不替代后续人工代码审查或真实质量验收。
