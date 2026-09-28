# OPENFOOTBALL ELO BOOTSTRAP ADAPTER — STAGE 3

日期：**2026-09-22**。最终状态：**STAGE3_BLOCKED**。

独立OpenFootball preparation lane、四文件精确获取、数据资格检查和OF01–OF20已完成。阻塞来自**12条未证实FT时段的direct arrays、1条awarded、未完成canonical/正式审核以及精确窗口确认**；不是重新否定OpenFootball CC0许可。

## A. 基线、分支与冻结

| 项目 | 结果 |
|---|---|
| 正式base / HEAD | `5ed940a8af8077be80549603a2da38aea77fc1bf`（v1.1.0） |
| 新分支 | **feature/openfootball-elo-bootstrap-v1**，从上述正式base创建 |
| 本轮implementation身份 | 工作区additive文件 + 交审包implementation.patch与manifest中的SHA256；没有把base HEAD称作已提交的实现commit |
| main / origin-main | 保持正式base |
| release version / tag | 本轮未选新版本、未改tag；package仍1.1.0 |
| migration | head6c859ab273fe，未增加migration |
| Elo | ELO_THREE_WAY_BASELINE_V1，model version=1，BASELINE_UNCALIBRATED |
| Elo参数 | initial_rating=1500；k_factor=20；home_advantage=100；season_regression_factor=0.75；draw_probability=0.25；minimum_prior_matches=5 |
| config_hash | `c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4` |

冻结验收通过：v1.0原有211文件投影、v1.1已跟踪源码/config/migration/operator/package、bridge hash与6项既有冻结identity。Fusion、P_final、EV、Strategy、Return Distribution、objective、Settlement均无改动；未将P_market充当P_quant。

## B. 精确数据获取

Repository：**openfootball/football.json**。
Commit：**40b3e1b7391932d133287115106304444bf297e1**。

| 文件 | bytes | SHA256 |
|---|---:|---|
| `2024-25/de.1.json` | 88232 | `f473d6595e6c29ebedc08b09177aed6a6b2ff0fa8378b82f51dea469139f44c3` |
| `2025-26/de.1.json` | 88247 | `17d0999db6281e6365823acdbce41a4f01dd469eac63e1f897e4e47c02906672` |
| `README.md` | 6330 | `aadc464ae476ba4d5467785f6f2be435d1a583f589f4b2b0617fdf86fc18e29c` |
| `LICENSE.md` | 6555 | `36ffd9dc085d529a7e60e1276d73ae5a030b020313e6c5408593a6ae2af39673` |

4次HTTP、4次成功、0重试/重定向，合计189364bytes。URL、commit/path和每文件真实capture_at_utc见[数据资格报告](openfootball_bundesliga_data_qualification_v1.md)。

独立私有根：`D:\文档\xs\football_training_evidence\openfootball_stage3_20260922\`。四个原始文件、完整逐行qualification、scope/rights/mapping候选均在该根。交审包仅放代码、文档、hash、聚合计数和异常pointers；不含raw match dataset或旧受限数据。

另查阅了upstream spec/generator源码解释score语义；这些是文档研究，不是新增比赛数据集。固定IANA软件依赖安装在独立临时dependency目录，未改正式wheel环境。

## C. 数据质量与schema exceptions

| 项目 | 2024/25 | 2025/26 |
|---|---:|---:|
| raw / unique matches | **306 / 306** | **306 / 306** |
| date / time完整数 | 306 / 306 | 306 / 306 |
| score.ft / direct array / missing | 306 / 0 / 0 | 294 / **12** / 0 |
| duplicate / malformed | 0 / 0 | 0 / 0 |
| team count | 18 | 18 |
| matchdays | 1–34，每轮9条 | 1–34，每轮9条 |
| date min/max | 2024-08-23 / 2025-05-17 | 2025-08-22 / 2026-05-16 |
| schema exceptions | 0 | **12** |
| abnormal status | **1 awarded** | 0 |
| 普通FT结构候选数 | 305 | 294 |

这些是文件实际解析计数，不是赛制推算。两季保留16队、移出Holstein Kiel/VfL Bochum 1848、加入1. FC Köln/Hamburger SV，并集20队。

2024/25 `/matches/120`的awarded结果未当作常规时间最终赛果。2025/26的12个array pointers及上游证据在数据报告中完整列出。上游`Score.build(Array)`创建`reported`，其注释明确period可能未知/FT/aet或行政结果，不能证明FT等价；因此标记**SOURCE_SCHEMA_EXCEPTION**，没有静默转换为`score.ft`。

全部612条来源行保留。305+294不是已选训练集，13条阻塞记录没有被静默删除或用另一数据源替换。

## D. 实现与时区政策

新增独立：

- `OPENFOOTBALL_CURRENT_SNAPSHOT_SCOPE_V1`
- `OPENFOOTBALL_JSON_ADAPTER_V1`
- source capture、rights candidate、canonical mapping plan、record provenance和historical-validation类型。
- 私有四文件qualification边界及`python -m football_system.interfaces.openfootball_cli`入口。

新scope显式provider=OPENFOOTBALL、competition=BUNDESLIGA，同时固定SOURCE_TIME_RESEARCH、retrospective=true、CURRENT_SNAPSHOT_OBSERVED。旧Sportmonks/V1路径原封不动；新候选不会通过旧V1 recorder/release writer取得生产资格。

timezone policy：**Europe/Berlin / tzdata2025.2 / IANA2025b**。
TZif SHA256：`a7fd9932d785d4d690900b834c3563c1810c1cf2e01711bcc0926af6c0767cb7`。
Policy hash：**`a237ea1875560ccbbc15efa3102463f025baa825be04004863a1e5a4c9e36f75`**。

直接从固定TZif加载，不依赖host timezone；CET/CEST与DST边界已测试。缺time保持UTC=null；不存在/歧义local time拒绝。原始local text、minute精度、pointer、file SHA、commit、filename和policy hash全部保留。

provider publication/finalization始终null/UNKNOWN，既没有用Git/download time，也没有用kickoff+2h填充。

完整规则见[adapter契约](openfootball_current_snapshot_adapter_v1_contract.md)。

## E. Canonical / rights / 历史验证

| 项目 | 当前结果 |
|---|---|
| Mapping plan | 20个team、1个competition、2个season，共23项显式候选；实际canonical IDs与catalog证据全部待审核，**IDENTITY_UNRESOLVED** |
| Scope候选content hash | `c240a70d97f5b0c7d2fcc1ddff28cbcf508026d4cbbe751ff0b832fdd0c3b5e8` |
| Mapping plan content hash | `42ff15a62c2d7a7a9a59aff8df5efca5c435049da9583e296caa38be87eb8e75` |
| OpenFootball许可/有限获取 | **APPROVED FOR LIMITED ACQUISITION AND BOOTSTRAP PREPARATION**；CC0原文及固定文件hash已取得 |
| 正式rights candidate | 已生成，绑定7项候选用途；content hash `af891bd283ada59bbfcd5b1e28624f5381d7390057d2c4cba47d096b007fb14b` |
| Authority / review | 未创建正式pins/review/production grant；candidate不是已批准artifact |
| Reviewer政策 | operator本人可以审核，需trusted pins真实覆盖其身份和精确source/schema/time范围；不强制外部律师或第三方 |
| TRAINING DATA VALIDITY | 当前有源异常及未完成身份/审核；不能直接训练 |
| HISTORICAL PREDICTIVE VALIDATION | **UNAVAILABLE / UNPROVEN_HISTORICAL_VERSION_TIME**；availability、Brier、LogLoss、calibration metrics=null |

历史时间未证实不是永久禁止未来合法快照bootstrap的理由。实际training validity、内部权限、窗口及后续正式绑定满足后，可由新的独立授权启动固定Elo candidate build；本轮不执行。

## F. 旧training window规则恢复

调查仅使用仍合法保留的Git文档/软件schema，未打开或恢复已删除raw/state/私有approval。

1. 在事前已接受ADR commit **`4ee7cab`** 的`fankui/decisions/0008-approved-training-history.md`中恢复到原规则：
   - `ELO_TRAINING_WINDOW_V1`独立封存competition、按真实时间顺序的season IDs、season roles和全部target exclusion。
   - 完整已结束历史赛季，优先2025/2026；**如需**warm-up才加入紧邻2024/2025。
   - 窗口/cohort/cutoff在pilot前固定，禁止基于概率指标、ROI或GPT判断选择。
2. 当前冻结`EloTrainingWindowContentV1`仍要求role顺序为`WARMUP* → PILOT_TARGET → PRODUCTION_TARGET`，最后两个season独立，target exclusion=ALL_OPERATION_TARGET_IDS。
3. `phase_6_final_acceptance_report.md`及v0.6.0公开历史记录实际接受过Bundesliga **2025/2026完整source-defined cohort**。该事实不提供已清理私有窗口的精确artifact ID/hash或完整season-role明细。

结论：**TRAINING_WINDOW_RULE_METADATA_FOUND；EXACT_PREVIOUS_PRODUCTION_WINDOW_NOT_RECOVERED**。不是“完全没有规则”，也没有从有限公开说明补造一个旧生产实例。

新bootstrap继续受以上原规则约束。两份下载文件仅为候选；本轮`training_window=null`、cutoff未设置，未自动决定启用warm-up、精确canonical season roles或按两季训练。请网页GPT确认精确窗口后再形成正式window artifact；不按本次数据表现选窗。

## G. 测试与冻结验证

### OF01–OF20

最终新合同测试：**69 passed / 0 failed / 0 skipped**。以下20项全部PASS，包含参数化和额外负例。

| ID | 结果覆盖 |
|---|---|
| OF01 | CC0/source/commit/LICENSE与README绑定；candidate不能变成approval |
| OF02 | 错provider/大小写/尾空白拒绝 |
| OF03 | exact commit/file/URL/bytes/hash、文件集合与model_copy重验 |
| OF04 | 源bytes变异及caller重写hash拒绝 |
| OF05 | duplicate JSON key、NaN拒绝 |
| OF06 | missing/malformed date拒绝 |
| OF07 | missing/invalid/time带offset等保持阻塞，无午夜补值 |
| OF08 | CET/CEST、DST前后、gap/fold拒绝 |
| OF09 | exact UTC、host独立、policy必填、tzdata/TZif变异拒绝 |
| OF10 | score.ft、ht一致性及原始provenance |
| OF11 | direct/ambiguous/malformed score拒绝；missing与0:0区分 |
| OF12 | extra time/penalties/aggregate/cup/awarded/abandoned拒绝 |
| OF13 | 同赛季重复/冲突fixture全部标记，无静默去重 |
| OF14 | 未解决alias、缺canonical证据、队名冒充team_id拒绝 |
| OF15 | retrospective/source-mode/basis保持，禁止填入训练窗口 |
| OF16 | publication/finalization只能null |
| OF17 | 不允许历史metrics或PASS声明 |
| OF18 | 文件/映射输入顺序确定性，record语义确定性与原始byte谱系分别校验 |
| OF19 | PYTHONHASHSEED=0/1/42/12345一致 |
| OF20 | 旧Sportmonks/V1文件精确回归及211-file frozen projection |

额外覆盖：private输出不覆盖/public Git拒绝、CLI无training入口、future capture与unknown fields拒绝。

### 旧路径定向回归

**648 passed / 1 skipped / 0 failed**，覆盖：

- `tests/contract/test_sportmonks_observed.py`
- `tests/unit/test_observed_training.py`
- `tests/unit/test_observed_integrity.py`
- `tests/unit/test_training_evidence.py`
- `tests/unit/test_elo_baseline.py`
- `tests/unit/test_quant_integrity.py`
- `tests/unit/test_production_release.py`

唯一skip：`test_posix_directory_swap_cannot_follow_a_new_symlink`，Windows不适用POSIX dir_fd/O_NOFOLLOW分支。Windows safe-open等其他相应用例已执行。

合计**718个不同pytest cases，717 passed / 1 skipped**，不冒充全仓suite或新CI。首次OF测试67 passed的XML保留；补充两个负例后最终为69，汇总没有重复累计首次结果。

### 其他验证

- Ruff PASS；4个新源码module内存compile PASS。
- 真实四文件qualification在四个PYTHONHASHSEED下重复计算，均为 **`65bc03c8d16b9ce7b644912a6a270eac96e3259b0897fe2044a0b3103de18115`**。该重放禁用fit/predict与网络，不是训练。
- 211份旧冻结投影、bridge hash、6项数学identity、Elo config通过。
- 两个正式runtime DB hash均与Stage 1相同，head6c859ab273fe、integrity ok/FK empty；model/state/release/binding及真实program/anchor/epoch/run/lock等计数全0。
- 正式`.venv`在fresh `-I`解释器中模块/metadata均1.1.0。Source-checkout测试显式加入src后，旧未跟踪egg-info可显示0.8.0 metadata；首次维护核验因此触发断言，最终改为隔离核验installed distribution并分别记录。未删除该用户文件或重装正式环境，新功能只在source准备上下文测试。

冻结验证回执时间：**2026-09-22T08:54:07.974233+00:00**。原始JUnit、分组pytest summary和frozen verification在[交审包](../upload/openfootball-elo-bootstrap-v1/README.md)。

## H. 剩余阻塞与停止点

1. **SOURCE_SCHEMA_EXCEPTION**：12条reported arrays没有足够FT等价证明；1条awarded需要明确异常政策/事实审核。原始文件保持完整。
2. **IDENTITY_UNRESOLVED**：23项canonical映射候选等待实际catalog身份与审核，不猜ID。
3. **FORMAL_REVIEW_PENDING**：有限获取批准已有效；source-rights/authority/review/retention的精确正式工件仍需operator真实提供和审核。
4. **EXACT_WINDOW_PENDING**：旧规则已恢复并保持；精确season-role/window/cutoff尚未定稿，两文件不等于训练窗口。
5. **PRODUCTION_INGESTION_PENDING**：本轮完成独立preparation类型/adapter，未将它们接入旧V1 production recorder/release writer；后续正式版本化绑定需在审核后处理，不能用改SPORTMONKS literal绕过。

```text
FINAL_STATUS=STAGE3_BLOCKED
LIMITED_OPENFOOTBALL_ACQUISITION=COMPLETE
OPENFOOTBALL_ADAPTER_SOFTWARE_ACCEPTANCE=PASS
PRODUCTION_TRAINING_CALLS=0
PRODUCTION_RELEASES_CREATED=0
REAL_MODEL_PINS_CREATED=0
REAL_PROGRAMS_EPOCHS_RUNS_LOCKS_CREATED=0
REAL_PROSPECTIVE_OBSERVATIONS=0
THE_ODDS_API_HTTP=0
SPORTMONKS_HTTP=0
LLM_API_HTTP=0
AUTO_BETTING=NO
```

旧回归中的模型构建仅使用明确的synthetic测试材料；没有把本次真实OpenFootball赛果送入Elo训练。没有访问/恢复Sportmonks restricted raw。

交付三份文档、implementation patch、manifest、pytest summary和frozen verification后停止，等待网页GPT审核。没有commit、main合并、tag或release动作。
