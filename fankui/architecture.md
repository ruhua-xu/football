# 系统架构

## 状态

- 状态：已发布 package metadata 为 `0.4.0`；`0.5.0` 真实数据工作为 `Unreleased`
- 架构形态：Python 3.12+ 模块化单体
- 边界模式：六边形架构
- 存储边界：SQLite-only
- 入口：CLI
- 历史策略：Append-only Source Fact、Analysis Snapshot、Settlement 与 Backtest Artifact

`0.4.0` 在既有概率、融合、投注、风险与离线 Review 图之外，增加本地只读历史归档、赛果评估、结算和严格时间序列回测图。当前 `Unreleased` 工作继续保持模块化单体和六边形边界，增加显式 Sportmonks fixture capture、raw archive 与数据库 identity lineage。

## 系统边界

系统生成研究和决策辅助结果：

```text
PortfolioRecommendation | NO_BET
```

系统不负责自动下注、账户登录、支付、出票确认和真实资金托管。

## SQLite-only 边界

`0.4.0` 只支持 SQLite。运行时 Engine、`create_schema()`、迁移 helper 和直接 Alembic 环境都会先校验 backend，非 SQLite URL 在加载对应数据库驱动前失败。SQLAlchemy 模型不是 PostgreSQL/MySQL 兼容性承诺；外键 PRAGMA、partial index、append-only/lineage trigger 和 `INSERT OR REPLACE` 防护均属于当前正确性边界。

## 模块边界

| 模块 | 职责 |
|---|---|
| Domain | 市场、概率、竞彩计奖、Ticket、Portfolio、MatchResult、Settlement、Backtest 与指标不变量 |
| Application | 编排数据冻结、预测、融合、组合优化、归档登记、结算和 walk-forward |
| Ports | 决策数据 Provider、HistoricalDataProvider、Evidence/LLM Provider 与 Repository 协议 |
| Infrastructure | SQLite、Mock/本地历史 Provider、Sportmonks/The Odds API Adapter、人工复核 Sporttery Adapter；没有真实 LLM Adapter |
| Interfaces | CLI 输入和结果展示 |
| Backtest | 先封存 base AnalysisRun，再按独立 evaluation cutoff 追加赛果、结算、指标和报告 |

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

## Live Fixture Ingestion

```text
explicit CLI + SPORTMONKS_KEY
              |
              v
audited bounded HTTP request
              |
              v
immutable raw response archive
              |
              v
scope / status / pagination validation
              |
              v
capture + identity + observation transaction
```

`live ingest-fixtures` 与 `live ingest-market-odds` 是仅有的显式联网入口。配置、SQLite URL、kickoff window 和 provider scope 必须在读取 secret 或网络 I/O 前通过校验；fixture successful raw response 在 JSON/schema normalization 前封存，payload 错误不得打开数据库。odds 命令必须先从数据库读取命令起始 cutoff 可见的 identity catalog，因此数据库 lookup 先于 HTTP，但 source capture、consensus 和 issue 仍在完整 normalization 后原子追加。

Provider receipt time 作为 fixture identity 和 observation 的 `available_at_utc`，repository clock 记录 capture 的实际 `ingested_at_utc`。只有首次由某次 capture 创建的 Match、CanonicalMatchIdentity、TeamAlias、CompetitionMapping 和 ProviderMatchMapping 才保存该 capture 的 `fixture_ingestion_id`；预先存在的普通 identity 保持 `NULL`，不会因后续 capture 被追溯重分类。

Catalog 查询对 fixture-owned identity 同时执行 `available_at_utc <= cutoff` 和 owner capture `ingested_at_utc <= cutoff`，latest fixture observation 也必须通过这两个边界。新 kickoff/status、team alias 或 competition mapping 在其 capture 实际入库前不可见。identity ownership、capture 和 observation 均为 append-only；包含任何 fixture capture 的数据库禁止回退 fixture-ingestion migration，以免删除 provenance 后保留无来源 identity。

Live odds 与 reviewed Sporttery 使用独立的 append-only ingestion graph。每次 capture 保存 artifact、mapping、snapshot 和结构化 reconciliation issue；market consensus 额外保存每个 constituent，Sporttery snapshot 强制绑定 manual document 与 source artifact。`live reconcile` 只报告指定 cutoff 仍未由 imported review 关闭的 issue；review mapping 在实际 import 时才进入 identity catalog，不能按 `reviewed_at_utc` 回填历史可见性。

`live prepare-analysis` 不构造 provider 或 HTTP transport。它按单一 competition/season、decision cutoff 和 kickoff window，从数据库冻结 latest visible fixture observation、verified market consensus 与 verified Sporttery provenance；赔率 age、bookmaker coverage 或任一来源不合格时保存结构化 reason code。`live run-analysis` 同样完全离线，只通过 prepared providers 重放 ready graph，并以专用 `live_analysis_run_preparations` 关系将 AnalysisRun 绑定到唯一 preparation；同一 preparation 可产生多个 run。insert/completion trigger 和 repository retry 同时校验每场 fixture observation、market consensus 与 Sporttery snapshot，不能重新选择较新的来源。若 fixture 已改期，V3 packet 从该 run 冻结的 observation 投影 kickoff，而不是使用 immutable Match identity 上的首次 kickoff。

## Historical Archive 与回测流

```text
HISTORICAL_ARCHIVE_V1 read-only files
        |
        +-> Manifest/checksum/provenance registration only
        |
        +-> cutoff-selected Fixture/Odds/Sporttery/Quant
        |          |
        |          v
        |   immutable base AnalysisRun
        |          |
        |          v
        |   Ticket / Portfolio / Risk
        |
        +-> MatchResult at evaluation_as_of_at_utc
                   |
                   v
        Ticket/Portfolio Settlement
                   |
                   v
        BacktestSlice / Metrics / Report
```

Archive V1 将 `FIXTURES`、`MARKET_ODDS`、`SPORTTERY_BONUS`、`MANUAL_QUANT`、`MATCH_RESULTS` 和 `PROVIDER_MAPPINGS` 拆为独立文件。`historical-archive import` 采用 `MANIFEST_PROVENANCE_ONLY`，payload 保持只读；只有进入某次决策的 cutoff 合法版本由既有 AnalysisRun 事务物化，MatchResult 只在评估阶段物化。

`LIVE_STRICT` 与 `SOURCE_TIME_RESEARCH` 是互斥运行模式。后者必须保存 `retrospective=true` 和独立 `imported_at_utc`，并在输出标记 `RETROSPECTIVE_SOURCE_TIME_RESEARCH`；不能伪装为系统当时实时保存的数据，也不能与严格模式指标静默混合。

walk-forward V1 使用 `DAILY_FIXED_CUTOFF_V1`，每个 slate 共享一个 decision cutoff 和一个更晚的 evaluation cutoff，只支持 base AnalysisRun、`QUANT_ONLY_V1`/`MARKET_QUANT_BLEND_V1` 及一个预算 Portfolio。策略比较要求两边具有相同归档 provenance、切片、预算、阈值、约束、指标配置和冻结输入，不进行自动排名或调参。规范合同见 [backtest_v1_contract.md](backtest_v1_contract.md)。

## Elo model quant 与 Backtest V2

`Unreleased` model path 使用固定标识 `ELO_THREE_WAY_BASELINE_V1`、版本 `1` 和校准标签 `BASELINE_UNCALIBRATED`。它只读取显式 season 中同时满足 `available_at_utc <= cutoff` 与 `ingested_at_utc <= cutoff` 的常规时间 MatchResult，按 supersession chain 选择 cutoff 时最新可见 correction，并无条件排除本次 target match IDs。训练不足时保存 `QuantModelEvaluation(status=UNAVAILABLE)`，不创建 QuantPrediction/FinalPrediction，不复制 `P_market`。

```text
archived MatchResult + exact archive provenance
                 |
                 v
       QuantModelTrainingFact refs
                 |
                 v
        immutable Elo model state
                 |
                 v
 AVAILABLE evaluation -> model QuantPrediction -> deterministic Fusion
 UNAVAILABLE evaluation -----------------------> explicit no prediction
```

`MVP_INPUT_MANIFEST_V3` 将 model state 纳入 AnalysisRun input graph。manual context 与 model context 互斥；model prediction 必须引用同一 run/match/market 的 evaluation，state/evaluation/prediction 时间必须位于实际 run start/completion 范围，训练 match 不得与 target 集合相交。

`BACKTEST_V2` 在每个 slice 中先完成并封存上述 decision graph，再在更晚的 evaluation cutoff 读取 target result。V2 run、archive、slice、training source、evaluation ref、result source、ticket-settlement link 和 metric snapshot 使用独立八表持久化；run 只能由 `RUNNING` 经完整性 trigger 一次转换为 `COMPLETED`。Repository 在一个事务中保存整图，exact retry 不增加行，读取时重算 canonical hashes、概率、结算和 metrics。Alembic revision `c4e8a1d7f205` 与 runtime schema/trigger signature 完全对齐，并只允许空 V2 lineage 数据库回退。

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

`ANALYSIS_PACKET_V1/V2` 保持原字节合同和 manual-only lineage。`ANALYSIS_PACKET_V3` 保留 V2 MatchReviewContext，并用判别联合显式区分 manual/model `P_quant`；顶层去重保存结构化 model state hashes、训练 match/result IDs 和真实 run start，evaluation 明确区分 `AVAILABLE` 与 `UNAVAILABLE`。V3 不输出任意 `config_json/state_json/output_json`，也不输出 `P_final`、EV、Ticket、Portfolio 或资金参数。`LLM_REVIEW_V3` 必须回显 context ID/hash；model unavailable 只能对应 `MODEL_UNAVAILABLE`，不能提交伪概率。详见 [llm_review_v3_contract.md](llm_review_v3_contract.md)。

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

历史回测额外冻结 `decision_as_of_at_utc < evaluation_as_of_at_utc`。target MatchResult、Settlement 和 Backtest 指标不进入 AnalysisRun input manifest、AnalysisPacket 或 Review context；Elo 训练 MatchResult 只通过 cutoff 合法的 state/training lineage 进入 V3 manifest/packet。评估阶段不能改写 `P_final`、Ticket、Portfolio 或 Risk。赛果和结算更正通过 supersession 追加，BacktestRun/Slice/Metric 通过 hash、归档 provenance 和血缘表保持可回放。

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

## 当前范围

`0.4.0` 实现：

- Mock Fixture、国际市场赔率和竞彩固定奖金。
- `THREE_WAY` 市场计算。
- `P_market` 基础去水和手工 `P_quant`。
- `QUANT_ONLY_V1` 和 `MARKET_QUANT_BLEND_V1`。
- 简单2串1、2元基础投注单位、正整数倍数。
- 预算上限、配置化 Ticket 偏好和绝对上限、允许未使用预算和 `NO_BET`。
- SQLite、CLI、完整输入 Manifest、不可变 AnalysisRun 和核心测试。
- `HISTORICAL_ARCHIVE_V1`、本地 cutoff Provider、两种 data mode 和 manifest-only provenance 登记。
- append-only MatchResult、`BACKTEST`/`THREE_WAY`/简单2串1 Ticket Settlement 与 Portfolio Settlement。
- 固定 slate walk-forward、BacktestRun/Slice、概率/资金/覆盖率/回撤/风险指标、报告与并排策略比较。

当前 `Unreleased` 另已实现：

- Sportmonks fixture raw capture、canonical identity、双时间可见性与 append-only observation。
- 固定参数三向 Elo model `P_quant`、`MVP_INPUT_MANIFEST_V3` 和 unavailable-aware analysis。
- `BACKTEST_V2` 双 cutoff domain/application/repository、完整 SQLite lineage triggers 与 migration。
- `ANALYSIS_PACKET_V3` / `LLM_REVIEW_V3` model-lineage 离线闭环，同时保留 V1/V2 bytes。
- The Odds API、reviewed Sporttery、reconciliation/preparation 与 preparation-bound 离线 live AnalysisRun；无合格训练赛果时显式保存 model unavailable。

`0.4.0` 不实现：

- 让球胜平负的概率和计奖计算。
- 真实 Evidence Collector 和真实 LLM 调用。
- AtomicBet 数据库表、3串4、4串11和复式。
- 真实历史数据抓取或付费数据打包、网页 GPT 历史回测、自动调参、机器学习、相关性修正、Monte Carlo 和 Web 前端。
- 取消、腰斩、`VOID`、退款、加时、点球或串关降级结算。

核心模型交叉引用见 [data_model.md](data_model.md)；历史设计沿革见 [historical_data_backtest.md](historical_data_backtest.md)；Strategy Profile 见 [0006-configurable-ticket-strategy-profile.md](decisions/0006-configurable-ticket-strategy-profile.md)。
