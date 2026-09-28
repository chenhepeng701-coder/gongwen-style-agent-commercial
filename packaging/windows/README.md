# Windows 0.1.0 原生桌面构建与开发机验收

这是 DESKTOP-NATIVE-SHELL-01 的构建说明，发行合同已按 DESKTOP-NATIVE-SHELL-FIX-01 修正；不是新的项目规则。

## 工具链

- Windows x64、Python 3.12.14、PyInstaller 6.22.3（onedir）。
- NSIS 3.12 Unicode，使用标准安装、快捷方式和卸载机制。
- 前端使用当前 package-lock.json 的依赖，执行 `npm run build`。
- Python 运行依赖及精确版本见 manifest.json。pytest/pip/Node/npm/Vite server 不进入客户端。
- 桌面壳固定 pywebview 6.2.1 → pythonnet 3.1.0 / .NET Framework → EdgeChromium。Windows 需已有 .NET Framework 4.6.2 以上；本轮实际 Windows / Python 3.12.14 / PyInstaller 6.22.3 组合已构建并运行。仅打包 x64 WebView2 SDK Loader、Core、WinForms 与所需 Python 依赖，不包含 Qt、CEF、MSHTML 或其他架构的运行二进制。
- 新增依赖的文件名、版本、官方来源、SHA-256、许可证及真实 dependency graph 见 `licenses/desktop-dependencies.json`。proxy_tools 0.1.0 的 PyPI 元数据标 MIT，源码头标 BSD；这项历史差异记录在 `licenses/proxy_tools-NOTICE.txt`。完整 BSD 许可证使用官方仓库 `jtushman/proxy_tools` 在 commit `ccd35a569b95bec271a59890780c7821756c548a` 的 `LICENSE.txt` 原始字节，固定于 `licenses/proxy_tools/LICENSE.txt`，SHA-256 为 `a428fb8a2e762af3eb0a6edbbb88e9b42ccfee80fd9b423958bcacf9b9abbfe4`。来源与摘要写入 manifest；build input、stage、最终包均校验完整 LICENSE，NOTICE 不能替代。构建和运行均不联网下载许可证。
- NSIS 官方 ZIP SHA-256：`56581f90db321581c5381193d796fffcf2d24b2f8fed2160a6c6a3baa67f2c4f`。
- Python 第三方许可来自 https://docs.python.org/3.12/_sources/license.rst.txt；其他许可从现有锁定依赖中收集。

## 构建命令

开发人员在项目根目录执行（最终用户不需要这些工具或命令）：

```powershell
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B packaging/windows/build_release.py --site-packages tmp/desktop-native-shell-01/build-site --build-tools tmp/release-rc-01/build-tools --nsis tmp/release-rc-01/installer-tools/nsis-3.12 --webview2-installer tmp/desktop-native-shell-01/downloads/MicrosoftEdgeWebView2RuntimeInstallerX64.exe
```

构建器仅枚举 Git tracked 生产输入和 manifest.json 中明确新增路径。每次建立新的短路径 stage，单文件复制、重新构建 React；不会复制工作目录或读取原有无关 untracked 内容。构建所需 node_modules 仅作为暂存前端的依赖 junction，不进入 bundle。

原始源码、第三方许可记录于 `stage/BUILD-MANIFEST.json`；实际包内全部文件和 SHA-256 记录于 `PACKAGE-MANIFEST.json`。编译安装器前校验 PyInstaller 模块来源、每个数据文件的字节、实际产物文件集合。拒绝未知运行依赖、测试依赖、用户目录、私有 Writing Reference 和前端源码。

生产 upstream_suite owner 会校验完整的 pinned vendor manifest，因此包中保留该 manifest 明确要求的上游构建器和 `tests/test_offline_build.py`，均为只读校验资产，不执行上游代码。此项为必需 Vendor 文件例外，不包含本项目测试、测试输出或截图；不能在不改变真源合同的情况下任意删去它。

## 桌面行为

安装默认位于 `%LOCALAPPDATA%\Programs\GongwenWritingAssistant`。程序只读取安装目录；所有应用写入与上传临时文件限于 `%LOCALAPPDATA%\GongwenWritingAssistant`。源码模式继续使用 backend 下的原路径。两种根目录不是同一个概念。

双击仅打开一个标题为“公文写作助手”的 Windows 主窗口，内部嵌入原有 React 页面，没有地址栏、浏览器标签页、控制窗口和 console。默认 1440 × 900，最小 1100 × 700，可最小化、最大化、调整大小和 Alt+Tab。服务仅绑定随机 127.0.0.1 端口；health PASS 后才创建 WebView。重复启动验证实例身份与 HWND 所属 PID 后恢复、前置已有窗口，不创建第二套服务。关闭时提示先保存文稿、等待生成完成；取消则继续运行，确认则停止服务、关闭 listener、删除实例状态、释放锁并退出。浏览器 fallback 不存在。

`desktop_shell.py` 只拥有窗口、renderer、关闭确认和 foreground 行为；`desktop_launcher.py` 继续拥有服务生命周期、单实例锁、Secret 启动迁移和原样保留的 DesktopBoundary。WebView profile、cookies、cache、local storage 存入 Runtime Root 的 `webview` 目录。无新 JS API bridge、Origin exemption 或 User-Agent bypass。

安装器随包携带 Microsoft 官方 **Evergreen Standalone Installer x64**，并在安装页面显示完整 Microsoft Runtime 条款；已有合格 Evergreen Runtime 时跳过，否则从包内提取并执行 `/silent /install`。不使用在线 Bootstrapper，不在首次启动时下载。安装后仍未检测到 Runtime 则停止并报错。应用安装器保持 `RequestExecutionLevel user`；微软安装器自身可能按现有更新器策略选择 per-machine。

pywebview 6.2.1 的最低 Runtime 合同为 **146.0.3856.49**，唯一值位于 `backend/desktop_shell.py` 的 `WEBVIEW2_MIN_RUNTIME`。构建器从 stage 中读取该常量并传入 NSIS 的 `/DWEBVIEW2_MIN_RUNTIME`，未传值时 NSIS 编译失败；安装器不另设默认版本。构建还校验随包 Runtime 不低于该门槛，并在 `INSTALLER-MANIFEST.json` 记录最低版本。145.x 和 146.0.3856.48 不合格；146.0.3856.49、154.x、155.x 合格。缺失或旧版本选择随包安装，合格版本跳过。

离线包所含 Runtime 为 **154.0.4258.37**；外层安装器文件版本 **1.3.271.7** 不等于 Runtime 版本。SHA-256、官方页面、解析后 URL、Microsoft 签名与分发证据均记录在 manifest.json，构建器验证大小、摘要和 Authenticode 后才使用。SDK 许可和 Evergreen 许可随应用交付。实际测试机器已有更新的 Runtime **155.0.4283.18**，验收使用该版本；未卸载机器现有 Runtime，也未声称完成无 Runtime 的干净系统测试。

设置入口：账户中心 → 模型配置 → Writing Reference。模型配置复用原 API；Reference 上传复用原 schema，经校验后按内容摘要命名保存，config.yaml 只保存资源文件名。原 Writer 继续读取同一资源 owner。

安装版在 Runtime Root 初始化、单实例锁取得和环境 Secret 清理之后，先调用配置 owner 的 `migrate_legacy_api_keys()`，成功后才导入应用并建立 HTTP listener。该入口只读取磁盘配置、迁移旧明文、验证 DPAPI 并原子替换配置，不加载 Settings 或启动 Provider、Writer、Embedding、RAG。任一迁移步骤失败则启动失败，不进入 HTTP ready，原 config.yaml 字节保留。无旧明文时不读取 DPAPI、不重写配置；合法 `${ENV_NAME}` 保留原值、不迁移，安装版仍不解析环境 Secret。

源码模式无关设置保存（含 Writing Reference）从磁盘原配置保留合法 API Key placeholder，不落盘环境变量解析值。显式非空新 Key 仍写入 Secure Store 并替换对应 placeholder；空 Key 仍保留已有凭据。

卸载默认只删除程序文件、快捷方式和注册信息，保留用户 Runtime Root。

## 验收与证据

当前桌面验收使用 `native_shell_acceptance.py`，在新的 tmp 安装目录和独立 Runtime Root 中验证真实 EXE。它备份并在卸载后恢复既有快捷方式与注册信息。窗口操作由 Computer Use 执行；脚本记录正常用户 token、进程树、窗口状态、监听端口、前后安装文件哈希、Origin 响应码及卸载数据哈希。快捷方式激活使用 Windows ShellExecuteEx，与双击相同的 `.lnk` 解析启动路径；这是自动化激活，不是鼠标双击录像。

```powershell
$releaseRun = Get-Content tmp/release-rc-01/latest-package.txt
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B packaging/windows/test_desktop_packaging.py --build $releaseRun --site-packages tmp/desktop-native-shell-01/build-site --webview2-installer tmp/desktop-native-shell-01/downloads/MicrosoftEdgeWebView2RuntimeInstallerX64.exe --acceptance tmp/dns01-d55f9ede
```

历史 `dev_smoke.py`、`startup_migration_acceptance.py` 和 `test_packaging_manifest.py` 属于旧控制窗口验收链，仍保留以便追溯，但不能直接用于当前原生窗口，不能拿旧产物报告证明新包通过。当前 packaged Secret migration 回归由 `backend/tests/test_release_launcher.py` 与 `test_release_security_closeout_fix.py` 等覆盖；桌面层新增 `test_desktop_native_shell.py`。未修改公共 runtime owner，不触发后端全量离线测试；运行了受影响的 launcher/runtime/security/API hosting/package scope 测试，以及前端全量测试与 build。

FIX-01 的新增发行测试为 `test_desktop_release_contract.py`。运行 pytest 前，将 `GONGWEN_TEST_NSIS` 指向已有 `makensis.exe`。测试编译并执行生产 NSIS 的检测和安装选择逻辑，只替换注册表读取及 Runtime 安装副作用，不改机器注册表、不安装测试 Runtime。结果不能代替 `CLEAN_MACHINE_WITHOUT_WEBVIEW2` 实机 Gate；最终仍需在干净 Windows / VM 验证 Setup 自动安装随包 Runtime 后，桌面快捷方式打开独立窗口且没有系统浏览器。FIX-01 的命令、候选及验证边界见 `docs/DESKTOP-NATIVE-SHELL-FIX-01.md`。

本轮候选是指定 BASE 上的未提交工作区构建。遵照本工单，不暂存、不提交、不推送、不做 External Review。本轮只验证桌面外壳，不调用 Writer / Embedding，也不评价生成文章质量。通用现有 EXE 图标继续复用，正式品牌图标后续单独处理。
