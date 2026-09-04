# Football System

可回放的足球比赛分析、中国竞彩简单2串1组合决策、Portfolio 风险分析、离线 LLM 文件协作和严格时间序列回测工具。

## 发布状态

- `v0.1.0` 固定在提交 `fceb945d07218290dc85b465e885a47ae9912c3f`，保留原始 MVP 行为。
- 当前已发布 package metadata 保持 `0.4.0`；`0.5.0` 真实数据工作仍为 `Unreleased`，且不改变既有概率、EV、计奖、风险、离线 Review、FusionRun、PortfolioRevision 和不可变 AnalysisRun 合同。
- `0.4.0` 仅支持 SQLite。运行时建库、Schema 创建和 Alembic 迁移都会在加载其他数据库驱动前拒绝非 SQLite URL。
- 只有显式执行 `live ingest-fixtures` 或 `live ingest-market-odds` 才会分别读取 `SPORTMONKS_KEY` 或 `ODDS_API_KEY` 并访问网络；Sporttery、reconcile、review import、prepare-analysis 和 run-analysis 均为本地 I/O。项目不连接真实 LLM API，不执行自动下注。

## 当前能力

- Mock 或本地历史归档的 Fixture、国际市场赔率、竞彩固定奖金、手工 `P_quant`，以及固定参数的三向 Elo baseline `P_quant`。
- `THREE_WAY` 去水、`QUANT_ONLY_V1`、`MARKET_QUANT_BLEND_V1`、Selection EV 和简单2串1。
- 显式 Cash、`NO_BET`、Exposure、确定性 Stress Test 和 Portfolio 风险约束。
- SQLite、SQLAlchemy、Alembic、不可变 AnalysisRun 和追加型审计工件。
- V1/V2/V3 `analysis_packet`、`llm_review`、append-only FusionRun 和独立 PortfolioRevision；V1/V2 字节合同继续保持 manual-only，V3 显式承载 manual/model lineage 和 model unavailable。
- Historical Archive V1、MatchResult、Ticket/Portfolio Settlement、walk-forward、概率/资金/回撤/覆盖率指标和并排策略比较；`BACKTEST_V2` 额外冻结 Elo state/evaluation、双 cutoff、归档和财务结算血缘。
- Sportmonks fixture raw capture、canonical identity、append-only observation lineage 和 SQLite 原子落库；仓库与自动化测试不包含账户响应或真实第三方数据。
- The Odds API current h2h raw capture、全部 bookmaker 快照、`MARKET_CONSENSUS_MEDIAN_V1` lineage，以及 reviewed `SPORTTERY_MANUAL_ARCHIVE_V2` capture；unresolved/ambiguous identity 作为结构化 issue 落库，不模糊绑定。
- append-only live reconciliation/review 与 persisted-only analysis preparation；preparation 按 decision cutoff 冻结 fixture observation、market consensus 和 Sporttery provenance，并对缺失、陈旧或覆盖不足输入给出 reason code。
- `live run-analysis` 只重放一份 ready preparation，以固定 `ELO_THREE_WAY_BASELINE_V1` 创建 V3 model AnalysisRun；run 与 preparation 的完整 ready-match graph 由专用 append-only 关系封存。

## Live Source Ingestion

`live ingest-fixtures` 是显式启用的 `LIVE_STRICT` 命令。它要求调用方提供 kickoff window、provider league/season ID、season、competition type 和 team type，并逐项校验 provider 返回的 league、season、team type/gender，不从不完整 payload 猜测 identity scope。API token 只从 `SPORTMONKS_KEY` 读取并放入 `Authorization` header；raw payload 和 secret-safe request metadata 会先写入 `data/raw`，完整 capture 验证通过后才迁移或打开数据库。

```text
football-system live ingest-fixtures --kickoff-from 2026-09-03T00:00:00Z --kickoff-to 2026-09-03T23:59:59Z --league-id 501 --provider-season-id 23690 --season 2026/27 --competition-type LEAGUE --team-type CLUB
```

每次成功响应使用本地 receipt time 作为 availability，并单独保存数据库 ingestion time；首次由 capture 创建的 identity row 显式绑定 `fixture_ingestion_id`，catalog 只有在 availability 和对应 ingestion 均不晚于 cutoff 时才可见。既有普通 identity 不会被后来 capture 重新分类；后续 status/kickoff 变化追加到 `fixture_observations`，名称漂移追加新 alias/mapping。当前 vertical slice 要求一个经过 league filter 的完整单页响应；若 terminal pagination metadata 不一致或 `pagination.has_more=true`，会拒绝落库并要求缩小 kickoff window，绝不把截断页当作完整数据。实际账户调用在核对 token entitlement、目标联赛、字段和限流 metadata 前仍处于 operational `DEFER`；自动化验收仅使用 scripted transport 和自造 payload，不访问外网。

其余 live source 命令形成显式 capture -> reconcile -> review -> recapture -> prepare 流程。odds 命令先按命令起始时刻加载 provider-specific identity catalog，再发起一次 current endpoint 请求；raw response 在 normalization 前封存，source snapshots 与 consensus lineage 在同一事务中追加。Sporttery 命令只接受 reviewed V2 本地文件及其 SHA-256 source artifact，不实现网页爬虫。首次无法解析的 provider event 可先落为 issue，导入人工 mapping 后再重新 capture：

```text
football-system live ingest-market-odds --kickoff-from 2026-09-03T00:00:00Z --kickoff-to 2026-09-03T23:59:59Z --match-id <internal-match-id> --sport-key soccer_epl --season 2026/27 --competition-type LEAGUE
football-system live ingest-sporttery --archive data/manual/sporttery.json --kickoff-from 2026-09-03T00:00:00Z --kickoff-to 2026-09-03T23:59:59Z
football-system live reconcile --ingestion-id <ingestion-id> --output exchange/live_reconciliation.json
football-system live import-identity-review --review exchange/live_identity_review.json
football-system live prepare-analysis --decision-as-of 2026-09-02T12:00:00Z --kickoff-from 2026-09-03T00:00:00Z --kickoff-to 2026-09-03T23:59:59Z --competition-id <competition-id> --season-id 2026/27 --maximum-odds-age-seconds 21600 --minimum-bookmaker-count 2 --output exchange/live_analysis_preparation.json
football-system live run-analysis --date 2026-09-03 --budget 100 200 --analysis-run-id <analysis-run-id>
football-system analysis-packet export --config config/live.toml --analysis-run-id <analysis-run-id> --schema-version ANALYSIS_PACKET_V3 --output exchange/live_analysis_packet_v3.json
```

`reconcile`、`import-identity-review`、`prepare-analysis` 和 `run-analysis` 不读取 API key、不构造 HTTP transport。Preparation 只查询 cutoff 前已持久化的数据；缺少任一必要来源时保存 `NO_ANALYSIS_INSUFFICIENT_DATA`，不会临时联网补数。`run-analysis --date` 仅在该 UTC 日期恰好匹配一份 ready preparation 时运行；否则必须使用 `--preparation-id`。当前没有 provenance-qualified persisted 训练赛果时，Elo evaluation 明确保存 `UNAVAILABLE`，不会复制 `P_market` 或生成伪 `P_final`。以上真实 provider CLI 已实现不改变 operational `DEFER`：当前仓库仍未读取任何真实 key，也没有可声明为真实表现的数据或 packet。

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
- Ticket Settlement 仅支持 `BACKTEST`、`THREE_WAY`、简单2串1：两腿全中采用冻结 `potential_gross_payout_fen`，任一腿失败返还为零；缺失赛果不创建伪 Settlement。
- 赛果和 Settlement 更正均追加 supersession 记录，不更新旧记录。Portfolio Settlement 聚合冻结 Ticket 和原 Cash，并分别报告 ROI on budget 与 ROI on deployed。
- 回测报告覆盖 `P_market`、`P_quant`、`P_final` 的 Brier、LogLoss、10-bin Calibration/ECE，以及覆盖率、资金、ROI、命中率、`NO_BET`、回撤、连败和风险实现指标。

## 离线文件桥

外部协作者只处理已封存 AnalysisRun 导出的白名单 Packet，并返回绝对 `P_llm`；本地严格校验、追加导入，再创建 FusionRun 和独立 PortfolioRevision。导入不会更新原 AnalysisRun、`P_final`、候选、Ticket 或 Portfolio。V1/V2 只接受手工 `P_quant` lineage；V3 增加 `started_at_utc`、结构化 model state/evaluation hashes、紧凑训练 match/result ID lineage，以及有预测/无预测两种显式状态。model-unavailable 比赛必须返回 `MODEL_UNAVAILABLE`，不会伪造概率；若整次运行没有任何可用 base prediction，则拒绝创建 FusionRun。合同见 `fankui/llm_review_v1_contract.md`、`fankui/llm_review_v2_contract.md` 与 `fankui/llm_review_v3_contract.md`。

## 范围边界

不支持真实历史数据或生产抓取、真实 LLM 调用、网页 GPT 历史回测、自动调参、机器学习 `P_quant`、3串4、4串11、复式、Web、调度器或自动下注。结算不支持取消、腰斩、`VOID`、退款、加时、点球或串关降级；这些情况只能显式记录为 `UNSUPPORTED_SETTLEMENT_CASE`，不能猜测返还规则。

历史回测规范见 `fankui/backtest_v1_contract.md`；设计沿革见 `fankui/historical_data_backtest.md`，架构与模型资料见 `fankui/architecture.md` 和 `fankui/data_model.md`。
