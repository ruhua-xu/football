# 系统架构

## 状态

- 状态：已确认，按 `yaoqiu0831054.md` 修订
- 架构形态：Python 3.12+ 模块化单体
- 边界模式：六边形架构
- 首期存储：SQLite
- 首期入口：CLI
- 历史策略：Append-only Analysis Snapshot

本次修订不改变总体架构，只补充多市场、融合策略、LLM Evidence、Ticket/AtomicBet 和实际投注单位的演进边界。

## 系统边界

系统生成研究和决策辅助结果：

```text
PortfolioRecommendation | NO_BET
```

系统不负责自动下注、账户登录、支付、出票确认和真实资金托管。

## 模块边界

| 模块 | 职责 |
|---|---|
| Domain | 市场、概率、竞彩计奖、Ticket、Portfolio 等业务对象和不变量 |
| Application | 编排数据冻结、预测、融合、候选生成和组合优化 |
| Ports | 数据 Provider、Evidence Provider、Repository、LLM Provider 等协议 |
| Infrastructure | SQLite、Mock Provider、未来真实 API 和 LLM Adapter |
| Interfaces | CLI 输入和结果展示 |
| Backtest | 后续使用冻结快照回放，不修改历史 AnalysisRun |

固定程序负责概率校验、融合、EV、计奖、串关、资金约束和 `NO_BET`。LLM 只负责基于冻结证据给出语义概率、比赛剧本和方向关系。

## 主数据流

```text
Fixture / Odds / Sporttery Provider
              |
              v
标准化与 internal_match_id
              |
              v
Append-only Source Snapshots
              |
              +-----------------------------+
              |                             |
              v                             v
      P_market / P_quant          Evidence Provider
                                            |
                                            v
                                  Evidence Snapshot
                                            |
                                            v
                                     MatchContext
                                            |
                                            v
                                  LLMStrategyProvider
                                            |
                                            v
                                          P_llm
              |                             |
              +--------------+--------------+
                             v
                    Versioned FusionPolicy
                             |
                             v
                           P_final
                             |
                             v
                       Betting Engine
                             |
                             v
                     Ticket Candidates
                             |
                             v
                    Portfolio Optimizer
                             |
                             v
             PortfolioRecommendation | NO_BET
```

MVP 不接真实 Evidence Provider 和 LLM。对应端口与数据模型提前定义，运行时使用 Disabled Provider，`P_llm` 明确缺席。

## Market 抽象

系统使用 `MarketKey` 标识一个可定价市场，而不是在核心接口中默认所有数据都是胜平负。

```text
MarketKey
├── market_type
└── handicap_value
```

首批预留 `MarketType`：

```text
THREE_WAY
HANDICAP_THREE_WAY
CORRECT_SCORE
TOTAL_GOALS
HALF_FULL
```

MVP 只实现 `THREE_WAY`。`HANDICAP_THREE_WAY` 只完成模型、持久化字段和校验边界，不实现概率、EV 和计奖计算。

`ThreeWayProbability`、`ThreeWayMarketOdds` 和 `ThreeWayFixedBonus` 继续作为强类型值对象。持久化层采用 Snapshot Header + Outcome Quote 明细，避免数据库永久绑定三个固定列。

详细模型见 [data_model.md](data_model.md) 和 [0001-market-abstraction.md](decisions/0001-market-abstraction.md)。

## FusionPolicy

上层分析流程只依赖统一的 `FusionPolicy` 接口：

```text
FusionInputs -> FusionPolicy -> FinalPrediction
```

```python
@dataclass(frozen=True)
class FusionInputs:
    match_id: MatchId
    market_key: MarketKey
    p_market: ProbabilityDistribution | None
    p_quant: ProbabilityDistribution | None
    p_llm: ProbabilityDistribution | None
    data_quality: DataQuality


class FusionPolicy(Protocol):
    policy_id: str
    version: str

    def fuse(
        self,
        inputs: FusionInputs,
        config: FusionConfig,
    ) -> FinalPrediction:
        ...
```

MVP 定义并测试两个确定性策略：

```text
QUANT_ONLY_V1
MARKET_QUANT_BLEND_V1
```

默认运行策略仍为 `QUANT_ONLY_V1`。第二个策略用于验证接口和配置边界：

```text
P_final = w * P_quant + (1 - w) * P_market
```

`w` 来自冻结的运行配置，必须满足 `0 <= w <= 1`。加入 `P_llm` 时只新增策略实现，不修改上层调用方式。

`QUANT_ONLY_V1` 要求有效 `P_quant`；`MARKET_QUANT_BLEND_V1` 要求有效且 MarketKey 一致的 `P_market` 和 `P_quant`。输入缺失时策略抛出统一 `FusionInputsUnavailable`，由应用层执行配置中明确声明的 fallback，并把 fallback code 写入 FinalPrediction，禁止策略内部静默猜测。

详细决策见 [0002-versioned-fusion-policies.md](decisions/0002-versioned-fusion-policies.md)。

## Evidence 与 LLM

生产 LLM 分析禁止在推理时临时浏览互联网并直接形成 `P_llm`。动态信息必须先进入可审计的数据链：

```text
External Source
-> Evidence Provider / Collector
-> Append-only Evidence
-> Frozen Evidence Snapshot
-> MatchContext
-> LLM
```

只有满足以下条件的 Evidence 才能进入某次分析：

```text
evidence.available_at_utc <= analysis_run.as_of_at_utc
```

LLM 可以输出 `preferred_outcomes`、`avoid_outcomes`、`counter_scenarios` 和 `scenario_relationships`，但这些只是语义判断。是否成为投注候选仍由固定程序根据 `P_final`、竞彩固定奖金、EV、风险和预算决定。

详细边界见 [llm_strategy.md](llm_strategy.md) 和 [0004-frozen-evidence-for-llm.md](decisions/0004-frozen-evidence-for-llm.md)。

## Ticket 与 AtomicBet

`Ticket` 是用户看到的一张顶层竞彩票。`AtomicBet` 是某个过关方式按官方规则展开后的内部计奖组合。

```text
Ticket
├── pass_type
├── top-level selections
├── multiplier
└── AtomicBet[1..n]（未来展开）
```

简单2串1包含一个 AtomicBet。未来3串4、4串11会包含多个 AtomicBet，因此 Ticket 不以单个联合概率或单个组合赔率作为长期不变量。

MVP 暂不创建 AtomicBet 数据库表，只实现简单2串1。Ticket 仍保存 `atomic_bet_count` 和聚合后的收益指标，为后续展开预留稳定边界。

详细决策见 [betting_model.md](betting_model.md) 和 [0003-ticket-and-atomic-bet.md](decisions/0003-ticket-and-atomic-bet.md)。

## 时间与不可变性

数据库时间统一为 UTC。外部事实至少区分：

```text
observed_at_utc
captured_at_utc
available_at_utc
ingested_at_utc
```

`AnalysisRun.as_of_at_utc` 是知识截止时间。运行必须保存具体输入 Snapshot ID、配置内容和版本，不能在重放时重新查询“当前最新值”。应用层会再次校验所有输入的 `available_at_utc/captured_at_utc/ingested_at_utc`，不依赖 Provider 自律。

每次运行保存规范化 `input_manifest_json`、Manifest 版本与 SHA-256，同时保存配置原文/hash、包源码 revision、各来源 payload hash。赔率、奖金和手工概率事实均使用版本化 ID；重复 ID 的内容不一致会被拒绝。

正式 AnalysisRun 封存后，其输入、预测、候选、Portfolio 和所有后代表不允许插入、更新或删除；来源聚合采用 append-only 语义。SQLite 触发器与 Repository 双重校验这些约束。重新分析必须创建新 `analysis_run_id`。

## 版本化竞彩规则

PayoutPolicy 与 BettingRules 从配置加载并随 AnalysisRun 冻结：

```toml
[sporttery]
base_stake_fen = 200
max_multiplier = 50
max_ticket_stake_fen = 600000
```

固定程序统一校验：

```text
1 <= multiplier <= max_multiplier
stake_fen = atomic_bet_count * base_stake_fen * multiplier
stake_fen <= max_ticket_stake_fen
```

Optimizer 只能选择合法正整数 multiplier，不能内置或绕过这些规则。

## Strategy Profile

Ticket 数量由随 AnalysisRun 冻结的 Portfolio Strategy Profile 决定：

```toml
[portfolio]
preferred_max_tickets = 4
absolute_max_tickets = 8
extra_ticket_min_roi = "0.20"
operational_complexity_penalty = "0.01"
```

Optimizer 默认在 preferred 范围内寻找方案。超出 preferred 的 Ticket 必须在扣除操作复杂度惩罚后仍满足更严格的价值门槛，并带来新的比赛暴露；任何情况下不得超过 absolute 上限。`NO_BET`、少于 preferred 数量和保留预算始终合法。

## MVP 范围

MVP 实现：

- Mock Fixture、国际市场赔率和竞彩固定奖金。
- `THREE_WAY` 市场计算。
- `P_market` 基础去水和手工 `P_quant`。
- `QUANT_ONLY_V1` 和 `MARKET_QUANT_BLEND_V1`。
- 简单2串1、2元基础投注单位、正整数倍数。
- 预算上限、配置化 Ticket 偏好和绝对上限、允许未使用预算和 `NO_BET`。
- SQLite、CLI、完整输入 Manifest、不可变 AnalysisRun 和核心测试。

MVP 不实现：

- 让球胜平负的概率和计奖计算。
- 真实 Evidence Collector 和真实 LLM 调用。
- AtomicBet 数据库表、3串4、4串11和复式。
- 相关性修正、Monte Carlo、完整回测和 Web 前端。

详细决策见 [0006-configurable-ticket-strategy-profile.md](decisions/0006-configurable-ticket-strategy-profile.md)。
