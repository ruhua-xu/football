# ANALYSIS_PACKET_V3 / LLM_REVIEW_V3 离线合同

V3 在 V2 的 MatchReviewContext 上增加显式 manual/model `P_quant` lineage。文件仍为 UTF-8、canonical JSON、最大 1,000,000 bytes，不调用网络；网页 GPT 只能返回绝对 `P_llm` 和语义判断，本地程序继续负责概率修正、截断、融合、EV、Ticket、Portfolio 和风险约束。

## 兼容策略

- `ANALYSIS_PACKET_V1` 只能搭配 `LLM_REVIEW_V1`。
- `ANALYSIS_PACKET_V2` 只能搭配 `LLM_REVIEW_V2`。
- `ANALYSIS_PACKET_V3` 只能搭配 `LLM_REVIEW_V3`。
- CLI 默认仍为 `ANALYSIS_PACKET_V1`；V3 必须显式选择。
- V1/V2 继续只接受 manual `P_quant` lineage；model run 必须使用 V3，不能静默降级或改写旧合同。
- V1/V2 固定 fixture 的 packet ID、packet hash 和 canonical bytes 由 golden regression test 封存。

## Run 与顶层 lineage

V3 的 `analysis_run` 在 V2 字段之外增加 `started_at_utc`，并要求：

```text
as_of_at_utc <= started_at_utc <= completed_at_utc <= generated_at_utc
```

顶层 `quant_model_states` 对 model run 去重保存以下结构化字段：

- state ID、AnalysisRun ID、model name/version、calibration label。
- config/state/state-payload/training-data hashes。
- cutoff、generated time、season ID 和 training fact count。
- 按训练顺序保存的 `training_match_ids` 与 `training_result_ids`。

Packet 不嵌入 `config_json`、`state_json` 或 `output_json`。Repository 在投影前用这些 sealed canonical payload 重验数据库记录，但对外文件只暴露必要的结构化 lineage，避免任意字段穿透白名单和重复放大文件。

model state cutoff 必须等于 AnalysisRun cutoff；state、evaluation 和 model prediction 必须位于实际 run start/completion 窗口。target match ID 不得出现在任何引用 state 的训练 match IDs 中。一次 AnalysisRun 不得混合 manual 与 model quant lineage。

## 每场 P_quant

V3 的 `p_quant` 使用 `source_kind` 判别联合。

manual 形式固定为：

```json
{
  "source_kind": "MANUAL",
  "status": "AVAILABLE",
  "prediction": {
    "prediction_id": "quant-prediction-id",
    "probabilities": {
      "home_win": "0.50",
      "draw": "0.30",
      "away_win": "0.20"
    },
    "manual_input_id": "manual-input-id",
    "input_payload_hash": "manual-payload-hash"
  }
}
```

model 形式包含结构化 evaluation；`AVAILABLE` 时还必须包含唯一 model prediction，二者的 run、match、market、evaluation ID 和 probabilities 必须一致：

```json
{
  "source_kind": "MODEL",
  "status": "AVAILABLE",
  "evaluation": {
    "quant_model_evaluation_id": "evaluation-id",
    "analysis_run_id": "run-id",
    "quant_model_state_id": "state-id",
    "match_id": "match-id",
    "market": {"market_type": "THREE_WAY", "handicap_value": null},
    "status": "AVAILABLE",
    "unavailable_reason": null,
    "probabilities": {
      "home_win": "0.50",
      "draw": "0.30",
      "away_win": "0.20"
    },
    "output_hash": "0000000000000000000000000000000000000000000000000000000000000000",
    "model_prediction_hash": "0000000000000000000000000000000000000000000000000000000000000000",
    "evaluated_at_utc": "2026-09-03T12:00:00Z"
  },
  "prediction": {
    "prediction_id": "quant-prediction-id",
    "analysis_run_id": "run-id",
    "match_id": "match-id",
    "market": {"market_type": "THREE_WAY", "handicap_value": null},
    "probabilities": {
      "home_win": "0.50",
      "draw": "0.30",
      "away_win": "0.20"
    },
    "quant_model_evaluation_id": "evaluation-id",
    "method": "ELO_THREE_WAY_BASELINE_V1",
    "method_version": "1",
    "generated_at_utc": "2026-09-03T12:00:00Z"
  }
}
```

`UNAVAILABLE` model evaluation 必须包含非空 `unavailable_reason`、`probabilities=null` 和 `prediction=null`。系统禁止从 `P_market`、默认值或其他比赛复制概率。

## 严格白名单

V3 保留 V2 的比赛身份、赔率 Evidence 和 MatchReviewContext，并允许上述 quant lineage。Packet 仍不得包含：

- `P_final`、probability delta 或 fusion weight。
- EV、SelectionCandidate ranking。
- Ticket、Portfolio、budget、stake。
- Strategy Profile 资金参数。
- 未列入合同的 model payload 字段。

packet hash 是移除 `packet_hash` 后 canonical JSON 的 SHA-256。packet ID 继续绑定 AnalysisRun ID、schema version、input manifest hash 和 code revision。导入还必须与数据库中 append-only packet 完全一致，因此自行重算外部文件 hash 不能替换已封存 packet。

## LLM_REVIEW_V3

V3 review 复用 V2 的 `VALID`/`UNAVAILABLE` match shape，并必须原样回显 `review_context_id` 与 `review_context_hash`。`VALID` 只能提交绝对 `p_llm`、confidence、scenario/opinion 和 Evidence 引用，不能提交本地决策字段。

当 Packet 中 model `P_quant` 为 `UNAVAILABLE` 时，对应 review 必须为：

```json
{
  "status": "UNAVAILABLE",
  "match_id": "match-id-from-packet",
  "market_key": "THREE_WAY",
  "failure_code": "MODEL_UNAVAILABLE",
  "limitations": ["Model P_quant is unavailable."],
  "review_context_id": "context-id-from-packet",
  "review_context_hash": "0000000000000000000000000000000000000000000000000000000000000000"
}
```

这类比赛没有 base `P_final`，不会出现在后续 FusionRun results 中。若整次 AnalysisRun 没有任何 available base prediction，review 仍可校验和追加导入，但创建 FusionRun 会显式拒绝；系统不会为完成闭环而伪造 base probability。

## 本地闭环

```powershell
.venv\Scripts\football-system analysis-packet export `
  --database-url "sqlite:///data/football_mvp.db" `
  --analysis-run-id "run-model-001" `
  --schema-version ANALYSIS_PACKET_V3 `
  --output "exchange/analysis_packet_v3.json"

.venv\Scripts\football-system llm-review validate `
  --packet "exchange/analysis_packet_v3.json" `
  --review "exchange/llm_review_v3.json"

.venv\Scripts\football-system llm-review import `
  --database-url "sqlite:///data/football_mvp.db" `
  --packet "exchange/analysis_packet_v3.json" `
  --review "exchange/llm_review_v3.json"
```

Packet、review artifact、FusionRun 和 PortfolioRevision 均为 append-only；任何 V3 操作都不会更新原 AnalysisRun 或旧 V1/V2 文件。
