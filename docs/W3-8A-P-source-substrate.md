# W3-8A-P｜Source Substrate 验收与实现边界

文件类型：本轮验收报告及内部接口说明，不是第二套项目规则。

## 结果

W3_8A_P = PASS。范围仅为显式启用的 Source Substrate、确定性缓存和最终 Evidence 等价验证；不是生产上线、材料理解质量或文章质量验收。

```text
BASE_SHA = 46adf2f894dc5b005b6e16efa7cfe3e1cc2de735
BRANCH = codex/w3-8a-p-source-substrate
CURRENT_HEAD = 46adf2f894dc5b005b6e16efa7cfe3e1cc2de735
SOURCE_SUBSTRATE_OWNER = backend/app/services/generation/source_substrate.py

SOURCE_SUBSTRATE_REUSABLE = YES
SECOND_USE_CACHE_HIT = YES
TASK_CHANGE_REUSES_SOURCE_SUBSTRATE = YES
SOURCE_CHANGE_INVALIDATES_CACHE = YES
FINAL_EVIDENCE_EQUIVALENT = YES
SOURCE_FACT_LOSS_COUNT = 0
SEMANTIC_STATE_DRIFT_COUNT = 0
ILLEGAL_EVIDENCE_ID_COUNT = 0
UNEXPLAINED_EVIDENCE_OMISSION_COUNT = 0
TASK_DEPENDENT_STATE_CACHED = NO
PROJECT_MODEL_CALL_COUNT = 0
PRODUCTION_ROUTE_CHANGED = NO

FIRST_BUILD_MS = 33.328
CACHE_LOAD_MS = 7.494
TASK_REBUILD_MS = 18.022
BLOCKERS = NONE（本轮边界内）
```

计时来自 `backend/tmp/w3-8a-p/final-verified/test_abcd_equivalence_and_timi0/metrics.json`，同一次完整回归中的 A/B/C 场景，单位为毫秒，单次小样本实测；不承诺整体生成加速。TASK_REBUILD_MS 只计调用现有 Anchor 构建及其校验，不含 RAG、Brief、Writer 或比较器。

## 当前真源与推理闸

1. 实际问题：当前代码对同一源文本重复切分，尚无可复用源底座。
2. 证据：`build_atomic_evidence_pack` 和 `build_full_draft_anchor_input` 均使用 `_source_fragments`；已有 Brief 则绑定当前 task、registry 和 restrictions。
3. 已验证：`_source_fragments` 仅依赖文本、模式及当前规则；无 task、模型或外部调用。
4. 未作事实断言：缓存是否显著降低实际整篇文章延迟；源片段是否足以支持以后替代 Brief。
5. 尚未验证：用户真实资料库规模、打包程序部署、文章质量及端到端生成。
6. 概念创建者：本轮新增的 `load_source_substrate`。
7. 调用者：显式内部调用及本轮测试；无生产路由调用。
8. 消费者：Atomic 构建和 Anchor 原文定位；后续材料包消费其原有输出。
9. 唯一新 owner：`source_substrate.py` 管理源快照、缓存及失效。
10. 已有同职责逻辑：源切分算法仍由 `atomic_evidence.py::_source_fragments` 负责，直接复用，未复制算法。
11. 最小实现：缓存原文和切分序列，给两个现有构建函数增加可选内部参数。
12. 最大风险：把任务状态缓存，或缓存片段变化导致静默丢证据。
13. 阻断验证：整结构对比提交基线；换任务/主辅/覆盖角色/顺序/选择/overlay；缓存损坏、失效及准入拒绝测试。
14. 文章质量：未进入写作阶段，不声称质量改善；缓存路径给下游的完整材料包与基线相同。
15. 事实安全：原 ID 生成、状态传播、准入、校验和拒绝条件不变。
16. 用户行为：默认调用及公开 API 不变；仅显式启用者新增本地缓存读写及缓存错误。
17. 旧模式：没有增加、恢复或切换任何生成路线。

## source-stable / task-dependent 实际边界

缓存保存：

- 当前 `selected_chunks` 的 document_id、chunk_id、未经本模块改写的有效源文本；正文完整保存，不仅保存被切分器接受的部分。
- 现有切分器输出的片段序列，保留顺序和重复；exact / 非 exact 模式隔离。
- 来源属性指纹：来源标题、来源位置、显式 scope、来源限制和来源提供的 writability 等。只保存指纹用于失效，不把这些属性解析成准入结论。
- 格式版本、切分模式和 Atomic owner 源码指纹；源码指纹规范化 checkout 换行，保守地在 owner 任何源码变化时失效。

实时重新执行：

- 现有 RAG 及当前任务的选材结果。本模块接收现有检索结果，不改检索逻辑。
- 主辅文档选择、材料角色、覆盖角色、内容角色、标题过滤、上下文语义识别。
- 原子提取及 Evidence ID 生成。现有 ID 输入包含 `document_role`，所以它不是本轮可以冻结的源片段属性。
- 辅材补缺、跨材料去重、安全数量上限、冲突标记、排序、required/optional evidence。
- 原子校验、来源绑定、scope、writability、admission、Fact Anchor 及 registry。
- 当前限制、时间 overlay、任务字段、Brief 输入绑定和最终 Evidence 组织。

这里的“同一 source”是相同的源片段集合及上述有效来源属性。单纯 task/主辅/评分/检索顺序/覆盖角色变化不改缓存键；实际选中源片段集合变化会形成另一份底座。本轮没有建立整个资料库的全量文档缓存，也没有证明任意任务变更导致 RAG 选材改变时仍能复用整份底座。

## 接口与数据流

```python
from pathlib import Path
from app.services.generation.source_substrate import load_source_substrate
from app.services.generation.atomic_evidence import build_full_draft_anchor_input

loaded = load_source_substrate(retrieval, Path("tmp/source-substrate-cache"))
anchors = build_full_draft_anchor_input(
    task, retrieval, source_substrate=loaded.substrate,
)
```

`source_substrate=None` 仍执行原有路径。可选参数是内部复用接口扩展；没有修改 Atomic Evidence / Fact Anchor 输出 schema、事实语义、授权或校验合同。无需导入新模块的生产调用不会创建缓存。

加载器直接调用既有切分器构建缓存。命中后使用缓存片段，Atomic/Anchor 仍运行原算法；测试将原切分器替换为立即报错的哨兵，证明命中不是重新切分后伪报命中。哨兵不提供事实、模型响应或模拟业务成功。

缓存以确定性 key 命名，写入临时文件后原子替换。损坏/错误绑定明确报 `FullArticleContractError`，不返回假命中或静默成功。返回值只含不可变字符串/元组，调用者取得的片段列表可修改而不污染快照。

## 实际比较与差异

比较器通过 `git show HEAD:backend/app/services/generation/atomic_evidence.py` 加载本仓库提交基线执行，未读取旧工作区，未复制历史实验实现。输入是测试中明确标识的合成文本，调用真实确定性业务 owner，不使用固定 Evidence 结果。

| 场景 | 缓存结果 | 比较结果 |
|---|---|---|
| A 首次构建 | miss | 完整输出与提交基线一致 |
| B 同源二次使用 | hit | 与 A 及提交基线一致 |
| C 改任务标题和年份 | hit | 当前任务下与提交基线一致；required_evidence 顺序、时间 overlay、Brief 输入绑定变化，registry 保持一致 |
| D 原文增加一条动作 | miss | 新旧路径同步新增 `EV-DF4EB5B6AF34`，没有额外差异 |

A/B/C 的 registry SHA256 为 `215d9a219dbc9016b26b19e79702dc9e4e88f5ba93d14c9312cd1fe3dfde5879`；D 为 `bd4eddeee9ede6a01b8549dba4d9d3a666c35e4254ef87c6fafe7a05a9f7793d`。

主辅身份变化会改变原 ID 生成输入及辅材补缺结果；覆盖角色可改变原算法的状态和组织字段。这些跨任务差异在新旧路径中一致，并非缓存引入的漂移。完整对比包括事实全文、所有 ID、semantic_state、exact_values、限制、来源、顺序、拒绝记录、校验报告、材料包、candidate_input 和 overlay，不只是比较数量。

原有过滤没有消失：A—D 均包含 `duplicate_proposition`、`unsafe_placeholder`、两条 `auxiliary_does_not_fill_primary_type_gap`，以及一条 `NOT_FACT_ANCHORABLE`（`EV-264A1A5AACED`）。这些是基线已有的明确拒绝原因，底座仍保留原文。零遗漏指标表示相对基线没有新增遗漏，不表示现有解析器已把原文所有含义转换成合法 Evidence。

语义保留示例：原“基本完成”仍按基线输出 COMPLETED，事实原句保留“基本”；“明显提升”仍按基线输出 UNKNOWN；没有为了缓存重新解释二者。这次证明一致性，不是重新认证既有分类质量。

本轮新增的实际运行差异只有本地缓存 I/O、源指纹验证、显式启用后的片段复用和缓存错误；未改 Brief、Writer、生产入口、RAG 架构或 FLOW 合同。

## 测试与证据

本机复用当前仓库 `tmp/baseline-02b-r2r3/test-env` 的已安装 Python 依赖；未读取其中嵌套 clone 作为代码真源，未安装新依赖。

在 `backend` 目录执行的主要命令：

```powershell
& '..\tmp\baseline-02b-r2r3\test-env\Scripts\python.exe' 'tmp\w3-8a-p\run_offline.py' -q -s tests/test_source_substrate.py --basetemp 'tmp/w3-8a-p/directed-3'
& '..\tmp\baseline-02b-r2r3\test-env\Scripts\python.exe' 'tmp\w3-8a-p\run_offline.py' -q tests --basetemp 'tmp/w3-8a-p/final-verified'
```

- 定向独立运行：30 passed，3.25 秒；随后增加来源章节标题失效用例，最终全套中的 31 项底座测试全部通过。
- 最终全套：204 passed，21.74 秒，1 条已有 AnyIO 弃用警告。
- 此前现有全套：173 passed，10.90 秒。
- `git diff --check` 通过；未改变其他已有代码文件。
- 本地 runner 只允许回环连接，阻断外网；定向测试还禁止实际 Provider 获取和调用。没有启动项目服务或调用外部模型。
- 没有运行前端、在线 RAG、真实 Brief/Writer 或黄金样本生成。现有全套回归包含合成夹具的 DOCX 导出测试，不是用户真实文章导出或质量验收。

日志位于 `backend/tmp/w3-8a-p/`。A/B/C/D 各自的 `*-comparison.json` 含相同输入下完整基线和新路径结果，位于 `final-verified/test_abcd_equivalence_and_timi0/`。它们由测试生成、被 Git 忽略，不提交。

失败记录未隐藏：首次断网 runner 禁止所有 socket，导致本机异步回环失败；该轮另有一次既有 `test_flow4_v3_real_owner_roundtrip_and_readback[CASE_2]` 失败，其独立根因未定位，不把它直接归因于网络。调整为仅允许回环后，该用例及全套回归通过。首次定向测试额外探测 Brief prompt 时暴露下述基线资产问题，随后把比较边界限定到本轮要求的完整 Brief 前材料包，没有修改 Brief 资产、锁或校验规则。

## 已知风险和未证明事项

1. Brief 基线资产问题：工作区及 HEAD 的 `brief_schema.json` 字节相同，实际 LF 哈希为 `e29b2d3331d1e9a81f5a4d1e85ca6a1abd171d4295a41535b454d8a2abbfd47d`，源码锁为 `d5f3832b9fad0e57cce8026bbf67cdb9a5c4e84010b7dbf41af77112c7e41ecf`；将相同内容转为 CRLF 的哈希恰等于该锁。本轮未改此文件。真实 `brief_prompt()` 因 `CASE32_CONTRACT_ASSET_CHANGED` 失败，不得把 Evidence 等价写成 Brief 端到端可用。后续任务需单独处理或明确约束比较边界。
2. 本地缓存保存完整源文本，属于私有运行数据，不应入 Git、外发或放在共享不可信目录。checksum 只检测意外损坏，不能防御有能力同时修改内容和 checksum 的本地恶意写入者；仍需信任内部调用者和缓存目录。
3. 当前缓存按选中源集合存储，片段查找是线性的；大资料库性能、磁盘占用/清理策略、打包后源码指纹可用性未验证。
4. 缓存最小切分结果，没有缓存原子提取或材料理解；本轮不能证明移除 Brief 或整篇文章提速，也不能代替用户黄金样本质量验收。

## Git、回滚与复核

开始时 main tracked/untracked 状态干净、暂存为空，仅有原有 ignored tmp。检查到 3 个位于原有 tmp 下的嵌套测试 clone，未用作代码来源；两个旧临时目录枚举权限不足，不影响当前任务文件。

从 main 创建 `codex/w3-8a-p-source-substrate`，基准 HEAD 同时作为 checkpoint。没有创建新 commit、暂存、Push、PR、Merge 或 External Review。

CHANGED_FILES / FINAL_GIT_STATUS：

```text
 M backend/app/services/generation/atomic_evidence.py
?? backend/app/services/generation/source_substrate.py
?? backend/tests/test_source_substrate.py
?? docs/W3-8A-P-source-substrate.md
```

暂存区为空；新增日志/缓存/测试产物均在 ignored `backend/tmp/w3-8a-p/`。本轮没有最终 commit message。

回滚方案（仅在主控明确决定后执行）：把本轮新文件另存后，用 `git restore --source=46adf2f894dc5b005b6e16efa7cfe3e1cc2de735 -- backend/app/services/generation/atomic_evidence.py` 撤回唯一已有代码文件的改动；本轮新文件可移出仓库保存，再切回 main。不要删除或重置原有用户 tmp、环境、嵌套 clone；当前未执行回滚。

具备提交给 External Review 的代码和可复现证据条件，但按工单没有启动。下一步由主控复核决定。

## 大白话

现在仓库有了真正可调用、能落盘复用的“原材料切分底座”。同一份材料换写作任务时，可以复用切好的片段；该怎么选、怎么定角色、怎么形成证据、哪些内容不能写，仍由现有规则当场重新计算。已测案例相对旧路径没有丢事实、换 ID 或改语义。

此前 W3-8B 缺少当前仓库中的底座 owner 和实测基准，现在这两个前提已经具备。主控复核后才适合继续研究 Digest 和 Task Assembly；能不能真正去掉实时 Brief，仍是 W3-8B 要证明的问题。
