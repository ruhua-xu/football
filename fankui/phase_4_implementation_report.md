# Football System v0.4.0 发布候选实施报告

## 发布结论

本报告记录 `v0.4.0` release candidate 的本地源码、wheel 和远端 CI 验收状态，不是公开 package publication 声明。包元数据已统一为 `0.4.0`，全量本地测试、迁移/schema parity、acceptance 重放、最终 wheel 隔离安装验收和 GitHub Actions Python 3.12 门禁均已通过。Phase 4 implementation commit `13e97a12ceed0df0d7f11fe8f8732c17845e7fe9` 已快进合并并推送到 `main`。

| 项目 | 当前事实 |
| --- | --- |
| RC 目标版本 | `0.4.0` |
| 当前 `pyproject.toml` 包版本 | `0.4.0` |
| Alembic head | `f3a1c6d8e204` |
| 数据库边界 | 仅支持 SQLite |
| 集成分支 | `main` |
| Phase 4 implementation commit | `13e97a12ceed0df0d7f11fe8f8732c17845e7fe9` |
| 远端提交状态 | 已推送到 `origin/main` |
| 本地 wheel artifact | `football_system-0.4.0-py3-none-any.whl`，`291273` bytes |
| wheel SHA-256 | `086781775f954c51d9a78d90e3781db3a7db243ee89b57f92c5c4326db8fc7c6` |
| 远端 CI | GitHub Actions run [`33521490204`](https://github.com/ruhua-xu/football/actions/runs/33521490204) 成功 |

## 实现范围

当前源码包含以下闭环：

- provider-neutral、版本化、只读的本地 Historical Archive 验证与 provenance 注册。
- Fixture、Market Odds、Sporttery Bonus、Manual Quant 和 MatchResult 的 cutoff-aware 本地 provider。
- append-only MatchResult、Ticket Settlement、Portfolio Settlement、BacktestRun、BacktestSlice 和指标快照持久化。
- `THREE_WAY` 简单 `2X1` 回测结算，以及原 AnalysisRun / PortfolioRevision 两种结算 scope。
- 固定 slate 的 `DAILY_FIXED_CUTOFF_V1` walk-forward，分别冻结决策输入和评估结果。
- P_market、P_quant、P_final 的 Brier、LogLoss、10-bin calibration/ECE，以及资金、coverage、NO_BET、drawdown 和 exposure 实现指标。
- `historical-archive validate/import`、`match-results list`、`settlement create/report`、`backtest run/report/compare` 八条 CLI 路径。

现有 `ANALYSIS_PACKET_V1/V2`、`LLM_REVIEW_V1/V2`、FusionRun 和 PortfolioRevision 合同未被改写。

## SQLite 边界与迁移

运行时 engine、schema 创建和 Alembic 入口都会在加载其他数据库驱动前拒绝非 SQLite URL。当前版本不声明 PostgreSQL、MySQL 或其他后端兼容性。

迁移 `f3a1c6d8e204` 从 `c8b7e2a4f190` 增加以下恰好 11 张持久化表：

1. `historical_archive_imports`
2. `match_results`
3. `ticket_settlements`
4. `ticket_settlement_match_results`
5. `portfolio_settlements`
6. `portfolio_settlement_tickets`
7. `backtest_runs`
8. `backtest_slices`
9. `backtest_metric_snapshots`
10. `backtest_metric_settlements`
11. `backtest_metric_ticket_settlements`

这些表在 SQLite 下安装 append-only/immutable 和血缘校验 trigger，拒绝 UPDATE、DELETE、覆盖式重复 INSERT、跨 scope 引用及非法更正链。指标快照保存版本、规范化 JSON/hash，并显式关联参与聚合的 Portfolio/Ticket Settlement。

本地测试覆盖 fresh schema、`c8b7e2a4f190 -> f3a1c6d8e204` 升级与回退、`PRAGMA foreign_key_check`、`alembic check`，以及 runtime metadata schema/trigger 与 migration head 的一致性。

## Historical Archive 合同

`HISTORICAL_ARCHIVE_V1` manifest 固定包含：

```text
archive_schema_version
archive_id
provider_code
dataset_kind
created_at_utc
source_reference
source_description
license_note
data_mode
payload_sha256
record_count
```

支持的六种独立 dataset kind 为：

```text
FIXTURES
MARKET_ODDS
SPORTTERY_BONUS
MANUAL_QUANT
MATCH_RESULTS
PROVIDER_MAPPINGS
```

归档 loader 要求 UTF-8 JSON、唯一对象键、timezone-aware 时间、正确记录数和 canonical payload SHA-256；拒绝 NaN/Infinity、重复业务键、非法时间顺序、坏 payload hash、缺失/冲突 mapping 和不完整或分叉的 MatchResult 更正链。归档文件保持只读，注册操作只向 `historical_archive_imports` 追加 manifest provenance，合同值为 `MANIFEST_PROVENANCE_ONLY`；决策输入仅在 cutoff 选择后由既有 AnalysisRun 持久化，MatchResult 只在评估阶段物化。

`source_reference`、`source_description` 和 `license_note` 是强制 provenance 字段，但系统不能仅凭非空字符串证明许可真实性或历史时间来源。真实来源仍需外部审查，参见 `fankui/historical_source_research.md`。

## Data Mode 合同

真实归档和生产运行的数据模式恰好为 `LIVE_STRICT` 与 `SOURCE_TIME_RESEARCH`，不增加第三种模式。

| 模式 | cutoff 规则 | 实际导入时间 | 报告语义 |
| --- | --- | --- | --- |
| `LIVE_STRICT` | 快照要求 `captured <= available <= ingested <= decision cutoff`；赛果要求 `observed <= available <= ingested <= evaluation cutoff` | record 不携带 retrospective import 时间 | 标签 `LIVE_STRICT` |
| `SOURCE_TIME_RESEARCH` | cutoff 约束可信的来源 `captured/observed` 与 `available` 时间；source-time `ingested_at_utc` 明确等于 `available_at_utc` | `retrospective=true` 且 `imported_at_utc` 单独保存，必须晚于来源已知时间 | 醒目标记 `RETROSPECTIVE_SOURCE_TIME_RESEARCH` |

同一 store 的模式不能静默混用；存在多种模式时必须显式选择。无法可靠确认来源的 historical availability timestamp 时，该数据不满足严格 point-in-time，不能伪造时间，也不能借研究模式伪装成实时留存数据。表中 `LIVE_STRICT` 的历史采集含义对所有真实归档保持严格；下文 synthetic acceptance 仅有非生产测试语义例外。

## Settlement 合同

第一版结算策略为 `THREE_WAY_2X1_BACKTEST_V1`，只支持：

- 三项胜平负 `THREE_WAY`。
- 简单 `2X1`，每张 Ticket 恰好两个不同比赛 leg。
- `settlement_kind=BACKTEST`，不代表真实购买或官方兑奖。

MatchResult 当前表示常规时间加伤停补时后的主客队比分，并由比分确定 `HOME_WIN`、`DRAW` 或 `AWAY_WIN`。结算只能读取冻结 Ticket 的 stake、`potential_gross_payout_fen` 和 `payout_policy_version`，不能重新读取当前赔率或配置：

```text
两腿全中: WON, gross_payout = frozen potential_gross_payout
任一腿未中: LOST, gross_payout = 0
profit_loss = gross_payout - stake
```

缺失赛果返回 `MISSING_RESULT`，记录 coverage 且不创建假 Settlement。取消、腰斩、VOID、退款、加时、点球/点球大战、串关降级，以及非三项市场、非 2X1 和非 BACKTEST 类型均不猜测规则，返回 `UNSUPPORTED_SETTLEMENT_CASE` 及具体原因。

Portfolio 只在其全部冻结 Ticket 可结算时聚合：

```text
ending_capital = original_cash + sum(ticket gross payout)
profit_loss = ending_capital - budget
ROI_on_budget = profit_loss / budget
ROI_on_deployed = profit_loss / deployed_stake
```

全 Cash Portfolio 不创建 Ticket Settlement，`ROI_on_deployed` 在零投入时为 N/A。更正通过新的 MatchResult、Ticket Settlement 和 Portfolio Settlement 的 `supersedes_*` 链追加，旧记录不更新。Settlement service 支持 `ANALYSIS_RUN` 与 `PORTFOLIO_REVISION` scope；walk-forward V1 当前只回放原始 base AnalysisRun。

## 时间语义

系统明确分离历史知识 cutoff 与真实执行时间：

- `decision_as_of_at_utc` 是决策知识边界。Fixture、mapping、赔率、竞彩奖金和 Manual Quant 必须在该时点可见，AnalysisRun 在读取赛果前完成并冻结。
- `evaluation_as_of_at_utc` 是结果知识边界，必须晚于 decision cutoff 和 slate kickoff window。只有该时点前合法可见的 MatchResult 才可用于结算。
- 回测 Settlement 的 `settled_at_utc` 使用 evaluation cutoff，表示“按该评估时点结算”的 as-of 语义，不冒充程序真实运行时刻。
- `execution_time_utc` 是程序实际执行记录，缺省为 `utc_now()`，不得早于最后一个 evaluation cutoff。walk-forward 中各 AnalysisRun 的 started/completed 时间以及 BacktestRun 的 created/execution 时间使用该真实执行时间。
- 归档的本地 `imported_at_utc` 另行保存，不能回填为历史 cutoff。

当前 acceptance report 的历史 cutoffs 位于 2025-01-06 至 2025-01-16；实际执行时间分别记录为：

- `QUANT_ONLY_V1`: `2026-09-01T14:33:24.868104Z`
- `MARKET_QUANT_BLEND_V1`: `2026-09-01T14:33:48.716757Z`

因此报告没有声称系统在 2025 年真实运行过这些分析。

## Synthetic Acceptance 数据

**SYNTHETIC ACCEPTANCE DATA**

**NOT REAL HISTORICAL PERFORMANCE**

验收目录包含 6 个静态归档、共 365 条记录：

| dataset kind | 记录数 |
| --- | ---: |
| `FIXTURES` | 61 |
| `MARKET_ODDS` | 62 |
| `SPORTTERY_BONUS` | 61 |
| `MANUAL_QUANT` | 61 |
| `MATCH_RESULTS` | 60 |
| `PROVIDER_MAPPINGS` | 60 |
| 合计 | 365 |

数据计划为 10 个按时间排序且不重叠的 slate，每个 6 场，共 60 场计划比赛。评估 cutoff 覆盖 59 场，match coverage 为 `59/60 = 0.983333333333`。

特殊样例恰好包括一条赛果更正链和一个缺失赛果：

- `ha-20250108-02` 从 `ha-result-ha-20250108-02-v1` 更正为 `ha-result-ha-20250108-02-v2`；更正时间晚于该 slate 的 evaluation cutoff，因此当期回放仍使用 v1，后续 cutoff 可见 v2。
- `ha-20250115-06` 没有可用结果，必须进入 partial coverage，不能静默删除。

60 条 MatchResult 记录由 59 场有结果的比赛加一条更正版本构成；缺失比赛没有伪造结果。其他数据种类多出的记录用于 future-version 和 cutoff 边界验收。

该目录是非生产测试工件，不是历史来源归档。原始验收要求将 `config/backtest.toml` 固定为 `data_mode=LIVE_STRICT`；这里的 `LIVE_STRICT` 只命名被验收的 captured/observed、available、ingested 与 cutoff 校验规则，不证明系统在 2025 年真实采集或持有数据，也不增加第三种 production data mode。所有真实归档的严格含义保持不变。

验收配置和报告合同显式使用 `classification=SYNTHETIC_ACCEPTANCE_DATA` 与 `performance_warning=NOT REAL HISTORICAL PERFORMANCE`，报告必须将两者显示为上方 banner。两条 banner 的解释优先级高于 data mode、合成时间戳、指标、`source_description`、fixture `license_note` 或其他任何 performance/provenance 暗示。Synthetic 与 non-synthetic provenance 即使 data mode 相同，也不得视为相同、合并指标或相互比较。

## 两种策略结果

以下数值逐项来自 `exchange/backtest_acceptance_report.md`。两侧使用同一 synthetic 归档、时间窗口、预算、阈值和 Portfolio constraints；结果只用于功能验收，不作策略排名、推荐或盈利承诺。

| 指标 | `QUANT_ONLY_V1` | `MARKET_QUANT_BLEND_V1` |
| --- | ---: | ---: |
| P_final sample count | 59 | 59 |
| P_final multiclass Brier | 0.395661016949 | 0.428734712113 |
| P_final multiclass LogLoss | 0.71739548562 | 0.763860053431 |
| P_final ECE | 0.308813559322 | 0.267442497191 |
| ROI on budget | 0.62106 | 0.60279 |
| ROI on deployed | 5.750555555556 | 5.581388888889 |
| max drawdown | 0 fen | 0 fen |
| slate coverage | 0.9 (9/10) | 0.9 (9/10) |
| match coverage | 0.983333333333 (59/60) | 0.983333333333 (59/60) |
| ticket coverage | 1 (18/18) | 1 (18/18) |
| NO_BET count | 1 | 1 |
| NO_BET ratio | 0.1 | 0.1 |
| ticket hit rate | 0.944444444444 | 0.944444444444 |

synthetic 数据被刻意构造为覆盖 WON、LOST、NO_BET、Cash、cutoff 和 partial coverage 路径；高 ROI 和零回撤不能外推到真实历史或生产环境。

## 本地验证状态

| 检查 | 当前结果 |
| --- | --- |
| 全量 pytest | `266 passed, 24 warnings`，Python 3.13.9，本次运行 28.50s |
| warnings | 24 条均来自 `test_historical_persistence.py` 触发的 Python 3.12+ sqlite3 默认 datetime adapter deprecation warning |
| Ruff | `python -m ruff check .` 通过，`All checks passed!` |
| compileall | `python -m compileall -q src tests migrations scripts` 通过 |
| workflow 静态检查 | YAML 可解析；normal push、PR three-dot 与 empty-tree new-branch 的 `git diff --check` 命令本地执行通过；本机未安装 `actionlint` |
| schema parity | runtime metadata 与 Alembic head 的表、列、约束、索引和 SQLite trigger 签名一致；包含在通过的全量测试中 |
| migration/foreign keys | fresh、`c8b7e2a4f190 -> f3a1c6d8e204` upgrade/downgrade、f3 offline SQL、`alembic check` 与 `foreign_key_check` 均通过 |
| installed wheel E2E | 37 个允许资源、八条 CLI、两种策略各 10 slices、migration head 与持久化 graph 全部通过；in-memory 与重载报告逐字一致 |

最终 `football_system-0.4.0-py3-none-any.whl` 由 `scripts/wheel_e2e.py` 在源码目录外创建独立 virtual environment、从 wheel 安装并验证 import provenance。验收确认不存在 `.db`、`.env`、`yaoqiu/` 或 `scripts/` 泄漏，并实际完成历史归档 validate/import、两种 backtest、report/compare、MatchResult list、Settlement create/report 及持久化状态复核。

`.github/workflows/ci.yml` 已配置 Python 3.12 下的依赖安装、Ruff、compileall、pytest、fresh migration、`alembic check`、wheel build、installed-wheel E2E 和 whitespace check。`main` push 对 implementation commit `13e97a12ceed0df0d7f11fe8f8732c17845e7fe9` 触发 GitHub Actions run [`33521490204`](https://github.com/ruhua-xu/football/actions/runs/33521490204)，job [`99901598745`](https://github.com/ruhua-xu/football/actions/runs/33521490204/job/99901598745) 于 `2026-09-01T14:45:54Z` 开始、`2026-09-01T14:47:17Z` 完成，结论为 `success`。依赖安装、Ruff、compileall、全量 pytest、fresh migration head、wheel build、installed-wheel E2E 和 whitespace check 各步骤均成功，远端 CI 门禁已用可核验 run 关闭。

## 已知限制与排除项

- 未下载或提交真实第三方历史数据，未产生真实来源回测结果；许可、再分发权和 historical availability timestamp provenance 仍待确认。
- 项目尚未选择软件分发许可证，也未声明 PEP 639 `license`/`license-files` 元数据；公开发布 package 必须由 owner 决定。该项目级决定与 archive manifest 的 fixture `license_note` 不同，后者只记录数据工件的 provenance/限制，不能充当项目软件许可证。
- Phase 4 implementation commit 已推送并通过远端 CI，但尚未创建 `v0.4.0` Git tag、GitHub Release 或公开 package publication。
- 数据库仅支持 SQLite；不承诺其他后端。
- walk-forward V1 仅支持统一 slate cutoff 的 `DAILY_FIXED_CUTOFF_V1`，不支持逐场 T-24h 或重叠 slate。
- 历史 walk-forward 只使用 base AnalysisRun，不执行网页 GPT、真实 LLM 或 PortfolioRevision 历史比较。
- Settlement 不支持取消、腰斩、VOID、退款、加时、点球、串关降级、3串4、4串11或其他市场/过关类型。
- 不包含机器学习 P_quant、Elo、Poisson、LightGBM、CatBoost、自动调参或基于全样本 ROI 的策略选择。
- 不包含真实 LLM API、自动下注、账户/出票/兑奖、Web Dashboard、定时任务或生产抓取。
- synthetic acceptance 不能用于策略背书、真实收益估计或生产风险限额选择。

## 下一阶段建议

1. 由 owner 选择项目分发许可证并确定 PEP 639 元数据后，才进行公开 package publication。
2. owner 确认发布授权和元数据后，再从通过门禁的提交创建 `v0.4.0` tag、GitHub Release 或 package publication。
3. 在引入真实 adapter 前完成许可、原始数据保存/再分发权和历史可用时间证明审查；不满足 point-in-time 的来源不得标为 `LIVE_STRICT`。
4. 评估替换 sqlite3 默认 datetime adapter，以消除 Python 3.12+ 下的 24 条 deprecation warning。
5. 在扩展 VOID/退款/延期、逐场 cutoff、PortfolioRevision 回测、其他数据库或真实数据前进行下一次架构与规则评审。
