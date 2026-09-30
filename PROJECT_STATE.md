# 项目状态

更新日期：2026-09-30。本文件记录当前工程事实和已确认方向；项目规则唯一真源为 [AGENTS.md](AGENTS.md)，当前任务见 [CURRENT_TASK.md](CURRENT_TASK.md)。

## 当前主线

- 文章质量为最高优先级。
- 当前目标：提高第一次 Writer 调用的商业质量。
- Recovery 后续定位为异常兜底，不作为首稿质量替代方案。
- 战略原则：给模型组织和表达的自由，不给模型编造工作、成绩、状态和因果的自由。

## RECOVERY-SELECTION-RCA-01

报告：[RECOVERY-SELECTION-RCA-01](docs/rca/RECOVERY-SELECTION-RCA-01.md)。审查结果依据用户在本次 Git Closeout 工单中确认的 Gate；本阶段未重新执行审查或真实生成。

```text
RCA_STATUS = PASS
CHATGPT_CODE_REVIEW = PASS_WITH_NON_BLOCKING_FINDINGS
EXTERNAL_REVIEW = PASS_WITH_NON_BLOCKING_FINDINGS
EVIDENCE_CHAIN = PASS
ATTEMPT_2_REJECTION = VERIFIED
ATTEMPT_3_REJECTION = VERIFIED
ATTEMPT_1_SELECTION = VERIFIED
RECOVERY_PROMPT_IDENTITY = VERIFIED
FEEDBACK_PROJECTION_FINDING = VERIFIED
RCA_SCOPE_DISCIPLINE = PASS
BLOCKING_FINDINGS = NONE
GIT_CLOSEOUT = PASS
RCA_COMMIT_PUSH = PASS
PR_CREATED = YES
PR_NUMBER = 28
PR_STATE = OPEN
RCA_COMMIT_SHA = d392efd0fd609edef2f8d603aaf5561c8ba28bd4
REMOTE_BRANCH = origin/codex/must-deliver-recovery-01
PR_HEAD = codex/must-deliver-recovery-01
PR_BASE = main
MERGE_PERFORMED = NO
```

PR：[RECOVERY-SELECTION-RCA-01: close recovery selection RCA #28](https://github.com/chenhepeng701-coder/gongwen-style-agent-commercial/pull/28)。

`RCA_COMMIT_SHA` 是仅提交已外审报告的第一提交，不是状态同步提交的自引用。两个根目录状态文件作为第二个独立提交纳入同一 PR，实际提交列表以该 PR 为准。PR 尚未合并，不得把分支成果写成已进入 main。

**RCA PASS ≠ Commercial Quality PASS；RCA PASS ≠ Fact Safety PASS。** 本次 RCA 也不证明 RAG 已通过，未解释首稿商业质量差的根因，未实施生产修复。

### 已验证核心事实

- A2：hard block 1→2，unresolved 32→37，多类事实风险增加，被“事实风险不得增加”的候选选择规则拒绝。
- A3：unresolved 降至31，但相对 A1，hard block 1→2、`SUBJECT_SCOPE_MISMATCH` 1→2，因此被拒。
- A1 最终选中由 selector 风险合同导致，不是同分时的顺序偏好。
- A2/A3 recovery prompt 的 system/user hash 相同；两轮均基于 A1 + 同一34条反馈。
- Quality feedback projection 丢失 issue、location、repairability、repair_instruction 等细粒度修复信息。
- Recovery 有局部检查项改善，但没有改善本次最终交付正文；`RECOVERY_EXTRA_CALLS_VALUE = PARTIAL`。

### 后续必须继承的非阻断输入

- selector 的 `quality_rank` 无法表达同一 check 内的细粒度改善；这不是本次 A2/A3 被拒的直接原因，不能因此用质量收益抵消新增事实风险。
- 本次三稿 `citations.proposals` 为空，同时存在大量 `AMBIGUOUS_EVIDENCE`；不能直接推导资料库无证据或 RAG 已失败。
- recovery prompt / feedback 正文未持久化，本次通过冻结产物、生产构造函数和已保存哈希重建核验；后续应考虑将其纳入可观察性合同。
- 报告968/989共同前缀的文字精度不一致为非阻断发现，不改变结论；本阶段保持已外审报告原样。
- CURRENT_TASK.md 创建晚于 RCA 报告。报告中的文件缺失、待审查等状态属于报告形成时的历史快照；当前工程状态以本文件和根目录 CURRENT_TASK.md 为准，不回改已外审 artifact。

## 下一任务

```text
NEXT_TASK = WRITER-QUALITY-RCA-01
NEXT_TASK_STATUS = READY_TO_START
INVESTIGATION_STARTED = NO
```

目标：查清首稿商业质量差的直接原因。首批调查范围与禁止事项见 [CURRENT_TASK.md](CURRENT_TASK.md)。本次仅完成任务登记，不开展下一任务调查、A/B 或生产实现。
