# GENERATION-WAITING-UX-01 实现与验证记录

本文是本轮前端实现记录，不是新的项目规则真源。

## 基线与边界

- 实施前分支：`main`。
- Required BASE / 当前 HEAD：`fb9dda729dd66898fc987f32828bc68743067bdc`。
- 实施分支：`codex/generation-waiting-ux-01`，直接从 Required BASE 创建。
- 原有 11 个 untracked 用户文件保持原位；实施前后以路径、大小、SHA-256 核对。
- 本工单明确禁止暂存、提交、push、PR、merge；以既有 BASE 作为回滚基点，停留在未提交状态。

## 当前证据与最小实现

真实等待入口是 `App` 的 `genResult` → `ArticleGenPage` → `GenerationPage`，不是旧版兼容布局中的 loading 区域。

等待 UI owner 为 `frontend/src/pages/GenerationPage.jsx`；样式 owner 为 `frontend/src/App.css`；请求生命周期仍归 `frontend/src/pages/ArticleGenPage.jsx`。

已确认的问题是等待展示缺少已用时间和流程说明。本轮没有推断后端当前执行阶段，也没有修改生成质量、事实安全、资料检索或文章交付逻辑。待验风险限定为计时生命周期、原有导航行为和桌面布局。

生产文件仅 3 个：

1. `GenerationPage.jsx`：私有 `GenerationWaitingIndicator` 持有计时状态，只更新等待区域。每秒读取 `Date.now()` 与挂载起点的差值，避免后台标签页延迟回调导致计时累计失真。卸载清理 interval；`loading=false` 不挂载等待 UI。
2. `ArticleGenPage.jsx`：只向等待页传入既有 `loading`，并用既有当前任务 id 作为 React key，确保连续同标题任务也重新计时。不改任何请求处理或 payload 构造。
3. `App.css`：仅调整等待页样式，静态纵向七步、等宽计时数字、轻量圆点动画及 `prefers-reduced-motion`。

标题原文直接展示；七个流程节点没有执行中、已完成、百分比或剩余时间状态。只有等待提示在 31 秒、91 秒时切换。`role="status"` 保留在静态状态区域，秒级 timer 为独立兄弟节点且 `aria-live="off"`，动画为 `aria-hidden="true"`。

## 测试证据

工作目录为 `frontend`，实际执行：

```powershell
node --test tests/pages/GenerationPage.waiting-ux.test.mjs
node --test tests/**/*.test.mjs
npm run build
```

- 定向测试：10/10 通过。
- 完整前端测试：113/113 通过。
- 构建：通过；Vite 报告大于 500 kB 的 bundle 提示，没有构建失败。
- Node MockTimers 提示 experimental API；测试未跳过，没有真实等待 60 秒。
- 定向测试日志：`tmp/generation-waiting-ux-01/targeted-tests.log`。
- 完整测试日志：`tmp/generation-waiting-ux-01/full-tests.log`。

测试执行真实等待组件及 `ArticleGenPage` 的事件处理逻辑，API 使用内存 mock，时钟使用 Node fake timers。覆盖 00:00、00:01、00:59、01:00、01:02:15，延迟回调、清理、StrictMode 等价 effect 重放、提示阈值、静态步骤和屏幕阅读器区域隔离。

成功响应进入原有 EditorPage；失败保留原有错误 Modal；返回参数立即卸载 timer，同时保留原有在途请求行为；批量同标题不同任务 id 重新从零计时。

## 离线浏览器验收

在仓库根目录实际执行：

```powershell
node tmp/generation-waiting-ux-01/browser-verify.mjs
```

真实渲染 `GenerationPage + Layout + App.css`，Chrome headless 使用本地 `file://`、内存 fake clock，网络设为 offline 并拦截 HTTP/HTTPS。14 个计时、生命周期、可访问性检查点通过；浏览器记录的外部请求、API 请求和运行异常均为 0。这里没有冒充完整 App 的请求测试；成功/失败业务调用由上面的定向测试覆盖。

主 Agent 已查看 1440×900 和 1920×1080 截图：流程完整可见，底部为 705px，右栏宽度分别约 351px / 500px，返回按钮完整，无横向滚动及中文截断。另以长标题检查换行，标题保持原文，流程底部为 732px。减少动画模式下，三个圆点的计算样式 `animationName` 均为 `none`。

本地证据位于被 Git 忽略的 `tmp/generation-waiting-ux-01/`：

- `browser-verification.json`
- `waiting-1440x900.png`
- `waiting-1920x1080.png`
- `waiting-1440x900-long-title.png`

首次浏览器启动因执行沙箱的 Chrome 子进程权限失败；获准提升执行权限后正常启动，保留 Chrome 自身沙箱。检测脚本随后修正了步骤装饰连接线造成的裁切误报，最终使用实际文字边界验收。这些属于验证环境/脚本问题，没有掩盖产品失败。

没有启动后端、调用真实生成 API、Writer 或 Embedding；没有外部网络调用，也没有运行后端 suite。

## 六遍自检

1. PASS：Backend diff 为 0，API 和依赖文件没有变化。
2. PASS：timer 使用真实时间差，未将时间映射成后台进度。
3. PASS：七步始终是静态说明，没有百分比、ETA 或假完成状态。
4. PASS：成功、失败、返回参数行为保持原实现，定向测试通过。
5. PASS：卸载及请求切换正确清理/重置 interval，伪时钟和真实 React 浏览器验证通过。
6. PASS：仅 3 个等待 UX 相关生产文件、1 个测试文件及本文；未顺手修改其他 UI。

## 回滚与交付状态

本轮没有 commit 或 staged 文件。后续如需回滚，先确认没有新增用户修改，再仅将上述三个生产文件还原到 Required BASE；新增测试和本文可移入本任务 `tmp` 备份目录，不触碰原有 11 个用户文件。当前没有执行回滚、删除或迁移操作。

本地验证通过不代表已执行 ChatGPT Review、Claude Code / DeepSeek External Review、Git Closeout 或发布。本轮只交付未提交的实现，等待后续 review。
