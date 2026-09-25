# P1-TOPIC-FIRST-RANKING 验收记录

日期：2026-09-25。性质：本轮实现说明及离线验收记录，不是新的项目规则真源。

工作区：`D:\AI_Projects\gongwen-style-agent-commercial`。
修改前后 HEAD 均为 `b989aeb16fe38733136dfdd7a5b1074b094673ca`，与用户指定的 PR #11 基线一致。
工作分支：`codex/p1-topic-first-document-ranking`，从该基线新建，作为本轮 Git 边界。
依本工单要求，不创建 checkpoint commit，不 add、commit、push 或创建 PR。

## 已确认的问题及边界

`retrieve_documents` 调用 `_aggregate_documents` 创建文档得分、gate 状态及排序，
随后由 `_select_documents` 消费顺序来选择 primary / auxiliary；生成编排及
`full_draft_generation` 消费返回结果，PR #11 的 trace 投影记录得分及原始分项。
文档排序的唯一 owner 是 `document_retriever.py::_aggregate_documents`。

当前源码证实：旧第一排序键为 `_source_role_priority`；只要有很弱的词面主题支持，
role 兼容文档就可以排在得分更高的不兼容文档之前。此外，旧 role 系数 0.080
带来最多 0.160 的候选间分差，与文种等偏好叠加后也可能抵消明确的主题优势。
两类故障均已由合成回归复现。用户提供的真实案例背景未在本轮重新调用模型复现。

修改仅涉及上述函数的评分偏好及排序键。raw retrieval、query、vector_only、
RAW_CANDIDATE_LIMIT、分类器、relevance gate、数量上限、chunk selection/budget、
Atomic Evidence、辅助证据补缺限制、Writer/Prompt、Fact Safety、生成路由及前端未修改。
AST 对比覆盖 owner 中除 `_aggregate_documents` 以外的全部顶层代码；函数内各原始
score components、gate、source role 和 chunk 数据另与固定基线运行结果逐项比较。

推理闸结论：最小改法是移除 role 硬优先级并限制次级偏好的分差，不引入第二个排序 owner。
主要回归风险是 topic 接近时 role 失效、gate 漂移、召回增多、trace 不再解释顺序及 guard 放宽；
分别由反向边界、固定基线差分、调用计数、真实 trace 投影/落盘、精确哈希及防篡改测试阻断。
用户可见的变化限定为文档顺序及由顺序产生的材料选择；不放宽事实安全，不恢复旧路线。
实际文章质量影响尚未验证，按工单禁止真实生成，因此不作产品质量达标结论。

## 确定性排序规则

```text
topic = 0.625 * semantic_aggregate
      + 0.180 * title_topic_match
      + 0.060 * content_topic_match
      + 0.015 * business_domain_match

secondary = 0.020 * source_role_compatibility
          + 0.005 * document_type_match
          + 0.005 * auxiliary_match

document_score = topic + secondary - 0.350 * negative_title_match
```

四项 topic 权重和负向惩罚保持原值。secondary 的取值范围为 `[-0.020, 0.030]`，
候选之间最多相差 `0.050`；在原有六位小数精度下，明确超过该幅度的主题优势优先，
接近的主题仍可由 role、文种及辅助匹配决定顺序。negative title 的独立 gate 不变。

排序依次按 document_score 降序、semantic_aggregate 降序、max_chunk_similarity
降序、source_title 升序、document_id 升序。无需新增 trace 字段：PR #11 的
document_score、原始 score components、role compatibility、PRIMARY / AUXILIARY /
REJECTED、raw 候选和门禁信息足以解释结果，现有 trace schema 与生产投影代码不变。

合成主题“星图归档流程”的集成回归证据如下（另外覆盖“样本编码校核”，并覆盖工作总结、
调研报告、领导讲话三种目标文种；生产代码没有任何新增主题或文种分支）：

| 候选 | semantic | title/content | domain | role | 文种匹配 | 旧 score / rank / 选择 | 新 score / rank / 选择 |
|---|---:|---:|---:|---:|---:|---|---|
| A | 0.933 | 1.0 / 1.0 | 1 | -1 | 0 | 0.758125 / 3 / AUXILIARY | 0.818125 / 1 / PRIMARY |
| B | 0.833 | 0.2 / 0.2 | 0 | 1 | 1 | 0.668625 / 1 / PRIMARY | 0.593625 / 2 / AUXILIARY |
| rejected | 0.400 | 1.0 / 1.0 | 0 | 1 | 1 | 0.590000 / 2 / REJECTED | 0.515000 / 3 / REJECTED |

仅含 A/B 的最小回归中，排序由 B、A 变为 A、B。上表第三个候选始终因
`semantic_below_threshold` 被拒绝，其 rank 变化不代表 gate 改变。
反向边界包括主题完全相同及 semantic 仅差 0.01，两种方向的 role 兼容候选均仍可优先。
另有用例验证 0.0625 的主题得分优势能够超过全部次级偏好的 0.050 分差。

## Protected-owner 精确批准

四个已有 guard 仍扫描原路径、比较原历史基线。仅对 `document_retriever.py` 使用
`backend/tests/p1_ranking_approval.py` 保存的精确 SHA-256：

```text
fb5e709a1cc9166c024cbf6317f889f819223cb44cb3582ba972989b04d57348
```

哈希只归一化 CRLF 为 LF，不移除空白、注释或任何其他内容。没有把文件加入永久免检集合，
没有推进任何历史 BASE_SHA，没有缩小扫描范围。四个防篡改用例在内存中为 owner 追加一个
换行，分别调用每个原始 guard，均确认触发精确批准失败，不实际修改生产文件。

旧 PR #11 同主题夹具的 parity 测试只允许本轮明确授权的 document_score 数值差：
`-0.060 * role -0.015 * type -0.015 * auxiliary`；剩余字段、材料包、证据注册表、
完整 Prompt、Writer 参数及交付结果继续作完整相等比较。

## 实际执行和证据

修改前相邻基线：19 passed。新增合成回归在未改生产代码时：8 个预期失败、11 passed，
失败均落在新排序/选择预期上。首次综合验证：61 passed、1 failed，失败为新增 trace
断言未遵循既有六位小数精度；已修正测试断言，未更改 raw similarity 生产逻辑。

最终验证：**63 passed、0 failed、0 skipped**（24 个 P1 用例、16 个 RAG 可观测性用例、
21 个 production task assembly 用例、2 个其他受影响 guard；四个 guard 全部包含其中）。
`git diff --check` 通过。

实际最终命令如下。现有 `.venv` 的 Python launcher 无法启动，因此使用已安装的 Codex
Python 3.12.14 和项目现有 site-packages；没有安装依赖或改写运行环境配置。

```powershell
Set-Location -LiteralPath 'D:\AI_Projects\gongwen-style-agent-commercial\backend'
$env:PYTHONPATH = (Join-Path (Get-Location).Path '.venv/Lib/site-packages')
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
& 'C:/Users/peng chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' tests/run_w3_8c_2a_offline.py -q tests/test_topic_first_document_ranking.py tests/test_rag_observability.py tests/test_production_task_assembly.py tests/test_stable_material_assets.py::test_production_and_fact_owners_remain_unchanged tests/test_material_ingestion.py::test_protected_generation_retrieval_and_legacy_owners_unchanged --basetemp tmp/p1-topic-first-final --junitxml=tmp/p1-topic-first-final.xml 2>&1 | Tee-Object -FilePath tmp/p1-topic-first-final.log
git diff --check
```

以上为已执行命令记录；再次运行时应为 basetemp、日志和 XML 选择新的路径，保留本轮证据。

- 日志：`backend/tmp/p1-topic-first-final.log`。
- JUnit：`backend/tmp/p1-topic-first-final.xml`。
- 前后 trace：`backend/tmp/p1-topic-first-final/test_raw_gate_roles_and_search0/p1_ranking_comparison.json`。
- 本轮 RAG trace：同目录 `RAG_RETRIEVAL_TRACE.json`。

离线 runner 的审计计数：external_network=0、real_embedding=0、real_model=0、
real_user_data_access=0；110 次 asyncio socketpair 是本地事件循环 IPC。
full_article_drafting 相邻回归仅使用测试内的合成资料、Embedding 替身和 Writer 替身，
没有真实生成、真实 LLM/Embedding 请求或读取用户资料。

仅有一个现有 Starlette / AnyIO BlockingPortal 弃用警告，修改前基线亦存在。
真实任务排序回放、真实文章生成和黄金样本质量验收均未运行（遵守本轮范围）。

## 文件与 Git 状态

本轮共 8 个文件，全部未暂存：

1. `backend/app/services/retrieval/document_retriever.py`：唯一生产文件，仅文档排序。
2. `backend/tests/test_topic_first_document_ranking.py`：新增，合成差分及边界测试。
3. `backend/tests/p1_ranking_approval.py`：新增，精确批准哈希。
4. `backend/tests/test_rag_observability.py`：严格授权 score delta、guard 维护。
5. `backend/tests/test_production_task_assembly.py`：仅 guard 维护。
6. `backend/tests/test_material_ingestion.py`：仅 guard 维护。
7. `backend/tests/test_stable_material_assets.py`：仅 guard 维护。
8. `docs/P1-TOPIC-FIRST-RANKING.md`：本记录。

原有 8 个未跟踪用户文件保留，未读取、修改或暂存：`backend/low-vs-xhigh-runs.zip`、
`backend/manual_writer_flash_result.json`、`backend/manual_writer_flash_stream_result.json`、
`backend/manual_writer_result.json`、`backend/proxy_check.txt`、
`backend/writer_flash_stream_test.json`、`backend/writer_flash_test.json`、`backend/writer_test.json`。
初始 ignored 清单包含既有缓存/资料/输出；部分旧 tmp 子目录列举遇到权限拒绝，未更改权限，
最终测试使用新的隔离 tmp 路径。工作源码范围没有发现嵌套仓库或子目录 Agent 规则。

回滚基点为上述 BASE_SHA。本轮未执行回滚；经复核没有后续改动后，可按明确路径恢复
5 个已跟踪修改文件，另处理本轮新增的 2 个测试支持文件及本记录。不要运行全仓 reset/clean，
不要删除原有未跟踪文件。例如只恢复已跟踪修改：

```powershell
git -C 'D:\AI_Projects\gongwen-style-agent-commercial' restore --source=b989aeb16fe38733136dfdd7a5b1074b094673ca --worktree -- backend/app/services/retrieval/document_retriever.py backend/tests/test_material_ingestion.py backend/tests/test_production_task_assembly.py backend/tests/test_rag_observability.py backend/tests/test_stable_material_assets.py
```

结论：P1 实现与指定离线验收完成，可供 ChatGPT 审查。未创建提交或远程产物，未声称真实文章质量完成。
