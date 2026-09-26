# UPSTREAM-22-GENRE-MIGRATION 实施与验收记录

本文是本次迁移的验收报告，不是项目规则真源。项目规则仍以根目录 `AGENTS.md` 为准。

> 验收状态：工程集成与离线回归通过，具备 ChatGPT 复审条件。22 文种均接入既有基础设施，工作总结保持原生产行为；其余 21 文种为 ENGINEERING_READY，未开启生产、未取得真实文章商业验收。本轮依工单保持未暂存、未提交。

## 1. 目标、基线与边界

- TASK：`UPSTREAM-22-GENRE-MIGRATION`
- 项目：`D:\AI_Projects\gongwen-style-agent-commercial`
- BASE：`main @ ecfa1b9da98101fe74ecabc26b026ec8818a3488`
- 本轮分支：`codex/upstream-22-genre-migration`
- 实施阶段不暂存、不提交、不 push、不创建 PR；用户原有 11 个未跟踪文件不属于本轮修改范围。
- 目标：把唯一固定上游的 22 文种接入既有 Commercial Genre Infrastructure，其中工作总结保留现有生产实现，其余 21 文种补齐各自规则、Adapter、阅读覆盖、结构和质量合同。
- 本轮只做离线工程集成。禁止真实 Writer、Embedding、真实资料库处理、自动模型重试、新依赖、数据库变更和第二条 RAG/Writer/DOCX/Delivery 链路。

当前源码确认的迁移位置是既有 `GenreAdapter` / Registry / `CommercialGenreContract` 边界。正式生成仍由共享 Commercial Runtime 编排，读取统一资料与证据视图，经过既有 Writer、Fact Safety、Must-Deliver 和 FLOW4。迁移没有赋予文种规则证据准入或事实裁决权。

## 2. 推理闸与最小设计

已确认事实：原 `work_summary.py` 使用固定上游 `summary` 系统提示词、SHA256 锁、共享 Host Boundary 和项目生产 User Prompt；现有基础设施已经支持文种能力注册及商业成熟度门禁。原 Foundation 的 `report` 表示“汇报材料”，而固定上游 `report` 表示法定“报告”。两者必须明确区分。

最小改法：保留工作总结的原 Adapter、原实验快照、规则身份、合同和正式路由；新增一套完整、只读的上游文本快照；21 个新增 Adapter 统一由 Registry 注册，并各自绑定上游身份及语义合同。保留本地主键 `report` 的历史含义，将上游 `presentation` 映射到它；上游法定 `report` 使用本地主键 `statutory_report`。

唯一职责归属：

| 责任 | Owner |
| --- | --- |
| 上游原始文本、生成系统提示词和完整哈希目录 | `backend/vendor/official_document_drafting/6b4166a50b96733684b36da6336ea85a847cf3be/` |
| 离线快照构建、Git blob 与源生成一致性检查 | `backend/scripts/build_upstream_genre_snapshot.py` |
| 运行时上游 provenance、文件锁与 Adapter 装配 | `backend/app/services/generation/genres/upstream_suite.py` |
| 新文种检索角色、结构与商业能力配置 | `backend/app/services/generation/genres/upstream_genre_contracts.py` |
| 新文种质量观察和既有 QUALITY receipt | `backend/app/services/generation/genres/upstream_genre_quality.py` |
| 注册与历史 report 导出兼容 | `genres/registry.py`、`genres/upstream_adapters.py`、`genres/report.py` |
| 事实、语义状态、主体边界、交付、正文身份 | 原共享核心 Owner；本轮不迁移其职责 |

最大回归风险是工作总结行为变化、报告/汇报材料身份冲突、上游文种规则取得事实权威、未成熟文种被请求参数提升为可生产，以及快照来源或内容漂移。对应阻断入口为基线字节与合同比较、别名唯一性测试、Shared Runtime / FACT / Must-Deliver 合同测试、成熟度门禁测试以及 manifest 与逐文件双重完整性校验。

本轮未进行真实文稿质量比较；“词面观察符合合同”不能推出结构语义完整、文章成熟或商业质量通过。

## 3. 固定上游、许可证与获取证据

- 唯一上游：<https://github.com/zhaohui-yang/official-document-drafting>
- 固定 commit：`6b4166a50b96733684b36da6336ea85a847cf3be`
- 该 commit 指向的 Git root tree：`b649efe9a575dcf86cd1428b18605216b7260a63`
- [固定 commit 的 LICENSE](https://github.com/zhaohui-yang/official-document-drafting/blob/6b4166a50b96733684b36da6336ea85a847cf3be/LICENSE)：MIT。
- 版权声明：`Copyright (c) 2026 official-document-drafting contributors`
- LICENSE 原字节 SHA256：`2d8ba6968842d2d058a2f1f02f8a5252603e32124d79b684edf2ca119acd6a38`
- 本地 manifest SHA256：`69d3b0d1a830f070d97884a31fb620fe65b16f132906d8a8ffbeb308208272b9`

LICENSE 明确覆盖上游原创代码和文档，允许使用、复制、修改、分发及商业使用，条件是保留版权与许可声明；同时明确第三方字体及其他二进制资源可能适用独立许可。本地完整保留该 LICENSE，不把其 MIT 许可扩大解释到第三方字体。

获取只请求上述固定 commit 的 commit 对象、完整递归 Git tree 及白名单 raw 文本。首次浏览工具读取失败、普通沙盒网络被禁止，随后按本工单既有授权通过 `require_escalated` 获取指定上游。没有查找其他项目，没有读取 upstream HEAD。构建时按 Git 对象格式重算递归目录中的每个 tree 及 root tree，再对每个采用文件核验 Git blob SHA1、原始长度和本地 SHA256。

## 4. 快照组成、排除项与来源链

快照根路径简称 `SNAPSHOT`：

```text
backend/vendor/official_document_drafting/6b4166a50b96733684b36da6336ea85a847cf3be/
```

`SNAPSHOT/manifest.json` 是可审计的项目自有来源清单。它包含固定仓库、commit、tree、许可证、构建入口、22 文种身份、文件角色及逐文件 SHA256 / Git blob SHA1 / 长度。manifest 自身 SHA256 固定在运行时 Owner 中；不会在运行时从“当前文件内容”推导期望哈希。

本轮采用 136 个上游原字节文本文件，另生成 1 个 manifest：

- `LICENSE`。
- `prompts/core/*.md`。
- `prompts/doc-types/*/{meta.toml,spec.md,examples.md}`，完整保留上游文种源结构；系统提示词以 `include_examples=False` 构建，示例不进入生产 Prompt 或用户资料库。
- `prompts/profiles/default.toml`、`prompts/font-profiles/*.toml`、`prompts/layout-profiles/*.toml`。
- `assets/templates/*.md`。
- `assets/fonts/catalog.toml`、`assets/fonts/README.md`，仅为上游渲染器所需的字体名称/路径/许可元数据，不包含字体内容。
- `src/adapters/{doc_types,paths,profiles,rendering,shared}.py` 与 `src/adapters/offline/build.py`，只用于离线维护校验。
- `tests/test_offline_build.py`，保留上游覆盖完整文种目录的原始测试。
- `dist/offline/default/doc-types/*/system_prompt.md`，共 22 份原字节系统提示词。

明确排除的上游字体路径：

```text
assets/fonts/仿宋_GB2312.ttf
assets/fonts/方正小标宋简.TTF
assets/fonts/楷体_GB2312.ttf
assets/fonts/黑体公文字体.ttf
```

这些二进制未下载、未复制、未纳入快照。也未采用上游 Provider、联网搜索、Agent 生命周期、skill runtime、独立 DOCX 导出器或 generic `user_prompt_template.md` / 合并 Prompt 产物。

来源链为：

```text
固定 commit → 已验证 Git tree → meta.toml / spec.md / 共享源 / profile
→ 固定 Python offline builder 与 renderer
→ default + include_examples=False
→ 固定 dist 中的 system_prompt.md
→ SHA256 / Git blob 双核验及离线重建字节一致性检查
→ 既有 DraftingRules 与 GenreAdapter
```

上游 builder 仅使用 Python 标准库。本地在维护进程调用其纯文本渲染函数验证原生成物，生产只读取固定系统提示词，不执行该 builder，不下载 GitHub 内容，不自动更新 commit，也不引入外部可执行运行时或新包依赖。LF、UTF-8 和上游文件原字节均保留。

## 5. 精确目录、身份与 ruleset

目录由 pinned `prompts/doc-types/*/meta.toml` 实际解析取得，交叉核对 pinned 生成产物与上游目录测试：15 个法定公文、7 个常见正式材料，总计 22 个。

各行 ruleset ID 的共同前缀为：

```text
zhaohui-yang/official-document-drafting@6b4166a50b96733684b36da6336ea85a847cf3be/offline/default/
```

前缀后追加本表 `upstream_id`。源路径为 `prompts/doc-types/<upstream_id>-<文种>/spec.md`；元数据为同目录 `meta.toml`；本地系统提示词为 `dist/offline/default/doc-types/<upstream_id>-<文种>/system_prompt.md`；模板为 `assets/templates/<upstream_id>.md`。路径和完整 SHA 也逐项记录在 manifest 中。

| 文种 | 分类 | upstream_id | 本地 genre_id | 系统提示词 SHA256 |
| --- | --- | --- | --- | --- |
| 公告 | 法定公文 | `announcement` | `announcement` | `7d2796e18e437c9c12e733aabe080c09b53d4ce5c807bd495fc41d6d4e07f56c` |
| 批复 | 法定公文 | `approval` | `approval` | `9714cdd0bc9fd8375f94bd0812de539dbc983eba139101f192d38f37df3c0a83` |
| 简报 | 常见正式材料 | `briefing` | `briefing` | `6ea7ec2a8aa16d675440593d1bd4aae0a60c37cd31f564cc425b60336ef575f2` |
| 通报 | 法定公文 | `circular` | `circular` | `9a0e183d6c6eb2e5297c10d3767684d96d70e8d7358509adb572b8b65f4a9012` |
| 公报 | 法定公文 | `communique` | `communique` | `d5cdc525b7d5249a42a3bbf523f4398c7861149dd878d3f9942d9622a3a92318` |
| 决定 | 法定公文 | `decision` | `decision` | `95e5fb38d0d68569f899a29716bc8e1c863b99954a3d3f4ab4a2f51e85c6a546` |
| 函 | 法定公文 | `letter` | `letter` | `0c4805b4e9e6f20b5af135105fdbe440d6b8f562b98df45033b59af495da0f0f` |
| 纪要 | 法定公文 | `minutes` | `minutes` | `f6dcf578df85d86bec32c94da267a4d72dd1972c4dbd24d07c2471171daa954d` |
| 议案 | 法定公文 | `motion` | `motion` | `9f9262003ffe89512ee2da434de9f3ebf3a9b6020701e8a5fd4e742dec096efb` |
| 通知 | 法定公文 | `notice` | `notice` | `f590f3a5249d422c7bebb0aa80c464baa1f93b2e347e76e87fea537c272e74bf` |
| 意见 | 法定公文 | `opinion` | `opinion` | `944ed82db73e2832f134b684d5db37ef7cad1680f4f2a9a850cefb4a0f6ba3ad` |
| 命令（令） | 法定公文 | `order` | `order` | `38c304e0a809194a2490a78d921a570c0135d875a4ffaf0e83584b6dfcf62857` |
| 汇报材料 | 常见正式材料 | `presentation` | `report` | `b19ad7ba4def1ee2c5008a2d35d4d03d967c747464e9d841efb9577bb492ad0e` |
| 通告 | 法定公文 | `public-notice` | `public-notice` | `4e4e26b0202dd3402553b79eb1c584bb79390b0806ddb379afa7a21e86634848` |
| 回复函 | 常见正式材料 | `reply` | `reply` | `cb967faff0033650843fee645246dd61d0e236afd35fa7497e933eb857f402ad` |
| 报告 | 法定公文 | `report` | `statutory_report` | `78ce0ecca0a495dbdeda594b08d4909bee1d6c1f6476a90bf105f1e97a6861a2` |
| 请示 | 法定公文 | `request` | `request` | `f9bbaca88e69057fc12d185a7399008540a03f93882fbc481b951a33c4cf42cc` |
| 决议 | 法定公文 | `resolution` | `resolution` | `4a0ebc131bb61d37bd0a083a1648622ea870d0aa7ecb43179b05560cbb508960` |
| 情况专报 | 常见正式材料 | `special-report` | `special-report` | `76c35c2769002b80b906444f628983cf11b82b9a2d5fec6e88779b8ffcf9ba40` |
| 讲话稿 | 常见正式材料 | `speech` | `speech` | `b35a5426196f576f5bf323352ddd905766525f704f2527d06f16b13ec9fe178c` |
| 工作总结 | 常见正式材料 | `summary` | `work_summary` | `80c99367127e6eabca3a5329ec745bcd5bad46734b10aa577a478f4627f858b1` |
| 工作方案 | 常见正式材料 | `work-plan` | `work-plan` | `1cb1cf8437562a603f6546f25d568fdb46adca74deae5e53ac45683de0aaee2c` |

别名以 pinned meta 为来源，通过 Registry 做唯一解析。“令/命令/命令令”“会议纪要”“信息简报/新闻简报”“实施方案/方案”等别名保留。工作总结保留既有生产 Adapter 的别名行为，以字节与行为 parity 为优先，不为形式统一改变原运行方式。

## 6. Adapter、Retrieval、Structure、Quality 与商业状态

下表中的 21 个新文种共享实现机制，但配置的是各自上游文种角色、顺序、结语与混用边界；不把工作总结质量门换名复用。

| 本地 genre_id | Retrieval Profile | Structure Contract | Quality Contract | 商业成熟度 |
| --- | --- | --- | --- | --- |
| `announcement` | `upstream_announcement_coverage_v1` | `upstream_announcement_structure_v1` | `upstream_announcement_quality_v1` | `ENGINEERING_READY` |
| `approval` | `upstream_approval_coverage_v1` | `upstream_approval_structure_v1` | `upstream_approval_quality_v1` | `ENGINEERING_READY` |
| `briefing` | `upstream_briefing_coverage_v1` | `upstream_briefing_structure_v1` | `upstream_briefing_quality_v1` | `ENGINEERING_READY` |
| `circular` | `upstream_circular_coverage_v1` | `upstream_circular_structure_v1` | `upstream_circular_quality_v1` | `ENGINEERING_READY` |
| `communique` | `upstream_communique_coverage_v1` | `upstream_communique_structure_v1` | `upstream_communique_quality_v1` | `ENGINEERING_READY` |
| `decision` | `upstream_decision_coverage_v1` | `upstream_decision_structure_v1` | `upstream_decision_quality_v1` | `ENGINEERING_READY` |
| `letter` | `upstream_letter_coverage_v1` | `upstream_letter_structure_v1` | `upstream_letter_quality_v1` | `ENGINEERING_READY` |
| `minutes` | `upstream_minutes_coverage_v1` | `upstream_minutes_structure_v1` | `upstream_minutes_quality_v1` | `ENGINEERING_READY` |
| `motion` | `upstream_motion_coverage_v1` | `upstream_motion_structure_v1` | `upstream_motion_quality_v1` | `ENGINEERING_READY` |
| `notice` | `upstream_notice_coverage_v1` | `upstream_notice_structure_v1` | `upstream_notice_quality_v1` | `ENGINEERING_READY` |
| `opinion` | `upstream_opinion_coverage_v1` | `upstream_opinion_structure_v1` | `upstream_opinion_quality_v1` | `ENGINEERING_READY` |
| `order` | `upstream_order_coverage_v1` | `upstream_order_structure_v1` | `upstream_order_quality_v1` | `ENGINEERING_READY` |
| `report` | `upstream_presentation_coverage_v1` | `upstream_presentation_structure_v1` | `upstream_presentation_quality_v1` | `ENGINEERING_READY` |
| `public-notice` | `upstream_public-notice_coverage_v1` | `upstream_public-notice_structure_v1` | `upstream_public-notice_quality_v1` | `ENGINEERING_READY` |
| `reply` | `upstream_reply_coverage_v1` | `upstream_reply_structure_v1` | `upstream_reply_quality_v1` | `ENGINEERING_READY` |
| `statutory_report` | `upstream_report_coverage_v1` | `upstream_report_structure_v1` | `upstream_report_quality_v1` | `ENGINEERING_READY` |
| `request` | `upstream_request_coverage_v1` | `upstream_request_structure_v1` | `upstream_request_quality_v1` | `ENGINEERING_READY` |
| `resolution` | `upstream_resolution_coverage_v1` | `upstream_resolution_structure_v1` | `upstream_resolution_quality_v1` | `ENGINEERING_READY` |
| `special-report` | `upstream_special-report_coverage_v1` | `upstream_special-report_structure_v1` | `upstream_special-report_quality_v1` | `ENGINEERING_READY` |
| `speech` | `upstream_speech_coverage_v1` | `upstream_speech_structure_v1` | `upstream_speech_quality_v1` | `ENGINEERING_READY` |
| `work_summary` | `work_summary_coverage_v1` | `work_summary_full_article_structure_v1` | `work_summary_quality_contract` | `PRODUCTION_ENABLED（原状态）` |
| `work-plan` | `upstream_work-plan_coverage_v1` | `upstream_work-plan_structure_v1` | `upstream_work-plan_quality_v1` | `ENGINEERING_READY` |

新的 Retrieval Profile 只对已有 chunk 的标题、路径与文本提供文种阅读覆盖信号及小幅偏好；沿用 Shared Retrieval，不改变 P1 文档排名，不创建证据、不授予准入、不修改 semantic state 或 source_scope。

Structure Contract 的角色来自各自 pinned spec 中的“要素分层、分段职责、撰写思路”。它描述功能，不要求机械套用模板标题；短通知、批复、会议纪要、不同讲话场景允许合并/内嵌功能。命中关键词仅是复核线索。

Quality evaluator 针对必需功能、文种身份、顺序、常见结语与明显混用提供带位置的观察。未命中只进入复核，命中不能证明语义充分。`fact_safety` receipt 引用原 canonical FACT 报告和报告哈希，不重算、替代或覆盖 FACT。整体商业成熟度和真实文章质量仍需人工复核。

新增文种均为 `EXPERIMENTAL` / `ENGINEERING_READY`，`production_ready=False`，不标记为 `COMMERCIAL_VERIFIED`。它们具备后续真实验收所需的现有商业能力接口；正式请求仍经过原 Commercial Production Gate，不能通过 payload 改成熟度或提升生产权限。工作总结保持原 `PRODUCTION_ENABLED` 和现有生产能力；该状态本身也不等于本轮重新完成商业文稿验收。

## 7. 工作总结 parity 与 report 单真源迁移

工作总结原运行根继续为：

```text
backend/experiments/case_a_upstream_drafting_validation_32/upstream/
```

已实际验证：新套件 `summary` 原生成物与该根原 `system_prompt.md` 字节相等，SHA256 均为 `80c99367127e6eabca3a5329ec745bcd5bad46734b10aa577a478f4627f858b1`。离线使用完整上游源码重建后的字节仍一致。

工作总结原 ruleset ID、DraftingRules、Host Boundary、User Prompt composition、Commercial Contract 和 formal route 已通过针对精确 BASE 的完整 Prompt、事实报告、质量报告、正文与 FLOW4 交付行为比对；对应 Owner 字节未修改。套件里的 `summary` 用于完整目录与来源重建验证，不重复注册一个新工作总结 Adapter。

旧 `report` 的本地主键与“汇报材料”含义保留，导出名 `REPORT_ADAPTER` 指向唯一 pinned `presentation` Adapter。原 host-authored `report_offline.md` 不再用于生产规则解析，旧 report retrieval/quality 名称只保留迁移兼容出口，不成为第二套语义实现。法定“报告”用 `statutory_report` / `报告` 解析；字符串 `report` 继续表示历史“汇报材料”，避免含义静默反转。

旧 Foundation 测试历史保留，并将“host-authored / no commercial contract / 旧 profile”的实现断言显式迁移到新事实；拒绝不成熟生产请求、共享 FACT/交付、输入隔离和身份绑定等架构保证继续回归。

## 8. Host Boundary、事实安全、交付与 Stable Asset

Prompt 组合继续采用：项目 Host Boundary + 本文种 pinned 上游系统提示词 + 项目生产任务/材料 User Prompt。上游通用 User Prompt 不进入生产组合；Task Assembly、Evidence Registry、Material Brief、Fact Anchor、source_scope、semantic state 和限制信息继续由现有主线提供。

上游只提供文种写法、组织和表达规则。发生冲突时 Host Boundary 优先。上游通用反虚构提醒是补充，不替代 P1 Topic-first、P2 Auxiliary Admission、Atomic Evidence、P3-A、P3-B 或 canonical Fact Safety。

文种没有 Evidence admission、UNKNOWN 可写权、状态覆盖、主体转移、未证实成果/因果生成、引用编造或最终 FACT 裁决权。Quality 不改变正文；Must-Deliver、FLOW4、正文 identity/hash 和风险稿交付沿用共享 Owner。

Stable Asset parser/fingerprint、source substrate、material ingestion fingerprint、`atomic_evidence.py` 以及 P1/P2/P3 Owner 均与精确 BASE 相同。专项测试同时验证未来单字节改动会被拒绝，`STABLE_ASSET_REPROCESS_REQUIRED=NO`。

## 9. 维护与检查命令

在项目根目录运行，使用本机已验证可用的 Python 3.12.14；没有安装新依赖：

```powershell
$taskPython = 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $taskPython -B '.\backend\scripts\build_upstream_genre_snapshot.py' --check
```

该检查完全离线，核验文件清单、许可、Git blob/SHA256、22 文种目录、22 份系统提示词的上游原 builder 重建结果、模板一致性和原工作总结字节 parity。失败时不重写文件。

只有已按工单授权取得指定 commit 的 raw 文本、`commit.json`、`commit-tree.json` 后，才能维护性构建快照：

```powershell
& $taskPython -B '.\backend\scripts\build_upstream_genre_snapshot.py' --source-root '.\tmp\upstream-22-acquisition'
```

维护脚本本身没有网络获取逻辑。它先验证固定 commit/tree 和递归 tree 对象，再逐 blob 校验来源，保留原字节，并拒绝覆盖已有不同内容；不通过重新计算 manifest 接受任意上游变更。迁移升级不在本轮授权范围内。

此机器上的虚拟环境启动器指向 Windows Store Python，普通沙盒中无法启动。上游专项检查曾在授权的提升环境中运行该启动器；主 Agent 的所有后端回归统一改用上面可用的 Python，通过 `PYTHONPATH` 读取现有虚拟环境依赖。未安装、升级或修改环境。中文日志显式使用 UTF-8；终端显示问题未当作源码损坏处理。

后端回归的可复制命令（当前工单的初始 11 文件保护清单已保存在 `tmp/upstream-22-genre-migration/protected-user-files.json`，runner 缺少该清单会拒绝执行）：

```powershell
Set-Location 'D:\AI_Projects\gongwen-style-agent-commercial'
$taskPython = 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:PYTHONPATH = Join-Path (Get-Location) 'backend\.venv\Lib\site-packages'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
Set-Location backend
& $taskPython -B tests/run_upstream_suite_offline.py -q tests/test_upstream_suite_integrity.py tests/test_upstream_suite_contracts.py tests/test_upstream_suite_flows.py tests/test_multi_genre_foundation.py tests/test_multi_genre_drafting_quality.py tests/test_multi_genre_retrieval.py tests/test_multi_genre_scope.py tests/test_commercial_genre_infrastructure.py tests/test_commercial_genre_parity.py tests/test_commercial_genre_scope.py
& $taskPython -B tests/run_upstream_suite_offline.py -q -rs tests
```

## 10. 实际执行的验证与中间失败

| 阶段 | 实际结果 | 解释 |
| --- | --- | --- |
| 修改前完整后端离线基线 | 1027 passed，2 skipped，169.49s；外部调用计数 0 | 主 Agent 已执行的基线结果 |
| 固定上游获取及 LICENSE/目录核验 | 完成 | 只读指定 commit，136 份采用文本逐 blob 校验 |
| `build_upstream_genre_snapshot.py --source-root ...` | PASS：22 genres，136 files | 同时重算 Git tree、重建系统提示词与模板 |
| `build_upstream_genre_snapshot.py --check` | PASS | manifest SHA 与 summary parity 均通过 |
| pinned `tests/test_offline_build.py` | 7 tests OK，0.402s | socket connect/connect_ex/create_connection/getaddrinfo 禁止；`-B` 禁 bytecode |
| 首轮迁移专项合并回归 | 26 failed，396 passed，52.13s | 21 项新测试误引“不授权新增更强结果”；5 项旧 report 来源/覆盖断言迁移，问题块阅读偏好仍保留 |
| 第二轮迁移专项合并回归 | 1 failed，631 passed，78.85s | 旧测试强制不同文种选取数量不同；新 profile 沿用共享预算，改检验实际读取顺序和覆盖差异，P1 排名断言保留 |
| 最终迁移专项与必要回归 | 634 passed，78.91s | 全 22 文种合同/来源/合成正式链路与历史回归通过；四项受限调用/访问计数均 0 |
| 最终完整后端离线套件 | 1421 passed，2 skipped，193.08s | 覆盖 Retrieval、Task Assembly、Writer/material、Full Article、FACT、Delivery、FLOW4、Stable Asset、P1/P2/P3；受限调用/访问计数均 0 |
| 最终 protected owners 与 staged/user-file 检查 | PASS | 工作总结及保护 Owner 与 BASE 一致；11 用户文件 SHA256 不变；暂存区空；HEAD 仍为 BASE；全部 156 项任务文件 UTF-8/Python AST 检查通过 |

最终计数：`external_network=0`、`real_model=0`、`real_embedding=0`、`forbidden_data_access=0`。`asyncio_socketpair_ipc=277` 为 Windows 本地事件循环 IPC，不是外部网络请求。2 项跳过与基线一致：P3-A 和 P3-B 的明确授权私有 Run 回放，本工单禁止使用真实库，故未执行。唯一 warning 是现有 Starlette/AnyIO 的弃用提示，未引入或升级依赖。

实际日志在 `tmp/upstream-22-genre-migration/`：`baseline-offline.log`、`targeted-first.log`、`targeted-second.log`、`targeted-final.log`、`full-offline-final.log`；各隔离 runtime 另有 `call-counts.json`。`final-scope-audit.json` 记录完整任务路径列表与用户文件保护结论。日志及临时证据不进入提交。Git Unicode 路径初次审计被默认引用格式影响，改用 `git ... -z` 后完整编码/路径审计通过。

未运行真实页面、真实 Writer、真实文章商业验收或黄金样本生成对比；本工单明确将这些留到每文种后续验收，不把离线合成流程当成真实文章结果。22 文种的合成正式 Runtime 测试只在隔离测试 Registry 中临时提升资格，唯一 Writer 返回明确标记的合成夹具；生产配置始终未提升。

## 11. 修改范围、未决限制与回滚

本轮合计 156 项任务文件：8 项既有文件修改、148 项新增；另有 11 项原有用户未跟踪文件未修改。无暂存文件，无 commit、push、PR 或 merge。修改前/修改后 HEAD 均为 `ecfa1b9da98101fe74ecabc26b026ec8818a3488`，commit message 不适用；BASE 与本地分支起点构成回退参照，没有创建 checkpoint commit（本工单禁止 commit）。

生产与维护文件（9 项）：

```text
backend/app/services/generation/genres/registry.py
backend/app/services/generation/genres/report.py
backend/app/services/generation/genres/report_quality.py
backend/app/services/generation/genres/report_retrieval.py
backend/app/services/generation/genres/upstream_adapters.py
backend/app/services/generation/genres/upstream_suite.py
backend/app/services/generation/genres/upstream_genre_contracts.py
backend/app/services/generation/genres/upstream_genre_quality.py
backend/scripts/build_upstream_genre_snapshot.py
```

测试文件（9 项）：

```text
backend/tests/commercial_genre_scope.py
backend/tests/test_multi_genre_drafting_quality.py
backend/tests/test_multi_genre_foundation.py
backend/tests/test_multi_genre_retrieval.py
backend/tests/run_upstream_suite_offline.py
backend/tests/upstream_suite_scope.py
backend/tests/test_upstream_suite_integrity.py
backend/tests/test_upstream_suite_contracts.py
backend/tests/test_upstream_suite_flows.py
```

另有本报告 1 项，以及 `SNAPSHOT` 中 136 项原始上游文本和 1 项 manifest；完整 136 路径、来源、长度、Git blob 和 SHA256 见 manifest 的 `files`。原历史批准 hash/BASE 不变，仅针对 4 项实际迁移的 Registry/report 入口增加独立本轮精确 hash 许可；所有共享核心仍独立与 BASE 比较，未来单字节变更负例仍生效。

已知限制：

1. 新文种尚无真实 Writer/用户业务案例/黄金样本验收；本轮不承诺成熟稿质量。
2. 文种质量为明确标注的词面与位置观察，不能可靠识别所有同义写法、法定主体资格、文号真伪或结构语义遗漏；事实结论仍由共享证据链负责，质量成熟度待人工验收。
3. 新文种适用原 FLOW4 导出链路；未使用上游字体或新 exporter，后续排版细化仍归 FLOW4，不在本轮扩展。
4. `report` 英文历史含义特意保持为汇报材料；调用法定报告应使用 `报告` 或 `statutory_report`。此映射在 manifest、Registry 与测试中锁定。
5. 上游 `examples.md` 仅保留作源完整性和来源审计，不注入 Prompt，不导入资料库，不作为事实来源。
6. Registry 初始化时统一校验完整套件，任一被锁定资源缺失或损坏会阻断初始化，包括工作总结入口。此行为选择完整套件 fail-closed；没有引入绕过或降级为未校验 Prompt 的路径。

当前 HEAD 仍是 BASE，未生成本轮 commit。由于有用户未跟踪文件，禁止使用 `git clean`、`git reset --hard` 或全目录删除回滚。需要撤销时，先核对最终变更清单，仅对本轮已跟踪修改按 BASE 内容逐文件恢复；新增文件先归档，再逐路径处理本轮新增的 suite owner、测试、构建脚本、snapshot 和报告，绝不触及 11 个用户文件、既有工作总结快照及真实资料。执行删除/破坏性回滚前仍需用户明确确认；本报告不执行回滚。

## 12. 最终验收与六遍自检

六遍自检通过：唯一指定上游；MIT/字体排除与逐字节 provenance；各文种使用自身规则且 summary parity；既有 Commercial Runtime/Gate 无第二链路；P1/P2/P3/FACT/Must-Deliver/body identity 权限不变；定向与完整离线回归通过、11 用户文件不变、Git 未暂存。

```text
TASK = UPSTREAM-22-GENRE-MIGRATION
BASE_VERIFIED = YES
UPSTREAM_LICENSE_VERIFIED = YES
UPSTREAM_FONT_BINARIES_EXCLUDED = YES
UPSTREAM_GENRE_CATALOG_VERIFIED = YES
UPSTREAM_CANONICAL_GENRE_COUNT = 22
MIGRATED_GENRE_COUNT = 22 (原工作总结保留，其余21种完成迁移)
ALL_QUALIFIED_UPSTREAM_GENRES_MIGRATED = YES
SINGLE_UPSTREAM_SUITE = YES
OTHER_OPEN_SOURCE_PROJECTS_USED = NO
WORK_SUMMARY_REFERENCE_PATTERN_PRESERVED = YES
WORK_SUMMARY_PROMPT_PARITY = YES
WORK_SUMMARY_COMMERCIAL_PARITY = YES
WORK_SUMMARY_PRODUCTION_BEHAVIOR_PRESERVED = YES
COMMERCIAL_RUNTIME_PRESERVED = YES
COMMERCIAL_GATE_PRESERVED = YES
EACH_GENRE_HAS_PINNED_RULESET = YES
EACH_GENRE_HAS_ADAPTER = YES
EACH_GENRE_HAS_RETRIEVAL_PROFILE = YES
EACH_GENRE_HAS_STRUCTURE_CONTRACT = YES
EACH_GENRE_HAS_QUALITY_CONTRACT = YES
SHARED_RAG_PRESERVED = YES
SHARED_CORE_HAS_NO_NEW_GENRE_BRANCHES = YES
FACT_SAFETY_SHARED = YES
GENRE_CANNOT_OVERRIDE_FACT_AUTHORITY = YES
MUST_DELIVER_SHARED = YES
BODY_IDENTITY_PRESERVED = YES
P1_CHANGED = NO
P2_CHANGED = NO
P3A_CHANGED = NO
P3B_CHANGED = NO
STABLE_ASSET_OWNER_CHANGED = NO
STABLE_ASSET_REPROCESS_REQUIRED = NO
PROTECTED_OWNER_DIFF = NONE
FINAL_TARGETED_TESTS = 634 passed
FINAL_FULL_OFFLINE_TESTS = 1421 passed, 2 skipped (未授权私有Run)
ALL_TARGETED_TESTS_PASS = YES
FULL_BACKEND_OFFLINE_SUITE_PASS = YES
REAL_MODEL_CALLS = 0
EMBEDDING_CALLS = 0
EXTERNAL_NETWORK_CALLS_DURING_OFFLINE_TESTS = 0
FORBIDDEN_REAL_DATA_ACCESS = 0
FINAL_CHANGED_FILES = 156 task files
FINAL_GIT_STATUS = 8 modified task files; 148 new task files; 11 unchanged user untracked files
USER_11_UNTRACKED_FILES_PRESERVED = YES
STAGED_FILES = NONE
COMMIT_CREATED = NO
PUSHED = NO
PR_CREATED = NO
REAL_COMMERCIAL_ACCEPTANCE_DEFERRED = YES
NEW_GENRES_PRODUCTION_READY = NO
NEW_GENRES_COMMERCIAL_VERIFIED = NO
BLOCKING_FINDINGS = NONE
READY_FOR_CHATGPT_REVIEW = YES
```
