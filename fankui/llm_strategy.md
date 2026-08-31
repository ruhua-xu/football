# LLM 动态分析策略

## 角色边界

LLM 的职责是理解固定规则难以完整表达的足球语义信息，并产生：

```text
P_llm
主、次、冷门剧本
比赛方向偏好
剧本失败后的替代关系
动态风险标签
证据化摘要
```

固定程序负责：

```text
P_final
EV
投注候选
串关结构
资金与倍数
Ticket 角色
最终 Portfolio
NO_BET
```

## 冻结 Evidence 原则

生产 LLM 分析不得在推理时自行临时浏览互联网。动态信息必须先经过独立采集和冻结：

```text
External Source
-> EvidenceProvider / EvidenceCollector
-> Append-only Evidence
-> EvidenceSnapshot
-> MatchContext
-> LLMStrategyProvider
```

Evidence 模型：

```text
Evidence
├── evidence_id
├── match_id
├── category
├── statement
├── source_reference
├── observed_at_utc
├── available_at_utc
├── confirmation_status
├── reliability_level
└── payload_hash
```

某次分析只允许使用：

```text
evidence.available_at_utc <= analysis_run.as_of_at_utc
```

MatchContext 必须引用具体 `evidence_snapshot_id` 和 `snapshot_hash`。历史回放使用原 Evidence Snapshot，不重新访问互联网，也不重新调用模型。

## 接口分层

### Provider Port

```python
class LLMStrategyProvider(Protocol):
    async def generate_assessment(
        self,
        request: LLMProviderRequest,
    ) -> LLMProviderResponse:
        ...
```

Provider 只处理供应商调用和统一错误转换。其返回值仍是不可信响应。

### Application Service

```python
class LLMAssessmentService(Protocol):
    async def assess_match(
        self,
        context: MatchContext,
        *,
        analysis_run_id: UUID,
        deadline_utc: datetime,
    ) -> LLMAssessmentOutcome:
        ...
```

`LLMAssessmentOrchestrator` 负责：

- 从冻结 MatchContext 生成白名单 LLMInputView。
- 渲染并版本化 prompt。
- 附加严格 JSON Schema。
- 控制 timeout、retry、cache 和 idempotency。
- 保存原始请求、响应和验证报告。
- 校验概率、剧本、方向和 Evidence 引用。
- 在失败或结果过期时生成明确降级结果。

## MatchContext

```text
MatchContext
├── context_schema_version
├── snapshot_id
├── context_digest
├── match_id
├── as_of_at_utc
├── kickoff_at_utc
├── competition
├── home_team
├── away_team
├── market_context
├── performance_context
├── availability_context
├── schedule_context
├── p_market
├── p_quant
├── evidence_snapshot_id
├── evidence[]
└── data_quality
```

LLMInputView 采用固定白名单，不包含：

```text
budget
stake
EV
P_final
fusion_weight
PortfolioConstraints
strategy_config
```

## LLMMatchAssessment

> 本节是后续扩展设计，不是当前离线导入合同。当前实现采用严格的 `LLM_REVIEW_V1` 子集；准确字段及示例见 `llm_review_v1_contract.md`。未列入该合同的 `semantic_factors`、`risk_score`、`market_interpretation`、`scenario_relationships`、`opinion` 和 `relationship` 字段都会被拒绝。

### 系统 Envelope

以下字段由系统附加，不能由模型自行填写：

```text
assessment_id
analysis_run_id
match_id
context_snapshot_id
context_digest
evidence_snapshot_id
as_of_at_utc
provenance
```

### 模型 Payload

```text
LLMMatchAssessmentPayload
├── p_llm
├── assessment_confidence
├── scenarios
├── preferred_outcomes
├── avoid_outcomes
├── counter_scenarios
├── scenario_relationships
├── semantic_factors
├── risk_score
├── risk_tags
├── market_interpretation
├── reasoning_summary
└── limitations
```

### Scenario

```text
Scenario
├── scenario_id
├── scenario_type: MAIN | SECONDARY | UPSET
├── market_key
├── outcome
├── summary
├── trigger_conditions
└── evidence_ids
```

### OutcomeOpinion

`preferred_outcomes` 和 `avoid_outcomes` 使用统一结构：

```text
OutcomeOpinion
├── market_key
├── outcome
├── opinion: PREFERRED | AVOID
├── strength: LOW | MEDIUM | HIGH
├── rationale
└── evidence_ids
```

这里的 `AVOID` 表示语义上脆弱或证据不支持，不等于系统最终 `NO_BET`。`PREFERRED` 也不等于投注推荐。

### CounterScenario

```text
CounterScenario
├── if_scenario_id
├── fails_outcome
├── alternative_scenario_id
├── relationship: MOST_LIKELY_ALTERNATIVE
├── rationale
└── evidence_ids
```

例如主剧本为 HOME_WIN 时，LLM 可以指出其失败后的最可能替代剧本为 DRAW。

### ScenarioRelationship

```text
ScenarioRelationship
├── source_scenario_id
├── target_scenario_id
├── relationship_type
├── strength
└── evidence_ids
```

允许的语义关系使用固定枚举，例如：

```text
SUPPORTS
CONFLICTS_WITH
FAILURE_ALTERNATIVE
SHARES_RISK_SOURCE
```

禁止模型自行创造可执行的“对冲规则”。

## 禁止输出

LLM Schema 明确禁止：

```text
EV
stake
budget
P_final
fusion_weight
parlay_structure
ticket_role
final_ticket
final_portfolio
NO_BET
strategy_config
executable_code
SQL
```

Schema 使用 `additionalProperties: false`。即使模型在文本中提出这些内容，也不能进入有效领域对象。

## 校验

### 结构校验

- 必须是严格 JSON，不接受 Markdown 包裹。
- 禁止重复 key、NaN、Infinity 和额外字段。
- 字符串和数组设置长度上限。
- MarketKey 和 SelectionKey 必须在注册表中有效。

### 概率校验

```text
0 <= p <= 1
abs(sum(p_llm) - 1) <= 1e-6
```

只有总和处于微小容差内时才允许规范化。负数、缺项或明显总和错误直接使本次 Assessment 不可用。

### Evidence 校验

- 所有 `evidence_ids` 必须属于当前 Evidence Snapshot。
- Evidence 的 `available_at_utc` 必须不晚于 AnalysisRun 截止时间。
- 模型不得引用输入中不存在的来源。

### 语义一致性

- MAIN、SECONDARY、UPSET 的 scenario_id 必须唯一。
- 关系引用的 scenario_id 必须存在。
- 同一个 MarketKey 下不得同时把同一 Outcome 标记为 PREFERRED 和 AVOID。
- OutcomeOpinion 的 strength 不是概率，也不是融合权重。

## 失败与降级

```text
LLMAssessmentOutcome
├── ValidLLMAssessment
└── UnavailableLLMAssessment
```

建议失败码：

```text
SKIPPED_DISABLED
TIMEOUT
NETWORK_ERROR
RATE_LIMITED
INVALID_JSON
SCHEMA_VIOLATION
PROBABILITY_VIOLATION
EVIDENCE_VIOLATION
STALE_CONTEXT
PROVIDER_ERROR
```

禁止用均匀概率或复制 `P_market/P_quant` 伪造 `P_llm`。

固定融合层的行为：

```text
LLM 有效 -> 由配置的 FusionPolicy 决定是否使用
LLM 无效 -> 使用不含 LLM 的确定性 FusionPolicy
Market 和 Quant 都无效 -> 不允许 LLM 单独驱动投注
```

## 审计和回放

每次逻辑调用保存：

```text
invocation_id
analysis_run_id
context_digest
evidence_snapshot_id
request_fingerprint
prompt_id/version/digest
schema_id/version/digest
validator_version
deployment_route
requested_model
resolved_model
generation_options
status
failure_code
```

每次物理尝试保存：

```text
attempt_id
provider_request_id
started_at_utc
completed_at_utc
duration_ms
raw_response
raw_response_hash
validation_report
token_usage
```

不得保存 API Key、Authorization Header 或其他密钥。历史决策回放直接读取当时已验证 Assessment，不重新调用模型。

## MVP 行为

MVP 不接真实 LLM：

```text
DisabledLLMStrategyProvider -> SKIPPED_DISABLED
FixtureLLMStrategyProvider -> Contract / Validator tests
```

Fixture Provider 至少覆盖：

- 合法 `P_llm` 和剧本关系。
- preferred/avoid 冲突。
- 不存在的 Evidence 引用。
- 非法概率总和。
- 额外禁止字段。
- Context 过期。
