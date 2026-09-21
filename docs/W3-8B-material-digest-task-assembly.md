# W3-8B｜Material Digest + Task Assembly 去实时 Brief 实验

文件类型：本轮实验验收报告及内部接口说明；不是新的项目规则真源。

## 结论与范围

**W3_8B = PASS**。仅表示本地确定性实验、缓存复用、材料守恒、零模型写作准备通过；不是材料理解质量、文章质量或生产接线验收。

实验真实调用现有 Atomic/Anchor、材料包和未修改的 Writer Prompt 构建器，形成合法下游输入。没有执行 Brief 模型、Writer 模型、Provider 或在线检索。原 Brief 实现、Prompt 资产锁、生产接口和生成路由均未改变。

```text
BASE_SHA = 54d0a88a07c0321ba0dab5e8ab2dcf3bc69ff285
BRANCH = codex/w3-8b-material-digest-task-assembly
CURRENT_HEAD = 54d0a88a07c0321ba0dab5e8ab2dcf3bc69ff285
MATERIAL_DIGEST_OWNER = backend/app/services/generation/material_digest.py
TASK_ASSEMBLY_OWNER = backend/app/services/generation/task_assembly.py

MATERIAL_DIGEST_REUSABLE = YES
SECOND_USE_CACHE_HIT = YES
TASK_CHANGE_REUSES_DIGEST = YES
SOURCE_CHANGE_INVALIDATES_DIGEST = YES
TASK_DEPENDENT_STATE_REBUILT_REALTIME = YES
SOURCE_FACT_LOSS_COUNT = 0
SEMANTIC_STATE_DRIFT_COUNT = 0
ILLEGAL_EVIDENCE_ID_COUNT = 0
UNEXPLAINED_EVIDENCE_OMISSION_COUNT = 0
TASK_DEPENDENT_FIELDS_FROZEN_IN_DIGEST = 0
REALTIME_BRIEF_MODEL_CALL_COUNT_IN_EXPERIMENT = 0
PROJECT_MODEL_CALL_COUNT = 0
PRODUCTION_ROUTE_CHANGED = NO
DIGEST_FIRST_BUILD_MS = 11.331
DIGEST_CACHE_LOAD_MS = 2.621
TASK_ASSEMBLY_MS = 18.995

BRIEF_RESPONSIBILITIES_REPLACED = 原文阅读索引、全量合法ID绑定、逐条状态/限制呈现、当前时间风险传递、Brief输入绑定与校验
BRIEF_RESPONSIBILITIES_NOT_REPLACED = 跨材料事项语义归并、简洁连贯归纳、非确定性歧义识别、减少Writer阅读负担的质量效果
WRITER_CONTRACT_COMPLETE = YES（原材料包、Brief schema及完整Prompt实测；非Writer执行或成稿质量）
TESTS = baseline 204 passed; directed 44 passed; final backend 248 passed
KNOWN_RISKS = 详见下文；原句索引不等于成熟Brief理解，生产前债务仍在
BLOCKERS = NONE（本轮实验边界内）
```

指标来自同一次完整回归 `backend/tmp/w3-8b/final-verified/test_abcdef_contract_and_metri0/metrics.json`。Digest 时间含 Source Substrate 构建/加载和 Digest I/O；Task Assembly 时间含校验、现有 Atomic/Anchor、材料包及阅读卡片生成，不含 Writer Prompt 构建、比较器或模型耗时。单次小型合成材料实测，不能推断实际整篇生成提速。

零损失/零漂移是相对于同输入下当前提交的 Brief 前材料合同；不是声称现有提取器能把原文所有含义都转成 Evidence。缓存另有原文完整保留测试。

## 当前真源审计与推理闸

1. 问题：Source Substrate 仅缓存切分；需要验证进一步复用纯源信号，并用实时确定性组装替代 Brief 模型调用。
2. 当前证据：`full_draft_generation.py::prepare_full_draft_material/generate_full_draft`、`full_draft_material.py`、`case32_contract/brief_rules.txt`、`official_document_drafting.py`、`atomic_evidence.py`。
3. 已确认调用关系：`/api/generation/generate` → `generate_commercial_flow`；其中现有 `generation_route == full_article_drafting` 分支进入 `generate_full_draft`，依次准备材料、调用 Brief、调用 Writer。不是声称所有生产分支都调用 Brief。
4. Brief 创建阅读说明和事项卡片，现有规则禁止其谋篇、分配段落和决定事实权限。`validate_material_brief` 校验 schema、输入绑定、全量 ID 守恒和引用范围；它不认证模型文字事实。
5. 已验证的稳定部分：源片段及其字面动作词、否定/模态/完成信号只依赖文本和 Atomic 规则。最终 semantic_state 还会受 semantic_context、coverage_role 等影响，因此不缓存。
6. 未验证：这类保守索引能否达到模型 Brief 的概括质量，真实用户资料和黄金样本成稿是否等效，实际延迟/成本收益。
7. 新概念创建者与 owner：`material_digest.py::load_material_digest` 管理源阅读索引；`task_assembly.py::assemble_task` 管理实验任务组装。
8. 调用者：本轮测试及明确内部调用。生产代码没有导入两个新 owner。
9. 消费者：Task Assembly 消费 Digest；既有 `FullDraftMaterialPacket` 和纯函数 `build_full_draft_prompt` 消费任务组装结果。
10. 既有同职责部分全部复用：Source Substrate 切分、Atomic 文字信号/状态/ID/准入、材料包/Brief 验证、时间 overlay、写法参考加载、Writer Prompt。
11. 最小实现：仅新增两个 owner、一个测试文件、一个断网测试入口和本报告。没有修改任何既有 tracked 文件。
12. 最大风险：缓存任务状态、把阅读索引变成事实授权、遗漏合法 ID、把语法有效误称为成熟材料理解。
13. 阻断措施：整结构与提交基线比较、变更任务/主辅/覆盖角色/选择/来源、异常缓存/非法 ID/准入测试、未改 Writer 的完整 Prompt 构建、模型调用失败哨兵。
14. 文章质量：没有进入写作，不声称改善或等效；保留全部合法原文不能证明 Writer 表现不退化。
15. 事实安全：核心算法、输出合同和校验原样使用，不放宽任何规则。
16. 用户行为：公开入口不变；显式内部实验调用增加私有缓存 I/O 和明确的实验错误。
17. 旧路线：没有恢复、删除或切换任何路线；没有修改 FLOW3/FLOW4 合同。

基线由 fetch 核实远端 main 后，将本地 main 快进同步至指定 SHA，再创建独立实验分支。该 SHA 和保持不动的 main 是 checkpoint；没有创建新的 commit 或任务合并。

## 稳定层与实时层边界

Material Digest 的持久化字段严格限定为：

| 字段 | 来源和含义 |
|---|---|
| `version/substrate_key/rules_sha256` | 缓存版本、原 Source Substrate 绑定和 Digest owner 指纹 |
| `reading_units[].source_span` | 现有切分器返回的原文片段 |
| `reading_units[].actions[]` | 复用 `_verb_positions` 的字面动作词与原片段 offset |
| `reading_units[].literal_signals` | 复用 `classify_semantic_state` 返回的否定、模态、完成标记；只取三个 marker 列表 |

不存 `semantic_state`、`state_qualifier`、Evidence ID、task、主辅角色、coverage_role、section assignment、Evidence selection、时间风险、写作目标、Brief JSON、registry 或准入决定。字面“完成”标记不代表已授权 COMPLETED；例如它不能覆盖同句中的否定、计划或当前 Atomic 判定。信号受既有提取数量限制，不是完整事实替代物。

源文本和切分完整序列仍由 Source Substrate 保存，包括切分器未接纳的原文。Digest 对相同文字只存一份阅读特征；原出现位置、重复片段及身份仍在 Substrate，最终 Evidence 重新绑定。相同动作词仅用于导航，不宣称同一对象、事件、因果或章节。

同一 source 的定义沿用 Source Substrate：相同选中源集合及有效来源属性。任务变化但检索选中集合变化时，可能产生新的整体 Digest，本轮不宣称任意 RAG 选材变化都能命中。来源、Atomic 源码或 Digest 源码变化均会失效；任务组装规则本身不冻结入 Digest。

Task Assembly 每次重新执行：

- 现有请求到 task 规范化，保留 style、scene 和既有 hard_max 计算。
- 当前 Atomic 提取、主辅选择、上下文状态、去重、补缺、数量限制、required/optional evidence、ID 生成、原拒绝记录。
- 当前 Anchor/registry 准入、来源 scope、writability、语义约束、exact_values、禁止推断和使用限制。
- 当前材料包和 `brief_input_sha256`；现有 owner 计算时间 overlay。若输入已提供绑定 overlay，遵循原 owner 原样保留，不另行改写或裁决。
- 当前全部合法 Evidence 对 Digest 原句的绑定、动作词阅读分组、逐条 limits、已知时间风险 uncertainties。
- 经既有 `with_brief` 验证后返回原材料包。任务产物仅返回，不写进可复用缓存。

## Brief 与 Writer 合同的替代程度

| 当前 Brief 职责或字段 | 实验实现 | 限制 |
|---|---|---|
| `overview` | 从当前合法条目/阅读组实时形成导航说明 | 不是模型式材料全貌归纳 |
| `matters` | 按源动作词序列建立阅读组；不是一条 Evidence 一卡，也不是 scope 一卡 | 不是跨材料的真实事项聚类 |
| `summary.text/evidence_ids` | 合法原句按首次出现去重复展示，绑定所有合法 ID | 原句式摘要，未实现 Brief 规则所要求的凝练连贯概括 |
| `limits` | 按每条当前证据保存原状态、语义约束、writability、限制与来源约束，附源文字信号 | 不自行补齐主体、因果、结果，也不解释所有不透明 limitation |
| `uncertainties` | 传递当前 owner 已知的时间风险 findings | 不宣称已识别所有缺口或歧义；其他限制仍完整保留 |
| `input_sha256` 与 ID 守恒 | 使用当前材料包绑定，调用未修改的 Brief 校验器 | 不生成、不格式化或修补任何 EV ID |

实验生成符合当前 Brief **结构合同**的动态阅读视图，并非缓存/复制静态 Brief JSON。现有 `bound_brief()` 返回字段名仍是 `model_brief`，`materials_markdown()` 标题仍是“自动 Material Brief”；这是沿用未修改的旧合同命名，不代表实际调用了模型。报告明确记录该来源差异。

Writer 并不通过代码逐条执行 Matter，而是收到包含完整 Brief JSON 的 `materials.md`。测试使用真实写法参考加载器读取仓库明确标识的 synthetic fixture，再调用未修改的 `build_full_draft_prompt`，验证下列内容全部在 Prompt 中：当前任务、hard_max、完整 Brief、完整 Evidence 写作视图、时间 overlay、来源限制和写法参考。`bound_brief` 和 candidate input 继续有效。

`brief_prompt()` 只属于模型 Brief 的请求准备，本轮用哨兵禁止调用它；没有绕过或修改其中的 LF/CRLF 资产锁。本轮没有生成任何模型回复或成稿。

## 比较场景、实际差异和证据

比较器执行 `git show 54d0a88...:backend/app/services/generation/atomic_evidence.py` 的真实提交源码；不用旧工作区、历史报告或手写替代算法作为结果真源。输入是 `test_source_substrate.py::sources` 的合成材料以及本轮明确构造的变体。

| 场景 | 实测 |
|---|---|
| A 首次 Digest | miss，真实构建字面阅读特征；完整新旧材料合同一致 |
| B 同源二次 | hit；与 A 的 Digest 及组装材料包相同 |
| C 2026安全检查 → 2028宣传培训，同时更改 style/scene | hit；当前任务、required evidence 顺序、时间风险和 Brief 绑定重新生成；该任务下新旧合同一致 |
| D 增加“测试街道开展应急演练。” | miss，新旧路径同步新增 `EV-DF4EB5B6AF34`，其他事实不漂移 |
| E 逐字段对比 | anchor_input、pre_brief_packet、evidence_writing_view、overlay、candidate_input 的所有 differences 均为空 |
| F 零调用 | Brief 请求、Writer 入口、Provider 工厂/构造/调用、socket 连接哨兵均为 0 次 |

E 包括所有事实全文、ID、scope、source_span、语义状态/约束、exact_values、限制、输入约束、全部不可用记录、丢弃原因和验证报告，不以条目数量替代比较。原“基本完成”保留完整原句及基线 COMPLETED，“明显提升”仍为基线 UNKNOWN；实验没有重新解释二者。

基线已有的 `duplicate_proposition`、`unsafe_placeholder`、两条 `auxiliary_does_not_fill_primary_type_gap` 和 `EV-264A1A5AACED` 的 `NOT_FACT_ANCHORABLE` 原样保留。移除本来就被拒绝的辅材时，`atomic_evidence.auxiliary_document_ids/discarded_evidence` 改变，合法 packet 不变；这是正确行为，不是未重新执行选择。

全部实际差异分类：

1. **相同输入的新旧 Brief 前合同**：没有差异。比较文件保存每个字段的旧值、新值及差异数组。
2. **新出现的实验行为**：显式本地 Digest 缓存 I/O、校验、命中/失效、字面特征复用；新增缓存/源绑定错误，不会静默降级或伪报命中。
3. **替代 Brief 输出字段**：`overview` 是计数及导航说明；`matters[].matter_id/title` 是当前阅读组编号及动作词标签；`summary.text` 是保守原句集合；`limits[].text` 是当前证据约束 JSON；`uncertainties` 仅取现有 overlay。与真实模型 Brief 的文字/事项组织等效性未测试，不能写成零差异。
4. **绑定哈希**：加入新的阅读视图后 `packet.digest`、`brief_sha256`、`candidate_input.material_packet_sha256`、Writer `user_hash` 必然绑定当前实验内容，不能拿这些值与无 Brief 的包要求相等。`registry_sha256` 不因此改变，候选事实输入不变。
5. **跨任务 A→C**：`atomic_evidence.required_evidence[0..5]` 的顺序、`task_json`（标题/style/scene）、当前 Brief 的 `input_sha256/uncertainties` 及相应包/Prompt 哈希变化；时间风险从 `CROSS_YEAR_TARGET` 变为 `STALE_OR_AMBIGUOUS_TARGET/CURRENT_COMPLETION_UNKNOWN`。这是两路径一致的任务重算。
6. **跨来源 A→D**：`atomic_evidence.evidence/required_evidence`、`atomic_report.evidence_count/evidence_type_distribution.action/remaining_atom_count`、`registry.EV-DF4EB5B6AF34/registry_sha256` 改变；当前 Brief 和包哈希随新增合法材料改变。Digest 新建。
7. **扩展场景**：主辅角色、coverage_role 可改变 ID、语义、选材和限制，但均与提交基线逐字段一致。当前任务缺年份/多年份产生原有时间不确定性。源 scope、限制、writability 和身份冲突仍按原 owner 执行。
8. **异常 overlay**：实验无法解释 findings 的结构或布尔阻断标记时，返回 `TASK_ASSEMBLY_OVERLAY_UNINTERPRETABLE`。这是新增实验输入限制，不是改写现有材料包合同；不能把任意自定义 overlay 宣称为可组装。外来 EV ID 由现有 outbound 校验拒绝，不裁剪伪造成功。

A/B/C registry SHA 为 `215d9a219dbc9016b26b19e79702dc9e4e88f5ba93d14c9312cd1fe3dfde5879`；D 为 `bd4eddeee9ede6a01b8549dba4d9d3a666c35e4254ef87c6fafe7a05a9f7793d`。

详细证据目录：`backend/tmp/w3-8b/final-verified/test_abcdef_contract_and_metri0/`：

- `A/B/C/D-comparison.json`：相同输入、完整旧新材料合同、逐字段差异、实验阅读视图。
- `cross_scenario_differences.json`：跨任务及跨来源全部实际字段旧值、新值和变化原因。
- `A/B/C/D-writer-preparation.json`：真实纯函数生成的完整 Writer Prompt；不属于模型调用记录。
- `metrics.json`：实际指标及各场景所有拒绝/不可用记录。

这些产物由测试生成，全部位于 ignored tmp，不暂存、不提交、不外发。

## 测试、失败记录和复现

使用当前仓库已有 `tmp/baseline-02b-r2r3/test-env/Scripts/python.exe`，未安装依赖，也未读取其旁边的旧测试 clone 作为源码。

在仓库根目录建立本轮父目录后，在 backend 执行：

```powershell
New-Item -ItemType Directory -Path 'D:\AI_Projects\gongwen-style-agent-commercial\backend\tmp\w3-8b' -Force | Out-Null
Set-Location 'D:\AI_Projects\gongwen-style-agent-commercial\backend'
& '..\tmp\baseline-02b-r2r3\test-env\Scripts\python.exe' 'tests/run_w3_8b_offline.py' -q -s tests/test_material_digest_task_assembly.py --basetemp 'tmp/w3-8b/review-directed'
& '..\tmp\baseline-02b-r2r3\test-env\Scripts\python.exe' 'tests/run_w3_8b_offline.py' -q -s tests --basetemp 'tmp/w3-8b/review-full'
```

复测使用尚不存在的 basetemp；不要把 pytest 指向已有用户数据目录。上述命令是后续复现命令，本轮实际使用 `directed-1`、`directed-2`、`final-verified`。

- 修改前基线：现有离线 runner `tmp/w3-8a-p/run_offline.py -q tests --basetemp tmp/w3-8b/baseline-ready`，204 passed，11.47 秒。
- 定向最终：新 runner，44 passed，3.33 秒；`directed-2.log`。
- 完整后端最终：新 runner，248 passed，12.58 秒；`final-verified.log`。仅一条现有 AnyIO 弃用警告。
- 命中测试将 `_reading_units` 和 `_source_fragments` 替换为立即失败哨兵，证明不是重算后伪称命中；没有以 Mock 结果替代业务输出。
- 现有全套测试自身含其原有模型测试替身；它们不代表真实模型调用。本轮新增实验不提供任何假 Brief、Writer 或 Provider 成功输出。
- 定向测试阻断所有 socket 连接；全套 runner 只允许 loopback 以支持现有异步测试，禁止外网解析/连接。
- `git diff --check` 通过；对五个新增文件逐一执行 `git diff --no-index --check -- /dev/null <file>`，没有空白错误输出。严格 UTF-8 解码、无 U+FFFD 检查及四个 Python 文件的 `compile(..., 'exec')` 均通过。既有生产文件与 BASE_SHA 一致性测试通过。

未隐藏的失败：第一次基线运行因 pytest basetemp 父目录未建立而出现 93 个 setup errors，111 项通过；补建本轮父目录后基线 204 项通过。第一次定向运行 31 passed、1 failed，失败来自“移除已被拒绝辅材应改变 packet”的错误测试假设；新旧完整结果均表明应只改变 Atomic 选择/拒绝记录，修正该断言后全部通过。没有修改任何原有测试或产品标准。

最终文件检查脚本首次将 `git diff --no-index` 的正常“存在差异”退出码 1 误作检查失败；该命令没有错误输出。纠正退出码处理后，五文件检查、UTF-8 和语法检查全部通过；没有因此改动产品代码。

未运行：服务启动、真实页面、在线 RAG、真实 Brief/Writer、用户黄金样本生成、人工成稿质量对比。全套包含原有合成夹具 DOCX 测试，不能称为真实文章验收。

## 风险、重复实现与后续准入

1. 原句阅读索引可能比模型 Brief 更冗长，按动作词分组也可能不符合真实业务事项；Prompt/token 成本与 Writer 理解质量未证明。不能仅凭 schema 合法宣布完整替代了材料理解。
2. Digest 特征只作阅读辅助，事实仍唯一来自当前 registry。事实与语义合同没有改变；没有在 Digest 内运行 Evidence selection 或冻结任何最终语义状态。
3. 未重复实现切分、文字信号、ID、准入、状态传播、时间风险、Brief 校验或 Writer。新增 Digest 独立持久化封装与 Source Substrate 存在少量同类原子写入/校验代码；没有为抽公共缓存框架扩大修改既有 owner。
4. Source Substrate 私有缓存生命周期和打包环境源码指纹依然是生产接线前须关闭的非阻断债务；新 Digest 同样按私有运行数据管理，checksum 不防御能改 payload 和 checksum 的恶意本地写入者。大规模磁盘/延迟/清理未验证。
5. Brief LF/CRLF 资产哈希锁问题未修、未改、未在本轮重新认证其在线路径。实验根本不调用旧 Brief prompt，因此实验通过不代表原在线 Brief 问题已消失。
6. 准备后续生产接线验证的**实验技术前提已具备**：明确 owner、边界、合法输入、守恒和零调用证据。**生产准入尚不具备**：应先 External Review，并关闭既有生产前债务，再经另行授权安排真实材料/Writer质量及受控接线验证。

可提交给 External Review 做代码与实验边界审查，建议主控复核后进入；本轮未启动 External Review，也没有授权或进行模型调用。

## Git 状态与回滚

CHANGED_FILES / FINAL_GIT_STATUS：

```text
?? backend/app/services/generation/material_digest.py
?? backend/app/services/generation/task_assembly.py
?? backend/tests/run_w3_8b_offline.py
?? backend/tests/test_material_digest_task_assembly.py
?? docs/W3-8B-material-digest-task-assembly.md
```

暂存为空，既有 tracked 文件 diff 为空。没有 Commit、Push、PR、任务 Merge 或 External Review；没有修改 `.gitignore`，缓存、日志、派生数据都未进入待提交文件。初始 ignored tmp 中三个嵌套测试仓库未被使用；两个原临时目录枚举权限告警不影响本轮目录。

回滚入口：BASE_SHA 即 checkpoint。主控决定回滚时，将上述五个新增文件另存移出仓库，再切回保持在该 SHA 的 main；无需恢复任何旧 tracked 代码。不要 reset/clean 或删除原 tmp、资料与环境。本轮没有执行回滚。

## 大白话

这次实验拿掉的是“每次先让大模型整理一次材料”这次调用：把原材料里的动作词和限定信号先缓存起来，再按当前任务的合法证据，当场生成原句阅读卡片和风险说明。实验里实时 Brief 模型调用确实是零，Writer 模型也没有运行。

已测材料与旧路径逐字段一致，没有新增丢事实、改状态或非法 ID，任务判断没有存进 Digest。但我们还没证明这种较朴素的阅读卡片和模型 Brief 一样好读，更没证明写出的文章一样好。下一步适合由主控先复核，再进入 External Review；暂不接生产、不宣称文章质量完成。
