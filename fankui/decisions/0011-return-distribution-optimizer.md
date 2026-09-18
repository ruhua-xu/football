# ADR-0011 — Exact portfolio return distribution / marginal optimizer

- Status：**Accepted**（整体0.9 Architecture Review通过）
- Date：2026-09-17
- Baseline：v0.8.0，`cc95d3242218a64712f860fff4979066d4a72e1d`
- Branch：`feature/0.9.0-return-distribution-optimizer`
- Acceptance：2026-09-18，workspace-owner接受tree `52284cdd20111da15b7be6d5d70c0bce38b19a86`；内容SHA256 `d53e3cc59978bbab8136bfe202316e6452fdf9efb74cefff69543a8c0cb0b069`。下方技术决策正文保持接受时原样。

## Context

0.8已封存P_final marginals、SP、candidate catalog和expanded AtomicBet graph。
0.9在不改变这些输入的前提下，计算组合gross payout、现金后的ending capital和profit/loss分布，
并用固定、未校准的策略目标重新选择candidate tickets与整数倍数。预算是上限；NO_BET是正式baseline。

Sportmonks capability capture已完成且raw已提前清理。该capture不授予训练、预测、回测或长期raw保留权限；
0.9设计和验收只使用synthetic/fixed fixtures与v0.8冻结代码/工件，不读取capability raw。

## Proposed decision（候选实现已遵循）

1. **Same match / one MarketKey across the entire portfolio.** 包括handicap参数不同的keys；否则`CROSS_MARKET_JOINT_UNAVAILABLE`，不做独立相乘、Poisson替代、IPF/copula或重校准。
2. **Relevant outcomes + REST.** 概率取封存P_final；REST为1减所选outcomes概率之和。全catalog被覆盖则无REST。输入和必须exactly 1；不将V2允许的近似输入和偷偷归一化。
3. **INDEPENDENT_MATCHES_V1.** 每个unique match仅一次状态变量；ticket↔match连通组件内枚举，独立组件做精确convolution。同一gross聚合成唯一support point。
4. **Fixed Decimal 512.** Probabilities不量化，保留极小正质量；所有有限概率乘加exact且closure=1。利润比例不额外quantize；无限小数只按固定512精度HALF_EVEN处理，budget0为null。
5. **Frozen payout.** 直接使用封存AtomicBet gross与multiplier；不重新乘SP。显式world payout逐值对照Settlement V2。
6. **Evaluation ≠ recommendation.** 合法金额下可对结构上高风险的组合计算分布，同时返回`feasible=false/violations`；optimizer严格排除这些组合。
7. **RETURN_DISTRIBUTION_MARGINAL_V1.** NO_BET起步，先逐轮开票，再逐轮multiplier+1。每次重算完整分布；只接受正utility增量，并保留V2风险/role/auxiliary约束。不是global optimum。
8. **Fixed objective.** `UNCALIBRATED_STRATEGY_POLICY_V1`固定正向/penalty weights；break-even报告但不高权重奖励。CLI只消费sealed source IDs，不接受概率、SP、budget、EV、profile内容override。
9. **Pareto scope explicit.** 对每轮feasible increment pool进行支配筛选，再比较正utility；最终计数覆盖所有独特已评估feasible allocations（含NO_BET）。不宣称未遍历全空间的完整frontier。
10. **Additive persistence.** 独立`rd_*`闭集typed graph、source FKs、append-only、deferred seals、exact retry与source/数学/optimizer重放。旧mm/V1表、trigger和工件不改写。

## Bounds and consequences

- 默认catalog32、每component65536 worlds、support65536、unique matches12、preferred4/absolute8。
- 额外固定CPU/存储guard：convolution products1048576、evaluation work16777216、optimizer evaluations4096/work67108864；gross support使用SQLite signed-int64 fen，budget最多128位十进制数字，artifact最多64MiB。
- 超限明确unavailable/reject；无top-N、sampling、Monte Carlo或approximate fallback。
- 旧selected tickets仅作legacy benchmark。源catalog超限则optimizer unavailable；旧0.8 recommendation不被冒充为新结果。
- 当前实现code hash绑定三份算法/合同源码的LF-normalized bytes；不匹配的implementation不默默重放旧0.9工件。
- HEDGE需V2 surviving-path witness且utility增益为正；LONGSHOT需独立eligible outcomes、高回报、小额auxiliary资格及正增量。标签不强制出票。
- 未加入新market、模型tuning、same-match joint模型、Kelly、formal CVaR、跨日资金模拟或真实业务回测。

## Candidate contracts and acceptance

- `fankui/return_distribution_v1_contract.md`
- `config/return_distribution_policy_v1.json`
- `config/return_objective_profile_v1.json`
- `data/fixtures/return_distribution_v1.json`（A–T）
- `fankui/phase_9_implementation_report.md`

Exact仅在封存marginals及声明的既有独立比赛假设下成立；不宣称现实统计独立。
Synthetic acceptance和legacy software comparison不代表真实ROI、alpha或收益优势。
Package metadata保持0.8.0；无main merge、0.9 tag或release bump。
