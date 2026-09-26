# P3-B：Claim / Evidence 主体与状态边界验收

任务：`P3-B-CLAIM-EVIDENCE-SUBJECT-BOUNDARY`。日期：2026-09-26。

本文是本轮实现和离线验收记录，不是新的项目规则真源，不宣布真实模型成文质量已达黄金样本标准。

## 基线与边界

- 项目：`D:\AI_Projects\gongwen-style-agent-commercial`。
- BASE / 修改前 HEAD：`cdf06f48f390925d3bbe2d3c9d13d67935f6b903`。
- 分支：`codex/p3b-claim-evidence-boundary`；仅创建本地分支。
- 开始时 tracked diff / staged 均为空，存在 11 个用户未跟踪文件。
- 修改生产代码前，将历史 Run 19 个文件、用户未跟踪 11 个文件、全部 tracked 文件的 SHA-256 记录于 ignored `tmp/p3b-claim-boundary/baseline-hashes.json`。
- 用户本轮明确禁止 add / commit / push / PR / merge，优先于通常的 checkpoint / 提交流程。本轮 checkpoint 是上述 HEAD、独立分支与基线 hash 快照，没有创建 commit。
- 生产改动限定为本文列出的 5 个文件；FullArticle schema、正文存储、P1/P2/P3-A、RAG、Retriever、Stable Asset parser/fingerprint、Embedding、前端、数据库和真实资料均未修改。

## 真实生产 owner 与根因

实际链路为 `/api/generation/generate` 的 `full_article_drafting` 请求 → `full_draft_generation.generate_full_draft()` → 既有检索与 Task Assembly / 冻结 Registry → 单次 Writer → 原文落盘 → `import_full_article()` → `propose_exact_citations()` → `evaluate_full_article_facts()` → Quality → FLOW4 handoff/render → `full_article_review_result()`。

`propose_exact_citations()` 只产生 `EXACT_WHOLE_UNIT`。这是正确的引用合同，但旧版全文 FACT adapter 只对有 Citation 的单元调用 canonical validator，导致没有逐字引用的自然改写直接成为 `UNBOUND_OR_UNCLASSIFIED`。指定历史 Run 的原报告中，40 个单元全部进入该通用状态，citation proposals 为空。

已有 `_evaluate_evidence_safety_fact()`、`_bound_state_check()`、`_unsupported_hard_details()` 等函数才是事实安全 owner。P3-B 复用并扩展它们，不引入第二套 LLM 或独立事实判定器。

| 文件 | 本轮职责 |
| --- | --- |
| `backend/app/services/generation/full_article_candidate_adapter.py` | 新增 `discover_safety_candidates()`；原 exact citation 函数保持不变 |
| `backend/app/services/generation/flow3_v3_trace.py` | 候选接入、逐证据 canonical 检查、状态消费边界、主体/辖区兼容、无引用结果/因果检查、位置化风险报告 |
| `backend/app/services/generation/official_document_drafting.py` | Writer 预防性边界、完整成文及正文外复核提示 |
| `backend/app/services/generation/delivery_decision.py` | 将具体 risk codes / findings 投影到正文之外的 risk_items |
| `backend/app/services/flow4/integrity_gate.py` | 验证独立安全候选校验记录的传输完整性；不增加事实判定引擎 |

最后一项是生产链闭环所必需的最小增补：旧完整性检查断言“没有 Citation 的单元不得 SUPPORTED”，与本轮候选/引用分离目标冲突。现在仍校验 Citation 身份、位置、摘要、报告绑定，并允许**唯一合法候选、正确 scope、匹配的 canonical 通过记录且无风险**的非引用支持。没有破坏性 schema 修改。Delivery 仍会重现 Quality / FACT 校验记录，伪造报告不能靠填 hash 获得安全通过。

## 设计与推理闸结论

1. **候选不是引用，也不是支持。** 发现只遍历冻结 Registry 中的已有合法 ID，复用 `_source_related()` 获取所有可能相关的来源；不排序选 Top1，不调用 Embedding，不合并证据。Citation 的 `evidence_ids`、`scope_id` 仍只来自引用侧车；新候选使用独立 `candidate_evidence_ids`、`candidate_scope_id`、`candidate_checks`。
2. 短于四个规范字符的孤立分句不用于候选发现，避免“下一步”仅凭共享“一步”匹配“进一步……”材料。原完整单元仍接受覆盖审查及无引用结果扫描，没有删去正文或审查范围。
3. 无引用时，只有唯一候选才能进入支持判定。多候选（包括重复 ID 对应相同来源、跨 scope 或冲突来源）保持 `AMBIGUOUS_EVIDENCE / REVIEW_REQUIRED`。不会因某一候选得分更高或校验通过而强选它。
4. 唯一候选仍必须通过 `_evaluate_evidence_safety_fact()`：来源绑定、scope、状态、主体/辖区、hard detail、结果/因果、可写性与限制逐层生效。相似度只服务发现，不是 entailment 或通过依据。测试以 canonical veto 证明候选匹配不能自动 SUPPORTED。
5. 对全文支持增加保守表面证明：仅接受明确的有限同义/语法变换，例如“已经/已”“和/与”“明确了/明确”、职责句“是/为”以及保留 PLAN 的检查组本义展开。仍保留数字、否定、主体、强度、时间和其他动作。无法证明的其他自然语言改写保留 Review，不用模糊相似度放行。
6. 多证据须显式声明同一 scope；每个成员分别 materialize、校验状态/主体/细节，正文额外分句也必须被成员覆盖。不能通过“已包含 A 和 B”授权第三个无来源事实。
7. 主体检查使用 canonical_subject、source_span、冻结来源 metadata 与 task.level / org_type。组织类型与行政层级采用通用映射；保留来源的明确本地主体时，不把“被检查企业”等组织宾语误当成主语。来源“我们/本单位”缺乏身份映射、元数据冲突或辖区不明时保守 Review。
8. P3-A 是唯一状态分类真源。`_bound_state_check()` 仅修消费端对目的/要求谓词的误 completion_upgrade；依赖 P3-A 的 modality/completion markers，并同时检查独立结果分句。PLAN、RECOMMENDATION、ONGOING、PARTIAL、NOT_IMPLEMENTED、PROBLEM/GAP、UNKNOWN、CONTEXT 均不能借候选进入无依据完成/结果态。COMPLETED 不授权更强结果；结果事实需要合法 RESULT Evidence。
9. 不管有没有 Citation，结果和因果表达都被扫描。无法由对应合法证据通过 canonical 检查时，输出 `UNSUPPORTED_RESULT` 或 `UNSUPPORTED_CAUSALITY`。诊断性分句探针只定位风险，不创建 Citation、不跨 scope 拼接支持、不改变整句支持状态。
10. Writer Prompt 只是预防。post-generation checker 可以独立阻断安全通过；不调用二次 Writer/Fact Checker，不自动改写正文。事实风险只降低安全评级，不丢文章。

最大回归风险是模糊匹配冒充支持、去限定后的状态升级、组织宾语误判为主体、多证据洗白新谓词和完整稿被拦截。专项正反例、伪造报告测试、真实 Run 回放、P1/P2/P3-A guard 与全后端离线回归用于阻断这些风险。

## 历史 Run 只读回放

Run：`backend/output/full-draft/2e09c781576545a681a1ee206642618a`。

旧 Registry 来自 P3-A 之前。测试只在内存中复制 Registry，使用当前 `atomic_evidence.classify_semantic_state(source_span)` 更新 replay 的状态约束；来源 span、evidence ID、scope、正文不变，不写回历史文件。这是消费端验收输入，不是 Stable Asset 迁移，也没有回改 P3-A classifier。

| 已知问题 | 精确分类 |
| --- | --- |
| 将组织联合检查组 → 组织联合检查组 | SEMANTIC_STATE_UPGRADE |
| 必须建立严格闭环机制 → 建立严格闭环机制 | SEMANTIC_STATE_UPGRADE |
| 要常态化开展五进 → 常态化开展五进 | SEMANTIC_STATE_UPGRADE |
| 对各乡镇跟踪问效被写成本级工作 | JURISDICTION_SCOPE_MISMATCH |
| 公司上下 → 全镇上下 | SUBJECT_SCOPE_MISMATCH |
| 取得阶段性成效 | UNSUPPORTED_RESULT |
| 重大隐患完成整改销号 | UNSUPPORTED_RESULT |
| 主体责任意识明显增强 | UNSUPPORTED_RESULT |
| 自查自纠能力有所提升 | UNSUPPORTED_RESULT |
| 未发生较大及以上生产安全事故 | UNSUPPORTED_RESULT |

`KNOWN_ISSUES_SPECIFICALLY_CLASSIFIED = 10/10`。每一项断言对应文本及该文本上的具体 finding；不是只验证整句有某个 generic Review。另验“公司产业重组 → 全镇产业重组”主体转移。

- 原报告：40 / 40 单元 generic UNBOUND。
- P3-B：40 / 40 单元保持审查覆盖；33 个单元发现安全候选；generic UNBOUND 剩余 7 个，其中 5 个章节标题、1 个编号引导句、1 个问题段过渡句。
- 原始风险正文的 SUPPORTED 单元数仍为 0：这篇文章没有因候选发现被包装成安全稿。
- 三个用户指定安全改写，用历史 Registry 的真实单条合法记录分别重放，`SUPPORTED = 3/3`。放回完整 66 条 Registry 后，安全 PLAN 改写仍可 `SUPPORTED`；另两项因重复/其他相关候选保持 Review，不强行选 ID。
- 原正文经过实际 FACT → Quality → FLOW4 → Delivery，`delivery_status=SUCCESS_WITH_REVIEW`，`article_exists=true`，`article_recoverable=true`，`official_success=false`，`article_qualified=false`，`user_confirmation_required=true`。
- 返回 45 个风险/质量复核项目；具体风险保留在 fact_report / risk_items，正文没有新增内部诊断或占位模板。
- RAW Writer / Candidate / Delivered Body 的 UTF-8 bytes 相等，正文 SHA-256 为 `8d30f8454af092ba8c2fa4e8169cb7a329e2a5e4f95d62847e426f31c7095e2d`。
- 详细 replay 与摘要保留在 ignored `tmp/p3b-claim-boundary/`，不将私有整稿和 Registry 放入版本管理。

## 测试、失败与修复记录

测试执行器：`backend/tests/run_p3b_offline.py`。运行时和缓存隔离到本轮 ignored tmp；真实资料库/上传目录/环境文件禁止访问，历史 Run 仅可读，用户原有未跟踪文件禁止访问。

真实 `httpx.HTTPTransport` / `AsyncHTTPTransport`、Embedding API 和 socket 外网连接均被拦截。Provider 回归使用原有内存 HTTP/SSE 测试替身，ASGI TestClient 不外发。Windows asyncio 的 socketpair 仅放行调用栈可证实的 loopback IPC，不能用于真实 HTTP 请求。

原 venv 的 Microsoft Store Python 入口在 sandbox 中不可执行，使用现有 bundled Python 3.12.14 加载项目既有 site-packages；没有安装或升级依赖。

| 轮次 | 实际结果 | 处理 |
| --- | --- | --- |
| 专项红灯 `targeted-red.log` | 41 failed / 9 passed | 证明原始缺口 |
| 第一轮 `targeted-01.log` | 2 failed / 48 passed | 修复职责句 place 提取误报；解除 FLOW4 对 Citation/支持的错误耦合 |
| `targeted-02.log`、`targeted-03.log` | 各 50 passed | 初始边界闭环 |
| 扩展 `targeted-04.log` | 1 failed / 64 passed | 已经逐成员支持的多证据句，被其他候选的诊断误覆盖；修正诊断与支持职责 |
| `targeted-05.log` | 65 passed | 多证据正反例闭环 |
| `adjacent-01.log` | 2 failed / 359 passed | 旧历史文件逐字冻结 guard 与本轮合法改动冲突 |
| 完整首跑 `backend-01.log` | 83 failed / 691 passed / 8 errors | 隔离器在 generate_text 层误拦内存 Provider 测试；socketpair 包装导致 IPC 误判；另有历史 guard 冲突 |
| `targeted-06.log` | 154 passed | 调整到真实传输边界，Provider 原测试与 P3-B 均通过 |
| `backend-02.log` | 787 passed | 当时版本全后端通过 |
| `targeted-final.log` | 73 passed | 补充主体宾语、元数据冲突、真实交付回放 |
| `backend-final.log` | 2 failed / 787 passed | 执行中继续修短过渡语发现规则，磁盘 hash 与已加载 pin 不同；保留该失败并重新完整运行 |
| `targeted-final-02.log` | 73 passed | 最终短过渡语规则及完整 Registry 正向支持通过 |
| `backend-final-02.log` | 789 passed / 0 failed / 0 skipped，124.28 秒 | 最终固定代码的完整回归；包含全部相邻回归 |
| `backend-final-03.log` | 789 passed / 0 failed / 0 skipped，122.38 秒 | 清理旧 Prompt 中 UNKNOWN 结果授权的歧义后，最终完整回归通过 |

首跑日志中的 `real_model=75` 是旧隔离器在进入任何 HTTP 前误拦的合成 Provider 调用，`external_network=8` 是栈追踪记录为 `127.0.0.1` 的 socketpair IPC；不是已执行真实模型或外网调用。修正后的 targeted / backend 日志分别记录真实边界计数为 0。中间日志完整保留，没有删除或修改失败结果。

历史保护测试没有整体跳过或移动 BASE_SHA：新增 `p3b_boundary_scope.py` 只锁定这 5 个生产文件的精确 hash；4 个旧 guard 读取这个显式本轮范围，其余仍与原历史版本比对。另有内存单字节变更反例，确认这些 guard 仍拒绝未来改动；P1/P3-A 原 guard 和变异测试保留。

实际命令（PowerShell，从项目根目录执行）：

```powershell
$p3bPython = 'C:/Users/peng chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
Set-Location 'D:/AI_Projects/gongwen-style-agent-commercial/backend'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = "$PWD/.venv/Lib/site-packages"
& $p3bPython tests/run_p3b_offline.py --real-run -q tests/test_p3b_claim_evidence_boundary.py
& $p3bPython tests/run_p3b_offline.py --real-run -q tests/test_flow3_v3_atomic_evidence_generation.py tests/test_fact_safety_delivery_policy_reset.py tests/test_flow4_dev002_integrity_gate.py tests/test_flow4_dev003_renderer.py tests/test_flow4_dev004_artifact_delivery.py tests/test_p3a_semantic_state_recognition.py tests/test_production_task_assembly.py tests/test_writer_material_view.py tests/test_material_digest_task_assembly.py tests/test_writing_reference_pack_02b1.py
& $p3bPython tests/run_p3b_offline.py --real-run -q tests
Set-Location 'D:/AI_Projects/gongwen-style-agent-commercial'
git diff --check
git diff --cached --name-only
git status --short
```

最终 suite 数量按单次全量执行计数，不把重复重跑累加为“通过总数”。未运行真实模型生成、真实 Embedding、外部联网、真实资料处理、前端构建或真实浏览器测试；本轮无前端修改。真实黄金样本成文质量不是这次离线边界验收的结论。

## 已知限制与 Stable Asset 影响

1. 确定性中文发现与支持证明不是通用自然语言语义证明。有限同义变换以外的安全句子可能继续 Review，不能用相似度替代核验。
2. 多来源/重复来源不去重授权；完整 Registry 的另外两个安全例仍因歧义 Review。后续若扩展消歧，须独立设计可证明的主体、限定和 scope 合同。
3. 无可验证身份的第一人称、来源 scope 元数据冲突等保持 Review。通用机构与辖区词表不是完整组织知识图谱。
4. 单元级风险与质量必须人工复核；本次风险原稿仍为完整风险稿，未静默修正文风或事实。
5. 既有 Starlette / AnyIO 弃用警告不影响本轮测试结论，没有升级依赖。
6. P3-B 未改 parser / fingerprint owner，`NO NEW REPROCESS REQUIREMENT`。源码核对 `stable_material_assets._binding().rules` 的依赖只有 Atomic parser、source_substrate、material_digest、stable_asset，这四个文件相对 BASE 的 hash 全部相同；本轮 5 个修改 owner 不在指纹输入中。仍保留 P3-A 已产生的一次待迁移；没有执行真实 Stable Asset 重处理。

## Git 与回滚

生产文件为上述 5 个。测试新增 `backend/tests/test_p3b_claim_evidence_boundary.py`、`backend/tests/run_p3b_offline.py`、`backend/tests/p3b_boundary_scope.py`；测试修改 `test_production_task_assembly.py`、`test_material_digest_task_assembly.py`、`test_material_ingestion.py`、`test_stable_material_assets.py`。其他新增文件仅本文；日志/计划/快照在 ignored tmp 中。

回滚范围是这 5 个生产文件、4 个旧测试中的本轮 guard 接入，以及 3 个新测试文件和本文。无 commit，因此不能使用 git revert。后续用户决定回滚时，应先核对本轮最终 hash 和 diff，再逐文件恢复到 BASE_SHA；若已出现后续用户改动，只反向应用本轮差异。新文件按明确清单移出工作区。不要对整个仓库执行 reset/clean，不碰 11 个原有未跟踪文件、历史 Run、真实资料或 P3-A 实现。本文仅描述回滚入口，没有执行恢复或删除。

最终 Git / hash 证据：`tmp/p3b-claim-boundary/final-verification.json`。最终工作树保留本轮未暂存差异；staged=0；HEAD 与 BASE 相同；无新 commit / push / PR / merge。

## 最终报告字段

```text
TASK = P3-B-CLAIM-EVIDENCE-SUBJECT-BOUNDARY
BASE_SHA = cdf06f48f390925d3bbe2d3c9d13d67935f6b903
BRANCH = codex/p3b-claim-evidence-boundary
ROOT_CAUSE_CLOSED = YES (P3-B implementation and offline replay scope)
PRODUCTION_FILES_CHANGED = 5 (listed above)
TEST_FILES_CHANGED = 7 (3 new, 4 historical guards)
OTHER_FILES_CHANGED = docs/P3-B-claim-evidence-subject-boundary.md
SUPPORT_DISCOVERY_OWNER = full_article_candidate_adapter.discover_safety_candidates
FINAL_FACT_AUTHORITY = flow3_v3_trace._evaluate_evidence_safety_fact
EXACT_CITATION_CONTRACT_PRESERVED = YES
PLAN_UPGRADE_BLOCKED = YES
RECOMMENDATION_UPGRADE_BLOCKED = YES
ONGOING_UPGRADE_BLOCKED = YES
PARTIAL_UPGRADE_BLOCKED = YES
NOT_IMPLEMENTED_UPGRADE_BLOCKED = YES
UNKNOWN_STRONG_RESULT_BLOCKED = YES
SUBJECT_TRANSFER_DETECTED = YES
JURISDICTION_TRANSFER_DETECTED = YES
UNSUPPORTED_RESULT_WITHOUT_CITATION_DETECTED = YES
UNSUPPORTED_CAUSALITY_WITHOUT_CITATION_DETECTED = YES
SAFE_PARAPHRASE_SUPPORTED = YES
SAFE_PARAPHRASE_SUPPORTED_COUNT = 3 (single authorized real records); 1 (full 66-row Registry)
AMBIGUOUS_SUPPORT_FAILS_CLOSED = YES
REAL_RUN_REPLAY = READ_ONLY_PASS
KNOWN_ISSUES_SPECIFICALLY_CLASSIFIED = 10/10
GENERIC_UNBOUND_REMAINING = 7/40 (baseline 40/40)
WRITER_PROMPT_STATE_BOUNDARY = YES
WRITER_PROMPT_SUBJECT_BOUNDARY = YES
WRITER_PROMPT_RESULT_BOUNDARY = YES
PLACEHOLDER_POLICY_ALIGNED = YES
P3A_CLASSIFIER_CHANGED = NO
P1_CHANGED = NO
P2_CHANGED = NO
RAG_CHANGED = NO
STABLE_ASSET_OWNER_CHANGED = NO
FULL_ARTICLE_DELIVERY_PRESERVED = YES
RISK_ARTICLE_DELIVERY_STATUS = SUCCESS_WITH_REVIEW
BODY_PRESERVED = YES (byte-identical)
REAL_RUN_FILES_UNCHANGED = YES (19/19 SHA-256)
USER_UNTRACKED_FILES_UNCHANGED = YES (11/11 SHA-256)
TEST_COMMANDS = see exact PowerShell commands above
TOTAL_PASSED = 789 (final backend suite; targeted 73 included)
TOTAL_FAILED = 0
TOTAL_SKIPPED = 0
ALL_TARGETED_TESTS_PASS = YES
ALL_ADJACENT_REGRESSION_PASS = YES (included in full suite)
GIT_DIFF_CHECK = PASS
REAL_MODEL_CALLS = 0
EMBEDDING_CALLS = 0
EXTERNAL_NETWORK_CALLS = 0
STAGED_FILES = NONE
COMMIT_CREATED = NO
PUSHED = NO
PR_CREATED = NO
BLOCKING_FINDINGS = NONE (P3-B offline scope)
NON_BLOCKING_FINDINGS = finite paraphrase coverage; ambiguity/identity review; existing dependency warning
STABLE_ASSET_REPROCESS_REQUIRED_AFTER_P3B = NO NEW REPROCESS REQUIREMENT; P3-A one pending migration retained
P3B_ROOT_CAUSE_CLOSED = YES
NATURAL_PARAPHRASE_NO_LONGER_ALL_NO_BINDING = YES
CANDIDATE_MATCH_IS_NOT_CITATION = YES
CANDIDATE_MATCH_IS_NOT_AUTOMATIC_SUPPORT = YES
CANONICAL_FACT_VALIDATOR_REMAINS_FINAL_AUTHORITY = YES
RISK_ARTICLE_STILL_DELIVERED_COMPLETE = YES
INTERNAL_DIAGNOSTICS_NOT_IN_BODY = YES
NO_WHOLE_ARTICLE_PLACEHOLDER_TEMPLATE = YES
READY_FOR_CHATGPT_REVIEW = YES
```
