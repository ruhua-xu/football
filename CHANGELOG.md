# Changelog

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
