# 架构修订报告 0831054

## 修订范围

本次根据 `yaoqiu/yaoqiu0831054.md` 对已确认架构做增量修订，没有重新设计总体架构，也没有进入 MVP 业务实现。

已生成：

```text
fankui/architecture.md
fankui/data_model.md
fankui/betting_model.md
fankui/llm_strategy.md
fankui/decisions/0001-market-abstraction.md
fankui/decisions/0002-versioned-fusion-policies.md
fankui/decisions/0003-ticket-and-atomic-bet.md
fankui/decisions/0004-frozen-evidence-for-llm.md
fankui/decisions/0005-sporttery-stake-unit.md
fankui/decisions/0006-configurable-ticket-strategy-profile.md
```

## Domain Model 差异

### 1. Market

新增：

```text
MarketType
MarketKey(market_type, handicap_value)
SelectionKey
ProbabilityDistribution
```

数据库同时保存非空规范化 key，例如 `THREE_WAY`、`HANDICAP_THREE_WAY:-1`，用它建立唯一约束，避免 nullable handicap 造成重复记录。

预留：

```text
THREE_WAY
HANDICAP_THREE_WAY
CORRECT_SCORE
TOTAL_GOALS
HALF_FULL
```

MVP 仍只计算 `THREE_WAY`。`ThreeWayProbability`、`ThreeWayMarketOdds` 和 `ThreeWayFixedBonus` 保留。

### 2. Fusion

新增统一 `FusionPolicy` 边界和第二个确定性策略：

```text
QUANT_ONLY_V1
MARKET_QUANT_BLEND_V1
```

后者使用配置中的 `quant_weight`，使未来引入 `P_llm` 时无需修改上层调用。

### 3. Ticket

明确：

```text
Ticket = 顶层票
AtomicBet = 内部计奖子注
```

Ticket 新增长期字段 `atomic_bet_count` 和聚合收益，不再假设每张 Ticket 永远只有一个联合概率。

### 4. Evidence

新增 `Evidence` 和 `EvidenceSnapshot`。LLM 只能读取在 AnalysisRun 截止时间前已经 available 的冻结 Evidence。

### 5. Money

明确：

```text
base_stake_fen = 200
max_multiplier = 50
max_ticket_stake_fen = 600000
1 <= multiplier <= max_multiplier
stake_fen = atomic_bet_count * 200 * multiplier
stake_fen <= max_ticket_stake_fen
```

Optimizer 不再拥有任意金额决策变量，规则参数由版本化 PayoutPolicy / BettingRules 提供。

## 数据库差异

### 1. 赔率和奖金

上一版固定 `home/draw/away` 三列调整为：

```text
market_odds_snapshots + market_odds_quotes
sporttery_bonus_snapshots + sporttery_bonus_quotes
```

Snapshot Header 增加：

```text
market_key
market_type
handicap_value
```

Quote 表通过 `selection_key` 表达不同市场方向。

### 2. 概率

概率表增加 `market_type` 和 `handicap_value`，概率值调整为 Outcome 明细：

```text
market_probability_outcomes
quant_prediction_outcomes
final_prediction_outcomes
```

概率和候选的业务唯一约束统一使用非空 `market_key`，不直接依赖 nullable `handicap_value`。

### 3. Ticket

移除 Ticket 对单一联合概率和单一组合赔率的长期依赖，增加：

```text
atomic_bet_count
base_stake_fen
payout_policy_version
aggregate expected metrics
```

未来再新增：

```text
atomic_bets
atomic_bet_legs
```

### 4. Evidence

真实 LLM 阶段新增：

```text
evidence
evidence_snapshots
evidence_snapshot_items
```

这些表不在 MVP 创建。

## 接口差异

### FusionPolicy

```python
class FusionPolicy(Protocol):
    def fuse(
        self,
        inputs: FusionInputs,
        config: FusionConfig,
    ) -> FinalPrediction:
        ...
```

MVP 必须对两个策略执行同一套 Contract Tests。

### Betting Engine

输入和输出全部携带 `MarketKey`。未实现市场返回 `UNSUPPORTED_MARKET`，不能误走1X2逻辑。

### Portfolio Optimizer

Optimizer 输出每张 Ticket 的正整数 `multiplier`。`stake_fen` 由 PayoutPolicy 推导，而不是由 Optimizer 任意填写。

### LLMStrategyProvider

Provider 主接口不变，但 MatchContext 增加冻结的 `evidence_snapshot_id`。LLM 输出新增允许的语义字段：

```text
preferred_outcomes
avoid_outcomes
counter_scenarios
scenario_relationships
```

这些字段不会直接创建投注候选或决定 `NO_BET`。

## 保持不变

- Python 3.12+ 模块化单体和六边形架构。
- SQLite、CLI 和 append-only Analysis Snapshot。
- 国际市场赔率与竞彩固定奖金类型隔离。
- `available_at_utc` 防止 point-in-time 泄漏。
- 正式 AnalysisRun 封存后不可修改。
- 预算是上限，允许未使用资金和 `NO_BET`。
- Ticket 数量约束作用于顶层 Ticket；默认 preferred 为4、absolute 为8，均由冻结配置决定。
- LLM confidence 不直接作为融合权重。
- MVP 使用独立比赛假设。
- Market 和 Quant 都不可用时，LLM 不能单独驱动投注。

## 结论

六项修订已纳入文档和 ADR。总体架构保持稳定，可以在确认文档后进入 MVP 工程骨架和实现阶段。
