# RELEASE-RC-01 / Phase 4 External Review handoff

本文由执行方整理，供既定外部审计流程使用，不是 External Review 结论。

## 待审对象

- 分支：`codex/release-rc-01`。
- BASE / HEAD / main：`e50981945773ac7263a5f90fb7f69569ff584098`。
- Required BASE：`e50981945773ac7263a5f90fb7f69569ff584098`。
- 版本：`0.1.0`；实现仍未提交，暂存区为空。
- Security Closeout 开发候选安装包：`tmp/rc01-bd0a07db/公文写作助手-Setup.exe`。
- SHA-256：`3fa2d3f982685a3bac8375581dbb506ca402ef3bc17973b5f533e10ea0864e95`。
- 构建时间：`2026-09-28 02:15:04 UTC`；大小：22,233,996 bytes。
- `source_state = uncommitted_candidate`，不是合并 main 后的最终 Release。

## 审查范围与证据入口

| 范围 | 实现 / 证据 |
| --- | --- |
| 顶层导航保护 | 已验收 `frontend/src/App.jsx`、`pages/ArticleGenPage.jsx`；既有导航测试继续保留。Phase 3 及本次文档修复未再改变 Phase 1 已验收字节；历史 pin 与 re-anchor 范围见下文 |
| Runtime Root | `backend/app/utils/runtime_paths.py`、`services/app_settings.py`、`app/config.py`；source mode 保持旧路径，frozen mode 使用用户 Runtime Root；安装进程另有写入边界和上传临时目录 |
| 模型配置 | 原 `routers/settings.py` / ModelConfigPage；原有 API 保存和测试连接，响应补充 Key 脱敏，不读取安装目录 .env；当前候选已接入 DPAPI CurrentUser，详见 Security Closeout 记录 |
| Writing Reference | `services/writing_reference_settings.py`、settings 上传接口、`WritingReferenceSettings.jsx`；原 schema/校验仅提取为共享函数，AST 对比证明其逻辑未变；相对资源文件名持久化，不返回绝对路径 |
| 静态前端 | `utils/frontend_hosting.py`、`app/main.py`；同源 /api、正式 assets、SPA fallback、未知 API 404；覆盖 Windows 路径分隔符 |
| Launcher / 单实例 | `backend/desktop_launcher.py`；原生 Win32 控制窗口，OS 文件锁，同进程 uvicorn，健康检查后打开系统浏览器，正常退出停止服务；只监听 127.0.0.1，并检查 Host/Origin |
| PyInstaller | `packaging/windows/app.spec`、`manifest.json`、`build_release.py`、`verify_package.py`；onedir、独立 fresh stage、逐文件输入、精确依赖版本、许可证、实际输出清单 |
| Installer | `packaging/windows/installer.nsi`；NSIS 3.12、用户级安装、中文入口、桌面/开始菜单、0.1.0 卸载注册；默认保留 Runtime Root |
| Secret / 数据 | 原有 11 个无关 untracked 未读写；.env、用户配置/资料/Reference、日志、测试产物、frontend 源码、node_modules 未入包；安装后写入不落到程序目录 |

上游 Vendor 的完整 pinned manifest 会校验 `tests/test_offline_build.py` 和构建器源码，故将这些精确锁定文件作为**必需的只读 Vendor 校验资产**保留；不执行它们，也不包含本项目测试或测试产物。外审请核对此处与现有 owner 的完整性合同；本轮未删除或改写该合同。

## Phase 3 历史验证（Security Closeout 前）

- 受影响后端：Runtime/config/Reference 54 passed，静态托管 4 passed，Launcher 5 passed；后续范围锁及相关回归 134 passed、补充 22 passed。
- 最终 backend full offline：**1671 passed、0 failed、2 skipped、1 warning**。两个 skip 是未授权的私有真实 Run 回放；warning 是既有 Starlette/AnyIO 弃用提示。
- 离线 runner 计数：external_network / real_model / real_embedding / forbidden_data_access 全部 0。
- frontend full：**131 passed、0 failed**。
- frontend production build：PASS；候选安装包从 fresh stage 重新生成 `dist-commercial`。既有 >500KB bundle warning 保留，不属本工单修复范围。
- Packaging manifest smoke：4 passed；实际文件 470 项，逐项 SHA-256 和来源已核对。
- 开发机安装版：配置保存/重载、本机 fake 连接测试、Reference 有效/无效上传、资料至 READY、DOCX 输出、单实例、仅 loopback、正常退出、重启持久化、卸载保留运行数据，均通过已执行验收；最终候选复验结果见 `dev-acceptance.json`。
- 正式浏览器页面：模型两类 Key 显示已配置，Reference 显示已配置/更换文件；无浏览器脚本错误；21 个未验收文种仍 disabled。
- `git diff --check`：PASS；边界检查：PASS，受保护生成/RAG/Writer/Fact Safety/FLOW3/FLOW4/Genre/Word owner 无差异。

日志与清单（均保留在 ignored 目录，不进入提交或安装包）：

```text
tmp/release-rc-01/phase3-backend-full-tests-final.log
tmp/release-rc-01/phase3-frontend-full-tests.log
tmp/release-rc-01/phase3-affected-scope-tests.log
tmp/release-rc-01/phase3-package-boundary-tests.log
tmp/release-rc-01/phase3-packaging-smoke-tests.log
tmp/release-rc-01/phase3-dev-acceptance-final.log
tmp/release-rc-01/phase3-browser-acceptance.log
tmp/release-rc-01/phase3-boundary-final.json
tmp/rc01-89d578d6/frontend-build.log
tmp/rc01-89d578d6/pyinstaller.log
tmp/rc01-89d578d6/installer-build.log
tmp/rc01-89d578d6/stage/BUILD-MANIFEST.json
tmp/rc01-89d578d6/PACKAGE-MANIFEST.json
tmp/rc01-89d578d6/dev-acceptance.json
```

完整可复制构建与开发机验收命令见 `packaging/windows/README.md`。后端仍使用原 `tests/run_multi_genre_offline.py`，没有跳过、删除或弱化 guard；新增 Phase 3 精确锁与既有 Phase 2 锁分开。首次 Phase 3 全量记录了 21 个 scope / 锁相关连带失败，保留原失败日志；这一失败清单受首个 mismatch 短路限制，不能用于证明其他前端 pin 均正常。修复后的完整套件结果如上。

## Guard / Phase 2 / Phase 3 / Historical Lock 溯源补充（B1）

Required BASE 为 `e50981945773ac7263a5f90fb7f69569ff584098`。该 BASE 上的 `backend/tests/genre_ui_scene_scope.py` 对 `frontend/src/pages/ArticleGenPage.jsx` 存在历史 stale pin；因此，BASE 自身以该 guard 的历史 pin 校验 ArticleGenPage.jsx 时，本来就无法通过。不得将 RC-01 的 pin re-anchor 描述为“BASE 原本全部为绿”。

历史字节来源为以下已合并 PR：

- PR #21：`feat: improve generation waiting UX`。
- PR #22：`feat: unify generation waiting experience`。

两个 PR 均已独立完成 Review / External Review 并合并，但合并时未同步更新上述 scope pin。这里记录的是已经验收并进入 main 的历史代码与旧锁定值之间的偏差，不是未知生产代码的新增批准。

RC-01 中 App.jsx / ArticleGenPage.jsx 的 pin re-anchor 包含两类溯源：一部分属于 RC-01 当前已验收的 Phase 1 导航与请求保护内容；ArticleGenPage.jsx 的 re-anchor 同时补齐了 PR #21 / #22 已验收、已合并但未同步到 scope pin 的历史字节。Phase 2 Repair 对这些已确认内容重新锚定，是 traceability repair（溯源修复），不是重新批准未知代码，也不是整体推进 baseline。

Guard 会在首个 mismatch 处短路。首次 Phase 3 的失败现场中，backend owner mismatch 先触发，阻止了 ArticleGenPage stale pin 被同时暴露。因此，当时的 21 个失败不能证明所有其他前端 pin 均正常；失败清单也不能作为 BASE 无历史 stale pin 的证据。历史溯源、当前验收范围和修复后的完整套件结果必须分别理解。

```text
GUARDS_REMOVED_OR_WEAKENED = NO
```

Guard 机制没有删除、绕过或降低强度；前述修复只是把已验收内容重新锚定到真实基线。本次 RELEASE-RC-01-TRACEABILITY-FIX-01 仅补充本文溯源说明，不重新计算或再次修改任何 pin，不修改代码、测试、guard、打包文件或配置，也不处理 External Review 的 N1–N4。B1 是否关闭由外部复核确认。

## Security Closeout / AUTHORIZATION-PATCH-02（当前实现记录）

Protection Preflight 已完成：本轮触及的生产 owner 为 `backend/app/config.py`、新增 `backend/app/utils/secret_store.py`、`backend/app/routers/settings.py`、`backend/desktop_launcher.py`、`frontend/src/components/WritingReferenceSettings.jsx`。config、settings router、Reference UI 及其既有测试证据的精确 pin 均位于 `backend/tests/release_rc_01_package_scope.py`；Launcher 无既有 pin，新增 Secret Store 无旧 pin。Writing Reference schema 的 AST lock、Phase 1/2 历史保护及其他 scope guard 均保持原样。Packaging manifest 仅收录明确授权新增的 Secret Store owner，不扩展任何 guard allowlist。

Secret Store 使用 Windows DPAPI CurrentUser；不使用管理员权限、固定应用密钥、Base64/XOR 或额外 Python 加密依赖。密文写入 Runtime Root 的 `config/secrets/*.dpapi`，identity 分离 Provider 和 Embedding。普通配置只保存非敏感字段；空 Key 保存继续保留已配置 Key。旧明文先写入 Secure Store、回读验证，再原子重写 YAML；失败保留旧配置，已有不同密文与旧明文冲突时明确失败，不推测覆盖。源码模式保留环境变量 runtime override，load 或非密钥设置保存不自动持久化环境 Key；安装版继续剥离环境 Key，config 层也拒绝 packaged 环境注入。

Reference UI 仅补披露，schema、validation、Reference hash、Writer payload 和事实安全行为不变。披露明确本机保存，以及生成时相关内容进入当前模型服务商。

调用方分类：浏览器通过既有同源 axios `/api` 调用；Launcher / health probe 仅 GET；installer 无 HTTP 写请求；内部业务调用直接调用 Python owner，不经过 DesktopBoundary；现有 backend TestClient 为进程内路由测试；唯一安装版 stateful automation 为 `packaging/windows/dev_smoke.py`，已按每次实际目标 URL 构造精确 Origin。未发现新增产品调用场景。Provider/Embedding 的上游 HTTP 调用不属于本机 API caller，本轮没有对它们改动。POST/PUT/PATCH/DELETE 必须具有精确 Origin，GET/HEAD 可省略；错误、重复或畸形 Host/Origin 拒绝，不增加豁免或 wildcard CORS。

### N2：输出目录 metadata

```text
OUTPUT_DIR_MTIME_CHANGE = EXPECTED_SIDE_EFFECT
OUTPUT_CONTENT_MUTATION = NO
SECURITY_RISK = NO
```

现有可写性探针通过创建临时文件并立即删除来验证可写性，可能更新父目录 metadata/mtime，但不留下持久内容。此项归类不代表删除、覆盖或修改用户稿件；本轮未修改或弱化 writability probe。

### 独立 reseal 记录

`HISTORICAL_REANCHOR = PR21_PR22_TRACEABILITY_FIX`，沿用上文 B1 溯源，不再改历史 pin。`CURRENT_SECURITY_RESEAL` / `SECURITY_CLOSEOUT_RESEALS` 仅记录本安全任务最终字节，旧、新哈希及原因已列于下表；guard 判断逻辑、保护路径集合及 assertion 不变。

| path | old_sha256 | new_sha256 | reseal_reason |
| --- | --- | --- | --- |
| `backend/app/config.py` | `8358e314c33756eb25a6aa22a8d46a8c591f71847d24a66e43f26a7d2af76842` | `8da951cd255142b4e51f37fcac6b50ea10a24bd18df5c02e19d0efa3725c780d` | `SECURE_SECRET_STORAGE` |
| `backend/app/routers/settings.py` | `4e51e54b02487ee209ce29ca79c048ded1492dfc0c554c6224778576e2272595` | `4ec8c2ecdd0077a22a66bf5dc77cd39d06cac663679871eaa10f8642c7924120` | `SECRET_STORE_ERROR_AND_RESPONSE_REDACTION` |
| `frontend/src/components/WritingReferenceSettings.jsx` | `c96fccca6c732b0b5b9a52b920c5b440782f9c9e86e502fab8075d06d518fba1` | `631fe54fabd248993fa2ae8fc5253237c35d43656136a10e0d40d6c0d406a85a` | `WRITING_REFERENCE_PRIVACY_DISCLOSURE` |
| `frontend/tests/pages/WritingReferenceSettings.test.mjs` | `95df947b6d90ef3e1b387b47d14c49b94e0a617f3b36fc878d8286fcea7f3e07` | `72a804c32e33ac268367917cf09a4eeb04cad87d4bb3b1e6abb4577a63081596` | `DISCLOSURE_SEMANTIC_TEST_EVIDENCE` |

上述哈希沿用既有 guard 的 CRLF → LF 规范化规则。4 个 reseal 文件共 27,061 个字节位置逐一做单字节 XOR 1 篡改，均由原 guard 拒绝；只在内存中构造变体，没有修改真实文件。guard AST 对比除这些精确 hash 值外完全一致。

```text
GUARDS_REMOVED = NO
GUARDS_WEAKENED = NO
ALLOWLIST_EXPANDED = NO (guard path sets)
HISTORICAL_REANCHOR_SEPARATED_FROM_CURRENT_RESEAL = YES
```

### Security Closeout 最终验证

- 定向安全 / release / affected 回归：203 passed；补充环境变量优先级测试：1 passed。Gate B 的 28 项、Gate C 的 4 项、Gate D 的 53 项也已分别通过，不将这些重叠测试相加作为独立测试数量。
- backend full offline：**1736 passed、0 failed、2 skipped、1 warning，434.32 秒**。两个 skip 仍为原有私有 Run 授权条件；未新增 skip 或 xfail。1 个 warning 为既有 Starlette/AnyIO 弃用提示。
- offline runner：`external_network=0`、`real_model=0`、`real_embedding=0`、`forbidden_data_access=0`。
- frontend full：**132 passed、0 failed**；`npm run build` PASS。只保留既有 >500KB warning。
- PyInstaller onedir / NSIS 重建 PASS，构建输入 407 项，实际包文件 471 项。当前工作树生产源码与候选包构建清单逐文件哈希相符。
- Packaging smoke：**4 passed**。实际包内未包含 .env、运行配置、DPAPI 用户密文、测试 Secret、用户资料/稿件、private Writing Reference 或开发临时文件。
- 实际安装 EXE：普通 Windows 用户令牌（未提升权限），DPAPI Provider / Embedding 保存和重启读取、实际旧明文迁移、配置 / 连接测试 / Reference 上传 / 资料至 READY / Word 导出均通过。
- 安装版 Origin：`http://127.0.0.1:19627` 来自最后一次 smoke 的实际端口；重启后重新从实例状态获取端口。所有合法 stateful 请求发送精确 Origin；缺失/非法 Origin 请求拒绝；GET/HEAD、health、静态首页兼容。无测试豁免。
- 最终完整 Runtime 扫描在程序关闭后执行，包含已释放的 `desktop.lock`，无测试 Secret 明文。卸载等待程序目录、桌面/开始菜单入口及两处安装注册均删除；Runtime 默认保留。
- 最后一次验收仅调用本机 fake 服务：LLM 连接测试 2 次、Embedding 连接/资料测试 3 次；真实 Writer、真实 Embedding、外部网络调用均为 0。构建使用现有本地工具，没有下载依赖。
- `git diff --check`、scope/boundary PASS：本轮 **5 个生产文件、8 个测试/打包支持文件、1 个文档**；RAG、Retrieval、P1/P2/P3-A/P3-B、Evidence、Atomic Evidence、Writer Prompt、Fact Safety、FLOW3/FLOW4、Must-Deliver、Commercial Gate、Genre/Scene/Business Domain 及未验收文种权限不变。原有 11 个无关 untracked 未读写。

安装验收保留了两次测试支持问题的原始失败证据：第一次扫描了正在被 Windows 锁定的 `desktop.lock`；第二次在 NSIS 子进程完成快捷方式/注册表清理前提前断言。前者改为关闭后全量扫描，后者等待完整卸载条件；没有删除断言、放宽 Origin 或改动 Installer/生产代码来迎合测试。最终同一候选安装包完整复验 PASS。

本轮证据目录为 `tmp/release-rc-01/security-closeout/`：

```text
protection-preflight.json
security-reseals.json
reseal-mutation-proof.json
gate-b-tests.log
gate-c-tests.log
gate-d-tests.log
targeted-tests.log
env-precedence-test.log
frontend-full.log
frontend-build.log
backend-full.log
package-build.log
installed-smoke-first-lock-scan-failure.log
installed-smoke-second-uninstall-race.log
installed-smoke-final.log
packaging-smoke.log
package-final.json
boundary-final.json
```

新候选的 `tmp/rc01-bd0a07db/stage/BUILD-MANIFEST.json`、`PACKAGE-MANIFEST.json`、`dev-acceptance.json` 及构建日志提供包级证据；旧 Phase 3 候选仅作为历史记录。

本轮实际运行的主要命令（PowerShell，测试 Python 使用已存在的 runtime 和依赖）：

```powershell
$python = 'C:\Users\peng chen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
Set-Location -LiteralPath 'D:\AI_Projects\gongwen-style-agent-commercial\backend'
& $python -B -c "import sys; sys.path.insert(0,r'D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'); sys.path.insert(0,'tests'); sys.argv=['security-closeout','-q','-rs','tests']; from run_multi_genre_offline import main; raise SystemExit(main(workspace_relative='tmp/release-rc-01',task_name='RC01-SECURITY-FULL',count_label='SECURITY_OFFLINE_COUNTS',require_user_manifest=True))"
Set-Location -LiteralPath '../frontend'
node --test tests/pages/*.test.mjs tests/utils/*.test.mjs
npm.cmd run build
Set-Location -LiteralPath '..'
& $python -B packaging/windows/build_release.py --site-packages backend/.venv/Lib/site-packages --build-tools tmp/release-rc-01/build-tools --nsis tmp/release-rc-01/installer-tools/nsis-3.12
$securityBuild = Get-Content -LiteralPath tmp/release-rc-01/latest-package.txt
& $python -B -c "import sys,runpy; sys.path.insert(0,r'D:\AI_Projects\gongwen-style-agent-commercial\backend\.venv\Lib\site-packages'); sys.argv=['dev_smoke.py','--build',sys.argv[1]]; runpy.run_path('packaging/windows/dev_smoke.py',run_name='__main__')" $securityBuild
& $python -B packaging/windows/test_packaging_manifest.py
& $python -B tmp/release-rc-01/security-closeout/verify_closeout.py
git diff --check
```

本轮仍无 commit，修改前后 HEAD 均为 Required BASE，暂存区为空。回滚依据为 `security-closeout/before/` 中的逐文件快照及 `before.diff`；若用户另行授权回滚，只撤销本文列出的本轮增量，并单独处理新增 Secret Store / 两个安全测试文件，不覆盖既有 RC-01 实现、历史 re-anchor 或用户文件。

## 当前停止边界

```text
IMPLEMENTATION = PASS
PRE_RELEASE_AUDIT = PASS
PACKAGE_BUILD = PASS (development candidate)
READY_FOR_EXTERNAL_REVIEW = YES
RELEASE_RC01_SECURITY_CLOSEOUT_IMPLEMENTATION = PASS
READY_FOR_CHATGPT_REVIEW = YES
EXTERNAL_REVIEW = RECHECK_PENDING (Security Closeout)
READY_FOR_COMMERCIAL_RELEASE = NO
```

未 commit / push / PR / merge。未运行干净机验收、安装后的真实模型生成或商业发布；开发机 fake 结果不替代这些 Gate。安装包尚未做发行签名。

只有用户明确提供 `EXTERNAL_REVIEW = PASS` 且 `BLOCKING_FINDINGS = 0`，才继续 Git Closeout + Push + PR。创建 PR 后再次停止，等待明确合并授权；合并后仍须从干净 main 重建，再进入真正干净 Windows 环境验收。
