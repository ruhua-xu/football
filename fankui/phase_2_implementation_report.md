# Portfolio Risk 与离线 Review 桥实现报告

## 范围

本阶段在 `v0.1.0` MVP 之上增加：

- 显式 `CashPosition`，金额始终等于 `unused_budget_fen`。
- `TOP_LEVEL_STAKE_EXPOSURE_V1` Portfolio Risk 报告。
- 比赛级和方向级 stake Exposure。
- `DETERMINISTIC_PORTFOLIO_STRESS_V2` 压力场景及逐票状态。
- `ANALYSIS_PACKET_V1` 与 `LLM_REVIEW_V1` 离线文件桥。

现有概率、EV、竞彩奖金舍入和不可变 AnalysisRun 计算未重写。没有新增 HTTP、LLM SDK、API Key 或自动下注路径。

## 风险与压力测试

每个 Portfolio 都保存一个现金持仓和一个风险报告。Exposure 的分母同时保留预算和已投入资金；比赛 Exposure 不可跨比赛相加，因为2串1 Ticket 会出现在两个比赛桶中。

首批确定性场景：

- `TOP_EXPOSURE_MATCH_ADVERSE`
- `TOP_TWO_EXPOSURE_MATCHES_ADVERSE`
- `ALL_EXPOSED_MATCHES_ADVERSE`
- 全现金 Portfolio 使用 `CASH_BASELINE`

部分场景只报告资金上下界，不伪造精确收益；全部 Ticket 已判定时才输出精确毛返还、盈亏和资金恢复率。

V2 使用精确 Ticket 状态搜索，不再在比赛数较多时回退到贪心近似。为限制最坏状态空间，配置和 Domain 均将单个 Portfolio 限制为最多 12 张 Ticket。

## 文件桥

AnalysisPacket 只投影比赛身份、冻结上下文 hash、`P_market`、`P_quant` 和输入引用。以下内容明确排除：

- `P_final`
- EV 与候选排名
- Ticket 和 Portfolio
- budget、stake 和策略配置

Review 文件必须：

- 使用 UTF-8 严格 JSON，不允许 BOM、重复 key、NaN、Infinity 或额外字段。
- 返回绝对三项 `P_llm`，不允许 `probability_delta` 或 `P_final`。
- 覆盖 Packet 的完整比赛集合。
- 严格匹配 `analysis_run_id`、`packet_id` 和 `packet_hash`。
- 单文件不超过 1 MB，Packet 与 Review 均不超过 256 场，并限制概率的小数精度和 JSON 嵌套深度。

AnalysisPacket 和 LLMReviewArtifact 是完成 AnalysisRun 之后的追加型工件。它们允许插入但禁止更新和删除；Review 导入不会修改原运行的任何后代记录。

## 验证

- 全量 pytest：`79 passed`。
- `compileall`：通过。
- fresh Alembic upgrade：通过。
- runtime schema 与 Alembic schema 的 `147` 个触发器定义一致。
- `0.2.0` wheel 构建、安装、资源合同及安装后 CLI 分析/Packet 导出：通过。
- Packet 导出、纯文件校验、导入、幂等重复导入和 append-only 触发器：通过。
