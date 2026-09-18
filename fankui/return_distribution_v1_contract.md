# Return Distribution V1 — Accepted Contract

状态：**Accepted / 0.9 Architecture Review PASSED**。技术合同已冻结；发布门禁见`phase_9_final_acceptance_report.md`。

## 1. Input and frozen boundary

唯一业务输入是sealed `StrategyPassPlanV2`。绑定plan/source/analysis/fusion IDs与hash、完整candidate catalog hash/count、原rules/constraints、strategy profile hash及原budget。
只在`plan.candidates`内重选ticket IDs和1..50整数multiplier。既有selected仅benchmark；已排除outcomes不可恢复。
P_final取原fusion结果及distribution hash，SP绑定原snapshot ID/hash；不训练、推断、融合或改变概率。
EV仍唯一`P_final × SP - 1`；分解、Cartesian expansion及各atomic gross保持V2工件原值。

## 2. Probability domain

一个portfolio内，每个match必须只有一个完整`MarketKeyV2`（包括handicap参数）。不同key立即`CROSS_MARKET_JOINT_UNAVAILABLE`。
不独立相乘同场不同市场，不用Poisson替换其他P_final，不IPF/calibration/copula。

对一个match，按market catalog顺序取所有ticket实际引用outcomes的union，每项保留原P_final。
若union没有覆盖整个catalog，追加`outcome=null`的REST，质量为`1-sum(selected)`；否则没有REST。
REST是等价类：该match所有被选outcome对应legs都未命中，不是一个随机具体outcome。零质量REST仍可存在于状态合同，但不产生support质量。
完整输入marginal必须非负且**exact sum=1**。旧V2输入容差不会被偷偷normalize；否则`P_FINAL_MASS_NOT_EXACTLY_ONE`。

跨比赛固定`INDEPENDENT_MATCHES_V1`，不是现实统计独立保证。同一match无论被多少ticket引用，其state概率只乘一次。

## 3. Exact components and payout

建立ticket↔match hypergraph，拆分不共享match的connected components。每个component只枚举其relevant-state product；不同components的gross分布做精确convolution。
相同gross合并；support按gross升序，不能有重复gross、负质量或零质量support point。

每个world：

`ticket_gross = sum(sealed_atomic.gross_payout_fen for winning atomics) × multiplier`

`portfolio_gross = sum(ticket_gross)`

`cash_fen = budget_fen - stake_fen`

`ending_capital_fen = cash_fen + gross_payout_fen`

`profit_loss_fen = ending_capital_fen - budget_fen`

0.9不重新乘SP、不重写payout rounding。完整比赛结果映射到对应market outcome后，金额必须逐值等于正式Settlement V2。
Budget为上限；NO_BET有gross0/P1、stake0、cash=budget，expected profit0。

## 4. Decimal proof and canonical bytes

- 使用独立`Context(prec=512, rounding=ROUND_HALF_EVEN)`，不继承ambient精度、rounding或traps。
- 每个输入probability最多28位小数，unique matches最多12；joint product最多336位小数，概率乘加在512精度内exact。
- Budget最多128位、gross为signed-int64，期望金额乘加最多约465位；仍在512内。
- Kernel额外启用Inexact/Rounded traps；发生精度损失不能标为exact。
- Support probability**不量化**，不丢弃极小正质量；只省略exact zero。每component及final support的和都必须等于1。
- Canonical Decimal只去无意义尾零，不改变数值；source marginal与其hash仍原样绑定。
- `expected_profit_ratio`不额外quantize，极小有限比例仍保留；无限小数按固定512精度HALF_EVEN计算。Utility也使用固定512精度；不把无限小数近似宣称为有理数的无限精度表示。
- Code hash是三个return合同/算法源码relative paths及LF-normalized bytes的SHA256；policy和objective分别封存hash。

## 5. Metrics

| 字段 | 定义 |
| --- | --- |
| p_gross_payout_gt_zero | P(gross > 0) |
| p_break_even_or_better | P(cash + gross >= budget) |
| p_2x_budget | P(cash + gross >= 2 × budget) |
| p_3x_budget | P(cash + gross >= 3 × budget) |
| p_loss | P(cash + gross < budget) |
| p_deep_loss | P(cash + gross <= floor(budget / 5)) |
| expected_gross_payout_fen | sum(gross × probability) |
| expected_ending_capital_fen | cash + expected gross |
| expected_profit_fen | expected ending - budget |
| expected_profit_ratio | expected profit / budget，固定512精度、不额外quantize；budget0为null |
| median_ending_capital_fen | 最小的ending，使累计probability >= 0.5 |
| expected_shortfall_to_budget_fen | E[max(budget - ending, 0)] |

所有金额单位为fen；没有含糊的单独`return`金额字段。Expected金额是Decimal fen，离散支持、stake/cash/median为整数fen。
Budget0时不除零；按以上不等式，NO_BET的break-even/2x/3x/deep-loss概率均为1，loss/gross-positive为0，ratio为null；utility的null ratio贡献为0。
不使用formal CVaR名称，不引入Kelly。

## 6. Evaluation and feasibility

`ReturnEvaluationV1`有AVAILABLE或DISTRIBUTION_UNAVAILABLE。AVAILABLE只表示分布可精确计算，不是下注建议；另有`feasible`与`violations`。
金额/rules/budget不合法或概率/复杂度不可支持时无distribution；结构风险/role不合格的组合可显示其数学分布，但feasible=false。
optimizer只考虑feasible组合。V2 `structural_risk_v2`、`hedge_v2`保持原函数与数学，未用概率模型替换结构约束。

角色由程序和原plan中封存的role hints决定，不接受CLI/GPT临时role资金指令。无hint的selected候选按canonical ID分配最多preferred_primary_max个PRIMARY，其余SECONDARY；显式PRIMARY占用名额优先保留。
HEDGE对当前全部PRIMARY/SECONDARY重算surviving atomic witness；LONGSHOT需新的independently eligible outcome、高回报、非空main及小额auxiliary约束。标签不豁免utility或风险。
超preferred票数仍需原extra-ticket ROI/operational complexity资格及canonical前缀之外的新match；absolute、match/outcome exposure、core/overlap及auxiliary约束均保留。

## 7. Marginal optimizer

`RETURN_DISTRIBUTION_MARGINAL_V1`：从NO_BET起步，逐轮尝试开启一个candidate（multiplier1）；开票停止后逐轮尝试某已选ticket multiplier+1。
每一轮重新计算整个portfolio分布/metrics。对feasible增量池做Pareto支配筛选，再选最高正utility增量；并尊重封存source的min_marginal_score下界。无正增量立即停止，不为花完预算而分配。
Tie-break：utility降序、stake升序、canonical `(candidate_id,multiplier)`序列升序。权重和来源在整次run固定。

默认未校准utility：

`0.45 × expected_profit_ratio + 0.10 × p_gross_payout_gt_zero + 0.20 × p_2x_budget + 0.10 × p_3x_budget - 0.10 × p_loss - 0.05 × p_deep_loss`

Weights非负，positive/negative分组；配置在`return_objective_profile_v1.json`。不根据历史结果或GPT自动调权；break-even仍报告但不额外奖励。
策略可保留现金；最终不胜NO_BET或无正增量时输出NO_BET。不是global/exhaustive optimum，也不在multiplier阶段重新开启已拒绝的票。

Dominance：expected profit、break-even、2x、3x不低；loss、deep-loss、shortfall不高；至少一项严格。`pareto_candidate_count`/`dominated_candidate_count`覆盖所有独特已评估feasible allocations（含NO_BET，legacy-only reference不计）；不是全搜索空间frontier。
Legacy V2 allocations单独评估，可能因同场跨market而unavailable；其utility/delta仅software comparison。

## 8. Hard bounds and explicit failure

| Guard | 默认/版本上限 |
| --- | --- |
| max_optimizer_candidates | 32 |
| max_component_world_states | 65536 |
| max_distribution_support | 65536 |
| max_unique_matches | 12 |
| preferred / absolute tickets | 4 / 8，并服从更严格source limits |
| max_convolution_products（每次） | 1048576 / 4194304 |
| max_evaluation_work | 16777216 / 67108864 |
| max_optimizer_evaluations | 4096 |
| max_optimizer_work | 67108864 |
| gross support金额 | 0..9223372036854775807 fen（SQLite int64） |
| budget数字位数 / artifact bytes | 128 / 64MiB |

Component count在枚举第一项前全体检查，零概率state也计入logical bound。Support/convolution有独立guard；whole-portfolio product从不替代component算法。
Work单位为world×atomic-leg检查数及convolution products；optimizer累计预算包含trial及chosen reconstruction。Guard超限不保留partial optimized recommendation。
Catalog超限：`OPTIMIZER_UNAVAILABLE / CANDIDATE_SEARCH_SPACE_TOO_LARGE`，不top-N。其余明确`DISTRIBUTION_COMPLEXITY_LIMIT`、`DISTRIBUTION_AMOUNT_LIMIT`或`SEARCH_SPACE_TOO_LARGE`。
无silent truncation、sampling、Monte Carlo或approximate fallback。

## 9. Persistence and CLI

新增版本化policy、objective、match states、ticket return functions、portfolio distribution、metrics、evaluation、optimization run工件。
新`rd_*`表以typed FK绑定旧mm计划/candidates/atoms/outcomes/fusion/SP，以及新分布与metrics。SQL阻止update/delete/replace、sealed后追加和不完整header commit；repository对完整图、source、math、role/search进行重放，检测重封hash的错误数据。
Support以unique gross升序保存probability文本，不通过SQLite REAL检查微小概率。Component的matches/tickets/support及atomic requirements均有typed child rows。
只保留最终/NO_BET/legacy distributions及accepted-step摘要checksum，不保存巨大完整trial frontier；step checksum由optimizer replay验证，不伪装成已持久化distribution FK。

```
football-system return-distribution evaluate --database-url sqlite:///... --input evaluate.json
football-system return-distribution optimize --database-url sqlite:///... --input optimize.json
football-system return-distribution show --database-url sqlite:///... --artifact-id ...
football-system return-distribution policy
football-system return-distribution profile
```

evaluate input：`plan_id`、`allocations:[{ticket_candidate_id,multiplier}]`；optimize input：`plan_id`。
可提供expected policy/objective hash作一致性断言，不是override。其他字段（budget/P_final/SP/EV/profile内容）拒绝；Float、重复JSON key、NaN和超4MiB请求拒绝。
输出同时含所需summary与完整sealed artifact；`show`做read/replay，不能用旧0.8建议冒充优化结果。

## 10. Acceptance and interpretation

A–T fixtures：`data/fixtures/return_distribution_v1.json`。小规模`EXHAUSTIVE_TEST_ORACLE`仅在tests内，不进入production。
升级使用正式v0.8.0代码创建旧库，逐值比较旧table/trigger/rows/serialized artifacts；populated新图拒绝downgrade。
既有Elo/Poisson/P_final/EV/V4/StrategyPassV1/V2/SettlementV1/V2文件和goldens保持冻结。
Exact只在声明假设及支持范围内成立；不宣称真实独立、ROI、alpha或现实收益优势。本版本不含真实provider ingestion、真实回测、登录/支付/下注。
