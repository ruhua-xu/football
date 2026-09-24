# OPENFOOTBALL_CURRENT_SNAPSHOT / JSON_ADAPTER V1 契约

**Accepted for software release 1.2.0 — OpenFootball candidate CI Review PASSED**：commit `df47ba4cf34eb4f0a964c2e5ab5d0e88ea6a57f6` / tree `034372ea5297d391bcfef74d76d92d4175461689`。本次仅更新发布状态；以下Stage 3技术正文及parsing/timezone/qualification语义保持冻结。最终production binding与运行库维护见1.2.0发布报告。

Stage 3实现候选，2026-09-22；基线v1.1.0 / `5ed940a8af8077be80549603a2da38aea77fc1bf`。
本轮是**独立、additive的有限文件qualification与bootstrap preparation lane**，不执行production admission、training、release或real-bridge写入。

## 1. 实现范围与入口

| 新文件 | 责任 |
|---|---|
| `domain/openfootball_snapshot.py` | 版本化scope、capture、policy、rights/mapping候选、provenance及不可声称历史metrics的类型 |
| `infrastructure/providers/real/openfootball_observed.py` | 纯离线native JSON检查、A/B/C/D分类、明确Berlin转换、完整统计及逐行诊断 |
| `infrastructure/files/openfootball_evidence.py` | 私有受控文件读取、精确四文件pins/bytes核验、qualification组装、no-replace私有输出 |
| `interfaces/openfootball_cli.py` | 独立`python -m`入口，仅接收evidence root/policy/output |
| `config/openfootball_snapshot_requirements.txt` | 独立准备环境使用的固定tzdata依赖 |

旧`CurrentSnapshotCollectionScopeV1`、Sportmonks native parser、strict historical adapter、production-quant CLI、DB schema及历史工件语义均保持原有字节。没有向SPORTMONKS literal塞入OPENFOOTBALL，也没有用新的候选类型冒充旧admission。

## 2. 新scope与provenance

新类型：**`OPENFOOTBALL_CURRENT_SNAPSHOT_SCOPE_V1`**。

```text
provider_code = OPENFOOTBALL
competition = BUNDESLIGA
source_data_mode = SOURCE_TIME_RESEARCH
retrospective = true
evidence_basis = CURRENT_SNAPSHOT_OBSERVED
status = CANDIDATE_NOT_ADMITTED
production_training_authorized = false
training_window = null
```

Scope绑定完整四文件acquisition manifest、显式adapter policy及两个candidate season标签；候选赛季不等于训练窗口。`SnapshotModel`拒绝未知字段、保留原文空白语义、重新验证传入Pydantic实例（包括model_copy/model_construct）。布尔标志不接受整数冒充。

逐record的`OpenFootballRecordProvenanceV1`保留：

- source commit、filename、原始文件SHA256。
- 原始`/matches/N` JSON pointer；完整原record的tagged canonical hash。
- 实际local capture UTC、local date/time text、adapter policy hash。
- SOURCE_TIME_RESEARCH、retrospective、CURRENT_SNAPSHOT_OBSERVED。
- **provider_publication_at_utc=null、provider_finalized_at_utc=null、provider_time_status=UNKNOWN**，类型本身拒绝非null。

Git时间、HTTP下载时间和kickoff+2h都不能填入这两个provider时间。Local capture只证明当时持有该版本。

## 3. 精确source binding

第一期只接受`openfootball/football.json`的commit **40b3e1b7391932d133287115106304444bf297e1**及：

`2024-25/de.1.json`、`2025-26/de.1.json`、`README.md`、`LICENSE.md`。

`OpenFootballFileCaptureV1`逐项核对固定URL、commit、filename、byte length、SHA256及request-start/capture先后。固定hash/长度来自本轮真实获取，详见[数据资格报告](openfootball_bundesliga_data_qualification_v1.md)。不能通过改caller hash批准另一份bytes，也不能把license/readme的hash当dataset hash。

Manifest需要恰好四个唯一文件、COMPLETE、4次发送/0重试/无errors和本轮有限获取授权标签。标签是操作证据字段，不是production grant或软件能独立验证的法律裁决。

`qualify_openfootball_files`在读取任何match记录前验证所有bytes。单独调用`inspect_openfootball_file`仅产生**UNVERIFIED_SCAN**，用于诊断/合成测试；只有精确四文件边界的父qualification才声明**EXACT_PINNED_FILES_VERIFIED**，仍然不是admission。

私有root必须现存、位于public Git外、无symlink/reparse；使用已有safe-open/contained-reference边界和closed-root拒绝规则。Parser和CLI都不获取网络数据；只接受本地文件。Qualification文件以临时文件+no-replace link发布，已有输出拒绝覆盖。

## 4. Adapter policy：时间必须显式

**`OPENFOOTBALL_JSON_ADAPTER_V1`**第一期固定Bundesliga、DOMESTIC_LEAGUE、REGULAR_LEAGUE_MATCHES。

以下字段是必填项，不能靠系统时区或隐藏默认值：

```text
timezone = Europe/Berlin
tzdata_version = 2025.2
iana_version = 2025b
tzif_sha256 = a7fd9932d785d4d690900b834c3563c1810c1cf2e01711bcc0926af6c0767cb7
```

Policy还封存YYYY-MM-DD、HH:MM、MINUTE来源精度、显式秒00规则、DST gap/fold拒绝策略、score.ft候选语义、direct array异常策略与abnormal status拒绝策略。

实际policy content hash：**`a237ea1875560ccbbc15efa3102463f025baa825be04004863a1e5a4c9e36f75`**。

转换步骤：

1. date/time格式与日历值分别校验，保留原文；缺失time时UTC=null并记录MISSING_OR_INVALID_TIME，不补午夜。
2. 核验tzdata package版本、IANA版本与Europe/Berlin TZif SHA256，通过`ZoneInfo.from_file`构造时区，绕开宿主机TZPATH/cache。
3. 分别枚举fold=0/1，UTC→local round-trip后只接受**唯一**UTC。0个候选为NONEXISTENT_LOCAL_TIME，2个为AMBIGUOUS_LOCAL_TIME，均阻塞。
4. HH:MM的秒00是表示规则，保留minute precision，不声称来源提供了秒级测量。相邻minute仍得到不同的精确UTC。
5. kickoff必须早于local capture，不能把未来fixture作为历史结果候选。

缺少/错误tzdata或被修改的TZif均拒绝，不退回固定UTC+1/UTC+2。

## 5. Native JSON与比分

- 根只接受`name`与非空`matches`，name必须与固定filename对应的`Deutsche Bundesliga 2024/25`或`2025/26`精确一致。
- 从原始record读取`date,time,team1,team2,score`及round；不猜日期、队名、比分或联赛。
- 完整JSON严格解析，拒绝重复key、NaN/Infinity等；根schema错误直接拒绝。逐行问题保留diagnostics和完整行数。
- 第一版regular round仅接受`Matchday 1`至`Matchday 34`；实际每轮数单独统计，不由理论场次推导coverage。
- A：`score.ft`包含两个严格非负整数，可有合法且不大于ft的ht；仅输出待审核的regular-time候选。
- B：直接array保持`SOURCE_SCHEMA_EXCEPTION / REPORTED_SCORE_PERIOD_UNPROVEN`。已查上游reported语义没有FT等价保证，不静默转换。
- C：missing/null score与0:0不同，比分候选null。
- D：et/p/agg、abnormal representation、awarded/其他status或杯赛/非regular round均拒绝。数字存在不能覆盖异常状态。
- bool、负数、float、字符串比分、额外比分项和冲突ht均拒绝。
- 所有来源行保留；同赛季有向队对重复时全部相关行标记DUPLICATE_MATCH，不选择有利的日期/比分变体。

有任意阻塞diagnostic的record不输出可用比分候选。全文件/全cohort仍保留错误清单，不能以通过结构检查的子集暗中组成训练集。

## 6. Canonical plan与rights candidate

`OPENFOOTBALL_CANONICAL_MAPPING_PLAN_V1`绑定scope hash；每个TEAM/COMPETITION/SEASON source label都有显式entry，canonical ID和identity evidence reference/hash缺失时为null。按kind/source label确定性排序，拒绝重复entry。

`require_explicit_identity`在未解决alias或不同拼写下返回IDENTITY_UNRESOLVED；没有fuzzy匹配、ID猜测或队名→team_id捷径。该方法只读取候选plan内显式身份，不证明catalog真实性或人工审核；正式使用还需真实catalog与审核。

`OPENFOOTBALL_SOURCE_RIGHTS_CANDIDATE_V1`固定CC0、repository/commit、README/LICENSE hash和7项候选用途，status为CANDIDATE_NOT_APPROVED，trusted pins/review=null，不能改为production_training_authorized=true。

Operator可以本人担任authorized_reviewer。后续须由独立trusted authority pins明确授权其source/schema/时间范围，真实review记录该operator身份并绑定精确payload/hash；沿用现有LocalTrainingEvidence的核验语义，不附加“必须第三方律师/不同人”的要求。本轮没有制造authority或review artifact。

## 7. 训练资格与历史预测验证分离

`OpenFootballHistoricalValidationV1`将historical availability、Brier、LogLoss、calibration固定为UNAVAILABLE，reason=UNPROVEN_HISTORICAL_VERSION_TIME，metrics=null。

这不禁止未来对合法当前快照做固定Elo candidate build。生产训练还需要完整事实/身份准入、窗口确认、真实审核及后续明确授权；这些不能由本preparation报告替代。

`semantic_records_hash`只用于检查解析结果的确定性，不是Elo training_data_hash或state_hash。源文件重排会改变原始file SHA/pointers；同一事实集的semantic hash仍可一致。Acquisition文件集合和mapping输入顺序变化不改变各自canonical content hash。

## 8. 操作方式与阶段停止点

本轮依赖安装到隔离目录`C:\Users\93428\AppData\Local\Temp\opencode\openfootball-stage3-deps`，正式`.venv`中的1.1.0 wheel未重装；pyproject/version/旧freeze verifier未修改。功能从本分支source checkout验证。

在已配置新source和固定tzdata的准备解释器中：

```text
python -B -m football_system.interfaces.openfootball_cli
  --evidence-root <私有root>
  --policy adapter_policy.candidate.json
  --output <全新的私有qualification文件路径>
```

Policy reference必须是root内受控相对路径；output禁止public Git及替换原始证据。CLI成功生成报告的退出码0只表示qualification完成，报告仍可为STAGE3_BLOCKED；输入/文件错误返回2。没有train/release/database/real-bridge子命令。

Stage 3只产生scope、rights、mapping候选与qualification。新的类型未被接入旧V1的production recorder/release writer；后续正式入库和production build仍需适用的版本化绑定与独立审核。本轮不修改旧V1去接纳它们。

## 9. 验收

OF01–OF20及扩展负例共69个pytest cases全部通过；旧native/observed/strict evidence/Elo/production release定向回归648 passed、1个POSIX Windows不适用skip。
真实文件qualification在PYTHONHASHSEED=0/1/42/12345下字节hash一致；重放进程禁用fit/predict并禁止网络。

结果、精确源码hash及命令见[Stage 3报告](openfootball_elo_bootstrap_stage3_report.md)和[交审包](../upload/openfootball-elo-bootstrap-v1/README.md)。
