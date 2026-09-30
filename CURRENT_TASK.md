# 当前任务

更新日期：2026-09-30。项目规则以 [AGENTS.md](AGENTS.md) 为准；工程状态见根目录 [PROJECT_STATE.md](PROJECT_STATE.md)。

## 任务状态

```text
TASK = WRITER-QUALITY-RCA-01
TASK_TYPE = Root Cause Analysis
STATUS = READY_TO_START
CURRENT_GATE = IMPLEMENTATION_OR_INVESTIGATION_NOT_STARTED
```

目标：查清首稿商业质量差的直接原因。本文件仅登记下一任务，调查尚未开始。

## 首批调查范围

- 首稿32个事实/非事实检查单元逐项复核。
- 实际生效 Writer Prompt 规则审计。
- Evidence 对模型组织表达自由度的影响。
- Writing Reference 在真实运行中为何 unavailable、是否实际参与。

## 调查边界

- 不调用真实 Writer 或 Embedding。
- 不修改生产实现。
- 不开始 A/B。
- 不放宽 Fact Safety。
- 不先改 Recovery。

战略原则：给模型组织和表达的自由，不给模型编造工作、成绩、状态和因果的自由。

## 前置成果与继承输入

前一任务 RECOVERY-SELECTION-RCA-01 的 ChatGPT Code Review 和 External Review 均为 PASS_WITH_NON_BLOCKING_FINDINGS，无阻断发现；RCA 报告已提交并推送，PR 已创建且未合并。

- 报告：[RECOVERY-SELECTION-RCA-01](docs/rca/RECOVERY-SELECTION-RCA-01.md)。
- PR：[RECOVERY-SELECTION-RCA-01 #28](https://github.com/chenhepeng701-coder/gongwen-style-agent-commercial/pull/28)。
- RCA 提交：`d392efd0fd609edef2f8d603aaf5561c8ba28bd4`。
- 已验证事实、未证明事项及非阻断发现见 [PROJECT_STATE.md](PROJECT_STATE.md)。RCA PASS 不代表商业质量或 Fact Safety PASS，首稿质量差的根因仍待调查。
