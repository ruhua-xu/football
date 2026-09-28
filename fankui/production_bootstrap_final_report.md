# OpenFootball production bootstrap — 最终交付报告

日期：**2026-09-23**。最终状态：**PRODUCTION_BOOTSTRAP_BLOCKED**。

## 1. 本轮最终执行边界

用户最初要求完整production bootstrap。执行中发现正式runtime没有未来Bundesliga 2026/27 target identity/live-input材料，无法封存具体target exclusions及pilot后的精确model approval payload。

展示完整前置payload/hash/authority/mapping/window摘要后，用户在唯一的question确认点明确选择：

> **确认前置准入后BLOCKED**

即：允许正式backup、production库升级、24项canonical catalog、612条源记录及599条observed facts准入；本轮随后停止，不授予model approval或release。后续“继续”按此范围收尾。

**该最新选择是本轮真实执行边界。** 本报告不把未执行的pilot、manifest、model approval、release、target/state binding标记为完成。

## 2. 软件身份

| 项目 | 精确身份 |
|---|---|
| Base / Git HEAD | v1.1.0 / `5ed940a8af8077be80549603a2da38aea77fc1bf` |
| 分支 | `feature/openfootball-elo-bootstrap-v1` |
| **Implementation SHA-256** | **`128a4737803ab9c0998038038c86790e7c7b21b0df5cf72a214f70d4754c323b`** |
| Production implementation revision | `package:128a4737803ab9c0998038038c86790e7c7b21b0df5cf72a214f70d4754c323b`，使用现有`_code_revision()`对实际source package计算 |
| Implementation commit | 未创建；不能把base HEAD冒充实现commit。交审manifest另列精确patch SHA和candidate tree |
| main / tags / release version | 未推进；没有commit、merge、push、tag或软件版本发布 |
| 新migration | `7d96abc3840f`，parent `6c859ab273fe` |

实现为versioned additive binding：新OpenFootball domain/application/repository/schema/CLI，以及既有production-quant、quant-integrity、production-inference的显式OpenFootball入口。旧`CurrentSnapshotCollectionScopeV1`、Sportmonks parser、strict historical artifacts保持原字节/语义，未扩大SPORTMONKS literal。

新`ofp_*` ledger与旧observed图分离，保留精确parent/hash、原子准入、one-shot pilot reservation、immutable exact retry、真实review/authority、manifest/release完整性边界。模型计算复用现有冻结Elo。未决target不会被按availability删除。

新增API与SQL双层隔离：OpenFootball normalized results不能通过旧裸训练或旧production-binding入口被当成LIVE_STRICT训练事实；必须使用新的版本化binding。原始数据没有重标SPORTMONKS。

## 3. 固定source cohort与实际准入

Repository：`openfootball/football.json`；commit：**`40b3e1b7391932d133287115106304444bf297e1`**。
用户的2024/25、2025/26是season标签；实际固定仓库路径仍为`2024-25/de.1.json`、`2025-26/de.1.json`，未重命名或重新获取。

| source文件 | SHA256 | expected / stored | exceptions | admitted |
|---|---|---:|---:|---:|
| `2024-25/de.1.json` | `f473d6595e6c29ebedc08b09177aed6a6b2ff0fa8378b82f51dea469139f44c3` | 306 / 306 | 1 | **305** |
| `2025-26/de.1.json` | `17d0999db6281e6365823acdbce41a4f01dd469eac63e1f897e4e47c02906672` | 306 / 306 | 12 | **294** |
| 合计 | 精确原始文件重放通过 | **612 / 612** | **13** | **599** |

固定exceptions完整登记：

- 2024/25：`/matches/120`，`ABNORMAL_MATCH_STATUS_AWARDED`。
- 2025/26：`/matches/8`、`/matches/44`、`/matches/53`、`/matches/75`、`/matches/99`、`/matches/127`、`/matches/129`、`/matches/133`、`/matches/153`、`/matches/159`、`/matches/223`、`/matches/274`，均为`REPORTED_SCORE_PERIOD_UNPROVEN`。

全部612条source records在原始文件、独立binding表和准入后backup中保留；exceptions仅改变明确的scope inclusion，不删除、转换或覆盖原始record。没有重新寻找历史来源、购买API或尝试把这12条array解释成FT。

每个included fact保留精确source filename/commit/file SHA、original pointer/record hash、Berlin→UTC、source capture与本地admission事件、canonical IDs和常规时间比分。`SOURCE_TIME_RESEARCH / retrospective=true / CURRENT_SNAPSHOT_OBSERVED`保持；provider publication/finalization均为null/UNKNOWN。

## 4. Canonical identity与固定window

已在同一production库原子登记：**1 competition、3 seasons、20 teams，共24项**。

| 类型 | label | canonical ID |
|---|---|---|
| Competition | Deutsche Bundesliga → Bundesliga | `4ed332b3-3b22-5645-bcbf-3c1895ea653e` |
| Season | 2024/25 — WARMUP | `4a25fb3c-cfa0-5042-90ff-ca7a848371ba` |
| Season | 2025/26 — PILOT_TARGET | `339d9480-55f2-5183-868c-cbf92d65cd5d` |
| Season | 2026/27 — PRODUCTION_TARGET | `1ffdfd54-dd21-5da6-bdd3-e2133d0846a8` |

20个team的逐项label→UUID、mapping_method、review evidence、reviewed_by/at、content hash在[正式mapping review投影](../upload/openfootball-production-bootstrap/openfootball_canonical_mapping_review_v1.json)中完整列出。没有fuzzy、隐藏alias、大小写合并或源队名充当内部ID。

- **Canonical mapping root**：`67a96a44b05339fa4dd5d5f11ddc50a536c78fac4f8884cb072750dd31c9b589`。
- **Training window hash**：`0e4c4ddbaafee6600de070d05f344ce9384742b81ff4191dc98b36cef9367116`。
- 2026/27为**zero-fact**，没有纳入其已完成赛果。
- 当时技术准备review与后续workspace-owner确认分别保留；确认投影引用原catalog entry hash及真实source review，不改写原先mapping/root。
- Confirmation entries root：`19a098b80bae524a7ea44474b88871b5db3a2d3bf514fbf4982e67bb981a45ae`，它与canonical mapping root属于不同hash域。

## 5. Genuine rights / authority / user confirmation

用户确认前，没有激活trusted pins或生成approved=true文件。收到明确选择后，才记录真实确认回执、source review并激活精确authority pins。

| 项目 | 已记录身份 |
|---|---|
| 前置source review subject hash | `128c7e170a181a7a8258a9bd25fd5969fd73b01e75dcb053d682329257c0c947` |
| CC0 LICENSE SHA | `36ffd9dc085d529a7e60e1276d73ae5a030b020313e6c5408593a6ae2af39673` |
| README SHA | `aadc464ae476ba4d5467785f6f2be435d1a583f589f4b2b0617fdf86fc18e29c` |
| Authority document SHA | `c79e6636c501ee3f5ca8bd5f20c6fc95c763e6391abfc5c0097d21588f39e60f` |
| Trusted authority pins文件SHA | `91dd2f97f430ff6e106146dd76687bed3967ed48c3b7ef7bc44631bea601d4dc` |
| 实际source review文件SHA | `220fe47801a0d89fc24dd0e52937ea4e392428dae5c06b59da80035f91a1beaf` |
| Operator / authorized reviewer | `workspace-owner` |
| 已确认的操作/review范围 | `2026-09-23T02:43:27.544096Z ≤ t < 2027-07-01T00:00:00Z` |
| Source uses | ACQUIRE、STORE_LOCAL、NORMALIZE、INTERNAL_RESEARCH、MODEL_TRAINING、DERIVED_STATE_RETENTION、AUDIT_RETENTION |
| Source retention | **PERMANENT_ALL_612_SOURCE_RECORDS**；操作授权区间不是原始文件删除期限 |

Authority覆盖OPENFOOTBALL source和相应data/model review schemas，但**授权某人担任reviewer不等于已经批准模型**。实际source review只绑定上述前置subject；model review/approval仍不存在。本人operator可审核，没有要求外部律师或第三方。

真实确认回执说明：review时间为trusted local记录已收到确认的时刻，没有伪造更早的UI点击时间。该时间也不用于历史provider publication/finalization。

## 6. 实际写入与时间

正式production DB：`D:\文档\xs\football_runtime\v1.1.0\db\production.sqlite`。

| 项目 | 结果 |
|---|---|
| application_id | **1697215811**，保持原值 |
| physical identity | `[10015492855225488523, 2251799814421351]`，保持原值 |
| migration head | **7d96abc3840f** |
| DATA_BINDING ID | **`165b9b7c-08c8-503a-89ac-f49f559e7197`** |
| DATA_BINDING hash | **`8ef97d042be818cdbb7b2b18e628d91aa8ce7cb6fc469b588760cfa21e5f7dc9`** |
| 本地admission封存观察时间 | `2026-09-23T03:18:44.993708+00:00` |
| 完成后的维护回执时间 | `2026-09-23T03:18:56.592948+00:00` |
| DATA_COMPLETION ID | `be2fa3e6-65da-597d-92c4-156eed76f185` |
| Admitted facts root | **`9440470d48857e43e9607b3aaf85581087d3d40da77d337dbe099b9853cb2d15`** |
| DB SHA256（准入后） | `3b7a0d11bb961aaf722a30c58536a0b6c3e9098a57b08759bd0294e75c5c4dee` |

`ofp_phase_slots`仅有DATA_BINDING和DATA_COMPLETION。全图检查确认：matches=612、match_results=599、ofp_source_records=612、ofp_canonical_entities=24；所有612原始JSON records均保留并核验pointer/hash/inclusion。

**training cutoff未创建。** 不能把admission时间、回执时间或当前时钟擅自填作已封存cutoff。后续若获授权继续，须在完整admission之后使用repository trusted clock记录新的selection/training cutoff，并校验未来target kickoff严格晚于它。

## 7. 备份与operator维护状态

### 变更前正式backup

使用Git v1.1.0的原始4文件operator glue，未改其行为；以安装中原operator hash验证仍为6c859ab273fe的两库，再调用正式`Operator.backup()`。

- 目录：`D:\文档\xs\football_backups\v1.1.0\74e908e63ada\`。
- manifest SHA：`007a99af3f796586e7f4e02014dce583f8bd4d9135bb39dc88302b1debf1ba3e`。
- snapshot SHA：`b74f51c0850bacd12ca37a81a0ab0df58e8f416fc5d5dff537abad5173ab2bdd`。
- COMPLETE、安装ID、application_id、head、integrity/FK及全部文件hash核验通过。

### 准入后维护backup

- 目录：`D:\文档\xs\football_training_evidence\openfootball_stage3_20260922\production_v1\post_admission_backup\`。
- 方式：SQLite native backup + 明确evidence allowlist；独立`OPENFOOTBALL_POST_ADMISSION_MAINTENANCE_BACKUP_V1`，不冒充旧DAILY_OPERATOR_BACKUP。
- manifest SHA：`a22dee82b82383d18ed67cb5dcf005bd0d9a04d71bc4cbed7f8ab0695bf81ebb`。
- snapshot SHA：`8969cac833c2a05448b42abc0b09ae531bbe5420373d05f3927f013f77625320`。
- COMPLETE、snapshot integrity/FK、7d96abc3840f、612/599及每个被复制evidence文件的原始bytes/hash全部通过；源DB未改变。

Synthetic库保持原head6c859ab273fe及SHA `651a4f627a243df0a17fda74cd2e66bc0387d150326767b6b983dde665c9e1f1`。

**当前daily operator为维护/待部署审核状态：`OPERATOR_IMPLEMENTATION_CHANGED_REVIEW_REQUIRED`。** 它的安装manifest仍钉住原v1.1.0 glue/hash，且旧HEAD常量为6c859ab273fe；本分支新增的freeze hooks与production 7d schema不能冒充已发布1.1运行环境。未改写operator-install.json或backup installation.json来绕过门禁，未迁移synthetic或创建第二个production库。后续部署/安装身份处理须随软件审核决定。

## 8. 逐项模型产物状态

| 用户要求报告的字段 | 本轮实际状态 |
|---|---|
| exact source records / exceptions / admitted facts | **612 / 13 / 599，已真实提交** |
| canonical mapping root / window hash | 已记录，见第4节 |
| cutoff | **NOT_CREATED** |
| pilot plan / run / report / attestation IDs | **NOT_CREATED** |
| training manifest ID/hash | **NOT_CREATED** |
| model approval ID/hash | **NOT_CREATED**；source review不得冒充model approval |
| production release ID/hash | **NOT_CREATED** |
| training_data_hash / state_hash | **NOT_COMPUTED**；admitted facts root不替代Elo hash |
| target-plan ID/hash / target scope | **NOT_CREATED / NOT_PROVIDED** |
| source_analysis_id / model_state_id | **NOT_CREATED** |
| model authority hash | **NOT_CREATED**；本轮仅有source authority文件/pins及source review hashes |
| deterministic replay | 真实source/identity/UTC/599事实重放PASS；Elo state replay仅在synthetic OP13/OP19测试中PASS，真实pilot未执行 |

历史availability、Brier、LogLoss、calibration继续UNAVAILABLE / UNPROVEN_HISTORICAL_VERSION_TIME，metrics=null；这不是数据资格失败或模型效果结论。

## 9. 完整相关测试与门禁

**1237个不同pytest cases：1236 passed、1 skipped、0 failed。** 按最终完整JUnit去重，不累计重复运行和中断的半次测试。

| 分组 | 结果 |
|---|---|
| OP01–OP20 + 裸训练隔离负例 + 新migration + 原OF01–OF20 | **92 passed** |
| 旧Sportmonks/native/observed/strict evidence/Elo/quant/production inference/audit单元回归 | **692 passed / 1 skipped** |
| 旧observed admission、quant workflow/findings、production persistence/inference/audit、real bridge、observed/production CLI集成与E2E | **452 passed**，隔离进程完整执行 |

OP01–OP20全部PASS，覆盖306/306、1/12、无静默例外更改、24映射完整、source hash/Berlin/null时间、599facts、固定roles/zero-fact、全部target exclusion、no tuning、deterministic Elo、exact retry、authority、精确review hash、历史metrics unavailable、manifest与release完整性、旧V1/Sportmonks回归。

额外负例证明：剥离标签或提供旧binding均不能使OPENFOOTBALL结果进入旧裸训练；SQL直接插入legacy training facts也被拒绝。

唯一skip：`tests.unit.test_training_evidence::test_posix_directory_swap_cannot_follow_a_new_symlink`，Windows不适用POSIX dir_fd/O_NOFOLLOW。未抑制原有SQLite expression-index reflection与deferred-FK cycle warnings。

早期合并运行触及600/1800秒超时；首轮隔离运行因补充新数据隔离保护停止。最终完整452项隔离结果全部通过，早期部分结果不计为通过终态。这是完整相关选择集，**不是全仓pytest或新CI声明**。

- `ruff check src tests scripts migrations`：PASS。
- `compileall -q src tests scripts migrations`：PASS，cache写入预批准临时目录。
- `git diff --check`：PASS，Git LF/CRLF提示保留；implementation patch另做精确scope的cached diff检查。
- 冻结211-file投影、原Elo/strict/Sportmonks字节、bridge与6项数学identity：PASS。
- 真实准入后的只读全图复验：PASS，fit/predict/network在复验进程中禁用。
- 正式production DB integrity/FK：PASS；真实program/epoch/run/lock、analysis/model/release计数均0。

## 10. 交付与停止

交审目录：[upload/openfootball-production-bootstrap](../upload/openfootball-production-bootstrap/README.md)。包含：

- `production_bootstrap_final_report.md`。
- `implementation.patch`、`manifest.json`。
- `pytest-summary.json`、最终JUnit。
- `frozen-verification.json`、`admission-summary.json`。
- `openfootball_canonical_mapping_review_v1.json`确认投影。
- **openfootball-production-bootstrap-review.zip**。

原始match datasets、包含599行事实的私有admission artifact、原始rights/authority/review材料与DB/backup不进入public Git或公开交审ZIP。上述交付只含代码、公开文档、聚合计数、明确ID/hash和mapping确认投影。

```text
FINAL_STATUS=PRODUCTION_BOOTSTRAP_BLOCKED
USER_CONFIRMATION=CONFIRMED_PRELUDE_ADMISSION_THEN_BLOCKED
SOURCE_RECORDS=612
DECLARED_EXCEPTIONS=13
ADMITTED_FACTS=599
REAL_ELO_TRAINING_CALLS=0
PILOT_RUNS=0
MODEL_APPROVALS=0
PRODUCTION_RELEASES=0
REAL_MODEL_PINS=0
REAL_PROGRAMS_EPOCHS_RUNS_LOCKS=0
REAL_PROSPECTIVE_OBSERVATIONS=0
THE_ODDS_API_BUSINESS_HTTP=0
NEW_HISTORICAL_DATA_ACQUISITION=0
AUTO_BETTING=NO
```

停止于用户明确批准的前置准入边界，等待网页GPT审核未来target材料、后续bootstrap执行与软件部署安排。
