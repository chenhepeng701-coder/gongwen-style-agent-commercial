# DESKTOP-NATIVE-SHELL-FIX-01 验收记录

记录日期：2026-09-28。仅记录本轮 B1/B2 发行修复，不是新的项目规则。

## 结果与边界

`DESKTOP_NATIVE_SHELL_FIX_01 = PASS`，可以提交 ChatGPT 再审。本轮没有执行 External Review、暂存、提交、push、PR 或 merge。

- BASE / HEAD：`7d2df31b3660e656eadf483478785588e5ec53aa`。
- 分支：`codex/desktop-native-shell-01`。
- 开始时已有 DESKTOP-NATIVE-SHELL-01 未提交实现，保留了全部原实现和其他用户文件。
- 本轮生产 owner：`backend/desktop_shell.py`、`packaging/windows/build_release.py`、`installer.nsi`、`manifest.json`、`verify_package.py`。
- 本轮支持文件：`backend/tests/test_desktop_native_shell.py`、`packaging/windows/test_desktop_packaging.py`、新增 `test_desktop_release_contract.py`、新增 `licenses/proxy_tools/LICENSE.txt`、Windows README、本记录。
- 原 `proxy_tools-NOTICE.txt` 字节保持不变。
- 没有改变 launcher、窗口行为、单实例、Origin / CSRF、DPAPI、FastAPI、React 业务页面或生成链。没有新增依赖、运行真实 LLM / Embedding 或业务外网调用。

## 已确认原因和最小修复

修改前 Python 的 `runtime_version()` 和 NSIS 两个注册表检测分支分别写死 86.x，不能表达本工单要求的 pywebview 6.2.1 最低 Runtime 合同。修改后唯一常量为 `desktop_shell.WEBVIEW2_MIN_RUNTIME = (146, 0, 3856, 49)`。构建器静态读取 **stage** 中的该常量，不导入 Windows UI；将其传给 NSIS 的 `/DWEBVIEW2_MIN_RUNTIME`。NSIS 没有默认值，缺少构建参数时拒绝编译。随包 Runtime 还必须达到该最低版本。

Python 消费常量作检测，构建器传递合同，NSIS 消费合同作安装选择。只改变兼容性判断，不改变 renderer、窗口、关闭、聚焦等行为。最大回归风险为版本阈值漂移和错误跳过随包安装；边界测试和编译执行的 NSIS 测试覆盖这些风险。

修改前 build 只为 proxy_tools 收集 NOTICE，包验证也没有独立要求完整 LICENSE。修改后 LICENSE 是固定构建输入；构建前、stage、最终包分别验证 SHA-256。即使库存表漏掉文件，也不能用 NOTICE 代替完整许可证。保留原有许可、来源和业务文件选择断言。

## 固定许可证来源

- 官方仓库：[jtushman/proxy_tools](https://github.com/jtushman/proxy_tools)。
- 固定 commit：`ccd35a569b95bec271a59890780c7821756c548a`。
- 原始来源：[LICENSE.txt](https://raw.githubusercontent.com/jtushman/proxy_tools/ccd35a569b95bec271a59890780c7821756c548a/LICENSE.txt)。
- 完整 SHA-256：`a428fb8a2e762af3eb0a6edbbb88e9b42ccfee80fd9b423958bcacf9b9abbfe4`。
- 原始文件 1436 bytes，LF；保留完整上游文本，包括上游免责声明原有措辞。没有自行重写许可证。
- 项目输入：`packaging/windows/licenses/proxy_tools/LICENSE.txt`。
- stage：`licenses/proxy_tools/LICENSE.txt`。
- 最终包：`_internal/licenses/proxy_tools/LICENSE.txt`。
- 来源、commit、摘要固定于 `manifest.json`；来源 URL 进入 `stage/BUILD-MANIFEST.json`。构建和应用运行不下载许可证。

本轮仅联网核验并获取公开的上述官方 LICENSE 及其提交来源，没有向外部发送用户材料或工作区派生内容，没有下载构建依赖。

## 最终构建与实际测试

最终候选：`tmp/rc01-d9269c9f`。前两次中间构建没有作为最终候选复用；最终重新构建的 stage 与当前生产文件逐字节一致。

- Setup：`tmp/rc01-d9269c9f/公文写作助手-Setup.exe`，237016589 bytes。
- Setup SHA-256：`546bf7894e3c0cbcacdb2151c1b43dd7d3e9756e48e0c7225a636cea579a0c38`。
- `INSTALLER-MANIFEST.json` 记录最低版本 **146.0.3856.49**。
- 随包 Runtime 仍为原有、已验签验 hash 的 **154.0.4258.37**。
- Python、PyInstaller、NSIS、npm 依赖均复用本地已存在版本。

以下命令在当前项目根目录实际执行。测试 wrapper 位于 `tmp/desktop-native-shell-fix-01/run_tests.py`，使用现有 build-site 与 pytest，安装 socket audit hook 拒绝非 loopback 测试连接。

```powershell
$taskPython = 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:GONGWEN_TEST_NSIS = Join-Path (Get-Location) 'tmp/release-rc-01/installer-tools/nsis-3.12/makensis.exe'

& $taskPython -B tmp/desktop-native-shell-fix-01/run_tests.py -q -p no:cacheprovider --basetemp=tmp/desktop-native-shell-fix-01/targeted-final backend/tests/test_desktop_native_shell.py packaging/windows/test_desktop_release_contract.py

& $taskPython -B tmp/desktop-native-shell-fix-01/run_tests.py -q -p no:cacheprovider --basetemp=tmp/desktop-native-shell-fix-01/affected backend/tests/test_release_launcher.py backend/tests/test_release_runtime_paths.py backend/tests/test_release_security_origin.py backend/tests/test_release_security_secrets.py backend/tests/test_release_security_closeout_fix.py backend/tests/test_release_frontend_hosting.py backend/tests/test_release_package_scope.py backend/tests/test_release_rc_01_scope.py

& $taskPython -B packaging/windows/build_release.py --site-packages tmp/desktop-native-shell-01/build-site --build-tools tmp/release-rc-01/build-tools --nsis tmp/release-rc-01/installer-tools/nsis-3.12 --webview2-installer tmp/desktop-native-shell-01/downloads/MicrosoftEdgeWebView2RuntimeInstallerX64.exe

& $taskPython -B tmp/desktop-native-shell-fix-01/run_packaging_smoke.py
git diff --check
git diff --cached --name-only
```

| 验证 | 实际结果与证据 |
|---|---|
| Desktop / packaging targeted | **45 passed**，`tmp/desktop-native-shell-fix-01/targeted-final.log` |
| 受影响 backend 回归 | **191 passed**，`affected.log`；仅既有 Starlette/AnyIO deprecated alias 提示 |
| 编译执行 NSIS 分支 | 10 组 HKCU/HKLM 输入：缺失、145.x、146.0.3856.48 选择安装；146.0.3856.49、154.x、155.x 跳过；旧用户版本不遮蔽新版机器 Runtime |
| 许可证负向测试 | stage 与最终包分别缺失、篡改、NOTICE 替换均拒绝；manifest 移除完整许可证条目也拒绝 |
| 真实前端 build | PASS，最终候选 `frontend-build.log`：123 modules；保留现有大 chunk 提示，没有改 UI |
| PyInstaller / NSIS build | PASS，`build-verified.log` 及候选目录 `pyinstaller.log`、`installer-build.log` |
| Packaging smoke | **8 passed**，`packaging-smoke-final.log`：实际库存、生产字节、GUI PE、NSIS 版本参数、来源及 LICENSE 原始字节等 |
| UTF-8 源文件 | 本轮所有变更文本严格解码通过；终端编码显示不作为源码损坏证据 |
| 范围与保护 | 修改前文件哈希逐一核对；非本轮路径不变，窗口函数 AST 不变，原 manifest allowlist / 依赖 / pin 不变 |
| Git | `git diff --check` PASS，staged 为空，HEAD 和分支未改变 |

过程中的两项修正均已保留证据：manifest 末尾空行造成的 diff-check 提示已修正；新增 smoke 测试起初将 Windows code page 的 NSIS 日志误按 UTF-8 读取，失败日志保存在 `packaging-smoke.log`。测试已改为对版本参数进行精确 ASCII 字节断言，未删除或放宽断言，最终复跑 8 项通过。

## 未执行的验收

`CLEAN_MACHINE_WITHOUT_WEBVIEW2 = NOT_PERFORMED`。本机实际检测到 **155.0.4283.18**，没有卸载或篡改机器 Runtime。

NSIS 分支测试编译并执行生产检测/安装决策代码，仅在测试中替换注册表读取和微软安装器调用，不写机器注册表、不执行真实 Runtime 安装。它验证安装选择逻辑，不能声称完成无 Runtime 实机验收。

本轮未重跑上轮已通过的原生窗口完整安装/交互验收，也没有拿上轮 acceptance.json 证明新包通过。Smoke 仅选择 8 项发行包验证，原安装交互测试保持完整。没有运行不相关的生成质量专项或真实模型调用。

最终发布仍需在干净 Windows / VM 验证：无 WebView2 → Setup 自动安装随包 Runtime → 桌面“公文写作助手” → 独立桌面窗口 → 无系统浏览器。

## Git、保护与回滚

本任务明确禁止 Git 写操作，因此没有创建 Git commit/checkpoint；采用本地字节 checkpoint：`tmp/desktop-native-shell-fix-01/before/`、`before-hashes.json`、`before.patch`、`before-status.txt`。开始时的所有非忽略文件均有哈希，所改已有文件留存修改前字节。

没有需要 reseal 的 owner guard，未改保护文件、断言或任何 scope allowlist。仅在既有 desktop license inventory 增加本工单明确授权的完整许可证条目。

回滚范围仅为本轮 8 个已有文件的增量及 3 个新文件。需要回滚时，先与 `boundary-final.json` 核对是否存在后续修改；仅对已确认路径恢复 `before/` 中的字节，另行处理本轮新增的许可证、测试和本记录。禁止恢复整个 BASE、reset/clean、覆盖此前桌面实现或其他用户文件。本轮没有执行回滚。最终工作区保持未暂存状态，待用户再审。
