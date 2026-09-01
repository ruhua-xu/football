# HISTORICAL_ARCHIVE_V1 / BACKTEST_V1 规范合同

## 1. 状态与规范用语

本文是 `HISTORICAL_ARCHIVE_V1` 本地历史归档和 `BACKTEST_V1` walk-forward 回测的规范性合同。实现、归档生产方、Provider、CLI、持久化、报告和测试必须同时满足本文。

本文中的“必须”“不得”表示强制要求；“应”表示除非有书面且可审计的例外，否则必须遵守。无法证明数据满足某项时间或来源要求时，必须按不满足处理，不得猜测、补值或伪造时间。

本合同不修改 `ANALYSIS_PACKET_V1/V2`、`LLM_REVIEW_V1/V2`、FusionRun 或 PortfolioRevision 的既有文件合同。`BACKTEST_V1` 只运行确定性的 `QUANT_ONLY_V1` 和 `MARKET_QUANT_BLEND_V1`，不在历史回测中调用 LLM。

## 2. HISTORICAL_ARCHIVE_V1 文件边界

### 2.1 目录与文件发现

1. 一个归档目录必须至少包含一个位于目录顶层的 `*.json` 普通文件。
2. 加载器只按文件名排序后读取目录顶层的 `*.json`；不递归读取子目录。子目录中的无效样例、JSON 或其他文件不得被视为归档输入。
3. 顶层非 JSON 文件可以用于配置或说明，但不属于归档文档，也不参与 manifest、记录数或 checksum。
4. V1 的一个 JSON 文件只允许一个 `manifest`、一个 `records` 数组和一个 `dataset_kind`。不同数据种类必须使用不同的顶层 JSON 文件；V1 不支持在一个巨型 JSON 内设置多个业务 logical section。
5. 同一种 `dataset_kind` 可以由多个文件组成，但每个文件仍是独立、不可变、可校验的归档。
6. CLI 的 validate/import 操作不得接受混合 `data_mode` 的目录。低层存储即使显式选择其中一种模式，也不得在一次 Provider、BacktestRun、指标或比较中同时使用另一模式的记录。

### 2.2 字节编码与严格 JSON

1. 归档文件必须是严格 UTF-8。需求原文采用 GB18030 编码不改变归档 wire format；不得把 GB18030、系统 ANSI 或容错替换后的文本当作有效归档。
2. UTF-8 解码、JSON 解析或 typed model 校验任一步失败，整个文件必须失败。
3. JSON 对象不得包含重复键；不得包含 `NaN`、`Infinity` 或 `-Infinity`；所有数值必须有限。
4. 顶层对象、manifest、record envelope 和 typed payload 不得包含模型未声明的额外字段。
5. 所有时间字段必须是带 UTC offset 的 timezone-aware ISO 8601 值。Naive datetime 必须失败；模型可将合法 offset 归一化为 UTC，但不得补造缺失时区。
6. `record_count`、`home_goals`、`away_goals` 和 `retrospective` 使用严格 JSON 类型，不得用字符串冒充整数或布尔值。
7. 加载器不得静默修复坏 checksum、错误 schema、非法时间、重复业务键、错误引用或错误 provider。

### 2.3 文档 envelope

每个文件的顶层结构必须且只能是：

```json
{
  "manifest": {},
  "records": []
}
```

`manifest` 必须是对象，`records` 必须是数组。数组顺序属于 payload checksum 的一部分，不得在校验前排序。`record_count` 必须严格等于数组长度，包括零记录归档。

### 2.4 Manifest 的 11 个字段

`HistoricalArchiveManifest` 必须且只能包含下列 11 个字段：

| 字段 | V1 约束 |
|---|---|
| `archive_schema_version` | 必须精确等于 `HISTORICAL_ARCHIVE_V1` |
| `archive_id` | 非空、最长 160 字符；目录内唯一 |
| `provider_code` | 非空、最长 160 字符；标识该文件全部记录的来源 |
| `dataset_kind` | 必须是六种枚举之一 |
| `created_at_utc` | timezone-aware；表示归档实际创建时间，不是历史来源时间 |
| `source_reference` | 1 至 2048 字符；可审计的来源引用 |
| `source_description` | 1 至 4000 字符；来源和字段语义说明 |
| `license_note` | 1 至 2048 字符；许可或限制说明 |
| `data_mode` | `LIVE_STRICT` 或 `SOURCE_TIME_RESEARCH` |
| `payload_sha256` | 64 位小写十六进制，按第 4 节计算 |
| `record_count` | JSON 整数且 `>= 0`，必须等于 `records` 长度 |

同一目录中的 `archive_id` 必须唯一。validate/import 服务还要求每个 manifest 的 `payload_sha256` 唯一，并以 `(provider_code, dataset_kind, payload_sha256)` 作为持久化 provenance 身份；相同身份不得换用另一个 `archive_id`。

### 2.5 Record envelope

每个 `records[]` 元素必须且只能包含：

```json
{
  "retrospective": false,
  "imported_at_utc": null,
  "payload": {}
}
```

`payload` 的类型由 manifest 的 `dataset_kind` 唯一决定。所有 record 必须使用同一种 typed payload，不能在一个文件中混入另一种 payload。

`retrospective` 和 `imported_at_utc` 的组合由 `data_mode` 决定：

| `data_mode` | `retrospective` | `imported_at_utc` |
|---|---:|---|
| `LIVE_STRICT` | 必须为 `false` | 必须为 `null` |
| `SOURCE_TIME_RESEARCH` | 必须为 `true` | 必须是实际回溯导入时间 |

### 2.6 六种 dataset kind 与 typed payload

V1 只支持以下六种值：

```text
FIXTURES
MARKET_ODDS
SPORTTERY_BONUS
MANUAL_QUANT
MATCH_RESULTS
PROVIDER_MAPPINGS
```

对应 payload 合同如下：

| `dataset_kind` | Typed payload | 必需内容与核心约束 |
|---|---|---|
| `FIXTURES` | `FixtureArchivePayload` | `competition`、`home_team`、`away_team`、`match`；Match 的 competition/home/away ID 必须精确引用同一 payload 内对象，主客队不得相同 |
| `MARKET_ODDS` | `MarketOddsSnapshot` | `snapshot_id`、`match_id`、`provider_code`、`bookmaker_code`、`market`、三项 `quotes`、三个时间、`source_snapshot_key`、`payload_hash`；H/D/A 必须各一项且赔率 `> 1` |
| `SPORTTERY_BONUS` | `SportteryBonusSnapshot` | `snapshot_id`、`match_id`、`provider_code`、`sporttery_match_no`、`market`、三项 `quotes`、`sale_status`、三个时间、`source_snapshot_key`、`payload_hash`；H/D/A 必须各一项且固定奖金 `> 1` |
| `MANUAL_QUANT` | `ManualQuantInput` | `input_id`、`match_id`、`market`、H/D/A `probabilities`、`available_at_utc`、`payload_hash`；概率各在 `[0,1]`，总和误差不得超过 `0.000001` |
| `MATCH_RESULTS` | `MatchResult` | `match_result_id`、`match_id`、`provider_code`、严格非负整数比分、三个时间、`source_result_key`、`payload_hash`、可空 `supersedes_match_result_id` |
| `PROVIDER_MAPPINGS` | `ProviderMatchMapping` | `mapping_id`、`provider_code`、`external_namespace`、`external_match_id`、`internal_match_id`、`resolution_method`、`confidence`、`available_at_utc`；confidence 在 `[0,1]` |

payload 中存在 `provider_code` 时必须与 manifest 的 `provider_code` 完全一致。`FIXTURES` 和 `MANUAL_QUANT` 没有 payload provider 字段，其来源直接继承 manifest。

## 3. 时间模式与来源真实性

### 3.1 共同时间不变量

市场赔率和竞彩固定奖金必须满足：

```text
captured_at_utc <= available_at_utc <= ingested_at_utc
```

赛果必须满足：

```text
observed_at_utc <= available_at_utc <= ingested_at_utc
```

用于 manifest 创建关系的 `source_known_at` 定义为：

| payload | `source_known_at` |
|---|---|
| Fixture | `match.available_at_utc` |
| MarketOddsSnapshot | `ingested_at_utc` |
| SportteryBonusSnapshot | `ingested_at_utc` |
| ManualQuantInput | `available_at_utc` |
| MatchResult | `ingested_at_utc` |
| ProviderMatchMapping | `available_at_utc` |

### 3.2 LIVE_STRICT

对所有真实归档，`LIVE_STRICT` 只表示系统当时真实采集并保存的数据，必须满足：

```text
source_known_at <= manifest.created_at_utc
retrospective = false
imported_at_utc = null
```

快照在 cutoff 可见必须同时满足：

```text
captured_at_utc <= cutoff
available_at_utc <= cutoff
ingested_at_utc <= cutoff
```

赛果在 evaluation cutoff 可见必须同时满足：

```text
observed_at_utc <= cutoff
available_at_utc <= cutoff
ingested_at_utc <= cutoff
```

Fixture、ManualQuant 和 ProviderMapping 的 `available_at_utc` 必须不晚于相应 cutoff。

### 3.3 SOURCE_TIME_RESEARCH

`SOURCE_TIME_RESEARCH` 只用于后来取得、但具有可信来源发布时间的历史数据。每条记录必须满足：

```text
retrospective = true
source_known_at < imported_at_utc <= manifest.created_at_utc
```

市场快照、竞彩快照和赛果还必须满足：

```text
ingested_at_utc = available_at_utc
```

这里的 `ingested_at_utc` 是显式的 source-time ingestion boundary，不是本系统实际导入时间；实际时间只能写入 record 的 `imported_at_utc`，不得回填为历史日期。

研究模式按可信来源时间选择数据：快照要求 `captured_at_utc` 和 `available_at_utc` 不晚于 cutoff；赛果要求 `observed_at_utc` 和 `available_at_utc` 不晚于 cutoff；其他记录要求 `available_at_utc` 不晚于 cutoff。由于 V1 强制 `ingested_at_utc = available_at_utc`，后续通用 Batch 和冻结输入校验仍能验证 ingestion cutoff。

所有研究模式报告必须醒目且精确输出以下标签，不得使用缩写、别名或仅输出 `SOURCE_TIME_RESEARCH`：

```text
RETROSPECTIVE_SOURCE_TIME_RESEARCH
```

`LIVE_STRICT` 报告标签精确为 `LIVE_STRICT`。两种模式的指标不得合并、静默比较或共同排名。无法可靠确定历史 `available_at_utc` 时，该数据不满足严格 point-in-time，必须拒绝或在本合同外另行设计，不能伪造 timestamp。

### 3.4 非生产 synthetic acceptance 例外

`data/fixtures/historical_acceptance/` 是固定、纯合成、非生产的测试工件，不是历史来源归档。原始验收配置明确要求 `config/backtest.toml` 使用 `data_mode=LIVE_STRICT`，因此该 corpus 不得通过增加第三种 `HistoricalDataMode` 来消除语义差异；面向真实归档和生产运行的模式仍必须恰好是 `LIVE_STRICT` 与 `SOURCE_TIME_RESEARCH`。

在这个且仅这个明确分类的 corpus 中，`LIVE_STRICT` 表示正在验收 3.2 的 captured/observed、available、ingested 与 cutoff 校验规则，不是系统在 synthetic 时间戳对应历史时点实际采集、持有或运行过数据的事实声明。该例外不放宽 3.2 对任何真实归档的要求。

验收配置及其报告合同必须显式传播以下字段和值：

```text
classification = SYNTHETIC_ACCEPTANCE_DATA
performance_warning = NOT REAL HISTORICAL PERFORMANCE
```

报告必须把两个值分别渲染为醒目 banner。它们的解释优先级高于 `LIVE_STRICT` 标签、合成时间戳、指标、manifest 来源文本或其他任何 performance/provenance 暗示，并明确否定真实历史采集、真实历史表现与收益声明。字段缺失、值被改写或 banner 未显示时，synthetic acceptance 报告不得视为合格。

Synthetic 与 non-synthetic 归档即使具有相同 `data_mode`，也不得规范化为相同 provenance，不得合并指标，且不得作为策略比较的两侧。Synthetic 之间的比较仍必须使用完全相同的 synthetic classification、warning 和 archive provenance。

## 4. Checksum 与 payload 完整性

### 4.1 Records-array SHA-256

manifest 的 `payload_sha256` 只覆盖完整 `records` 数组，不覆盖 manifest、文件缩进、换行或 JSON 对象原始键顺序。算法必须与以下步骤等价：

```python
normalized_records = [
    HistoricalArchiveRecord.model_validate(record).model_dump(mode="json")
    for record in records
]
canonical = json.dumps(
    normalized_records,
    ensure_ascii=False,
    allow_nan=False,
    separators=(",", ":"),
    sort_keys=True,
)
payload_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

对象键递归排序，输出不带多余空白，非 ASCII 字符不转义，摘要使用 canonical JSON 的 UTF-8 字节和小写 hex。数组顺序保持原样。任何 record envelope 或 payload 值变化都必须导致 checksum 变化；checksum 不匹配时不得继续 typed payload 加载或导入。

### 4.2 记录内部 hash

归档 checksum 之外，下列 payload 还必须通过自身 hash：

1. `MarketOddsSnapshot.payload_hash` 必须等于 canonical 三项赔率对象的 SHA-256。
2. `SportteryBonusSnapshot.payload_hash` 必须等于 canonical 三项固定奖金对象的 SHA-256。
3. `ManualQuantInput.payload_hash` 必须等于 canonical 三项概率对象的 SHA-256。
4. `MatchResult.payload_hash` 必须等于下列对象的 canonical SHA-256：

```json
{"away_goals": 1, "home_goals": 2}
```

键排序、紧凑 JSON、UTF-8 和 SHA-256 规则与 4.1 相同。记录 ID、来源键和时间不属于 MatchResult score hash，但属于外层 records-array hash。

## 5. 业务键、映射与版本链

### 5.1 不可重复业务键

加载单文件和合并后的同模式目录时必须校验以下唯一性：

| Kind | 唯一键 |
|---|---|
| `FIXTURES` | `(provider_code, match_id, match.available_at_utc)` |
| `MARKET_ODDS` | `snapshot_id` |
| `MARKET_ODDS` | `(provider_code, source_snapshot_key)` |
| `MARKET_ODDS` | `(provider_code, match_id, bookmaker_code, market.canonical, captured_at_utc, available_at_utc)` |
| `SPORTTERY_BONUS` | `snapshot_id` |
| `SPORTTERY_BONUS` | `(provider_code, source_snapshot_key)` |
| `SPORTTERY_BONUS` | `(provider_code, match_id, sporttery_match_no, market.canonical, captured_at_utc, available_at_utc)` |
| `MANUAL_QUANT` | `input_id` |
| `MANUAL_QUANT` | `(provider_code, match_id, market.canonical, available_at_utc)` |
| `MATCH_RESULTS` | `match_result_id` |
| `MATCH_RESULTS` | `(provider_code, source_result_key)` |
| `MATCH_RESULTS` | `(provider_code, match_id, available_at_utc, ingested_at_utc)` |
| `PROVIDER_MAPPINGS` | `mapping_id` |
| `PROVIDER_MAPPINGS` | `(provider_code, external_namespace, external_match_id)` |

同一 `competition_id` 和 `team_id` 的完整定义必须一致。同一 `match_id` 的 `(competition_id, home_team_id, away_team_id)` 必须稳定，Fixture 版本不得改变 canonical 比赛身份。

### 5.2 ProviderMapping 是强制血缘

1. 每个非 `PROVIDER_MAPPINGS` 记录必须存在相同 `provider_code`、相同内部 `match_id` 的 mapping。
2. `SPORTTERY_BONUS.sporttery_match_no` 还必须精确匹配 mapping 的 `external_match_id`。
3. mapping 只有在 `available_at_utc <= cutoff` 时可见。不可见、缺失、跨 provider 或冲突 mapping 不得由自动模糊匹配补齐。
4. `MatchResultBatch` 中每个 result 和每个 issue 都必须有 mapping；result/issue 不得引用请求外比赛。
5. 持久化 MatchResult 时 mapping 必须先存在，且其 `available_at_utc` 不得晚于 result 的 `available_at_utc` 或 `ingested_at_utc`。

### 5.3 cutoff 前的确定性版本选择

Provider 必须先按模式过滤 cutoff，再按以下 stream 选择一个最新版本：

| Kind | stream | 最新版本排序 |
|---|---|---|
| Fixture | `match_id` | 最大 `available_at_utc` |
| Market odds | `(match_id, bookmaker_code, market.canonical)` | 最大 `(available_at_utc, captured_at_utc, ingested_at_utc, snapshot_id)` |
| Sporttery bonus | `(match_id, sporttery_match_no, market.canonical)` | 最大 `(available_at_utc, captured_at_utc, ingested_at_utc, snapshot_id)` |
| Manual quant | `(match_id, market.canonical)` | 最大 `available_at_utc` |
| Match result | `match_id` | 最大 `(available_at_utc, ingested_at_utc, match_result_id)` |

返回顺序必须确定：Fixture/Result 按 `match_id`，快照按其 stream key，mapping 按 `mapping_id`。缺失合法版本时返回空或部分 Batch；不得向 cutoff 后搜索一个“最近可用”值。

### 5.4 MatchResult 更正

每个 `(provider_code, match_id)` 赛果流必须只有一个 root，并通过 `supersedes_match_result_id` 形成不分叉、不成环的线性追加链：

```text
MatchResult v2.supersedes_match_result_id -> MatchResult v1
```

子版本必须与父版本属于同一 match 和 provider，并满足：

```text
parent.available_at_utc <= child.available_at_utc
parent.ingested_at_utc < child.ingested_at_utc
```

不得更新或删除旧赛果。查询必须在指定 cutoff 下返回最新可见、尚未被 cutoff 内后继替代的版本；更晚才可见的更正不得改变更早 cutoff 的结果。

## 6. Validate 与 MANIFEST_PROVENANCE_ONLY import

1. validate/import 必须先完整加载目录、校验所有文件、跨文件业务键、版本链和 mapping coverage，任一失败不得产生数据库写入。
2. import 的规范 scope 精确为 `MANIFEST_PROVENANCE_ONLY`。
3. import 只向 `historical_archive_imports` 注册 11 个 manifest 字段和本次数据库注册的实际 `imported_at_utc`，不得在该步骤填充 Match、ProviderMapping、Odds、Bonus、ManualQuant 或 MatchResult 业务表。
4. 规范 materialization policy 精确为：

```text
READ_ONLY_FILES; CUTOFF_SELECTED_DECISION_INPUTS_ONLY; MATCH_RESULTS_AT_EVALUATION
```

5. 本地归档文件保持只读；只有 cutoff 选中的决策输入可由 AnalysisRun 持久化，MatchResult 只能在 evaluation 阶段从 Batch 单独物化。
6. 数据库注册 `imported_at_utc` 必须是 timezone-aware 的真实注册时间，且不得早于任一 `manifest.created_at_utc`。CLI 必须使用当前 `utc_now()`，不得用历史 source time、decision cutoff 或 evaluation cutoff 回填。
7. 完全相同的 `archive_id` 和 manifest 可幂等重放；第一次注册时间保持不变。相同 ID 不同 manifest，或相同 `(provider_code, dataset_kind, payload_sha256)` 不同 ID，必须冲突失败。
8. 多 manifest 注册必须优先使用单事务 bulk append；冲突预检必须在写入前完成，不能留下部分 provenance。

## 7. BACKTEST_V1 配置与运行身份

V1 固定版本边界为：

```text
backtest.version = BACKTEST_V1
backtest.slates.policy = DAILY_FIXED_CUTOFF_V1
settlement.policy = THREE_WAY_2X1_BACKTEST_V1
metrics.version = BACKTEST_METRICS_V1
log_loss_clip_version = EPSILON_CLIP_V1
```

`log_loss_epsilon` 从配置读取，默认验收值为 `0.000001`。V1 只允许 `QUANT_ONLY_V1` 和 `MARKET_QUANT_BLEND_V1`；`LLM_REVIEW_DELTA_V1` 必须拒绝。

一个 BacktestRun 必须冻结：

1. `backtest_run_id`、`backtest_version`、单一 `data_mode`、`date_from`、`date_to` 和 `status`。
2. canonical `strategy_config_json`、其 SHA-256、`strategy_version`、budget、quant weight、EV/ROI 门槛、PortfolioConstraints 和 slate policy。
3. `code_revision`。
4. 排序后的 archive provenance；每项包含 `archive_id`、schema version、provider、dataset kind 和 payload SHA-256。
5. 按请求计划确定生成的 `expected_slice_ids`。
6. 实际 `created_at_utc`。

CLI 回测所选归档必须使用同一 provider、同一 data mode，并完整覆盖六种 `HistoricalArchiveDatasetKind`。运行前必须注册完全相同的 manifest provenance；持久化和报告时必须再次核对注册记录。

## 8. 三类时间不得混淆

### 8.1 决策 cutoff

`decision_as_of_at_utc` 是历史知识边界，不是程序执行时间。进入 immutable AnalysisRun 的 Fixture、mapping、odds、bonus 和 manual quant 必须在该 cutoff 可见；MatchResult、MatchSettlementIssue 和 Settlement 不得进入 AnalysisRun input manifest。

### 8.2 评估 cutoff

`evaluation_as_of_at_utc` 是赛果和模拟结算的知识边界，必须晚于该 slate 的完整 kickoff window。它不得改写已冻结的 Prediction、EV、Ticket、Portfolio 或 Risk。

### 8.3 实际执行、导入与计算时间

1. `execution_time_utc` 是回测任务真实执行时间；未显式提供时必须取 `utc_now()`。
2. `execution_time_utc` 不得早于最后一个 slate 的 `evaluation_as_of_at_utc`。
3. 同一回测的 `BacktestRun.created_at_utc` 以及每个 AnalysisRun 的 `started_at_utc`、`completed_at_utc` 必须使用该冻结执行时间。报告必须同时显示历史 cutoffs 和实际 execution timestamp。
4. 持久化 BacktestSlice 的 `created_at_utc` 和指标的 `calculated_at_utc` 使用实际计算时间，不得伪装为历史 decision/evaluation 时间。
5. walk-forward 生成的 `Settlement.settled_at_utc = evaluation_as_of_at_utc`，表示模拟结算的有效 as-of 边界，不表示数据库写入或任务实际发生在历史时刻。
6. Archive `created_at_utc`、record `imported_at_utc`、数据库注册 `imported_at_utc` 和 Backtest execution/calculation 时间都不得回填为来源历史时间。

## 9. Chronological slate 与 walk-forward 顺序

每个 `BacktestSlatePlan` 必须满足：

```text
kickoff_from_utc <= kickoff_to_utc
decision_as_of_at_utc <= kickoff_from_utc
evaluation_as_of_at_utc > kickoff_to_utc
```

一个 slate 内的 `match_ids` 必须唯一。相邻 slate 必须满足：

```text
current.decision_as_of_at_utc > previous.evaluation_as_of_at_utc
```

因此 slates 必须严格按时间递增且不重叠。V1 不支持隐式 per-match `T-24h` cutoff；一个 slate 共享一个 decision cutoff 和一个 evaluation cutoff。

服务必须按输入顺序逐 slate 执行以下不可交换流程：

```text
select decision inputs <= decision cutoff
-> create and persist immutable AnalysisRun
-> freeze P_market / P_quant / P_final / EV / Ticket / Portfolio / Risk
-> query MatchResultBatch <= evaluation cutoff
-> settle complete Tickets
-> create BacktestSlice and immutable metric snapshots
```

严格禁止先读取结果再创建 AnalysisRun。若预期比赛缺少任一完整 MVP 决策输入，V1 可以在 partial-input 模式中排除该比赛，但必须按原计划顺序写入 `missing_decision_match_ids`，并仍以计划 `match_ids` 作为 coverage 分母。不得用未来输入补齐。

`BacktestSlice` 必须冻结 `slice_id`、run/data mode、两个 cutoff、`analysis_run_id`、`decision_input_manifest_hash`、`match_result_ids`、`match_result_issues`、`missing_decision_match_ids`、match/ticket counts 和 coverage。派生对象必须能从冻结 AnalysisRun、结果 Batch 和结算结果重新计算并完全相等。

## 10. MatchResult、issue 与 fail-closed 语义

### 10.1 本地归档 V1 的表达能力

`MATCH_RESULTS` 的本地 payload 只能表达常规时间加伤停补时后的非负整数比分，并由比分映射为 `HOME_WIN`、`DRAW` 或 `AWAY_WIN`。它没有 outcome status、void flag、退款金额或竞彩特殊规则字段，因此不能表达取消、腰斩、`VOID`、退款、加时、点球或串关降级。

Fixture 的 `status` 即使为 `POSTPONED` 或 `CANCELLED`，也不能替代 MatchResult 或授权结算。遇到上述情况不得伪造 `0-0`、pending/final score、胜负结果或退款 Settlement。

### 10.2 未来 Provider 的 MatchSettlementIssue

Provider port 允许未来来源在 `MatchResultBatch.issues` 返回 `MatchSettlementIssue(match_id, reason, detail)`。Provider 必须保证 issue 在 Query cutoff 已可知；每个 issue 必须有可见 mapping，同一比赛在一个 Batch 中最多一个 issue，且不得同时存在 result 和 issue。

可用 reason 包括 `CANCELLATION`、`ABANDONMENT`、`VOID`、`REFUND`、`EXTRA_TIME`、`PENALTY`、`PENALTY_SHOOTOUT`、`DEGRADE`、`PARLAY_DEGRADE`、`UNSUPPORTED_MARKET`、`UNSUPPORTED_PASS_TYPE` 和 `UNSUPPORTED_SETTLEMENT_KIND`。

本地 `HISTORICAL_ARCHIVE_V1` 当前没有 issue record kind，因此 `LocalArchiveHistoricalDataProvider` 不能从 score archive 合成 issue。未来 Provider 可以通过 port 返回真实 issue，但不得借此合成 MatchResult。

### 10.3 Durable issue 与不造假

1. Backtest 必须将 Batch issue 原样写入 `BacktestSlice.match_result_issues`，并通过 `BACKTEST_SLICE_RECORD_V2` canonical manifest/hash 持久保存。
2. issue 不计入 `settled_match_count`，不产生 BacktestMatchSnapshot，也不产生概率评分样本。
3. 受 issue 影响的 Ticket 必须返回 `UNSUPPORTED_SETTLEMENT_CASE`，不得创建 Ticket Settlement。
4. 缺少 result 且没有 issue 的 Ticket 必须返回 `MISSING_RESULT`，不得创建 Ticket Settlement。
5. 只要 Portfolio 存在 unsupported 或 missing Ticket，就不得创建虚假的 PortfolioSettlement。其他结果完整且不受影响的 Ticket 可以独立产生真实 Settlement，但不能据此伪造完整 Portfolio aggregation。
6. 报告必须分别显示 partial decision coverage、partial MatchResult coverage 和 `UNSUPPORTED_SETTLEMENT_CASE` issue；不得从指标分母中静默删除问题比赛。

## 11. Settlement V1

`THREE_WAY_2X1_BACKTEST_V1` 只支持：

```text
settlement_kind = BACKTEST
market = THREE_WAY
pass_type = TWO_FOLD_ONE (2X1)
```

其他 settlement kind、market 或 pass type 必须返回显式 unsupported reason，不得猜测规则。Settlement 必须引用同一 decision scope 下的冻结 Portfolio 和冻结 Ticket；跨 AnalysisRun、PortfolioRevision、Portfolio、Ticket 或错误 match 的输入必须失败。

每张完整 2X1 Ticket 恰好使用两个不同 MatchResult，并按冻结 leg 顺序判断：

```text
两腿全部命中 -> WON, gross_payout_fen = frozen potential_gross_payout_fen
任意一腿未命中 -> LOST, gross_payout_fen = 0
profit_loss_fen = gross_payout_fen - frozen stake_fen
```

结算必须使用冻结 `stake_fen`、`potential_gross_payout_fen` 和 `payout_policy_version`，不得重新读取当前赔率、当前奖金或当前配置。

Portfolio 只有在所有 Ticket 均已结算时才聚合：

```text
budget_fen = deployed_stake_fen + original_cash_fen
ending_capital_fen = original_cash_fen + gross_ticket_payout_fen
profit_loss_fen = ending_capital_fen - budget_fen
roi_on_budget = profit_loss_fen / budget_fen
roi_on_deployed = profit_loss_fen / deployed_stake_fen
```

分母为零时对应 ROI 必须为 `null/N/A`。`NO_BET` 是合法的全 Cash Portfolio：无 Ticket、stake 为零、ending capital 等于原 Cash。

赛果或 Settlement 更正必须追加新记录，并用 `supersedes_*` 指向同 scope、同业务对象的前一版本；旧记录不得 UPDATE/DELETE。更正赛果必须确实 supersede 原结算引用的赛果，结算更正时间不得早于被更正记录。

## 12. Metrics V1

概率指标只使用有真实 MatchResult 的 `BacktestMatchSnapshot`。对 `P_market`、`P_quant`、`P_final` 分别计算：

1. Multiclass Brier Score 和 H/D/A 三个分项；总分等于三个分项之和。
2. Multiclass Log Loss；真实 outcome 概率先按版本化 epsilon 裁剪到 `[epsilon, 1-epsilon]`。
3. 十个 calibration bins：`[0.0,0.1)` 至 `[0.9,1.0]`，最后一档包含 1；每场贡献三个 outcome 概率。
4. 每个 bin 输出 count、mean predicted probability、observed frequency 和 absolute gap；ECE 按全部 `3 * settled_match_count` 个概率观察加权。

聚合指标至少必须包含：

```text
slate_count / settled_slate_count / slate_coverage
match_count / settled_match_count / match_coverage
ticket_count / settled_ticket_count / ticket_coverage
total_budget / total_stake / total_settled_stake / total_cash
gross_payout / profit_loss
ROI_on_budget / ROI_on_deployed
winning_ticket_count / ticket_hit_rate
NO_BET_count / NO_BET_ratio
max_drawdown / max_consecutive_losing_slates
average_ticket_odds / average_ticket_probability / average_selection_EV
max_match_exposure / max_selection_exposure
realized_loss_when_top_exposure_failed
realized_loss_when_top_two_exposure_failed
```

Coverage 必须使用计划比赛和全部冻结 Ticket 作为分母。概率样本数必须等于 settled match 数。总盈亏只聚合已结算 stake 的实现结果；`ROI_on_deployed` 的 V1 分母是全部冻结 deployed stake。最大回撤和连续亏损 slate 必须按 `(decision_as_of_at_utc, slice_id)` 的时间顺序计算。

所有比率和指标按 V1 Decimal 规则确定性量化；缺少分母时输出 `N/A`，不得输出误导性的零。

## 13. Report 与公平比较

### 13.1 单次报告

报告必须包含 data mode label、run/version/strategy/config hash/code revision、archive provenance、历史日期窗口、实际 execution timestamp、概率指标、资金/风险指标、每个 slice 的两个 cutoff、决策输入 manifest hash、result IDs、issues 和 coverage。Synthetic acceptance 报告还必须包含并显示 3.4 定义的 `classification` 与 `performance_warning` 合同字段。

研究模式必须使用精确标签 `RETROSPECTIVE_SOURCE_TIME_RESEARCH`。Synthetic archive 必须同时醒目标记：

```text
SYNTHETIC ACCEPTANCE DATA
NOT REAL HISTORICAL PERFORMANCE
```

两条 banner 覆盖并否定 data mode、时间戳或指标可能造成的任何真实 performance/provenance 解读，不得只把它们隐藏在 manifest 文本中。

### 13.2 比较前置条件

V1 compare 必须恰好比较 `QUANT_ONLY_V1` 与 `MARKET_QUANT_BLEND_V1`，并拒绝不公平输入。两侧必须具有相同的：

1. `BACKTEST_V1`、data mode、date window 和 code revision。
2. archive provenance，以及 synthetic/non-synthetic classification；synthetic 时还必须具有相同的 `classification` 和 `performance_warning`。
3. slate 数量、顺序、decision/evaluation cutoffs、kickoff windows 和 expected match IDs。
4. budget、selection EV threshold、ticket ROI threshold、PortfolioConstraints 和 metrics config。
5. 除 fusion policy 及其 policy-specific `quant_weight` 外的 strategy config。
6. 每个 slice 的决策输入 manifest hash、实际决策比赛结构、missing decision IDs。
7. evaluation MatchResult IDs、MatchSettlementIssue、settled counts 和 coverage lineage。

任一项不同必须 fail closed，不能输出貌似可比的表格。

Synthetic 与 non-synthetic provenance 绝不能作为相同 provenance 通过比较，即使两侧 `data_mode` 相同或其他运行参数一致。

比较只并排报告 P_final Brier、P_final LogLoss、两个 ROI、Drawdown、NO_BET 和 Ticket Hit Rate，并附两侧完整报告。系统不得输出 `winner`、`best`、`better`、`outperform`、`ranked`、`ranking` 或任何“最优策略”与排序结论；不得自动调参、选择最高 ROI 参数或用全历史区间反向选择 threshold。

## 14. SQLite-only 持久化

1. 本合同范围内唯一支持的数据库 backend 是 SQLite。非 `sqlite` URL 必须立即失败，不得降级、模拟兼容或绕过校验。
2. SQLite 连接必须启用 `PRAGMA foreign_keys=ON` 和 `PRAGMA recursive_triggers=ON`。
3. `historical_archive_imports`、`match_results`、Ticket/Portfolio settlements、`backtest_runs`、`backtest_slices`、`backtest_metric_snapshots` 及其 lineage 关联表必须 append-only。UPDATE、DELETE、冲突 insert 和 `INSERT OR REPLACE` 语义不得改变历史事实。
4. MatchResult、Settlement、BacktestRun、BacktestSlice 和 metrics 必须保存 canonical payload/hash及可验证 lineage；读取时必须重新验证 hash、冗余列和外键关系。
5. 保存完整 walk-forward graph 前必须重新计算并验证派生 artifacts 和 metrics。MatchResult Batch、Ticket Settlement、Portfolio Settlement、BacktestRun、Slice 和 metric lineage 的写入必须在一个事务中完成；失败不得留下部分 backtest graph。
6. 完全相同的重放可以幂等返回既有记录；同一业务身份但内容不同必须冲突失败。
7. BacktestRun 只能引用已注册且完全匹配的 archive provenance；BacktestSlice 只能引用其 run manifest 中预期的 slice 和对应完成的 AnalysisRun；metric snapshot 必须引用同 run 的 slice/result/settlement lineage。

## 15. Fail-closed 总则

以下任一情况必须拒绝文件、请求、结算、持久化或比较，不得生成替代事实：

```text
错误 UTF-8 / 非严格 JSON / 重复键 / NaN / Infinity
未知字段 / 错误 schema / checksum 或内部 hash 不匹配
naive datetime / 回填 import 或 execution timestamp
重复业务键 / provider 或 mapping 冲突
cutoff 后输入、结果、更正或 mapping
混合 LIVE_STRICT 与 SOURCE_TIME_RESEARCH
把 synthetic 与 non-synthetic provenance 视为相同或进行比较
错误 match、scope、Ticket、Portfolio 或 supersession lineage
无法表达的取消、腰斩、VOID、退款、加时、点球或降级规则
不完整或不公平的策略比较
非 SQLite 持久化
```

“没有可用事实”必须表示为空结果、显式 missing coverage 或 `MatchSettlementIssue`，绝不能表示为编造的 MatchResult、Settlement、收益、指标样本或排名。
