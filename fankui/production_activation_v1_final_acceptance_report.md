# Production Activation v1 — Final Acceptance / v1.1.0 Release Closeout

## 1. Review acceptance and release identity

**Bridge Review APPROVED / Production Activation Implementation Review PASSED — APPROVED。无 implementation blocker。**

| Identity | Accepted value |
|---|---|
| Approved bridge design SHA | `8b1cdf727c14510e590450406d89a9edae7ff759` |
| Approved implementation SHA | `b2544464055b0fda954b1fbed299f5d749dae6a0` |
| Approved implementation tree | `6942954d9686b75543749fa4c918aec81bddf49c` |
| Frozen base | v1.0.0 / `6d633d4425d5fd6d93918d60526f701a59020b3d` |
| Implementation candidate CI | [#41 SUCCESS](https://github.com/ruhua-xu/football/actions/runs/35502941119) |
| Formal software release | **1.1.0** |
| Migration head | **6c859ab273fe**, parent 5b748fa162ed |
| Accepted rb schema | 64 tables / 261 triggers |
| Release branch | `feature/production-activation-v1` |
| Bridge implementation identity | `aa55cbe20e31b6ea1b4f4bc1f723832ae34474b85b02222c6d05e66e4bb4b11b` |

**1.1.0是Production Activation软件能力及公开real-bridge接口的发布，不是新预测算法版本。**
上述implementation SHA的业务行为已经冻结。Closeout只改变版本、版本校验/目录期望、合同状态/文件名、wheel资源/版本期望和发布文档。没有为release新增业务migration。

正式合同：[real bridge](real_prospective_activation_bridge_v1_contract.md)、[pre-lock replacement](pre_lock_replacement_v1_contract.md)、[daily operator](daily_operator_v1_contract.md)。各文件接受状态与release identity单独更新，技术正文逐字保留获批版本。正文中的设计阶段状态/旧版本基线由其接受记录解释，不重新定义业务语义。

本报告不打包进wheel；wheel增加三份正式合同，显式资源为**86**。Final feature/tree/main/annotated-tag object/tag-target SHAs、三个CI URLs和最终wheel SHA由独立release回执绑定，避免递归记录自身commit或wheel hash。

## 2. Operational state and interpretation

```text
MODE = INPUT_PREPARATION
REAL_PROVIDER_HTTP during release = 0
LLM_API_HTTP = 0
REAL_PROSPECTIVE_OBSERVATIONS = 0
REAL PERFORMANCE = INSUFFICIENT_PROSPECTIVE_SAMPLE
AUTO_BETTING = NO
```

发布成功不自动初始化runtime，不创建真实program/anchor/epoch/lock，也不授予source rights、credential或model使用权限。软件验收的real-type对象保留`SYNTHETIC_SOFTWARE_ACCEPTANCE`及隔离测试时钟/数据库，不能计为真实样本。

Production Activation软件发布**不证明ROI、alpha、P_llm改善或P_final优于P_base**。所有描述性概率/资金指标均保持原解释与样本限制。

## 3. RA01–RA21 accepted evidence

以下保存已通过Implementation Review的证据边界；1.1.0 final local gates已完整重跑对应用例，独立结果记录在第6节和发布回执中。

| Case | Accepted proof |
|---|---|
| RA01 | 新real analysis仅接受REAL_SOURCE_DATA及THREE_WAY，错误market/身份/source ref和外部analysis注入拒绝 |
| RA02 | V1 synthetic工件不可重标；独立real schema、typed references及完整来源重放拒绝伪造来源 |
| RA03 | 只有program、合法pin和独立policy/anchor即可bootstrap；无matches、tickets、V1 analysis或Strategy seed也能验证配置链 |
| RA04 | Anchor created/sealed与epoch publication严格早于future start；用户事件时间字段、晚anchor和缺pin拒绝 |
| RA05 | 缺失/失效pin阻止可运行epoch；合法epoch后的模型失效/target不可用具有明确availability fact |
| RA06 | P_quant不可用保持null；整桶UNAVAILABLE保留全部成员，不复制P_market、不伪装NO_BET |
| RA07 | The Odds API current h2h request/capture、mapping、完整constituents、MEDIAN及原P_market逐项重放，等数量篡改仍拒绝 |
| RA08 | Reviewed Sporttery V2 document/evidence/provenance、membership、quotes及身份重放；错误/缺失/晚到输入拒绝 |
| RA09 | 精确UTC分桶；等价时区归一化，一秒或一微秒差异分开，成员不按收益/票据重组 |
| RA10 | Cutoff/receipt来自repository；已知赛果、回填、deadline越界、长publication及crash均fail closed |
| RA11 | 新packet绑定真实analysis；V4 wire、absolute P_llm、场景/abstention/cap原义保留，旧packet与UNKNOWN伪确认拒绝 |
| RA12 | R0→R1→R2全链保留，新analysis/packet/cutoff；只有最后active PREPARING head可锁 |
| RA13 | 多连接replace/replace与replace/lock只有一个head消费；事务中断无半图，exact retry重取原receipt |
| RA14 | Program+canonical match+THREE_WAY跨slate/bucket/epoch仍唯一；合法post-lock替代只原子推进同slot版本 |
| RA15 | 旧V1 synthetic、REAL拒绝门禁、seed guard、V4/Strategy/Return/lock/report及goldens继续冻结回归 |
| RA16 | 真正git archive v1.0.0代码生成populated旧库；全部旧定义/trigger/rows/artifacts保持，空库往返与populated rb拒绝通过 |
| RA17 | 每张已填充rb表拒绝UPDATE/DELETE/REPLACE；typed refs、完整seal、schema/guard和一致重封metric攻击检测 |
| RA18 | 测试网络/rebuild禁止；test clock不能登记真实program，测试输入不能提升为真实performance |
| RA19 | 原normalizer、MEDIAN、Elo/base/fusion、EV、Strategy、Return/objective、payout/metrics复用；真实两场数值图与原kernels等价 |
| RA20 | 隔离installed-wheel完整生命周期、输入/候选顺序、PYTHONHASHSEED、Decimal、exact retry、restart确定性 |
| RA21 | Lock时重查head、epoch、admission expiry/revoke、model、source invalidation/replay、canonical kickoff、已知结果、lead及全部slots |

主要用例：`tests/integration/test_real_bridge_repository.py`、`test_real_bridge_upgrade.py`、`test_daily_operator.py`，及原有完整回归。Installed-wheel scaffold为`scripts/real_bridge_acceptance.py`，完整wheel入口为`scripts/wheel_e2e.py`。

## 4. Detailed proof boundaries

### Real analysis / model-unavailable

Real contracts与旧synthetic类独立；不使用model_construct、重标旧工件或宽化旧validator。The Odds API的规范化市场事实与manual Sporttery报价具有独立binding、hash、完整membership/provenance和准入链。Model pin只引用已有合法approved production binding/release/state及原target scope；live pin不复制state/training facts，预测只用已pin state。原授权reader的sealed-proof校验不创建新训练state或参数。

没有合法可读state时，不从market/SP伪造P_quant。不足以形成合法base/final的unit使整个exact bucket UNAVAILABLE；正当NO_BET仍仅由冻结数值规则产生。

### Epoch bootstrap / no-lookahead / exact kickoff

Anchor独立于任何比赛/票据seed，封存program/scope、implementation、policies、model pin、budgets与future window。Repository控制created/sealed/prepare/cutoff/receipt/lock时间；严格`created <= sealed < start < end`，publication时再次检查。

Slate declaration与原fixture observations定义固定canonical成员。桶使用完整精度UTC kickoff；prepare、replacement和lock保持整桶，`locked_at + 60s < kickoff`。已知结果优先于过时赛程，真实到达/ingestion与source发布时间分离。历史fixture/result watermark阻止同一UTC稍后入库事实污染原预测。

### PRE_LOCK race / official prediction uniqueness / DecisionLock V2

BEGIN IMMEDIATE内核验expected head并生成新analysis/packet/run与replacement事件；`parent_run_id` PK仲裁REPLACE XOR LOCK。旧head不能复活，race失败不自动跟随新head。多个match slots同事务取得，有一个冲突则全部rollback。

Official slot key不含epoch/slate/bucket/run。真正赛前post-lock revision必须同epoch/anchor/bucket/成员，失效旧lock与连续slot successor一起封存。Lock重新验证当前输入/权限/fixture/model/epoch/结果/lead；普通新增信息本身不自动撤销旧预测，显式失效会阻止新锁定。检查覆盖计算及实际图publication窗口。

### Frozen V4 / source replay / append-only / census

原ANALYSIS_PACKET_V4与LLM_REVIEW_V4字段、absolute P_llm、scenario/counter-scenario、MODEL_UNAVAILABLE、abstention及correction cap保持。独立sidecar提供typed evidence reasons；UNKNOWN lineup不是confirmed，UNKNOWN absence不是无伤停。

闭集typed graph把dict refs、scalar-ID refs、原math leaves和具体旧source复合键完整投影。Canonical JSON、hash、完整children/seals与关系均验证，读取重建原closed request与数学。声明/运行可见性在加载前按program/epoch限定；报告用完整census、as-of和receipt watermark，不筛选成功案例。Result revision追加settlement/report，旧报告仍按原边界重放。

Schema包含SQLite表达式唯一索引；因Alembic不能反射它们，另逐条验证SQL定义。表级独立约束的等价排序不改变约束本身。Populated rb downgrade不能删除任何新旧历史。

### Genuine v1.0.0 → 1.1.0 upgrade

`test_real_bridge_upgrade.py`用`git archive v1.0.0`、原1.0.0包和原迁移生成真实populated库，验证旧import实际来自该archive；1.1.0升级后比较全部旧SQL definitions/triggers/rows/serialized artifacts，重放旧report，并验证新数据存在时回退拒绝。不是用1.1代码伪装旧head。

## 5. Frozen identities

| Identity | SHA-256 |
|---|---|
| Return algorithm | `76995ed699a8d054f3865ffbbc0063a46af2123f18ab34aa6b27a91690dc7aa6` |
| Return policy | `bd9cf8b2c3d36ce13327cb49b9513c4d6c995762387606ce26bba37ef0769b47` |
| Return objective | `9bb39a771ac2390cb537030c809a7e214dcf88bcce2be17808bc7b3dcbfe7c30` |
| V1 prospective implementation | `6b2a73c14720c60eb30f9bc5d4158bccf095d397748cd1157eb53f13933ddb1f` |
| V1 prospective policy | `06f9cb5289959cf6387b324f9a8ef617d1960a68e88e23cb1423e0c15ef847f8` |
| MEDIAN/source identity wrapper | `c6c22bfb8a2c53aeb624b485c73c6e144c65f1a3cd8b6df2d7c5f32a88cc914f` |

ELO_THREE_WAY_BASELINE_V1、POISSON_GOALS_BASELINE_V1、MARKET_CONSENSUS_MEDIAN_V1、LLM_REVIEW_V4、fusion/P_final、EV、Strategy Pass V1/V2、Return Distribution V1、ReturnObjectiveProfileV1、payout和Settlement数学均保持。Bridge identity也必须继续等于第1节接受值。

## 6. Final release gates and platform accounting

1.1.0 final门禁必须独立重跑完整pytest、Ruff、compileall、RA01–RA21、fresh/check、empty down/up、genuine populated v1.0升级、populated rb refusal、wheel build与隔离installed-wheel、input/candidate order/hash seed/Decimal/retry/restart、diff check及secret-pattern scan。

**1.1.0完整final local gates已通过。** 本轮重新执行全部测试，未复用已接受candidate的测试结论。

- Windows / Python 3.13：**2809 passed / 1 skipped**；2810 collected/verified unique nodes，132个不重叠分组，0 failures、0 errors，`complete=true`。
- Windows唯一skip：`tests/unit/test_training_evidence.py::test_posix_directory_swap_cannot_follow_a_new_symlink`；原因`POSIX dir_fd/O_NOFOLLOW validation`，该原生POSIX路径不适用于Windows。
- Local tested tree：`df53788748f4b51d23824e8ea1cf3bd5783d73c3`。测试后只回填本报告的实测结果，业务代码、正式合同与wheel输入文件不变；FINAL candidate CI另验证最终实际commit/tree。
- Final wheel：`football_system-1.1.0-py3-none-any.whl`，**1075453 bytes / 86 resources**。
- Final wheel SHA256：`6ea95c4abe79601a5c34a9b8607c8458d20e5eb612785cc876a04a1a562c0594`。
- 独立输出目录、offline wheelhouse与`--no-index`完成构建/隔离安装。历史路径、V4/Strategy、Return、prospective与real-bridge全链路E2E通过。
- Installed real bridge在PYTHONHASHSEED 1/987654、Decimal 28/6及反向输入顺序下生成相同完整artifact graph；candidate order、exact retry、crash/restart验证均通过原对应回归。

| Gate | Final local result |
|---|---|
| pytest / RA01–RA21 / all previous regression | PASS：2809 passed / 1 platform skip |
| Ruff / compileall | PASS：完整src/tests/migrations/scripts |
| fresh migration/check | PASS：head 6c859ab273fe，无新增schema revision |
| empty downgrade/re-upgrade | PASS：旧head往返后重新check |
| genuine populated v1.0.0 → 1.1.0 upgrade | PASS：全部旧definitions/triggers/rows/serialized artifacts保留 |
| populated rb downgrade refusal | PASS：拒绝后完整数据库快照不变 |
| wheel build / isolated installed-wheel E2E / determinism | PASS：1.1.0 / 86 resources，完整旧/新路径 |
| git diff --check / secret-pattern scan | PASS：14个closeout文件及wheel共287项、12类patterns、0 findings |
| release-only / frozen proof | PASS：363份已审核业务文件字节不变，3份合同技术正文不变，bridge及冻结数值hashes不变 |

Final local gate使用短Windows TEMP根，保留完整collection、JUnit和每组回执；本轮没有因路径长度而失败/重试，也没有为了通过门禁修改业务行为。SQLAlchemy旧cyclic-FK排序及SQLite表达式索引反射warning保持可见，表达式索引定义另外精确核验。

Linux / Python 3.12分别从新的FINAL candidate、main、tag CI的实际Test日志提取passed/skipped及逐项原因，保存于对应发布回执，不用旧#41计数替代。平台区别是Linux不运行原生Windows handle/reparse-point验证，而Windows不运行原生POSIX dir_fd/O_NOFOLLOW验证。

Windows local与Linux CI的passed/skipped分别记录，不能把一种平台的skip原因套到另一种。完整JUnit/日志、测试唯一性与所有结果由最终release回执绑定；Linux三条CI必须针对各自精确ref/SHA且所有required steps均成功。

## 7. Known limitations and go-live prerequisites

- 当前只是软件release；真实观测数仍0，真实performance不足。Local clock/人工review不是外部公证，source publication/version未知保持UNKNOWN/UNAVAILABLE。
- 首个real slice仅THREE_WAY，一个run一个精确kickoff桶；不支持同场跨market joint、不声明现实比赛独立性。Objective仍未校准，marginal optimizer不是global optimum。
- 只读已有合法pinned model及已批准target；没有pin、expired/revoked或必需source replay不可用即拒绝/UNAVAILABLE，不恢复restricted history、不训练新模型、不修改参数。
- 原fixture、quote、evidence、result许可/retention边界及工作量/金额/规模限制保持；无法验证的历史不能宣称full audit pass。Missing/unsupported/VOID不猜测资金结算或当输。
- SQLite-only；准备入口保持双库/文件身份、manual input、单写者/intent/receipt与受控backup边界。没有force、skip、backdate或自动恢复生产库入口。

发布完成后保持INPUT_PREPARATION。Runtime初始化、source rights/admission、The Odds API credential admission、Sporttery/evidence/result admissions、合法existing model pin、real observation program、sealed anchor与future real epoch，等待独立 **PRODUCTION ACTIVATION GO-LIVE CHECKLIST**。不自动购买provider、不自动下注。

## 8. Publication sequence

完整local gates与release-only diff通过后，commit/push feature并等待新的FINAL candidate CI Success；保留实现历史、非squash/non-rewrite合入main，等待main CI Success；创建指向最终main release commit的annotated `v1.1.0`，等待tag CI Success。已发布v1.0.0 tag不变。

最终发布回执记录implementation SHA、feature SHA/tree、main SHA、annotated tag object SHA、tag target、三条CI URL/status、package/migration、平台测试计数/skip、RA01–RA21、wheel资源/SHA、冻结hashes、secret scan及upgrade proof。完成1.1.0 release后停止。
