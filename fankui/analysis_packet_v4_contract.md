# ANALYSIS_PACKET_V4 / LLM_REVIEW_V4

## 版本隔离

新V4类型和byte parser独立实现，V1/V2/V3文件、hash、schema和行为保持冻结。
Packet使用新的artifact_id/content_hash作为packet identity；Review中的packet_id /
packet_hash分别精确绑定这两个字段。旧wire不会自动升级成V4。

## Packet 白名单

AnalysisPacketV4包含analysis ref与ordered market_units，每个market unit唯一键为
`match_id + MarketKeyV2.canonical`。同场可以有多个市场，各自review_context_id/hash，
重复键或错序拒绝。Context包含：

- canonical match/team/competition/season/kickoff identity；
- market_key、decision cutoff、generic P_market/P_quant与availability；
- model/state/config/training hashes、model/version、training cutoff/generated_at；
- data_quality、football evidence及其精确IDs/hash。

Context本身封存hash。Football facts可逻辑共享，但不同market有不同context绑定。
Packet不包含P_base/P_final、SP资金计算、EV、ticket、portfolio、budget、stake、
profile资金参数或fusion weight。Poisson完整训练图保留本地，wire只给所需lineage。

## Review 白名单

LLMReviewV4绑定analysis_id、packet_id、packet_hash和完整ordered market_reviews。
每项带match_id、MarketKeyV2、review_context_id/hash与limitations。
VALID返回absolute P_llm distribution、assessment_confidence、scenarios、preferred
outcomes、avoid outcomes、counter scenarios、risk tags、reasoning_summary和evidence refs。
MarketScenarioV4包含必填scenario_id、scenario_type（MAIN / SECONDARY / UPSET）、
description、outcomes、trigger_conditions和evidence_refs。每项trigger为非空、最多2000
字符的文本，最多16项；scenario IDs在当前match-market review unit内唯一。

counter_scenarios使用独立MarketCounterScenarioV4：if_scenario_id、
alternative_scenario_id、fails_outcomes、rationale和evidence_refs。两个scenario引用
必须指向**同一market review unit**中存在的scenario ID；不能用其他unit的ID满足引用。
fails_outcomes与scenario outcomes均须属于当前market catalog，且各自列表内部唯一。

preferred_outcomes和avoid_outcomes分别唯一且不得重叠；risk_tags、limitations以及
review/scenario/counter各自的evidence_refs列表内部必须唯一。合法evidence可在不同
scenario中复用，所有引用仍必须属于当前精确context的evidence IDs。

## V4 abstention semantics

Review abstention与模型可用性分开表达，failure_code至少支持下列四项：

| quant状态 | 允许的Review |
|---|---|
| MODEL_UNAVAILABLE | 必须UNAVAILABLE / MODEL_UNAVAILABLE |
| AVAILABLE | VALID，或UNAVAILABLE / INSUFFICIENT_EVIDENCE、INVALID_CONTEXT、SKIPPED_DISABLED |

AVAILABLE quant不能声称MODEL_UNAVAILABLE；unavailable quant不能返回VALID或其他
failure code。UNAVAILABLE类型没有P_llm/assessment_confidence等VALID字段，额外字段
拒绝，不以伪P_llm完成fallback。

对于证据/上下文/禁用导致的abstention，若本地已有P_base，Fusion逐值保留
P_final=P_base、influence=0，fallback_code记录原failure_code，不进入generic correction。
P_base缺失时继续保持缺失和已有source unavailable原因。

未知、遗漏、重复unit、跨市场distribution、错context、未知evidence、额外资金字段均拒绝。
INVALID_CONTEXT是语义上的abstention原因，不豁免packet/context ID/hash绑定检查。
Review不能提交P_final、delta、fusion weight、EV、票据或任何资金参数。

## Byte / hash / import

文件上限4MiB，UTF-8；拒绝duplicate JSON key、JSON float、NaN/Infinity。
Probability为Decimal，最多28位小数，0..1并按原严格tolerance闭合；程序输出1e-12。
输入JSON保留原bytes和SHA256，canonical artifact hash独立记录。所有新模型extra=forbid，
可通过 `market-v2 schema packet` / `schema review` 获取JSON schema。

Import要求本地已存在完全一致的sealed packet，append-only、exact retry；保存和读取
逐层比对typed SQLite图及完整规范JSON。无法通过重新hash篡改来替换原source graph。

## Generic fusion 数学

显式GENERIC_LLM_REVIEW_DELTA_V1，旧LLM_REVIEW_DELTA_V1不变。
新市场base可为QUANT_ONLY_V1或MARKET_QUANT_BLEND_V2（固定输入quant_weight）；
缺失所需market时base unavailable。THREE_WAY从原Elo/final source只读适配。

LLM effect = assessment_confidence × data_quality。N-outcome correction先以固定
概率量化规则闭合输入，计算scaled deltas，再按max absolute delta统一缩放。
为量化预留N个1e-12 quantum，形成位于base和P_llm之间的convex move，最后量化闭合。
不是逐项clamp。每项非负、和1、最大delta不越cap；cap=0保留原base。
THREE_WAY adapter复现原三向的2-quantum cap及原量化顺序，新增回归逐值对照旧函数。

## 已知限制与未来

V4是本地文件协作合同；本轮没有真实LLM API或网页Review调用。
Baseline/合成fixture只能证明软件合同。数据质量值来自sealed local context，
不代表已校准预测能力。V4 import/fusion不会自动生成或执行下注。
