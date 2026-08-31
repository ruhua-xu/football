# LLM_REVIEW_V1 离线合同

`LLM_REVIEW_V1` 是 `analysis_packet.json` 对应的严格、追加型离线 Review 合同。导入过程不调用网络，不更新原 AnalysisRun，也不参与当前 `P_final`、EV、Ticket 或 Portfolio 计算。

## 顶层字段

- `schema_version`：固定为 `LLM_REVIEW_V1`。
- `analysis_run_id`：必须与 Packet 完全一致。
- `packet_id`：必须与 Packet 完全一致。
- `packet_hash`：必须与 Packet 完全一致，为 64 位小写 SHA-256。
- `match_reviews`：必须逐场覆盖 Packet，不能遗漏、增加或重复；最多 256 场。

所有未声明字段均会被拒绝。JSON 还会拒绝重复 key、UTF-8 BOM、非有限数值、超过 128 层的嵌套和超过 1 MB 的文件。ID、hash、版本、枚举及概率等机器字段不允许首尾空白；自由文本按 Domain 规则去除首尾空白。

## 单场结果

每场结果使用 `status` 区分两种结构。

### VALID

- `status`：`VALID`。
- `match_id`、`market_key`：必须与 Packet 对应比赛一致。
- `p_llm`：绝对三项概率 `home_win`、`draw`、`away_win`，每项位于 `[0, 1]` 且总和为 1；不能提交概率增量。
- `assessment_confidence`：`[0, 1]`。
- 合同中的概率和 confidence 最多保留 24 个有效数字、小数点后 18 位；等价的尾随零不计入限制。
- `scenarios`：最多 16 项，`scenario_id` 唯一。
- `preferred_outcomes`、`avoid_outcomes`：各最多 16 项；同一方向不能重复，也不能同时 preferred 和 avoid。
- `counter_scenarios`：最多 16 项，只能引用本场已声明的 scenario。
- `risk_tags`、`limitations`：各最多 32 项且不能重复。
- `reasoning_summary`：非空，最多 4000 字符。

Scenario 字段：`scenario_id`、`scenario_type`、`market_key`、`outcome`、`summary`、`trigger_conditions`、`evidence_ids`。

Outcome opinion 字段：`market_key`、`outcome`、`strength`、`rationale`、`evidence_ids`。所属集合已经表达 preferred 或 avoid，不能额外提交 `opinion`。

Counter scenario 字段：`if_scenario_id`、`fails_outcome`、`alternative_scenario_id`、`rationale`、`evidence_ids`。

`scenario_type` 仅允许 `MAIN`、`SECONDARY`、`UPSET`；`strength` 仅允许 `LOW`、`MEDIUM`、`HIGH`；`outcome` 仅允许 `HOME_WIN`、`DRAW`、`AWAY_WIN`。所有 `evidence_ids` 必须来自对应 Packet 比赛。

### UNAVAILABLE

- `status`：`UNAVAILABLE`。
- `match_id`、`market_key`：必须与 Packet 一致。
- `failure_code`：`SKIPPED_DISABLED`、`INVALID_CONTEXT`、`INSUFFICIENT_EVIDENCE` 或 `MODEL_UNAVAILABLE`。
- `limitations`：最多 32 项且不能重复。

## 完整示例

以下绑定值必须替换为目标 Packet 中的原值。

```json
{
  "schema_version": "LLM_REVIEW_V1",
  "analysis_run_id": "run-id-from-packet",
  "packet_id": "packet-id-from-packet",
  "packet_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "match_reviews": [
    {
      "status": "VALID",
      "match_id": "match-id-from-packet",
      "market_key": "THREE_WAY",
      "p_llm": {
        "home_win": "0.50",
        "draw": "0.30",
        "away_win": "0.20"
      },
      "assessment_confidence": "0.60",
      "scenarios": [
        {
          "scenario_id": "main-home-control",
          "scenario_type": "MAIN",
          "market_key": "THREE_WAY",
          "outcome": "HOME_WIN",
          "summary": "Home controls territory and limits transitions.",
          "trigger_conditions": ["Home starting lineup remains available"],
          "evidence_ids": []
        }
      ],
      "preferred_outcomes": [
        {
          "market_key": "THREE_WAY",
          "outcome": "HOME_WIN",
          "strength": "MEDIUM",
          "rationale": "The main scenario supports the home outcome.",
          "evidence_ids": []
        }
      ],
      "avoid_outcomes": [],
      "counter_scenarios": [],
      "risk_tags": ["LINEUP_UNCERTAINTY"],
      "reasoning_summary": "Assessment uses only the supplied frozen packet.",
      "limitations": ["No external evidence was supplied"]
    }
  ]
}
```

## 校验与导入

```powershell
.venv\Scripts\football-system llm-review validate --packet "exchange/analysis_packet.json" --review "exchange/llm_review.json"
.venv\Scripts\football-system llm-review import --database-url "sqlite:///data/football_mvp.db" --packet "exchange/analysis_packet.json" --review "exchange/llm_review.json"
```

数组在导入前会按合同语义规范化。同一 Packet、规范化 Review hash 和 validator version 的重复导入返回已有工件，不新增记录。
