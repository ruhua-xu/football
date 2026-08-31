# 0.4.0 Historical Data、Settlement 与时间序列回测设计

## 1. 本阶段边界

本文是 `0.4.0` 的第一阶段设计冻结，只提交：

- 历史数据源选择与验收方案。
- `HistoricalDataProvider` 赛果接口。
- `MatchResult` 与回测用 Ticket 级 `Settlement` 模型。
- 严格时间序列回测的时间语义、结算规则和后续实现顺序。

本阶段不实现真实数据 adapter、下载任务、数据库 migration、Settlement service、BacktestRun、CLI、指标引擎或调度器，也不把项目版本号从 `0.3.0` 提升为已发布的 `0.4.0`。

以下边界继续冻结：

- `ANALYSIS_PACKET_V1/V2` 与 `LLM_REVIEW_V1/V2` 文件合同。
- Review validate/import、FusionRun 与 PortfolioRevision 算法。
- `P_market`、`P_quant`、原 `P_final` 和本地 clipping 规则。
- Selection EV、简单2串1计奖、Optimizer、Exposure 与 Stress Test。
- 不增加任何新 LLM 功能，不增加真实 LLM API。
- 不实现3串4、4串11或其他新过关类型。

## 2. 数据职责拆分

历史回测不能使用一个“万能 Provider”同时返回决策输入和赛果。职责保持为小接口：

| 数据 | Port | 使用阶段 |
|---|---|---|
| 赛程和比赛身份 | `FixtureProvider` | 决策 |
| 国际赔率快照 | `MarketOddsProvider` | 决策 |
| 竞彩固定奖金 | `SportteryProvider` | 决策与冻结返还依据 |
| 人工量化输入 | `ManualQuantProvider` | 决策 |
| 最终赛果 | `HistoricalDataProvider` | 评估/结算 |

`HistoricalDataProvider` 当前只提供最终赛果。历史赛程、赔率和量化输入继续通过现有 port 的 `as_of_at_utc` 语义读取，避免赛果进入 AnalysisRun 的 input manifest。

接口合同：

```text
MatchResultQuery(match_ids, as_of_at_utc)
  -> HistoricalDataProvider.fetch_match_results(...)
  -> MatchResultBatch(as_of_at_utc, results, mappings)
```

约束：

- Query 至少包含一个内部比赛 ID，且不能重复。
- Batch 允许部分覆盖或空结果，因为评估时点可能早于完赛。
- 同一个 Batch 对每场最多选择一个赛果版本。
- 每个结果必须有同 provider 的 `ProviderMatchMapping`。
- `available_at_utc` 和 `ingested_at_utc` 都不能晚于 Batch cutoff。
- Provider 必须确定性选择 cutoff 前最新的有效赛果版本。
- Provider 不返回 AnalysisRun、Prediction、Ticket、Portfolio 或 Settlement。

## 3. 历史数据源方案

### 3.1 采用顺序

建议按以下顺序推进，而不是直接绑定某一个外部 API：

1. 先定义 provider-neutral 的原始归档格式和 checksum。
2. 使用经过许可的本地 CSV/JSON 小样本完成 adapter contract 验收。
3. 分别验收“赛果”“国际赔率”“竞彩固定奖金”的覆盖率和时间字段。
4. 数据许可、稳定性、修订机制和 point-in-time 语义全部通过后，再选择生产数据源。
5. 每个真实来源使用独立 adapter，不在 Domain 或 Application 中出现供应商 SDK 类型。

### 3.2 候选来源

| 来源 | 候选用途 | 优点 | 进入实现前必须解决的问题 |
|---|---|---|---|
| football-data.org v4 | 赛程、比赛结果 | 标准 REST API，比赛/赛事资源清晰 | 套餐覆盖、历史赛季深度、调用限制、结果修订时间与商用许可 |
| football-data.co.uk CSV | 研究用赛果和国际赔率基线 | 官方页面提供多年结果及赔率 CSV，易于离线归档 | CSV 发布时间通常不足以证明精确可用时点；来源声明不保证无误；只能在时间语义合格后用于严格回测 |
| Sportmonks Football API | 付费赛果、赛程和赔率候选 | 有 Core/Odds 文档及结构化 endpoint | 购买范围、联赛覆盖、历史赔率快照时间、修订策略和再分发许可 |
| 中国体育彩票官方/授权归档 | 竞彩场次、固定奖金、赛果核对 | 与本系统实际计价语义最匹配 | 尚未确认稳定授权 API、历史奖金完整度及使用条款；默认不采用网页抓取 |
| 用户提供的官方导出文件 | 第一批可控验收数据 | 可离线审计、可固定 checksum、无运行时网络依赖 | 必须保留来源证明、导出时间、字段说明和使用授权 |

推荐的首个可执行数据集不是“抓取全量互联网数据”，而是一组经过人工验收的只读归档：

- 一份最终赛果归档。
- 一份带明确快照时间的国际赔率归档。
- 一份官方或授权的竞彩固定奖金归档。
- 一份 external match ID 到 internal match ID 的映射清单。
- 每个文件的 SHA-256、来源 URL/合同、下载或导出时间和字段版本。

### 3.3 严格模式与研究模式

首个实现只接受严格模式：

```text
captured/observed <= available <= ingested <= as_of
```

如果 2024 年的 CSV 在 2026 年才首次导入本系统，它不能被描述为“本系统在 2024 年已经知道”。这类数据未来只能进入单独命名的 `PROVIDER_TIME_RESEARCH` 模式，不能静默混入严格回放。本阶段不定义或实现该宽松模式。

### 3.4 来源验收门槛

真实 adapter 开发前必须给出：

- 使用许可和保存原始响应的权限。
- 目标联赛、赛季、比赛和竞彩场次覆盖率。
- 时区、延期、取消、腰斩和赛果更正规则。
- 赔率是开盘、采集时点还是收盘值，以及对应 timestamp。
- provider match ID 的稳定性和映射冲突处理。
- payload checksum、重复下载幂等性和更正链测试。
- 小样本人工复核结果；任何缺失不得用模型或 LLM 补造。

在这些条件通过前，不创建真实 API adapter。

## 4. MatchResult 模型

`MatchResult` 是赛后来源事实，不属于 AnalysisRun 决策图。

核心字段：

- `match_result_id`、`match_id`、`provider_code`
- `home_goals`、`away_goals`
- `observed_at_utc`、`available_at_utc`、`ingested_at_utc`
- `source_result_key`、`payload_hash`
- `supersedes_match_result_id`

当前语义严格限定为常规时间加伤停补时后的最终比分；加时赛和点球大战不进入三项胜平负判断。`three_way_selection()` 只从主客队比分确定 `HOME_WIN`、`DRAW` 或 `AWAY_WIN`。

非最终、延期、取消、腰斩和需要作废规则的比赛暂时不创建 `MatchResult`。缺少结果意味着“不可结算”，不能创建虚假的 pending/final 记录。

赛果更正不更新旧记录：

```text
MatchResult v2.supersedes_match_result_id -> MatchResult v1
```

报告在指定 evaluation cutoff 下选择最新且未被更晚版本替代的记录。

## 5. Settlement 模型

`Settlement` 是回测模拟产物，不表示真实购买或官方兑奖。模型固定 `settlement_kind=BACKTEST`。

当前粒度是一张顶层 Ticket 一条 Settlement：

- `scope_kind` 区分原 AnalysisRun 和 PortfolioRevision。
- `parent_analysis_run_id` 保留根血缘。
- `decision_scope_id` 指向 AnalysisRun ID 或 PortfolioRevision ID。
- `portfolio_id`、`ticket_id` 指向冻结决策。
- `match_result_ids` 当前必须恰好包含两个不同赛果，对应既有简单2串1。
- `stake_fen`、`gross_payout_fen`、`profit_loss_fen` 使用整数分。
- `payout_policy_version` 必须来自冻结 Ticket。
- `settlement_policy_version` 标识本次赛果判断规则。
- 更正结算通过 `supersedes_settlement_id` 追加，不覆盖旧记录。

财务不变量：

```text
profit_loss_fen = gross_payout_fen - stake_fen
WON  -> gross_payout_fen > 0
LOST -> gross_payout_fen = 0
```

结算时不能重新查询当前赔率，也不能使用当前配置重算奖金。胜票直接采用冻结 `TicketAllocation.potential_gross_payout_fen`；负票返还为零。CashPosition 不创建 Ticket Settlement，Portfolio 收益由 Ticket Settlement 和原 Cash 聚合。

当前不支持 VOID、部分退款、延期等待、取消、腰斩、加时/点球市场或串关降级。加入这些状态前必须先单独冻结竞彩规则和测试样例。

## 6. 时间序列回测

### 6.1 两个独立 cutoff

每个回测切片必须有：

- `decision_as_of_at_utc`：允许进入 AnalysisRun 的最大知识时点。
- `evaluation_as_of_at_utc`：允许进入 MatchResult/Settlement 的最大知识时点，且必须晚于 decision cutoff。

决策阶段不能加载 MatchResult。评估阶段不能改写 AnalysisRun、P_final、Ticket 或 Portfolio。

### 6.2 最小 walk-forward 流程

```text
for each chronological slate:
    1. 固定 decision_as_of_at_utc
    2. 只选择 captured/available/ingested <= decision cutoff 的输入
    3. 创建新的 immutable AnalysisRun
    4. 冻结 P_final、SelectionCandidate、Ticket、Portfolio、Risk Report
    5. 推进到 evaluation_as_of_at_utc
    6. 查询每个 Ticket leg 的最终 MatchResult
    7. 结果完整时追加 Ticket Settlement
    8. 聚合切片指标，不修改任何决策记录
```

第一版使用一个 slate 一个统一 cutoff。按每场 `T-24h` 选择不同 cutoff 会让同一2串1混入不同知识时点，必须在后续定义独立的 slice policy，不能隐式实现。

### 6.3 防止未来数据泄漏

- 结果表和 settlement 表不加入 AnalysisRun input manifest。
- 选择赔率快照时按 `captured_at`、`available_at`、`ingested_at` 和稳定 ID 确定性排序。
- 同一回测配置只能读取 cutoff 前已存在的 provider mapping。
- 赛季参数、阈值或策略选择只能使用更早切片；评估区间配置冻结。
- 不允许使用全样本收益反向选择当期 Ticket。
- 缺失赛果、奖金或映射必须计入 coverage，不能静默丢弃后只报告成功样本。

### 6.4 计划指标

后续指标引擎至少报告：

- 决策切片数、比赛覆盖率、可结算 Ticket 比例。
- 总预算、总投入、保留 Cash、毛返还、实现盈亏和 ROI。
- Ticket 命中率，但不把命中率替代收益指标。
- 最大回撤、连续亏损区间。
- Match/Selection Exposure 与 Stress 结果对应的实现损失。
- `NO_BET` 比例和数据缺失原因。
- 按 provider、联赛、赛季和固定策略版本分组的结果。

指标只能基于 append-only Settlement 聚合，不写回原 Portfolio。

## 7. 后续持久化方向

本阶段不修改 schema。下一次实现评审通过后才考虑新增：

```text
match_results                 # source fact, append-only
settlements                   # post-decision artifact, append-only
```

`match_results` 应采用 source-table 不可变触发器；`settlements` 应校验 completed decision parent、Ticket/Portfolio scope、MatchResult 覆盖和 supersession 血缘。PortfolioRevision 的 Ticket 当前保存在 canonical revision JSON 中，持久化设计必须先决定如何建立可验证引用，不能先写弱外键表。

本阶段不创建 `backtest_runs` 表。必须先用小样本证明切片、结果选择和结算规则，再冻结 BacktestRun manifest。

## 8. 推荐实现顺序

1. 冻结本文和当前接口/模型。
2. 选择一组有授权的小型历史归档并记录 checksum。
3. 实现只读文件 `HistoricalDataProvider` adapter 与 contract test。
4. 实现纯 Domain 的2串1 Settlement service，不接数据库。
5. 用固定切片做内存端到端测试并核对人工结算。
6. 再评审 migration、append-only repository 和 BacktestRun manifest。
7. 最后才增加 CLI 与批量指标报告。

任一步都不需要修改 LLM 合同或增加新的过关类型。

## 9. 当前不做

- 不开发新的 LLM prompt、Review schema、LLM API 或自动 Review。
- 不修改 0.3.0 网页 GPT 协作协议和 clipping 算法。
- 不开发机器学习训练、特征工程或自动调参。
- 不实现3串4、4串11、其他串关或 VOID 降级规则。
- 不开发 Web、定时任务、爬虫或大规模历史下载。
- 不创建真实下注、账户、出票或兑奖流程。

## 10. 参考来源

- football-data.org v4 文档：<https://www.football-data.org/documentation/quickstart>
- football-data.co.uk 历史结果与赔率说明：<https://www.football-data.co.uk/data.php>
- Sportmonks Football API 文档：<https://docs.sportmonks.com/football/>
- 中国体育彩票官网：<https://www.sporttery.cn/>

这些链接只表示候选来源，不构成选型、授权或数据准确性背书。
