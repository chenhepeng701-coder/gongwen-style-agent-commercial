# RECOVERY-SELECTION-RCA-01

> 类型：本次真实运行的只读 RCA，不是长期产品规则，也不是修复验收。
> 审计日期：2026-09-29。生产代码、测试、运行产物均未修改；未调用 Writer、Embedding 或生成 API。
> 审计工作区：`D:\AI_Projects\gongwen-style-agent-commercial`。

## 1. Executive Conclusion（结论摘要）

```text
RECOVERY_SELECTION_RCA = PASS
WHY_ATTEMPT_2_REJECTED = 相对第1稿，HARD_BLOCK由1增至2、unresolved由32增至37，4类风险计数增加，触发selector事实风险不可增加的直接否决条件
WHY_ATTEMPT_3_REJECTED = 虽然unresolved降至31且部分风险减少，但相对当前best第1稿，HARD_BLOCK由1增至2、SUBJECT_SCOPE_MISMATCH由1增至2，仍被直接否决
WHY_ATTEMPT_1_SELECTED = 第1稿先成为有效候选，两次prefer_candidate均返回current；最终账本、selected别名、API正文和交付文件共同绑定第1稿
RECOVERY_EXTRA_CALLS_VALUE = PARTIAL
PRIMARY_ROOT_CAUSE = Recovery候选未满足事实风险不增加约束；局部改善不能抵消新增hard block或任一风险代码计数增加
SECONDARY_ROOT_CAUSE = 无改善未停止；被拒后重新使用best第1稿及原反馈，且反馈投影丢失Quality具体问题及repairable语义，将人工核对型聚合问题继续当作恢复触发项
READY_FOR_FIX_DESIGN = YES
```

`PASS` 表示本次“为何调用、为何拒选、为何交付第1稿”的证据链闭合，不表示文章质量或事实安全通过。`PARTIAL` 的口径是：新增候选确有局部检查项改善并提供诊断价值，但没有改善最终交付正文。若只衡量本次最终交付文本收益，则为零；不得把诊断价值说成用户已得到更好成稿。

第2稿消除了第1稿的 `DENSITY_AND_BALANCE` 告警，但新增两个 `AI_STYLE` 问题及超长，事实风险也增加。第3稿相较第2稿去掉超长和两个套语问题、减少若干风险计数；相较第1稿仍新增了主体范围不匹配的 hard block。因此，“后两稿全无改善”“selector只按先后顺序取第1稿”都不是准确解释。

## 2. Real Run Identity（真实运行身份）

### 2.1 定位方法与身份

先在实际生产输出目录枚举 `call_accounting.json`，按 model、writer_calls、recovery_rounds、recovery_used、selected_candidate_attempt、status、canonical_fact_status 筛选，再用 `backend/data/logs/generation_log.json` 的 `articles[].generation_provenance.run_id` 联结任务标题和账本时间。没有用目录名排序或文件修改时间猜测。

账本共28条记录，最大 `timestamp` 为 `2026-09-29 21:17:12`。两次满足条件的运行如下：

| 账本 id | timestamp（账本原值） | run_id | 标题 | 处理 |
|---|---|---|---|---|
| 8a52db7c | 2026-09-29 21:05:39 | a8e36523b882431795253730e5e304f5 | 2026年安全生产工作总结 | 较早，不作为本次逐轮对象 |
| bdb6f350 | 2026-09-29 21:17:12 | 0ac7d3ab0cd144cc991c79a19f0ab66f | 2026年安全生产工作总结 | 最近一次，本报告对象 |

下文 `R/` 统一表示仓库内真实目录：

`backend/output/full-draft/0ac7d3ab0cd144cc991c79a19f0ab66f/`

| 字段 | 真实值 | 证据 |
|---|---|---|
| model / provider | qwen3.8-flash / OpenAICompatibleProvider | R/call_accounting.json、各candidate provenance |
| writer_calls / writer_http_attempts / http_attempts | 3 / 3 / 3 | R/call_accounting.json |
| recovery_rounds / recovery_used | 2 / true | 同上 |
| selected_candidate_attempt | 1 | 同上、R/api_result.json provenance |
| delivery status / canonical fact | SUCCESS_WITH_REVIEW / REVIEW_REQUIRED | 同上、selected fact report |
| route / article mode | full_article_drafting / official_draft | accounting、账本 |
| target_words / min / max | 1200 / 840 / 1700 | packet.task_json、各Quality budget |
| 冻结Registry条目数 | 65 | R/material_packet.json.registry_json、各prompt_manifest.evidence_ids |
| Registry SHA256 | 992e576e94b7cfec9bf2f1f1b75ba3ea7ccef8b8c705d87d52b8328dac187d19 | 三轮manifest、Fact sidecar |
| packet digest | d68bfdcf229de4f70a1646e01596b6370581496bc85fa973761a9812f3242bb3 | 三轮manifest、candidate provenance |
| 写法参考 | UNAVAILABLE / WRITING_REFERENCE_PACK_REQUIRED；REFERENCE_PACK_SHA256为空 | accounting、三轮manifest |
| 总generation_time | 230.25秒 | R/api_result.json |

三轮 `writer_timeline.jsonl` 均有 `http_status=200` 和 `provider_return/outcome=success`，耗时分别79.813、71.421、61.234秒。第2、3稿额外Provider等待合计132.655秒；这不是含检查/导出的完整增量耗时。没有证据表明是传输重试，账本的 `retry_limit=0`，本次是两次应用层内容恢复。

### 2.2 实际读取的关键产物

- `backend/data/logs/generation_log.json`：标题、时间、run_id、选中稿provenance。
- `R/call_accounting.json`、`R/writer_timeline.jsonl`：运行预算、次数、状态、调用成功记录。
- `R/RAW_WRITER_ARTICLE_01.md`、`_02.md`、`_03.md` 和首稿别名 `RAW_WRITER_ARTICLE.md`：原始三稿。
- `R/candidate_01.json`、`_02.json`、`_03.json`、`candidate.json`：正文和任务/证据/来源绑定。
- `R/fact_report_01.json`、`_02.json`、`_03.json`、`fact_report.json`：读取并解析嵌套 `result_json`，不是仅看外层字段。
- `R/quality_report_01.json`、`_02.json`、`_03.json`、`quality_report.json`：每个required check及具体issue。
- `R/citations_01.json`、`_02.json`、`_03.json`、`citations.json`：三轮proposals均为空；不等于Fact没有发现候选证据。
- `R/prompt_manifest_01.json`、`_02.json`、`_03.json`、`prompt_manifest.json`：冻结输入及prompt哈希。
- `R/material_packet.json`：在内存中重建初始prompt所用真实输入；不把用户材料复制进本报告。
- `R/article_identity.json`、`R/api_result.json`、`R/FINAL_DELIVERED_ARTICLE.md`。
- `R/FULL-3c3b2f32aad5a5dc-21273506c12d/artifact_manifest.json` 和 `article.txt`。

该目录没有独立 `run_summary.json`、`runtime_manifest.json`、recovery feedback正文文件、recovery prompt正文文件或逐次selector决策日志。实际等价证据是accounting中的commercial_runtime、逐轮manifest、canonical报告、candidate别名和交付manifest。**反馈与prompt是在本次审计内存中按当前生产函数重建，并用实际保存的hash核验；没有伪造或补写运行产物。** 初始与两轮Recovery的system/user哈希全部吻合，足以闭合本次反馈来源；没有据此宣称全部运行时代码都已被commit哈希认证。

### 2.3 正文与交付身份核验

| Attempt | 原始正文SHA256 | raw与candidate正文一致 |
|---|---|---|
| 1 | a09f1cac4f0a11d144b4c0d0767633fe7d481b36b3cd5b13708a2581c4beeb44 | YES |
| 2 | 5a283953b002bbad0176393b2008809d2f300087022a138e701271e1266437bb | YES |
| 3 | 46ffbe333e86836352f2f597802bd534489a04aa658ef981f1770d41fd1221d1 | YES |

三轮Fact/Quality的 `result_sha256` 均与其 `result_json` 实算哈希一致，候选摘要互相绑定，正文哈希及Registry哈希吻合。`candidate.json / fact_report.json / quality_report.json / citations.json` 分别与各自 `_01` 完全一致。首稿raw、FINAL、API article_markdown和导出TXT字节一致。交付manifest绑定第1稿candidate digest `3c3b2f32aad5a5dcfc42729c15ccc7dda396063fd597771456fe63d8ce1e0866`。本轮没有重新渲染或验收DOCX排版，仅读取既有manifest中的核验记录。

## 3. Attempt 1/2/3 对照矩阵

官方“字数”实际是 `count_official_draft_words = len(str(final_text or ""))`，包括Markdown标记、标题和换行，不是汉字数。另列非标题段落字符和，避免口径混淆。三稿均为5个Markdown标题块、8个正文段落块；加粗的（一）至（四）计入段落而非Markdown标题。

| 项目 | Attempt 1 | Attempt 2 | Attempt 3 |
|---|---|---|---|
| 是否形成有效正文 | YES，导入成功、candidate和检查产物齐全 | YES | YES |
| 正文字数（官方口径） | 1390 | 1739 | 1343 |
| 非标题段落字符和 | 1314 | 1663 | 1267 |
| Fact 状态 | REVIEW_REQUIRED | REVIEW_REQUIRED | REVIEW_REQUIRED |
| Fact hard block | 1：U0022 | 2：U0022、U0024 | 2：U0012、U0022 |
| unresolved / review 项 | 32 unresolved；validation中31 REVIEW_REQUIRED+1 HARD_BLOCK | 37；35+2 | 31；29+2 |
| SUPPORTED / NON_FACTUAL | 0 / 0 | 0 / 0 | 0 / 0 |
| Quality 状态 | REVIEW_REQUIRED；qualified=false | 同左 | 同左 |
| required Quality未PASS项 | content_quality_observations、fact_safety、semantic_task_and_evidence_coverage、baseline_genre_maturity_revision_cost；4项 | 同前4项，加whole_article_budget=FAIL_OVER_LENGTH；5项 | 与第1稿相同4项 |
| 具体内容质量问题 | DENSITY_AND_BALANCE，WARNING，repairable=false | AI_STYLE两项：WARNING/false及BLOCKING/true | DENSITY_AND_BALANCE，WARNING，repairable=false |
| Recovery触发原因（本稿完成后） | 32条FACT反馈+2条QUALITY反馈非空，无检查异常且1<3，授权第1次Recovery | 37条FACT+3条QUALITY反馈非空，2<3，授权第2次Recovery | 31条FACT+2条QUALITY反馈仍非空，但3达到max_calls，不再调用 |
| Recovery收到的具体反馈（生成本稿前） | 不适用，初始prompt | 第1稿全文+第1稿34条反馈；详见第4节 | **仍是第1稿全文+同一34条反馈**，不是第2稿40条反馈 |
| 相比上一稿修掉了什么 | 基线 | DENSITY_AND_BALANCE不再报告；问题和下一步展开增加 | 第2稿超长和两项AI_STYLE不再报告；unresolved 37→31；多类风险计数下降 |
| 相比上一稿新增了什么问题 | 基线 | 新增1个UNSUPPORTED_RESULT hard block、主体范围风险等计数上升；超长39字符；2个AI_STYLE issue | 新增“联合消防、住建、商务等部门…”主体范围不匹配hard block；DENSITY_AND_BALANCE重新出现；正文新增[单位名称]占位 |
| 是否进入候选集合 | YES，保留并检查 | YES，生成evaluated并实际比较 | YES，生成evaluated并实际比较 |
| 是否成为当前best candidate | YES | NO，保留1 | NO，保留1 |
| 未被选择的精确原因 | 最终被选中 | 任一安全分量增加即否决：hard 1→2，unresolved 32→37；risk Counter有正差 | 与best 1比较：hard 1→2且SUBJECT_SCOPE_MISMATCH 1→2；即使unresolved 32→31仍否决 |

这里“候选集合”指三份真实evaluated均进入比较链；实现没有另存一个候选数组。Fact的每个unit展示 `status=REVIEW_REQUIRED`，但内部 `validation_result` 可为 `HARD_BLOCK`；hard计数取后者，不能因展示状态相同就认为风险相同。

### 3.1 风险代码计数（selector实际读取）

计数单位是“每个unit的risk_codes中该代码出现一次”，不是现实事件数量，也不是审计者认定的虚构事实数量。

| risk code | A1 | A2 | A3 | A2−A1 | A3−A1 |
|---|---:|---:|---:|---:|---:|
| AMBIGUOUS_EVIDENCE | 21 | 25 | 21 | +4 | 0 |
| SEMANTIC_STATE_UPGRADE | 8 | 8 | 7 | 0 | −1 |
| SUBJECT_SCOPE_MISMATCH | 1 | 2 | 2 | +1 | +1 |
| SUBJECT_SCOPE_UNCERTAIN | 5 | 6 | 5 | +1 | 0 |
| UNSUPPORTED_CAUSALITY | 2 | 2 | 2 | 0 | 0 |
| UNSUPPORTED_RESULT | 3 | 4 | 2 | +1 | −1 |

### 3.2 第2稿改善与退化（回答B）

第2稿前968字符与第1稿一致，保留了开头、四段主要做法及工作成效前两句。问题段由140字符扩展到288字符，下一步由178扩展到369，工作成效由165变为175。四部分框架没有变化。篇幅平衡告警消失，说明存在一个被canonical Quality识别的局部改善；增加展开并不自动证明文章更成熟。

第1稿U0022 `[917,968)` 的“取得阶段性进展，部分长期存在的安全隐患得到整改”在第2稿逐字保留，hard block完全未修。第2稿U0024 `[996,1019)` “安全宣传教育覆盖面扩大，群众安全意识有所增强。”被报告为新增 `UNSUPPORTED_RESULT / HARD_BLOCK`。类似宣传成果语义在第1稿已存在，因此这里准确说法是**改写后的新增hard分类单元**，不能把它说成新增一种此前完全没有的成果主题。

第2稿问题段还新增“全镇安全生产工作总体平稳”等表达，U0027 `[1068,1114)` 带有 `SUBJECT_SCOPE_UNCERTAIN / SUBJECT_SCOPE_MISMATCH`。内容质量新增 `CQ-B0008-AI_STYLE-01`（2次“进一步”、WARNING、不可自动修复标记）及 `CQ-B0010-AI_STYLE-01`（3次“进一步”、BLOCKING、repairable=true）。全文1739超过1700。虽有展开和平衡的局部改善，selector在比较Quality之前即因Fact恶化拒选。

### 3.3 第3稿改善与退化（回答D）

第3稿相较第2稿，字数1739→1343，超长消失；两个AI_STYLE issue消失；AMBIGUOUS_EVIDENCE 25→21、SEMANTIC_STATE_UPGRADE 8→7、SUBJECT_SCOPE_UNCERTAIN 6→5、UNSUPPORTED_RESULT 4→2。问题段与第1稿完全一致；下一步段基本回到第1稿，只把末句的“防灾避险能力”改为“自救互救能力”。并非沿第2稿继续定点完善：第3稿实际输入仍是第1稿。

第3稿删除“部分长期存在的安全隐患得到整改”，但保留并合并“阶段性进展/应急响应更加灵敏/能力提升”等成果判断，U0022 `[886,952)` 仍是 `SEMANTIC_STATE_UPGRADE / UNSUPPORTED_RESULT / HARD_BLOCK`。因此不能将删除一句等同于成果风险修复成功。

第3稿U0012 `[523,547)` 新增“联合消防、住建、商务等部门开展消防安全专项整治。”，报告为 `SUBJECT_SCOPE_MISMATCH / HARD_BLOCK`。第2稿宣传成果句对应的hard分类不再出现，但被这个新hard block替代，hard总数仍为2。相对第1稿，unresolved和两类风险下降确为改善；新增hard和主体范围风险仍不允许被这些改善抵消。篇幅失衡告警重新出现（579对章节中位数158），也新增了 `[单位名称]` 占位表达；该占位是人工阅读观察，不冒称canonical新增issue。

## 4. Recovery Trigger Chain（恢复触发链）

### 4.1 真实代码owner与触发条件（回答A、C）

- 编排：`backend/app/services/generation/full_draft_generation.py:263` 的 `generate_full_draft` 循环。
- 反馈投影与候选选择：`backend/app/services/generation/commercial_genre_runtime.py:155` 的 `recovery_failures`、`:184` 的 `prefer_candidate`。
- Recovery prompt拼接：`backend/app/services/generation/official_document_drafting.py:115` 的 `build_full_draft_recovery_prompt`。
- 本次文种Quality：`backend/app/services/generation/work_summary_quality_gate.py:1436`；Fact：`flow3_v3_trace.py` 的 `evaluate_full_article_facts`。

本次为正式full_article_drafting分支：`generator.py:5593` 根据请求route进入 `generate_full_draft`；不是历史其他文章模式或实验quality recovery流程。

第1次Recovery的真实判断：第1稿成功导入→Fact/Quality检查成功→`recovery_failures` 返回34条→selector保留首稿→`:335` 的 `checks_error or not failures` 为false→`:337` 的 `attempt < max_calls` 为true→`authorize_recovery(content_failure=True)`→拼Recovery prompt。

这34条不是一句“质量未通过”：包括全部32个非SUPPORTED/NON_FACTUAL的Fact unit（包括标题和未分类过渡），及 `content_quality_observations=REVIEW_REQUIRED`、`fact_safety=REVIEW_REQUIRED` 两个required Quality聚合项。首稿篇幅是PASS；触发不是“字数太少”。

第2次Recovery的真实判断：第2稿有40条非空反馈（37 FACT+3 QUALITY，含超长），且无检查异常、预算还有1次，因此继续。**这一继续条件用的是刚检查的第2稿**，不是直接用best判定；随后 `:341–344` 再取best第1稿正文，并将failures改成best第1稿的34条反馈。因此第3稿没有收到第2稿的超长、套语或新增hard block反馈。不存在“如果未成为best就停止”“如果反馈和上轮相同就停止”的条件。

第2稿确有可操作问题（超长和repairable=true的AI_STYLE），但它们不是第3稿实际修订任务。第1稿best仍有未解决事实问题，加上被聚合投影带入的人工型篇幅失衡提示，构成反复使用的同一反馈。预算达到3次才结束；不是发现无改善后主动停止。

### 4.2 两轮实际反馈的完整投影

以下表对应 `recovery_failures(fact_report_01, quality_report_01)` 的真实输出；第2、3次调用均收到它。所有FACT条目的 `owner=FACT`、`status=REVIEW_REQUIRED`；start/end是第1稿Unicode字符串偏移 `[start,end)`。为精确还原，代码列表保留函数把reason与risk_codes直接拼接产生的重复项。

缩写仅用于本报告表格：A=`AMBIGUOUS_EVIDENCE`；U=`SUBJECT_SCOPE_UNCERTAIN`；M=`SUBJECT_SCOPE_MISMATCH`；S=`SEMANTIC_STATE_UPGRADE`；R=`UNSUPPORTED_RESULT`；C=`UNSUPPORTED_CAUSALITY`；N=`UNBOUND_OR_UNCLASSIFIED`；E=`source_surface_not_entailed`。

| 来源unit | start:end | 实际codes（按顺序） |
|---|---|---|
| U0001 | 2:15 | A,A,U |
| U0002 | 17:194 | A,A,U,M,R |
| U0003 | 194:207 | N |
| U0004 | 212:218 | N |
| U0005 | 220:239 | N |
| U0006 | 239:294 | A,A |
| U0007 | 294:345 | A,A,S |
| U0008 | 345:383 | A,A |
| U0009 | 385:404 | A,A,U,S |
| U0010 | 404:461 | A,A,S |
| U0011 | 461:522 | A,A,S,C |
| U0012 | 522:577 | A,A,S |
| U0013 | 579:598 | E |
| U0014 | 598:646 | A,A,U |
| U0015 | 646:687 | A,A |
| U0016 | 687:719 | E |
| U0017 | 721:740 | A,A |
| U0018 | 740:792 | A,A |
| U0019 | 792:867 | A,A |
| U0020 | 872:878 | N |
| U0021 | 880:917 | A,A,U |
| U0022 | 917:968 | S,S,R |
| U0023 | 968:999 | N |
| U0024 | 999:1045 | A,A,R |
| U0025 | 1050:1056 | N |
| U0026 | 1058:1198 | A,A,C,S |
| U0027 | 1203:1210 | N |
| U0028 | 1212:1226 | N |
| U0029 | 1226:1270 | A,A |
| U0030 | 1270:1311 | A,A,S |
| U0031 | 1311:1346 | A,A |
| U0032 | 1346:1390 | A,A |

另两条原样为：

```json
[
  {"owner":"QUALITY","code":"content_quality_observations","status":"REVIEW_REQUIRED"},
  {"owner":"QUALITY","code":"fact_safety","status":"REVIEW_REQUIRED"}
]
```

真正的issue代码 `DENSITY_AND_BALANCE`、位置、`repairable=false`、章节589/中位数162、`repair_instruction`，均没有进入这两个QUALITY条目。FACT反馈有偏移及风险代码，但不携带 `validation_result=HARD_BLOCK`、具体证据候选、边界发现或复核建议。原Registry和完整材料仍在初始prompt中，不能说Writer完全没拿到证据。

### 4.3 Prompt内容与哈希证据

生产Recovery指令要求“输出以一级Markdown标题开头的完整文章”“修复下附规范检查失败项”，重申冻结材料、不得新增事实、不得把人工复核宣称为通过。user内容是初始user加上JSON对象 `previous_body` 和 `canonical_failures`；不是只要求输出局部补丁。

| prompt hash | 初稿 | 第2稿 | 第3稿 |
|---|---|---|---|
| system_hash | b51bfe39cbdaca9cbac4f2f206d2ff07cce39c03f61e7add51ea36ea1eea17ab | 8cda7d9f1110b0297bde71f405e6b08f45466d1b2bb65121014dc302826fee27 | 同第2稿 |
| user_hash | 4520b8d8e0100647e419b0c82c966b8da559bacb17e6253244f03f67a0894ff1 | a2d0305a9c59507028374f74292aed06c30afe9e5f648ee6a5fef0743c0e7937 | 同第2稿 |

离线调用现有纯构造/选择函数，使用真实 `FullDraftMaterialPacket(**material_packet)`、`use_writing_reference_pack(None)`（对应实际参考不可用快照），重建初稿，再用第1稿正文和其34条反馈构建Recovery。初稿system/user/host_boundary/ruleset hash全部匹配；Recovery system/user hash分别与02、03 manifest全部匹配。这不是再次调用Writer或跑真实API。

### 4.4 可自动修复与人工复核的实际边界

全文Quality只复用 `HEADING_QUALITY / AI_STYLE / REPETITION / DENSITY_AND_BALANCE` 这四类内容计算结果，不能把旧结构化流程的全部修复器套进本次运行。该层不自动改写正文。

- 当前内容issue定义中，目录式标题 `HEADING_QUALITY`、达到BLOCKING阈值的 `AI_STYLE`、完全重复句的 `REPETITION` 标记 `repairable=true`。本次第2稿的 `CQ-B0010-AI_STYLE-01` 正属于此类。
- 篇幅越界可给Writer明确数字目标，Recovery投影对 `whole_article_budget` 特别保留target/min/max/measured；低于下限虽然标成MANUAL，也作为例外进入反馈。第2稿超长为实际存在的可操作目标，但没有被传给第3稿。
- `DENSITY_AND_BALANCE` 明确 `repairable=false`，要求结合冻结计划人工核对；本次全文路径没有Frozen Plan。它仍被聚合成required `content_quality_observations=REVIEW_REQUIRED`，投影只筛聚合状态、不检查内部repairable，于是两轮都进入反馈。
- `semantic_task_and_evidence_coverage` 与 `baseline_genre_maturity_revision_cost` 是明确 `MANUAL_REVIEW_REQUIRED`，**已经被recovery_failures排除，不是本次循环触发原因**。但selector的required非PASS计数仍包含它们，三稿各同占2项。
- FACT投影不分自动可修与人工待核，把所有非SUPPORTED/NON_FACTUAL单元都纳入，包括标题、未分类句和证据歧义；重复生成不能替代人工认证。不能据此断言所有Fact风险都不可通过改写改善。

## 5. Candidate Selection Chain（候选选择链，回答E）

`commercial_genre_runtime.py:184–213` 的selector没有总分、文章长度奖励、文风语义评分或显式attempt排序。它读取：

1. `safety=(validation_counts.HARD_BLOCK, unresolved_count)`。
2. 全部unit的risk_codes构成 `Counter`，按每种代码出现次数比较。
3. `quality_rank=(int(article_qualified), -required非PASS检查项数)`；缺Quality为(-1,-1)。它不读取具体内容issue是否修复、severity、repairable或实数字数，只通过check状态间接反映。

直接生产代码：

```python
if any(new > old for new, old in zip(new_safety, old_safety)) or new_risks - old_risks:
    return current
if new_safety < old_safety or old_risks - new_risks or new_quality > old_quality:
    return proposed
return current
```

先否决任何安全分量或任一种风险代码计数增加，再接受安全减少、风险减少或Quality严格改善。这里是分量不增加保护，不是把不同风险加权相抵。最后仍未严格改善时保留current；在正常从早到晚的循环中即保留较早稿。不是读取“attempt=1”就优待首稿。

| 比较 | current | proposed | 命中条件 | 返回 |
|---|---|---|---|---|
| 初始化 | 无 | A1 | 首个有效正文先被保留 | A1 |
| A1 vs A2 | safety=(1,32)，quality=(0,-4) | (2,37)，(0,-5) | hard和unresolved均增加；Counter正差A+4、M+1、U+1、R+1 | A1 |
| 当前best A1 vs A3 | (1,32)，(0,-4) | (2,31)，(0,-4) | hard增加，Counter正差M+1；unresolved下降不能抵消 | A1 |

本次离线用真实reports调用未修改的 `prefer_candidate`，输出 `SELECTED_AFTER 2 1`、`SELECTED_AFTER 3 1`，与实际accounting及selected文件完全一致。两次都在第一条安全否决分支返回，**没有走到同等候选保留早稿的最后分支**。

`full_draft_generation.py:358` 把selected.attempt写入accounting，随后将selected的candidate和报告写入无编号别名并交付。`delivery_decision.py:387` 的 `full_article_review_result` 保留正文，组织Fact与required Quality复核信息，形成SUCCESS_WITH_REVIEW。最终选第1稿不代表它安全通过，首稿hard block仍在交付风险信息中。

## 6. Root Cause（根因）

### 6.1 直接选稿原因

两次恢复都没有产生满足“事实风险不得增加”约束的替代稿。第2稿安全与聚合Quality指标均退化；第3稿存在部分真实改善，但新增主体范围风险、hard count高于第1稿。selector按明确代码合同拒绝，没有证据证明本次拒选是错误排序、报告串稿或默认选首稿。

### 6.2 额外调用未改善交付的结构原因

继续条件只看最新候选反馈非空和预算；修订输入却取best，且无“没有改善/相同反馈”的停止检查。本次拒绝A2以后，A3完全重复A2的输入prompt，不会接收A2特有问题；即使生成了不同文章，过程没有逐轮吸收失败差异。

反馈投影没有保留Quality issue层的可修复性与具体目标。明确需要人工核对的density告警被降成普通required REVIEW_REQUIRED，再混入Recovery；Fact的人工待核项也没有分流。该问题会增加无有效修复目标的调用机会，但本次还有真实成果hard block等独立触发因素，不能说“删掉density触发就一定只调用一次”。

## 7. Secondary Findings（次要发现与五种可能）

| 必查可能 | 判定 | 证据与边界 |
|---|---|---|
| 1. 第2、3稿实际上比第1稿更差 | NOT PROVEN（整体文章质量）；YES（selector保护的hard风险维度） | 两者hard 1→2；但A2消除density，A3减少unresolved/部分risk，不支持各维度全面变差。未做黄金样本人工验收。 |
| 2. 第2、3稿有所改善，但selector无法识别 | NO（作为本次拒选根因） | A3的unresolved及风险减少正是selector读取的字段，但先被新增风险否决。A2的density改善被同一聚合check下AI_STYLE替代，selector确不区分子issue收益，但在本次安全否决后根本没有进入Quality胜负分支。 |
| 3. 修复旧问题的同时新增更严重Fact风险 | YES（按本次canonical分类） | A2消除density却增加UNSUPPORTED_RESULT hard；A3相对A1减少部分风险，却新增SUBJECT_SCOPE_MISMATCH hard。不能外推为已人工证实的现实造假。 |
| 4. 无法通过重复生成解决的问题持续触发 | YES（人工核对义务）；非唯一原因 | density的repairable=false/人工核对目标和标题等待核项持续进入A1反馈；Writer不能替代人工确认。两项显式MANUAL_REVIEW_REQUIRED已排除；不能声称所有Fact问题永远不可修复。 |
| 5. feedback太抽象/缺目标，导致Writer只是重写全文 | NOT PROVEN（完整因果命题） | Quality具体issue、位置、repairable和修复指令确实丢失，且prompt要求完整输出；但Fact保留偏移和代码，第2稿前968字符完全没变，不能断言“只是全文改写”或反馈不足是唯一生成原因。 |

其他局限：selector把required检查非PASS数量作为Quality层级，无法表达同一check内问题类型/数量/严重度变化；风险计数受句子划分影响，第2稿新增hard单元有与原稿类似的成果主题。这些是后续设计需考虑的粒度问题，不构成本轮放宽安全规则的理由。

参考包不可用是已确认背景，三轮条件相同；没有对照实验能证明它导致本次不采纳。三轮proposals为空、AMBIGUOUS_EVIDENCE很多，说明canonical追溯未形成支持结论；不等于资料库无证据，也不能仅凭这一点宣布RAG失效。

## 8. What Is NOT Proven（尚未证明事项）

- 没有黄金样本人工对比与用户质量确认，不能宣布任何一稿达到人工成熟终稿；本任务不是生成质量修复。
- 本次未重跑Fact/Quality全链测试；以既有真实报告为证据核验哈希与选择逻辑，没有把当前生产代码全部运行时版本等同于HEAD。相关代码本来就在dirty worktree中。
- 没有独立的运行时代码全量hash manifest或逐次选稿事件日志；prompt重建、selector离线结果与持久化终态一致构成本次归因证据，不是对全部代码版本的证明。
- 没有证据证明任何未来early-stop或更细反馈一定提升成稿质量；不预测改动后必然少调用或必然选中恢复稿。
- 即使排除人工density和标题反馈，首稿仍有其他Fact反馈，本次调用次数是否减少需要后续授权验证。
- 本轮未进行外部审查、ChatGPT Code Review、Git Closeout、Push、PR；没有启动下一任务。

## 9. Recommended Fix Scope（仅建议，不实现）

建议下一工单先设计“恢复资格/反馈对象/停止条件”的明确合同，保持Fact Safety原判和风险稿交付，不先调selector权重。

| 文件与函数 | 触发条件 | 本次实际影响 | 推荐方向 |
|---|---|---|---|
| commercial_genre_runtime.py / recovery_failures | required聚合check为REVIEW_REQUIRED，但内部issue repairable=false | density人工提示进入两轮恢复；具体issue类型被丢弃 | 区分人工复核义务与有明确修复目标的问题；明确是否传递issue代码、位置、修复指令及repairable；不要靠降低安全等级消除反馈 |
| full_draft_generation.py / generate_full_draft | proposed未胜出、最新failures非空且预算剩余 | best不变，第二轮Recovery重复相同A1 prompt；A2特有问题没进入下一prompt | 设计“无改善/输入与反馈相同”策略及可观察决策记录；保留best正文，是否继续由明确合同决定 |
| official_document_drafting.py / build_full_draft_recovery_prompt | 只有聚合Quality代码及Fact偏移/风险码 | 不提供具体Quality修复目标，要求输出完整文章 | 在owner结构化合同确定后表达可执行目标；Prompt不能单独拥有恢复资格规则 |
| commercial_genre_runtime.py / prefer_candidate | Quality子问题改善但check仍REVIEW_REQUIRED | 无法表达同一check内改善；本次不是直接拒选原因 | 仅记录粒度风险；优先保持现有事实风险不增加原则，不以文风或字数抵消hard风险 |
| work_summary_quality_gate.py / evaluate_full_article_quality | density沿用“结合冻结计划”指令，全文路径无该计划 | 人工复核提示引用缺失的过程对象 | 后续评估提示适用性；不伪造Frozen Plan，不改变Fact owner |

建议后续离线验收覆盖：人工-only反馈不误触发、可修复项带明确目标、候选被否决后下一轮输入有明确策略、同分保留早稿、部分风险下降但新风险上升仍不得升级安全状态、正文始终可交付。任何生产改动及真实外部调用都需另行明确任务授权。本轮没有实施这些建议或修改测试。

## 10. 审计执行、Git与任务状态边界

### 10.1 本轮实际核验

- 读取UTF-8 `AGENTS.md`；执行 `git rev-parse --show-toplevel`、`git branch --show-current`、`git rev-parse HEAD`、`git status --short`、`git diff --cached --name-only`、`git status --short --ignored=matching`、`git ls-files --stage`。
- 用 `rg` 定位账本、manifest、selector、Recovery和canonical检查owner；用PowerShell `ConvertFrom-Json`及Python标准库解析真实产物。
- 使用应用自带Python `-B -X utf8 -`，禁止写pyc，以只读现有site-packages补足依赖；没有安装依赖。执行正文/别名/报告hash一致性检查、纯prompt构造hash复核和真实reports的selector复算。prompt/selector核验设置网络审计拦截，未导入或调用Provider生成入口。
- 初次仓库venv启动失败（其基础Python路径不可启动）；改用内置Python后遇到缺yaml，再使用已有site-packages。首次prompt重建未显式绑定参考快照，尝试读取本地参考配置时因SecretStoreError停止，未输出密钥、未调用外部服务；随后按真实reference_status用现有 `use_writing_reference_pack(None)` 绑定快照，重建核验通过。没有把这些环境失败写成测试通过。
- 文档复核曾对“A1与A3下一步段完全一致”作断言，断言失败；逐字diff确认末句有上述6字替换，已据此修正报告。问题段完全一致及A1/A2前968字符一致均核验通过（实测共同前缀为989字符）。
- 未运行pytest、构建、服务、真实页面、真实生成、Embedding、DOCX重新导出或黄金样本质量验收。本任务以已发生运行的证据审计为目标，没有伪造新测试结果。

### 10.2 Git边界

- 分支：`codex/must-deliver-recovery-01`。
- 审计前HEAD：`5e79982034936027d1a3df7e9e00e5362c803221`；本轮不创建commit，结束时应保持此值。
- 开始时34个tracked文件已修改、21个untracked文件已存在，staged为空。对这55个文件建立内存SHA256清单供结束复核，不复制私有运行物进Git。
- 用户本工单明确禁止add/commit/push，优先于一般checkpoint/closeout流程；不创建提交型checkpoint，不吸收既有改动。
- 本报告为新增文件，普通 `git diff --stat` 不显示未跟踪文件。其显示的34文件、525行新增、177行删除均属于开始前已有差异，不得归因于本轮。
- 回滚边界：仅撤销本任务新建报告及经用户确认的CURRENT_TASK变更；不得reset/clean或覆盖任何既有文件。本轮未执行回滚或删除。

### 10.3 CURRENT_TASK / PROJECT_STATE 缺失事项

根目录读取 `CURRENT_TASK.md`、`PROJECT_STATE.md` 均报不存在；`git ls-files`及仓库文件名搜索也未找到这两个文件（部分历史临时目录权限受限，不能声称已读这些目录）。因此没有可沿用的状态枚举或目录规范，RCA采用本工单指定的 `docs/rca/RECOVERY-SELECTION-RCA-01.md`。

已向用户请求实际文件位置或新建CURRENT_TASK许可；在答复前不伪造“已遵守其内容”或“已按原格式更新”。PROJECT_STATE不创建、不更新。RCA调查本身已完成，工作流状态更新尚未闭合，不把缺失治理文件当作选稿原因。

```text
INVESTIGATION_STAGE = COMPLETED
CHATGPT_CODE_REVIEW = NOT_STARTED
EXTERNAL_REVIEW = NOT_STARTED
GIT_CLOSEOUT = NOT_STARTED
PUSH = NOT_STARTED
PR = NOT_CREATED
PRODUCTION_CODE_CHANGED = NO
REAL_WRITER_CALLS = 0
REAL_EMBEDDING_CALLS = 0
CURRENT_TASK_UPDATED = NO（文件缺失，等待用户提供位置或确认新建）
PROJECT_STATE_UPDATED = NO
NEXT_FORMAL_TASK = NOT_STARTED
READY_FOR_CHATGPT_CODE_REVIEW = NO（RCA可审查，但本工单要求的状态文件更新尚未闭合）
```

### 10.4 结束核验与完整Git输出

内存清单对照：开始前55个dirty/untracked文件的SHA256全部保持不变；无既有文件丢失。唯一新增文件是本报告。staged仍为空、HEAD不变。`git diff --check`退出0，无输出；报告UTF-8、必需章节及逐字对照复核通过。代码目录范围检查未发现嵌套Git（明确排除运行/缓存目录），`git ls-files --stage`未见160000条目。

`git diff --stat`（全为本轮开始前既有变更，不含未跟踪报告）：

```text
 backend/app/routers/generation.py                  |  27 ++-
 backend/app/services/generation/atomic_evidence.py |   4 +-
 .../generation/commercial_genre_runtime.py         | 102 +++++++++--
 .../services/generation/full_draft_generation.py   | 201 ++++++++++++++++-----
 .../app/services/generation/full_draft_material.py |   7 +-
 .../generation/genres/commercial_contract.py       |   8 +-
 .../services/generation/genres/commercial_gate.py  |   6 +-
 .../generation/genres/upstream_genre_contracts.py  |   4 +-
 .../generation/genres/work_summary_commercial.py   |   8 +-
 .../generation/official_document_drafting.py       |  31 +++-
 .../services/generation/writing_reference_pack.py  |   9 +-
 backend/tests/commercial_genre_scope.py            |  11 ++
 backend/tests/genre_ui_scene_scope.py              |  19 +-
 backend/tests/multi_genre_scope.py                 |   4 +-
 backend/tests/p3a_semantic_approval.py             |   3 +
 backend/tests/release_rc_01_package_scope.py       |   8 +
 backend/tests/test_auxiliary_evidence_admission.py |   3 +-
 backend/tests/test_business_domain_catalog.py      |  13 +-
 .../tests/test_commercial_genre_infrastructure.py  |  11 +-
 backend/tests/test_commercial_genre_parity.py      | 101 ++++++-----
 backend/tests/test_flash_low_reasoning.py          |   4 +-
 backend/tests/test_genre_ui_scene_scope.py         |   3 +
 backend/tests/test_multi_genre_production_gate.py  |  28 ++-
 .../tests/test_p3a_semantic_state_recognition.py   |   3 +-
 backend/tests/test_production_task_assembly.py     |  15 +-
 backend/tests/test_rag_observability.py            |   4 +-
 backend/tests/test_scene_contract.py               |   2 +-
 backend/tests/test_upstream_suite_flows.py         |   4 +-
 backend/tests/test_upstream_suite_integrity.py     |   4 +
 backend/tests/test_writer_material_view.py         |   6 +-
 backend/tests/test_writer_observability.py         |   5 +-
 backend/tests/test_writing_reference_pack_02b1.py  |  31 ++--
 backend/tests/upstream_suite_scope.py              |   9 +-
 docs/MULTI-GENRE-COMMERCIAL-INFRASTRUCTURE.md      |   4 +
 34 files changed, 525 insertions(+), 177 deletions(-)
```

`git status --short`：

```text
 M backend/app/routers/generation.py
 M backend/app/services/generation/atomic_evidence.py
 M backend/app/services/generation/commercial_genre_runtime.py
 M backend/app/services/generation/full_draft_generation.py
 M backend/app/services/generation/full_draft_material.py
 M backend/app/services/generation/genres/commercial_contract.py
 M backend/app/services/generation/genres/commercial_gate.py
 M backend/app/services/generation/genres/upstream_genre_contracts.py
 M backend/app/services/generation/genres/work_summary_commercial.py
 M backend/app/services/generation/official_document_drafting.py
 M backend/app/services/generation/writing_reference_pack.py
 M backend/tests/commercial_genre_scope.py
 M backend/tests/genre_ui_scene_scope.py
 M backend/tests/multi_genre_scope.py
 M backend/tests/p3a_semantic_approval.py
 M backend/tests/release_rc_01_package_scope.py
 M backend/tests/test_auxiliary_evidence_admission.py
 M backend/tests/test_business_domain_catalog.py
 M backend/tests/test_commercial_genre_infrastructure.py
 M backend/tests/test_commercial_genre_parity.py
 M backend/tests/test_flash_low_reasoning.py
 M backend/tests/test_genre_ui_scene_scope.py
 M backend/tests/test_multi_genre_production_gate.py
 M backend/tests/test_p3a_semantic_state_recognition.py
 M backend/tests/test_production_task_assembly.py
 M backend/tests/test_rag_observability.py
 M backend/tests/test_scene_contract.py
 M backend/tests/test_upstream_suite_flows.py
 M backend/tests/test_upstream_suite_integrity.py
 M backend/tests/test_writer_material_view.py
 M backend/tests/test_writer_observability.py
 M backend/tests/test_writing_reference_pack_02b1.py
 M backend/tests/upstream_suite_scope.py
 M docs/MULTI-GENRE-COMMERCIAL-INFRASTRUCTURE.md
?? P1-TOPIC-FIRST-RANKING.patch
?? P2-AUXILIARY-EVIDENCE-ADMISSION-20260926-012405.patch
?? P2-NEW-TEST-FILES.zip
?? backend/low-vs-xhigh-runs.zip
?? backend/manual_writer_flash_result.json
?? backend/manual_writer_flash_stream_result.json
?? backend/manual_writer_result.json
?? backend/proxy_check.txt
?? backend/tests/atomic_evidence_live_block_scope.py
?? backend/tests/must_deliver_recovery_scope.py
?? backend/tests/run_atomic_evidence_live_block_offline.py
?? backend/tests/run_must_deliver_recovery_offline.py
?? backend/tests/test_atomic_evidence_live_block.py
?? backend/tests/test_atomic_evidence_live_block_scope.py
?? backend/tests/test_must_deliver_recovery.py
?? backend/tests/test_must_deliver_recovery_scope.py
?? backend/writer_flash_stream_test.json
?? backend/writer_flash_test.json
?? backend/writer_test.json
?? docs/ATOMIC-EVIDENCE-LIVE-BLOCK-01.md
?? docs/MUST-DELIVER-RECOVERY-01.md
?? docs/rca/
```
