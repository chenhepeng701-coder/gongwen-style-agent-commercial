# GENERATION-WAITING-UX-02 实现与验证记录

本文是本工单的实现及验收证据，不是项目规则真源。实施完成后停在 External Review 前，未暂存、commit、push、创建 PR 或 merge。

## 基线与边界

- 修改前分支：`main`；BASE / 修改前后 HEAD：`e50ecb0388eb6dd2ad465e39752331cb5b58e25c`。
- 实施分支：`codex/generation-waiting-ux-02`，从 BASE 创建；以此既有 commit 作为回滚 checkpoint，没有创建新 commit。
- 修改前 tracked / staged 均无改动；原有 11 个 untracked 文件在实施后以路径、大小、SHA-256 核验，全部保持不变。
- ignored 状态已检查。初始全仓扫描发现 `tmp/baseline-02b-r2r3/` 下有历史测试嵌套仓库，部分 ignored 临时目录拒绝访问；没有读取这些仓库内容或改变访问权限，均不属于本轮范围。
- 没有后端、API、payload、EditorPage、批量业务逻辑或依赖变更。NB-1、NB-2、NB-3 保留原状。

## 当前证据、Owner 与最小实现

从当前源码确认：`App` 的 articleGen / genResult 路由使用同一个 `ArticleGenPage` 类型，无路由 key 导致的父组件重建。`ArticleGenPage.handleGenerate` 拥有请求生命周期，调用 `buildTaskGenerationPayload` 和 `generateArticle`；`GenerationPage` 只展示等待状态。修改前定向测试 10/10 通过。

实际问题：`GenerationPage` 内的私有等待组件在挂载时记录开始时间，切页就会重置；articleGen 右侧是另一套单一 spinner，没有返回完整等待页的入口。没有把等待秒数推断为后端执行阶段。

生产文件共 4 个：

1. `frontend/src/pages/ArticleGenPage.jsx`：创建唯一 `requestStartedAt` 状态，在现有 `generateArticle(payload)` 调用前记录 `Date.now()`，在该请求对应的 `finally` 清理。向完整及紧凑等待视图传入同一基准。紧凑视图按钮仅调用 `onNavigate?.('genResult')`。
2. `frontend/src/components/GenerationWaitingIndicator.jsx`：从原页面提取共享组件，支持 `full` / `compact`。共享格式化、真实时间差计算、等待提示及圆点动画。挂载时直接读取当前时间，interval 只刷新当前时间，不累计回调次数；卸载或 startedAt 变化会清理旧 interval。
3. `frontend/src/pages/GenerationPage.jsx`：消费共享组件的 full 视图；七步说明和原导航、停止后续任务入口保持原样。
4. `frontend/src/App.css`：为共享内容适配段落和标题样式，增加 compact 间距、字号。沿用已有圆点动画与 `prefers-reduced-motion`，未调整七步流程样式。

紧凑视图包含“正在生成文章”、已用时间、请求仍在处理／返回参数不会取消请求说明、等待提示和“查看生成状态”。没有七步流程、百分比、ETA 或阶段完成信息。

时间基准由请求 owner 创建，两种等待视图消费；导航不写请求状态。最大回归风险是重挂载导致计时归零、误发第二次请求和完成后残留 timer，均由下述测试阻断。文章质量、事实安全、生成合同及模式没有变化。

## 实际运行与结果

工作目录为 `frontend`：

```powershell
node --test tests/pages/GenerationPage.waiting-ux.test.mjs
node --test tests/**/*.test.mjs
npm run build
```

- 修改后定向测试：16/16 PASS，未跳过。
- 全量前端测试：119/119 PASS，未跳过；日志 `tmp/generation-waiting-ux-02/full-tests.log`。
- 构建：PASS；日志 `tmp/generation-waiting-ux-02/build.log`。Vite 提示 bundle 大于 500 kB；Node 提示 MockTimers 为 experimental，均不是失败。
- `git diff --check`：PASS。

测试文件为 `frontend/tests/pages/GenerationPage.waiting-ux.test.mjs`。使用当前真实页面、共享组件和事件处理函数，生成 API 为内存 pending Promise，时钟为 Node fake timers；这不是远程模型生成验收。

覆盖：紧凑内容与旧 spinner 移除、无假进度、60 秒返回参数及 80 秒重回完整等待页、同一个未结束请求与原 payload 保持、重复切页不增发请求、后台延迟后的时间追赶、成功／失败分别在两个视图收尾、新单篇请求归零、批量原有逐请求计时、interval 清理、StrictMode 等价 effect 重放以及 timer 与 live region 分离。

## 离线浏览器验证

仓库根目录实际执行：

```powershell
node tmp/generation-waiting-ux-02/browser-verify.mjs
```

真实 React StrictMode 渲染当前 `App`、导航、`ArticleGenPage` 和两种等待视图。API 替换为内存测试端点，使用合成目录和标题；Chrome 加载本地 file 页面，网络设为 offline 并阻断 HTTP/HTTPS/WS/WSS。没有启动后端或调用真实模型。

最终 7 个检查点 PASS：完整等待起点、60 秒紧凑页、减少动画、1024 宽度、80 秒完整页、失败清理、下一次请求归零。往返前后的生成请求数为 1；下一次显式点击生成后才为 2。两个视图的减少动画计算样式均为 `animationName: none`；外部请求数和运行异常数均为 0。

主 Agent 已查看以下实际截图，紧凑按钮可见、中文可换行、无横向溢出：

- `tmp/generation-waiting-ux-02/compact-1440x900.png`
- `tmp/generation-waiting-ux-02/compact-1024x768.png`
- `tmp/generation-waiting-ux-02/full-1440x900.png`

结果保存在 `tmp/generation-waiting-ux-02/browser-verification.json`。这些脚本、构建和证据位于 ignored 目录，不进入提交范围。

验证中出现并保留记录的问题：首次 Chrome 控制在沙箱内 Page.enable 超时；获准提升后测试入口因独立 esbuild 默认 JSX 模式报 React 未定义，诊断重跑确认根因。将测试脚本改为与 Vite 一致的 automatic JSX 后通过，没有为修复测试环境改动产品实现。失败记录在 `tmp/generation-waiting-ux-02/browser-failure.json`。

## 最终边界与回滚

本轮应审查的文件仅上述 4 个生产文件、1 个测试文件和本文。后端 diff、API diff、payload 构造 diff、EditorPage diff、BatchTaskPage diff、依赖 diff、staged diff 均为空。现有 11 个无关 untracked 文件不属于本轮审查范围。

未运行：真实生成 API、后端 suite、Writer、RAG、Embedding、文章质量或黄金样本验收。本工单仅前端等待 UX，不声称生成质量变化；外部独立审查尚未开始。

需要回滚时，先确认没有后续用户编辑并备份本轮补丁，然后只将 `App.css`、`ArticleGenPage.jsx`、`GenerationPage.jsx` 和本轮测试文件还原至 BASE；新增共享组件与本文可在用户确认后移入备份。不要使用全仓 restore/reset/clean，不触碰原有 untracked 文件。当前未执行回滚、删除或迁移。

`GENERATION_WAITING_UX_02_IMPLEMENTATION = PASS`，`READY_FOR_EXTERNAL_REVIEW = YES`。所有代码保持未提交状态，HEAD 仍为 BASE，commit message 不适用。
