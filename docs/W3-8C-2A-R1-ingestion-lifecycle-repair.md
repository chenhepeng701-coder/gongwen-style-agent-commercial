# W3-8C-2A-R1 生命周期修复验收

类型：本工单实现与验收记录，不是新项目规则。范围只包括 N1、N2、N5 修复及 N4 补测。

## 结论

```text
W3_8C_2A_R1 = PASS
N1_FIXED = YES
N2_FIXED = YES
N5_FIXED = YES
N4_TEST_ADDED = YES
PROCESSING_CAN_STALE_RECOVER = YES
STALE_JOB_DOES_NOT_BLOCK_RETRY = YES
FAILED_EMBEDDING_RESIDUE = NO
LEGACY_EMBED_INVALIDATES_READINESS = YES
BUSY_BRANCH_TESTED = YES
READY_CONTRACT_CHANGED = NO
RETRIEVAL_POLICY_CHANGED = NO
WRITER_CHANGED = NO
FACT_POLICY_CHANGED = NO
REAL_EMBEDDING_CALL_COUNT = 0
WRITER_CALL_COUNT = 0
BRIEF_CALL_COUNT = 0
EXTERNAL_NETWORK_CALL_COUNT = 0
REAL_USER_DATA_TOUCHED = NO
```

本轮未运行真实服务、真实 Embedding、真实 Writer 或当前用户资料库。没有安装新依赖，没有改 RAG、300/800、1 主 + 2 辅、Atomic Evidence、Fact Safety、frontend、旧 scene/card/style 抽取器。

## 基线与推理闸

开始时：分支 `codex/w3-8c-2a-ingestion-readiness`，HEAD `90b5a2141580610e9c60e1805db7a034affb57b5`，W3-8C-2A 的 6 个跟踪修改与 5 个新增文件均未提交，暂存为空。用户已明确授权在这些改动上执行 R1，不能用 Git HEAD 覆盖它们。

源码确认的问题：

- N1：`_jobs` 只存 token；readiness 与 retry 看到 token 就认为活动，无法识别响应发送失败导致的后台任务未启动。
- N2：`_fail` 只重写索引。Embedding 成功后资产失败会残留向量文件，旧 GET embed 仍按文件报告成功。
- N5：旧 Embedding 重建会改索引状态，但没有使旧 manifest 失效。
- N4：既有非阻塞锁和 409 分支存在，缺少实际持锁情况下的接口测试。

唯一生命周期 owner 仍为 `material_ingestion.py`；router 继续调用它，embedder 在旧入口重建时调用同一 owner 的失效钩子。没有改切块合同、稳定资产规则、manifest schema 或 READY 校验条件。风险是抢占正常运行任务、晚到的旧回调破坏新任务、失败后旧查询误报成功；由并发锁测试、可控时钟、真正的 ASGI 发送异常及磁盘文件断言验证。

修改前将现有 11 个文件逐字节保存到 `backend/tmp/w3-8c-2a-r1/checkpoint/`，含 `sha256.json`。这是不创建 commit 的本地回退快照，没有复制真实资料。

## 实现

### N1：可恢复的等待启动租约

`LibraryDocument.ingestion_lease_expires_at` 保存 UTC epoch 秒，默认 0，旧 JSON 兼容。登记 PROCESSING 时设置 300 秒租约。

查询、重试、后台启动都经过 `_recover_processing`：

1. 尝试非阻塞获取同一文档生命周期锁。
2. 等待启动且租约有效时继续去重。
3. 租约过期且没有真正运行的 worker 时撤销旧 token，落盘 FAILED，并清理向量和 manifest。
4. 查询返回 FAILED；直接 retry 可先收敛失败再登记新任务。旧回调晚到时 token 不匹配，不能执行或撤销新任务。

实际 worker 启动后才登记 `_running_jobs`，在 finally 清除运行登记和 token。各处理阶段刷新租约；正常 worker 始终持有文档锁，不能只因某一阶段较慢而被判过期。测试也覆盖同线程重入查询，不能用 RLock 可重入误判为无人执行。

没有新增定时线程或队列，也没有扫描真实库。过期恢复由查询、重试或迟到后台回调触发；不会自动调用外部服务。进程重启后的 PROCESSING 没有活动 token，仍按原中断语义转 FAILED。

### N2：失败清理

统一 `_fail` 先写 FAILED、清除 has_embedding 和向量元数据/租约，然后删除该文档向量文件与 manifest。保留已完成的内容寻址缓存以供重试复用；缓存本身不表示 READY。

真实资产失败、manifest 写入失败、manifest 最终校验失败三条路径都验证：失败前向量文件确实已写入，失败后文件确实不存在，旧 GET embed 返回 `success=false`，再次 process 会重新生成完整向量并到达 READY。

### N5：旧入口使完成状态失效

单文档 `/embed` 与 `/embed-all` 共用 `generate_embeddings`。旧入口在请求向量前通过 owner 清理 manifest、清空完成时间与租约，将状态改为 INDEXED，并撤销尚未启动的 process token。成功或失败均不会保留旧 READY manifest。

稳定资产缓存可保留；下一次完整 process 重新写 manifest 并验证 READY。未删任何旧 API，未调用 scene/card/style。额外测试保证已取消的旧后台回调不会在 legacy embed 后意外再跑完整处理。

### N4：实际忙碌路径

用另一个线程运行真正的旧 `generate_embeddings`，只在 fake Embedding 内用事件暂停。持锁期间两次 POST process 均返回 409 MATERIAL_PROCESSING_BUSY；索引状态逐字段不变，BackgroundTasks 没有新增任务，`_jobs` 不出现第二个任务。原处理结束后，POST process 正常登记且完成 READY。

原新链活动任务的重复请求仍按原合同返回 202 并去重，而非改成 409。

## 本轮文件

在 W3-8C-2A 未提交成果上修改：

- `backend/app/models/library.py`
- `backend/app/services/library/material_ingestion.py`
- `backend/app/services/library/embedder.py`
- `backend/tests/test_material_ingestion.py`（加强旧 JSON 与运行登记清理检查）
- `docs/W3-8C-2A-ingestion-readiness.md`

新增：

- `backend/tests/test_material_ingestion_lifecycle.py`
- `docs/W3-8C-2A-R1-ingestion-lifecycle-repair.md`

相对修复前快照，`_validate_manifest` 与 `get_embed_status` 函数内容完全不变；router 整个文件也未修改。N3 的旧 message 向量回显问题未处理。

## 实际验证

- 修复前复现：12 项新验收测试中 11 failed、1 passed；失败包括缺少租约字段/运行登记、过期登记无法恢复、向量和 manifest 残留。409 用例在修复前已通过，证明此项是补测。
- 修复后定向加 W3-8C-1/W3-8C-2A 回归：109 passed，1 warning，31.05 秒。
- 随后补充“legacy embed 取消尚未开始的旧回调”测试，并在最终完整 backend 中运行。
- 最终完整 backend：358 passed，1 warning，52.31 秒。其中本轮 13 项，W3-8C-2A 26 项，W3-8C-1 71 项，全通过；其余后台测试同样通过。
- 离线计数：external_network=0、real_embedding=0、real_model=0、real_user_data_access=0。新链 fixture 的 Writer/Brief/scene/card/style 哨兵全部为 0。
- 唯一 warning 为既有 Starlette/AnyIO 弃用提示；未升级依赖。

复用原离线 runner，导入配置前隔离 runtime；拦截真实资料目录读取、真实 API 和外部 socket。仅允许 Windows asyncio socketpair 的内部 IPC。

工作目录 `backend`，实际命令前缀：

```powershell
$env:PYTHONPATH = 'D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'
$env:PYTHONIOENCODING = 'utf-8'
```

定向与回归：

```powershell
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tests/run_w3_8c_2a_offline.py -q tests/test_material_ingestion_lifecycle.py tests/test_material_ingestion.py tests/test_stable_material_assets.py --basetemp tmp/w3-8c-2a-r1/directed-01 --junitxml tmp/w3-8c-2a-r1/directed-results.xml 2>&1 | Tee-Object -FilePath tmp/w3-8c-2a-r1/directed-results.log
```

完整后台：

```powershell
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tests/run_w3_8c_2a_offline.py -q tests --basetemp tmp/w3-8c-2a-r1/full-01 --junitxml tmp/w3-8c-2a-r1/full-results.xml 2>&1 | Tee-Object -FilePath tmp/w3-8c-2a-r1/full-results.log
```

复跑应换新的 `--basetemp` 目录，避免 pytest 清理旧证据。所有日志/快照留在 ignored tmp 目录，未暂存。

## 已知债务与未运行事项

- **N3 未处理**：GET embed 的 message 包含向量内容，用户明确排除。本轮没有更改该函数。
- **另行发现的既有问题**：`/embed-all` 失败时 `public_batch_error` 返回字典，但 `BatchEmbedResponse.errors` 声明为 `list[str]`，导致响应序列化失败。修复前已复现；本轮不改该合同。测试显式预期这个既有 ValidationError，同时验证真实 FAILED 状态、文件清理和后续 process 成功，未将旧接口失败描述为成功。
- 单进程边界不变；没有多 worker 或持久化队列。租约处理的是未启动/无有效执行者的登记，不强杀仍持锁运行的线程。
- 未调用真实 Embedding、未处理用户真实资料、未运行页面或 Writer。产品文章质量不是本轮验收内容。

## Git 与回退

HEAD 仍为 `90b5a2141580610e9c60e1805db7a034affb57b5`；分支不变。没有新 commit、push、PR 或 main 修改；暂存区为空。本轮保留之前的未提交成果，禁止用 `git restore` 到 HEAD 撤销整个 W3-8C-2A。

仅撤销 R1 时，应停止服务、确认这些文件没有叠加后续改动，再从 `backend/tmp/w3-8c-2a-r1/checkpoint/` 恢复本节五个修改文件，移除本轮两个新增文件。不处理任何真实 data/uploads。回退未执行。

项目根目录可执行的精确回退命令（执行前须确认没有后续编辑）：

```powershell
$r1Files = @('backend/app/models/library.py','backend/app/services/library/material_ingestion.py','backend/app/services/library/embedder.py','backend/tests/test_material_ingestion.py','docs/W3-8C-2A-ingestion-readiness.md')
foreach ($r1File in $r1Files) {
    Copy-Item -LiteralPath (Join-Path 'backend/tmp/w3-8c-2a-r1/checkpoint' $r1File) -Destination $r1File
}
Remove-Item -LiteralPath 'backend/tests/test_material_ingestion_lifecycle.py','docs/W3-8C-2A-R1-ingestion-lifecycle-repair.md'
git status --short
```

大白话：上传请求断开后没启动的任务可以过期恢复；材料处理失败就撤掉成功向量和完成清单；旧向量接口重跑也不能保留旧 READY，完整重试之后才能重新就绪。
