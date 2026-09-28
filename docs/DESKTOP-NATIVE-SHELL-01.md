# DESKTOP-NATIVE-SHELL-01 验收记录

记录日期：2026-09-28。此文件是本轮证据，不是项目规则。

## 结果与候选

Windows 原生桌面壳、真实 PyInstaller/NSIS 构建以及开发机隔离安装验收通过。只替换桌面外壳，React 业务 UI、API、生成链和安全 owner 未修改。

- BASE / 当前 HEAD：`7d2df31b3660e656eadf483478785588e5ec53aa`。
- 分支：`codex/desktop-native-shell-01`。
- 工单明确覆盖默认提交流程：未暂存、未提交、未 push、未建 PR、未 External Review。回滚边界为上述 BASE；本轮工作区修改保留供检查。
- 候选目录：`tmp/rc01-d3007f58`。
- 安装包：`tmp/rc01-d3007f58/公文写作助手-Setup.exe`，237015385 bytes。
- 安装包 SHA-256：`125ae40b150cf1f4ca8529f8818a8175cbf1de7ff944800486cf697d5f7139b2`。
- 精确输入、输出、安装包摘要：候选目录的 `stage/BUILD-MANIFEST.json`、`PACKAGE-MANIFEST.json`、`INSTALLER-MANIFEST.json`。
- 真实隔离安装证据：`tmp/dns01-d55f9ede/acceptance.json`。安装已卸载；测试 Runtime Root 保留，测试前快捷方式已恢复。

## Owner、边界与取舍

原 launcher 用 `webbrowser.open` 展示主 UI，并创建另一个控制窗口；这正是本工单要消除的用户可见行为。现有 FastAPI 和 React 不需要业务改造。最小改法是在 launcher 的 health ready 之后调用单一 Windows shell owner。launcher 创建服务、锁和实例状态；shell 消费 ready URL，创建窗口并发布 HWND；第二进程先验证服务 identity，再验证 HWND 的 PID 并恢复前台。

主要回归风险是窗口早于服务、退出留下服务、窗口重复、误选旧 renderer、数据写入安装目录及 Origin 被豁免。对应测试分别覆盖真实 loopback 服务生命周期、重复启动、renderer 参数和 fail closed、storage path、原 DesktopBoundary AST 与真实 EXE 的请求拒绝结果。业务请求继续走原 HTTP 合同，无 JS bridge / WebView / UA / Origin 豁免。没有改变文章质量或事实安全语义，没有恢复旧生成路线。

生产修改严格为 7 个文件：

1. `backend/desktop_launcher.py`
2. `backend/desktop_shell.py`
3. `packaging/windows/manifest.json`
4. `packaging/windows/app.spec`
5. `packaging/windows/installer.nsi`
6. `packaging/windows/build_release.py`
7. `packaging/windows/verify_package.py`

测试、文档、许可支持为 10 个文件：两个 backend tests、`native_shell_acceptance.py`、`test_desktop_packaging.py`、Windows README、本记录、4 个 desktop 许可/依赖清单文件。未更改任何保护 guard 或其 allowlist/hash；无需 reseal。manifest 中加入新 shell 的打包输入并不扩大业务 owner 保护名单。

进入任务前已有的 P1/P2 patch、zip、manual_writer 结果、proxy_check 及 writer 测试文件未改动、未暂存、未入包。现有 icon 继续复用，正式品牌图标不在本轮设计。

## 依赖与离线 Runtime 分发

Python 3.12.14、PyInstaller 6.22.3、NSIS 3.12；pywebview 固定 6.2.1。Windows 路径新增 pythonnet 3.1.0、clr_loader 0.3.1、cffi 2.1.1、pycparser 3.0、bottle 0.13.4、proxy_tools 0.1.0，并沿用 typing_extensions 4.16.0。每个下载文件的版本、名称、官方 URL、摘要、license 和 requires_dist 都在 `packaging/windows/licenses/desktop-dependencies.json`。

实际调用 `pythonnet.load('netfx')`、`initialize('edgechromium')`，并在冻结 EXE 中成功创建 WebView2。实际打包仅包含该 renderer 的 Python / SDK x64 二进制；没有 Qt、CEF、PySide、MSHTML、外部浏览器 fallback、开发工具或 pytest。SDK 的目录探测需要空架构目录，使用随包 SDK LICENSE 占位，未加入那些架构的 DLL。

Microsoft Runtime：官方 Evergreen Standalone Installer x64，运行内容版本 154.0.4258.37，外层安装器 FileVersion 1.3.271.7；版本通过官方安装包的压缩 payload 核对，不把外层版本误当 Runtime 版本。

- 官方下载页面：<https://developer.microsoft.com/en-us/microsoft-edge/webview2>
- 页面 x64 下载入口：<https://go.microsoft.com/fwlink/?linkid=2124701>
- 官方安装命令与部署依据：<https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution#offline-deployment>
- Runtime 条款：<https://developer.microsoft.com/microsoft-edge/api/eula/webview2?locale=en-us>
- SDK 1.0.3856.49 条款：<https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3856.49/License>
- 官方下载 SHA-256：`771042db15cb5c463bac51a8408e70183d7130e8ac946709384c2223da582c1b`；有效 Microsoft Authenticode 签名，签名主体和证书 thumbprint 已钉在 manifest。

Runtime 条款第 2 节提供 object-code 复制与分发许可，并要求产品提供主要功能、保留必要通知并保护 Microsoft 条款。完整条款随包、在 NSIS 额外许可页面展示；不修改微软 installer。应用以本地资料库和公文生成作为主要功能。安装器含完整 offline standalone，在已有合格 Runtime 时跳过，缺少时 `/silent /install`，随后复检；未安装成功则中止，不下载、不 fallback。

proxy_tools 官方 PyPI sdist/wheel 元数据声明 MIT，源码头声明 BSD 且引用缺失的 LICENSE。本轮保留两个声明和原作者署名，明确记录发行包遗漏；没有声称获得不存在的上游 LICENSE 全文。证据在 `licenses/proxy_tools-NOTICE.txt`。

用户追加授权后，仅联网获取上述官方构建依赖及官方许可证/分发证据。不能把本轮全部外网调用报告成 0；真实 Writer、真实 Embedding、业务外网调用均为 0。构建/验收没有触发真实生成。

## 实际命令和测试

命令均在当前项目运行。Python 使用 Codex bundled Python 的绝对路径（见 Windows README），测试 PYTHONPATH 指向隔离 build-site 和现有测试依赖。pytest 入口安装 socket.connect audit hook，拒绝非 loopback 测试连接。

最终 backend 测试相当于：

```powershell
python -B -m pytest -q -p no:cacheprovider --basetemp=../tmp/desktop-native-shell-01/pytest-final tests/test_desktop_native_shell.py tests/test_release_launcher.py tests/test_release_runtime_paths.py tests/test_release_security_origin.py tests/test_release_security_secrets.py tests/test_release_security_closeout_fix.py tests/test_release_frontend_hosting.py tests/test_release_model_configuration.py tests/test_release_writing_reference.py tests/test_release_package_scope.py tests/test_release_rc_01_scope.py
```

实际 Python wrapper 与外网阻断在执行记录中，日志 `tmp/desktop-native-shell-01/backend-tests-final.log`：**218 passed**，330.40s；唯一 warning 为既有 Starlette/AnyIO deprecated alias。包含 packaged Secret migration 的顺序、失败拒绝、环境 Secret 隔离、placeholder 与 DPAPI 回归。公共 runtime owner 未修改，未运行 backend full offline suite。

前端全量：在 frontend 中 `node --test @(rg --files tests -g '*.test.mjs')`，**132 passed**，日志 `tmp/desktop-native-shell-01/frontend-tests.log`。真实构建每次对新 stage 执行 `npm.cmd run build`；最终 `tmp/rc01-d3007f58/frontend-build.log` 显示 123 modules、build PASS。保留既有大于 500 kB chunk 提示，未因此修改 UI。

真实构建命令：

```powershell
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B packaging/windows/build_release.py --site-packages tmp/desktop-native-shell-01/build-site --build-tools tmp/release-rc-01/build-tools --nsis tmp/release-rc-01/installer-tools/nsis-3.12 --webview2-installer tmp/desktop-native-shell-01/downloads/MicrosoftEdgeWebView2RuntimeInstallerX64.exe
```

最终日志 `tmp/desktop-native-shell-01/build-05.log` 及候选目录中的 pyinstaller.log、installer-build.log。期间实际发现并修复：旧 PowerShell 签名模块不可用（优先可用 pwsh）；optional cryptography 被自动收集（排除非本路径依赖）；无 console 的 frozen stdout/stderr 导致 Bottle import 打开 NUL，被原 write guard 正确拦截（改用不写设备、不累计内存的文本 sink）。没有放宽 guard。先前失败候选未当作交付通过。

Packaging smoke 命令：

```powershell
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B packaging/windows/test_desktop_packaging.py --build tmp/rc01-d3007f58 --site-packages tmp/desktop-native-shell-01/build-site --webview2-installer tmp/desktop-native-shell-01/downloads/MicrosoftEdgeWebView2RuntimeInstallerX64.exe --acceptance tmp/dns01-d55f9ede
```

`tmp/desktop-native-shell-01/packaging-smoke.log`：**7 passed**。验证实际包清单/来源、7 个生产输入与工作区逐字节相同、微软签名/摘要/许可、EXE 和 Setup 的 PE Windows GUI subsystem、NSIS user-level/offline/no-download 分支、该候选安装验收证据。

## 安装版原生窗口验收

测试在现有 Windows 的非管理员用户 token 下，安装到新的仓库 tmp 隔离目录，应用使用新的 LOCALAPPDATA。未触碰用户现有 Runtime Root。`native_shell_acceptance.py install/upgrade/launch/snapshot/audit/closed/uninstall` 记录机器证据，Computer Use 操作真实窗口。

桌面 `.lnk` 已创建并通过 ShellExecuteEx 激活，走与鼠标双击相同的 Windows shortcut 启动路径。为隔离 LOCALAPPDATA，测试使用自动化激活；没有把它写成完成鼠标双击录像。

| 验收项 | 实际结果 |
|---|---|
| 安装、桌面/开始菜单快捷方式 | PASS，非管理员 |
| 主窗口 | 单一“公文写作助手”，原有 React 完整显示，无地址栏/标签页/console/旧控制窗口 |
| 最小化后再次启动 | PID 31280、端口 48964、HWND 16912380 不变；原窗口恢复并获得焦点；第二进程退出 0 |
| 最大化、还原、调整尺寸 | PASS，Win32 IsZoomed；原始宽度 1440，实际调整为 1429 |
| Alt+Tab | 正常切出，前台状态 false，随后恢复应用 |
| 页面 | 写文章、账户中心、资料库、我的文稿均使用现有 UI |
| WebView 自然 Origin | UI 保存向量配置成功，配置只写测试 Runtime Root，未点测试连接 |
| 边界 | POST/PUT/PATCH/DELETE：无 Origin 403，exact Origin 对不存在测试 route 返回 404，localhost Origin 403 |
| 关闭 | 原文确认；取消继续运行；确认后状态删除、主进程和全部已记录 WebView2 子进程退出、端口拒绝连接 |
| 重新打开 | PASS，新 PID 6640 / 端口 21123 / identity；再次正常关闭 |
| WebView profile/cache | Runtime Root/webview；安装目录哈希始终不变 |
| 卸载 | PASS，程序删除，整个 Runtime Root 哈希集合不变；既有快捷方式恢复 |

窗口证据：`tmp/dns01-d55f9ede/native-main.jpg`、`native-origin-save.jpg`、`native-close-confirm.jpg`。窗口状态和服务监听在 acceptance.json。

浏览器启动前后快照都保留。机器已有 Chrome 和 Edge Beta，其他用户活动导致部分普通浏览器子进程 PID 变化，因此没有伪称“全机器浏览器进程列表完全不变”。结合真实窗口列表（已有浏览器窗口 ID 不变）和本应用进程树，未发现本应用创建普通 chrome.exe / msedge.exe 窗口；本应用的子进程均为 msedgewebview2.exe。第二次启动同样检查。WebView2 子进程不等于系统浏览器 UI。

## 验证边界与回滚

本机 Runtime 为 155.0.4283.18，安装时跳过随包 Runtime，未在无 Runtime 的全新 Windows 上实际执行微软安装分支；该分支经过 NSIS 构建与合同检查，官方离线分发包已验签、验 hash、验 payload 和许可。未为测试卸载现有系统 Runtime。Windows 需已有 .NET Framework 4.6.2 以上，主应用无在线依赖安装路径。

历史 dev_smoke/startup_migration_acceptance 使用已删除控制窗口的自动关闭协议，未复用其历史报告作为本轮 PASS。当前业务保护/迁移回归由上述 218 tests 提供，桌面真实窗口由本轮安装证据提供。未执行真实模型质量 E2E，不宣称生成质量变化。

回滚时仅针对上列 7 个生产文件及本轮 10 个支持文件，与 BASE 对照恢复/移除；先保存本轮 diff，并核对用户后续更改。不使用 reset --hard 或 git clean，不操作现存用户 patch/zip/结果和 Runtime Root。本轮未执行回滚。最终 staged 范围为空，HEAD 保持 BASE。
