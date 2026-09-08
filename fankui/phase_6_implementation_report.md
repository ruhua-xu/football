# Phase 6 Production Quant 本地契约与集成实施报告

## 结论与范围

**真实生产运行状态：等待来源材料与权限确认，尚未完成真实 0.6 验收。** 本次已按外部裁决实现 V2 审批正式记录路径、受控 correction、显式 season/correction 上下文及其 pilot/release/live/audit 集成。完成代码和隔离测试不等于获得真实生产授权、完成 Bundesliga pilot 或形成模型效果证据；没有生成真实 AVAILABLE Packet 或替代网页 Review。

本次基线为 `c9083e59a5fd3ad3a2528b5d4997b5cbcabcc415`；该提交保留且其 CI 已由外部裁决确认成功。继续使用 `feature/0.6.0-production-quant-bootstrap`，不合并 main、不发布版本；package 仍为 `0.5.0`。报告保留前批门禁记录，并单列本次结果。提交 SHA 与 CI 状态在交付消息中列明，避免报告自引用。

本次实现提交为 `970fb2d76c8959927e8ef4efdedf307761ecb519`（`feat: add approval v2 and controlled history corrections`）；报告单独提交。代码、测试、迁移和已授权 ADR 修订共 53 个文件，`+16218 / -456`，不含本报告的独立文档更新。

- `src/football_system/interfaces/production_quant_cli.py` 提供 22 个本地命令及共享 `production_inference_context`，新增 approval-prepare 和四个 correction 命令。
- `interfaces/cli.py` 已由主代理添加 production-quant dispatch、帮助入口及 `_run_live_analysis` 的成组 release/target-plan pins；本次续作未编辑该文件。
- `tests/e2e/test_production_quant_cli.py` 覆盖入口、读取顺序、文件边界、SQLite、synthetic pilot 和完整 audited downstream 接线；新增 `tests/e2e/test_production_live_cli.py` 覆盖 live pins 依赖、精确 preparation/plan 绑定和禁止 training-provider fallback。
- 运行时不访问 provider 付费 API、不提供假输入生成器、不自动创建 authority/来源许可或 reviewer 审核，不补造历史时间戳。新审批测试经正式 approval-record 写入后验证下游；旧 seeded V1 approval 仅保留作兼容回归。所有测试 review/pilot bridge 均为明确标注的隔离测试材料，不是现实授权。

## 实现链路

| 层/模块 | 已实现代码与集成边界 |
| --- | --- |
| `domain/training_admission.py`、`application/training_admission.py` | 来源使用许可、常规时间结果、fixture/season/mapping 关联、候选封印及 source-time 事实契约 |
| `LocalTrainingEvidence`、`training_admission_repository` | 受控本地文件/hash、授权 reviewer 与 trusted pins 检查；rights 先于 provider bytes；实际 import/registration/admission 时钟；既有身份精确复用、原子 admission、immutable retry、关系投影复核 |
| `domain/quant_integrity.py`、`QuantIntegrityPilotService`、`quant_integrity_repository` | 固定 Elo/no tuning；先封存完整 cohort/计划与 reservation，再读结果；确定性计算/replay、失败 attempt 留存、完整 attempt root、terminal attestation；synthetic 不得供给生产技术证据 |
| `domain/production_release.py`、`application/production_release.py`、`production_quant_repository` | V2 精确审核 subject 与独立记录事件；正式 approval-prepare/record、原 attestation 核验、幂等与 replay 防护、实际起止权限和独立 build/inference/retention；V1 解析/hash 保留 |
| `training_correction_repository`、`training_correction.py`、`versioned_training_history.py` | 实际前序与 capture/review 核验；同一比赛 anchor 的 fixture/mapping/season/status/result 完整版本；撤回不制造 normalized result，恢复需新证据/审核；context 与逐 cutoff selected-head roots 分离 |
| `HistoricalArchiveEloTrainingProvider` | 必须提供已验证的逐场 membership/context；constructor season 仅作断言，target season 仅控制最后 transition；旧文件与旧运行不重写 |
| `ports/production_inference.py`、`production_inference_repository`、`RunModelAnalysisService` | 从显式 release 和 target plan 投影既定状态；不调用 research training provider；保存 run/release/plan/state/facts 绑定，写入与重试重新验证当前授权和实际时钟 |
| `production_audit_repository`、review/post-review repositories | 将 approved run 的原 V3 packet 与 `APPROVED_TRAINING_HISTORY_AUDIT_V1` sidecar 原子绑定；packet、review import、fusion、portfolio 及 cached/direct 路径均要求实际 audit 依赖和当前授权 |
| `production_audit_bundle.py` | 原 V3 packet 字节不改版；以三文件 bundle 交换审计 sidecar，校验大小、hash、类型、绑定和安全内容，并在发布前通过本地授权门禁 |

前批 migration 到 `c2ebf618d354`。本次追加 `d3fc0729e465`（approval V2 guards）、`e40d183af576`（受控 correction）和 `f51e294b0687`（typed versioned quant history）。旧 V1 approval 的 JSON/request/hash、旧 normalized 行和旧运行保留；新字段和行为不通过改写旧工件实现。升级与相应空图 downgrade、populated refusal、runtime parity 由测试覆盖。

`recorded_at_utc`、`local_imported_at_utc`、`archive_created_at_utc`、`registered_at_utc`、admission/build/inference/audit 的实际开始、完成和持久化观察时间来自操作时钟，而不是调用者预测的未来提交时间。source available/observed/finalized 时间保持其原始证据语义，不与本地持有时间混用。相同 immutable request 重试保留原工件时间，但仍执行对应当前授权/完整性门禁。

共享 context 以 `clock=None` 为默认，在调用时解析当前 `utc_now`，将同一个时钟依赖传给 admission、pilot、production、inference、audit 仓库；测试可显式注入受控时钟，CLI 没有伪造操作时钟的选项。

## 真实来源核验

| 分类 | 具体结论 | 恢复条件 |
| --- | --- | --- |
| `MATERIAL_NOT_PROVIDED` | 未发现新提供的已结束 Bundesliga 赛季小样本、对应历史时间证据或完整 cohort 包；已有的是赛前 response | 先提供权利允许的最小 completed fixture 原始样本与 field dictionary，验证后再扩大 |
| `RIGHTS_UNCONFIRMED` | Sportmonks 公共条款有允许保存/使用数据的正面描述；但尚未提供适用于本账号/导出、历史赛季和 retention 的授权 reviewer 决定 | 提供准确 terms/version/hash、authority 与逐项 research/storage 权限及保留/删除规则，不需要提供 Key |
| `FIELD_MISSING` | 已有 Bundesliga 赛前 response 没有 scores/periods/timeline/finalization/publication 字段；status 存在且为 NS，并非 status 字段缺失 | 取得已授权 finished 小样本核对实际字段及其来源定义，不推断该 provider 永久没有相关能力 |
| `HISTORICAL_TIME_UNPROVABLE` | 现有证据不能将某个 finished/corrected result version 绑定到历史 finalized/observed/available 时间 | 需要真实版本/发布时间或当时已归档观察证明；现在 receipt、kickoff、period ending 和文件 mtime 不能替代 |
| 真实 production review | V2 写入口已解除固定合同阻断，但没有真实 manifest/pilot 与授权 review，仍不能成功生成真实审批 | 先完成来源准入和 pilot，再由真正获授权 reviewer 审核精确 V2 payload |

已有 ignored Scottish 228 条结果使用 `kickoff + 2h` 作为 observed 时间，不是 provider source/finalization 的原始时间证据，且不是所要求的 Bundesliga 历史。不得修改这些旧数据、补造时间戳、重新标记用途或将其用于本 pilot。既有 `0.5` acceptance 与其 `MODEL_UNAVAILABLE` 事实保持不变。

本次没有真实 Log Loss、Brier、ECE、命中率、ROI 或可用性指标可报告，没有生成真实 `AVAILABLE` packet、生产 P_quant、投注或收益声明。测试内计算值只验证契约，不是现实表现或授权依据。

### 最小样本字段与证据

只读核验引用本地已归档的 `data/raw/SPORTMONKS/2026-09-05/e939cef320abe6176ba501e912cb6cacd6a4108f6512008da88eb7c2416ca74f.raw` 与同名 metadata。原始 bytes 留在本地；本报告仅列字段位置、语义和缺口，不复制原始记录。校验的 payload SHA-256 为 `8ceeb95dd39e5bc4ea93e8cf9f6ce7fc149c99a21f826ce549a120eb22b57bc7`，只证明与归档 metadata 一致，不证明数据权利或历史发布时间。

| 合同事实 | 本地原始字段/位置 | 证据判断 |
| --- | --- | --- |
| Fixture/competition | `/data/*/id`, `/league_id`, `/league/id`, `/league/country/iso2` | 存在明确 fixture 与 Bundesliga/DE 关系，仅为该次赛前样本 |
| 实际 season membership | `/data/*/season_id`, `/season/id`, `/season/league_id`, `/season/finished` | 关系存在，但指向当前未结束赛季；不是 pilot 的已结束赛季证据，不能由名称推导历史 season ID |
| Home/away | `/data/*/participants/*/id` 与 `/meta/location` | 必须按 location 关联，不按数组顺序；仍需与注册 aliases/canonical identities 核对 |
| Kickoff | `/starting_at`, `/starting_at_timestamp` 与 response `/timezone` | 可证明该 fixture kickoff；不是结果观察或完成时间 |
| 完成状态 | `/state_id`, `/state/state`, `/state/developer_name` | 样本为 NS；需 finished 样本及终态语义，FT 可能在加时前短暂出现，不仅凭 generic FINISHED 准入 |
| 常规时间比分 | `scores[].description/type_id/participant_id/score.goals` | 样本未包含；文档中的 cumulative 2ND_HALF 与 2ND_HALF_ONLY 不同，CURRENT 可能含加时，需实际样本逐字段核对 |
| Finalized time | 未找到本样本等价字段 | 文档 `periods[].ended` 是 period ending event，不自动等同 provider 完成处理时间 |
| Source observed/available | 无已完成历史赛果字段；metadata receipt 是本地接收观察 | 不能倒推为赛季当时的 result publication/availability |
| Correction version | 样本无版本发布时间链 | 需要原始 revision ID/order 与 source-time 语义；不能用后补 period/end 值证明曾经可见 |

只读查阅的公开资料包括 [Sportmonks fixture entity](https://docs.sportmonks.com/v3/endpoints-and-entities/entities/fixture.md)、[scores](https://docs.sportmonks.com/v3/tutorials-and-guides/tutorials/includes/scores.md)、[states](https://docs.sportmonks.com/v3/definitions/states.md)、[periods](https://docs.sportmonks.com/v3/tutorials-and-guides/tutorials/includes/periods.md)、[latest-updated fixtures](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/fixtures/get-latest-updated-fixtures.md)、[changelog](https://docs.sportmonks.com/v3/changelog/changelog.md) 和 [terms](https://www.sportmonks.com/terms-of-service/)。10 秒 latest-updated feed 不是历史版本 ledger；历史 period 数据可能 backfill，`last_processed_at` 已在旧 changelog 中移除。这些是字段解释线索，不是对账户权限或缺失原始样本的替代证明。

本轮没有账号/付费 API 请求或新数据采集。原生 Sportmonks numeric IDs、UNIX timestamps、participant/score 数组需要经核验的 provider adapter 转换；当前通用 verifier 的显式字符串/ISO 时间字段要求不能通过补造缺失值满足。Pilot 元数据与 result 共用同一 capture、相同 payload 或路径别名的文档会在打开 result-bearing bytes 前被拒绝，需先有独立 metadata 证据包。该限制是当前适配边界，不是 provider 不合格的结论。历史赔率缺失仅令独立 benchmark unavailable，不阻断合格 Quant integrity pilot。

## 命令入口

安装包入口为 `football-system`。未安装包的源码工作区可先设置 `$env:PYTHONPATH = "src"`，并将下文入口替换为 `python -m football_system.interfaces.cli`。

所有操作使用同一签名；大写部分为占位参数，不是随仓库提供的数据文件：

```text
football-system production-quant COMMAND --request FILE --database-url SQLITE_URL --evidence-root DIRECTORY --authority-pins FILE --operator ID [--output NEW_FILE]
```

`--database-url`、`--evidence-root`、`--authority-pins`、`--operator` 均须显式给出，不读取 provider key 或隐式训练配置。路径相对于当前工作目录；数据库仅接受本地文件型 `sqlite:///...` 或 `sqlite+pysqlite:///...`，不接受内存库、远程后端或用户提供的 SQLite URI/query 选项。数据库父目录、证据根和输出父目录必须已存在。

### 精确 Schema 发现

```text
football-system production-quant --help
football-system production-quant --print-schema
football-system production-quant rights-record --print-schema
football-system production-quant approval-prepare --print-schema
football-system production-quant approval-record --print-schema
football-system production-quant correction-reference --print-schema
football-system production-quant correction-prepare --print-schema
football-system production-quant correction-admit --print-schema
football-system production-quant correction-context --print-schema
football-system production-quant pilot-plan --print-schema
football-system production-quant inspect --print-schema
football-system production-quant bundle-export --print-schema
football-system production-quant review-import --print-schema
football-system production-quant fusion-create --print-schema
football-system production-quant portfolio-revise --print-schema
football-system production-quant --print-schema --type AuthorityPinsV1
football-system production-quant --print-schema --type ReviewerAuthorityV1
football-system production-quant --print-schema --type TrainingJsonAdapterV1
football-system production-quant --print-schema --type QuantIntegrityScopeDocumentV1
football-system production-quant --print-schema --type QuantIntegrityScheduleDocumentV1
football-system production-quant --print-schema --type QuantIntegrityBuildRecipeV1
football-system production-quant --print-schema --type QuantIntegrityReviewDocumentV1
football-system production-quant --print-schema --type ApprovedTrainingHistoryAuditV1
football-system production-quant --print-schema --type LLMReviewSubmissionV3
football-system production-quant --print-schema --type AppSettings
```

每个操作都支持 `COMMAND --help` 与 `COMMAND --print-schema`。不带命令的 `--print-schema` 返回完整命令到请求类型的映射、所有可发现类型、字节上限和阻断命令列表。`--type` 只与 `--print-schema` 配合使用。帮助/schema 查询不打开数据库、不迁移、不读取请求或证据文件，也不生成文件；此时无需操作上下文参数，`--output` 不用于 schema 写出。

输出 schema 直接来自实际 Pydantic 请求模型，包括 `$defs`、必填字段、枚举及未知字段拒绝规则，不维护另一份手写 JSON 模板。哈希封印、时序、完整性和数据库关联约束仍由相同模型验证器和仓库检查；JSON Schema 本身不等于通过审核。请求模型会重新验证 `model_copy`/`model_construct` 形成的嵌套对象，不能靠已有 Python 对象绕过封印检查。

### 请求与行为

| COMMAND | 请求类型与顶层字段 | 行为 |
| --- | --- | --- |
| `rights-record` | `RightsRecordRequestV1`：`request_key`, `rights_payload`, `reviewer_attestation` | 核验已有条款、trusted pins 和授权 review 后记录许可；不是生成许可 |
| `capture` | `CaptureRequestV1`：`request_key`, `source_rights_admission_id`, `source_id`, `provider_code`, `evidence_reference` | CLI 只预检路径语法；仓库核验持久化 rights 后才读取本地 JSON，记录实际 `LOCAL_FILE_IMPORT` receipt；不声称上游获取时间 |
| `admit` | `AdmitRequestV1`：`request_key`, `source_rights_admission_id`, `submissions` | 每项为 `TrainingFactSubmissionV1`，核对候选、capture、adapter、review 与已存在身份，原子记录事实 |
| `pilot-plan` | `PilotPlanRequest`：`definition` | 原 V1 请求保留；含 corrections 使用明确的 `QUANT_INTEGRITY_PLAN_DEFINITION_V2`、typed context pin 和 V2 targets，不靠出现某字段猜版本 |
| `pilot-run` | `PilotReferenceRequestV1`：`plan_ref` | 先持久化 reservation，再通过现有服务执行离线概率完整性计算与 replay；失败 attempt 保留 |
| `pilot-attest` | `PilotReferenceRequestV1`：`plan_ref` | 汇总全部 attempt，绑定最后成功报告并终结 series；不授予生产许可 |
| `manifest` | 原 V1 字段保留；V2 显式 `PRODUCTION_QUANT_MANIFEST_REQUEST_V2` 增加 `correction_context`, `context_registered_at_utc`, `selection_cutoff_at_utc`, `exclude_match_ids` | 验证 terminal selected graph 与完整 predecessor context；不接受 synthetic pilot 作为现实生产证据 |
| `approval-prepare` | `ApprovalPrepareRequestV2`：manifest、四项 grants、approver、authority、retention compatibility、可选显式 supersession | 只读返回不含实际记录时间的精确 V2 review payload；不生成审核 |
| `approval-record` | `ApprovalRecordRequestV2`：`request_key`, `approval_payload`, `reviewer_attestation` | 核对原始审核文件、authority 和精确 V2 hash，再原子记录；拒绝旧 V1 写请求、缺证据和旧审核 replay，不自动转换 |
| `correction-reference` | `receipt_id`, `record_pointer` | 审计已存在 capture，返回完整 receipt/record hashes；不新采集、不生成审核 |
| `correction-prepare` | predecessor version、rights、`CorrectionEvidenceV2`、新 result ID 或 `null` | 验证真实前序与字段，生成 time-free intent；withdrawal 的 result ID 必须为 null |
| `correction-admit` | `request_key`, `intent`, `reviewer_evidence`, `reviewer_authority` | 外部审核 intent hash 后，通过受控事务写入完整版本/normalized successor 或 withdrawal，无部分图 |
| `correction-context` | `base_admissions`, `correction_ids`, `source_cutoffs` | 实际操作时钟核对上下文，返回 reusable pin 和逐 source cutoff 的完整 head；withdrawal 不回退 |
| `release-build` | `ReleaseBuildRequestV1`：`request_key`, `approval_id`, `training_cutoff_at_utc`, `state_retention_horizon`, `audit_retention_horizon` | 仅从已存在且可完整验证的审批构建；缺少真实审批时不能运行成功 |
| `target-plan` | `TargetPlanRequestV1`：`request_key`, `release_id`, `targets`, `kickoff_window_start_at_utc`, `kickoff_window_end_at_utc`, `decision_as_of_at_utc`, `selection_rule` | 按固定规则和已存在 canonical fixture 封存目标；不补建 fixture 或自动挑选有利目标 |
| `revoke-prepare` | `RevokePrepareRequestV1`：`approval_id`, `release_id`, `affected_grants`, `actor`, `reviewer_authority`, `reason`, 可选 `effective_at_utc` | 只读生成精确撤销意图与 reviewer hash，不产生审核或撤销事件 |
| `revoke` | `RevokeRequestV1`：`request_key`, `revocation_request`, `review` | 核验授权审核与已存审批/发布范围后 append-only 撤销 |
| `inspect` | `InspectRequestV1`：`target`，按 `kind` 判别 | 仅通过现有仓库 loader 检查并返回契约；不提供任意 SQL/table/raw dump |
| `bundle-export` | `BundleExportRequestV1`：`analysis_run_id`, `bundle_directory` | 从本地 approved run 原子保存 V3 packet/audit companion，再经当前授权门禁发布新目录 |
| `review-import` | `ReviewImportRequestV1`：`bundle_directory`, `review_file` | 数据库前验证 bundle/review bytes 和精确 packet/context/match 绑定，再经本地 audit/current grant 门禁导入；不安装外来 approval |
| `fusion-create` | `FusionCreateRequestV1`：`review_artifact_id`, `config_file` | 预检显式 TOML，经具体 audited repository 创建/复核 FusionRun |
| `portfolio-revise` | `PortfolioReviseRequestV1`：`fusion_run_id`, `config_file` | 预检显式 TOML，经同一 audit 依赖创建/复核 PortfolioRevision，不绕过 base prediction 门禁 |

`inspect.target` 的可用结构如下；完整模型以 schema 为准：

- `kind` 为 `rights`、`capture`、`admission`、`manifest`、`approval`、`release` 或 `target-plan` 时，提供 `artifact_id`。
- `kind` 为 `pilot-plan` 或 `pilot-report` 时，提供完整 `reference`，包含原 schema、artifact ID 和 content hash。
- `kind` 为 `pilot-attempts` 时，提供 `series_id`，返回全部已完成 attempt，包括失败项；空列表不证明不存在 pending reservation。
- 本版无独立 `inspect pilot-attestation` 或 `inspect revocation` loader；terminal/revocation 契约保留在对应命令的本地输出，相关 attempt/report 可单独检查。`inspect` 的验证程度以所调用仓库方法为准，不是当前生产推理授权检查。

### 串联与重放

成功结果为 canonical `PRODUCTION_QUANT_CLI_RESULT_V1` JSON。契约位于 `result`；`reference`（如适用）可直接作为后续请求中的 ref。`pilot-plan` 另外返回 `integrity_pilot_scope_id` 供 manifest 绑定。pilot plan 的原始 schema 为 `PRODUCTION_QUANT_INTEGRITY_PILOT_PLAN_V1`，不是另造的短名称。

`revoke-prepare` 返回 `result` 与 `review_subject`，后者含精确 schema 和 payload hash。被 pins 所指 authority 授权的 reviewer 审核该对象后，`revoke` 请求以原 `result` 作为 `revocation_request`，并提供实际已审核文件的 `review` reference/hash。CLI 不从该意图创建 review 文件，也不替换 reviewer 身份。

带 `request_key` 的仓库写操作遵守原有 immutable retry：相同操作内容返回原 ID/时间，复用 key 但修改内容会拒绝。`pilot-plan`、`pilot-run`、`pilot-attest` 使用实际时钟，不承诺重复 CLI 调用幂等；特别是 `pilot-run` 每次是新 attempt，不得靠换 series、重跑或丢弃失败隐藏历史。终结后拒绝后续 attempt。异常留下的 pending reservation 需按仓库既有受控 recovery 接口处理，本版不提供自动清除/恢复 CLI。

命令 JSON 不是伪输入模板。来源数据、审核和实际 capture IDs/times 必须先存在；需要封印对象时使用 schema 所对应模型的既有 `freeze`/校验 API，不推测 provider 字段、补造 source times 或代签 reviewer。

## Live Pins 与 Downstream

Live 命令保留现有 preparation 选择和预算选项，生产模式须同时提供下面五个参数，缺一即在数据库前拒绝：

```text
football-system live run-analysis --config FILE --database-url SQLITE_URL --preparation-id ID --budget YUAN [YUAN ...] --analysis-run-id ID --production-model-release-id ID --production-target-acceptance-plan-id ID --evidence-root DIRECTORY --authority-pins FILE --operator ID
```

仍可用 `--date YYYY-MM-DD` 替代 `--preparation-id`，但只能解析到唯一 ready preparation；命令不会猜测目标 plan。生产 request 的 decision/kickoff window、比赛集合、competition/season、preparation ID、fixture observation IDs 必须与选中的已持久化 preparation 和 target plan 精确一致。release 和 target plan 必须配对，不能用另一个 cutoff/目标范围的 plan 替换。生产路径不接受调用者提供的 `execution_time_utc` 来回填 run 时间；使用实际操作时钟。

生产推理明确披露 `decision_data_mode=LIVE_STRICT`、`model_training_source_mode=SOURCE_TIME_RESEARCH`、`model_training_use_class=APPROVED_TRAINING_HISTORY` 和 retrospective facts，不把 research 历史改标为实时采集。服务不构造或调用 research training provider，也没有缺失 release 后的历史 provider fallback。仓库为验证已释放状态、授权和血缘而复核受控本地原始证据，属于审计，不是重新抓取或从 research provider 重新训练。

`bundle-export` 发布的目录严格包含：

```text
analysis_packet_v3.json
approved_training_history_audit_v1.json
bundle_manifest.json
```

manifest 固定 schema 为 `PRODUCTION_AUDIT_BUNDLE_V1`，记录 packet/sidecar 的原始字节数与 SHA-256。V3 packet 本身保持原契约；sidecar 绑定 run/input manifest、target plan、release/approval/history manifest、quant state/core/facts、技术证据、来源/赛季摘要、保留期限以及实际运行时间。目录先完整 staging、fsync 和复读校验，再持有发布授权事务做 no-replace rename；任一步失败不发布半个 bundle。`--output` 是 CLI 结果 JSON 的可选位置，不替代请求里的 `bundle_directory`。

`review-import` 要求上述完整 bundle 与真实外部提供的 review 文件匹配。CLI 不产生网页 review，测试生成的 `LLM_REVIEW_V3` 仅存在 pytest 临时目录且标记 synthetic。`fusion-create`/`portfolio-revise` 使用明确的本地 TOML 参数和实际 `SqlAlchemyProductionAuditRepository`，新建、direct/cached/重试路径均不能略过当前授权、state/facts 与 sidecar 检查。

对已标记为 approved training history 的 run，旧 `analysis-packet export`、`llm-review import`、`fusion-run create`、`portfolio-revision create` 未注入具体 audit repository 时必须 fail closed；不能因对象已经存在而返回成功。使用 production-quant 的 audited 命令。旧的离线 `llm-review validate` 仍可验证历史字节，但验证成功不等于当前获得导入、推理或派生使用许可。

## 信任与文件边界

`AuthorityPinsV1` 包含固定 `schema_version=PRODUCTION_QUANT_AUTHORITY_PINS_V1` 和非空 `trusted_authorities` 字典：键为证据根内的相对 POSIX authority 文件路径，值为其原始字节的小写 SHA-256。它描述已批准的可信配置，不能由候选请求自我声明信任。每个 pinned authority 文件均需已存在、匹配 hash，并通过 `ReviewerAuthorityV1` 解析。CLI 从不自动补建 authority 或审批。

审核要求是 **reviewer 被该 trusted authority 授权，并绑定精确 payload、source scope、schema 与有效时间**。不额外发明 reviewer 必须不同于 operator/prepared_by/issued_by 的人员分离政策；这些名称是 provenance。trusted authority pins 仍是与候选审批内容分开管理的信任配置，而不是 review 自我授信。

authority、review、adapter 和原始文件全部保留在受控本地目录。证据 reference 拒绝绝对路径、路径穿越、反斜杠、冒号、符号链接和 junction；授权配置、目录 ACL、数据库和本机时钟仍是可信本地边界，hash/SQLite trigger 不是对可替换整个数据库与配置的管理员的防篡改保证。

| 边界 | 限制 |
| --- | --- |
| 请求 JSON | 每文件最多 `67,108,864` bytes，即 64 MiB |
| authority pins JSON | 最多 `1,048,576` bytes，即 1 MiB |
| 每个本地 evidence 文件 | 最多 `67,108,864` bytes，即 64 MiB |
| 每份输出 JSON | UTF-8 编码加末尾换行最多 `268,435,456` bytes，即 256 MiB |
| 每份 downstream TOML | 最多 `1,048,576` bytes，即 1 MiB，解析为现有 `AppSettings` |
| LLM review 与每个 bundle 文件 | 每文件最多 `1,048,576` bytes，即 1 MiB；bundle 的安全 JSON 检查另限制嵌套与字符串，拒绝 secret/raw payload 字段 |
| 操作 ID | 1–160 字符，不允许首尾空白 |
| JSON parser | 严格 UTF-8；拒绝重复键、NaN/Infinity、浮点溢出、空文件、超限和非法结构 |

上限按单个文件/输出计算，不代表整次操作的总内存上限。请求和结果会在内存中解析/序列化。本地 derived 契约可能包含规范化事实、review reference 和授权条款字段，因此输出也必须保持私有；“不输出原始 bytes”不等于可公开发布所有派生工件。

请求、pins、authority/review/adapter/descriptor/recipe、downstream bundle/review/config 和输出路径先预检，再打开或迁移数据库。review-import 在此阶段验证精确 packet/context/match 绑定，不是只确认 review 文件存在或未超限。

**capture 的 provider 原始文件不在此预检阶段读取或解析。** CLI 仅检查 reference 字符串的相对 POSIX 路径语法，数据库中的 source rights、source scope 和有效用途检查通过后，仓库才打开、限长读取和严格解析 provider JSON。因此 malformed raw 的拒绝可能发生在写命令迁移之后，不能声称数据库完全未打开；失败 capture 不写入 receipt。复用已过期 capture 的 key 但更换 reference/来源/请求内容必须在新 provider bytes 读取前拒绝；完全相同的既有 receipt 可在原历史授权语义下复核原文件，不授权导入新文件。

数据库 ID 才能解析到的证据仍由仓库在正确事务和 pilot reservation 门禁之后验证，不为了 CLI 预检提前读取目标结果。仓库也会复核证据，预检不是绕过其校验。

`inspect` 和 `revoke-prepare` 使用 SQLite `mode=ro` 连接，绝不自动创建或迁移数据库。旧 schema 的读取失败不会修改原数据库；需另行安排获授权的迁移。其他写命令可在预检通过后迁移，后续仓库拒绝不等于迁移被回滚。

不指定 `--output` 时，stdout 输出 canonical JSON。指定时，在同目录写临时文件、flush/fsync 后通过硬链接原子发布，**任何已有文件均不覆盖，即使内容相同也拒绝**；stdout 仅返回状态、ref（如适用）、文件字节数和 SHA-256，不重复大契约。底层文件系统不支持该原子操作则失败，不退化为覆盖写。

`inspect capture` 仅输出 receipt，不输出数据库中的 `payload_bytes` 或原始 provider JSON。CLI 不读取 provider key，错误不回显输入值、Pydantic input/location、SQL 参数、数据库 URL、原始异常文本或 traceback。错误输出为 sanitized `PRODUCTION_QUANT_CLI_ERROR_V1` JSON。

## 阻断与退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | `RECORDED`、`VERIFIED`、`PREPARED` 或帮助/schema 成功；不代表真实来源或推理已授权 |
| `2` | `INVALID_LOCAL_INPUT`；输入/显式证据/输出路径拒绝，数据库未打开 |
| `3` | 不支持的旧写合同或未经过受控 correction 的请求；不是 V2 approval-record 的固定阻断 |
| `1` | 数据库不可用、仓库拒绝或输出未确认；检查具体 sanitized code |

前批 V1 事先审核内容包含未来 persistence 观察导致的写入冲突，已通过获授权的独立 V2 envelope 解决，而不是改变 V1 hash。`approval-prepare` 返回精确 V2 payload；真正 reviewer 审核并提供原 attestation/evidence 后，`approval-record` 才能成功。Manifest/source/pilot/code/recipe/grants/retention/supersession 任一变化都必须重新审核匹配的新内容。

V2 记录事件保留原 attestation，不重新签署/绑定。实际 recorded/persisted 是事务封存观察，不预测物理提交完成。幂等同 key 同内容返回原事件；同 key 换内容拒绝；换 key、证据路径或排版也不能重用同一审核授权逃避撤销/supersession。新的精确 scope 授权遵守 freshly reviewed successor/no-fork 规则。Formal CLI/仓库测试覆盖实际 approval-record 后的 release/live/audit/Review/Fusion/Portfolio，不再仅以 seeded approval 证明写路径。

失败的持久化边界：解析/预检失败未打开数据库；prepare/context/inspect 是只读，不创建审批/修订；approval/correction 写事务中证据、权限、前序、冲突或最终时间检查失败会回滚本次图；并发原请求只得到一个已提交事件。数据库提交后输出失败可能已有持久化事件，需按原 key 核对/重试，不能删除记录或换 key 重发。测试显式覆盖这几类结果。

若操作已经提交而输出文件写出失败，返回 `OUTPUT_NOT_WRITTEN`，明确提示可能已持久化；输出文件与 SQLite 提交不构成跨资源原子事务。此时先核对已持久化工件和 attempt/reservation，不盲目重复 pilot。退出 `1` 不保证“没有写入任何内容”。

## 前批定向验证记录

下列命令与计数是 `c9083e5` 前批的历史记录，不是本次 V2/correction 的结果。本次完整门禁在末节单列。

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/e2e/test_production_quant_cli.py -q --durations=5
python -m pytest tests/e2e/test_production_live_cli.py -q --durations=5
python -m pytest tests/e2e/test_cli.py tests/e2e/test_live_cli.py tests/e2e/test_historical_cli.py tests/e2e/test_review_bridge_cli.py -q
python -m ruff check src/football_system/interfaces/production_quant_cli.py tests/e2e/test_production_quant_cli.py tests/e2e/test_production_live_cli.py
python -m ruff check .
```

- 前批 Production Quant CLI：`71 passed, 1 warning`，363.14 秒；Production Live CLI：`9 passed`，82.70 秒；既有四组 CLI：`26 passed`，67.68 秒。Python 3.13.9，均为前批本地定向复检，不是本次或全仓 pytest 总计。
- 所属文件 Ruff 与最终 `python -m ruff check .` 均通过；最终总门禁见下节。
- 唯一 warning 为 `AppSettings` schema 发现时，Pydantic 省略静态 `Path` 默认值 `data/fixtures/mvp_matches.json` 的非 JSON 序列化默认项；不涉及 provider bytes/key，也不改变实际配置模型的解析或运行门禁。
- Admission E2E 使用真实 SQLite 事务与临时测试证据，但它们是合同测试文件，不是真实来源许可或历史。
- Pilot E2E 使用现有、显式标记 `SYNTHETIC_CONTRACT_ONLY` 的 admission test port，并执行真实 CLI parser、证据检查、pilot 服务和 SQLite 仓库；验证失败留存、完整 attempt 汇总、terminal 关闭及 synthetic 不得提供生产技术证据。
- Downstream E2E 复用 `test_production_audit.py` 的受控 seeded synthetic integration fixture，执行真实 CLI、文件、数据库与 audit gates，覆盖 export/import/fusion/revision 和旧入口缺失 audit 的拒绝。
- Live E2E 复用 `test_production_inference.py` 的受控 fixture，在实际服务旁加 wiring spy，核对精确 pins/preparation/plan/observation、统一时钟和不调用 training provider；实际 inference 的独立集成测试仍由该原测试模块负责。
- capture spy 覆盖缺失/越界/过期 rights、过期 key 修改 reference 和原 receipt 精确重试；malformed downstream bundle/review/config 必须在数据库和 provider raw 读取之前拒绝，错误不泄露测试 secret。
- 前批 seeded approval 和测试生成 review 仅限 pytest 临时存储，当时正式写入口仍有合同阻断。本次另增正式 V2 approval-record 写路径测试；两类测试都不能声称真实生产链路已验收。

未运行真实 production pilot、release-build 或 AVAILABLE packet 验收，未修改 ignored raw/Scottish 历史或 `0.5` acceptance 数据。本报告不替代来源取证、法律授权、授权审核和主代理的最终全量发布门禁。

## 前批本地门禁

| 门禁 | 实际结果 |
| --- | --- |
| `python -m pytest -o pythonpath=src -x` | `1224 passed, 1 skipped, 33 warnings`，2288.22 秒；完整执行至 100%，不是截断的定向总和 |
| 最后补充的 populated-baseline upgrade 与 wheel script 回归 | `7 passed`；其中新增 upgrade 用例 1 项，其余 6 项已在完整 suite 中覆盖 |
| `python -m ruff check .` | 通过 |
| `python -m compileall -q src tests migrations scripts` | 通过 |
| Fresh SQLite `upgrade head` + `command.check` | head `c2ebf618d354`，`No new upgrade operations detected` |
| 带既有数据升级 | `test_production_quant_upgrade.py` 从 `6e4b1a9c2d73` 含完整旧行与已完成 manual AnalysisRun/V3 Packet 的隔离库升级，逐表原行及旧 trigger 不变，FK 检查通过 |
| `python -m build --wheel` | 生成 `football_system-0.5.0-py3-none-any.whl`，未变更版本 |
| `python scripts/wheel_e2e.py` | 通过；50 项资源，隔离安装 provenance、production CLI/schema/pinned live help、8 条历史 CLI 路径和 2 x 10 slices |
| whitespace | `git diff --check` 通过 |
| secret / scope scan | 已含全部新增 staged 文件；凭证模式与独立静态复核未发现真实 Key、原始数据、证据或数据库进入提交，测试敏感字串均为 synthetic sentinel |

完整 suite 的 1 项 skip 是 Windows 不适用的 POSIX `dir_fd/O_NOFOLLOW` 分支；Windows opened-handle/junction 防护测试已实际执行。33 项 warning 包含旧 SQLite datetime adapter 弃用提示、Pydantic Path schema 默认值提示，以及 pilot 为保证 summary/attestation 原子封存而引入的显式 deferred FK 环导致的 SQLAlchemy 排序提示。FK 运行时校验、schema/trigger parity 和升级测试通过；未来 SQLAlchemy 升级仍需复验该环。

此前一轮全量执行在一小时上限中止，不能算通过。随后修复小文件 64 MiB 预分配和重复 recursive seal 校验，并限定只在单次一致性验证范围内复用 immutable parents；不缓存跨操作授权。最终完整结果如表所列。仍需对真实 306 场以上 source graph 做资源/耗时验收，合同测试耗时不构成生产容量保证。

此节是 `c9083e5` 前批证据，不冒充本次测试计数。本次进一步完成 V2 写入、受控 correction 和显式 season 接口，并复核 review replay、最终时钟、withdrawal logical-key 归属、revision 顺序、base alias 重放、typed FK 和 pre-plan 读源边界。真实来源/权限缺口仍按前述四类分别记录。

## 本次边界与验收

- 实现范围内的 correction 保持同一 `internal_match_id` anchor，支持比分、时间、球队关联、赛季、mapping policy、status withdrawal/restoration；跨 canonical match 的 provider 重指仍明确拒绝，不把它伪装成普通结果修订。此边界不是通过第二套比赛/赛果表解决。
- 原生 source 混合 metadata/result 文档不能用于 plan 前 metadata 读取。需要分离的、可核验 metadata capture；共用 receipt、同 payload 或路径别名在读取前拒绝。源数据完整字节仍在 admission 和 reservation 之后复验。
- 真实样本、真实 rights/authority、真实 integrity pilot 和生产审核尚未具备，所以没有真实性能数字、sealed production release 或新的 AVAILABLE Packet 交付。不会自动购买接口、创建许可或生成网页 Review。
- V1 approval 保留 golden/hash/解析与升级回归；旧记录、旧运行和 Packet/Review V3 不自动转换。新的正式 writer/CLI 与 corrected workflow 结果只作为隔离测试证据。

### 本次完整门禁

| 门禁 | 结果 |
| --- | --- |
| `python -m pytest -o pythonpath=src -x` | **1577 passed, 1 skipped, 38 warnings**，完整运行至 100%，4652.00 秒；不是多个定向计数相加 |
| `python -m ruff check .` | 通过 |
| `python -m compileall -q src tests migrations scripts` | 通过 |
| `git diff --check` | 通过 |
| Fresh `upgrade head` + `command.check` | `f51e294b0687`，`No new upgrade operations detected` |
| 带旧数据升级 | 完整 suite 覆盖 c908 前的 normalized/AnalysisRun/V3 数据、已持久化 V1 approval/request/row checksums、populated V1 pilot，以及 correction 前序重放；原行和 hash 保持 |
| Wheel build | `football_system-0.5.0-py3-none-any.whl`，未改版本 |
| Installed-wheel E2E | 53 资源，安装 provenance、approval/correction schema、pinned live help、8 个历史 CLI 路径、2 x 10 slices、settlement/report 均通过 |
| Secret / scope scan | staged 新增与修改文件及本报告已复核；未发现真实凭证、raw/证据/数据库或伪造现实审批工件进入提交；数学/V3 与授权范围未扩张 |

唯一 skip 为 Windows 不适用的 POSIX `dir_fd/O_NOFOLLOW` 分支；Windows 文件句柄/junction 用例已执行。Warning 分别为既有 sqlite3 datetime adapter 弃用提示、AppSettings Path 默认值的 JSON-schema 提示及 pilot deferred FK 环的 SQLAlchemy 排序提示。FK、metadata/trigger parity 与升级检查通过；未来依赖升级仍需复验。

### 正式写路径与修复证据

- `test_training_approval_v2.py` / `test_training_approval_v2_cli.py` 通过正式 recorder/CLI 写入 V2 approval，再运行 release、live inference、audit、Review import、FusionRun 和 PortfolioRevision；没有用 seeded approval 替代这个写路径。技术 pilot bridge 在部分隔离端到端测试中明确使用 double，公共生产 gate 仍拒绝 synthetic pilot。
- 同 request 重复与并发重试保留唯一原事件；同 key 换内容和换 key 重放旧审核均拒绝。撤销/有效 successor 后，路径别名或重新排版的旧 review 不恢复权限；重新授权必须有精确 scope 的 freshly reviewed successor。
- 事务最终读取之后的时钟观察重新判断 source rights、training/inference 和 retention；测试注入在最后复验中生效的撤销/过期，确认回滚且最终时钟之后没有新的数据库/证据 I/O。
- Correction 测试覆盖 withdrawal 的 logical-result-key 所有权、修订 metadata 不得消失后重置 order、equal-source-time 明确 revision order、前序缺失/分叉/并发、withdrawal/restoration、真实 season/kickoff 变化及 late local registration。
- 同一来源事实在不同 admission/batch-local sequence 下有不同 audit binding，但来源版本 fingerprint 只排除局部 sequence，保留全部来源/审核内容。子集重新准入不会破坏原 base pin；旧 release 正确变 stale，完整新 corrected release 可用。原始 V1 bytes 不改写。
- 纠正后的 context rows 必须有非空、真实存在的 typed correction FK；共享 result-bearing capture 在 plan 前读取前拒绝；f51 空图 downgrade 保留真正的 populated V1 plans/reports/attestation，V2/未知图拒绝降级。独立审查复现的问题均有针对性修复和回查。

运行真实 pilot 的下一项外部输入仍是**适用历史来源的权限审核材料与最小 completed Bundesliga 原始样本/时间证据**，不是开发授权或 Key。具备后先核验字段再扩大完整 cohort；在真正 AVAILABLE Packet 生成前不进入网页 GPT Review 验收，也不伪造真实指标。
