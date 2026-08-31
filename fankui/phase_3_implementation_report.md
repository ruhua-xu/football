# 网页 GPT 离线融合与 Portfolio Revision 实现报告

## 范围

本阶段发布版本为 `0.3.0`，在 `0.2.0` 风险层和离线 Review 桥之上增加：

- `ANALYSIS_PACKET_V2` / `LLM_REVIEW_V2` 富上下文合同。
- append-only `FusionRun` 及逐场 Fusion result。
- append-only `PortfolioRevision` 决策快照。
- Portfolio Optimizer 内部 Exposure 约束、集中度惩罚和边际停止。
- `fusion-run create` 与 `portfolio-revision create` CLI 闭环。

没有增加真实比赛 API、真实 LLM API、API Key、Web、自动下注、3串4 或4串11。原概率、Selection EV、竞彩计奖、简单2串1生成和不可变 AnalysisRun 计算服务未重写。

## Packet 与 Review V2

每场 `review_context` 支持竞彩赔率、国际赔率、赔率变化、近期/主客场状态、休息日、赛程、伤停、预计/确认阵容、Evidence 和 DataQuality。

当前 Mock 只投影原 AnalysisRun 已封存的国际赔率与竞彩固定奖金。Evidence 保存正文、来源名称、来源表引用、源记录 ID、payload hash 和时间戳；其余语义数据保持 `null` 或空数组，并列入 `data_quality.missing_fields`，不伪造数据。

Packet 仍明确排除 `P_final`、EV、候选排名、Ticket、Portfolio、budget、stake、fusion weight 和资金策略参数。V2 Review 必须回显逐场 `review_context_id` / `review_context_hash`，且只能提交绝对 `P_llm`。

兼容矩阵是严格的 V1/V1 与 V2/V2。V1 保持 CLI 默认和原校验逻辑；同一 AnalysisRun 可以同时保存 V1、V2 Packet，但不能混用合同。

## FusionRun

FusionRun 引用 parent AnalysisRun 与 LLMReviewArtifact，并逐场保存：

- `P_base`、`P_llm`
- raw / applied probability delta
- confidence / data-quality factor
- `P_final`、fallback code
- policy、version、配置 JSON/hash 和创建时间

本地算法使用：

1. `raw_delta = P_llm - P_base`。
2. 乘以 Review confidence 和 Packet DataQuality。
3. 使用版本化 `max_probability_delta` 对三项修正统一缩放，保持 delta 总和为零。
4. 本地量化并生成 `P_final`。

Mock 配置的单项修正上限为 `0.08`；V1 Review 使用显式 legacy DataQuality factor `0.25`。`UNAVAILABLE`、`INVALID_CONTEXT` 等结果保留 `P_base` 并记录 fallback，不终止其他比赛。相同 Review、policy、version 和配置幂等；不同 Review 或配置可在同一 AnalysisRun 下产生多个 FusionRun。

## PortfolioRevision

PortfolioRevision 使用独立 Revision ID，引用 parent AnalysisRun 和 FusionRun。Revision 子对象全部使用 Revision ID 作为计算 scope，重新生成：

- revision FinalPrediction
- SelectionCandidate
- 简单2串1 TicketCandidate
- Portfolio 与 CashPosition
- Exposure 和 Stress 风险报告

完整 Revision 作为规范化 JSON/hash 追加保存。原 AnalysisRun 的 FinalPrediction、候选、Ticket、Portfolio 和风险表不更新、不解封。

## Optimizer 风险控制

新增配置：

- `max_match_exposure_ratio`
- `max_selection_exposure_ratio`
- `concentration_penalty`
- `min_marginal_score`

Mock 默认分别为 `0.60`、`0.60`、`0.10`、`0.10`。每次开新 Ticket 或增加 multiplier 前均检查预算、比赛 Exposure、方向 Exposure 和边际风险调整分数。不可满足时会重选、减投、保留 Cash，或返回 `NO_BET_RISK_LIMIT`；Domain validator 再次阻止超限 `RECOMMENDED` Portfolio。

## 持久化

Alembic head 为 `c8b7e2a4f190`，新增：

- `fusion_runs`
- `fusion_run_results`
- `portfolio_revisions`

SQLite 在 insert 时校验完成态 parent、Review/AnalysisRun 血缘、base prediction/match 血缘和 FusionRun/Revision 血缘。三张表均拒绝 UPDATE、DELETE、重复 INSERT 和 `INSERT OR REPLACE` 覆盖。

## 验证

- 全量 pytest：`93 passed`。
- Ruff：通过。
- `compileall`：通过。
- Alembic fresh upgrade 与 `alembic check`：通过。
- 从 `9d4e6f1a2c70`（0.2.0 head）升级到 `c8b7e2a4f190`：通过。
- runtime schema 与 migration schema：`159` 个 SQLite trigger 名称和定义一致。
- `football_system-0.3.0-py3-none-any.whl`：`114621` bytes。
- wheel SHA-256：`3c531f0a18fd69d38cd62bfe7deff98d1bc7b1caf3c52c405302d6a400e34c1e`。
- wheel 资源包含 V2 合同和新 migration。
- 隔离安装后的 CLI 完成 6 场 Mock 的 AnalysisRun → Packet V2 → Review validate/import → FusionRun → PortfolioRevision；生成 6 个 Fusion result 和 1 个 Revision，原 FinalPrediction outcome 行数保持不变。
