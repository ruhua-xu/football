# Domain Model 与数据库设计

## 设计原则

- Domain Model 不依赖 SQLAlchemy、HTTP SDK 或具体 LLM SDK。
- 所有核心计算使用内部 ID，不使用第三方比赛 ID。
- 金额使用整数分，赔率和概率使用 `Decimal`。
- 国际市场赔率和竞彩固定奖金保持不同类型、不同表。
- 所有外部事实和正式分析结果采用 append-only 语义。
- 通用模型支持多市场，MVP 业务计算只启用 `THREE_WAY`。
- `0.4.0` 持久化只支持 SQLite；SQLAlchemy 类型不表示其他数据库兼容承诺。

## 0.4.0 历史模型交叉引用

本文继续描述决策图的基础 Domain Model。Historical Archive、MatchResult、Settlement、BacktestRun/Slice/Metrics 的规范字段、时间语义、coverage 和血缘以 [backtest_v1_contract.md](backtest_v1_contract.md) 为准；设计理由与来源研究保留在 [historical_data_backtest.md](historical_data_backtest.md)，模块边界见 [architecture.md](architecture.md)。

核心关系如下：

```text
HISTORICAL_ARCHIVE_V1 Manifest/provenance
    -> cutoff-selected decision inputs
    -> immutable base AnalysisRun
    -> frozen Ticket / Portfolio / Risk

evaluation cutoff
    -> append-only MatchResult
    -> Ticket Settlement / Portfolio Settlement
    -> BacktestSlice / BacktestMetrics
```

- Archive V1 将 `FIXTURES`、`MARKET_ODDS`、`SPORTTERY_BONUS`、`MANUAL_QUANT`、`MATCH_RESULTS` 和 `PROVIDER_MAPPINGS` 分文件保存；import 只登记 `MANIFEST_PROVENANCE_ONLY`，不是全量 payload 入库。
- `LIVE_STRICT` 与 `SOURCE_TIME_RESEARCH` 不能在同一运行或指标中混用；研究模式保留显式 retrospective/import provenance。
- `MatchResult` 是 evaluation source fact，不属于 AnalysisRun、AnalysisPacket 或 Review context。赛果与 Settlement 更正都通过 supersession 追加。
- Settlement V1 只计算 `BACKTEST`、`THREE_WAY`、简单2串1的 `WON`/`LOST`；不定义 `VOID` 或退款返还。
- walk-forward V1 只使用 base AnalysisRun 和一个预算 Portfolio；Domain/Schema 预留 PortfolioRevision settlement scope，不等于公开 CLI 已支持 Revision 回测。
- 实际历史持久化表、外键、partial index 和 SQLite trigger 由 Alembic 迁移 `f3a1c6d8e204` 定义，不在本文重复维护第二份易漂移 Schema。

## Market 模型

### MarketType

```python
class MarketType(str, Enum):
    THREE_WAY = "THREE_WAY"
    HANDICAP_THREE_WAY = "HANDICAP_THREE_WAY"
    CORRECT_SCORE = "CORRECT_SCORE"
    TOTAL_GOALS = "TOTAL_GOALS"
    HALF_FULL = "HALF_FULL"
```

### MarketKey

```python
@dataclass(frozen=True)
class MarketKey:
    market_type: MarketType
    handicap_value: Decimal | None = None
```

当前校验规则：

| MarketType | handicap_value |
|---|---|
| `THREE_WAY` | 必须为空 |
| `HANDICAP_THREE_WAY` | 必须存在；中国竞彩常见值包括 `-1`、`+1` |
| 其他预留类型 | MVP 不执行计算，字段按后续玩法规则扩展 |

`MarketKey` 必须可比较、可哈希，并参与 Snapshot、Prediction 和 SelectionCandidate 的业务唯一性判断。

持久化时同时保存非空规范化字符串：

```text
THREE_WAY
HANDICAP_THREE_WAY:-1
HANDICAP_THREE_WAY:+1
```

该 `market_key` 用于唯一约束，避免 nullable `handicap_value` 在 SQLite 中允许重复记录。`market_type` 和 `handicap_value` 继续作为可查询字段，并由 Repository 校验它们与规范化 key 一致。

### SelectionKey

`SelectionKey` 是某个 Market 内的标准方向标识。MVP 支持：

```text
HOME_WIN
DRAW
AWAY_WIN
```

`THREE_WAY` 和 `HANDICAP_THREE_WAY` 可以具有相同 SelectionKey，但由于 MarketKey 不同，它们不是同一个投注方向。

未来可增加：

```text
SCORE_2_1
TOTAL_GOALS_3
HALF_DRAW_FULL_HOME
```

SelectionKey 必须由对应 MarketType 的 Validator 校验，不能接受任意未登记字符串进入固定计算。

## 核心值对象

### ThreeWayProbability

```text
home_win: Decimal
draw: Decimal
away_win: Decimal
```

不变量：

```text
0 <= probability <= 1
abs(home_win + draw + away_win - 1) <= 1e-6
```

### ThreeWayMarketOdds

仅表示国际市场1X2或让球1X2赔率。每项必须大于1，不能用于实际竞彩返还。

### ThreeWayFixedBonus

仅表示中国竞彩固定奖金。每项必须大于1，不能作为国际市场共识的替代品。

### ProbabilityDistribution

通用概率边界：

```text
market_key: MarketKey
outcomes: Mapping[SelectionKey, Decimal]
```

MVP 中 `ProbabilityDistribution` 必须能够安全转换为 `ThreeWayProbability`。转换要求方向集合恰好为 HOME_WIN、DRAW、AWAY_WIN。

### Money 与 PayoutPolicy

```text
Money.amount_fen: int
PayoutPolicy.base_stake_fen: 200
PayoutPolicy.max_multiplier: 50
PayoutPolicy.max_ticket_stake_fen: 600000
```

`Money` 不允许使用 float。`PayoutPolicy` 负责官方投注单位、倍数上限、单票投入上限、奖金舍入和未来奖金上限规则。参数来自版本化配置并随 AnalysisRun 冻结。

## 核心实体

### Match

```text
match_id
competition_id
home_team_id
away_team_id
kickoff_at_utc
status
```

不变量：`home_team_id != away_team_id`。

### MarketOddsSnapshot

```text
snapshot_id
match_id
provider_id
bookmaker_id
market_key
quotes: Mapping[SelectionKey, Decimal]
captured_at_utc
available_at_utc
ingested_at_utc
source_snapshot_key
payload_hash
```

### SportteryBonusSnapshot

```text
snapshot_id
match_id
provider_id
sporttery_match_no
market_key
fixed_bonuses: Mapping[SelectionKey, Decimal]
sale_status
captured_at_utc
available_at_utc
ingested_at_utc
source_snapshot_key
payload_hash
```

### ManualQuantInput

```text
input_id
match_id
market_key
probabilities: Mapping[SelectionKey, Decimal]
available_at_utc
payload_hash
```

手工 `P_quant` 先作为不可变输入事实保存，再由 `QuantPrediction.manual_input_id` 引用。修改任一概率会生成新的 payload hash、输入 ID 和 AnalysisRun Manifest hash。

### Evidence

```text
evidence_id
match_id
category
statement
source_reference
observed_at_utc
available_at_utc
confirmation_status
reliability_level
payload_hash
```

Evidence 是外部动态信息的不可变标准化事实，不是 LLM 自由生成的内容。

### EvidenceSnapshot

```text
evidence_snapshot_id
match_id
as_of_at_utc
evidence_ids
snapshot_hash
created_at_utc
```

EvidenceSnapshot 冻结某次 MatchContext 实际可见的 Evidence 集合。

### Prediction

所有预测都必须关联：

```text
prediction_id
analysis_run_id
match_id
market_key
probability_distribution
method
method_version
generated_at_utc
```

具体类型：

| 类型 | 附加信息 |
|---|---|
| `MarketPrediction` | 输入赔率 Snapshot、去水和共识策略 |
| `QuantPrediction` | 手工输入 ID/hash、方法和版本 |
| `LLMMatchAssessment` | Evidence Snapshot、prompt/schema/model 元数据 |
| `FinalPrediction` | 上游预测引用、FusionPolicy、配置和 fallback code |

### Ticket 与 AtomicBet

```text
Ticket
├── ticket_id
├── pass_type
├── selections
├── multiplier
├── atomic_bet_count
├── stake
└── aggregate_quote
```

```text
AtomicBet
├── atomic_bet_id
├── ticket_id
├── legs
├── joint_probability
├── gross_payout_per_base_stake
└── settlement_state
```

MVP 的简单2串1满足：

```text
atomic_bet_count = 1
stake_fen = 1 * 200 * multiplier
```

未来系统票的 Ticket 可能有多个 AtomicBet，因此 Ticket 只保存聚合指标，不把单一 `joint_probability` 或单一 `combined_odds` 作为长期通用字段。

### Portfolio

```text
portfolio_id
analysis_run_id
budget_fen
tickets
total_stake_fen
unused_budget_fen
status
no_bet_reason
constraints
strategy_version
```

不变量：

```text
ticket_count <= constraints.absolute_max_tickets
total_stake_fen <= budget_fen
total_stake_fen = sum(ticket.stake_fen)
unused_budget_fen = budget_fen - total_stake_fen
NO_BET => ticket_count = 0 and total_stake_fen = 0
```

`constraints` 冻结 `preferred_max_tickets`、`absolute_max_tickets`、额外 Ticket 的价值门槛和操作复杂度惩罚。Domain 不再永久硬编码4张上限。

## 修订后的数据库 Schema

以下为既有决策图涉及变更的逻辑 Schema。UUID 由应用生成；概率和赔率使用 `NUMERIC`；金额使用整数分；枚举采用字符串和 CHECK。0.4.0 历史评估图不在此重复展开，见上方规范交叉引用和迁移 `f3a1c6d8e204`。

未列出的 `providers`、`bookmakers`、`competitions`、`teams`、`matches`、`provider_match_mappings`、`analysis_runs`、`analysis_run_matches`、`market_probability_inputs` 和 `portfolios` 延续已确认的第一版设计。

### 市场与赔率

```text
market_odds_snapshots(
    snapshot_id PK,
    internal_match_id FK,
    provider_id FK,
    bookmaker_id FK,
    market_key,
    market_type,
    handicap_value NULL,
    captured_at_utc,
    available_at_utc,
    ingested_at_utc,
    source_snapshot_key,
    payload_hash,
    UNIQUE(provider_id, source_snapshot_key)
)

market_odds_quotes(
    snapshot_id FK,
    selection_key,
    odds,
    PRIMARY KEY(snapshot_id, selection_key),
    CHECK(odds > 1)
)

sporttery_bonus_snapshots(
    snapshot_id PK,
    internal_match_id FK,
    provider_id FK,
    sporttery_match_no,
    market_key,
    market_type,
    handicap_value NULL,
    sale_status,
    captured_at_utc,
    available_at_utc,
    ingested_at_utc,
    source_snapshot_key,
    payload_hash,
    UNIQUE(provider_id, source_snapshot_key)
)

sporttery_bonus_quotes(
    snapshot_id FK,
    selection_key,
    fixed_bonus,
    PRIMARY KEY(snapshot_id, selection_key),
    CHECK(fixed_bonus > 1)
)
```

采用 Header + Quote 明细的原因是 `CORRECT_SCORE`、`TOTAL_GOALS` 和 `HALF_FULL` 不适合永久映射成 home/draw/away 三列。

### 概率预测

```text
manual_quant_inputs(
    input_id PK,
    internal_match_id FK,
    market_key,
    market_type,
    handicap_value NULL,
    available_at_utc,
    payload_hash,
    UNIQUE(internal_match_id, market_key, available_at_utc, payload_hash)
)

manual_quant_input_outcomes(
    input_id FK,
    selection_key,
    probability,
    PRIMARY KEY(input_id, selection_key)
)

market_probabilities(
    market_probability_id PK,
    analysis_run_id FK,
    internal_match_id FK,
    market_key,
    market_type,
    handicap_value NULL,
    devig_method,
    devig_version,
    overround,
    generated_at_utc,
    UNIQUE(analysis_run_id, internal_match_id, market_key)
)

market_probability_outcomes(
    market_probability_id FK,
    selection_key,
    probability,
    PRIMARY KEY(market_probability_id, selection_key)
)

quant_predictions(
    quant_prediction_id PK,
    analysis_run_id FK,
    internal_match_id FK,
    market_key,
    market_type,
    handicap_value NULL,
    manual_input_id FK,
    input_payload_hash,
    method,
    method_version,
    entered_at_utc,
    UNIQUE(analysis_run_id, internal_match_id, market_key)
)

quant_prediction_outcomes(
    quant_prediction_id FK,
    selection_key,
    probability,
    PRIMARY KEY(quant_prediction_id, selection_key)
)

final_predictions(
    final_prediction_id PK,
    analysis_run_id FK,
    internal_match_id FK,
    market_key,
    market_type,
    handicap_value NULL,
    market_probability_id NULL,
    quant_prediction_id NULL,
    llm_assessment_id NULL,
    fusion_policy,
    fusion_version,
    fusion_config_json,
    fallback_code,
    confidence,
    UNIQUE(analysis_run_id, internal_match_id, market_key)
)

final_prediction_outcomes(
    final_prediction_id FK,
    selection_key,
    probability,
    PRIMARY KEY(final_prediction_id, selection_key)
)
```

概率总和等跨行约束由 `ProbabilityDistribution` 和 Repository 事务校验；数据库继续对单项执行 `0 <= probability <= 1`。

### 投注候选

```text
bet_candidates(
    candidate_id PK,
    analysis_run_id FK,
    internal_match_id FK,
    final_prediction_id FK,
    sporttery_bonus_snapshot_id FK,
    market_key,
    selection_key,
    probability_used,
    fixed_bonus,
    break_even_probability,
    ev,
    eligibility_status,
    rejection_code,
    UNIQUE(analysis_run_id, internal_match_id, market_key, selection_key)
)
```

Repository 必须验证 FinalPrediction、SportteryBonusSnapshot 和 BetCandidate 的 `internal_match_id` 与 `market_key` 完全相同。

### Evidence

Evidence 表在真实 LLM 阶段创建，MVP 只保留 Domain 和 Port：

```text
evidence(
    evidence_id PK,
    internal_match_id FK,
    provider_id FK,
    category,
    statement,
    source_reference,
    observed_at_utc,
    available_at_utc,
    ingested_at_utc,
    confirmation_status,
    reliability_level,
    payload_hash
)

evidence_snapshots(
    evidence_snapshot_id PK,
    internal_match_id FK,
    as_of_at_utc,
    snapshot_hash,
    created_at_utc
)

evidence_snapshot_items(
    evidence_snapshot_id FK,
    evidence_id FK,
    PRIMARY KEY(evidence_snapshot_id, evidence_id)
)
```

### Ticket

```text
tickets(
    ticket_id PK,
    portfolio_id FK,
    ticket_no,
    pass_type,
    role NULL,
    multiplier,
    atomic_bet_count,
    base_stake_fen,
    stake_fen,
    expected_gross_payout,
    expected_profit,
    expected_roi,
    probability_any_payout NULL,
    max_gross_payout_fen NULL,
    payout_policy_version,
    CHECK(ticket_no > 0),
    CHECK(multiplier > 0),
    CHECK(atomic_bet_count > 0),
    CHECK(base_stake_fen = 200),
    CHECK(stake_fen = atomic_bet_count * base_stake_fen * multiplier)
)

ticket_legs(
    ticket_id FK,
    leg_no,
    candidate_id FK,
    internal_match_id FK,
    PRIMARY KEY(ticket_id, leg_no),
    UNIQUE(ticket_id, internal_match_id)
)
```

`max_multiplier` 和 `max_ticket_stake_fen` 来自 AnalysisRun 的版本化规则配置，因其可能随规则版本变化，由 Domain Service 和 Repository 在封存事务中校验，不散落为数据库常量。

`portfolios.strategy_config_json` 保存冻结的 Strategy Profile。Ticket 数量上限由 Domain Service 和 Repository 根据该配置校验，数据库只保证 `ticket_no > 0`。

未来新增而非 MVP 创建：

```text
atomic_bets
atomic_bet_legs
```

这些表保存复杂过关展开和各子注的独立计奖信息，不要求重构顶层 Ticket。

## 时间和不可变约束

输入某次 AnalysisRun 的事实必须满足：

```text
available_at_utc <= analysis_run.as_of_at_utc
analysis_run.as_of_at_utc <= analysis_run.started_at_utc <= analysis_run.completed_at_utc
```

运行保存实际选中的赔率、奖金和手工概率输入 ID，并持久化规范化 `input_manifest_json`、版本和 SHA-256。Repository 读取 Manifest 时重新校验 hash。重放不得重新查询最新数据。正式 AnalysisRun、全部后代、Portfolio 和 Ticket 封存后不可更改；重新分析创建新 ID。

历史评估使用独立且更晚的 `evaluation_as_of_at_utc`。MatchResult、Ticket/Portfolio Settlement、BacktestSlice 和指标快照不进入决策 Manifest，也不能反向更新 `P_final`、候选、Ticket、Portfolio 或 Risk。
