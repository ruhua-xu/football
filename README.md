# Football System

可回放的足球比赛分析、中国竞彩2X1/3X4/4X11组合决策、Portfolio 风险分析、离线 LLM 文件协作和严格时间序列回测工具。

## 发布状态

- `v0.1.0` 固定在提交 `fceb945d07218290dc85b465e885a47ae9912c3f`，保留原始 MVP 行为。
- 当前 package metadata 为 **1.1.0**；Production Activation Implementation Review **APPROVED**。接受实现为 `b2544464055b0fda954b1fbed299f5d749dae6a0`，tree为 `6942954d9686b75543749fa4c918aec81bddf49c`，design为 `8b1cdf727c14510e590450406d89a9edae7ff759`。最终门禁及发布身份见 [1.1.0 最终验收报告](fankui/production_activation_v1_final_acceptance_report.md)与独立发布回执。
- **1.1.0不是新预测算法版本**；新增软件能力与公开`real-bridge`接口，既有Elo、Poisson、MEDIAN、V4、fusion/P_final、EV、Strategy、Return、objective、payout与Settlement数学继续冻结。Migration head保持`6c859ab273fe`。
- `1.1.0` 仅支持 SQLite。运行时建库、Schema 创建和 Alembic 迁移都会在加载其他数据库驱动前拒绝非 SQLite URL。
- 只有显式执行 `live ingest-fixtures` 或 `live ingest-market-odds` 才会分别读取 `SPORTMONKS_KEY` 或 `ODDS_API_KEY` 并访问网络；`ingest-fixtures-manual`、Sporttery、reconcile、review import、prepare-analysis 和 run-analysis 均为本地 I/O。项目不连接真实 LLM API，不执行自动下注。

## 当前能力

**1.1 Production Activation 软件能力**：独立real artifact / `rb_*`图、无比赛/票据seed的sealed anchor与future epoch、exact UTC kickoff bucket、PRE_LOCK replacement、program级official prediction slots和DecisionLock V2锁定重校验。复用已有合法pinned model与完整market/SP/source replay；不可用模型保留P_quant/P_base为空和整桶UNAVAILABLE。

合同：[Real bridge](fankui/real_prospective_activation_bridge_v1_contract.md)、[Pre-lock replacement](fankui/pre_lock_replacement_v1_contract.md)、[Daily operator](fankui/daily_operator_v1_contract.md)。使用`football-system real-bridge --help`查看闭集离线接口；`daily.cmd`保持INPUT_PREPARATION六项菜单。

发布后默认仍为 **INPUT_PREPARATION**。`REAL_PROSPECTIVE_OBSERVATIONS=0`，`REAL PERFORMANCE=INSUFFICIENT_PROSPECTIVE_SAMPLE`，`AUTO_BETTING=NO`。Runtime初始化、source/credential admissions、合法existing model pin、real program、sealed anchor和future epoch等待独立GO-LIVE CHECKLIST；不自动激活。

**1.0 Prospective Validation / Production Framework 能力继续保留**：本地 `prospective` 路径提供typed evidence、可信本地receipt/cutoff、赛前decision lock、追加式赛果修订/结算、全epoch描述性验证与审计。ADR-0012为Accepted，功能及既有数学保持已审查版本。
见 [合同与CLI流程](fankui/prospective_validation_v1_contract.md)、[ADR-0012](fankui/decisions/ADR-0012-prospective-validation-production-closeout.md)、[候选实施报告](fankui/phase_10_implementation_report.md)和[最终验收报告](fankui/phase_10_final_acceptance_report.md)。
旧V2 source graph仍明确synthetic，旧V1 REAL gate继续返回`PRODUCTION_DECISION_ADAPTER_UNAVAILABLE`；1.1使用独立real入口。软件发布不表示真实production activation，也不证明ROI、alpha、P_llm改善或P_final优于P_base。

```text
football-system prospective prepare --database-url <sqlite-url> --input <prepare.json> --packet-dir <directory>
football-system prospective lock --database-url <sqlite-url> --input <lock.json> --review <llm_review.json> --reasons <correction_reasons.json>
football-system prospective settle --database-url <sqlite-url> --input <settle.json>
football-system prospective report --database-url <sqlite-url> --input <report.json>
football-system prospective audit --database-url <sqlite-url> --artifact-id <sealed-id>
```

**0.9 Return Distribution Optimizer** 已通过整体架构验收：独立 `return-distribution` 路径消费封存的V2 candidate catalog、P_final、SP和budget，提供same-market relevant states、connected-component exact convolution、cash-aware metrics及deterministic marginal allocation。
同场跨market返回`CROSS_MARKET_JOINT_UNAVAILABLE`；不假造joint probability。NO_BET始终作为baseline，预算不必花完。
ADR-0011为Accepted，该能力于0.9.0发布；见 [ADR-0011](fankui/decisions/0011-return-distribution-optimizer.md)、[已接受合同](fankui/return_distribution_v1_contract.md)、[候选阶段实施报告](fankui/phase_9_implementation_report.md) 和 [最终验收报告](fankui/phase_9_final_acceptance_report.md)。

```text
football-system return-distribution policy
football-system return-distribution profile
football-system return-distribution evaluate --database-url <sqlite-url> --input <evaluate.json>
football-system return-distribution optimize --database-url <sqlite-url> --input <optimize.json>
football-system return-distribution show --database-url <sqlite-url> --artifact-id <sealed-id>
```

evaluate输入只含plan ID、candidate IDs与整数倍数；optimize输入只含plan ID，可附expected config hashes作校验。
默认权重为未校准策略参数；exact仅在`INDEPENDENT_MATCHES_V1`假设下成立，marginal不是global optimum，synthetic验收不代表真实收益优势。

**0.8 Market Expansion + Simple Multiple Selection** 已通过整体架构验收。
独立 `market-v2` 路径提供typed 3/8/31 outcome
catalog、固定independent Poisson新市场baseline、V4文件Review/fusion、mixed-market
cross-match串关和少量same-market choice Cartesian展开。原0.7默认路径与wire保持冻结。
设计见 [ADR-0010](fankui/decisions/0010-generic-market-review-and-simple-multiple.md)，
市场、Poisson、V4、复式分别见 [taxonomy](fankui/market_taxonomy_v1_contract.md)、
[goal model](fankui/poisson_goals_baseline_v1_contract.md)、
[V4](fankui/analysis_packet_v4_contract.md)、[Pass V2](fankui/strategy_pass_v2_contract.md)。

```text
football-system market-v2 schema review
football-system market-v2 profile
football-system market-v2 plan --database-url <sqlite-url> --input <request.json> --output <plan.json>
```

0.8验收全为synthetic/fixed fixtures；没有真实provider/LLM HTTP请求或真实预测效果声明。
实现与A–P验收汇总见 [Phase 8实施报告](fankui/phase_8_implementation_report.md)。
以下为已发布0.7能力：

**0.7 Strategy Profile / Pass Type Engine** 已通过整体架构验收。
显式 `strategy-pass` 路径支持1/4/11子注的2X1/3X4/4X11、
PRIMARY/SECONDARY/HEDGE/LONGSHOT、结构集中度与原资金约束、append-only持久化及独立
BACKTEST结算。入口、Profile示例和边界见 [V1合同](fankui/strategy_pass_v1_contract.md)。

```text
football-system strategy-pass profile --print-schema
football-system strategy-pass build --database-url <sqlite-url> --analysis-run-id <id> --budget-fen <existing-fen> --profile config/strategy_profile_v1.json
```

以下为已冻结的0.6能力：

- Mock 或本地历史归档的 Fixture、国际市场赔率、竞彩固定奖金、手工 `P_quant`，以及固定参数的三向 Elo baseline `P_quant`。
- `THREE_WAY` 去水、`QUANT_ONLY_V1`、`MARKET_QUANT_BLEND_V1`、Selection EV 和简单2串1。
- 显式 Cash、`NO_BET`、Exposure、确定性 Stress Test 和 Portfolio 风险约束。
- SQLite、SQLAlchemy、Alembic、不可变 AnalysisRun 和追加型审计工件。
- V1/V2/V3 `analysis_packet`、`llm_review`、append-only FusionRun 和独立 PortfolioRevision；V1/V2 字节合同继续保持 manual-only，V3 显式承载 manual/model lineage 和 model unavailable。
- Historical Archive V1、MatchResult、Ticket/Portfolio Settlement、walk-forward、概率/资金/回撤/覆盖率指标和并排策略比较；`BACKTEST_V2` 额外冻结 Elo state/evaluation、双 cutoff、归档和财务结算血缘。
- Sportmonks fixture raw capture 与 provider-neutral `REVIEWED_FIXTURE_MANUAL_ARCHIVE_V1`，共同复用 canonical identity、append-only observation lineage 和 SQLite 原子落库；仓库与自动化测试不包含账户响应或真实第三方数据。
- The Odds API current h2h raw capture、全部 bookmaker 快照、`MARKET_CONSENSUS_MEDIAN_V1` lineage，以及 reviewed `SPORTTERY_MANUAL_ARCHIVE_V2` capture；unresolved/ambiguous identity 作为结构化 issue 落库，不模糊绑定。
- provider-neutral `DAILY_SLATE_PLAN_V1`：从 reviewed Sporttery manual archive 或轻量 `SPORTTERY_DAILY_SLATE_INPUT_V1` 生成确定性 identity reconciliation 与 capture plan；只复用 cutoff 前已存在的 canonical identity，不创建比赛、不联网，也不产生 AnalysisRun。
- append-only live reconciliation/review 与 persisted-only analysis preparation；preparation 按 decision cutoff 冻结 fixture observation、market consensus 和 Sporttery provenance，并对缺失、陈旧或覆盖不足输入给出 reason code。
- `live run-analysis` 只重放一份 ready preparation，以固定 `ELO_THREE_WAY_BASELINE_V1` 创建 V3 model AnalysisRun；run 与 preparation 的完整 ready-match graph 由专用 append-only 关系封存。
- Production Quant Bootstrap：source rights、完整 scope/事实准入、受控 correction、相应时间依据的 integrity evidence、精确 ApprovalV2、sealed release 和 pinned live；已批准历史的 Packet 导出与后审路径均要求 mandatory audit sidecar 和当前授权。

## Production Quant Bootstrap

`production-quant` 提供现有 28 个受控本地命令。使用 `football-system production-quant --help` 和 `--print-schema` 查看安装版本的精确入口与请求类型；完整串联说明见 [实施报告](fankui/phase_6_implementation_report.md) 和 [ADR-0008](fankui/decisions/0008-approved-training-history.md)。帮助/schema 查询不访问数据源，也不创建许可或审核。

`EvidenceBasis` 与 data mode 分离：已验证历史 source time 保持严格路径；`CURRENT_SNAPSHOT_OBSERVED` 使用当前实际 capture/verify/admit 的历史快照，仍为 `SOURCE_TIME_RESEARCH`、`retrospective=true`。未知 provider publication/finalization/version 不用比赛结束时间或本地时钟填充。当前快照按完整 cohort 和固定 Elo structural replay 验收，不能据此给出严格历史 walk-forward 性能；相应指标明确 `UNAVAILABLE / UNPROVEN_HISTORICAL_VERSION_TIME / metrics=null`。

真实来源、scope 和技术证据通过后，reviewer 审核精确 `TRAINING_HISTORY_APPROVAL_PAYLOAD_V2`，正式 recorder 绑定原 review 与真实记录事件，再构建 sealed release。Live 只消费明确指定且预先存在的 release/target plan，不直接调用 research training provider。V3 wire 不变；source/approval/release 血缘通过版本化 model-source 与 mandatory audit bundle 验证。更正、撤销、到期或 hash 不匹配按各自当前使用门禁处理。

软件版本发布不授予任何数据用途、订阅、推理或保留权利。既有真实验收工件保留原 code revision、时间和 hash；不得因 version bump 重新生成或重绑定。真实 raw/state/audit 与审核文件保持本地，按已确认 retention 清理，不进入公开 Git 或 wheel。

## Live Source Ingestion

`live ingest-fixtures` 是显式启用的 `LIVE_STRICT` 命令。它要求调用方提供 kickoff window、provider league/season ID、season、competition type 和 team type，并逐项校验 provider 返回的 league、season、team type/gender，不从不完整 payload 猜测 identity scope。API token 只从 `SPORTMONKS_KEY` 读取并放入 `Authorization` header；raw payload 和 secret-safe request metadata 会先写入 `data/raw`，完整 capture 验证通过后才迁移或打开数据库。

```text
football-system live ingest-fixtures --kickoff-from 2026-09-03T00:00:00Z --kickoff-to 2026-09-03T23:59:59Z --league-id 501 --provider-season-id 23690 --season 2026/27 --competition-type LEAGUE --team-type CLUB
```

每次成功响应使用本地 receipt time 作为 availability，并单独保存数据库 ingestion time；首次由 capture 创建的 identity row 显式绑定 `fixture_ingestion_id`，catalog 只有在 availability 和对应 ingestion 均不晚于 cutoff 时才可见。既有普通 identity 不会被后来 capture 重新分类；后续 status/kickoff 变化追加到 `fixture_observations`，名称漂移追加新 alias/mapping。当前 vertical slice 要求一个经过 league filter 的完整单页响应；若 terminal pagination metadata 不一致或 `pagination.has_more=true`，会拒绝落库并要求缩小 kickoff window，绝不把截断页当作完整数据。真实账户调用必须先核对 token entitlement、目标联赛、字段和限流 metadata；自动化测试仍只使用 scripted transport 和自造 payload，不访问外网。第一次真实 V3 handshake 的无版权摘要见 `fankui/phase_5_implementation_report.md`，raw payload、Sporttery evidence 和 secret 不进入仓库。

`live ingest-fixtures-manual` 是受控的离线替代入口。每份 JSON 只接受一个 competition/season/type/team-type scope；每条 fixture 只保存赛事、赛季、UTC kickoff、主客队标签、赛事/球队类型、source reference/path/SHA-256，以及 capture/entry/review provenance。输入必须为 `SELF_REVIEWED` 或 `INDEPENDENT_REVIEWED`，并提供位于 JSON 同目录内、SHA-256 匹配的 PNG/JPEG/WebP screenshot、PDF、HTML 或 text evidence；不得放入赔率、预测、概率或 EV。第三方版权 evidence 保留在本地，不应提交到公共仓库。

该命令不读取 API key、不构造 HTTP transport，也不创建第二套 Match 模型。唯一完全一致的 competition/team label、season/type 和 kickoff 会复用既有 canonical identity并追加 provider provenance/observation；没有精确 label identity 的 reviewed fixture 通过同一正式 ingestion graph 创建 canonical identity，系统不会用近似名称自动绑定。重复执行同一 archive 为已验证的幂等 no-op。歧义、主客颠倒、kickoff、赛事或 scope 冲突均 fail closed；可用 `--reconciliation-output` 将确定性的 `REVIEWED_FIXTURE_MANUAL_RECONCILIATION_V1` 写为 append-only 文件，既有 identity 不会被覆盖。

```text
football-system live ingest-fixtures-manual --archive data/manual/reviewed_fixture.json --raw-archive data/raw --reconciliation-output exchange/fixture_reconciliation.json
```

其余 live source 命令形成显式 capture -> reconcile -> review -> recapture -> prepare 流程。odds 命令先按命令起始时刻加载 provider-specific identity catalog，再发起一次 current endpoint 请求；raw response 在 normalization 前封存，source snapshots 与 consensus lineage 在同一事务中追加。Sporttery 命令只接受 reviewed V2 本地文件及其 SHA-256 source artifact，不实现网页爬虫。首次无法解析的 provider event 可先落为 issue，导入人工 mapping 后再重新 capture：

```text
football-system live plan-slate --input data/manual/sporttery.json --as-of 2026-09-02T12:00:00Z --output exchange/daily_slate_plan.json
football-system live ingest-fixtures-manual --archive data/manual/reviewed_fixture.json --raw-archive data/raw --reconciliation-output exchange/fixture_reconciliation.json
football-system live ingest-market-odds --kickoff-from 2026-09-03T00:00:00Z --kickoff-to 2026-09-03T23:59:59Z --match-id <internal-match-id> --sport-key soccer_epl --season 2026/27 --competition-type LEAGUE
football-system live ingest-market-odds --plan exchange/daily_slate_plan.json --plan-request-id <request-id> --sport-key soccer_epl --season 2026/27 --competition-type LEAGUE
football-system live ingest-sporttery --archive data/manual/sporttery.json --kickoff-from 2026-09-03T00:00:00Z --kickoff-to 2026-09-03T23:59:59Z
football-system live reconcile --ingestion-id <ingestion-id> --output exchange/live_reconciliation.json
football-system live import-identity-review --review exchange/live_identity_review.json
football-system live prepare-analysis --decision-as-of 2026-09-02T12:00:00Z --kickoff-from 2026-09-03T00:00:00Z --kickoff-to 2026-09-03T23:59:59Z --competition-id <competition-id> --season-id 2026/27 --maximum-odds-age-seconds 21600 --minimum-bookmaker-count 2 --output exchange/live_analysis_preparation.json
football-system live run-analysis --date 2026-09-03 --budget 100 200 --analysis-run-id <analysis-run-id>
football-system analysis-packet export --config config/live.toml --analysis-run-id <analysis-run-id> --schema-version ANALYSIS_PACKET_V3 --output exchange/live_analysis_packet_v3.json
```

`plan-slate` 输入必须带 source artifact SHA-256 和 `SELF_REVIEWED` 或 `INDEPENDENT_REVIEWED` provenance。候选只保存日期限定的竞彩编号、UTC kickoff、主客队/赛事标签和 optional `THREE_WAY` SP；同一编号可跨日期出现，同日重复会被拒绝。已有 reviewed explicit mapping，或唯一完全一致的 canonical competition/home/away label 与 kickoff 的候选标记 `IDENTITY_RESOLVED` 并进入精确 market-odds request；零候选时额外标记 `FIXTURE_SOURCE_REQUIRED`，多个精确候选仍保持 unresolved，绝不模糊绑定。空输入正常输出 `NO_SPORTTERY_CANDIDATES` 与 `NO_ANALYSIS`。DailySlate 本身不创建 canonical fixture，plan 文件也不能作为 `run-analysis` 输入；仍必须先走正式 fixture/source ingestion 和 persisted preparation。

`reconcile`、`import-identity-review`、`prepare-analysis` 和 `run-analysis` 不读取 API key、不构造 HTTP transport。Preparation 只查询 cutoff 前已持久化的数据；缺少任一必要来源时保存 `NO_ANALYSIS_INSUFFICIENT_DATA`，不会临时联网补数。`run-analysis --date` 仅在该 UTC 日期恰好匹配一份 ready preparation 时运行；否则必须使用 `--preparation-id`。当前没有 provenance-qualified persisted 训练赛果时，Elo evaluation 明确保存 `UNAVAILABLE`，不会复制 `P_market` 或生成伪 `P_final`。真实 provider CLI 已通过单场 real positive acceptance；仓库仍不包含真实 key、raw payload 或可声明为模型表现的数据。

## Historical Archive V1

`HISTORICAL_ARCHIVE_V1` 是 provider-neutral、UTF-8、按数据类型拆分的只读 JSON 归档，支持且要求完整区分：

```text
FIXTURES
MARKET_ODDS
SPORTTERY_BONUS
MANUAL_QUANT
MATCH_RESULTS
PROVIDER_MAPPINGS
```

每个文件包含版本化 Manifest、来源和许可说明、`data_mode`、记录数与 SHA-256；加载时拒绝 checksum 不一致、重复业务版本、重复 JSON key、NaN/Infinity、非法时区、错误 mapping 和不合法更正链。

真实归档和生产运行恰好使用以下两种模式，不能混用：

| 模式 | 语义 |
|---|---|
| `LIVE_STRICT` | 对所有真实归档，表示系统当时实际采集的数据；要求 `captured/observed <= available <= ingested <= cutoff`。 |
| `SOURCE_TIME_RESEARCH` | 后来取得、按可信来源时间研究的归档；每条记录必须 `retrospective=true` 并单独保存 `imported_at_utc`，报告醒目标记 `RETROSPECTIVE_SOURCE_TIME_RESEARCH`。 |

`historical-archive import` 实际执行 `MANIFEST_PROVENANCE_ONLY`：只追加 Manifest、checksum 和 provenance，不把全量 payload 灌入规范化来源表。payload 保持只读文件；决策阶段只物化 cutoff 合法且进入 AnalysisRun Manifest 的输入，MatchResult 只在评估阶段追加。

## 历史 CLI

`0.4.0` 的公开历史接口恰好包含以下 8 条路径：

```text
football-system historical-archive validate
football-system historical-archive import
football-system match-results list
football-system settlement create
football-system settlement report
football-system backtest run
football-system backtest report
football-system backtest compare
```

`backtest run` 仅支持 `QUANT_ONLY_V1` 和 `MARKET_QUANT_BLEND_V1`。`backtest compare` 要求两次运行具有相同归档 provenance、模式、时间切片、预算、阈值、约束、指标配置和冻结输入，只并排报告结果，不宣布“最佳策略”。

当前公开 `backtest run/report/compare` CLI 仍使用兼容的 `BACKTEST_V1`。`BACKTEST_V2` 已实现 application、domain、SQLite repository、append-only trigger 和 Alembic persistence boundary，供显式 model-analysis/walk-forward 编排使用；它不会把不可用的 Elo 输出替换成 `P_market`，也不会自动调参或选择“最佳”参数。

## 合成验收

wheel 和源码安装均内置 `config/backtest.toml`、完整 Alembic 迁移、10 个固定 slate/60 场比赛的验收归档及 `acceptance_config.toml`。历史命令默认使用 `BACKTEST_V1`、`LIVE_STRICT`、`DAILY_FIXED_CUTOFF_V1`、`THREE_WAY_2X1_BACKTEST_V1` 和该合成归档；`backtest run` 仍要求显式选择 FusionPolicy。

该 corpus 是非生产测试工件，不是历史来源归档。这里固定的 `LIVE_STRICT` 只命名正在验收的 captured/observed、available、ingested 与 cutoff 时间规则，不声称系统曾在 synthetic 时间戳对应的历史时点采集或持有数据，也不增加第三种 production data mode。所有真实归档仍严格遵守上节的 `LIVE_STRICT` 含义。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"

$Cli = ".\.venv\Scripts\football-system.exe"
$AcceptanceRoot = Join-Path ([IO.Path]::GetTempPath()) ("football-v040-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $AcceptanceRoot | Out-Null
$DatabasePath = (Join-Path $AcceptanceRoot "acceptance.db").Replace("\", "/")
$DatabaseUrl = "sqlite:///$DatabasePath"
$QuantRunId = "synthetic-quant-v1"
$BlendRunId = "synthetic-blend-v1"
$QuantReport = Join-Path $AcceptanceRoot "quant.md"
$BlendReport = Join-Path $AcceptanceRoot "blend.md"
$ComparisonReport = Join-Path $AcceptanceRoot "comparison.md"

& $Cli historical-archive validate
& $Cli historical-archive import --database-url $DatabaseUrl
& $Cli backtest run --database-url $DatabaseUrl --fusion-policy QUANT_ONLY_V1 --backtest-run-id $QuantRunId --output $QuantReport
& $Cli backtest run --database-url $DatabaseUrl --fusion-policy MARKET_QUANT_BLEND_V1 --backtest-run-id $BlendRunId --output $BlendReport
& $Cli backtest report --database-url $DatabaseUrl --backtest-run-id $QuantRunId
& $Cli backtest compare --database-url $DatabaseUrl --left-run-id $QuantRunId --right-run-id $BlendRunId --output $ComparisonReport
```

验收配置和报告合同必须传播精确字段值 `classification=SYNTHETIC_ACCEPTANCE_DATA` 与 `performance_warning=NOT REAL HISTORICAL PERFORMANCE`，并分别渲染以下精确 banner：

```text
SYNTHETIC ACCEPTANCE DATA
NOT REAL HISTORICAL PERFORMANCE
```

这些 banner 的解释优先级高于 `LIVE_STRICT`、合成时间戳、指标或其他任何 performance/provenance 暗示。输出只验收确定性、时间隔离、结算、持久化和报告，不代表真实历史采集或真实历史表现；synthetic 与 non-synthetic provenance 即使 data mode 相同，也不得视为相同或相互比较。

## 结算与回测边界

- walk-forward V1 对每个固定 slate 先以 `decision_as_of_at_utc` 创建并封存 AnalysisRun，再以更晚的 `evaluation_as_of_at_utc` 读取赛果；MatchResult 不进入决策 Manifest、Packet 或 Review context。
- 兼容的旧 Ticket Settlement 支持 `BACKTEST`、`THREE_WAY`、简单2串1：两腿全中采用冻结 `potential_gross_payout_fen`，任一腿失败返还为零；新 `strategy-pass settle` 以独立版本逐子注结算2X1/3X4/4X11，支持部分子注中奖。两条路径均不为缺失赛果创建伪 Settlement。
- 赛果和 Settlement 更正均追加 supersession 记录，不更新旧记录。Portfolio Settlement 聚合冻结 Ticket 和原 Cash，并分别报告 ROI on budget 与 ROI on deployed。
- 回测报告覆盖 `P_market`、`P_quant`、`P_final` 的 Brier、LogLoss、10-bin Calibration/ECE，以及覆盖率、资金、ROI、命中率、`NO_BET`、回撤、连败和风险实现指标。

## 离线文件桥

外部协作者只处理已封存 AnalysisRun 导出的白名单 Packet，并返回绝对 `P_llm`；本地严格校验、追加导入，再创建 FusionRun 和独立 PortfolioRevision。导入不会更新原 AnalysisRun、`P_final`、候选、Ticket 或 Portfolio。V1/V2 只接受手工 `P_quant` lineage；V3 增加 `started_at_utc`、结构化 model state/evaluation hashes、紧凑训练 match/result ID lineage，以及有预测/无预测两种显式状态。model-unavailable 比赛必须返回 `MODEL_UNAVAILABLE`，不会伪造概率；若整次运行没有任何可用 base prediction，则拒绝创建 FusionRun。合同见 `fankui/llm_review_v1_contract.md`、`fankui/llm_review_v2_contract.md` 与 `fankui/llm_review_v3_contract.md`。

## 0.9.0 已知限制

- Exact仅限封存marginals与`INDEPENDENT_MATCHES_V1`，不宣称现实独立；整个portfolio同场跨market joint不支持。
- Objective为`UNCALIBRATED_STRATEGY_POLICY_V1`；marginal optimizer不是global optimum，synthetic验收及legacy comparison不证明真实ROI/alpha。
- 默认optimizer catalog32、component worlds/support各65536、unique matches12、preferred4/absolute8；额外CPU/金额/bytes guards见已接受合同，超限明确unavailable。
- 旧0.8能力边界继续保留：

- 当前 live Elo 可能因缺少合格 `LIVE_STRICT` history 而明确返回 `MODEL_UNAVAILABLE`；不复制 `P_market`，也不生成伪 `P_quant` / `P_final`。
- ADR-0007 保持不变，`SOURCE_TIME_RESEARCH` 不允许重标或直接混入 live provider。批准历史只经独立 build 生成 release；当前快照的历史发布时间未知不会被审批补造。
- 正式市场为THREE_WAY、HANDICAP_THREE_WAY、TOTAL_GOALS、CORRECT_SCORE；无HALF_FULL。V2支持少量同市场choices，不支持同票同场跨市场compound。
- 新cohort/odds入口为显式synthetic离线fixture边界；真实来源能力、权利及模型表现仍需独立验证。
- Poisson baseline未校准，home/away各至少5场、league10场，最多1024 facts、lambda20、score256、|handicap|20；越界不fallback或自动调参。
- Profile V2默认preferred choices2/absolute3，每票96 atomic、全计划4096 atomic、512 candidates、12 matches、256可重建states、source8MiB；超限硬拒绝。0.9独立消费该封存candidate catalog并施加其自身更严格的exact bounds。
- 不连接真实 LLM API，不执行自动下注、账户登录、支付或出票。
- 不提供通用生产抓取平台、网页 GPT 历史回测、自动调参、复杂机器学习模型、Web 管理界面或通用调度器。一次真实闭环与 NO_BET 结果不等于样本外性能或收益证明。
- 结算不支持取消、腰斩、`VOID`、退款、加时、点球或串关降级；这些情况只能显式记录为 `UNSUPPORTED_SETTLEMENT_CASE`，不能猜测返还规则。

历史回测规范见 `fankui/backtest_v1_contract.md`；设计沿革见 `fankui/historical_data_backtest.md`，架构与模型资料见 `fankui/architecture.md` 和 `fankui/data_model.md`。

`0.9.0` 发布收尾完成后立即停止，等待1.0 Prospective Validation / Production Closeout的独立指令。
