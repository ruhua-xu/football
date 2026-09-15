# Strategy Pass V2 / Simple Multiple Selection

## 隔离与来源

旧StrategyPassPlanV1/SystemTicketV1/SettlementV1完全冻结。V2封存显式schema/version，
只消费MultiMarketAnalysisV1及其精确V4 Review/Fusion。资金规则、原EV公式和单选
2X1/3X4/4X11数学不变。预算只能选择分析时已经封存的integer-fen budget。

## Choice → AtomicBet 数学

MatchChoiceSetV1内同一match、同一MarketKey，outcomes按canonical catalog排序。
每个outcome分别使用 `EV=P_final*SP-1`，沿用原selection_ev函数和原threshold。
负EV/rejected条目记录INELIGIBLE_OUTCOME_EXCLUDED后排除；不是top-N截断。
一个choice set无任何合格outcome时不生成该票。数据质量/模型不可用不补造概率。

同一ticket每场只有一个choice set；same-match cross-market compound拒绝。
不同比赛可mixed market。Within-match choices=OR，实际atomic的cross-match legs=AND。
Pass先生成constituent match subsets，再对每个subset做choice Cartesian product。

| Pass / counts | 子注展开 | expanded | 单倍本金 |
|---|---|---:|---:|
| 2X1 / 1,2 | 1×2 | 2 | 400 fen |
| 3X4 / 1,2,2 | AB2+AC2+BC4+ABC4 | 12 | 2400 fen |
| 4X11 / 1,2,2,1 | pairs(2+2+1+4+2+2)=13；triples(4+2+2+4)=12；quad4 | 29 | 5800 fen |

每个atomic：2元×SP乘积，原ROUND_HALF_EVEN到0.01元，转fen后乘同票multiplier；
中奖atomic相加。总本金=expanded_count×200×multiplier。倍数1..50、票本金≤600000
并服从更严格的原rules/预算。相互排斥的outcomes不能同时中奖，因此max payout按可行
的choice states计算，不能把所有展开atomic奖金直接相加当作最大中奖金额。

## 复杂度与重建

默认Profile见 [strategy_profile_v2.json](../config/strategy_profile_v2.json)：
preferred outcomes2/absolute3，每票96 atomic、全计划4096 atomic、512 generated
candidates、12 input matches、31 market outcomes，每票256可重建states，source 8MiB。
任何硬上限在AtomicBet materialization前检查，拒绝整项/整次请求，不silent truncate，
不采样，不在OOM之后降级近似。生成总候选数使用组合数/乘积预计算。

封存完整ordered choices、每outcome的P_final/SP、expanded atomic及legs、source/model/
fusion refs、本金和每atomic payout。状态使用EXCLUSIVE_CHOICE_OR_REST_CARTESIAN_V1：
每match状态是某个所选outcome或REST（所有未选outcomes的payout等价类）。最多4^4=256，
远小于31^4；对应ADR-0010的可重建图决策。这里没有状态概率或收益分布优化。

## 结构风险与资金

风险按实际expanded graph计算match/outcome exposure、atomic dependency、ticket
atomic overlap、choice-set Jaccard overlap和core dependency。单outcome失败只有在
所有相关atomic都必需它时才构成wipeout；OR中的另一outcome不能被当作同时必需。

Single-match-choice-set wipeout以常规时间比分生成具体failure witness。四种市场的
谓词只涉及0..6/7+、有限显式比分及[-20,20]整数goal-difference边界；0..28的比分
representative square覆盖全部Boolean cells。跨ticket同场不同market也用同一真实
比分验证，不独立虚构互相矛盾的market outcome。HEDGE必须有该failure下仍可中奖的
具体expanded atomic及新合格outcome；不建立统计correlation模型。

沿用原expected ROI/边际集中度score和逐轮加倍方法。结构约束作用于开票与每次加倍。
Profile preferred4/absolute8、PRIMARY软目标2–3，辅助票最多1、默认预算10%；
额外票仍需原更高ROI/complexity与新增match exposure。NO_BET和现金始终合法。

## Settlement V2 / persistence

REGULAR_TIME_EXPANDED_MARKET_PASS_V2只读normalized MatchResult，用各市场正式映射
判定每个expanded atomic，再按原金额数学汇总。OTHER用对应OTHER SP，不nearest-match。
缺赛果MISSING_RESULT，取消/VOID/退款/加时/降级UNSUPPORTED_SETTLEMENT_CASE，均不按输
处理。Correction必须直接supersede旧normalized result和同plan旧结算，append-only。

迁移39526d8f40cb为additive。新闭集typed artifact tables引用原canonical matches/
results/Elo states，quote/outcome catalog及所有图关系具typed FK；registry header要求
deferred completeness seal，禁止部分图commit及UPDATE/DELETE/REPLACE。读取复验hash、
字段/子图、source/math、raw semantics和replay；transaction-local DAG memoization不跨
请求缓存权限/数据。Populated downgrade拒绝，旧0.7表/触发器/行/bytes保持原值。

## 保留限制

只有四种市场、少量same-market choice；没有HALF_FULL、同票同场跨市场compound、
统计相关性、CVaR、Kelly或收益分布优化。0.9可使用封存图重建payout，不要求改写旧票。
真实provider/LLM接入与真实预测效果不在本次软件验收范围。
