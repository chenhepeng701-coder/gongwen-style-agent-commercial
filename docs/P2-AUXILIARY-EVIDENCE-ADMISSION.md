# P2-AUXILIARY-EVIDENCE-ADMISSION 验收记录

记录日期：2026-09-26。本文是本次修改与离线验收记录，不是新的项目规则真源。

## 范围与实际修复

- BASE_SHA / 修改前与修改后 HEAD：`8c7b57130a897c8b51e0b432a344abd0cbc08675`。
- 本地分支：`codex/p2-auxiliary-evidence-admission`。未暂存、未提交、未推送、未创建 PR。
- 唯一生产文件：`backend/app/services/generation/atomic_evidence.py`，仅删除 16 行，没有新增生产代码。
- 删除构建器中的 `auxiliary_does_not_fill_primary_type_gap` 分支和仅服务于该分支的 `primary_types` 状态。
- 下游实测发现校验器还用 `roles_by_type` 产生 `auxiliary_overlap`，重复执行相同类型排他合同；同步删除该校验及其死状态，否则已接受的辅助证据仍无法进入 Registry。
- evidence_type 分类、解析器、语义状态、重复命题、来源绑定、ID、复制保护、事实安全、Primary 优先顺序均保留。未修改检索、选择或预算、Writer、Prompt、Claim Binding、路由或前端。

本轮只证明合成材料的准入与下游合同。未运行真实 Writer、真实资料库生成或黄金样本验收，不能据此宣布真实故障的 73 条均已恢复或文章质量已经通过。

## 测试与保护机制

P2 专项共 24 项，覆盖普通/精确来源模式、同类型不同命题、两个辅助文档实际贡献、已有 document_id 范围内去重、全部 12 种语义状态和限定词、状态冲突、Registry、material packet、Writer 输入序列化及来源边界。

合成下游验收结果：

```text
AUXILIARY_EVIDENCE_ACCEPTED = 2
AUXILIARY_DOC_1_CONTRIBUTES = YES
AUXILIARY_DOC_2_CONTRIBUTES = YES
AUXILIARY_TYPE_EXCLUSION_COUNT = 0
DOWNSTREAM_REGISTRY_CONTAINS_AUXILIARY = YES
MATERIAL_PACKET_CONTAINS_AUXILIARY = YES
ILLEGAL_EVIDENCE_IDS = 0
```

来源保护测试使用长文档资产、有限授权片段、标题残留、占位符和不可解析长句；通过真实资产组装、独立解码最终 Writer 输入，确认合法 ID 保留，完整 chunk、完整文档、来源标题及超长原文不泄漏。只构造 Prompt，不调用真实 Writer。

冲突测试同时证明：可写的 PLAN / COMPLETED 同源命题保留各自状态并带复核限制；必要冲突状态落入 unavailable 时，与未修改基线相同地返回 `MATERIAL_BRIEF_CONFLICT_CONTEXT_REQUIRED`，不静默裁决或删除冲突上下文。后者是既有保护行为，本轮不改动。

`backend/tests/p2_admission_approval.py` 为本次生产内容固定批准哈希（仅规范化 CRLF）：

```text
SHA-256 = 16efd41b4688f987f1c6be391f7ccc1e2bf79386f06d0143cc242a9d7a6865ee
```

5 项受影响 guard 接入该精确检查：material digest、stable assets、production assembly、material ingestion、RAG observability。扫描范围、其他 owner 比较和历史批准版本均保留。5 项负向测试逐一在内存模拟 Atomic 文件增加一个换行，均要求对应 guard 抛出精确批准不匹配错误；没有修改磁盘生产文件来做破坏实验，也没有加入永久 allowlist。

Source Substrate 原先动态读取 HEAD 的比较基线固定为本轮修改前的同一 SHA，避免未来提交后随 HEAD 漂移。另两套历史比较仍使用原 SHA。三套比较只对冻结源码显式应用本次 16 行删除，完整字段比较不变，删除块缺失或重复即失败；独立专项继续使用未修改的 BASE_SHA 验证前后差异。另有逐字节测试证明当前生产文件恰为 BASE_SHA 删除这 16 行，不能掩盖其他改动。

旧 Atomic 测试、观测拒绝原因测试和移除辅助文档后的 packet 测试已更新新合同。移除辅助文档现在必须精确移除其合法 ID，其他 Registry 记录逐字段不变。历史 W3-8A-P / W3-8B 报告中关于类型排他与 packet 不变的结论仅描述当时基线，不适用于 P2 后的合同。

## 实际执行与结果

| 范围 | 通过 |
| --- | ---: |
| P2 专项 | 24 |
| Atomic Evidence 原文件完整回归 | 30 |
| Source Substrate | 31 |
| Material Digest / Task Assembly | 44 |
| Stable Material Assets | 71 |
| Writer Material View | 47 |
| Fact Safety Delivery Policy | 7 |
| M3 Semantic State 相邻用例 | 2 |
| RAG 保护及拒绝原因脱敏 | 2 |
| Production Assembly guard | 1 |
| Material Ingestion guard | 1 |
| P1 Topic First Ranking（含原保护负向测试） | 24 |
| **最终回归总计** | **284** |

最终结果：0 failed、0 errors、0 skipped。1 条既有 Starlette / AnyIO 弃用警告，不影响本次结果。

修改前基线：179 passed。测试驱动过程保留了中间失败记录：初始新合同复现 17 项旧规则失败和 1 项合成资产夹具缺少 `chunk_text`；随后发现并修复重复校验；冲突夹具进一步分成可交付复核状态与既有拒绝状态两种测试；相邻回归曾有 1 项旧 packet 不变断言失败，更新为精确 ID 差异合同后最终全部通过。没有把中间失败隐藏为通过。

现有 `backend/.venv/Scripts/python.exe` 启动器指向不可启动的 WindowsApps 路径。使用本机已安装的 Codex Python 3.12.14，加载仓库现有 Python 3.12 site-packages；未安装或升级依赖，未改动虚拟环境。

最终可复现命令（从项目根目录运行；basetemp 须选新的任务目录）：

```powershell
Set-Location -LiteralPath 'D:\AI_Projects\gongwen-style-agent-commercial\backend'
$env:PYTHONPATH = 'D:/AI_Projects/gongwen-style-agent-commercial/backend/.venv/Lib/site-packages'
$env:PYTHONIOENCODING = 'utf-8'
$p2Tests = @(
    'tests/test_auxiliary_evidence_admission.py'
    'tests/test_flow3_v3_atomic_evidence_generation.py'
    'tests/test_source_substrate.py'
    'tests/test_material_digest_task_assembly.py'
    'tests/test_stable_material_assets.py'
    'tests/test_writer_material_view.py'
    'tests/test_fact_safety_delivery_policy_reset.py'
    'tests/test_m3_section_writer.py::test_semantic_state_is_preserved_not_a_model_editable_field'
    'tests/test_rag_observability.py::test_retrieval_business_code_and_protected_owners_unchanged'
    'tests/test_rag_observability.py::test_actual_discard_reasons_and_atomic_redaction'
    'tests/test_production_task_assembly.py::test_protected_owners_unchanged_from_task_base'
    'tests/test_material_ingestion.py::test_protected_generation_retrieval_and_legacy_owners_unchanged'
    'tests/test_topic_first_document_ranking.py'
)
& 'C:/Users/peng chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' tests/run_w3_8c_2a_offline.py -q @p2Tests --basetemp tmp/p2-auxiliary-review-new --junitxml=tmp/p2-auxiliary-review-new.xml --tb=short
git diff --check
```

实际最终运行使用 `tmp/p2-auxiliary-final-20260926`，日志为 `backend/tmp/p2-auxiliary-final-20260926.log`，JUnit 为同名 `.xml`。修改前、中间与最终 XML 均保留在 ignored 的 `backend/tmp` 下，没有纳入版本管理。

离线 runner 阻断真实模型、Embedding、网络和用户数据访问，最终计数全部为 0。另已执行 `git diff --check`，全部 10 个变更 Python 文件的严格 UTF-8 解码和内存语法编译通过。未执行前端构建、真实页面、真实文章生成或导出，本轮没有这些改动。

## 文件及 Git 边界

生产变更：

- `backend/app/services/generation/atomic_evidence.py`

测试变更（7 个修改、2 个新增）：

- `backend/tests/test_flow3_v3_atomic_evidence_generation.py`
- `backend/tests/test_material_digest_task_assembly.py`
- `backend/tests/test_material_ingestion.py`
- `backend/tests/test_production_task_assembly.py`
- `backend/tests/test_rag_observability.py`
- `backend/tests/test_source_substrate.py`
- `backend/tests/test_stable_material_assets.py`
- `backend/tests/p2_admission_approval.py`（新增）
- `backend/tests/test_auxiliary_evidence_admission.py`（新增）

其他变更：本验收文档（新增）。开始时既有的 9 个未跟踪文件保留，本轮未修改、暂存或读取其内容。

Git 证据：项目根目录、分支、HEAD、staged、ignored 已检查；源码树未发现嵌套仓库，仅根目录 `AGENTS.md`。枚举 ignored 目录时部分历史 tmp 子目录有权限警告，不访问或更改这些目录，测试使用新的隔离目录。最终 staged 文件为空，HEAD 不变，任务改动全部留在工作区供审阅。

回滚基点为 BASE_SHA。若用户决定回滚，先确认后续没有其他改动，再仅恢复以上 8 个已跟踪文件到 BASE_SHA；对本轮新增的 2 个测试文件和本报告逐一移除即可。不要执行仓库级 reset/clean，不触碰原有 9 个未跟踪文件。本轮未执行回滚。

结论：本次限定的准入修复与离线工程验收通过，可供 ChatGPT 审阅。真实资料库的 73 条恢复情况留待合并后按同一资料库验证。
