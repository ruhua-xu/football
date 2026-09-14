# Strategy Profile / Pass Type Engine V1

状态：Accepted — 0.7 Architecture Review PASSED。Package metadata 为 `0.7.0`；最终发布门禁见 [Phase 7 最终验收报告](phase_7_final_acceptance_report.md)。

## 输入与血缘

入口是显式的 `StrategyPassService` / `strategy-pass` CLI。选择一个已完成
`AnalysisRun`，或一个已封存 `PortfolioRevision`，以及该父对象中**已经存在**的
integer-fen budget。调用方只提供 profile、role hints，不能提供替换概率、SP、EV、
threshold 或新预算。Revision 必须同时绑定其 AnalysisRun、FusionRun、revision hash。

Repository 读取父 config/context hash、原预测和候选、精确 Sporttery snapshot，
按原 `build_selection_candidates` 核对已有 eligibility gate，保留 rejected selections
的原状态/原因；仅 ELIGIBLE 进入系统票。Revision 的概率逐项等于封存 FusionRun
的 P_final。Elo、融合、V3 wire 和旧资金公式不变。

Source 保存排序后的完整 selection evidence、odds snapshots、原 rules/constraints/gates。
V1 要求引用的 SP captured/available/ingested 均不晚于父 decision cutoff；不为迟到 SP
重新解释旧时间语义。更换 source、profile 或角色请求产生新计划，不更新父图。

生产父工件的 create/load/save/retry/settle 都经原 `ProductionAuditGuard`；需要原
concrete auditor、sidecar 和当前授权复验。历史有效或命中缓存不替代当前授权。

## 正式 Profile

Schema：`STRATEGY_PROFILE_V1`，version `1`，默认示例
[`config/strategy_profile_v1.json`](../config/strategy_profile_v1.json)。

| 属性 | 默认 | 语义 |
|---|---:|---|
| preferred_max_tickets | 4 | 软偏好，绝非填满目标 |
| absolute_max_tickets | 8 | 顶层 Ticket 硬上限，与父 constraints 取更严格值 |
| preferred_primary_min / max | 2 / 3 | 下限是软偏好，上限约束 PRIMARY 角色 |
| max_auxiliary_tickets | 1 | HEDGE / LONGSHOT 合计最多一张 |
| max_auxiliary_budget_ratio | 0.10 | 辅助票 stake ≤ 原预算10%；配置最大25% |
| max_ticket_overlap | 0.75 | 两票共享selection数 / 较小票selection数 |
| max_core_dependency_ratio | 0.75 | selection所依赖子注本金 / 总部署本金 |
| longshot_min_max_return_multiple | 6 | LONGSHOT 的最大毛回报/票本金资格下限 |
| max_input_matches / max_generated_candidates | 12 / 4096 | 有界确定性枚举；超限整单拒绝，无静默截断 |

允许 `NO_BET`、少于preferred、保留现金。PRIMARY/SECONDARY 是已有质量排序结果的角色；
角色 hints 必须对应同源 selections，同一等价票不得请求多个角色。未合格的 hint
不生成新候选，也不恢复负EV。未指定辅助角色时，不自动制造反向低赔率“保本票”。

## Constituent 合同

| Pass | 比赛数 | 子注 | 单倍总本金 |
|---|---:|---|---:|
| 2X1 | 2 | C(2,2)=1 个二串一 | 200 fen |
| 3X4 | 3 | C(3,2)=3 个二串一 + C(3,3)=1 个三串一 | 800 fen |
| 4X11 | 4 | C(4,2)=6 + C(4,3)=4 + C(4,4)=1 | 2200 fen |

V1 每张票每场选择一个 THREE_WAY outcome；因此同场互斥 selection 不可能进入同一子注。
它不是多选复式。先按selection ID排序，然后按子注腿数递增、combinations顺序分解。
每个 AtomicBet 封存 selection IDs、match IDs、各腿 fixed bonuses、200 fen单位和毛奖金。
每张票封存 source/profile/parent、role、multiplier、stakes、odds snapshot ID/hash refs、
全部 outcome payout states 和 max payout。States 的 outcome 顺序严格对应该票排序后的
selections；取值顺序 HOME_WIN / DRAW / AWAY_WIN，分别9/27/81个states。

使用原 `official_gross_payout_fen`：

1. 每个子注按 `2元 × product(该子注SP)` 计算毛奖金，以 `ROUND_HALF_EVEN` 到0.01元。
2. 转为integer fen；对子注先舍入，随后乘同票 multiplier，最后相加。
3. 总本金 = `200 × constituent_count × multiplier`；整数倍数1–50，票本金≤600000 fen，
   并执行更严格的父rules。不能先把system总SP相加/相乘再统一舍入。

Expected gross 用原独立腿假设及期望线性性：每个子注的marginal概率积按原12位量化，
乘该子注已经舍入的奖金，按原8位metric量化，再求和。2X1与旧计算逐值回归一致。
Expected ROI = `(expected gross - unit stake) / unit stake`。不为system票构造单一
joint_probability，不给 payout states 附加模型概率。

手算 golden（SP=2、3、4、5，单倍）：

- 2X1：200本金，最大1200；3X4：800本金，最大10000。
- 4X11：2200本金；二串一毛奖金14200、三串一30800、四串一24000，总69000。
- 只有SP=2与SP=3两场正确时，只中奖一个二串一，毛奖金1200；倍数统一在舍入后相乘。

## 结构风险与选择

Match/selection exposure 保守记整张票本金，以便直接沿用0.6 exposure/marginal-score
内核；另给出实际依赖该selection的 AtomicBet 本金。每个子注必需selection的交集
决定一张票的单selection死亡条件，所有票再取交集；多票存在共同必死selection即拒绝。
Overlap、core ratio和共同死亡检查作用于开新票和每一次加倍，不能只检查初始单倍。
这是结构指标，不声称统计独立或相关性估计。

HEDGE 要求：原gate独立合格；相对开票时的主攻前缀有新selection；存在具体子注，
包含新selection，且在该前缀的共同核心selection失败时仍能中奖。封存
`failed_core_selection_id`、`surviving_atomic_bet_id` 和独立selection列表。
角色证据不承诺全赛果保本。LONGSHOT 要求独立质量合格、已有主攻票、达到profile的
最大毛回报倍数门槛，并同样受小额、重叠和集中度限制。

排序继续使用原 expected ROI / marginal concentration score。preferred之上的额外票
沿用父 extra-ticket ROI、complexity penalty和新增比赛暴露要求。资金分配仍是原逐轮
边际加倍方式；新约束只缩小可行域。预算低于200 fen直接NO_BET；无足够比赛时仅生成
profile允许且满足2/3/4场要求的pass，不能凑票。Profile主动禁用2X1时，不强行启用它。

## Determinism、持久化与结算

Canonical JSON、域分隔SHA256及stable UUID封存profile/source/plan/ticket identity。
Plan seal 对规范化JSON（含UTC `Z` 表示、Decimal字符串）计算；读取时独立重演候选、
角色、排序、资金和risk，逐字段比较，拒绝仅重新hash过的篡改。等价票按pass与
match/market/outcome集合识别，不以角色或不同倍数复制同一票。

新增迁移 `28415c7e39ba`，父head `17304b6d28a9`。新增9张表：strategy plan、完整source
selections、system tickets、atomic bets、atomic bet legs、plan seal、settlement、
normalized result refs、settlement seal。typed FK绑定父图及精确SP/赛果；deferred seal
使不完整图无法commit。SQL阻止UPDATE/DELETE/REPLACE、封存后追加子项、缺腿、同场
互斥与错序/错误snapshot引用。Repository另执行全部经济数学和父工件重演。
有任何新工件时拒绝downgrade；空库可回退。旧0.6表和migration不改写。

独立结算policy `THREE_WAY_SYSTEM_PASS_BACKTEST_V1`，只接已持久化、cutoff内的正规
`MatchResult` IDs。按实际outcomes取封存payout state并逐AtomicBet结算；partial payout
可以小于本金，不等于盈利。缺赛果保留 `MISSING_RESULT`，取消/VOID/退款/加时/降级
继续 `UNSUPPORTED_SETTLEMENT_CASE`，不虚构退款或按全输处理。未完全结算时总return为null。
NO_BET按现金结算，利润0。Correction必须直接supersede上一赛果/同一计划的上一结算；
保留旧记录，SQL阻止平行root及fork。重复同一工件返回原值，不增加记录。

## CLI

```text
football-system strategy-pass profile [--print-schema]
football-system strategy-pass build --database-url <sqlite-url> --analysis-run-id <id> --budget-fen <existing-fen> [--profile <json>] [--roles <json>] [--output <json>]
football-system strategy-pass build --database-url <sqlite-url> --portfolio-revision-id <id> --budget-fen <existing-fen>
football-system strategy-pass show --database-url <sqlite-url> --plan-id <id>
football-system strategy-pass settle --database-url <sqlite-url> --plan-id <id> --as-of <UTC> [--result-id <id> ...] [--issues <json>] [--supersedes-settlement-id <id>]
football-system strategy-pass settlement-show --database-url <sqlite-url> --settlement-id <id>
```

生产父工件复用 `--evidence-root`、`--authority-pins`、`--operator-id`，三者一起提供。
没有这些参数时，普通synthetic/非生产父图可用，生产父图由原mandatory audit拒绝。
CLI不调用数据API。新strategy export上限64MiB，原V3文件上限不变；原内容相同的文件
可重试，不同内容拒绝覆盖。

## 验收与延后范围

固定invented fixtures：[`data/fixtures/strategy_pass_v1.json`](../data/fixtures/strategy_pass_v1.json)。
A只有两场；B三场；C四场；D所有EV低于门槛；E高价值二串一全部依赖同一核心，
用2X1 profile证明减少到一张。Unit与真实repository/CLI的synthetic父工件均覆盖。

0.8前不增加市场；0.9前不做portfolio return-distribution optimizer、P(return>0)、
P(return>=budget)、多倍预算收益概率、CVaR或Kelly。当前封存足够的构成/价格/概率与
payout states供将来计算，不输出这些新分布指标。Synthetic验收不证明真实收益。
