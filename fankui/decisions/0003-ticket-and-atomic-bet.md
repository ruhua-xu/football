# ADR-0003：区分 Ticket 与 AtomicBet

## 状态

Accepted

## 背景

简单2串1可以被表示为一个联合事件，但3串4和4串11包含多个可独立中奖、独立计奖的子注。把 Ticket 永久建模为一个联合概率和一个组合赔率会阻碍复杂过关扩展。

## 决策

`Ticket` 表示用户看到的一张顶层竞彩彩票；`AtomicBet` 表示按过关规则展开后的内部计奖组合。

```text
Ticket 1 --- n AtomicBet
AtomicBet 1 --- n BetLeg
```

Ticket 保存 `atomic_bet_count`、投入和聚合收益指标，不把单个 `joint_probability` 作为长期通用字段。

MVP 简单2串1的 `atomic_bet_count=1`。MVP 暂不创建 AtomicBet 数据库表；实现复杂过关时新增 `atomic_bets` 和 `atomic_bet_legs`。

## 结果

- Ticket 数量策略始终作用于顶层 Ticket；具体数量由冻结的 Strategy Profile 决定。
- 未来3串4和4串11不需要重构 Ticket 身份和 Portfolio 约束。
- 复杂系统票需要计算任意中奖概率、盈利概率和场景返还，不能显示含糊的单一“票面概率”。
