# ADR-0008：审批门控的回溯历史用于当前生产模型训练

## 状态

Proposed

本提案尚未授权任何运行时代码、数据库迁移、真实数据导入或 package 版本变更。外部架构审查通过并将状态改为 `Accepted` 前，`0.5.0` 的 `LIVE_STRICT` guard、`MODEL_UNAVAILABLE` 行为和 ADR-0007 全部保持不变。

## 与 ADR-0007 的关系

ADR-0007 对来源事实的分类、时间语义、运行隔离和报告标签继续完整生效。`HistoricalDataMode` 仍然只有两个互斥值：

```text
LIVE_STRICT
SOURCE_TIME_RESEARCH
```

本提案不修改这两个值的含义，不增加第三种 data mode，也不把后来取得的历史记录重标为 `LIVE_STRICT`。它增加一个与 source mode 正交、仅限生产模型训练用途的内部授权类别：

```text
training_use_class = APPROVED_TRAINING_HISTORY
```

`APPROVED_TRAINING_HISTORY` 回答的是“本系统现在是否获准用这组精确事实构建指定的生产 model release”，不是“本系统是否在历史 source time 已经持有这些事实”。每条事实永久保留 `SOURCE_TIME_RESEARCH`、`retrospective=true`、source time 和实际 local import/register time。

为保持 ADR-0007 的 provider/run 隔离，approved research provider 不直接进入 live AnalysisRun。生产训练在独立、mode-homogeneous 的 build operation 中完成；live AnalysisRun 只消费该 operation 产生的 hash-sealed model release，并继续只从 `LIVE_STRICT` provider 读取当前 fixture、odds、Sporttery 和其他 decision facts。

## 背景

`0.5.0` 的 live model path 只接受合格 `LIVE_STRICT` training history。由于系统尚未实时积累足够长的赛果窗口，第一次真实 V3 handshake 正确地产生了 `MODEL_UNAVAILABLE`。直接将 `SOURCE_TIME_RESEARCH` provider 放入 live runtime 会破坏 ADR-0007；等待自然积累多个赛季又会长期阻塞 Production Quant Bootstrap。

现有实现还存在以下结构性缺口：

- `HistoricalArchiveEloTrainingProvider` 由构造参数接收一个 `season_id`，并把该值赋给全部结果，无法表达每场比赛真实所属的多个赛季。
- `HISTORICAL_ARCHIVE_V1` 的 Match/Mapping 没有 season membership，单独复制 `season_id` 不能证明其真实性。
- `MatchResult` 只保存比分和三个时间，不保存 provider final status、status mapping version、regular-time score evidence 或 finalized time。
- 历史 archive import 当前只登记 manifest provenance；AnalysisRepository 保存 training fact 时却要求对应 MatchResult 已经存在于 SQLite。
- 现有 Elo `training_data_hash` 封存数学输入，但不封存 rights、actual import、fixture/mapping/result source record 和 approval graph。
- `ANALYSIS_PACKET_V3` 公开 state/training hashes，但不包含 production-training approval；V3 本身不能证明该 state 获准用于生产。

Production bootstrap 必须解决这些缺口，同时保持 Elo 算法、现有概率/fusion policy、V3 wire contract 和投注边界不变。

## 决策

### 1. Source mode、训练资格与执行阶段分离

初始实现采用以下准入矩阵：

| 事实状态 | 来源时间 walk-forward | Production model build | Live AnalysisRun 直接读取 | 允许的声明 |
| --- | --- | --- | --- | --- |
| 合格 `LIVE_STRICT` | 只能进入同 mode 运行 | 保持 `0.5.0` 既有 strict path | 可由既有 live training path 使用 | 从真实 ingestion time 起持有 |
| 普通 `SOURCE_TIME_RESEARCH` | 可运行，必须标记 `RETROSPECTIVE_SOURCE_TIME_RESEARCH` | 禁止 | 禁止 | 仅来源时间研究 |
| `SOURCE_TIME_RESEARCH` + active `APPROVED_TRAINING_HISTORY` | Pilot 仍是 retrospective research | 只允许构建绑定精确 approval 的 model release | 禁止直接读取；live 只消费派生 release | 当前生产预测使用经审批的回溯训练历史；不得称为 `LIVE_STRICT` training |
| 未知 source availability | 禁止严格 point-in-time | 禁止 | 禁止 | Approval 不能补造时间 |
| `SYNTHETIC_ACCEPTANCE_DATA` | 仅合同测试 | 禁止 | 禁止 | 不构成真实历史或生产证据 |
| Approval/release 过期、撤销、stale 或 hash 不匹配 | 已封存研究不改写 | 禁止新 build | 禁止新 run/downstream artifact | 明确 fail closed |

`TRAINING_HISTORY_APPROVAL_V1` 只解决 retrospective `SOURCE_TIME_RESEARCH` bootstrap，并赋予 `training_use_class=APPROVED_TRAINING_HISTORY`。它不取代、收紧或放宽普通 `LIVE_STRICT` path。一个 history manifest 只能使用一种 source mode；V1 固定为 `SOURCE_TIME_RESEARCH` 和 `source_classification=REAL_SOURCE_DATA`。`REAL_SOURCE_DATA` 仅表示非 synthetic 且通过 source rights/fact admission，不表示 `LIVE_STRICT` 或自动获得 production 权利。

完整流程为：

```text
SOURCE_RIGHTS_ADMISSION_V1
  -> SOURCE_TIME_RESEARCH archives
  -> PRODUCTION_QUANT_PILOT_PLAN_V1
  -> point-in-time research pilot/report
  -> TRAINING_HISTORY_MANIFEST_V1
  -> TRAINING_HISTORY_APPROVAL_V1 event
  -> offline PRODUCTION_QUANT_MODEL_RELEASE_V1 build

LIVE_STRICT current decision facts
  + pinned PRODUCTION_QUANT_MODEL_RELEASE_V1
  -> current AnalysisRun
  -> ANALYSIS_PACKET_V3 + mandatory approval audit sidecar
```

所有 run、report 和 metric 必须声明完整 provenance tuple，不能只显示一个容易误解的 `LIVE_STRICT` 标签：

```text
decision_data_mode
model_training_source_mode
model_training_use_class
training_history_approval_id / training_history_approval_hash
production_model_release_id / production_model_release_hash
retrospective_source_facts_present
```

含 approved retrospective history 的 model release、当前预测和后续 prospective metric 不得与纯 `LIVE_STRICT`-trained model 静默聚合或公平比较。

### 2. 两阶段 source rights 门禁

软件 hash 只能证明内容完整，不能自行判断法律权利。系统实现的是可审计的内部授权门禁，不作法律结论。

任何第三方历史数据在取得、保存或 pilot 前，必须先封存结构化 `SOURCE_RIGHTS_ADMISSION_V1`：

```text
source_rights_admission_id / schema_version / admission_hash
source_owner / product_name / source_ids
terms_version / terms_reference / terms_sha256
authorized_reviewer / reviewer_authority_reference / authority_sha256
jurisdiction / effective_at_utc / expires_at_utc
permitted_use = ACQUIRE | STORE_LOCAL | NORMALIZE | INTERNAL_RESEARCH
raw_retention_rule / derived_retention_rule
subscription_end_retention_rule / deletion_obligation
public_repository_boundary
recorded_at_utc
```

`SOURCE_RIGHTS_ADMISSION_V1` 使用与 training approval 相同的两级 envelope：reviewer 先签署排除 signature/ID/hash 的 rights payload hash，admission hash 再覆盖 payload hash、signature metadata 和 signature evidence SHA-256。

Research/storage rights 不自动包含 production rights。Pilot 完成后，独立 approval event 必须另外确认：

```text
PRODUCTION_MODEL_TRAINING
PRODUCTION_MODEL_INFERENCE
DERIVED_MODEL_STATE_RETENTION
AUDIT_HASH_RETENTION
```

Approval 必须绑定 exact source rights admission、terms hash、history manifest hash、pilot plan/attempt-root/attestation/report hashes、model/config、build recipe/code revision 和 season scope，并保存 approver authority、effective/expiry time 及签名或等价不可抵赖 evidence 的 reference/hash。签名覆盖独立的 `approval_payload_hash`；approval event hash 再覆盖 payload hash、signature algorithm、signer key ID 和 signature evidence SHA-256，签名字节和 event ID/hash 不进入被签 payload，避免循环。

若条款要求删除的内容与 append-only training fact、model lineage 或 audit hash 的最低保留要求冲突，该 source 不具备本路径资格。Terms 过期、权限不明、授权主体不匹配或 retention 不兼容均 fail closed。Raw rights/evidence 保留在本地受控路径，不进入 public Git。

### 3. 事实、完成状态与 season admission

`TRAINING_HISTORY_MANIFEST_V1` 只允许真实、已完成比赛的常规时间赛果，以及重放该赛果所需的最小 identity/provenance。每个 fact 必须绑定三个独立 source record：

```text
fixture source record
provider mapping + season membership record
match-result admission record
```

新增 immutable `MATCH_RESULT_ADMISSION_V1`，至少保存：

```text
match_result_admission_id / admission_hash
internal_match_id / match_result_id
provider_code / provider_result_key
provider_raw_status / status_mapping_version
normalized_status = REGULAR_TIME_FINAL
regular_time_home_goals / regular_time_away_goals
provider_finalized_at_utc
source_observed_at_utc / source_available_at_utc
raw_artifact_id / raw_record_sha256 / normalized_record_sha256
adapter_name / adapter_version / reviewed_by / reviewed_at_utc
```

只有 provider status 和 score semantics 能明确映射到 `REGULAR_TIME_FINAL` 时才创建 MatchResult。加时、点球、取消、延期、腰斩、进行中或语义不明状态 fail closed。现有只覆盖比分的 payload hash 不能替代该 admission hash。

新增 immutable `MATCH_SEASON_MEMBERSHIP_V1`，至少保存：

```text
season_membership_id / membership_hash
provider_code / provider_competition_id / provider_season_id
provider_fixture_key / provider_mapping_id / internal_match_id
fixture_source_record_id / fixture_record_sha256
provider_scope_raw_artifact_id / provider_scope_record_sha256
provider season field path / season_mapping_version
canonical_competition_id / canonical_season_id
source_available_at_utc / local_imported_at_utc / registered_at_utc
mapping_policy_version / reviewed_by / reviewed_at_utc
```

每场 `season_id` 必须来自该 membership，且与 canonical identity、provider season scope、fixture record 以及原始 bytes 中明确的 provider competition/season/fixture 关系一致。仅复制 season 字段或 hash 一个不含 season 的 fixture record 不构成证据。不能从 target season、文件夹名称、比赛日期启发式规则或 provider 构造参数批量覆盖。

采集后的 `TRAINING_FACT_ADMISSION_V1` 是持久化、hash-sealed parent artifact，不只是事务名称。它保存 admission ID/hash、source rights admission ID/hash、source mode/classification、actual started/completed/persisted times 和 admitted fact count/root；typed child rows 分别绑定 fixture source、season membership、MatchResult admission 与 normalized MatchResult。该 transaction 原子验证 archive/record hash、物化或幂等复用 normalized MatchResult，并写入全部关系。它与采集前的 `SOURCE_RIGHTS_ADMISSION_V1` 是不同事件，前者不能追溯替代后者；它必须在 pilot plan sealed 前完成。裸 `match_results` row 没有 data mode 或 approval 资格，任何 production builder 都不得绕过这些关系直接查询它。

### 4. 时间合同与未来数据隔离

所有 persisted time 均为真实 UTC。`decision_as_of_at_utc` / `evaluation_as_of_at_utc` 仍只是知识 cutoff，不能替代实际 import、plan、run、approval 或 build time。

对 retrospective pilot 中某个 slice，必须同时满足：

```text
training kickoff
  < training result finalized
  <= training source_available
  = training source_time_ingested
  < slice decision_as_of
  < target kickoff
  < target result source_available
  <= slice evaluation_as_of
  <= pilot actual_started
  <= pilot actual_completed
```

以及独立的实际 possession chain：

```text
source rights admission recorded
  <= local imported
  <= source archive created
  <= database archive registered
  <= pilot plan sealed
  <= pilot actual_started
```

`local_imported_at_utc` 晚于历史 decision/evaluation cutoff 是合法且必须披露的 retrospective 事实。它不能被改写为 source time，也不能让 pilot 被描述成历史 live performance。

对当前 production target，必须满足：

```text
all approved training kickoff/result/effective_source_available < production decision_as_of
pilot actual_completed
  <= pilot attestation persisted
  <= training history manifest created
  <= approval event approved_at
  <= approval event persisted_at
  < model release build started
  <= model release build completed
  <= model release persisted
  < target acceptance plan sealed
  < production decision_as_of
  <= live run started
  <= live run completed
  < target kickoff
```

Source rights admission、actual local import、fact admission 和 database registration 也必须不晚于 manifest creation。每个 fact 的 fixture record、mapping/season membership 和 result admission 都必须独立满足 source visibility cutoff；`effective_source_available_at_utc` 是三者 source availability 的最大值，只用于汇总检查，不能替代逐项校验。相等只允许系统在同一真实时刻有明确顺序的事务事件；approval/release persisted 与 production decision 必须严格分离，不能在 cutoff 后补批。

Training fact 无条件排除当前 slice/production run 的全部 target match IDs。Walk-forward 的 exclusion 是 per-slice：较早 target 只有在其 result source availability 进入后续 decision cutoff 后，才可作为后续 slice 的训练事实。

Elo V1 training allowlist 仅为 exact match identity、home/away team、kickoff、真实 season 和 regular-time final score。赔率、竞彩、未来 fixture、未来 result、league table、排名、伤停或其他状态不能进入 training facts。Pilot 的 `P_market` 赔率是独立 decision input，必须按每个 slice 的 cutoff 选择。

### 5. 固定 Elo 合同

Production Quant Bootstrap 不改变 `ELO_THREE_WAY_BASELINE_V1`、model version `1` 或 `BASELINE_UNCALIBRATED`。参数固定为：

```text
initial_rating = 1500
k_factor = 20
home_advantage = 100
season_regression_factor = 0.75
draw_probability = 0.25
minimum_prior_matches = 5
config_hash = c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4
```

赛季窗口另以 `ELO_TRAINING_WINDOW_V1` 版本化，只描述 competition、按时间排序的 season IDs、各赛季角色、history cutoff 和 target exclusion。它不是可拟合参数。

Pilot 前冻结现有受支持的 fusion policy/config。任何人或程序都不得根据 pilot 的 Brier、LogLoss、Calibration、availability、ROI、网页 GPT review 或单场结果修改 Elo 参数、season window、cohort、cutoff、odds selection、fusion policy 或 weight。任何变更必须创建新 plan 并披露全部尝试，不得替换既有结果；本阶段不以这些变更申请 approval。

### 6. Precommitted Bundesliga pilot

第一条 pilot lane 固定为 Bundesliga，target historical season 至少是一个完整已结束赛季，优先 `2025/2026`；如需 warm-up，则加入紧邻的 `2024/2025`。最终 provider season IDs 和 expected match count 必须由 source evidence 确认，不能仅凭标签推断。标准18队完整赛季通常为306场；延期、取消、缺失或 provider scope 差异必须在 plan 中逐场列出。

首次查询 target result 或计算 metric 前，必须 append-only 保存 `PRODUCTION_QUANT_PILOT_PLAN_V1`。Plan 至少冻结：

```text
plan_id / schema_version / plan_hash / sealed_at_utc
source rights/fact admission IDs and hashes
fixture/result/odds archive IDs and payload hashes
competition and ordered real season IDs
predeclared cohort match IDs and completeness rules
per-slice decision/evaluation cutoffs
per-slice target exclusions
odds snapshot selection policy/version
eligible bookmaker set/rules
market consensus policy = MARKET_CONSENSUS_MEDIAN_V1
de-vig method/version
Elo name/version/config hash
training window policy/version
fusion policy/config hash
metric epsilon and 10-bin calibration definition
implementation code revision
model build recipe ID/hash
NO_PARAMETER_TUNING / NO_ROI_MODEL_SELECTION
```

每个 plan、attempt、run 和 report 都 append-only 保存。`production_quant_pilot_attempts` 对同一 pilot scope 使用连续 attempt sequence，逐项保存 plan/run/report ID/hash、status 和实际时间；`PRODUCTION_QUANT_PILOT_SUMMARY_V1` 保存 attempt count/root。失败或不利结果不能删除、覆盖或从汇总中隐藏。Retrospective outcomes 已经公开，因此 pilot 只构成重放、完整性和可用性证据，不声称独立 out-of-sample validation。

Pilot 使用 probability-only `PRODUCTION_QUANT_PILOT_V1`，不调用 optimizer、Selection、Ticket、Portfolio 或 Settlement，也不以缺少历史 Sporttery 为由伪造输入。它可复用现有 probability/fusion/metric service，但缺少完整投注输入时不得冒充完整 `BACKTEST_V2`。

若没有可信 source-time availability 或完整 point-in-time `THREE_WAY` odds，pilot 停止并报告 `NO_ADMISSIBLE_POINT_IN_TIME_ODDS_ARCHIVE`。Closing odds、未来 snapshot、事后 bookmaker selection 和推测 timestamp 均不得替代。

Pilot report 必须分别给出：

- `P_market` 的 multiclass Brier、LogLoss、10-bin Calibration/ECE、bin counts 和分母。
- 固定 Elo `P_quant` 的同组指标。
- 预先冻结 fusion 下 `P_final` 的同组指标；若为 `QUANT_ONLY_V1`，明确报告 `P_final = P_quant`。
- 全 cohort 的 MODEL availability count/rate、每个 unavailable reason 和 unavailable slices。
- 三种概率共同 available cohort 的配对指标，且不能用 coverage 差异暗示公平排名。
- Source mode、`RETROSPECTIVE_SOURCE_TIME_RESEARCH` banner、plan/source/state/config/report hashes 和 deterministic replay 结果。

Pilot 不报告投注 ROI，也不使用 ROI、概率指标、是否优于 `P_market` 或人工偏好来调参、重选 season/cohort 或决定哪个尝试成为“正式结果”。Pilot 完成后生成 `PRODUCTION_QUANT_PILOT_ATTESTATION_V1`，绑定 plan hash、attempt count/root、source/season/approved-fact roots、terminal state core hash、build recipe hash、code revision 和 summary/report hash。Approval 是 source/integrity/use gate，不是表现达标奖励。

### 7. Training manifest、approval event 与 hash roots

Pilot 完成后生成 immutable `TRAINING_HISTORY_MANIFEST_V1`。Manifest canonical JSON 是 source/season/fact graph 的权威记录；关系 child rows 是同一内容的受约束投影。Approval event 最后插入并重新验证完整 graph，因此不需要对 manifest 使用可变 `BUILDING -> APPROVED` 状态。

建议 additive persistence graph：

```text
source_rights_admissions

training_fact_admissions
├── training_fact_fixture_sources
├── match_season_memberships
└── match_result_admissions

training_history_manifests
├── training_history_fixture_sources
├── training_history_mapping_sources
├── training_history_result_sources
├── training_history_seasons
└── training_history_facts

training_history_approval_events
└── training_history_revocation_events

production_quant_pilot_attempts
└── production_quant_pilot_summaries
    └── production_quant_pilot_attestations

production_quant_model_releases
└── production_quant_model_release_facts

production_target_acceptance_plans
└── analysis_run_target_acceptance_plans

quant_model_states
└── quant_model_state_production_releases
```

`training_history_manifests` 至少保存 schema、source mode/classification、competition、`pilot_target_season_id`、`production_target_season_id`、model/config、`ELO_TRAINING_WINDOW_V1`、history as-of、typed source/season/fact counts and roots、pilot attestation/attempt-root/report hashes、actual created/persisted times、canonical `manifest_json` 和 `manifest_hash`。

三个 typed history-source table 分别引用 fixture source row、mapping/season membership row 和 result admission row，并保存各自连续 sequence、season sequence、provider、archive/admission ID、payload/record/admission hash、source observed/available time、actual local import/register time 和 source rights admission ID/hash。`training_history_facts` 使用三个 typed composite FK 绑定它们；不使用 SQLite 无法强制的 polymorphic `kind + arbitrary ID` 关系。

`training_history_seasons` 保存连续 `season_sequence`、真实 `season_id`、`WARMUP`/`PILOT_TARGET`/`PRODUCTION_TARGET` role、provider season reference、fact count 和 season facts hash。完整历史 pilot season 使用 `PILOT_TARGET`；当前模型最终过渡到的 season 使用 `PRODUCTION_TARGET`，可作为最后一个零 fact season，也可含 cutoff 前已完成的真实比赛。二者不得共用含糊的 target 字段，不能把 warm-up 或 pilot facts 重标成 production target season。

`training_history_facts` 每行至少保存：

```text
fact_sequence / season_sequence / actual season_id
competition_id / internal_match_id / match_result_id
home_team_id / away_team_id
fixture_source_sequence / mapping_source_sequence / result_source_sequence
fixture record hash / mapping membership hash / result admission hash
provider fixture key / provider result key / provider_mapping_id
normalized_status = REGULAR_TIME_FINAL / provider_finalized_at_utc
kickoff_at_utc / result_observed_at_utc
fixture_source_available_at_utc / mapping_source_available_at_utc
result_source_available_at_utc / effective_source_available_at_utc
source_time_ingested_at_utc / local_imported_at_utc / registered_at_utc
regular-time home_goals / away_goals
source_payload_hash / elo_fact_hash / approved_fact_hash
supersedes_match_result_id
```

现有 Elo `fact_hash` / `training_data_hash` 继续只封存数学输入并保持兼容。新增 `approved_fact_hash` 覆盖上述全部 approval provenance，`approved_facts_hash` 覆盖 ordered `(fact_sequence, approved_fact_hash)`；manifest 同时保存 `approved_facts_hash` 和 `training_data_hash`，二者不得互相替代。

所有 hash 使用带 schema domain tag 的 UTF-8 canonical JSON，例如：

```text
SHA256("APPROVED_TRAINING_FACT_V1\0" + canonical_json(fact_without_hash))
SHA256("TRAINING_HISTORY_MANIFEST_V1\0" + canonical_json(manifest_payload))
```

每个 artifact 必须定义独立 schema tag 和 `content_payload`。Payload 排除该 artifact 自身的 ID、content hash、serialized JSON 副本和 signature bytes，但包括所有业务时间、parent content hashes、counts、roots 和 supersession refs；ID 由 `(schema_version, content_hash)` 稳定派生。数据库 FK 可以存在于 relational projection，但 child source/season/fact hash payload 明确排除尚未生成的 parent manifest ID，改为绑定 pilot scope、source/admission hashes 和自身 sequence。这样 source/season/fact roots 可先生成，manifest 再包含这些 roots，不形成 `manifest_hash -> manifest_id -> child_hash` 循环。

Manifest payload 明确排除 `training_history_manifest_id`、`manifest_json` 和 `manifest_hash`。Plan、attempt、summary、attestation、manifest、release、revocation、target plan 和 audit sidecar 都必须在各自 schema 中列出同样的 include/exclude envelope，禁止依赖实现默认序列化。

`TRAINING_HISTORY_APPROVAL_V1` 先计算包含 manifest hash、pilot attestation/attempt root、source rights admission、approver、authority、effective/expiry time、actual approved/persisted time 和 supersession lineage 的 `approval_payload_hash`。Approver 对该 payload hash 签名；`training_history_approval_hash` 再覆盖 payload hash、signature algorithm、signer key ID 和 signature evidence SHA-256。Signature bytes、approval ID 和 final hash 不进入被签 payload。Approval、manifest 或 build recipe/code revision 与 pilot attestation 不完全一致时禁止 activation；任何 covered input、correction 或 implementation 变化都需要新 plan/attempt/attestation，再创建新 manifest/approval/release。

### 8. Model release 与持久化桥

`APPROVED_TRAINING_HISTORY` activation 是 append-only approval event，不是 manifest 上的可变 flag。Production build 必须 pin exact approval event，重新验证 source graph 后创建 `PRODUCTION_QUANT_MODEL_RELEASE_V1`：

```text
production_model_release_id / release_hash
training_history_approval_id / training_history_approval_hash
training_history_manifest_id / manifest_hash
source_data_mode = SOURCE_TIME_RESEARCH
model_name / model_version / config_hash
production_target_season_id / training_window_hash
training_data_hash / approved_facts_hash
released_state_core_json / released_state_core_hash
ordered release fact refs
pilot attestation ID/hash / build recipe ID/hash
implementation code revision
build_started_at_utc / build_completed_at_utc / persisted_at_utc
```

Build operation 使用 mode-homogeneous research provider；它不是 live AnalysisRun。`released_state_core_json` 是 cutoff-independent canonical payload，完整封存 model identity/config、production target season、ratings、prior-match counts、ordered training facts、training data hash 和 approved facts hash，但排除 release/run ID、run cutoff、generated time 和 run-scoped state hash。

Live AnalysisRun pin 一个在 decision cutoff 前已持久化的 exact model release，只从 released core 的 ordered facts 确定性重建当前 cutoff 的 run-scoped `EloBaselineState` 和 `QuantModelStateArtifact`。规范投影只允许加入 run ID、`cutoff_at_utc=AnalysisRun.as_of_at_utc`、run 内 generated time 及由完整 payload 重算的 state/artifact IDs/hashes；移除这些 run-scoped 字段后重算的 core hash 必须等于 `released_state_core_hash`。因此新 cutoff 可以产生新的 state hash，但 ratings、counts、facts、season、config 和 training hash 不能漂移。Live 过程不查询 research archive、不联网补 training facts，也不选择“最新 release”。

`TRAINING_FACT_ADMISSION_V1` transaction 必须先物化现有 `quant_model_training_facts.match_result_id` FK 所需的 normalized MatchResult，同时写入不可分离的 source-mode/admission 关系。Production release builder 只能从这些关系和 active approval 读取，不能查询裸 MatchResult。Release 必须满足 `build_completed_at_utc <= persisted_at_utc < production decision_as_of_at_utc`，live run 必须 pin 该预先存在的 row。AnalysisRun completion transaction 要求每个含 approved retrospective facts 的 state 恰好绑定一个 `quant_model_state_production_releases` row，并逐 fact 对齐 release sequence、season、result ID、`elo_fact_hash`、training data hash、approved facts hash 和 released state core hash。

Release replay 需要 exact training manifest、normalized archives/admissions、Elo config、training window 和 implementation revision。完整 production prediction replay 还必须 pin target AnalysisRun input manifest、code revision、current source snapshots 和 model release；仅有 training archives 不足以声称重放了 prediction。

### 9. 真正的多赛季 training provider

实现阶段新增只用于 build/pilot 的 `ApprovedTrainingHistoryEloProvider`，并把 archive research path 扩展为真正的 multi-season provider。二者共享 per-fact season projection：

```text
explicit season membership
-> exact fixture + mapping + result admission
-> EloRegularTimeResult(season_id = that match's actual season_id)
-> canonical Elo order
```

禁止继续调用把一个 constructor `season_id` 传给全部 `_elo_training_source(...)` 的方式。兼容单赛季调用时，也必须建立显式 membership 并逐场投影。Query 的 `target_season_id` 只控制最终 season transition，不能覆盖历史事实。

Provider 必须校验 competition、ordered seasons、model/config、source mode、manifest/hash、correction heads 和 target exclusion；逐项要求 fixture、mapping/season 和 result source availability 早于当前 slice cutoff，并以三者最大值形成 effective availability；按 `kickoff_at_utc`、effective source availability、source-time `ingested_at_utc`、match ID、result ID 确定性排序；要求 season 形成声明顺序一致的连续时间块；不读取 odds、Sporttery、table state 或普通未审批 archive。

### 10. Active approval/release、correction 与 revocation

首次 model build 只检查 `approval_active_for_build(t)`，不要求尚未创建的 release：

```text
approval persisted_at <= approval effective_at <= t < expires_at (when present)
source rights admission and production-use grant cover exact source/model/scope at t
no revocation with recorded_at <= t and effective_at <= t
no same-scope successor with persisted_at <= effective_at <= t
manifest/source/approval hashes still validate
pilot attestation, terminal attempt count/root, build recipe and code revision match
no attempt exists after the terminal attestation for the same pilot series
no newer fixture/mapping/season/status/result correction is both source-visible and locally registered by t
```

数据库在 terminal attestation 插入后禁止向同一 `pilot_series_id` 增加 attempt。任何后续尝试必须创建新 series/plan/summary/attestation，旧 approval 不得引用它。每次 build 还要重算截至 `t` 的连续 attempt count/root，并与 summary、attestation 和 approval 相等。

AnalysisRun 及后续 artifact 使用更强的 `release_active_for_operation(t)`：

```text
approval_active_for_build(t)
model release build_completed_at <= release persisted_at < t
release hash and released state core hash validate
target acceptance plan was sealed before evaluation and pins this exact release
```

Superseding approval 必须具有相同 competition/model/config/training-window scope，每个 approval 最多一个直接 successor，禁止 fork。Successor 必须满足 `persisted_at <= effective_at`；只有 persisted/effective 均不晚于 operation time 才影响该 operation，禁止后来写入的 backdated successor 追溯改变旧判断。Correction 是否影响 operation 同时取决于其 source visibility 和实际 local registration；后来下载但携带旧 source timestamp 的 correction 不得追溯改写已封存 run，却会使其 registration 后的新 build/run fail closed，直到新 pilot attestation/manifest/approval/release 完成。

Revocation event 保存 event ID/hash、approval/release ID、actor、authority、reason、recorded/effective time。Model build 在 start/completion 检查 approval predicate；AnalysisRun、packet export、review import、FusionRun 和 PortfolioRevision 在各自 start/completion 检查 release predicate。中途生效的 revocation 使尚未提交的 operation 失败。已经完成的 immutable artifact 不删除、不改写，但禁止其后继续派生新 artifact。

### 11. 保持 V3 wire contract，强制 approval audit bundle

`MVP_INPUT_MANIFEST_V3`、`ANALYSIS_PACKET_V3`、`LLM_REVIEW_V3` 和 `OFFLINE_REVIEW_VALIDATOR_V3` wire shape/bytes 保持不变，不新增 V4。实现前先为 `0.5.0` V3 建立 canonical-byte golden regression。

V3 已公开 state/config/training hashes 和 ordered training match/result IDs，但 V3 单独存在不能证明 production approval。每个 approved-history run 必须生成 append-only、hash-sealed `APPROVED_TRAINING_HISTORY_AUDIT_V1` sidecar：

```text
analysis_run_id / input_manifest_hash
target_acceptance_plan_id / target_acceptance_plan_hash
decision_data_mode = LIVE_STRICT
model_training_source_mode = SOURCE_TIME_RESEARCH
model_training_use_class = APPROVED_TRAINING_HISTORY
retrospective_source_facts_present = true
packet_id / packet_hash
quant_model_state_id / state_hash / state_payload_hash / training_data_hash
production_model_release_id / release_hash / released_state_core_hash
training_history_approval_id / training_history_approval_hash
training_history_manifest_id / manifest_hash / approved_facts_hash
pilot_attestation_id / pilot_attempt_root
ordered season/source summaries
audit_artifact_id / generated_at_utc / audit_hash
```

Packet 与 sidecar 通过一个完整性校验 bundle 导出和保存。网页 GPT 仍只接收正式 `ANALYSIS_PACKET_V3`；sidecar 不嵌入 packet、不改变 packet hash，也不由 V3 review validator 当作额外字段消费。本地 packet export、review import、FusionRun 和 PortfolioRevision 必须通过独立 sidecar/state/release/approval validator；缺失、stale 或 hash 不一致时禁止创建新 artifact。

### 12. 当前真实比赛 acceptance

Pilot、source/rights review、approval 和 model release 全部完成后，选择一个新的未来 Bundesliga target。不得重跑或改写 `0.5.0` 已封存的 Leverkusen 对 Union Berlin acceptance。`PRODUCTION_TARGET_ACCEPTANCE_PLAN_V1` 必须在任何 candidate model evaluation、packet export 或 review 前持久化，按 kickoff window、输入完整性和 minimum-prior-match 条件冻结 exact target IDs、decision cutoff、release ID/hash 和 selection rule，并由 AnalysisRun/audit sidecar 绑定其 hash。不得根据预测方向、EV 或结果挑选。

Current acceptance 必须满足：

- Model release persisted time、target plan sealed time 和 approval 的实际时间严格早于 target decision cutoff。
- 当前 target fixture、market odds 和 reviewed Sporttery 均为 cutoff 前真实取得的 `LIVE_STRICT` 输入。
- Target 不在 release training facts；两队满足固定 `minimum_prior_matches=5`，否则保持 `MODEL_UNAVAILABLE`。
- `ELO_THREE_WAY_BASELINE_V1` 至少一场 evaluation 为 `AVAILABLE`，并产生 model `P_quant` 和 base `P_final`。
- 导出新的 `ANALYSIS_PACKET_V3` 与 mandatory audit sidecar；网页 GPT 返回至少一场 `VALID` 的绝对 `P_llm` review。
- 本地完成 validate/import、FusionRun 和 PortfolioRevision。`NO_BET` 是合法结果，不要求 Ticket 或正 EV。

不得为完成 handshake 伪造概率、赔率、EV、Selection、Ticket、Portfolio 或历史 provenance。

## 分阶段门禁

本次提交只允许 Architecture/ADR proposal。ADR 仍为 `Proposed` 时禁止新增 migration、provider、CLI、真实 adapter 或版本号变更。

外部架构审查通过后，`0.6` 实现顺序为：

1. V3 golden freeze、source/rights/status/season admission contracts；
2. additive schema、append-only triggers、materialization bridge 和 repository integrity；
3. multi-season research/build provider；
4. precommitted Bundesliga point-in-time probability pilot；
5. training manifest、approval event、model release 和 deterministic replay；
6. 新未来比赛的 AVAILABLE V3 Review -> FusionRun -> PortfolioRevision handshake。

完成第6项前不得进入 `0.7`。

## 非目标

- 不新增或修改 Strategy Profile。
- 正式市场仍仅为 `THREE_WAY`；不实现任何新 market，包括让球、比分、进球数、半全场或未来新增类型。
- 不实现3串4、4串11、复式或新 optimizer。
- 不改变 Elo 参数，不进行自动或人工调参，不训练机器学习模型。
- 不用 ROI、命中率、概率指标、GPT opinion 或单场结果反向选择模型。
- 不修改 V1/V2/V3 packet bytes，不增加 V4。
- 不连接真实 LLM API，不自动下注、登录、支付或出票。

## 结果

- 回溯数据可在保留真实来源和实际 possession time 的前提下，构建受控、可撤销的当前 production model release。
- `LIVE_STRICT` 继续只表示系统当时真实取得；approval 不能制造历史持有事实。
- Research provider 与 live AnalysisRun 保持隔离；普通 `SOURCE_TIME_RESEARCH` 永远不能直接进入 live。
- 多赛季 Elo state 可审计每场真实 season，并继续使用完全冻结的 `.75` season regression。
- Pilot、approval、model build、current run 和 V3 review 各有独立、append-only、hash-sealed evidence chain。

## 被否决方案

- 将历史归档重标为 `LIVE_STRICT`：虚构历史持有事实，违反 ADR-0007。
- 把 `APPROVED_TRAINING_HISTORY` 增加为第三种 `HistoricalDataMode`：混淆 source truth 与 use authorization。
- 允许 approved 或普通 `SOURCE_TIME_RESEARCH` provider 直接进入 live AnalysisRun：破坏 provider/run mode 隔离。
- 继续用 target season 覆盖全部 training facts：破坏真实 season lineage 和固定 season regression。
- 只保存 opaque Elo state：无法证明事实、season、correction、rights 和 source provenance。
- 只使用现有 `training_data_hash`：它不覆盖 approval-only provenance。
- 修改 `ANALYSIS_PACKET_V3` 或新增 V4：本阶段无必要，并会破坏已接受合同。
- 根据 pilot metric 或投注 ROI 人工/自动调参：把验收变成 in-sample optimization，不符合 frozen baseline。
