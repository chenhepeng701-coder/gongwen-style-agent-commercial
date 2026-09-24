# NEXT-PERF-01｜Writer 流式可观测性与代理真值

文档类型：实现说明与本轮离线验收记录；不替代 AGENTS.md 或产品合同。
日期：2026-09-23。基线及本轮 LOCAL_HEAD：`01d1b6a1dbaa406a4b6dc1915631cae93b4ae7e4`，分支 `main`。

## 当前证据与边界

当前仓库确认的调用链为 `/api/generation/generate` → `generate_commercial_flow`
→ `generate_full_draft` → `OpenAICompatibleProvider.generate_text` → `_receive_stream`。
本轮只对该正式 Writer 调用设置观测上下文，其他 Provider 调用的观测钩子为空操作。
参考 Run `99ad8c75b4204d1da54bd63d5743ef12` 的 accounting 确认为一次 Writer、一次 HTTP、retry=0；
本轮不重新生成、补写或修改这个历史 Run，也不以历史文件 mtime 推断新的阶段时间。

推理闸结论：

- 问题是缺少可归属到一次 Writer 请求的阶段证据，不是已经确定某一性能根因。
- 已确认 Provider 内部等待完整流，只有 content 参与正文拼接，finish_reason 不直接退出循环；
  accounting 的最终写入覆盖中间状态，原代理说明只检查环境变量。
- 371.998s / 366.661s 是工单提供的历史审计值，本轮未重新测量。
  reasoning_effort/xhigh、网络、服务端排队或推理是否主因仍未确认。
- run 与 Writer 边界由 `full_draft_generation.py` 创建；Provider 创建传输事件；
  `writer_observability.py` 唯一负责时间线白名单、计时与追加持久化；
  `http_client.py` 负责代理配置解析。后续审计读取 run 内工件。
- 现有 accounting、Provider metadata 与 HTTP 辅助模块被复用；只增加一个小型 trace owner，
  不新增服务、依赖、公共接口参数、业务状态或客户端交互。
- 最小实现是调用上下文加观察钩子。最大风险是诊断故障影响生成、内容泄露、代理假阴性；
  用离线传输故障、I/O 故障、敏感哨兵、HTTPX 实际 transport 选择与基线差分测试阻断。
- 不改变文章质量规则、事实安全、用户交付语义、旧模式边界。RAG、Evidence、Prompt、
  FLOW3/FLOW4、model、stream、timeout、temperature、reasoning_effort、thinking_budget、retry 均不调整。

## 工件及字段

下一次正式调用在既有输出目录生成：

```text
backend/output/full-draft/<run_id>/writer_timeline.jsonl
backend/output/full-draft/<run_id>/call_accounting.json
```

accounting 新增 `run_id`、`writer_trace` 文件名引用，原有业务字段与状态保持原语义。
JSONL 每行是一个里程碑时的脱敏快照，以追加方式写入；finally 不覆盖首行 `writer_running`。
不按每个 token 写磁盘。计数在内存更新，并在首次事件、首次正文/推理、首次 finish_reason、
DONE/EOF、流关闭及 Provider 退出等节点写入。

| 字段 | 含义 |
| --- | --- |
| `run_id`, `writer_attempt`, `http_attempt` | 当前 run、逻辑 Writer 次数、Provider HTTP attempt；正式调用保持 1 次及 retry=0 |
| `clock`, `offset_unit`, `offset` | monotonic、seconds、该行相对 Writer 调用上下文开始的偏移 |
| `offsets.writer_running` | Provider 已进入该 attempt，保留中间运行时刻 |
| `offsets.request_start` | HTTPX 请求前的观测时刻 |
| `offsets.response_headers` | HTTPX 将响应头交给调用方的时刻，先于 HTTP 状态校验 |
| `offsets.first_sse`, `first_sse_type` | 第一个完整 data 事件；类型为 reasoning_content / content / other |
| `offsets.first_reasoning`, `last_reasoning` | 首次、末次非空 reasoning_content 事件的时刻，不保存文本 |
| `offsets.first_content`, `last_content` | 首次、末次非空 content 事件的时刻 |
| `offsets.first_finish_reason`, `finish_reason` | 首次完成原因及允许值；未知服务端字符串脱敏，不改变退出策略 |
| `offsets.done_or_eof`, `termination` | 收到 DONE 或迭代器正常结束；异常中断尚未到达时保持 null |
| `offsets.provider_return`, `outcome` | 完整 generate_text 调用退出时刻；成功为 success，抛异常为 error，不将异常伪装为返回正文 |
| `reasoning_event_count`, `content_event_count`, `other_event_count` | 非空推理/正文事件数，以及两者都没有的 data 事件数；同一事件含两者时分别计数 |
| `received_character_count` | content 累计字符数，与原正文拼接计数一致 |
| `received_bytes` | HTTPX num_bytes_downloaded，包括 SSE 封装；未拿到 response 时为 null |
| `request_id`, `response_id` | 服务端 ID 的 SHA-256 指纹对象；缺失为 null，不落盘原始 ID |
| `usage` | 允许的非负整数 token 计数及已知 token details；缺失或不合法为 null |
| `proxy` | 本次 URL 与原 request kwargs 对应的可生效代理配置快照 |
| `error_type`, `transport_error_type`, `exception_types` | Provider 对外异常、HTTPX 底层传输类型、异常因果链类型；不保存异常消息 |

SSE 注释、event/id/retry 行不算 data 事件；DONE 是终止标记，不进入三类事件计数。
空 content/reasoning 字符串算 other，不提前触发 first_content/first_reasoning。
尚未到达的阶段一直为 null。可直接计算 response_headers−request_start、
first_content−first_sse、done_or_eof−last_content、done_or_eof−first_finish_reason、
provider_return−done_or_eof 等客户端观测窗口。

## 代理与故障诊断方法

代理解析复用当前锁定 HTTPX 0.28.1 的 `get_environment_proxies` 和排序后的 `URLPattern`。
其 `urllib.request.getproxies()` 在 Windows 会按现有规则回退到 WinINET 注册表；
环境变量映射非空时不会回退，包括仅设置 NO_PROXY 的情况。
按本次目标 URL 匹配 scheme、ALL_PROXY 和 NO_PROXY；保留原 kwargs 的 trust_env 值。
记录 `resolution_source`、`effective_proxy`、`trust_env`、`proxy_configured`、`bypassed`。
有效状态包括 environment、windows_system、none、explicit、system、unknown。
解析异常为 unknown 并记录诊断异常类型，绝不冒充明确无代理。
不保存代理地址、账号、密码、环境变量原值、目标 URL 或请求头。
本轮没有设置 trust_env=False；原辅助函数已有的无效代理处理保持不变。

异常通过既有 `raise ... from exc` 形成的因果链提取类型，因此 `LLMConnectionError`
仍可对应 ConnectTimeout、ReadTimeout 或 RemoteProtocolError。诊断 I/O、时钟初始化和
代理观测故障均不替换原返回值或异常。上下文退出后清理绑定。

## 验证记录

项目 `.venv` 的 Windows Store Python 启动路径失效。使用已安装的同 ABI Python 3.12.14，
通过 PYTHONPATH 读取项目现有依赖（HTTPX 0.28.1 / pytest 8.3.5 / Pydantic 2.11.4 / FastAPI 0.115.12）。
未安装依赖，未修改虚拟环境或配置。

从项目根目录实际运行：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'backend/.venv/Lib/site-packages')
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' backend/tests/run_w3_8b_offline.py -q backend/tests/test_production_task_assembly.py --basetemp tmp/next-perf-01/baseline
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' backend/tests/run_w3_8b_offline.py -q backend/tests/test_writer_observability.py --basetemp tmp/next-perf-01/targeted-1 --junitxml tmp/next-perf-01/targeted-1.xml
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' backend/tests/run_w3_8b_offline.py -q backend/tests/test_writer_observability.py backend/tests/test_production_task_assembly.py --basetemp tmp/next-perf-01/targeted-2 --junitxml tmp/next-perf-01/targeted-2.xml
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' backend/tests/run_w3_8b_offline.py -q backend/tests/test_writer_observability.py backend/tests/test_production_task_assembly.py backend/tests/test_writing_reference_pack_02b1.py --basetemp tmp/next-perf-01/targeted-3 --junitxml tmp/next-perf-01/targeted-3.xml
git diff --check
```

- 修改前基线：21 passed。
- targeted-1：24 passed / 2 failed / 2 errors。失败是新夹具将 HTTPX 自动关闭误作上下文退出，
  以及禁止 Windows asyncio 内部 loopback socket。已修正夹具，继续禁止外部连接。
- targeted-2：48 passed / 1 failed。新 API 成功夹具的标题有前置空格，不符合既有 candidate 合同；
  改为合法合成输入，保留 Provider 级首尾空白原样拼接验证，未修改业务校验。
- targeted-3：86 passed，22.70s。唯一 warning 是基线也存在的 Starlette/AnyIO 弃用提示。
- git diff --check：通过。
- 七个本轮文件按严格 UTF-8 解码检查通过；JUnit 确认 86 tests / 0 failures / 0 errors / 0 skipped。

测试使用合成 SSE 和真实本地 parser/交付链。验证 DONE、EOF、无末尾换行、UTF-8 分片、
reasoning/content/other、缺失可选字段、敏感服务端 metadata、三类传输异常、诊断故障、
Windows/环境变量代理/直连/NO_PROXY、正式 API 成败以及 accounting finally 后的历史留存。
从当前仓库基线提交加载原 Provider，在完全相同合成流下比较原实现与新实现的正文、
异常类型/因果链及整个 HTTP request kwargs（包括 model、messages、temperature、stream、timeout）。
protected-owner 测试只补充本轮明确授权的两个 LLM 文件豁免，并由上述差分测试保护行为。
没有改写功能测试预期以掩盖业务失败。

本地 JUnit 证据：`tmp/next-perf-01/targeted-3.xml`。
合成时间线样例位于同名 basetemp 目录内各用例的 `writer_timeline.jsonl`；正式 API 样例位于
`test_formal_api_binds_run_and_*/full-draft/<synthetic_run_id>/writer_timeline.jsonl`。
这些均为离线测试工件，不是真实模型生成或文章质量验收。

## 局限与交付状态

- 时间是客户端可观察边界，不能分解 DNS/TCP/TLS、服务端排队与真实计算时间；
  收到 reasoning 事件的时间也不是服务端内部推理的精确起止。
- EOF 时刻是 HTTPX 行迭代器结束，包括库内部可能先完成的流关闭。
  request_start 包含代理快照与诊断写入的小量开销。
- 代理是请求前按 HTTPX 同一规则解析的可生效配置，不是抓包证明；紧邻请求前发生的配置变动、
  透明代理、VPN、PAC 或系统外部网络路径不在该证明范围内。使用 HTTPX 私有解析函数，升级时需回归。
- ID 仅存指纹，不能从工件还原原始服务端 ID；usage 仅保存已知数字字段。
- 正式 Writer 仍为 retry=0。本轮不增加 retry 或改变非流式接收方式。
- 持久化是 best effort：进程强杀、磁盘故障时可能只有早期快照，计数不能视为完整结果；
  诊断失败日志只包含异常类型，不影响 Writer 原成败。
- 未运行外部模型、真实页面、黄金样本或性能对比；不声称性能提升或生成质量提升。

```text
OBSERVABILITY_POINTS_IMPLEMENTED = YES
RUN_ID_CORRELATION = YES
MONOTONIC_TIMELINE = YES
REASONING_TEXT_PERSISTED = NO
PROMPT_OR_ARTICLE_PERSISTED_BY_TRACE = NO
EFFECTIVE_PROXY_STATE_RECORDED = YES
TRANSPORT_ERROR_TYPE_PERSISTED = YES
GENERATION_BEHAVIOR_CHANGED = NO
EXTERNAL_MODEL_CALLS = 0
READY_FOR_EXTERNAL_REVIEW = YES
```

## Git 与回滚

遵照本轮明确指令：没有 git add、git commit、push 或 PR。修改前后 HEAD 相同；staged 为空。
原有 7 个未跟踪诊断文件保持原样。改动为 4 个已跟踪文件及 3 个新增文件：

```text
backend/app/services/generation/full_draft_generation.py
backend/app/services/llm/openai_compatible_provider.py
backend/app/utils/http_client.py
backend/tests/test_production_task_assembly.py
backend/app/services/llm/writer_observability.py
backend/tests/test_writer_observability.py
docs/NEXT-PERF-01-writer-observability.md
```

修改前 checkpoint 为上述 HEAD，以及 `tmp/next-perf-01/checkpoint/` 中三个生产源文件的原始副本。
回滚时只将四个已跟踪文件恢复到该 HEAD，并移除三个本轮新增文件；先复核这些路径没有后续用户改动。
不得清理七个既有未跟踪文件或用户运行目录。本轮未执行回滚。

确认放弃本轮改动且上述文件无后续修改后，可从项目根目录执行以下定向回滚命令（未执行）：

```powershell
git restore --source=01d1b6a1dbaa406a4b6dc1915631cae93b4ae7e4 -- backend/app/services/generation/full_draft_generation.py backend/app/services/llm/openai_compatible_provider.py backend/app/utils/http_client.py backend/tests/test_production_task_assembly.py
Remove-Item -LiteralPath 'D:\AI_Projects\gongwen-style-agent-commercial\backend\app\services\llm\writer_observability.py','D:\AI_Projects\gongwen-style-agent-commercial\backend\tests\test_writer_observability.py','D:\AI_Projects\gongwen-style-agent-commercial\docs\NEXT-PERF-01-writer-observability.md'
```
