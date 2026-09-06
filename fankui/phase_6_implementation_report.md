# Phase 6 Production Quant 本地契约与集成实施报告

## 结论与范围

**真实生产运行状态：BLOCKED。没有达到完整 production quant 目标。** 当前实现覆盖纯领域契约、training admission、离线完整性 pilot、production release、固定 release 的 live inference、强制 audit/downstream 门禁以及本地 CLI。完成代码和隔离测试不等于获得生产授权、完成真实 Bundesliga pilot 或形成模型表现验收，本报告也不是新的发布版本。

基线为 `3a675dde1e7101d08f77e83e2c6e9ea117ff7aef`，继续使用 `feature/0.6.0-production-quant-bootstrap`。本批作为受阻状态下的实现提交，不代表完整 0.6 验收或发布到 `main`；版本仍为 `0.5.0`。提交 SHA 与远端 CI 结果另在交付消息中列明，避免报告自引用。

- `src/football_system/interfaces/production_quant_cli.py` 提供 17 个本地命令及共享 `production_inference_context`。
- `interfaces/cli.py` 已由主代理添加 production-quant dispatch、帮助入口及 `_run_live_analysis` 的成组 release/target-plan pins；本次续作未编辑该文件。
- `tests/e2e/test_production_quant_cli.py` 覆盖入口、读取顺序、文件边界、SQLite、synthetic pilot 和完整 audited downstream 接线；新增 `tests/e2e/test_production_live_cli.py` 覆盖 live pins 依赖、精确 preparation/plan 绑定和禁止 training-provider fallback。
- 运行时不访问 provider 付费 API、不提供假输入生成器、不自动创建 authority/来源许可/approval，不补造历史时间戳。测试中的 seeded approval、pilot double 和 review 文件仅用于受控临时数据库的契约验证。

## 实现链路

| 层/模块 | 已实现代码与集成边界 |
| --- | --- |
| `domain/training_admission.py`、`application/training_admission.py` | 来源使用许可、常规时间结果、fixture/season/mapping 关联、候选封印及 source-time 事实契约 |
| `LocalTrainingEvidence`、`training_admission_repository` | 受控本地文件/hash、授权 reviewer 与 trusted pins 检查；rights 先于 provider bytes；实际 import/registration/admission 时钟；既有身份精确复用、原子 admission、immutable retry、关系投影复核 |
| `domain/quant_integrity.py`、`QuantIntegrityPilotService`、`quant_integrity_repository` | 固定 Elo/no tuning；先封存完整 cohort/计划与 reservation，再读结果；确定性计算/replay、失败 attempt 留存、完整 attempt root、terminal attestation；synthetic 不得供给生产技术证据 |
| `domain/production_release.py`、`application/production_release.py`、`production_quant_repository` | 精确 admissions/history manifest、grant/retention/revocation/target 契约；实际授权边界与固定数学 replay；approval 写入口主动阻断；来源更正未受控解决时继续拒绝 |
| `ports/production_inference.py`、`production_inference_repository`、`RunModelAnalysisService` | 从显式 release 和 target plan 投影既定状态；不调用 research training provider；保存 run/release/plan/state/facts 绑定，写入与重试重新验证当前授权和实际时钟 |
| `production_audit_repository`、review/post-review repositories | 将 approved run 的原 V3 packet 与 `APPROVED_TRAINING_HISTORY_AUDIT_V1` sidecar 原子绑定；packet、review import、fusion、portfolio 及 cached/direct 路径均要求实际 audit 依赖和当前授权 |
| `production_audit_bundle.py` | 原 V3 packet 字节不改版；以三文件 bundle 交换审计 sidecar，校验大小、hash、类型、绑定和安全内容，并在发布前通过本地授权门禁 |

持久化新增 migration 为 `8a7c2f4e9b10`（admission）、`9b8d3e5f0a21`（pilot）、`a0c9e4f6b132`（release）、`b1dae507c243`（analysis binding）、`c2ebf618d354`（audit）。对应 immutable 表、投影、事务和拒绝路径由各层测试验证；全仓、升级和 wheel 门禁结果见末节。

`recorded_at_utc`、`local_imported_at_utc`、`archive_created_at_utc`、`registered_at_utc`、admission/build/inference/audit 的实际开始、完成和持久化观察时间来自操作时钟，而不是调用者预测的未来提交时间。source available/observed/finalized 时间保持其原始证据语义，不与本地持有时间混用。相同 immutable request 重试保留原工件时间，但仍执行对应当前授权/完整性门禁。

共享 context 以 `clock=None` 为默认，在调用时解析当前 `utc_now`，将同一个时钟依赖传给 admission、pilot、production、inference、audit 仓库；测试可显式注入受控时钟，CLI 没有伪造操作时钟的选项。

## 真实证据缺口

| 所需证据 | 当前结论 |
| --- | --- |
| 真实来源许可及授权审核 | 尚无可用于本生产流程的真实 rights；不能将测试条款、测试 reviewer 或 pins 当成授权 |
| 本地 Bundesliga 历史原始文件 | 尚无包含可核验 source available、observed、provider finalized 时间及常规时间比分语义的合格历史包 |
| 来源与赛季身份 | 尚缺原始 provider competition/season 字段、球队别名、fixture mapping、canonical season 的完整已审核关联 |
| 完整 pilot cohort | 尚缺完整赛程、已结束赛季证明、每个缺失/取消/延期/来源范围差异的授权审核证据 |
| 持有时间与来源时间 | 必须分别证明实际本地 capture/admission 时间与上游来源时间；mtime、文件名、赛季标签和 kickoff 推算均不能替代 |
| 生产审批 | `record_approval` 仍存在 reviewer hash 与未来持久化时间的核心契约冲突，写入必须停止 |
| 受控更正 | 完整 predecessor、来源/映射/赛季/结果更正以及后续授权关联路径尚未实现，相关生产操作继续阻断 |
| 旧 archive provider | 新 admitted offline provider 已按逐场 membership 多赛季投影；旧 `HistoricalArchiveEloTrainingProvider` 的 constructor-season 接口尚未改造，不得把它作为本生产路径的替代入口 |

已有 ignored Scottish 228 条结果使用 `kickoff + 2h` 作为 observed 时间，不是 provider source/finalization 的原始时间证据，且不是所要求的 Bundesliga 历史。不得修改这些旧数据、补造时间戳、重新标记用途或将其用于本 pilot。既有 `0.5` acceptance 与其 `MODEL_UNAVAILABLE` 事实保持不变。

本次没有真实 Log Loss、Brier、ECE、命中率、ROI 或可用性指标可报告，没有生成真实 `AVAILABLE` packet、生产 P_quant、投注或收益声明。测试内计算值只验证契约，不是现实表现或授权依据。

### 审批合同待决策

当前 reviewer attestation 绑定包含 `approved_at_utc` 和 `persisted_at_utc` 的完整 approval payload，而这些值必须由实际记录操作产生。提前审核不含这些值的请求，不能重新包装成已经审核最终 payload；预计未来时间也不合法。因此 `approval-record` 保持明确 BLOCKED，无公开导入或直接插入 approval 的旁路。

待外部确认的最小修订方向是分离“reviewer 审核的精确授权内容”和“系统记录事件”：前者封存 manifest/source/pilot/recipe/grants/retention/supersession 意图；后者再封存该已审核 payload hash、本地 attestation/evidence metadata 和真实 recorded/persisted 时间。此方向尚未获准、未实施，不静默改变 ADR-0008 或现有 V1 hash。

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
| `pilot-plan` | `PilotPlanRequestV1`：`definition` | 接受 `QuantIntegrityPlanDefinitionV1`，先核验元数据，再封存计划；不读取未保留 attempt 的目标结果 |
| `pilot-run` | `PilotReferenceRequestV1`：`plan_ref` | 先持久化 reservation，再通过现有服务执行离线概率完整性计算与 replay；失败 attempt 保留 |
| `pilot-attest` | `PilotReferenceRequestV1`：`plan_ref` | 汇总全部 attempt，绑定最后成功报告并终结 series；不授予生产许可 |
| `manifest` | `ManifestRequestV1`：`request_key`, `admission_ids`, `training_window`, `integrity_pilot_scope_id`, `attestation_id` | 从真实持久化 admissions 与有效 terminal pilot 图生成 manifest；不接受 synthetic 作为生产技术证据 |
| `approval-record` | `ApprovalRecordRequestV1`：`request_key`, `manifest_id`, `grants`, `review`, `reviewer_authority` | **BLOCKED**；输入预检后直接停止，不打开/迁移数据库，不伪造审批 |
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

主代理扩展的 live 命令保留现有 preparation 选择和预算选项，生产模式须同时提供下面五个参数，缺一即在数据库前拒绝：

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
| `3` | `BLOCKED`：`APPROVAL_RECORDING_CONTRACT_CONFLICT` 或 `CONTROLLED_CORRECTION_REQUIRED` |
| `1` | 数据库不可用、仓库拒绝或输出未确认；检查具体 sanitized code |

`TRAINING_HISTORY_APPROVAL_PAYLOAD_V1` 将 `approved_at_utc`、未来 `persisted_at_utc` 纳入 reviewer-attested payload hash。先前审核的无持久化时间操作请求不能诚实满足这一最终 hash，现有 `production_quant_repository.record_approval` 因而主动抛出 `ApprovalRecordingContractConflict`。CLI 在本地预检后镜像该停止条件，返回明确的 `BLOCKED` 和修复前提，避免无意义迁移。

运行时没有修改该 schema、移除 hash 字段、预测未来时间、重签 reviewer attestation 或提供直接插入 approval rows 的入口。为验证既有 approved graph 的 inference/audit 合同，受控集成/E2E fixture 可按既有测试约定 seed 精确匹配当前契约的 synthetic approval rows；这不是公开审批写流程或真实生产激活。下一步仍须先取得新 review/recording envelope 的明确核心契约变更授权，再单独实施、评审和验证。**该修复尚未实施，尚未接受任何受控更正。** 完整受控更正路径仍未实现，不得直接编辑旧来源或 immutable 记录代替。

若操作已经提交而输出文件写出失败，返回 `OUTPUT_NOT_WRITTEN`，明确提示可能已持久化；输出文件与 SQLite 提交不构成跨资源原子事务。此时先核对已持久化工件和 attempt/reservation，不盲目重复 pilot。退出 `1` 不保证“没有写入任何内容”。

## 验证记录

本任务实际执行的命令如下；这些是本地接口的定向验证，不是全仓总门禁，后者由主代理最后汇总。

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/e2e/test_production_quant_cli.py -q --durations=5
python -m pytest tests/e2e/test_production_live_cli.py -q --durations=5
python -m pytest tests/e2e/test_cli.py tests/e2e/test_live_cli.py tests/e2e/test_historical_cli.py tests/e2e/test_review_bridge_cli.py -q
python -m ruff check src/football_system/interfaces/production_quant_cli.py tests/e2e/test_production_quant_cli.py tests/e2e/test_production_live_cli.py
python -m ruff check .
```

- 本次续作 Production Quant CLI：`71 passed, 1 warning`，363.14 秒；Production Live CLI：`9 passed`，82.70 秒；既有四组 CLI：`26 passed`，67.68 秒。Python 3.13.9，均为本地定向复检，不是全仓 pytest 总计。
- 所属文件 Ruff 与最终 `python -m ruff check .` 均通过；最终总门禁见下节。
- 唯一 warning 为 `AppSettings` schema 发现时，Pydantic 省略静态 `Path` 默认值 `data/fixtures/mvp_matches.json` 的非 JSON 序列化默认项；不涉及 provider bytes/key，也不改变实际配置模型的解析或运行门禁。
- Admission E2E 使用真实 SQLite 事务与临时测试证据，但它们是合同测试文件，不是真实来源许可或历史。
- Pilot E2E 使用现有、显式标记 `SYNTHETIC_CONTRACT_ONLY` 的 admission test port，并执行真实 CLI parser、证据检查、pilot 服务和 SQLite 仓库；验证失败留存、完整 attempt 汇总、terminal 关闭及 synthetic 不得提供生产技术证据。
- Downstream E2E 复用 `test_production_audit.py` 的受控 seeded synthetic integration fixture，执行真实 CLI、文件、数据库与 audit gates，覆盖 export/import/fusion/revision 和旧入口缺失 audit 的拒绝。
- Live E2E 复用 `test_production_inference.py` 的受控 fixture，在实际服务旁加 wiring spy，核对精确 pins/preparation/plan/observation、统一时钟和不调用 training provider；实际 inference 的独立集成测试仍由该原测试模块负责。
- capture spy 覆盖缺失/越界/过期 rights、过期 key 修改 reference 和原 receipt 精确重试；malformed downstream bundle/review/config 必须在数据库和 provider raw 读取之前拒绝，错误不泄露测试 secret。
- seeded approval 和测试生成 review 仅限 pytest 临时存储，不绕过运行时核心审批阻断，不能据此声称真实正向生产链路已验收。

未运行真实 production pilot、release-build 或 AVAILABLE packet 验收，未修改 ignored raw/Scottish 历史或 `0.5` acceptance 数据。本报告不替代来源取证、法律授权、授权审核和主代理的最终全量发布门禁。

## 最终本地门禁

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

多轮独立审查中复现的读前权限、提交前时间、metadata stripping、缺失预测图、已记录撤销、历史 attempt 篡改与 orphan terminal 等问题已修复并由针对性回归复核。审查通过只覆盖已实现路径，不消除本报告列出的 core approval、controlled correction、旧 archive-provider 改造和真实来源阻塞。
