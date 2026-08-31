# 投注与组合模型

## 目标与边界

Betting Engine 将已冻结的最终概率和中国竞彩固定奖金转换为可验证的投注候选。Portfolio Optimizer 按冻结的 Strategy Profile 选择顶层 Ticket，并为每张 Ticket 分配正整数倍数。

本模块不修改预测概率，不调用 LLM，不抓取赔率，也不执行真实下注。

## 核心术语

### SelectionCandidate

单个比赛、单个 Market、单个方向的定价结果：

```text
candidate_id
analysis_run_id
match_id
market_key
selection_key
final_prediction_id
sporttery_bonus_snapshot_id
probability_used
fixed_bonus
break_even_probability
ev
eligibility_status
rejection_code
risk_annotations
```

LLM 的 `preferred_outcomes` 或 `avoid_outcomes` 只能作为语义注解或固定风险策略的输入，不能直接创建 SelectionCandidate。

### Ticket

Ticket 是用户实际看到的一张顶层竞彩彩票：

```text
Ticket
├── pass_type
├── top_level_legs
├── multiplier
├── atomic_bet_count
├── stake_fen
└── aggregate_quote
```

Ticket 数量限制作用于顶层 Ticket，不作用于倍数和 AtomicBet 数量。4张是默认操作偏好，不是永久业务上限。

### AtomicBet

AtomicBet 是一个可以独立判定中奖并独立计奖的内部子注：

```text
AtomicBet
├── legs
├── joint_probability
├── gross_payout_per_base_stake
└── result
```

关系：

```text
Ticket 1 --- n AtomicBet
AtomicBet 1 --- n BetLeg
```

MVP 简单2串1只有一个 AtomicBet。未来：

```text
3串4 -> 多个2-leg / 3-leg AtomicBet
4串11 -> 多个2-leg / 3-leg / 4-leg AtomicBet
```

因此顶层 Ticket 不使用“唯一联合概率”或“唯一组合赔率”作为长期通用模型。复杂系统票需要分别计算任意中奖概率、盈利概率、期望返还和场景返还。

MVP 不创建 `atomic_bets` 和 `atomic_bet_legs` 表，只在 Domain Design 中冻结该边界。

## Market 支持

Betting Engine 的所有输入都必须带 `MarketKey`：

```text
MarketKey(market_type, handicap_value)
```

MVP 只允许：

```text
MarketType.THREE_WAY
handicap_value = null
```

`HANDICAP_THREE_WAY` 已进入 Domain 和 Schema，但在 MVP 必须返回明确的 `UNSUPPORTED_MARKET`，不能误用普通胜平负算法。

## Betting Engine

推荐接口：

```python
class BettingEngine(Protocol):
    def build_candidates(
        self,
        predictions: Sequence[FinalPrediction],
        bonus_snapshots: Sequence[SportteryBonusSnapshot],
        rules: BettingRules,
    ) -> BettingCandidateSet:
        ...
```

输出：

```text
BettingCandidateSet
├── selection_candidates
├── ticket_candidates
└── rejected_items
```

Betting Engine 负责：

- 校验 FinalPrediction 与 BonusSnapshot 的 match、MarketKey 和时点一致。
- 计算盈亏平衡概率和单场 EV。
- 根据允许玩法枚举合法 TicketCandidate。
- 展开或调用 PassTypeDefinition 获得 AtomicBet 结构。
- 使用版本化 PayoutPolicy 计算投入和预计返还。
- 输出拒绝原因，不因个别候选非法而静默修复。

Betting Engine 不负责：

- 修改 `P_final`。
- 决定融合权重。
- 分配预算和倍数。
- 决定最终 Portfolio 或 `NO_BET`。
- 根据 LLM 文本直接推荐投注。

## PayoutPolicy

竞彩金额统一使用整数分：

```text
base_stake_fen = 200
max_multiplier = 50
max_ticket_stake_fen = 600000
```

Multiplier 必须为正整数。Ticket 投入必须由下式推导：

```text
stake_fen = atomic_bet_count * base_stake_fen * multiplier
```

并满足：

```text
1 <= multiplier <= max_multiplier
stake_fen <= max_ticket_stake_fen
```

禁止由 Optimizer 直接产生任意 stake，例如17元。

### 简单2串1

MVP 中：

```text
atomic_bet_count = 1
stake_fen = 200 * multiplier
```

独立比赛假设下：

```text
joint_probability = p1 * p2
```

单注中奖毛返还：

```text
gross_payout_fen = official_round_to_fen(
    base_stake_yuan * fixed_bonus_1 * fixed_bonus_2
)
```

期望指标：

```text
expected_gross_payout = joint_probability * gross_payout * multiplier
expected_profit = expected_gross_payout - stake
expected_roi = expected_profit / stake
```

单场近似 EV 仍可表示为：

```text
selection_ev = probability * fixed_bonus - 1
```

Selection EV 用于候选筛选；Ticket 级收益必须使用实际2元投注单位和官方舍入规则重新计算。

### 舍入

奖金舍入由版本化 `PayoutPolicy` 实现。禁止使用 float 和普通 `round`。MVP 测试至少覆盖：

```text
2 * 1.65 * 1.75 = 5.775 -> 5.78元
2 * 1.25 * 1.97 = 4.925 -> 4.92元
```

## Portfolio Optimizer

推荐接口：

```python
class PortfolioOptimizer(Protocol):
    def optimize(
        self,
        candidates: Sequence[TicketCandidate],
        constraints: PortfolioConstraints,
    ) -> PortfolioRecommendation | NoBet:
        ...
```

```text
PortfolioConstraints
├── budget_fen
├── preferred_max_tickets
├── absolute_max_tickets
├── extra_ticket_min_roi
├── operational_complexity_penalty
├── min_ticket_ev
├── min_ticket_roi
├── max_match_exposure
├── allow_unspent_budget
└── deterministic_tie_break
```

Optimizer 的决策变量是：

```text
是否选择 TicketCandidate
每张 Ticket 的正整数 multiplier
```

候选 multiplier 的上界统一由 PayoutPolicy / BettingRules 提供，Optimizer 不得写死50或600000等规则参数。

Optimizer 不直接优化任意金额。每个 multiplier 对应的 stake 由 PayoutPolicy 推导。

输出必须满足：

```text
ticket_count <= absolute_max_tickets
total_stake_fen <= budget_fen
total_stake_fen % 200 == 0（MVP 简单2串1）
unused_budget_fen = budget_fen - total_stake_fen
```

预算不是必须花完。101元预算允许保留1元或更多资金。

Optimizer 优先在 `preferred_max_tickets` 内形成方案。超过 preferred 时，每张额外 Ticket 必须：

- 在扣除递增的 operational complexity penalty 后仍达到更高 ROI 门槛。
- 引入此前未覆盖的比赛暴露，避免只为增加票数重复同类机会。
- 不使总数超过 `absolute_max_tickets`。

少于 preferred、只有1张或 `NO_BET` 均为合法结果。

## NO_BET

`NO_BET` 是正常业务结果，不是异常。建议稳定原因码：

```text
NO_BET_DATA_QUALITY
NO_BET_NO_VALUE
NO_BET_NO_FEASIBLE_TICKET
NO_BET_RISK_LIMIT
```

以下情况必须区分为运行失败而不是 `NO_BET`：

```text
概率不合法
配置不合法
输入快照损坏
数据库事务失败
```

## Ticket 聚合指标

MVP 简单2串1可以计算单一联合概率，但持久化字段采用可扩展语义：

```text
atomic_bet_count
stake_fen
expected_gross_payout
expected_profit
expected_roi
probability_any_payout
max_gross_payout_fen
payout_policy_version
```

实现3串4、4串11时，再增加：

```text
atomic_bets
atomic_bet_legs
scenario_payouts
probability_of_profit
```

## MVP 验收

- 不支持的 Market 返回稳定拒绝码。
- 3场各一个合法方向时产生3个不重复的2串1。
- 同场方向不能互相串关。
- Ticket multiplier 始终为正整数。
- Ticket multiplier 不超过版本化规则上限。
- 单票投入不超过 `max_ticket_stake_fen`。
- 简单2串1投入始终为2元的整数倍。
- 总投入不超过预算，顶层 Ticket 不超过冻结的绝对上限。
- 普通价值候选不会仅因 absolute 上限提高而自动增加票数。
- 满足更严格增量条件的候选可以超过 preferred，但不能超过 absolute。
- 所有候选不合格时稳定输出 `NO_BET`。
- 相同输入、配置和分析时点产生完全相同的 Portfolio。
