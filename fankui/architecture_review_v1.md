# 足球比赛智能分析与竞彩串关决策系统

> 历史版本：本文已被 `yaoqiu/yaoqiu0831054.md` 修订。当前有效设计以 `architecture.md`、`data_model.md`、`betting_model.md`、`llm_strategy.md` 和 `revision_report_0831054.md` 为准。

## 第一版架构评审摘要

> 基于 `yaoqiu.md` 的首轮架构评审。本阶段只确认架构、领域模型、数据库和接口边界，不实现完整系统。

## 1. 架构结论

第一版采用：

```text
Python 3.12+ 模块化单体
+ 六边形架构（Domain / Application / Ports / Adapters）
+ SQLite
+ CLI
+ 追加式、可回放的分析快照
```

系统边界止于：

```text
PortfolioRecommendation | NO_BET
```

不包含自动下注、支付、账户登录和出票确认。

核心原则：

```text
固定程序负责计算
LLM 负责语义判断
历史数据负责验证
```

## 2. 关键评审裁决

| 问题 | 第一版裁决 |
|---|---|
| 国际赔率和竞彩固定奖金容易混用 | `P_market` 只由国际市场赔率产生；竞彩固定奖金只用于 EV、串关奖金和资金配置 |
| MVP 没有明确 `P_final` | MVP 使用 `fusion_policy=QUANT_ONLY_V1`，明确令 `P_final=P_quant` |
| LLM 可能越过固定程序边界 | LLM 只输出 `P_llm`、剧本、语义风险和证据，不输出 EV、金额或最终投注候选 |
| 只保存最新赔率会破坏回测 | 所有赔率快照只追加，不覆盖历史数据 |
| `captured_at` 不能完全防止数据泄漏 | 所有时点数据增加 `available_at_utc`；分析只使用 `available_at <= as_of_at` 的数据 |
| 分析记录混入赛果和利润 | 分析快照不可修改；赛果、模拟结算和实际投注以后单独建模 |
| 预算可能被误解为必须花完 | 预算是支出上限，允许保留现金，允许输出 `NO_BET` |
| 最多4票定义不清 | 指最多4张顶层 Ticket；倍数和内部原子注不算额外票 |
| LLM confidence 直接作为权重不可靠 | confidence 只是输入特征，必须由固定融合策略和历史校准处理 |
| 相关性修正缺乏数据基础 | MVP 明确采用独立假设；风险标签暂不直接修改联合概率 |

## 3. 总体架构

```text
CLI
 |
 v
RunAnalysis Use Case
 |
 +-- Provider Ports
 |    +-- MockFixtureProvider
 |    +-- MockMarketOddsProvider
 |    +-- MockSportteryProvider
 |
 +-- 数据标准化与内部比赛身份
 +-- 追加保存原始快照
 +-- AnalysisRun(as_of_at)
 +-- P_market
 +-- P_quant
 +-- LLM Assessment（MVP 禁用）
 +-- FusionPolicy -> P_final
 +-- Betting Engine
 |    +-- 单场 EV
 |    +-- 2串1候选
 +-- Portfolio Optimizer
 |    +-- 预算约束
 |    +-- 最多4票
 |    +-- NO_BET
 v
SQLite + CLI Recommendation
```

| 模块 | 职责 |
|---|---|
| Domain | 领域对象、不变量、概率和竞彩计奖规则 |
| Application | 编排一次分析，不依赖具体 API、数据库或 LLM SDK |
| Ports | 数据 Provider、Repository、LLM 等抽象协议 |
| Infrastructure | SQLite、Mock Provider、未来真实 API 和 LLM Adapter |
| Interfaces | CLI 输入和展示 |
| Backtest | 后续读取冻结快照执行，不反向修改历史分析 |

## 4. 推荐目录结构

```text
football/
├── README.md
├── pyproject.toml
├── alembic.ini
├── .env.example
├── .gitignore
│
├── config/
│   ├── default.toml
│   └── mvp.toml
│
├── docs/
│   ├── architecture.md
│   ├── data_model.md
│   ├── betting_model.md
│   ├── llm_strategy.md
│   └── decisions/
│       ├── 0001-modular-monolith.md
│       ├── 0002-separate-market-odds-and-sporttery.md
│       └── 0003-append-only-analysis-snapshots.md
│
├── src/football_system/
│   ├── __init__.py
│   ├── config.py
│   │
│   ├── domain/
│   │   ├── ids.py
│   │   ├── enums.py
│   │   ├── values.py
│   │   ├── match.py
│   │   ├── odds.py
│   │   ├── prediction.py
│   │   ├── analysis.py
│   │   ├── betting.py
│   │   └── services/
│   │       ├── probabilities.py
│   │       ├── payout.py
│   │       ├── ticket_generation.py
│   │       └── portfolio_policy.py
│   │
│   ├── application/
│   │   ├── ports/
│   │   │   ├── data_providers.py
│   │   │   ├── repositories.py
│   │   │   └── llm_strategy.py
│   │   ├── llm_orchestrator.py
│   │   └── run_analysis.py
│   │
│   ├── infrastructure/
│   │   ├── database/
│   │   │   ├── session.py
│   │   │   ├── models.py
│   │   │   ├── repositories.py
│   │   │   └── migrations/
│   │   ├── providers/mock/
│   │   │   ├── fixtures.py
│   │   │   ├── market_odds.py
│   │   │   └── sporttery.py
│   │   └── llm/
│   │       ├── disabled.py
│   │       └── fixture.py
│   │
│   └── interfaces/
│       └── cli.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── e2e/
│
└── data/fixtures/
    └── mvp_matches.json
```

第一阶段不创建真实 OpenAI Adapter、API-Football、复杂 Identity Resolver、完整回测和 Dashboard。

## 5. Domain Model

### 5.1 核心值对象

| 模型 | 字段 | 主要不变量 |
|---|---|---|
| `ThreeWayProbability` | `home`, `draw`, `away` | 每项位于 `[0,1]`，总和容差内等于1 |
| `ThreeWayMarketOdds` | `home`, `draw`, `away` | 使用 `Decimal`，只能用于市场分析 |
| `ThreeWayFixedBonus` | `home`, `draw`, `away` | 使用 `Decimal`，只能用于竞彩实际计价 |
| `Money` | `amount_fen`, `currency` | 金额使用整数分，不使用 float |
| `KnowledgeCutoff` | `as_of_at_utc` | 必须是带时区 UTC |
| `DataQuality` | 完整度、时效性、冲突和缺失项 | 由固定程序计算，LLM 不能修改 |
| `RiskTag` | 固定枚举 | 不接受 LLM 自由创建标签 |

`ThreeWayMarketOdds` 和 `ThreeWayFixedBonus` 必须保持不同类型，防止国际赔率被误用于实际返还计算。

### 5.2 核心实体

| 模型 | 核心职责 |
|---|---|
| `Competition` | 赛事稳定身份 |
| `Team` | 球队稳定身份和球队类型 |
| `Match` | 内部比赛身份、主客队、开赛时间和状态 |
| `MarketOddsSnapshot` | 国际市场赔率不可变快照 |
| `SportteryBonusSnapshot` | 中国竞彩固定奖金不可变快照 |
| `MarketPrediction` | `P_market`、去水策略和输入赔率快照 |
| `QuantPrediction` | `P_quant`、模型或手工输入版本 |
| `LLMMatchAssessment` | `P_llm`、剧本、语义风险和证据 |
| `FinalPrediction` | `P_final`、上游引用、融合策略和降级原因 |
| `SelectionCandidate` | 单场方向、竞彩奖金、概率、EV 和资格状态 |
| `Ticket` | 串关类型、腿、倍数、投入和预期指标 |
| `Portfolio` | 预算、0至4张 Ticket、未使用预算和结果状态 |
| `AnalysisRun` | 分析时点、输入清单、配置快照和代码版本 |

### 5.3 聚合边界

| 聚合根 | 边界 |
|---|---|
| `Match` | 只管理稳定比赛身份，不承载无限增长的赔率历史 |
| `MarketOddsSnapshot` | 独立、不可变、只追加 |
| `SportteryBonusSnapshot` | 与国际市场赔率物理隔离 |
| `AnalysisRun` | 冻结一次分析的输入、配置、版本和预测结果 |
| `Portfolio` | 管理 Ticket、TicketLeg、预算和最多4票约束 |

关键不变量：

```text
home_team_id != away_team_id
所有核心计算只使用 internal_match_id
2串1必须包含两个不同比赛
同一张票不能重复同一比赛
总投入 <= budget
ticket_count <= 4
NO_BET 时 ticket_count = 0
正式 AnalysisRun 封存后不可修改
```

## 6. 数据库 Schema

### 6.1 MVP 表

```text
providers(
    provider_id PK,
    code UNIQUE,
    name,
    provider_kind
)

bookmakers(
    bookmaker_id PK,
    code UNIQUE,
    name
)

competitions(
    competition_id PK,
    canonical_key UNIQUE,
    name,
    country_code
)

teams(
    team_id PK,
    canonical_key UNIQUE,
    name,
    team_type
)

matches(
    internal_match_id PK,
    competition_id FK,
    home_team_id FK,
    away_team_id FK,
    kickoff_at_utc,
    status,
    available_at_utc,
    created_at_utc
)

provider_match_mappings(
    mapping_id PK,
    provider_id FK,
    external_namespace,
    external_match_id,
    internal_match_id FK,
    resolution_method,
    confidence,
    available_at_utc,
    supersedes_mapping_id NULL
)

market_odds_snapshots(
    snapshot_id PK,
    internal_match_id FK,
    provider_id FK,
    bookmaker_id FK,
    home_odds,
    draw_odds,
    away_odds,
    captured_at_utc,
    available_at_utc,
    ingested_at_utc,
    source_snapshot_key,
    payload_hash
)

sporttery_bonus_snapshots(
    snapshot_id PK,
    internal_match_id FK,
    provider_id FK,
    sporttery_match_no,
    home_bonus,
    draw_bonus,
    away_bonus,
    sale_status,
    captured_at_utc,
    available_at_utc,
    ingested_at_utc,
    source_snapshot_key,
    payload_hash
)

analysis_runs(
    analysis_run_id PK,
    run_kind,
    as_of_at_utc,
    status,
    started_at_utc,
    completed_at_utc,
    pipeline_version,
    code_revision,
    config_json,
    config_hash,
    input_manifest_hash,
    replay_of_run_id NULL
)

analysis_run_matches(
    analysis_run_id FK,
    internal_match_id FK,
    context_json,
    context_hash,
    PRIMARY KEY(analysis_run_id, internal_match_id)
)

market_probabilities(
    market_probability_id PK,
    analysis_run_id FK,
    internal_match_id FK,
    p_home,
    p_draw,
    p_away,
    devig_method,
    devig_version,
    overround,
    generated_at_utc
)

market_probability_inputs(
    market_probability_id FK,
    market_odds_snapshot_id FK,
    PRIMARY KEY(market_probability_id, market_odds_snapshot_id)
)

quant_predictions(
    quant_prediction_id PK,
    analysis_run_id FK,
    internal_match_id FK,
    p_home,
    p_draw,
    p_away,
    method,
    method_version,
    entered_at_utc
)

final_predictions(
    final_prediction_id PK,
    analysis_run_id FK,
    internal_match_id FK,
    market_probability_id NULL,
    quant_prediction_id NULL,
    llm_assessment_id NULL,
    p_home,
    p_draw,
    p_away,
    fusion_policy,
    fusion_version,
    fallback_code,
    confidence
)

bet_candidates(
    candidate_id PK,
    analysis_run_id FK,
    internal_match_id FK,
    final_prediction_id FK,
    sporttery_bonus_snapshot_id FK,
    selection,
    probability_used,
    quoted_bonus,
    break_even_probability,
    ev,
    eligibility_status,
    rejection_code
)

portfolios(
    portfolio_id PK,
    analysis_run_id FK,
    budget_fen,
    total_stake_fen,
    unused_budget_fen,
    status,
    no_bet_reason,
    strategy_version,
    strategy_config_json
)

tickets(
    ticket_id PK,
    portfolio_id FK,
    ticket_no,
    pass_type,
    role NULL,
    multiplier,
    stake_fen,
    quoted_combined_bonus,
    joint_probability,
    potential_gross_payout_fen,
    expected_gross_payout,
    expected_profit,
    expected_roi
)

ticket_legs(
    ticket_id FK,
    leg_no,
    candidate_id FK,
    internal_match_id FK,
    PRIMARY KEY(ticket_id, leg_no)
)
```

### 6.2 主要约束

```text
UNIQUE(provider_id, external_namespace, external_match_id)
UNIQUE(provider_id, source_snapshot_key)
UNIQUE(portfolio_id, ticket_no)
UNIQUE(ticket_id, internal_match_id)

CHECK(home_team_id <> away_team_id)
CHECK(odds > 1)
CHECK(0 <= probability AND probability <= 1)
CHECK(abs(p_home + p_draw + p_away - 1) <= 0.000001)
CHECK(ticket_no BETWEEN 1 AND 4)
CHECK(multiplier > 0)
```

SQLite 每个连接必须启用：

```text
PRAGMA foreign_keys = ON
```

### 6.3 第四阶段 LLM 表

第四阶段再创建：

```text
llm_invocations
llm_attempts
llm_artifacts
llm_assessments
```

分别保存逻辑调用、物理重试、prompt/schema/原始响应制品和通过验证的 `P_llm`。

## 7. Provider 接口

采用小型能力协议，不建立巨型 Provider：

```python
class FixtureProvider(Protocol):
    async def fetch_fixtures(
        self,
        query: FixtureQuery,
    ) -> Sequence[ExternalFixture]: ...


class MarketOddsProvider(Protocol):
    async def fetch_market_odds(
        self,
        query: OddsQuery,
    ) -> Sequence[ExternalMarketOddsSnapshot]: ...


class SportteryProvider(Protocol):
    async def fetch_fixed_bonus(
        self,
        query: SportteryQuery,
    ) -> Sequence[ExternalSportteryOffer]: ...
```

Provider 返回供应商中立 DTO，不返回 ORM Model。外部 ID 只存在于 Provider DTO 和 Mapping 层。

## 8. 概率数据流

| 概率 | MVP 来源 | 持久化要求 |
|---|---|---|
| `P_market` | 国际1X2赔率去水 | 保存三项概率、输入快照和去水版本 |
| `P_quant` | 人工输入 | 保存三项概率、录入时间和方法版本 |
| `P_llm` | MVP 缺席 | 不用均匀概率或复制其他概率伪造 |
| `P_final` | `QUANT_ONLY_V1` | 明确记录融合策略和空 LLM 引用 |

后续 LLM 失败时：

```text
P_final = fuse_without_llm(P_market, P_quant)
```

当 Market 和 Quant 都不可用时，不允许 LLM 单独驱动投注，应输出 `NO_BET`。

## 9. LLMStrategyProvider

### 9.1 两层接口

Provider 负责供应商调用，Orchestrator 负责 prompt、重试、审计、校验和降级：

```python
class LLMStrategyProvider(Protocol):
    async def generate_assessment(
        self,
        request: LLMProviderRequest,
    ) -> LLMProviderResponse:
        ...


class LLMAssessmentService(Protocol):
    async def assess_match(
        self,
        context: MatchContext,
        *,
        analysis_run_id: UUID,
        deadline_utc: datetime,
    ) -> LLMAssessmentOutcome:
        ...
```

`LLMProviderResponse` 是不可信原始响应，必须经过 Validator 后才能产生领域对象。

### 9.2 MatchContext

```text
MatchContext
├── schema_version
├── snapshot_id
├── match_id
├── as_of_at_utc
├── kickoff_at_utc
├── competition
├── home_team
├── away_team
├── sporttery_bonus
├── market_odds_summary
├── odds_history_summary
├── league_table
├── recent_form
├── home_form
├── away_form
├── xg / xga
├── injuries
├── suspensions
├── expected_lineup
├── confirmed_lineup
├── rest_days
├── schedule_density
├── p_market
├── p_quant
├── evidence[]
└── data_quality
```

动态证据必须包含：

```text
evidence_id
category
statement
source_reference
observed_at_utc
available_at_utc
confirmation_status
reliability_level
```

发送给 LLM 的白名单视图不包含预算、EV、融合权重、风险限制或资金配置。

### 9.3 LLM 输出

```text
LLMMatchAssessmentPayload
├── p_llm
│   ├── home_win
│   ├── draw
│   └── away_win
├── assessment_confidence
├── scenarios
│   ├── MAIN
│   ├── SECONDARY
│   └── UPSET
├── semantic_factors
├── risk_score
├── risk_tags
├── market_interpretation
├── reasoning_summary
└── limitations
```

LLM 不允许输出：

```text
P_final
EV
fair_odds
stake
budget
fusion_weight
main_bet_candidates
hedge_candidates
ticket_role
parlay_structure
NO_BET
strategy_config
```

LLM 可以描述主胜失败后的平局剧本，但是否创建保护票必须由固定 Betting Engine 和 Optimizer 决定。

### 9.4 校验与降级

```text
严格 JSON Schema
additionalProperties = false
禁止 NaN / Infinity
概率位于 [0,1]
三项总和误差 <= 1e-6
剧本 outcome 不重复
evidence_id 必须存在
响应返回后重新检查 context 是否过期
```

结果使用明确类型：

```text
ValidLLMAssessment
UnavailableLLMAssessment(reason_code)
```

禁止用 `[1/3, 1/3, 1/3]` 表示调用失败。

每次调用至少审计：

```text
invocation_id
attempt_id
context_digest
request_fingerprint
prompt/schema/version/digest
validator_version
deployment_route
requested_model
resolved_model
raw_response
validation_report
failure_code
duration_ms
token_usage
```

不得保存 API Key 或执行模型返回的代码、SQL和配置。

## 10. Betting Engine 与 Portfolio Optimizer

```python
BettingEngine.build_candidates(
    final_predictions,
    sporttery_bonus_snapshots,
    payout_policy,
) -> Sequence[TicketCandidate]
```

```python
PortfolioOptimizer.optimize(
    ticket_candidates,
    budget,
    constraints,
) -> PortfolioRecommendation | NoBet
```

Betting Engine 负责：

```text
竞彩报价合法性
单场 EV
2串1枚举
官方奖金计算
联合概率
TicketCandidate
```

Portfolio Optimizer 负责：

```text
选择最终票
整数倍数
预算分配
最多4票
留存现金
NO_BET
```

MVP 独立假设下，简单2串1：

```text
q = p1 * p2
单注成本 = 2元
单注毛返还 = 官方舍入(2 * SP1 * SP2)
期望利润 = q * 单注毛返还 - 2
ROI = 期望利润 / 2
```

竞彩奖金舍入应由版本化 `PayoutPolicy` 实现，不能直接依赖普通浮点 `round`。

## 11. MVP 范围

包含：

- 5至10场 Mock 比赛。
- 国际市场赔率和竞彩固定奖金两套 Mock 数据。
- SQLite、SQLAlchemy 和 Alembic。
- `P_market` 基础去水。
- 手工三项 `P_quant`。
- `QUANT_ONLY_V1` 的 `P_final`。
- 胜平负和简单2串1。
- 100元、200元和自定义预算。
- 0至4张最终票和 `NO_BET`。
- CLI 和不可变 AnalysisRun。
- LLM 接口、Disabled Provider 和 Fixture Provider，不接真实模型。
- 核心单元测试、数据库测试、Provider Contract 测试和端到端测试。

不包含：

- 真实网站和 API。
- 模糊球队匹配。
- GPT 实际调用。
- ML 训练。
- 3串4、4串11和复式。
- 自动 HEDGE、LONGSHOT 分配。
- 相关性修正和 Monte Carlo。
- 完整回测和 Dashboard。
- Web 前端和自动下注。

## 12. 第一阶段开发顺序

1. 写入架构文档和三项 ADR。
2. 建立 `pyproject.toml`、配置和工程骨架。
3. 实现值对象、Match、赔率和概率模型。
4. 建立 SQLite Schema、迁移和 Repository。
5. 实现三个 Mock Provider。
6. 实现 `P_market`、手工 `P_quant` 和 `QUANT_ONLY_V1`。
7. 实现竞彩奖金规则、EV 和2串1枚举。
8. 实现预算约束、最多4票和 `NO_BET`。
9. 实现 CLI 和不可变 AnalysisRun。
10. 完成测试、黄金样例和只读重放验证。

## 13. 进入实现前需要确认

1. `P_market` 只使用国际赔率，竞彩固定奖金只用于实际计价。
2. MVP 的 `P_final` 采用 `QUANT_ONLY_V1`。
3. LLM 不直接输出投注候选、金额和对冲票。
4. 预算是上限，不要求花完，允许输出0张票。
5. 最多4票按顶层 Ticket 计算，倍数不算额外票。
