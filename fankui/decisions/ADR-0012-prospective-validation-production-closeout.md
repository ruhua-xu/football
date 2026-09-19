# ADR-0012 — Prospective Validation / Production Closeout

- Status：**Accepted**
- Base release：v0.9.0 / `9a869d42b44282551cbdc694d8efc6d0009e9dde`
- Branch：`feature/1.0.0-prospective-validation-production-closeout`
- Scope：software framework candidate；真实prospective performance独立取证。

## Architecture Review acceptance record

- Conclusion：**APPROVED**；workspace owner明确接受candidate `da4e9c518492ebe24319b871deb1872ef91fb0c9`，tree `c796296101bf7881344f45d05e8a17c787dc0682`，无architecture blocker。
- Accepted migration head：`5b748fa162ed`。
- Prospective implementation hash：`6b2a73c14720c60eb30f9bc5d4158bccf095d397748cd1157eb53f13933ddb1f`。
- Prospective policy hash：`06f9cb5289959cf6387b324f9a8ef617d1960a68e88e23cb1423e0c15ef847f8`。
- Release authorization：完成1.0.0 release-only closeout及完整final local gates，依次通过final candidate、main、annotated v1.0.0 tag CI；不squash/rewrite已审核业务历史。下方原始技术决策与候选阶段release boundary保持原文，候选停止点现已由本次明确发布授权推进。
- v1.0.0仅表示Prospective Validation / Production Framework软件完成；`INSUFFICIENT_PROSPECTIVE_SAMPLE`与`PRODUCTION_DECISION_ADAPTER_UNAVAILABLE`保持真实，不宣称ROI、alpha、P_llm改善、P_final优于P_base或真实production activation。
- 最终验收与发布门禁记录：[Phase 10 final acceptance report](../phase_10_final_acceptance_report.md)。

## Context

冻结Elo、Poisson、market/fusion/P_final、V4 wire、EV、Strategy Pass V1/V2、Return Distribution V1、objective weights、payout/settlement与budget/risk规则。
1.0仅增加时序、证据、锁定、观察修订和描述性验证。当前Sportmonks capture永久结束，不读取或恢复已删除raw，不激活任何provider HTTP。

## Proposed decisions

1. **Trusted local event clock.** Ingestion、prepare、lock、invalidation、result capture和epoch close由repository注入的可信时钟记录；CLI不接受这些事件时间override。测试时钟显式标为synthetic，不能产生真实performance资格。
2. **Append-only lifecycle.** ProspectiveRun是准备快照；DecisionLock、pre-kickoff invalidation、ResultObservation、Settlement和Report分别封存。Current status是事件投影，不修改旧run/lock。Request key+request hash实现真正exact retry。
3. **No-lookahead.** 数据cutoff、source publication/availability与可信local receipt分别记录。Prepare以当前可信时钟封存cutoff及完整input/evidence集合；lock必须在所有相关比赛的最早kickoff前，且预留版本化安全lead。已知赛果、future/stale evidence、变动kickoff、错误依赖hash均明确拒绝。
4. **Supersession is pre-kickoff only.** 新prepare引用旧run；替代lock与旧run invalidation在同一事务完成，并同时满足新旧deadline。禁止分叉、赛后修改prediction或通过换slate key重复计入同一epoch observation。
5. **Provider-independent manual provenance.** MANUAL_VERIFIED_IMPORT_V1绑定实际本地source文件hash、reviewer/verification、source identity与structured事实；系统时间不能由导入文件回填。FACT/ANALYSIS/SPECULATION显式区分，UNKNOWN伤停不等于无人伤停，未确认名单不等于confirmed。
6. **Freshness uses source time.** FRESH/STALE/UNKNOWN由固定category时限与source publication计算；仅有ingestion不能证明fresh。Schedule/form同时绑定window、cutoff与所用source IDs，赛后材料不能进入赛前snapshot。
7. **V4 remains frozen.** Correction categories与evidence绑定使用独立V4CorrectionAuditV1 sidecar；不向LLM_REVIEW_V4添加字段。保留improved/worsened/neutral/abstained全部结果，不按修正表现筛案例。
8. **Locked money and probabilities.** Lock绑定完整source/packet/review/fusion/strategy/optimizer及code/config/hash、P_market/P_quant/P_llm/P_base/P_final/SP、精确selected tickets/multipliers/stake/cash。Settlement使用已冻结return evaluator及market settlement，不引入第二套SP/payout数学。
9. **Revisions and coverage.** ResultObservation绑定normalized MatchResult与source/local revision链；未知provider version/publication明确UNAVAILABLE。新revision追加，旧settlement可重放；最新结果尚未重新settle时不偷偷使用旧版本作为当前验证结果。Missing result不当输、不填0。
10. **Frozen validation epoch.** Epoch预先固定implementation/model/config/training-history/strategy/objective/risk hashes及窗口；每个run逐项比对。关闭epoch追加close seal；不自动调整参数，不把本epoch结果用于同epoch的新配置。
11. **Descriptive reports only.** 按market与layer分别计算Brier/log-loss/calibration，layer deltas使用同一observation交集。Cash/NO_BET、decision/value和portfolio event calibration分别报告。报告由repository选择整个epoch的可见集合，不能传入盈利或LLM正确case白名单。
12. **Truthful real eligibility.** 旧MultiMarketAnalysisV1明确标为SYNTHETIC_ACCEPTANCE_DATA；不能重标为真实样本。当前冻结V2/return graph bridge保留此来源事实。真实manual事实可独立登记；真实decision-source activation仍需正式合法来源与兼容的生产adapter，缺失时明确PRODUCTION_DECISION_ADAPTER_UNAVAILABLE。即使synthetic用例很多，真实报告仍INSUFFICIENT_PROSPECTIVE_SAMPLE。
13. **Closed typed persistence.** 新pv_*图、typed source FKs、deferred completeness seals、append-only triggers、完整source/time/math replay；旧v0.9表、triggers、rows及serialized artifacts逐值不变。

## Acceptance / release boundary

先完成A–Z软件验收、v0.9真实代码旧库升级、frozen regression、wheel installed E2E与candidate CI。
Package metadata保持0.9.0；仅commit/push feature，不merge main，不创建v1.0.0 tag。
真实performance evidence必须使用真实、合法、预先锁定的观察，样本不足保持INSUFFICIENT_PROSPECTIVE_SAMPLE；synthetic验收不证明ROI/alpha或模型有效性。
完成候选与统一review bundle后停止，等待唯一一次整体Architecture Review。
