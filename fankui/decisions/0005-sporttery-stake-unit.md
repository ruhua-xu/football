# ADR-0005：按竞彩2元基础单位推导 stake

## 状态

Accepted

## 背景

如果 Optimizer 直接分配任意金额，可能生成17元等无法按实际竞彩规则出票的结果。复杂过关的成本还取决于 AtomicBet 数量。

## 决策

Money 使用整数分，并由版本化 PayoutPolicy 定义：

```text
base_stake_fen = 200
max_multiplier = 50
max_ticket_stake_fen = 600000
1 <= multiplier <= max_multiplier
stake_fen = atomic_bet_count * base_stake_fen * multiplier
stake_fen <= max_ticket_stake_fen
```

MVP 简单2串1的 `atomic_bet_count=1`，因此投入是2元的整数倍。Optimizer 选择 Ticket 和 multiplier，不直接选择任意 stake。

三个规则参数由版本化 PayoutPolicy / BettingRules 配置统一提供，并随 AnalysisRun 冻结，不散落写死在 Optimizer 中。

奖金舍入由 PayoutPolicy 使用 Decimal 实现，不使用 float 或普通 `round`。

## 结果

- 所有推荐金额均可按竞彩基础单位表达。
- 预算允许未使用余额。
- 未来复杂过关只需提供正确的 AtomicBet 数量和展开规则。
- 官方规则变化可以通过 PayoutPolicy 版本审计和回放。
