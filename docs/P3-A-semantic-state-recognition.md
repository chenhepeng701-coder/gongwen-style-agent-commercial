# P3-A-SEMANTIC-STATE-RECOGNITION 验收报告

日期：2026-09-26。角色：本次实现与离线验收记录，不是新的项目规则真源。

P3-A 已按 ChatGPT Review 的两个阻断项完成 R1 修订，限定离线回归通过，停在等待再次 ChatGPT Review 的阶段。没有提交、推送、PR、合并或资料库重处理。本文不宣布 P3-B 或整体文章质量问题解决。

## 基线与边界

- 项目：`D:\AI_Projects\gongwen-style-agent-commercial`。
- BASE_SHA / 当前 HEAD：`72af33cf4e6283c04c18ab87a30e2b506471feaa`。
- 分支：`codex/p3a-semantic-state-recognition`，从上述 BASE_SHA 创建。
- 初始受控文件无修改，暂存区为空。已有用户未跟踪补丁、压缩包、手动 Writer 测试文件未触碰。
- 已检查 ignored 目录及嵌套仓库；`tmp/baseline-02b-r2r3` 下存在三个历史测试克隆，未作为真源使用。
- 初始七例分类、HEAD 和真实 Run 全部文件哈希已保存于 `tmp/p3a-semantic-state/checkpoint.json`。本轮明确禁止 commit，因此没有创建 checkpoint commit。
- 仅修改一个生产文件：`backend/app/services/generation/atomic_evidence.py`。
- AST 审计确认既有函数只改变 `classify_semantic_state()`、`_predicate_start()`，新增 `_completion_signal_matches()`；状态枚举、准入、下游传播、Fact Safety 方法均未修改。

## 根因与实现决策

已通过当前源码和未改动 BASE_SHA 的执行确认：要求情态缺失、谓词起点只识别少量紧邻前缀、裸完成词可在名词/目的表达内触发完成态；“将组织”虽然已有 PLAN 分类，但情态元数据为空。上述均是 Atomic owner 的源头识别问题。

要求态继续使用 RECOMMENDATION，目的/目标表达使用 PLAN，未增加枚举或改变状态合同版本。共享的情态前缀、修饰语和谓词模式用于分类与谓词边界；“要”必须处于分句/显式词边界或已识别的主语中心词之后，并紧接谓词结构。完成态要求“已/已经”或动后“了”等完成体依据；裸动作词不能独立证明完成，不再依据名词后缀排除表或 `_verb_positions()` 猜测完成。 “已圆满完成”“完成了”等明确完成形式保持有效。状态优先级和仅 UNKNOWN 触发 evidence_type fallback 的规则未改变。

修改前推理边界如下：

| 闸项 | 结论 |
| --- | --- |
| 实际问题 | 确定性源头语义识别错误，不是 Writer 成文问题 |
| 当前证据 | BASE_SHA 源码、七例未修改分类器输出、真实 Run 中保存的 source span |
| 已验证事实 | 原始七例旧状态与工单一致，真实 Run 产物存在 |
| 推测 | 不预设整库影响数量或成文质量改善幅度 |
| 待确认 | ChatGPT Review、External Review、Git Closeout、后续合并与独立迁移授权 |
| 概念创建者 | Atomic 的 SemanticState 与 classify_semantic_state |
| 调用者 | 原子提取、材料语义读取、既有状态一致性检查 |
| 消费者 | Registry、material packet、Writer view、下游事实校验 |
| 唯一 owner | atomic_evidence.py |
| 同职责实现 | 已有识别器可修，不新增第二个生产分类器 |
| 最小改法 | 正向情态边界、完成体依据、独立谓词的情态作用范围；不改变入锚和传播规则 |
| 最大风险 | 真完成态被削弱、混合句状态丢失、元数据漂移、Guard 失效 |
| 阻断手段 | 真完成对照、所有既有状态、混合状态、反向 fallback、原样传播与负向 Guard 测试 |
| 文章质量 | 不运行 Writer，不声称文章质量已经验收 |
| 事实安全 | 不放宽策略；要求/目的不升级为完成/成果 |
| 用户可见影响 | 后续重处理后，新源头状态及元数据改变；省略主体不能再把情态误充主体 |
| 旧模式 | 不引入或恢复旧路线 |

## ChatGPT Review R1 修订

首轮 478 项离线测试通过后，ChatGPT Review 给出 CHANGES_REQUIRED。本轮复现了全部新增反例，并在 `tmp/p3a-semantic-state/review-r1/` 保存修改前文件副本、前后分类结果及本轮日志。首轮测试通过不能代替本次 Review 的阻断结论。

1. 移除“要”的单字负向排除表。单字情态的左侧必须是显式边界或已识别的主语中心词，右侧必须是可选修饰语加谓词。分类、modality markers 与 `_predicate_start()` 共用这一条件。未知汉字前缀不被自动认作独立主语。测试同时覆盖“提要/概要/纪要/摘要/纲要/主要/重要”和添加“工作/各部门工作”后的复合表达，避免只修单个词。
2. 删除 `_COMPLETION_NOUN_SUFFIX_RE`。COMPLETED 要求明确完成体；“完成/形成/达到”等裸词不能单独区分事件谓词与名词化表达，不再用于推定完成。裸“完成年度任务/形成工作机制/基本完成任务/达到90%”在没有其他信号时保守为 UNKNOWN；若 evidence_type=result，仍按原合同回退 RESULT。这一取舍已显式加入测试，不宣称进行了通用中文句法解析。
3. 用谓词协调边界限定局部情态范围。“并/并且/同时/随后”等连接明确完成谓词时，前一个分句的 PLAN 不会删除后一个分句的 completion marker。整句状态优先级保持原合同。
4. 原有 P2 SHA、所有历史 BASE_SHA、P2 准入逻辑和所有下游生产 owner 均不变。只更新 P3-A 固定内容 SHA 与固定 recognition delta。

| Review 用例 | 修订后结果 |
| --- | --- |
| 提要形成后报审 | UNKNOWN，要求/完成标记为空 |
| 概要形成后报审 | UNKNOWN，要求/完成标记为空 |
| 纪要形成后印发 | UNKNOWN，要求/完成标记为空 |
| 主要工作已完成 | COMPLETED，保留“已完成” |
| 各部门要形成工作合力 | RECOMMENDATION |
| 我们必须建立闭环机制 | RECOMMENDATION |
| 汇报项目完成情况 | UNKNOWN，不是 COMPLETED |
| 项目完成情况总体较好 | UNKNOWN，不是 COMPLETED |
| 围绕年度目标完成情况开展分析 | UNKNOWN，不是 COMPLETED |
| 项目完成率达到90% + evidence_type=result | RESULT，不是 COMPLETED |
| 已完成年度任务 / 完成了整改任务 / 已形成工作机制 | COMPLETED |
| 要形成工作合力 | RECOMMENDATION |
| 计划开展培训并已完成档案登记 | PLAN，completion_markers=["已完成"] |

“要”的正向主语规则是保守的名词中心词边界识别，并非覆盖所有专名/省略句的分词器；无法确认的边界不猜测。没有增加“提/概”等负向字符，也没有增加“情况/率/进度/程度”等名词后缀排除项。

## 真实 Run 离线重放

Run：`2e09c781576545a681a1ee206642618a`。仅读取已有 `material_packet.json` 中的 Registry，不重新检索、不调用 Writer、不调用 Embedding。每条 source span 都校验已有 SHA-256，再分别执行未改动 BASE_SHA 分类器和新分类器；额外验证 `evidence_type=result` 不覆盖新状态。

| 结果字段 | 真实 evidence_id | 旧状态 → 新状态 |
| --- | --- | --- |
| REAL_CASE_1 | EV-64C8D756547C | PLAN → PLAN |
| REAL_CASE_2 | EV-5BF16B617858 | UNKNOWN → RECOMMENDATION |
| REAL_CASE_3 | EV-07A1DDDE03F4 | UNKNOWN → RECOMMENDATION |
| REAL_CASE_4 | EV-7CEDF40341ED | UNKNOWN → RECOMMENDATION |
| REAL_CASE_5 | EV-14614BDFF31D | COMPLETED → RECOMMENDATION |
| REAL_CASE_6 | EV-4E55000C7B67 | COMPLETED → RECOMMENDATION |
| REAL_CASE_7 | EV-745B9167802E | COMPLETED → PLAN |

七例全部匹配。Run 下 19 个文件的前后哈希完全一致，且与修改前 checkpoint 一致。日志记录每例 source span 哈希；报告不复制其他私有材料。

## 传播与受保护 owner

专项测试通过真实 Atomic → Registry → FullDraftMaterialPacket → Writer material view，并用独立 wire decoder 还原全部字段。创建 Atomic/材料词法读取结果后，测试把再次调用分类器设为失败条件，确认后续只复制状态和语义约束。还覆盖 P2 的 SOURCE_STATE_CONFLICT、不可用状态不可被静默裁剪，以及来源限制保留。

传播正例提供明确的合成主体。对四种省略主体的原始句式另有负向测试：主体保持“相关责任主体”，沿用 NOT_FACT_ANCHORABLE，并在 unavailable 中保留新状态；没有为入锚而迁移或虚构主体。

P2 历史哈希仍为：

```text
P2_ATOMIC_EVIDENCE_SHA256 = 16efd41b4688f987f1c6be391f7ccc1e2bf79386f06d0143cc242a9d7a6865ee
P3A_ATOMIC_EVIDENCE_SHA256 = 9936252cb6db55d8f1c1b0072348852f9a2ce520a84ecf13a8dc235fa1ebe8a6
P3A_RECOGNITION_DELTA_SHA256 = e1f0c5d9f43c79e4964896dd7b5d2799b65a2a6079e178e9fd918771cb9d6974
```

批准机制延续仅将 CRLF 归一化为 LF 的哈希口径。没有修改 `p2_admission_approval.py`，没有删除 Guard、使用 HEAD 动态基线、增加永久 allowlist 或推进历史 BASE_SHA。

五处受影响 Guard 改用 P3-A 固定内容哈希。五处 Guard 均实际执行了“一字节追加即失败”的负向测试；新 helper 也有独立负向检查。

旧缓存/组装/P2 全字段对照原本内含旧分类器行为。本轮保留原 Git baseline，通过独立且哈希固定的三段识别差异夹具，给历史算法只应用 P3-A 差异；不改变历史 admission、冲突、身份、缓存或安全实现，不删除全字段比较。专项测试另外执行完全未改动的 P3-A BASE_SHA，验证七例旧状态及 P2 历史批准值。此处理只更新测试 oracle 的已授权语义差异，不改变 P2 生产准入。

## 实际测试与证据

项目 `.venv` 的启动器指向当前无法访问的 Windows Store Python。使用本机已提供的 Python 3.12.14 和项目现有 `.venv/Lib/site-packages`，没有安装依赖或修改环境文件。

最终验收命令，在项目根目录 PowerShell 执行：

```powershell
Set-Location -LiteralPath 'D:\AI_Projects\gongwen-style-agent-commercial\backend'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = 'D:/AI_Projects/gongwen-style-agent-commercial/backend/.venv/Lib/site-packages'
& 'C:/Users/peng chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' tests/run_p3a_offline.py --real-run -q -s tests/test_p3a_semantic_state_recognition.py tests/test_flow3_v3_atomic_evidence_generation.py tests/test_auxiliary_evidence_admission.py tests/test_topic_first_document_ranking.py tests/test_source_substrate.py tests/test_material_digest_task_assembly.py tests/test_stable_material_assets.py tests/test_writer_material_view.py tests/test_fact_safety_delivery_policy_reset.py tests/test_production_task_assembly.py tests/test_rag_observability.py tests/test_material_ingestion.py tests/test_m3_section_writer.py --basetemp tmp/p3a-review-r1-regression-01 --junitxml=../tmp/p3a-semantic-state/review-r1/regression-01.xml
```

上述命令已经运行。若复跑，须给 `--basetemp` 和日志使用新的唯一目录/文件名，避免 pytest 清理已有目录。

| 测试文件 | 通过 |
| --- | ---: |
| test_p3a_semantic_state_recognition.py | 143 |
| test_flow3_v3_atomic_evidence_generation.py | 30 |
| test_auxiliary_evidence_admission.py | 24 |
| test_topic_first_document_ranking.py | 24 |
| test_source_substrate.py | 31 |
| test_material_digest_task_assembly.py | 44 |
| test_stable_material_assets.py | 71 |
| test_writer_material_view.py | 47 |
| test_fact_safety_delivery_policy_reset.py | 7 |
| test_production_task_assembly.py | 21 |
| test_rag_observability.py | 16 |
| test_material_ingestion.py | 26 |
| test_m3_section_writer.py | 53 |
| 合计 | 537 |

R1 最终结果：537 passed / 0 failed / 0 skipped，110.49 秒。专项较首轮增加 59 项用例。有一条既有 Starlette/AnyIO 弃用警告。离线 runner 记录：真实模型、真实 Embedding、外部网络、未授权数据访问、真实 Run 写入均为 0；160 次 asyncio socketpair 为本机测试 IPC。

中间结果不隐去：

- `baseline.log/xml`：339 passed / 2 failed。测试运行期间实现已开始，两个后执行的 Guard 读到修改后的 owner；因此这次运行不作为完整纯基线通过证据。
- `targeted-01.log/xml`：104 passed / 24 failed / 1 skipped。涉及尚未更新的旧语义 oracle/Guard、未完整构造的 Writer view 夹具以及原本未区分的省略主体入锚约束。该轮未启用真实 Run。
- `targeted-02.log/xml`：125 passed / 4 failed。四个省略主体的传播夹具未满足既有入锚条件；修正测试分工，保留生产拒绝规则。真实 Run 七例已通过。
- `targeted-03.log/xml`：139 passed / 0 failed / 0 skipped。
- 首轮 `regression-01.log/xml`：478 passed / 0 failed / 0 skipped，随后 ChatGPT Review 判定 CHANGES_REQUIRED。
- R1 `review-r1/targeted-01.log/xml`：167 passed / 0 failed / 0 skipped。
- R1 `review-r1/regression-01.log/xml`：537 passed / 0 failed / 0 skipped。当前合计数按 R1 最终唯一套件计算，不累计重复运行。

本地证据目录：`tmp/p3a-semantic-state/`，包括 checkpoint、各轮日志/XML、初轮 AST/范围审计 `scope-audit.json`、R1 审计 `review-r1/scope-audit.json` 和最终 Git 状态。这些运行产物保持 ignored，不应提交。

实际执行 `git diff --check` 通过；校验新文件 UTF-8、Python 语法及新文件差异空白。当前任务不涉及前端，因此未运行页面/前端构建；未运行真实 Writer、黄金样本成文比较或全资料库处理。

## 非阻断记录与后续授权边界

1. 未修改的 `check_semantic_state_consistency()` 使用 `_DIRECTIONAL_COMPLETION_RE`。本地只读探针显示：“为圆满完成年度目标”已被新分类器正确识别为 PLAN，但该方法在 source/candidate 都为 PLAN 时仍返回 `completion_upgrade=True, passed=False`。这是后续方向性完成校验的独立问题，记录给 P3-B/独立授权工单，不在本轮修改 Fact Safety。
2. 省略主体的要求/目的句可能按现有准入规则进入 unavailable。已通过专项负向测试确认新状态保留，不修主体迁移问题。
3. 本次改变 Atomic 文件内容，会改变 Source Substrate/Stable Asset 规则指纹。`STABLE_ASSET_REPROCESS_REQUIRED_AFTER_MERGE = YES`。当前没有重处理资料库；只在隔离临时目录创建合成测试资产。必须等实现 → ChatGPT Review → External Review → Git Closeout → 合并全部完成，再单独授权真实资料处理。
4. 中文谓词识别仍为确定性规则，不宣称覆盖所有自然语言结构。本轮通过的范围是工单反例、真正完成态对照及所列相邻回归。

## 文件与 Git 交接

生产修改：

```text
backend/app/services/generation/atomic_evidence.py
```

测试修改/新增：

```text
backend/tests/test_p3a_semantic_state_recognition.py                 (新增)
backend/tests/test_auxiliary_evidence_admission.py
backend/tests/test_material_digest_task_assembly.py
backend/tests/test_material_ingestion.py
backend/tests/test_production_task_assembly.py
backend/tests/test_rag_observability.py
backend/tests/test_source_substrate.py
backend/tests/test_stable_material_assets.py
```

必要 helper、夹具、离线 runner 与文档：

```text
backend/tests/p3a_semantic_approval.py                              (新增)
backend/tests/fixtures/p3a_atomic_recognition_delta.json            (新增)
backend/tests/run_p3a_offline.py                                   (新增)
docs/P3-A-semantic-state-recognition.md                            (新增)
```

最终工作区：上述 8 个受控文件为 unstaged 修改，5 个本轮新文件为 untracked，另保留原有用户未跟踪文件；暂存区为空。修改前后 HEAD 相同，没有 commit message，因为没有创建 commit。

回滚路径（只供后续明确授权时使用，本轮未执行）：将上述 8 个受控文件逐个恢复为 BASE_SHA，再仅处理本轮 5 个新增文件。不得 `git clean` 或批量删除已有未跟踪文件。受控文件恢复命令：

```powershell
git restore --source=72af33cf4e6283c04c18ab87a30e2b506471feaa --worktree -- backend/app/services/generation/atomic_evidence.py backend/tests/test_auxiliary_evidence_admission.py backend/tests/test_material_digest_task_assembly.py backend/tests/test_material_ingestion.py backend/tests/test_production_task_assembly.py backend/tests/test_rag_observability.py backend/tests/test_source_substrate.py backend/tests/test_stable_material_assets.py
```

R1 相对于上轮送审内容仅修改 5 个文件：Atomic owner、专项测试、P3-A approval helper、固定差异夹具和本报告；没有新增修改 P2 回归文件、任何其他生产文件或离线 runner。

下一步仅为再次 ChatGPT Review。发现需要修改其他生产 owner、P3-B、事实安全策略或真实资料时停止，不自行扩展。

## 工单最终字段

```text
TASK = P3-A-SEMANTIC-STATE-RECOGNITION
BASE_SHA = 72af33cf4e6283c04c18ab87a30e2b506471feaa
BRANCH = codex/p3a-semantic-state-recognition
ROOT_CAUSE_FIXED = YES (P3-A 限定范围)
PRODUCTION_FILES_CHANGED = 1 (见文件清单)
TEST_FILES_CHANGED = 8 (见文件清单)
OTHER_FILES_CHANGED = 4 (见 helper/夹具/runner/文档清单)
REQUIREMENT_STATE = RECOMMENDATION
PURPOSE_GOAL_STATE = PLAN
NEW_SEMANTIC_STATE_ADDED = NO
CASE_1_PLAN = PASS / PLAN
CASE_2_MUST_ESTABLISH = PASS / RECOMMENDATION
CASE_3_SHOULD_CARRY_OUT = PASS / RECOMMENDATION
CASE_4_SHOULD_IMPLEMENT = PASS / RECOMMENDATION
CASE_5_COMPLETION_DEADLINE = PASS / RECOMMENDATION / completion_markers=[]
CASE_6_SHOULD_FORM = PASS / RECOMMENDATION
CASE_7_PURPOSE_COMPLETE = PASS / PLAN
TRUE_COMPLETED_CASES_PASS = YES
NO_FALSE_REQUIREMENT_FROM_主要 = YES
RESULT_FALLBACK_SAFE = YES
PREDICATE_SUBJECT_BOUNDARY_FIXED = YES
SEMANTIC_METADATA_CONSISTENT = YES (专项覆盖范围)
REAL_RUN_OFFLINE_REPLAY = PASS
REAL_CASES_MATCH_EXPECTATION = 7/7
REGISTRY_PROPAGATION_PRESERVED = YES
MATERIAL_PACKET_PROPAGATION_PRESERVED = YES
WRITER_VIEW_PROPAGATION_PRESERVED = YES
P1_CHANGED = NO
P2_CHANGED = NO (生产准入不变；测试 oracle/Guard 按 P3-A 更新)
WRITER_CHANGED = NO
FACT_SAFETY_CHANGED = NO
DOMAIN_HARDCODE_ADDED = NO
P3A_APPROVAL_SHA256 = 9936252cb6db55d8f1c1b0072348852f9a2ce520a84ecf13a8dc235fa1ebe8a6
PROTECTED_GUARDS_PASS = YES
FUTURE_UNAPPROVED_CHANGE_BLOCKED = YES
STABLE_ASSET_REPROCESS_REQUIRED_AFTER_MERGE = YES
TESTS = 13 个文件，具体命令和数量见上文
TOTAL_PASSED = 537
TOTAL_FAILED = 0
TOTAL_SKIPPED = 0
GIT_DIFF_CHECK = PASS
REAL_MODEL_CALLS = 0
EMBEDDING_CALLS = 0
EXTERNAL_NETWORK_CALLS = 0
NETWORK_CALLS = 0
STAGED_FILES = NONE
COMMIT_CREATED = NO
PUSHED = NO
PR_CREATED = NO
BLOCKERS = NONE (P3-A 实现与离线验收范围)
NON_BLOCKING_FINDINGS = P3-B 方向性完成校验；省略主体沿用不可入锚；合并后须另行授权重处理；既有依赖弃用警告
READY_FOR_CHATGPT_REVIEW = YES
```
