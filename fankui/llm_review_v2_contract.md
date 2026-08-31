# ANALYSIS_PACKET_V2 / LLM_REVIEW_V2 离线合同

V2 在 V1 的严格文件绑定上增加可审计 MatchReviewContext。它仍是 UTF-8 离线 JSON，不调用网络；网页 GPT 只能提交绝对 `P_llm`，概率修正、截断和最终融合全部在本地完成。

## 兼容策略

- `ANALYSIS_PACKET_V1` 只能搭配 `LLM_REVIEW_V1`。
- `ANALYSIS_PACKET_V2` 只能搭配 `LLM_REVIEW_V2`。
- V1 导出、校验和导入保持可用，CLI 默认仍导出 V1。
- 新闭环显式使用 `--schema-version ANALYSIS_PACKET_V2`。
- 不允许 V1/V2 混用或将旧文件静默解释为新合同。

## MatchReviewContext

每场 V2 Packet 的 `review_context` 固定支持：

- `sporttery_odds`
- `international_odds`
- `odds_movement_summary`
- `recent_form`
- `home_away_form`
- `rest_days`
- `schedule_context`
- `injuries`
- `suspensions`
- `expected_lineup`
- `confirmed_lineup`
- `evidence`
- `data_quality`

当前 Mock 数据只提供封存的国际赔率与竞彩固定奖金。其余字段显式为 `null` 或空数组，并在 `data_quality.missing_fields` 和 `notes` 中说明，不伪造伤停、阵容或近期状态。

每条 Evidence 至少包含：

- `evidence_id`、`category`、`body`
- `source_kind`、`source_name`、`source_reference`
- `source_record_id`、`source_payload_hash`
- `observed_at_utc`、`available_at_utc`

因此 Evidence 是带正文和来源记录的可审计输入，而不是只有 ID 的占位符。

## 严格白名单

Packet 可以包含比赛身份、封存输入、`P_market`、`P_quant` 和 ReviewContext，但不得包含：

- `P_final`
- EV 或 SelectionCandidate ranking
- Ticket、Portfolio
- budget、stake
- fusion weight
- Strategy Profile 资金参数

## LLM_REVIEW_V2

顶层字段与 V1 相同，但 `schema_version` 固定为 `LLM_REVIEW_V2`。每场 `VALID` 或 `UNAVAILABLE` 结果还必须原样回显：

- `review_context_id`
- `review_context_hash`

这两个字段必须与 Packet 对应比赛完全一致。`VALID` 继续提交三项绝对概率 `p_llm` 和 `assessment_confidence`，不能提交 `probability_delta`、`P_final`、EV 或资金决策。

最小 `VALID` 示例：

```json
{
  "schema_version": "LLM_REVIEW_V2",
  "analysis_run_id": "run-id-from-packet",
  "packet_id": "packet-id-from-packet",
  "packet_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "match_reviews": [
    {
      "status": "VALID",
      "match_id": "match-id-from-packet",
      "market_key": "THREE_WAY",
      "review_context_id": "context-id-from-packet",
      "review_context_hash": "0000000000000000000000000000000000000000000000000000000000000000",
      "p_llm": {
        "home_win": "0.50",
        "draw": "0.30",
        "away_win": "0.20"
      },
      "assessment_confidence": "0.60",
      "scenarios": [],
      "preferred_outcomes": [],
      "avoid_outcomes": [],
      "counter_scenarios": [],
      "risk_tags": [],
      "reasoning_summary": "Assessment uses only the supplied sealed context.",
      "limitations": []
    }
  ]
}
```

`UNAVAILABLE` 仍使用 V1 failure code：`SKIPPED_DISABLED`、`INVALID_CONTEXT`、`INSUFFICIENT_EVIDENCE` 或 `MODEL_UNAVAILABLE`。本地 FusionRun 会记录 fallback 并继续处理其他比赛。

## 本地闭环

```powershell
.venv\Scripts\football-system analysis-packet export `
  --database-url "sqlite:///data/football_mvp.db" `
  --analysis-run-id "run-001" `
  --schema-version ANALYSIS_PACKET_V2 `
  --output "exchange/analysis_packet_v2.json"

.venv\Scripts\football-system llm-review validate `
  --packet "exchange/analysis_packet_v2.json" `
  --review "exchange/llm_review_v2.json"

.venv\Scripts\football-system llm-review import `
  --database-url "sqlite:///data/football_mvp.db" `
  --packet "exchange/analysis_packet_v2.json" `
  --review "exchange/llm_review_v2.json"

.venv\Scripts\football-system fusion-run create `
  --database-url "sqlite:///data/football_mvp.db" `
  --review-artifact-id "artifact-id-from-import"

.venv\Scripts\football-system portfolio-revision create `
  --database-url "sqlite:///data/football_mvp.db" `
  --fusion-run-id "fusion-run-id-from-create"
```

FusionRun 和 PortfolioRevision 均为 append-only。它们引用原 AnalysisRun，但不会更新原 `P_final`、候选、Ticket、Portfolio 或风险报告。
