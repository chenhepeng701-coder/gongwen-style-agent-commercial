# W3-8C-1｜稳定材料资产层

文档角色：本轮实现说明与离线验收报告，不新增项目规则真源。

日期：2026-09-22。范围：显式启用的片段预计算与任务组合；不接上传、Writer 或生产生成路线。

## 当前证据与推理闸

基线为 `87711c3c63e5ab280b31c4eaacfd43a7f2914ee7`，开始时工作区及暂存区为空。原分支为 `codex/w3-8c-01-writer-quality`；本轮分支为 `codex/w3-8c-1-stable-material-assets`，HEAD 未改变，基线即 checkpoint。

当前源码证据：

- `document_retriever._selected_chunk` 同时返回完整 `chunk_text` 与 `full_text[:text_limit]`；主材 800、辅材 300 的常量及实现未改。
- 原 `source_substrate._source_projection` 按 selected_chunks 的任务 text 集合建缓存，`load_material_digest` 再绑定其集合键。已有缓存不是稳定单 chunk 资产。
- `atomic_evidence.build_full_draft_anchor_input` 调用现有 Atomic owner 并重新验证；SourceSubstrate 只是可选切分结果，不能决定准入。
- `task_assembly.assemble_task` 是未接生产的离线入口；现有正式生成文件没有导入 Digest 或 Task Assembly。

修改前推理闸的结论及实现落点：

| 问题 | 结论 |
|---|---|
| 实际问题与已验证事实 | 缓存键随任务截断文本及选中集合变化；完整 chunk 已存在于 retrieval |
| 推测及待验证项 | 跨预算复用可能扩权；截断会影响句界、引用、标题判定及限定词，必须逐字段验证 |
| 创建者、调用者、消费者 | 新 loader 创建单 chunk 资产；显式组合函数调用；任务级 SourceSubstrate/Digest 与已有 Task Assembly 消费 |
| 唯一 owner 与既有同职责能力 | 新模块只拥有持久化源资产；切分仍由 Atomic parser 实现，字面信号仍由 Material Digest 实现 |
| 最小改法 | 新增一个单职责资产模块，向三个允许模块各加一个显式组合函数，保留现有实验 API |
| 最大回归风险 | 全文进入任务、截断后状态漂移、Evidence 新增或遗漏、原限制丢失 |
| 阻断方法 | 800/300 对照、逐字符截断、完整基线合同比较、Atomic 入参哨兵、无调用哨兵 |
| 质量和安全 | 不改写作、事实判定或事实安全规则；只验证离线事实守恒，不宣称文章质量提高 |
| 用户可见行为 | 无生产页面/API 行为变化；新显式接口遇到缺失授权或过期资产会报合同错误 |
| 旧模式与范围 | 不恢复旧模式；不接正式路线；不删除旧分类、素材卡或风格卡 |

## 接口与责任边界

新增模块 `backend/app/services/generation/stable_material_assets.py`：

- `load_stable_material_asset(chunk, cache_dir)`：只读取调用者显式传入的完整 chunk，返回不可变 `StableMaterialAsset`、命中状态和耗时。无需 task、retrieval 集合、Brief 或 Provider。
- `selected_asset_sources(retrieval, assets)`：逐个绑定当前选中的 chunk，拒绝缺失/过期资产，确认授权 text 属于完整源。
- `authorized_retrieval_view(retrieval)`：深复制当前 retrieval，移除每个 selected chunk 的 `chunk_text`。保留原 text、角色、顺序、覆盖语义、来源限制、source policy 和时间风险等任务输入。

允许的三个模块新增：

- `source_substrate.compose_source_substrate(retrieval, assets)`：只包含当前授权文本的临时切分视图。
- `material_digest.compose_material_digest(retrieval, assets)`：只包含临时视图中 source_span 的字面阅读信号。
- `task_assembly.assemble_task_from_assets(request, retrieval, assets)`：组合后调用原 `assemble_task`，Atomic 收到的是去除全文的授权 retrieval。

新路径：

```text
document_id + chunk_id + 完整 chunk_text + 稳定来源元数据
  → load_stable_material_asset（可无任务预计算）
  → 单 chunk 私有磁盘资产

本次 selected_chunks + 显式授权 text + 已有资产集合
  → task-specific SourceSubstrate / MaterialDigest（只在内存存在）
  → 移除 chunk_text 的 retrieval
  → 现有 Atomic / Fact Anchor owner
  → 原 Task Assembly / FullDraftMaterialPacket
```

旧 `load_source_substrate`、`load_material_digest` 仍供 W3-8A-P/W3-8B 实验使用，缓存格式和入口不变；它们不应被当成新的稳定预计算接口。未把任何入口接入生产，也没有通过旧缓存模拟新资产命中。

## 稳定键与资产内容

一条资产绑定 document_id、chunk_id、完整 chunk_text、白名单来源元数据、Source Substrate fragments、Material Digest reading_units、三个 schema 版本及四类规则指纹。

稳定元数据白名单 `STABLE_SOURCE_FIELDS` 依据当前来源与 chunk metadata 定义，涵盖标题、来源文件/路径、章节位置、段落范围、来源类型、源限制和 writability 等。顶层来源属性与 metadata 分开保留；未知字段不自动成为稳定字段。后续接线须传入相同的稳定来源投影，新增来源字段须显式更新 schema/规则。`document_role` 和可被任务覆盖的 `content_role` 不冻结。

键是上述 binding 的 canonical JSON SHA-256，不依赖任务标题、主辅角色、排名、score、selection_reason、授权截断 text、Evidence ID 或章节分配。资产不保存最终 semantic_state、准入结果、Evidence registry、任务限制推断或文章章节。

以下变化生成新键且旧内存资产不可用于新源：完整文本、document/chunk ID、声明的稳定来源字段、Source Substrate 代码/版本、Atomic parser 代码、Material Digest 代码/版本、资产自身代码/版本。代码指纹通过 UTF-8 文本读取统一换行。规则指纹比较保守，非语义代码修改也可能导致重建。

磁盘采用 checksum envelope、严格字段校验和临时文件原子替换。损坏、非法字段、I/O 失败均返回 `FullArticleContractError`，不静默回退为成功。缓存为调用者明确指定的本地私有目录；checksum 不是对抗可同时改写 payload 和 checksum 的本地攻击者的安全边界。

## 授权及切分边界

1. 预计算必须显式提供非空完整 `chunk_text` 和身份；缺少全文时报错，绝不将 300/800 字 text 冒充完整资产。
2. 任务必须显式提供字符串 `text`。缺失或非字符串时报错；空字符串保持空授权，现有 Atomic 给出无有效证据的失败结果。仅此异常输入的 fail-closed 行为比旧 `text or chunk_text` fallback 更严格，不改变正式路线。
3. 非空 text 必须是完整 chunk 的连续子串；组合层不会自己改 800/300 预算，也不会重新授予全文权限。
4. text 等于完整 chunk 时可直接复用 fragments；text 被截断时重新调用现有 parser 解析该 text。不能简单过滤完整 fragments：截断可能产生新的合法尾句，也可能改变标题、引号或限定语的解释。
5. Digest 只复用与本次 source_span 完全相等的字面信号；截断产生的新 span 用现有 Digest owner 重新计算，不继承授权外尾部的否定或完成信号。
6. Atomic 仍按当前角色、章节覆盖语义、顺序、主辅补缺规则、限制和上下文重新决定准入、ID、状态、Anchor。资产从不授予 Evidence。

## 实际验证

新增测试 `backend/tests/test_stable_material_assets.py` 共 71 项，使用明确合成材料，不处理用户资料。直接通过 `git show BASE_SHA:.../atomic_evidence.py` 加载基线 owner 对照，没有重写一个假基线算法。

覆盖内容：

- 超过 1200 字的完整片段先无任务预计算，随后 primary 800 字与 auxiliary 300 字命中同一文件、同一键；300 字视图无中段事实，两种视图均无授权外尾部事实。
- 改任务标题、rank/raw_rank/document_rank、score/document_score、主辅角色、selection_reason、Evidence ID、section assignment、coverage_role 不重建；同样检查嵌在 metadata 的任务字段。
- 缓存命中时将 parser/reading-unit 构建函数替换成失败哨兵，确认确实不重建。完整授权任务视图也可跳过这两项计算。
- 改全文、身份、稳定来源信息、来源限制、writability、源 scope 或任一规则指纹会失效。
- 1/4/12/20/299/300/301/799/800/801/1200 字边界，以及否定、计划、问题上下文、引号、标题、网页残留的逐字符截断。
- 不同 selected set、顺序、当前主辅角色、覆盖语义和任务时间下，重新组合且逐字段等价。
- 完整 Atomic/Anchor 输入、registry、不可用项、丢弃原因、来源限制、semantic_state、Brief 前完整材料包、Evidence 写作视图均对照；阅读卡片 ID 集合与合法 registry 集合完全相同。
- 全文移除入参哨兵、空/缺失/越界授权、非前缀授权、身份冲突、源 scope、无资料状态、缓存损坏、内存对象不可污染及 I/O 错误。
- 上传/生成路由、retrieval、正式生成、Atomic、材料合同、Writer prompt owner 与本轮 BASE_SHA 内容一致。

执行环境：`backend/.venv` 指向失效的 Windows Store Python，启动失败；改用**当前仓库内已有** `tmp/baseline-02b-r2r3/test-env/Scripts/python.exe`（Python 3.12.14、pytest 8.3.5）。仅使用其解释器及依赖，未读取任何旧 clone 或旧项目源码，没有安装依赖。

新增 `backend/tests/run_w3_8c_1_offline.py` 是对应测试运行器：在导入业务模块前，把 runtime paths 指向本轮唯一临时目录，避免读生产配置或写用户运行数据；禁止真实 Provider.generate_text，审计钩子禁止网络连接/解析。现有测试自己的本地 Provider 替身仍只用于原有单元测试，不代表真实模型调用。

Windows 的标准库 socketpair 通过回环 TCP 支持 asyncio 内部 IPC。运行器只对白名单中的**标准库 socketpair 原函数调用点**放行，普通 localhost、外网、DNS、sendto 均禁止。定向测试 IPC 为 0；完整套件 IPC 为 5，单独计数，未作为项目网络请求。`NETWORK_CALL_COUNT=0` 指项目/外部服务网络调用；不声称操作系统没有发生上述 5 次内部 IPC。

在 backend 工作目录实际执行（每次 basetemp 均为新目录）：

```powershell
$env:PYTHONIOENCODING='utf-8'
& '../tmp/baseline-02b-r2r3/test-env/Scripts/python.exe' tests/run_w3_8c_1_offline.py -q tests --basetemp tmp/w3-8c-1/baseline-2
& '../tmp/baseline-02b-r2r3/test-env/Scripts/python.exe' tests/run_w3_8c_1_offline.py -q tests/test_stable_material_assets.py tests/test_source_substrate.py tests/test_material_digest_task_assembly.py --basetemp tmp/w3-8c-1/directed-3
& '../tmp/baseline-02b-r2r3/test-env/Scripts/python.exe' tests/run_w3_8c_1_offline.py -q tests --basetemp tmp/w3-8c-1/full-final
```

| 验证 | 实际结果 | 本地日志 |
|---|---|---|
| 实现前完整基线 | 248 passed，14.16 秒 | `backend/tmp/w3-8c-1-baseline-2.log` |
| W3-8C-1 + W3-8A-P + W3-8B 定向 | 146 passed，13.72 秒 | `backend/tmp/w3-8c-1-directed-3.log` |
| 最终完整 backend | 319 passed，1 warning，21.61 秒 | `backend/tmp/w3-8c-1-full-final.log` |

唯一 warning 为既有 Starlette 引用 AnyIO BlockingPortal 的弃用警告。最终真实 Provider 调用为 0，禁止的网络调用为 0。全部测试生成物位于 ignored tmp，不暂存、不外发。

800/300 字实际比较证据：`backend/tmp/w3-8c-1/full-final/test_precompute_without_task_t0/comparison-evidence.txt`，记录稳定键、全文长度、授权长度、完整两组 Anchor 输入和零调用计数。W3-8A-P/W3-8B 的原有比较文件也在本次 full-final 对应测试目录中。

实际完整片段为 1221 字；800 字任务有 5 条合法 registry，300 字任务有 3 条，二者分别与同输入的基线结果完全一致，差异列表为空。最终七文件严格 UTF-8 解码、无替换字符检查及六个 Python 文件语法编译通过；`git diff --check` 和四个新增文件的 no-index whitespace 检查无错误。受保护的路由、retrieval、LLM、Atomic、正式生成、材料合同、Writer owner 与 frontend 路径 diff 为空。

失败记录保留：

- `baseline-1`：243 passed、1 failed、4 errors；严格 socket 阻断了 5 次 Windows asyncio socketpair 初始化，全部在连接前被拒绝。收窄放行到该标准库 IPC 调用点后完整基线通过。
- `directed-1`：136 passed、2 failed；新增测试误把“存在 atom”当成“必有合法 Anchor”，并错判空授权的既有异常。改为与实际基线失败结果逐项比较，未改变实现或旧测试。
- `directed-2`：144 passed、2 failed；动态加载基线模块的 InvalidEvidencePackError 与当前模块类身份不同。显式按两侧各自异常类比较错误内容后通过。
- 初始 Git 创建分支受 `.git` 写权限限制；权限审查允许后按工单创建成功，无提交或远程操作。

未运行：真实 Writer/Brief/LLM、在线 RAG/Embedding、上传、真实页面、真实用户材料或黄金样本生成。不宣称文章质量提升或上传生产接线完成。

## 技术债与后续边界

- 截断任务仍需要解析至多本次授权 text；完整资产命中不等于任务零计算。读取规则文件的成本、重复绑定检查及大库性能未做专项优化。
- 新元数据字段须按明确 source schema 加入白名单；当前 key 区分顶层与 metadata 的存储位置。上传接线应统一来源投影，不能把任务补充字段混作原始 metadata。
- 源码指纹依赖可读源码，打包环境、磁盘资产容量/清理/权限管理仍需后续专项验证。旧键文件保留，本轮未清理旧缓存。
- 原 W3-8A-P/W3-8B 实验接口保留，不是新的生产路线；今后上传预计算必须明确调用新单 chunk 接口。
- 当前没有 Writer 质量结论。下一工单可审查上传后本地预计算的接线边界与失效生命周期；仍需单独授权，不能从本轮自动延伸。

## Git 与验收结论

改动：三个允许的 generation 模块。新增：一个稳定资产模块、一个测试模块、一个隔离离线测试运行器及本报告。无允许范围外业务代码改动；新增运行器用于满足零项目调用和真实用户数据隔离要求。

没有暂存、commit、push、PR；main 未修改。修改前后 HEAD 均为 BASE_SHA，commit message 不适用。工作区保留本轮 3 个修改文件及 4 个新增文件供审阅。

回滚说明（未执行）：先将本轮 diff 与四个新增文件备份到用户指定位置；确认没有后续改动后，将三个修改文件恢复到 BASE_SHA，并将四个新增文件移出工作区，再切回原分支。不要 reset/clean、清理 tmp 或碰用户资料；回滚需另行授权。

```text
W3_8C_1 = PASS
STABLE_CHUNK_ASSET = YES
TASK_ROLE_CHANGE_REUSES_ASSET = YES
TASK_TEXT_BUDGET_PRESERVED = YES
EVIDENCE_SCOPE_EXPANDED = NO
FINAL_EVIDENCE_EQUIVALENT = YES
FACT_SAFETY_REGRESSION = NONE
PROJECT_MODEL_CALL_COUNT = 0
NETWORK_CALL_COUNT = 0
PRODUCTION_ROUTE_CHANGED = NO
UPLOAD_ROUTE_CHANGED = NO
LEGACY_PROCESSING_REMOVED = NO
```

大白话：同一份完整片段现在可以预先整理并反复使用；每次任务仍只拿 retrieval 当次划给它的那一小段。辅材料即使命中保存了全文的缓存，也不会因此多拿事实。没有接上传、接 Writer、删旧链或改生产路线。
