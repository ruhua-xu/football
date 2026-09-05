# Changelog

## Unreleased

- 增加显式 `live ingest-fixtures` vertical slice：仅在执行命令时从 `SPORTMONKS_KEY` 读取凭据，通过受审计的 Sportmonks HTTP client 获取指定 league/season/kickoff scope，并在规范化前封存 raw response。
- 增加 deterministic Team、Competition、Match identity persistence、`fixture_ingestion_captures` 和 `fixture_observations`，由 Alembic revision `a6c1f9e3b742` 原子落库并以 SQLite append-only/lineage trigger 防止改写。
- Fixture 首次创建的 identity row 显式保存 `fixture_ingestion_id`；catalog 同时要求来源 `available_at_utc` 与 capture `ingested_at_utc` 不晚于 cutoff，后续 kickoff、status 和名称漂移在对应 capture 实际入库前不可见，既有普通 identity 不会被后来 capture 重新分类。
- Sportmonks adapter 对 league、season、team type/gender、locale、官方 terminal status、单页 pagination 和 rate-limit metadata 执行 fail-closed 校验；HTTP transport 拒绝 redirect 并限制响应为 16 MiB。
- 包含 fixture capture 的数据库拒绝回退 `a6c1f9e3b742`，避免删除 capture provenance 后把保留的 identity 静默重分类；真实账户调用在 entitlement、目标联赛、字段和限流核验前仍为 operational `DEFER`。
- 增加固定标识 `ELO_THREE_WAY_BASELINE_V1` / `BASELINE_UNCALIBRATED` 的三向 Elo baseline：按 season 和双时间 cutoff 读取已封存 MatchResult correction，排除 target match；数据不足显式保存 `UNAVAILABLE`，不复制 `P_market`，不自动调参。
- 增加 `MVP_INPUT_MANIFEST_V3`、QuantModelState/TrainingFact/Evaluation 与 model-generated QuantPrediction lineage；Alembic revision `b7d4e9f2c631` 保持手工和模型 `P_quant` 互斥并封存完整 AnalysisRun graph。
- 增加 `BACKTEST_V2` 双阶段 walk-forward、decision/evaluation snapshot、availability-aware metrics、archive/result/training/financial lineage，以及原子、幂等、可重验的 SQLite repository；Alembic revision `c4e8a1d7f205` 增加八张 V2 表、append-only/completion triggers 和 populated downgrade guard。
- 增加 `ANALYSIS_PACKET_V3` / `LLM_REVIEW_V3`：保留 V2 review context，使用紧凑结构化 model lineage 表达 available/unavailable；V1/V2 输出字节和 manual-only 拒绝语义保持不变。
- 增加显式 `live ingest-market-odds` 与 `live ingest-sporttery`：前者通过 `ODDS_API_KEY` 获取 current h2h、先封存 raw response 再派生 deterministic consensus；后者只接受 reviewed `SPORTTERY_MANUAL_ARCHIVE_V2` 和已校验 source artifact，不实现网页爬虫。
- 增加 `live reconcile`、`live import-identity-review`、persisted-only `live prepare-analysis` 与离线 `live run-analysis`；identity issue 非致命落库，人工 mapping 仅在实际 import 后可见，preparation 按 cutoff 冻结 fixture observation、market consensus、Sporttery provenance 和数据质量 reason code，模型运行不得重新选择来源。
- Alembic revision `3cb19bcbdd88` 增加 12 张 live ingestion/review/preparation lineage 表及 runtime/migration append-only、duplicate-insert、cross-table triggers；有 live 数据时拒绝 destructive downgrade。
- Alembic revision `6e4b1a9c2d73` 增加 AnalysisRun 到 ready preparation 的多对一 append-only 关系；insert/completion triggers 要求 V3 model context 与冻结 fixture observation、market consensus、Sporttery snapshot 全量一致。V3 packet 对 prepared run 使用该 observation 的 kickoff，不用 immutable base Match 的旧赛程。
- `MARKET_CONSENSUS_MEDIAN_V1` 在生成 payload hash 前将派生公平赔率确定性量化为数据库 `NUMERIC(18,6)` 精度，保证 capture、持久化、重放与模型输入完全一致。
- 增加 provider-neutral、Sporttery-first 的 `live plan-slate`：严格读取 reviewed manual/lightweight slate，输出 frozen、deterministic、append-only `DAILY_SLATE_PLAN_V1` identity/reconciliation/capture work；空竞彩显式为 `NO_SPORTTERY_CANDIDATES` / `NO_ANALYSIS`，不得绕过 persisted preparation。`live ingest-market-odds --plan` 可消费 plan 中按赛事分组的精确 canonical match/window，同时保留原 `--match-id` 路径且不新增网络 Provider contract。

## 0.4.0 - 2026-09-01

- 增加 provider-neutral `HISTORICAL_ARCHIVE_V1`，按 `FIXTURES`、`MARKET_ODDS`、`SPORTTERY_BONUS`、`MANUAL_QUANT`、`MATCH_RESULTS` 和 `PROVIDER_MAPPINGS` 六类只读文件执行 schema、checksum、业务键、时间、mapping 与更正链校验。
- 实现 `LIVE_STRICT` 与显式回溯的 `SOURCE_TIME_RESEARCH`；后者保存独立 import 时间并在报告中标记 `RETROSPECTIVE_SOURCE_TIME_RESEARCH`，不与严格模式静默混合。
- 增加历史 Fixture/Odds/Sporttery/Quant/MatchResult 本地 Adapter；归档导入采用 `MANIFEST_PROVENANCE_ONLY`，只登记 Manifest、checksum 和来源，决策输入与赛果分别在合法 cutoff 阶段物化。
- 增加 append-only MatchResult、Ticket Settlement 和 Portfolio Settlement，使用冻结 Ticket、stake、潜在毛返还及规则版本；当前仅结算 `BACKTEST`、`THREE_WAY`、简单2串1，不支持 `VOID`、退款或串关降级。
- 增加固定 slate 的 `BACKTEST_V1` walk-forward、不可变 BacktestRun/Slice、归档 provenance 与 Manifest hash 绑定，以及 Brier、LogLoss、Calibration/ECE、覆盖率、资金、ROI、回撤、连败和风险实现指标。
- 增加 `QUANT_ONLY_V1` 与 `MARKET_QUANT_BLEND_V1` 的同口径并排比较；比较会校验模式、归档、时间窗口、预算、阈值、约束和冻结输入，不自动选择“最佳策略”。
- 增加 8 条公开历史 CLI 路径：归档 validate/import、赛果 list、结算 create/report、回测 run/report/compare。
- 增加 Alembic 迁移 `f3a1c6d8e204`，从 `c8b7e2a4f190` 添加归档 provenance、赛果、结算、Backtest 和指标快照/血缘表及 SQLite append-only 触发器；测试覆盖 fresh schema、0.3.0 head 升降级、外键检查、Alembic check 和 runtime/migration schema 对齐。
- 明确 `0.4.0` 仅支持 SQLite，建库、Schema 与迁移入口在加载其他数据库驱动前拒绝非 SQLite URL。
- 增加固定的 10-slate/60-match 合成验收数据，报告强制标记 `SYNTHETIC ACCEPTANCE DATA` 和 `NOT REAL HISTORICAL PERFORMANCE`；不将其描述为真实历史表现。
- 增加 Python 3.12 GitHub Actions 工作流配置，包含安装、ruff、compileall、pytest、fresh Alembic upgrade/check、wheel 构建与已安装 wheel E2E 步骤；本条仅记录工作流和覆盖范围，不声明远端 CI 运行结果。
- wheel 显式打包配置、迁移、文档和合成归档；隔离安装验收脚本覆盖全部 8 条历史 CLI 路径、两种策略、持久化报告和迁移 head，并拒绝从源码目录误导入。

## 0.3.0 - 2026-08-31

- 增加 `ANALYSIS_PACKET_V2` 富 MatchReviewContext、正文 Evidence、来源信息和显式 DataQuality，同时保留 V1 合同。
- 增加 `LLM_REVIEW_V2` context ID/hash 绑定；LLM 仍只能提交绝对 `P_llm`。
- 增加 append-only `FusionRun`，本地计算 raw/applied delta、confidence/data-quality 因子、版本化截断及 unavailable fallback。
- 增加独立 `PortfolioRevision`，从 FusionRun 重算候选、简单2串1、Portfolio 与风险报告，不修改原 AnalysisRun。
- 将比赛/方向 Exposure 硬约束、集中度惩罚和边际分配停止策略纳入 Optimizer，允许主动保留 Cash 或输出 `NO_BET_RISK_LIMIT`。
- 增加从 0.2.0 schema 升级的 `c8b7e2a4f190` 迁移、append-only/跨运行血缘触发器及完整 CLI 文件协作闭环。

## 0.2.0 - 2026-08-31

- 将现金建模为每个 Portfolio 的显式合法持仓，包括全现金 `NO_BET`。
- 增加按顶层 Ticket stake 计算的比赛与方向 Exposure。
- 增加最高暴露单场、最高暴露两场和全部暴露比赛的不利 Stress Test。
- Stress V2 使用精确状态搜索，并将可配置 Portfolio 上限约束为最多 12 张 Ticket。
- 风险和压力测试工件与 AnalysisRun 同事务保存并在完成后封存。
- 增加离线 `analysis_packet` 导出与严格 `llm_review` 校验、导入流程。
- Packet/Review 使用 SHA-256 绑定、追加型存储和幂等导入，不调用任何真实 API。
- 增加完成态风险图校验、跨运行血缘、`INSERT OR REPLACE` 防护和迁移前完整性检查。

## v0.1.0 - 2026-08-31

- 冻结首个可回放 MVP，Git 提交为 `fceb945d07218290dc85b465e885a47ae9912c3f`。
- 实现 Mock 输入、概率、Fusion、Selection EV、简单2串1、竞彩计奖、Portfolio、`NO_BET`、SQLite、CLI 和不可变 AnalysisRun。
