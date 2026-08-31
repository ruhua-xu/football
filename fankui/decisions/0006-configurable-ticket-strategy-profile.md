# ADR-0006：Ticket 数量由 Strategy Profile 配置

## 状态

Accepted

## 背景

日常操作优先控制在2至4张顶层 Ticket，但4张是操作复杂度偏好，不是永久业务上限。场次较多时，系统可能发现额外的高价值、低重复机会。硬编码 `ticket_count <= 4` 会阻止这些方案，而直接放宽上限又可能诱发大量低价值小票。

## 决策

每次 AnalysisRun 冻结：

```text
preferred_max_tickets = 4
absolute_max_tickets = 8
extra_ticket_min_roi
operational_complexity_penalty
```

Optimizer 先在 preferred 范围内选择满足基础门槛的 Ticket。超过 preferred 的候选必须在扣除递增复杂度惩罚后仍达到更严格的 ROI 门槛，并引入新的比赛暴露。任何方案不得超过 absolute 上限。

Domain 的 Portfolio 根据自身冻结的 constraints 校验绝对上限。数据库只执行 `ticket_no > 0`，并保存 `strategy_config_json`，不永久硬编码4或8。由于精确 Stress Search 的状态空间随 Ticket 数量指数增长，当前 Domain 另设12张的安全上界。

## 结果

- 4张仍是默认日常目标。
- 高价值独立机会可以受控扩展到配置化绝对上限。
- 增加 absolute 上限不会自动增加 Ticket。
- `NO_BET`、少于 preferred 和未使用预算仍然合法。
- 后续可在精确 Stress Search 的安全上界内调整 Profile，无需迁移数据库。
