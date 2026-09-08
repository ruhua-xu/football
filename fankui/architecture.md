# 系统架构

## 状态

- 状态：已发布 package metadata 为 `0.5.0`
- 架构形态：Python 3.12+ 模块化单体
- 边界模式：六边形架构
- 存储边界：SQLite-only
- 入口：CLI
- 历史策略：Append-only Source Fact、Analysis Snapshot、Settlement 与 Backtest Artifact

`0.4.0` 在既有概率、融合、投注、风险与离线 Review 图之外，增加本地只读历史归档、赛果评估、结算和严格时间序列回测图。`0.5.0` 继续保持模块化单体和六边形边界，正式增加显式 Sportmonks fixture capture、raw archive 与数据库 identity lineage。

## 系统边界

系统生成研究和决策辅助结果：

```text
PortfolioRecommendation | NO_BET
```

系统不负责自动下注、账户登录、支付、出票确认和真实资金托管。

## SQLite-only 边界

`0.5.0` 只支持 SQLite。运行时 Engine、`create_schema()`、迁移 helper 和直接 Alembic 环境都会先校验 backend，非 SQLite URL 在加载对应数据库驱动前失败。SQLAlchemy 模型不是 PostgreSQL/MySQL 兼容性承诺；外键 PRAGMA、partial index、append-only/lineage trigger 和 `INSERT OR REPLACE` 防护均属于当前正确性边界。

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

`0.5.0` model path 使用固定标识 `ELO_THREE_WAY_BASELINE_V1`、版本 `1` 和校准标签 `BASELINE_UNCALIBRATED`。它只读取显式 season 中同时满足 `available_at_utc <= cutoff` 与 `ingested_at_utc <= cutoff` 的常规时间 MatchResult，按 supersession chain 选择 cutoff 时最新可见 correction，并无条件排除本次 target match IDs。训练不足时保存 `QuantModelEvaluation(status=UNAVAILABLE)`，不创建 QuantPrediction/FinalPrediction，不复制 `P_market`。

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

## 0.6 Production Quant Bootstrap

ADR-0008 已在外部架构审查 `APPROVE WITH MINOR CHANGES` 且无 blocker 后更新为 `Accepted`。本节描述已接受、尚未实现的架构；本次修订不授权 migration、provider、CLI、真实数据导入或 package 版本变更，`0.5.0` 的 live guard 和 `MODEL_UNAVAILABLE` 行为在后续独立实施前保持不变。

本架构将 source provenance 与 production-training eligibility 拆为正交维度，但不把 research provider 放进 live AnalysisRun：

```text
HistoricalDataMode                 TrainingUseClass
├── LIVE_STRICT                    └── APPROVED_TRAINING_HISTORY
└── SOURCE_TIME_RESEARCH
```

`APPROVED_TRAINING_HISTORY` 不是第三种 data mode。它只授权一个 content-addressed、人工审批的 `SOURCE_TIME_RESEARCH` 事实集合构建指定 model/config 的 production model release；每条事实继续保留 `retrospective=true`、source available time 和实际 local import/register time。普通或 approved research provider 都不直接进入 live，`LIVE_STRICT` 也不成为这组历史的别名。

```text
SOURCE_RIGHTS_ADMISSION_V1
        |
        v
SOURCE_TIME_RESEARCH archives
        |
        v
mandatory PRODUCTION_QUANT_INTEGRITY_PILOT
        |
        +----> separate MARKET_FUSION_BENCHMARK
        |
        v
TRAINING_HISTORY_MANIFEST_V1
        |
        v
TRAINING_HISTORY_APPROVAL_V2 event
        |
        v
offline PRODUCTION_QUANT_MODEL_RELEASE_V1
        |
        v
current LIVE_STRICT target
        |
        v
AVAILABLE run state -> ANALYSIS_PACKET_V3 + mandatory audit sidecar
```

Source rights 使用两阶段门禁。`SOURCE_RIGHTS_ADMISSION_V1` 必须在 acquisition/import/pilot 前结构化证明 source identity、terms/authority hashes、research/storage uses、effective/expiry time、retention/deletion rule 和 public repository boundary。采集后的 `TRAINING_FACT_ADMISSION_V1` 再原子绑定 normalized result、status、season 和 source records；它不能追溯替代 rights admission。Integrity pilot 后的 production approval 单独记录 `PRODUCTION_MODEL_TRAINING`、`PRODUCTION_MODEL_INFERENCE`、`DERIVED_MODEL_STATE_RETENTION` 和 `AUDIT_HASH_RETENTION`，包括各自有效期及 retention 语义。`0.6` V1 只要求本地 hash-sealed reviewer attestation/evidence，不实现 PKI、CA、remote signer 或 key-management subsystem；以后如需 cryptographic signing，使用新版本扩展。该机制是内部授权审计，不是软件作出的法律判断；rights 与 append-only 最低保留要求冲突时 source 不准入。

新审批采用已授权的 `TRAINING_HISTORY_APPROVAL_PAYLOAD_V2` / `TRAINING_HISTORY_APPROVAL_V2`：reviewer 确认精确授权内容与既有证据 hash，系统事件绑定原 attestation/evidence、operator、幂等请求及实际 recorded/persisted 观察时间。未来记录时间不进入事先审核的 payload；persisted 观察不是物理 commit 完成的预测。V1 hash/解析保留，不自动转换。更换请求 key 或证据排版不能重放已撤销的授权。受控 correction 通过版本化上下文保存完整前序并逐 cutoff 选择真实 season 的完整 head，withdrawal 不回退旧比分，旧工件与 V3 wire 不改写。

常规时间完成状态和真实 season 不能由比分或复制字段自行证明。每个 approved fact 必须绑定 exact fixture source、`MATCH_SEASON_MEMBERSHIP_V1` 和 `MATCH_RESULT_ADMISSION_V1`。Result admission 保存 provider raw status、mapping version、regular-time score、finalized/source times、raw/full-record hashes 和 reviewer/adapter identity；season membership 必须绑定原始 bytes 中明确的 provider competition/season/fixture 关系、fixture/mapping IDs 和 canonical season。三者与 normalized MatchResult 在持久化、hash-sealed `TRAINING_FACT_ADMISSION_V1` transaction 中原子物化；裸 `match_results` row 不具备 production-training 资格。

建议 additive persistence graph 为：

```text
source_rights_admissions

training_fact_admissions
├── training_fact_fixture_sources
├── match_season_memberships
└── match_result_admissions

training_history_manifests
├── training_history_fixture_sources
├── training_history_mapping_sources
├── training_history_result_sources
├── training_history_seasons
└── training_history_facts

training_history_approval_events
└── training_history_revocation_events

production_quant_integrity_pilot_attempts
└── production_quant_integrity_pilot_summaries
    └── production_quant_integrity_pilot_attestations

market_fusion_benchmark_plans
└── market_fusion_benchmark_reports

production_quant_model_releases
└── production_quant_model_release_facts

production_target_acceptance_plans
└── analysis_run_target_acceptance_plans

quant_model_states
└── quant_model_state_production_releases
```

Manifest、approval/revocation event、integrity pilot attempt/summary/attestation、benchmark plan/report、model release 和 state binding 均 append-only。Typed source table 使用真实 FK，避免 polymorphic ID。Canonical roots 分别冻结 source graph、ordered `WARMUP`/`PILOT_TARGET`/`PRODUCTION_TARGET` seasons、full approved facts、existing Elo `training_data_hash`、integrity pilot plan/attempt-root/report、rights evidence、build recipe/code revision 和实际 approval/build/persisted time。现有 Elo hash 继续封存数学输入；新增 `approved_facts_hash` 另行覆盖 fixture/mapping/result source、season、actual import/register 和 status admission，二者不能互相替代。Child hash 排除尚未生成的 parent manifest ID，以 pilot scope 和 source hashes 建 root，避免循环。

Training fact 只允许已完成的 regular-time result 和最小 identity lineage。每场必须保存真实 `season_id`，并逐项要求 fixture、mapping/season 和 result source visibility 早于 cutoff；三者最大值是 effective availability，但不能替代逐项验证。Source rights/fact admission、actual import/register、pilot、approval 和 model release persistence 也必须在 production decision cutoff 前完成。未来 fixture/result/odds/table state 不得进入 Elo facts；target exclusion 按每个 walk-forward slice 执行，较早 target 只能在结果进入后续 cutoff 后成为训练事实。

现有 archive Elo provider 的单一构造参数 `season_id` 会把全部结果投影到同一 season，不能用于本架构。实现阶段必须改为 explicit per-season source membership；query target season 只触发最终 season transition，不能覆盖历史事实。单赛季兼容路径也必须通过逐场 season assignment，而不是复用统一 label。

Elo 数学保持 `ELO_THREE_WAY_BASELINE_V1` version `1`：

```text
initial_rating = 1500
k_factor = 20
home_advantage = 100
season_regression_factor = 0.75
draw_probability = 0.25
minimum_prior_matches = 5
config_hash = c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4
```

Mandatory Bundesliga `PRODUCTION_QUANT_INTEGRITY_PILOT` 至少覆盖一个完整已结束赛季，必要时加入紧邻前一赛季作为 warm-up。首次读取 target result 前必须封存 `PRODUCTION_QUANT_INTEGRITY_PILOT_PLAN_V1`，固定 source/archive roots、cohort、per-slice cutoffs/exclusions、season window、Elo config、metrics、build recipe 和 code revision。它只依赖合格 training facts，不依赖 historical odds 或 fusion；报告固定 Elo `P_quant` 的 availability、Brier、LogLoss、10-bin Calibration/ECE 和 deterministic replay。连续 attempt ledger/root 永久保留全部尝试；terminal attestation 将其与 source/fact roots、state core 和 report hash 绑定，可作为 training approval/model release 的技术证据，后续 approval/release 的 recipe/revision 必须完全相同。

Integrity pilot 的每个 slice 只读取 decision cutoff 前合格的 fixture、result admission 和 training correction，target result 只在更晚 evaluation cutoff 后评分；它不调用 fusion、optimizer、Ticket、Portfolio 或 Settlement，也不伪造 Sporttery。独立 `MARKET_FUSION_BENCHMARK` 才读取有合法 point-in-time provenance 的 `THREE_WAY` odds，并冻结 snapshot/bookmaker/consensus/de-vig/fusion policy。两条 lane 都不得用投注 ROI 或任何概率指标手工/自动调参、重选 cohort 或挑选“正式”尝试。

若没有合格 point-in-time historical odds，Market/Fusion benchmark 显式记录 `status=UNAVAILABLE`、`reason=NO_ADMISSIBLE_POINT_IN_TIME_ODDS_ARCHIVE`；不得以下载时间、closing odds 或未来 snapshot 回填，但该状态不阻塞已通过 integrity pilot 的合格 Elo model release。有合法 odds 时，benchmark 报告共同 available cohort 上 `P_market` / `P_quant` / `P_final` 的 paired Brier、LogLoss、10-bin Calibration/ECE、bin counts 和分母。两条 lane 都是 `RETROSPECTIVE_SOURCE_TIME_RESEARCH`，不是历史 live performance；production approval 发生在实际 import、mandatory integrity pilot 和人工 rights review 之后，只能用于其后 cutoff 的当前/未来预测。

Production model release 封存 run-cutoff-independent、training-cutoff-frozen state core，包括单一权威 `training_cutoff_at_utc`、ratings、prior counts、真实 seasons、ordered facts 和 training roots；release row 只保存该值的受约束等值投影。每个 release fact 的 `effective_source_available_at_utc <= training_cutoff_at_utc < build_started_at_utc`，不存在无 training cutoff 的 release。Live run 在当前 cutoff 只从该 core 确定性重建 run-scoped state；去除 run ID/run cutoff/generated time/state hash 后必须重得相同 core hash。Release 必须在 decision cutoff 前已持久化，因此 V3 要求的 state cutoff 和 run timing 可以满足，同时不能改变 release 的模型内容。

第一场 production acceptance 必须是 model release 后的新未来 Bundesliga 比赛，不得改写 `0.5.0` 已封存 handshake。`PRODUCTION_TARGET_ACCEPTANCE_PLAN_V1` 在任何 candidate evaluation/review 前冻结 exact target、cutoff、selection rule 和 release hash，并由 AnalysisRun/sidecar 绑定。当前 fixture、market odds 和 reviewed Sporttery 仍走 `LIVE_STRICT`；live run 不查询 research archive 或隐式选择“最新”模型。该 run 至少产生一个 `AVAILABLE` `P_quant` 和 base `P_final`。

`MVP_INPUT_MANIFEST_V3`、`ANALYSIS_PACKET_V3`、`LLM_REVIEW_V3` wire bytes 保持不变。每个 approved-history run 必须另有 `APPROVED_TRAINING_HISTORY_AUDIT_V1` sidecar，以 run/packet/state/training hashes 绑定 target plan、model release、approval、manifest 和 per-season sources，并显式声明 decision mode、training source mode/use class 和 retrospective presence；V3 单独存在不证明 approval。Model build 在 start/completion 检查 `approval_active_for_build(t)`，只要求 `PRODUCTION_MODEL_TRAINING` 在各时点有效。AnalysisRun 及 packet export、review import、FusionRun、PortfolioRevision 在各自 start/completion 独立检查 `release_active_for_inference(t)`，分别要求 `PRODUCTION_MODEL_INFERENCE` 有效、`DERIVED_MODEL_STATE_RETENTION` 覆盖声明的 state 保留期、`AUDIT_HASH_RETENTION` 覆盖声明的 audit 保留期；不得以 build-active 代替 inference-active。网页 GPT 仍只接收 V3 packet；`NO_BET` 仍是合法结果。

在上述 AVAILABLE handshake 完成前不进入 `0.7`。完整 provenance、schema、pilot 和 revocation 决策见 [ADR-0008](decisions/0008-approved-training-history.md)。

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

`0.5.0` 正式发布包含以下既有能力：

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

`0.5.0` 另已实现：

- Sportmonks fixture raw capture、canonical identity、双时间可见性与 append-only observation。
- 固定参数三向 Elo model `P_quant`、`MVP_INPUT_MANIFEST_V3` 和 unavailable-aware analysis。
- `BACKTEST_V2` 双 cutoff domain/application/repository、完整 SQLite lineage triggers 与 migration。
- `ANALYSIS_PACKET_V3` / `LLM_REVIEW_V3` model-lineage 离线闭环，同时保留 V1/V2 bytes。
- The Odds API、reviewed Sporttery、reconciliation/preparation 与 preparation-bound 离线 live AnalysisRun；无合格训练赛果时显式保存 model unavailable。

`0.5.0` 的 release boundary 保持不变：live Elo 缺少合格 `LIVE_STRICT` history 时允许 `MODEL_UNAVAILABLE`；ADR-0007 不修改，`SOURCE_TIME_RESEARCH` 不得混入 live；正式选择市场仍仅为 `THREE_WAY`，正式 pass type 仍仅为简单 `2X1`；不执行自动下注。历史 Elo bootstrap 留待下一阶段。

`0.5.0` 不实现：

- 让球胜平负的概率和计奖计算。
- 真实 Evidence Collector 和真实 LLM 调用。
- AtomicBet 数据库表、3串4、4串11和复式。
- 真实历史数据抓取或付费数据打包、网页 GPT 历史回测、自动调参、机器学习、相关性修正、Monte Carlo 和 Web 前端。
- 取消、腰斩、`VOID`、退款、加时、点球或串关降级结算。

核心模型交叉引用见 [data_model.md](data_model.md)；历史设计沿革见 [historical_data_backtest.md](historical_data_backtest.md)；Strategy Profile 见 [0006-configurable-ticket-strategy-profile.md](decisions/0006-configurable-ticket-strategy-profile.md)。
