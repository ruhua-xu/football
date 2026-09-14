# Phase 7 最终验收与 v0.7.0 发布收尾报告

## 整体架构验收

用户于2026-09-14明确声明 **0.7 Architecture Review PASSED**，接受实现提交
`dd13e0614b2cd4760d2af818cd17b867bc4086dd`。其 [完整CI #25](https://github.com/ruhua-xu/football/actions/runs/34794201494)
全部步骤成功；这是被接受的软件候选证据，不能替代本次0.7.0版本升级后的release gates。

[ADR-0009](decisions/0009-versioned-strategy-pass-engine.md)仅更新为Accepted并记录验收，
技术内容保持原样。发布收尾同步package/runtime版本0.7.0、版本提示、wheel版本预期及
公开文档。Strategy Profile、pass数学、结构风险、结算与优化逻辑已冻结。

## 已接受能力与 A–E 结果

版本化Profile偏好4张、绝对最多8张，默认偏好2–3张PRIMARY并允许一张小额
HEDGE/LONGSHOT；保留SECONDARY、现金及NO_BET。HEDGE必须保留原资格门禁、独立新
selection与具体核心失败时仍能中奖的子注证据，不承诺所有赛果保本。

仅从已完成AnalysisRun或已封存PortfolioRevision读取原概率、SP、阈值、rules、
constraints和已有预算；生产父工件继续经过concrete auditor和当前授权复验。
Ticket、AtomicBet、BetLeg及结算使用typed FK、append-only、deferred完整图seal；
读取与重试独立重演。兼容旧0.6 PortfolioRevision和旧2X1 settlement。

固定fixture为 [strategy_pass_v1.json](../data/fixtures/strategy_pass_v1.json)。全部为
invented prices/probabilities，验证软件合同，不声明真实收益或模型表现。

| Case | 验收事实 |
| --- | --- |
| A | 仅两场合格，SP2/3；只能生成1个2X1候选；10000 fen示例预算下1张票、stake10000、cash0 |
| B | 三场合格，SP2/3/4；生成3个2X1和1个3X4；默认按原ROI/风险选择1张2X1，指定3X4 profile的资金与结算验证通过 |
| C | 四场合格，SP2/3/4/5；生成6个2X1、4个3X4和1个4X11；默认4张3X4各2400、cash400；指定4X11 profile验证11个子注及资金/结算 |
| D | 全部EV低于原门槛；NO_BET、stake0、cash10000，不为票数或pass type恢复rejected selection |
| E | 高价值2X1全部依赖同一核心；结构约束将方案减为1张，记录STRUCTURAL_CONCENTRATION_LIMIT，不机械填满4张 |

上述默认资金示例采用fixture自身封存的10000 fen预算。生成能力与原ROI/风险的最终
择票分别验证；不会为演示3X4/4X11改变现有父预算或门槛。旧历史验收fixture的600 fen
单票上限正确阻止3X4，wheel中保留该拒绝检查；正向system settlement另用独立MOCK父图。

## Golden payout 与资金规则

| Pass | 子注构成 | 单倍本金 | 全中毛奖金（SP依次2/3/4/5） |
| --- | --- | ---: | ---: |
| 2X1 | C(2,2)=1 | 200 fen | 1200 fen |
| 3X4 | C(3,2)=3 + C(3,3)=1 | 800 fen | 10000 fen |
| 4X11 | C(4,2)=6 + C(4,3)=4 + C(4,4)=1 | 2200 fen | 69000 fen |

4X11中二串一合计14200、三串一30800、四串一24000 fen。仅SP2/3两场正确时，只中奖
对应二串一，毛奖金1200 fen。每个子注按原2元乘SP乘积、ROUND_HALF_EVEN到0.01元，
转fen后再乘倍数、求和；不能先将system总体合并舍入。整数倍数1–50，票本金≤600000
fen并服从更严格的父rules；总投入不超过原预算，零预算NO_BET。

Expected payout沿用原独立腿概率乘积、12位概率和8位metric量化及期望线性性。
States是9/27/81个确定性金额场景，不附加新收益分布概率。结算逐子注计算，partial
payout可能低于本金；missing/unsupported不给出伪造的总return，更正追加直接supersession。

## 发布门禁

被接受实现的本地证据为2476个collected node IDs完整去重覆盖：2475 passed、
1 skipped（Windows上的POSIX dir_fd/O_NOFOLLOW专用检查）；其中85项新增测试全部通过。
原中断运行和临时路径过长/慢分区超时记录保留，通过短basetemp和独立慢用例补齐，
不将中断或超时统计为成功。

本次0.7.0 version bump后的本地release gates已重新执行，结果如下：

| Release gate | 结果 |
| --- | --- |
| pytest | 115个有界隔离分区，2476个collected/unique node IDs全部核对；2475 passed、1个POSIX专用skip，零failure/error/timeout |
| Ruff / compileall | 全仓Ruff及src/tests/migrations/scripts compileall均通过 |
| fresh migration/check | 全新临时SQLite升级至28415c7e39ba，command.check为No new upgrade operations detected |
| existing-data upgrade | 用原0.6.0 wheel创建独立MOCK库，从17304b6d28a9升级副本至28415c7e39ba；136张旧表定义、677个旧触发器及285行旧数据保持原值；原库/旧wheel字节保持，升级后新strategy roundtrip通过 |
| wheel build | dist/football_system-0.7.0-py3-none-any.whl构建成功，保留旧wheel |
| installed-wheel E2E | 60资源、版本0.7.0、隔离import、原8条历史CLI与2×10 slices、3X4/4X11正向资金/结算、旧600 fen上限拒绝及head全部通过 |
| git diff --check / secret scan | 发布提交前核验完整diff；只stage本轮版本/文档文件，secret-pattern scan零发现 |

既有deferred FK环的SQLAlchemy排序warning保留，未suppress。Release gate没有暴露
需要修改已接受数学或业务逻辑的bug。本报告是发布候选封存时点记录；未来CI终态按下述
exact-ref门禁及最终交付消息确认。

候选HEAD CI必须成功后才合入main；随后创建annotated v0.7.0，并分别核验main和tag
对应exact SHA的完整CI。最终commit/tag object/target与三条CI URL在交付消息中列明，
避免为报告自引用反复制造新发布提交。CI流程保持完整，不改成仅文档或版本smoke。

## 保留限制与历史保护

- SQLite-only；市场仅THREE_WAY，每票每场一个outcome，不支持多选复式。
- Profile默认最多12场/4096候选；超限整单拒绝，不静默截断或降低EV门槛。
- 原独立腿假设及结构风险，不估计统计相关性。收益分布概率、CVaR、Kelly与分布优化留0.9。
- 新strategy source要求SP captured/available/ingested均不晚于父decision cutoff；不补造迟到SP的历史可见性。
- 取消、VOID、退款、加时/点球及串关降级仍UNSUPPORTED_SETTLEMENT_CASE；缺赛果保留MISSING_RESULT。
- v0.6.0历史、固定Elo/quant、P_market/P_llm/P_final融合、EV、V3 wire、bankroll constraints及0.6真实验收工件保持冻结。
- Package bump不重训、不重绑定历史code revision/hash；真实材料不进入Git或wheel，原retention和allowlist清理义务继续有效。

迁移head保持 `28415c7e39ba`，wheel显式资源仍60项。没有0.8市场扩展或新增handicap、
score、total goals。v0.7.0 main/tag CI全部成功后停止，等待用户的0.8指令。
