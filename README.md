# Football System

可回放的足球比赛分析、中国竞彩简单2串1组合决策、Portfolio 风险分析、离线 LLM 文件协作和严格时间序列回测工具。

## 发布状态

- `v0.1.0` 固定在提交 `fceb945d07218290dc85b465e885a47ae9912c3f`，保留原始 MVP 行为。
- 当前发布准备版本为 `0.4.0`；`0.3.0` 的概率、EV、计奖、风险、离线 Review、FusionRun、PortfolioRevision 和不可变 AnalysisRun 合同保持不变。
- `0.4.0` 仅支持 SQLite。运行时建库、Schema 创建和 Alembic 迁移都会在加载其他数据库驱动前拒绝非 SQLite URL。
- 项目不连接真实比赛 API 或真实 LLM API，不读取 API Key，不执行自动下注。

## 当前能力

- Mock 或本地历史归档的 Fixture、国际市场赔率、竞彩固定奖金和手工 `P_quant`。
- `THREE_WAY` 去水、`QUANT_ONLY_V1`、`MARKET_QUANT_BLEND_V1`、Selection EV 和简单2串1。
- 显式 Cash、`NO_BET`、Exposure、确定性 Stress Test 和 Portfolio 风险约束。
- SQLite、SQLAlchemy、Alembic、不可变 AnalysisRun 和追加型审计工件。
- V1/V2 `analysis_packet`、`llm_review`、append-only FusionRun 和独立 PortfolioRevision。
- Historical Archive V1、MatchResult、Ticket/Portfolio Settlement、walk-forward、概率/资金/回撤/覆盖率指标和并排策略比较。

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

外部协作者只处理已封存 AnalysisRun 导出的白名单 Packet，并返回绝对 `P_llm`；本地严格校验、追加导入，再创建 FusionRun 和独立 PortfolioRevision。导入不会更新原 AnalysisRun、`P_final`、候选、Ticket 或 Portfolio。合同见 `fankui/llm_review_v1_contract.md` 与 `fankui/llm_review_v2_contract.md`。

## 范围边界

不支持真实历史数据或生产抓取、真实 LLM 调用、网页 GPT 历史回测、自动调参、机器学习 `P_quant`、3串4、4串11、复式、Web、调度器或自动下注。结算不支持取消、腰斩、`VOID`、退款、加时、点球或串关降级；这些情况只能显式记录为 `UNSUPPORTED_SETTLEMENT_CASE`，不能猜测返还规则。

历史回测规范见 `fankui/backtest_v1_contract.md`；设计沿革见 `fankui/historical_data_backtest.md`，架构与模型资料见 `fankui/architecture.md` 和 `fankui/data_model.md`。
