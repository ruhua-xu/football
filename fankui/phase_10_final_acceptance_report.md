# Phase 10 — Final Acceptance / v1.0.0 Release Closeout

## Architecture Review acceptance

**Architecture Review APPROVED；没有architecture blocker；ADR-0012 Accepted。**
Workspace owner明确接受以下candidate并授权release-only closeout：

| 标识 | 接受值 |
| --- | --- |
| Implementation SHA | `da4e9c518492ebe24319b871deb1872ef91fb0c9` |
| Candidate tree | `c796296101bf7881344f45d05e8a17c787dc0682` |
| Formal base | v0.9.0 / `9a869d42b44282551cbdc694d8efc6d0009e9dde` |
| Migration head | `5b748fa162ed` |
| Prospective implementation hash | `6b2a73c14720c60eb30f9bc5d4158bccf095d397748cd1157eb53f13933ddb1f` |
| Prospective policy hash | `06f9cb5289959cf6387b324f9a8ef617d1960a68e88e23cb1423e0c15ef847f8` |
| Return algorithm hash | `76995ed699a8d054f3865ffbbc0063a46af2123f18ab34aa6b27a91690dc7aa6` |
| Return policy hash | `bd9cf8b2c3d36ce13327cb49b9513c4d6c995762387606ce26bba37ef0769b47` |
| Objective hash | `9bb39a771ac2390cb537030c809a7e214dcf88bcce2be17808bc7b3dcbfe7c30` |

Reviewed candidate CI：[#37 — Success](https://github.com/ruhua-xu/football/actions/runs/35354277664)，head为上表implementation SHA。该记录与下面另行运行的1.0.0 final release gates明确区分。

## Release identity and meaning

- Feature：`feature/1.0.0-prospective-validation-production-closeout`。
- Package/runtime version：**1.0.0**。
- Migration head保持**5b748fa162ed**，parent **4a637e9051dc**；closeout不新增migration。
- 已接受持久化图：30张`pv_*`表、126个triggers；wheel显式资源清单为**81**。本最终报告不新增runtime资源。
- v1.0.0表示**Prospective Validation / Production Framework软件完成**。
- 最终feature/main/tree/tag object/tag target SHAs与三条release CI URLs由最终发布回执绑定，避免在本报告中递归记录自身commit hash。

**REAL PERFORMANCE: INSUFFICIENT_PROSPECTIVE_SAMPLE**

**PRODUCTION DECISION ADAPTER: UNAVAILABLE**

**Reason code: PRODUCTION_DECISION_ADAPTER_UNAVAILABLE**

**PROVIDER / LLM HTTP: 0**

本次发布不证明真实ROI、alpha、P_llm改善或P_final优于P_base，不表示真实production activation。Synthetic acceptance不得重标为真实performance evidence。

## A–Z acceptance retained

| Case | 已审核软件验收证据 |
| --- | --- |
| A | 赛前prepare/lock，所有相关kickoff与固定safety lead均通过 |
| B | 晚锁、伪造事件字段、clock regression拒绝，无lock receipt |
| C | Lock/receipt/typed graph的UPDATE、DELETE、REPLACE拒绝 |
| D | Replacement lock与旧run invalidation同事务，赛前满足新旧deadline；赛后拒绝 |
| E | Result observation单链追加，旧结果/settlement/report可重放 |
| F | Freshness按source publication计算；stale证据拒绝，ingestion不能补造fresh |
| G | UNKNOWN/EXPECTED lineup不能变成CONFIRMED，sidecar需明确完整证据 |
| H | UNKNOWN absence不是无伤停；injury与ban区分，不能自动推断active伤停 |
| I | Manual实际文件SHA、contained path、reviewer/source/rights声明与retention；expired exact retry不重新读raw |
| J | Provider capability未激活时明确UNAVAILABLE，不发HTTP |
| K | Schedule/rest/form按window、team/location、known-at/source IDs和cutoff校验 |
| L | 每market完整P_base/P_final与其他layer配对比较，使用相同observation交集 |
| M | Improved correction保留 |
| N | Worsened correction保留，不筛掉失败案例 |
| O | Abstained correction保留，neutral及unavailable显式统计 |
| P | NO_BET保留cash；缺失/unsupported结果不假算输 |
| Q | 正投入锁定分配与冻结payout/return/Settlement路径一致 |
| R | Gross>0、break-even、2x、3x、loss、deep-loss实现事件正确 |
| S | 全epoch census与receipt watermark确定性封存；同UTC时刻后入事件不改变旧报告 |
| T | 空报告不捏造metrics、不除零 |
| U | Synthetic数量不能消除真实INSUFFICIENT_PROSPECTIVE_SAMPLE |
| V | Exact retry返回原工件；request key不同内容拒绝 |
| W | Hash、typed projection、request/source/census/math重放检测损坏，包括一致重封的错误metric |
| X | 用真正v0.9.0原代码生成populated旧库，升级后全部旧定义/trigger/rows/serialized artifacts保持 |
| Y | Populated prospective downgrade拒绝；empty downgrade/re-upgrade正常 |
| Z | Isolated installed-wheel完整prospective生命周期与公开CLI，以及既有历史/V4/Strategy/Return路径 |

用例与原始implementation report、`data/fixtures/prospective_validation_v1.json`对应。接受candidate的干净tree完整测试为2746 passed / 1 skipped；版本更新后的final gates单独完整执行，不能仅沿用candidate结果。

## Proof boundaries

### No-lookahead and atomic supersession

事件时间来自repository clock；source publication/availability、capture/verification与local ingestion分别记录。公共请求没有receipt/prepare/lock/invalidation时间override。Lock必须满足`locked_at + minimum_lock_lead_seconds < earliest relevant kickoff`。已知赛果、变动kickoff和不可用fixture明确拒绝。

旧prediction不修改。Replacement prepare引用旧run；new lock与old invalidation在BEGIN IMMEDIATE事务中原子提交，并在commit前复查时钟/截止。禁止赛后supersession、分叉及以新slate重复计入同epoch match+market。

### Provenance and freshness

MANUAL_VERIFIED_IMPORT_V1实际校验contained source bytes SHA，保留typed FACT/ANALYSIS/SPECULATION、source identity、reviewer与时间/保留边界。Lineup确认、active injury/ban所需typed证据不能被UNKNOWN替代。Freshness基于source publication与冻结category policy；UNKNOWN不能借ingestion变为FRESH。

Local clock不是外部可信时间戳公证；人工review声明不等于自动证实事实或授权。已删除/永久关闭的Sportmonks capability raw不恢复、不读取。

### Result revision, complete census, append-only and replay

Result observation只追加且必须延伸当前source/match head；source version未知时不捏造历史publication链。FT/REGULATION_ONLY才能建立normalized result；void/cancelled/缺失等不猜测payout。

新result revision不改prediction，追加新settlement。当前报告将尚未重结算的旧版本标记STALE_SETTLEMENT，旧封存报告仍可重放。报告使用全epoch可见run census与receipt-sequence watermark，没有按盈利或修正成功case过滤的入口。

Typed source FKs、deferred completeness seals、append-only triggers和request receipts共同约束存储；读取重新校验源、时序、依赖、金额/概率与census。受控离线篡改即使重新封hash也因source replay不符而失败。

### Genuine v0.9 upgrade and frozen regression

升级测试用`git archive v0.9.0`及其原始代码生成旧库，而不是用新代码伪装旧head。比较升级前后全部旧table/trigger/rows与serialized artifacts，并重放/重试旧optimizer；新增epoch后验证populated downgrade refusal。

原有127份v0.9 domain/application/config/migration Git blob bytes保持不变。已审核candidate的全部业务实现、测试、配置/fixtures与迁移保持；唯独正式wheel/version expectation属于获准closeout差异。五个algorithm/policy/implementation hashes必须逐项等于本报告顶部接受值。

## Final local release gates

**1.0.0完整final local gates已通过。** 以下为版本更新后的独立实测结果，不是复用candidate门禁：

- 干净发布tree完整pytest：**2746 passed / 1 skipped**；2747 collected/verified unique nodes，129个不重叠分区，0 failures、0 errors，`complete=true`。
- 唯一skip：Windows不支持的POSIX `dir_fd/O_NOFOLLOW`验证，`tests/unit/test_training_evidence.py:292`。
- Local tested tree：`7c6dd6c2bbd3f9b77e712c410226d2762d597494`；测试后只补充本最终报告的实测记录，业务代码及wheel所含文件不变。Final candidate CI另外验证实际发布commit。
- 完整pytest回执：`release10-pytest-v2/summary.json`，最终发布材料提供其副本`release10-pytest-summary.json`。
- Final wheel：`football_system-1.0.0-py3-none-any.whl`，**987321 bytes**，**81 resources**。
- Final wheel SHA256：**`28eee6a96464efdc8c54d9282d345d2a781d25fcb94a9d12f81d28de7a1127ec`**。
- Wheel使用独立输出目录、本地offline wheelhouse与`--no-index`构建/隔离安装；完整historical、V4/Strategy、Return Distribution与prospective E2E全部通过。

| Gate | Final local result |
| --- | --- |
| pytest / A–Z / frozen regression | PASS：2746 passed / 1 platform skip |
| Ruff / compileall | PASS：完整src/tests/migrations/scripts |
| fresh migration/check | PASS：head 5b748fa162ed，无schema差异 |
| empty downgrade/re-upgrade | PASS：重新升级/check通过 |
| genuine v0.9.0 populated upgrade | PASS：旧定义/trigger/rows/serialized artifacts逐值保持 |
| populated prospective downgrade refusal | PASS：拒绝后新旧已存工件仍可重放 |
| wheel build / isolated installed-wheel E2E | PASS：1.0.0 / 81 resources，全部既有路径与prospective链路 |
| git diff --check / secret-pattern scan | PASS：10个closeout文件及256个wheel members，12类patterns，0 findings |
| reviewed candidate → closeout diff | PASS：仅10个允许的release-closeout文件，无业务逻辑修改 |

重新比较Git blobs：**346份已审核业务相关文件、127份v0.9冻结文件逐字节相同**。ADR-0012及正式contract技术正文保持原文。五个指定hashes逐值等于本报告顶部接受值，version-only文件精确限制为`0.9.0`→`1.0.0`替换。Final gates没有暴露需要修改业务语义的bug。

执行记录：首次后台启动遇到Windows `0xC0000142` DLL初始化失败，尚未开始完整测试，不计为通过。仅调整本地测试运行器为no-console启动，随后完成上述全量门禁；没有修改候选业务代码或缩减测试。

完整门禁仅使用既有synthetic/fixed fixtures与软件回归。既有未提交provider诊断、exchange/yaoqiu材料与`upload/phase10`接受快照保留原状，不混入发布commit。旧SQLAlchemy deferred-FK cycle排序warning保持可见。

## Known limitations retained

- Real decision-source adapter仍UNAVAILABLE；旧MultiMarketAnalysisV1明确synthetic，不能重标真实。Manual真实事实登记本身不构成production activation或performance observation。
- 真实样本仍不足；不证明ROI、alpha、P_llm改善或P_final优于P_base。描述性概率质量、资金结果与portfolio calibration分开解释，不自动调参。
- SQLite-only；local clock不等于外部notarization；source publication/version未知保持UNKNOWN/UNAVAILABLE。
- 冲突赛程保守拒绝，不猜测新canonical身份；缺失/unsupported/void结果不当输。
- 256 epoch runs、4096 report units、128 evidence/run及既有artifact/work/money bounds保留；超限拒绝，不选择性省略case。
- Same-match跨market joint、真实相关性模型、Kelly/跨日bankroll、season simulation等不在本版本范围。

## Publication sequence and stop point

Final local gates完成后commit/push feature；**FINAL candidate CI Success**后才fast-forward/非重写合入main；**main CI Success**后才创建指向最终main commit的annotated `v1.0.0`；**tag CI Success**后才完成发布交付。原审核业务历史保留。

仅按明确授权进行GitHub push/CI查询及release refs操作。**PROVIDER / LLM HTTP = 0**；没有真实训练、自动调参、真实回测或自动下注。Synthetic软件测试不是实际业务运行。

完成后停止，等待网页GPT给出独立的post-1.0 Production Activation / Prospective Observation Plan；不开始新的预测算法，不为获取真实样本修改冻结model/fusion/objective。
