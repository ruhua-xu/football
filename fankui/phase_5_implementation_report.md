# 0.5.0 First Real V3 Handshake Acceptance 实施报告

## 验收结论

`0.5.0 Real Positive Acceptance` 已在 `feature/0.5.0-real-data` 完成第一次真实 V3 handshake。真实 Fixture、国际赔率、竞彩固定奖金均达到单场完整覆盖，persisted-only preparation 为 `ANALYSIS_INPUT_READY`，网页 GPT 返回的 `LLM_REVIEW_V3` 已离线验证并 append-only 导入。由于该场 `ELO_THREE_WAY_BASELINE_V1` 没有合格的 `LIVE_STRICT` 训练事实，base prediction 明确为 `MODEL_UNAVAILABLE`；FusionRun 按合同拒绝创建，没有生成伪 `P_quant`、`P_final`、PortfolioRevision、Ticket 或投注输出。

本报告是数据链路和 fail-closed 行为验收，不是投注建议、模型表现声明或收益声明。

## 代码与运行边界

| 项目 | 验收事实 |
| --- | --- |
| 分支 | `feature/0.5.0-real-data` |
| acceptance 基线 revision | `9e9aafc35b06ee0c846ce4a8155b94c3ffcd8096` |
| 运行模式 | `LIVE_STRICT` |
| 数据库 | 本地 SQLite `data/live/football.db` |
| acceptance 时间 | `2026-09-05` |
| decision cutoff | `2026-09-05T12:59:27.814284Z` |
| 比赛 kickoff | `2026-09-05T13:30:00Z` |
| 核心架构变更 | 无 |
| `main` merge | 未执行 |

decision cutoff 晚于本次选中 fixture、market odds 和 Sporttery capture 的实际持久化时间，并早于 kickoff。Preparation 与 AnalysisRun 均只读取已持久化数据，没有临时联网补数。

## 真实来源覆盖

| 输入 | 覆盖 | 结果 |
| --- | ---: | --- |
| Sportmonks fixture | `1/1` | Bundesliga league `82`、season `28321`；正式 Sportmonks ingestion 成功，未使用 manual fixture fallback |
| The Odds API current h2h | `1/1` | `soccer_germany_bundesliga` 目标 event 已 reviewed mapping 并持久化 |
| Bookmaker snapshots | `21` | 全部作为 source snapshots 保存，并组成 `MARKET_CONSENSUS_MEDIAN_V1` lineage |
| Reviewed Sporttery | `1/1` | 真实 `SPORTTERY_MANUAL_ARCHIVE_V2`，THREE_WAY SP 和 source artifact hash 已校验 |
| Persisted preparation | `1/1` | `ANALYSIS_INPUT_READY`，bookmaker count `21`，odds age `201` 秒，无 data-quality reason code |

Acceptance match 为 Sporttery `周六011`，勒沃库森 vs 柏林联合；canonical match ID 为 `b6afb029-e23b-5983-99cf-046a49d06979`。真实竞彩 THREE_WAY SP 为 HOME `1.23`、DRAW `5.40`、AWAY `7.60`。The Odds API accepted ingestion 保留 `16` 个非目标 event 的 structured unresolved issue，没有对其进行模糊或批量映射；目标比赛 coverage 不受影响。

## Analysis Packet 与网页 GPT Review

| 项目 | 值 |
| --- | --- |
| AnalysisRun | `real-positive-acceptance-20260905-leverkusen-union-v1` |
| AnalysisPacket schema | `ANALYSIS_PACKET_V3` |
| Packet ID | `e4510881-241c-5d34-a2c0-c4cd05d0a5a6` |
| Packet contract hash | `245102dcc4aa301be19920b22413924945825b97ac7b989a4436503796927a2b` |
| Packet file SHA-256 | `51b7a7f672d0827c3cd9110990094c4b4c11257c811681587b89066a1a9c133b` |
| Review schema | `LLM_REVIEW_V3` |
| Review file SHA-256 | `e2348094f8856819ee3be2468b4f098cb90ca2b1974a6306035b0e74a7e2ef0f` |
| Validator | `OFFLINE_REVIEW_VALIDATOR_V3` |
| Review artifact ID | `fe808ce2-db33-56ec-997a-e545766867ff` |
| Normalized review hash | `6a092367e3b0f700b387e29bc7cffd3476472ea4d2d94bfdac19fd6bece4cdfc` |
| Imported at | `2026-09-05T13:16:36.892470Z` |

`llm-review validate` 成功确认 `LLM_REVIEW_V3` 与该 Packet ID 的绑定。`llm-review import` 首次写入上述 artifact；再次导入相同 packet/review 返回同一 artifact ID，数据库中该 packet 的 review artifact 数量仍为 `1`，原 `imported_at_utc` 未改变。这证明本次 review 以 append-only、内容寻址和幂等方式落库，而非覆盖既有记录。

验收前后 `analysis_packet_v3.json` 与 `llm_review_v3.json` 的文件 SHA-256 和字节数均未变化。

## Elo 与 Fusion Fail-Closed

| 项目 | 结果 |
| --- | --- |
| Quant model | `ELO_THREE_WAY_BASELINE_V1` |
| Elo evaluation | `MODEL_UNAVAILABLE` |
| unavailable reason | `INSUFFICIENT_PRIOR_MATCHES` |
| `LIVE_STRICT` training facts | `0` |
| P_quant | 未生成 |
| base P_final | 未生成 |
| FusionRun | 按预期拒绝 |
| 拒绝原因 | `FusionRun requires at least one available base prediction` |

系统没有把 research history 重标或混入 live，没有调整 Elo 参数，也没有复制 `P_market` 制造 `P_quant` 或 `P_final`。网页 GPT Review 的成功导入不能绕过 base prediction 可用性门禁；Fusion source 在发现可用 `final_predictions` 为零时，在任何 FusionRun 写入之前终止。

## 拒绝后的持久化验证

| 持久化对象 | 本次 parent/review 下数量 |
| --- | ---: |
| LLMReviewArtifact | `1` |
| FusionRun | `0` |
| Fusion result | `0` |
| PortfolioRevision | `0` |
| FinalPrediction | `0` |
| BetCandidate | `0` |
| TicketCandidate | `0` |
| Ticket | `0` |

原 AnalysisRun 的唯一 Portfolio 保持 `NO_BET`，budget `10000` fen、stake `0`、unused cash `10000` fen，原因是 `NO_BET_NO_VALUE`。没有执行 `portfolio-revision create`，没有投注、出票或自动下注行为。

## 本地验证

| 检查 | 结果 |
| --- | --- |
| 全量 pytest | `500 passed, 31 warnings`，Python 3.13.9，72.06 秒 |
| Ruff | `python -m ruff check .` 通过 |
| compileall | `python -m compileall -q src tests migrations scripts` 通过 |
| fresh migration | 从空 SQLite 升级到 head `6e4b1a9c2d73`，`alembic check` 无待生成操作 |
| wheel build | `football_system-0.4.0-py3-none-any.whl` 构建成功 |
| installed-wheel E2E | 45 个允许资源、live help、manual fixture help、8 条 historical CLI、2 runs x 10 slices、migration head 和报告重放全部通过 |
| secret scan | 扫描 227 个 tracked/candidate 文件；本机真实 key 精确值命中 `0`，高置信 secret pattern 命中 `0` |

31 条 pytest warning 均为既有 Python 3.12+ sqlite3 默认 datetime adapter deprecation warning。Secret scan 识别并仅 allowlist 了单元测试中 2 处显式 `synthetic-secret-token` fixture；没有将其误判为真实凭据。

## 本地工件与发布边界

- `exchange/live_acceptance/analysis_packet_v3.json`、`llm_review_v3.json`、Sporttery evidence、reviewed archives、reconciliation reports 和 SQLite 数据库保持本地，不纳入本次 Git 提交。
- Sportmonks 与 The Odds API raw payload 保持在 `data/raw/`，不纳入 Git。
- API key 只由显式网络命令从本机环境读取，报告不包含 secret。
- 本次可提交内容仅为该无版权原始数据的 acceptance 实施摘要与安全 hash。
- 未创建 FusionRun 或 PortfolioRevision，未继续 Settlement，未 merge `main`。
