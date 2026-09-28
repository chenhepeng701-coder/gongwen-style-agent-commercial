# Windows 0.1.0 构建与开发机验收

这是 RELEASE-RC-01 的构建说明，不是新的项目规则。

## 工具链

- Windows x64、Python 3.12.14、PyInstaller 6.22.3（onedir）。
- NSIS 3.12 Unicode，使用标准安装、快捷方式和卸载机制。
- 前端使用当前 package-lock.json 的依赖，执行 `npm run build`。
- Python 运行依赖及精确版本见 manifest.json。pytest/pip/Node/npm/Vite server 不进入客户端。
- NSIS 官方 ZIP SHA-256：`56581f90db321581c5381193d796fffcf2d24b2f8fed2160a6c6a3baa67f2c4f`。
- Python 第三方许可来自 https://docs.python.org/3.12/_sources/license.rst.txt；其他许可从现有锁定依赖中收集。

## 构建命令

开发人员在项目根目录执行（最终用户不需要这些工具或命令）：

```powershell
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B packaging/windows/build_release.py --site-packages backend/.venv/Lib/site-packages --build-tools tmp/release-rc-01/build-tools --nsis tmp/release-rc-01/installer-tools/nsis-3.12
```

构建器仅枚举 Git tracked 生产输入和 manifest.json 中明确新增路径。每次建立新的短路径 stage，单文件复制、重新构建 React；不会复制工作目录或读取原有无关 untracked 内容。构建所需 node_modules 仅作为暂存前端的依赖 junction，不进入 bundle。

原始源码、第三方许可记录于 `stage/BUILD-MANIFEST.json`；实际包内全部文件和 SHA-256 记录于 `PACKAGE-MANIFEST.json`。编译安装器前校验 PyInstaller 模块来源、每个数据文件的字节、实际产物文件集合。拒绝未知运行依赖、测试依赖、用户目录、私有 Writing Reference 和前端源码。

生产 upstream_suite owner 会校验完整的 pinned vendor manifest，因此包中保留该 manifest 明确要求的上游构建器和 `tests/test_offline_build.py`，均为只读校验资产，不执行上游代码。此项为必需 Vendor 文件例外，不包含本项目测试、测试输出或截图；不能在不改变真源合同的情况下任意删去它。

## 桌面行为

安装默认位于 `%LOCALAPPDATA%\Programs\GongwenWritingAssistant`。程序只读取安装目录；所有应用写入与上传临时文件限于 `%LOCALAPPDATA%\GongwenWritingAssistant`。源码模式继续使用 backend 下的原路径。两种根目录不是同一个概念。

双击启动系统浏览器和一个“打开界面 / 退出软件”原生控制窗口。服务与控制窗口在同一进程，单实例文件锁由 Windows 自动释放。服务仅绑定 127.0.0.1；重复启动验证已有实例健康状态后打开其页面。退出需确认，随后停止服务。浏览器标签页本身不是后台服务的进程 owner。

设置入口：账户中心 → 模型配置 → Writing Reference。模型配置复用原 API；Reference 上传复用原 schema，经校验后按内容摘要命名保存，config.yaml 只保存资源文件名。原 Writer 继续读取同一资源 owner。

安装版在 Runtime Root 初始化、单实例锁取得和环境 Secret 清理之后，先调用配置 owner 的 `migrate_legacy_api_keys()`，成功后才导入应用并建立 HTTP listener。该入口只读取磁盘配置、迁移旧明文、验证 DPAPI 并原子替换配置，不加载 Settings 或启动 Provider、Writer、Embedding、RAG。任一迁移步骤失败则启动失败，不进入 HTTP ready，原 config.yaml 字节保留。无旧明文时不读取 DPAPI、不重写配置；合法 `${ENV_NAME}` 保留原值、不迁移，安装版仍不解析环境 Secret。

源码模式无关设置保存（含 Writing Reference）从磁盘原配置保留合法 API Key placeholder，不落盘环境变量解析值。显式非空新 Key 仍写入 Secure Store 并替换对应 placeholder；空 Key 仍保留已有凭据。

卸载默认只删除程序文件、快捷方式和注册信息，保留用户 Runtime Root。

## 验收和发布边界

`dev_smoke.py` 是不入包的开发机验收脚本：安装到新的 tmp 子目录、使用独立 Runtime Root、启动 127.0.0.1 合成服务，验证配置、资料 READY、Word 导出、退出、重启和卸载。它拒绝覆盖已有安装。脚本中的合成响应不用于评价生成质量。

```powershell
$releaseRun = Get-Content tmp/release-rc-01/latest-package.txt
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -c "import sys,runpy; sys.path.insert(0,r'D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'); sys.argv=['dev_smoke.py','--build',sys.argv[1]]; runpy.run_path('packaging/windows/dev_smoke.py',run_name='__main__')" $releaseRun
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -c "import sys,runpy; sys.path.insert(0,r'D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'); sys.path.insert(0,'packaging/windows'); sys.argv=['startup_migration_acceptance.py','--build',sys.argv[1]]; runpy.run_path('packaging/windows/startup_migration_acceptance.py',run_name='__main__')" $releaseRun
& 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B packaging/windows/test_packaging_manifest.py
```

`startup_migration_acceptance.py` 在独立 Runtime Root 运行当前候选的真实 EXE，首次 HTTP ready 状态发布后、任何 Settings 请求前检查迁移与 DPAPI 解密；再检查重启幂等、无明文 no-op、placeholder 和环境隔离。它通过合成 Secret Store 写入障碍及 Windows 配置替换锁验证失败不监听 HTTP、原配置不丢失。仅使用 `TEST_SECRET_DO_NOT_USE_123`，不请求 Provider / Embedding / 外网；结果写入候选包的 `startup-migration-acceptance.json`。

当前安装包只供 Phase 4 外审。外审 PASS 且 BLOCKING_FINDINGS=0 后才可提交、推送和建 PR；不得自动合并。最终候选包必须在获准合并后的 main 上重新构建，再在真正干净 Windows 上执行真实模型 E2E。
