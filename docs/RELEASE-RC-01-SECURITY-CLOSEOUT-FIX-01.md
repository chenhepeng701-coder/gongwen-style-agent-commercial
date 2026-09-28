# RELEASE-RC-01-SECURITY-CLOSEOUT-FIX-01 验收记录

本文仅记录 B1/B2 修复，不是新的项目规则。项目路径为 `D:\AI_Projects\gongwen-style-agent-commercial`，分支 `codex/release-rc-01`，BASE / HEAD 为 `e50981945773ac7263a5f90fb7f69569ff584098`。保留此前 RC-01 全部未提交改动，本任务不暂存、不提交、不推送、不进行外审。

## 当前证据与修改边界

修改前配置 owner 的 `save_settings()` 无条件移除 API Key，已解析的环境 Secret 不落盘，但原始 placeholder 也因此丢失。`configure_reference()` 经 `load_settings()` 调用此保存函数，构成 B1 的实际回归路径。桌面启动器在环境变量清理后直接导入应用并创建 listener，旧明文只在首次 Settings 加载时迁移，构成 B2。

唯一配置 owner 仍为 `backend/app/config.py`；调用者为原设置 API、Writing Reference 配置服务及本次接入的 `backend/desktop_launcher.py`。Secret Store 仍为原 Windows DPAPI CurrentUser 实现，不修改加密、身份绑定、存取或验证规则。

最小改法：配置保存从当时磁盘 YAML 读取合法 `${ENV_NAME}` 原值；只在对应 identity 有显式非空 Secret 更新时移除 placeholder。Provider、Embedding 使用同一处理。抽取磁盘读取函数并给现有迁移增加 migration-only 分支，启动器在 Runtime Root / 写入 guard / 单实例锁 / packaged 环境清理之后、导入应用和建立 socket 之前调用。无明文直接跳过 DPAPI，不触发 Settings、Reference 资源加载或任何模型服务。placeholder 在两种模式均保留，安装版仍不解析其值。

主要风险为新 Key 被旧 placeholder 覆盖、环境解析值误落盘、迁移半途失败后丢失原配置、失败后 HTTP 仍启动。新增测试分别检查空 Key、新 Key、磁盘原值变化、真实 Writing Reference HTTP 保存、缓存清除、第二个 Secret 失败、回读验证失败及原子替换失败。实际 EXE 验收检查真实 DPAPI、首次 ready、Windows TCP 监听和退出码。

不触及文章质量链路、RAG、Retrieval、Writer、Evidence、Fact Safety、FLOW3/FLOW4、Must-Deliver、Commercial Gate、Genre/Scene/Business Domain，也不恢复旧模式。用户可见变化仅为 placeholder 持续可用，以及安装版迁移失败时启动失败而非等待 Settings 请求。未更改 Origin 或 Writing Reference runtime 数据流。

## 精确 reseal

`backend/tests/release_rc_01_package_scope.py` 仅更新 `backend/app/config.py` 对应值，遵循原 guard 的 CRLF → LF 规范化。其他 hash、assertion、guard AST、allowlist 和历史溯源 pin 不变。

| 字段 | 值 |
| --- | --- |
| old SHA-256 | `8da951cd255142b4e51f37fcac6b50ea10a24bd18df5c02e19d0efa3725c780d` |
| new SHA-256 | `367c4b1a78b2b139d63bf600e4995099b318f28097b13590a0121c962085fdb8` |
| reason | `B1_DISK_PLACEHOLDER_PRESERVATION_AND_B2_MIGRATION_ONLY_STARTUP_ENTRY` |

验证：将新 hash 替回旧值后，guard AST 与修改前完全一致；对最终 `config.py` 的 13,789 个字节逐一执行单字节 XOR 1 内存篡改，全部被原 guard 拒绝。没有修改真实文件来执行篡改验证。

## 实际验收

证据根目录：`tmp/release-rc-01/security-closeout-fix-01/`。当前新候选：`tmp/rc01-78a656e3/`。`SECURITY_CLOSEOUT_FIX_01 = PASS`。

- 首轮 B1/B2 + Secret Store + Launcher 定向：39 passed，1 个既有 Starlette/AnyIO 弃用 warning。
- 全部 release/security + material ingestion / production task assembly / business domain 受影响回归：231 passed，1 个既有 warning。
- 上述离线 runner 的外网、真实模型、真实 Embedding、受保护用户资料访问计数均为 0。
- backend full offline：**1753 passed、0 failed、2 skipped、1 warning，513.65 秒**。两个 skip 为原有 P3A/P3B 私有真实 Run 的授权限制；warning 为原有 Starlette/AnyIO 弃用提示。全量 runner 的外网、真实模型、真实 Embedding、受保护用户资料访问计数均为 0，没有新增 skip / xfail 或改动 runner。
- PyInstaller / NSIS 构建通过；前端生产源码未改。既有构建器在新的 stage 内重建前端，这是打包步骤；未重跑前端 full。
- 实际 packaged startup：7 个场景全部 PASS，普通 Windows 用户令牌。首次 ready 时 Provider 和 Embedding 已迁移并可用真实 DPAPI 解密；此前验收脚本没有发送 Settings 或其他 HTTP 请求。第二次启动配置/密文原字节不变。无明文、placeholder、环境 Secret 隔离通过。真实 Secret Store 写入障碍、Windows 配置替换锁均使启动失败，无 HTTP listener，原 config.yaml 字节不变，日志无 Secret，退出码为 1。
- startup 专项没有调用 Provider / Embedding / 外网，计数均为 0。
- 现有安装回归通过：安装/卸载、DPAPI 配置与重启、Origin、单实例、Reference 上传、资料 READY、Word 导出、运行文件无测试 Secret 明文均通过。该原有回归只向 loopback fake 服务发出 2 次 LLM 和 3 次 Embedding 合成请求，真实调用均为 0，不能将这些合成结果视为真实生成验收。
- Packaging smoke：**5 passed**，包含新增的当前候选 startup 验收归属检查；原有 4 项断言完整保留。
- 范围与候选一致性：除本轮配置改动外，原有 12 个 tracked diff 完全相同；原有 untracked 状态均保留。340 项当前源码/打包输入与本次候选 BUILD-MANIFEST 的逐文件 SHA-256 一致。HEAD、分支不变，暂存区为空。`git diff --check` 通过。

已保留验收脚本修复过程中的失败证据：首次 TCP 查询将无匹配或查询失败混为非零退出；随后明确暴露沙箱对 CIM 的拒绝访问。经自动审批，以普通用户令牌在沙箱外运行原 TCP 断言。另一次错误对话框未按测试脚本的 WM_COMMAND 方式关闭；脚本改发 WM_CLOSE 后，同候选全部通过。这些修改仅属于测试支持，未更改生产代码或放宽断言。

主要实际命令：

```powershell
$python = 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
Set-Location -LiteralPath 'D:\AI_Projects\gongwen-style-agent-commercial\backend'
& $python -B -c "import sys; sys.path.insert(0,r'D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'); sys.path.insert(0,'tests'); sys.argv=['security-fix','-q','-rs','tests']; from run_multi_genre_offline import main; raise SystemExit(main(workspace_relative='tmp/release-rc-01',task_name='RC01-SECURITY-FIX-01-FULL',count_label='FIX01_OFFLINE_COUNTS',require_user_manifest=True))"
Set-Location -LiteralPath '..'
& $python -B packaging/windows/build_release.py --site-packages backend/.venv/Lib/site-packages --build-tools tmp/release-rc-01/build-tools --nsis tmp/release-rc-01/installer-tools/nsis-3.12
$fixBuild = Get-Content -LiteralPath tmp/release-rc-01/latest-package.txt
& $python -B -c "import sys,runpy; sys.path.insert(0,r'D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'); sys.path.insert(0,'packaging/windows'); sys.argv=['startup_migration_acceptance.py','--build',sys.argv[1]]; runpy.run_path('packaging/windows/startup_migration_acceptance.py',run_name='__main__')" $fixBuild
& $python -B -c "import sys,runpy; sys.path.insert(0,r'D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'); sys.argv=['dev_smoke.py','--build',sys.argv[1]]; runpy.run_path('packaging/windows/dev_smoke.py',run_name='__main__')" $fixBuild
& $python -B packaging/windows/test_packaging_manifest.py
git diff --check
```

## 回滚与范围

快照为 `tmp/release-rc-01/security-closeout-fix-01/before/`、`before.diff`、`before-status.txt`。因用户明确禁止 Git mutation，使用逐文件快照作为本轮增量 checkpoint，没有创建提交或暂存内容。后续只有在用户另行授权回滚时，才恢复本轮改过的既有文件快照并处理本轮新增测试、验收脚本及文档；不得 reset、clean 或覆盖此前 RC-01 实现。Secret Store、Origin 和 Writing Reference runtime 原文件未改。

本轮不是文章质量优化或商业发布验收，没有执行真实模型、真实 Embedding、真实生成稿、黄金样本质量确认或干净机验收，不声称这些通过。
