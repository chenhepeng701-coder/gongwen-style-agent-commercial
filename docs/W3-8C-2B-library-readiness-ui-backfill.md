# W3-8C-2B：资料库就绪状态与受控历史回填

类型：本轮实现与验收记录，不是独立项目规则真源。

## 边界与结论

本轮只接线 W3-8C-2A/R1 已有材料状态及单文档处理 API，不改变后端业务合同、检索策略或生成链路。开发与验证只使用合成资料，未读取、处理真实资料库。没有执行真实历史回填，没有新增依赖、任务队列、持久化任务系统。

`W3_8C_2B = PASS`：前端 Node 全集 61 passed；构建通过；后端完整离线回归 358 passed。实际浏览器验证连接独立的内存模拟 API，不能替代合并后正式环境的真实回填验收。

用户本轮明确要求不 commit、不 push、不创建 PR、不修改 main，优先于项目默认提交要求。

## 当前真源、基线与推理闸

- 已执行授权的 `git fetch origin main`，确认 `origin/main = 2d3113c995b80b3cb546eb136c397a03cb06b369`。
- 新建分支 `codex/w3-8c-2b-library-readiness-ui`，修改前后 HEAD 均为该 SHA。新分支及基线提交作为可回退检查点，开始时工作区、暂存区为空。
- fetch 与创建分支分别遇到 `.git/FETCH_HEAD`、`.git/index.lock` 沙箱写权限限制；仅对对应 Git 操作申请并取得权限。
- 已检查 Git 根、分支、HEAD、暂存、ignored 元数据及代码目录内嵌套仓库；未读取 ignored 的真实 data/uploads 内容。部分既有 ignored 临时目录列举有权限警告，未尝试提权读取。

| 推理闸 | 结论 |
| --- | --- |
| 实际问题与证据 | 原 LibraryPage 的 `getDocumentStatus` 依据 `has_embedding === true` 声明“可用于写作”；现 API 已有 ingestion_status。 |
| 已验证事实 | POST `/api/library/documents/{id}/process` 返回 202 LibraryDocument；GET 单文档返回 LibraryDocument；列表 `page_size=-1` 返回全量。409 使用 detail.error_type。 |
| 推测与待确认 | 真实私有资料的成功率、耗时及服务配置不在本轮验证范围，未作推断。 |
| 概念创建者及 owner | 后端 material_ingestion.py 仍是持久化状态及就绪合同 owner。前端只展示、乐观显示请求中状态、查询和调度。 |
| 调用者与消费者 | LibraryPage 调用 api.js 正式接口，资料列表和详情消费状态；新 utility 管理页面生命周期内的去重、等待及顺序回填。 |
| 同职责模块 | 旧 processLibraryAll 是旧批处理链路，职责与本轮不同，保留但不调用；未发现现有单文档 readiness 前端调度模块。 |
| 最小改法 | 页面、API、新 utility、新 Node 测试、本文档；另有用户明确批准的历史测试范围断言调整。 |
| 最大回归风险 | 将向量存在误作 READY、重复 POST、回填并发、页面退出后继续排队、错误原文泄露。 |
| 阻断风险的验证 | 状态映射、延迟终态、失败继续、重复执行、409、丢失响应、定时器清理及合成浏览器验证。 |
| 文章质量与事实安全 | 不触达 Writer、Brief、RAG、Atomic Evidence、Fact Safety，不更改生成行为或证据规则。 |
| 用户可见变化 | 四状态、处理/重试按钮、自动刷新、回填确认及进度/汇总；失败详情统一安全提示，移除内部原文件路径展示。 |
| 旧路线 | 不恢复旧模式，不调用 processLibraryAll 或旧 scene/card/style 处理链。 |

## 实现合同

状态只读取 `ingestion_status`：

| 状态 | 用户文案 | tone | 操作 |
| --- | --- | --- | --- |
| INDEXED | 待处理 | neutral | 处理 |
| PROCESSING | 正在处理 | info | 处理中，禁用 |
| READY | 可用于写作 | success | 不显示处理按钮 |
| FAILED | 处理失败 | danger | 重试 |
| 未知、缺失、旧值 | 待处理 | neutral | 处理 |

列表与详情共用映射；`has_embedding` 不参与就绪判断。只有明确 INDEXED 才纳入历史回填，未知旧值不会自动处理。

单文档处理/重试即时显示 PROCESSING，以页面内请求 Map 防止快速重复点击。按官方响应更新，再每 2500ms 查询该文档；每篇只有一个轮询 Promise，列表观察、上传返回值观察、单篇处理和回填共享它。已知 PROCESSING 文档即使翻页也能继续被追踪，不会定时重读全库 READY 文档。READY/FAILED 后立即结束对应轮询。

页面卸载会 abort 页面控制器，清理 timeout，忽略迟到的 GET/POST 结果，停止安排后续资料。已被后端接受的任务继续由后端完成。没有在前端把请求异常直接伪装成持久化 FAILED。

409 MATERIAL_PROCESSING_BUSY 显示“资料正在处理中，请稍后查看。”，不再次 POST。随后通过 GET 确认服务端状态；如为 PROCESSING 则等待终态，如仍 INDEXED（例如旧处理持锁）则恢复真实状态、计为跳过，留待以后用户操作。

POST 连接中断可能发生在服务器已经接收之后，因此只通过 GET 核对，不自动重发 POST。若确认仍 INDEXED，可计一次调度失败并继续；若无法确认状态则暂停整个回填，不冒险启动下一篇。处理中的文档意外回到 INDEXED 也会暂停，避免将非终态当作完成。

处理失败详情只显示“资料处理未完成，可重试。”，不回显任意 ingestion_error.message、provider 原文、路径、堆栈或向量。轮询导致的状态更新不会清空用户尚未保存的标签输入。

## 受控回填与中断恢复

点击“处理已有资料”必须先确认以下完整文案：

> 将依次处理尚未完成材料处理的历史资料。  
> 处理过程会调用当前配置的 Embedding（向量化）服务。  
> 已经可用于写作的资料不会重复处理。

确认之后才 GET 全量列表（page=1, page_size=-1），按唯一 ID 筛选 INDEXED。逐篇再 GET 核对最新状态，仍为 INDEXED 才 POST。等待当前篇 READY 或 FAILED 后推进；FAILED 不妨碍下一篇，但不会自动重试失败篇。

页面显示“正在处理已有资料”、已处理数 / 本轮候选数以及当前标题。统计来自实际结果：成功、失败、跳过；跳过包含初始非 INDEXED 和执行前已改变状态的资料。只在全部候选走完后显示完成汇总；查询失败导致的暂停不会伪装成完成。

无需保存前端队列。离开、刷新或关闭页面后，不再安排下一篇；已 READY 的状态由后端保存，未执行的仍 INDEXED。再次确认回填会重新读取列表，只继续剩余 INDEXED。FAILED 始终需要用户单篇重试。

多浏览器页面之间没有全局前端队列锁；沿用后端单文档去重与锁定合同。一次回填流程内部保持串行，没有新增跨页面任务系统。

## 文件范围

修改：

- `frontend/src/pages/LibraryPage.jsx`
- `frontend/src/api.js`
- `backend/tests/test_material_ingestion.py`：用户本轮明确批准的唯一额外范围。

新增：

- `frontend/src/utils/libraryReadiness.js`
- `frontend/tests/utils/libraryReadiness.test.mjs`
- `docs/W3-8C-2B-library-readiness-ui-backfill.md`

后端测试旧断言将整个 frontend 固定在旧提交 `90b5a21`，与本轮明确授权的前端修改冲突。经用户确认，只豁免上述四个前端实现/测试路径（含未来暂存的新增文件）；保留所有后端 owner 以及其余前端文件的原有逐字节比较。未改业务预期或后端功能实现。

没有修改 CSS、依赖清单或 lockfile。

## 实际验证与失败记录

### 前端

工作目录 `frontend`：

```powershell
node --test tests/utils/*.test.mjs tests/pages/*.test.mjs
npm run build
```

- 原有 40 项先通过；新增 21 项后最终全集 **61 passed，0 failed**。
- 新测试覆盖工单 12 类要求，并覆盖 409、未知状态、重复点击、丢失 POST 响应、请求中途卸载、状态意外回退、回填前跨页面状态改变、异常数据安全文案。
- 构建 **PASS**：119 modules transformed。仍有主 bundle 超过 500 kB 的提示，不在本轮拆包重构范围。
- Node 测试使用 fake 文档、fake timer 与 Axios adapter，不发送实际 API 网络请求；没有新增测试框架。
- 原始日志：ignored `tmp/w3-8c-2b-frontend-tests.log`、`tmp/w3-8c-2b-frontend-build.log`。

### 后端

虚拟环境 `.venv/Scripts/python.exe` 的基础 Python 启动器不可用；改用已经安装的 Codex Python 及现有虚拟环境依赖，没有安装或升级包。

工作目录 `backend`，最终实际命令：

```powershell
$env:PYTHONPATH = 'D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'
$env:PYTHONIOENCODING = 'utf-8'
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tests/run_w3_8c_2a_offline.py -q tests --basetemp tmp/w3-8c-2b/full-04 --junitxml tmp/w3-8c-2b/full-04-results.xml 2>&1 | Tee-Object -FilePath ../tmp/w3-8c-2b-full-04.log
```

复跑应使用全新的 basetemp 名称，并先确保其父目录存在，避免 pytest 清理旧验证目录。

- 首次完整尝试因 basetemp 父目录不存在出现 244 个 fixture 错误，同时触发旧前端范围断言：113 passed、244 errors、1 failed。该次不是通过，日志保留。
- 父目录存在后，W3-8C-2A/R1 定向：38 passed、1 failed；完整回归：357 passed、1 failed。唯一失败均是上述历史前端范围断言。
- 用户批准窄范围调整后，最终完整离线回归 **358 passed，1 warning，51.16 秒**，包括 W3-8C-2A/R1。
- warning 为既有 Starlette/AnyIO 弃用提示。
- 复用原离线 runner：导入配置前隔离 runtime，拦截真实资料目录读取、真实 Embedding/模型入口及外部 socket。计数：`external_network=0, real_embedding=0, real_model=0, real_user_data_access=0`。
- ingestion fixture 的 Writer/Brief/旧提取链路哨兵全部通过，真实 Writer/Brief 调用为 0；全量离线套件中的模拟调用不计为真实外部模型调用。
- Windows asyncio socketpair 内部 IPC 102 次不是外部网络请求。

### 实际浏览器（仅合成资料）

用最终构建和独立 Node 内存模拟 API 在 `127.0.0.1:4179` 验证；该服务只读取构建文件，不连接实际后端，不读取任何真实 data/uploads。

- 五条模拟资料初始为 READY、INDEXED A、INDEXED B、FAILED、PROCESSING，并全部设置 has_embedding=true。页面仍正确显示四状态，A/B 为待处理。
- 确认弹窗完整显示服务调用说明；确认前没有 POST。
- 回填先 POST A，GET 到 READY 后才 POST B；B 进入 FAILED 后显示“成功 1，失败 1，跳过 3”。事件日志中 A terminal 在 B POST 之前。
- 失败详情显示固定安全文案；模拟响应故意包含 provider/路径/堆栈等字符串，页面未显示它们。
- 在详情点击单篇 FAILED 的“重试”，列表及详情立即同步为正在处理，按钮禁用，之后 GET 确认 READY。
- 最终构建重新加载后，再次回填显示“成功 0，失败 0，跳过 5”，未再 POST A/B 或其他资料。
- 离开资料库切到其他页后，PROCESSING 文档的轮询停止。
- 临时脚本及事件记录位于 ignored `tmp/w3-8c-2b-ui-server.mjs`、`tmp/w3-8c-2b-ui-events.jsonl`，不纳入交付源码。

## 验收字段

```text
W3_8C_2B = PASS
BASE_SHA = 2d3113c995b80b3cb546eb136c397a03cb06b369
BRANCH = codex/w3-8c-2b-library-readiness-ui
FRONTEND_USES_INGESTION_STATUS = YES
HAS_EMBEDDING_USED_AS_READY = NO
INDEXED_UI = 待处理
PROCESSING_UI = 正在处理
READY_UI = 可用于写作
FAILED_UI = 处理失败
SINGLE_PROCESS_SUPPORTED = YES
FAILED_RETRY_SUPPORTED = YES
PROCESSING_AUTO_REFRESH = YES
CONTROLLED_BACKFILL = YES
BACKFILL_SEQUENTIAL = YES
BACKFILL_READY_SKIPPED = YES
BACKFILL_PROCESSING_SKIPPED = YES
BACKFILL_FAILED_AUTO_RETRY = NO
BACKFILL_RESUMABLE = YES
LEGACY_PROCESS_LIBRARY_ALL_CALLED = NO
FRONTEND_TESTS = 61 passed
FRONTEND_BUILD = PASS
BACKEND_REGRESSION = 358 passed
REAL_EMBEDDING_CALL_COUNT = 0
WRITER_CALL_COUNT = 0
BRIEF_CALL_COUNT = 0
EXTERNAL_NETWORK_CALL_COUNT = 0 (开发/测试；不包括用户明确要求的 Git fetch)
REAL_USER_DATA_TOUCHED = NO
RETRIEVAL_POLICY_CHANGED = NO
WRITER_CHANGED = NO
BRIEF_CHANGED = NO
FACT_POLICY_CHANGED = NO
```

## 技术债、未运行事项与回退

- N3：旧 GET embed message 回显 chunks/vectors；只记录，未修改或调用该接口。
- 既有 `/embed-all` 失败响应类型不匹配；只记录，不修复。
- 保留构建 bundle 大小及 Starlette/AnyIO 提示；不升级依赖或顺手重构。
- 真实资料回填、真实 Embedding、真实 Writer/Brief、文章质量验收均未执行。正式真实回填必须等代码合并 main 后，由正式运行环境的用户通过 UI 确认执行。
- 没有本轮范围内未完成事项；合并、提交、PR、真实回填属于本轮禁止事项。

最终未暂存：三个修改文件、三个新增文件。HEAD 未变；没有 commit message、push、PR 或 main 修改。日志、模拟服务及构建产物保留 ignored，不吸入暂存。

需要回退时，先确认没有后续用户编辑并停止相关前端会话；仅恢复上述三个跟踪文件到 BASE_SHA，移除上述三个本轮新增文件。不要恢复整个仓库，不动真实资料或 ignored 目录。以下命令仅供确认后执行，本轮未执行：

```powershell
git restore --source=2d3113c995b80b3cb546eb136c397a03cb06b369 -- frontend/src/api.js frontend/src/pages/LibraryPage.jsx backend/tests/test_material_ingestion.py
Remove-Item -LiteralPath 'frontend/src/utils/libraryReadiness.js','frontend/tests/utils/libraryReadiness.test.mjs','docs/W3-8C-2B-library-readiness-ui-backfill.md'
git status --short
```

大白话：现在能看清哪些资料可写作、哪些还需要处理，也能单篇重试；处理历史资料必须先确认，一篇结束再开始下一篇。关闭页面后不会继续排后面的资料，再回来只处理剩余待处理项。本轮没有碰真实资料。
