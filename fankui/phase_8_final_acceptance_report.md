# Phase 8 最终验收与 v0.8.0 发布收尾报告

## 结论、接受基线与冻结范围

用户于2026-09-16明确声明 **0.8 Architecture Review PASSED**，最终接受软件候选
`e6e7fba26b0fc69506100f4c908b45b2fcd890ae`；两个V4 contract blocker已关闭。
其 [完整CI #30](https://github.com/ruhua-xu/football/actions/runs/34986353953) 全部步骤Success，
本地2625个用例完整去重覆盖为 **2624 passed / 1 skipped**。此为被接受候选证据，
不替代本次0.8.0版本升级后的最终release gates。

本轮只更新ADR验收状态、package/runtime版本0.8.0、版本提示、wheel版本预期及文档。
Poisson数学/config、taxonomy、generic correction、EV、Strategy Pass V2、Settlement V2、
V1/V2/V3 wire、Strategy Pass V1和旧THREE_WAY/Elo已冻结。没有0.9功能工作。

[ADR-0010](decisions/0010-generic-market-review-and-simple-multiple.md)状态为Accepted；
技术正文保留。[原实施报告](phase_8_implementation_report.md)保留初始候选的历史时点。

## A–P 固定验收

数据为synthetic/fixed fixtures，不声明真实预测表现、命中率、ROI或alpha。

| Case | 已接受结果 |
|---|---|
| A | 旧2X1/3X4/4X11的V1 plan/ticket/settlement bytes、hash、单选数学及replay与v0.7.0 golden一致 |
| B | 2-1/-1→DRAW；3-1/-1→HOME_WIN；1-2/+1→DRAW；1-3/+1→AWAY_WIN，home handicap符号无翻转 |
| C | 2-1→GOALS_3；4-3、5-2→GOALS_7_PLUS；0-0→GOALS_0 |
| D | 3-1/4-1/5-1进入各自显式SCORE；6-1/4-4/1-6进入对应OTHER；0-0→SCORE_0_0 |
| E | 指定五组lambda及0/0、20/20验证确定性、无负数、三个新市场各自闭合、完整7+ tail、OTHER及稳定hash |
| F | 2X1的1/2 choices展开2个AtomicBet，unit stake400 fen；互斥选项不能同时计入最大奖金 |
| G | 3X4的1/2/2 choices：AB2+AC2+BC4+ABC4=12个AtomicBet，unit stake2400 fen |
| H | 4X11的1/2/2/1 choices：pairs13+triples12+quad4=29个AtomicBet，unit stake5800 fen |
| I | 不同比赛THREE_WAY与TOTAL_GOALS mixed-market生成、封存、结算通过 |
| J | SCORE_3_1/4_1合格而5_1负EV时仅保留前两项，明确记录排除原因，不恢复负EV |
| K | expanded/state/candidate硬上限在materialization前拒绝，artifact数量不变，无部分plan/persistence |
| L | ANALYSIS_PACKET_V1/V2/V3、LLM_REVIEW_V1/V2/V3及normalization bytes保持发布版golden |
| M | 4场×4市场=16个market units；旧Elo来源适配、新market consensus/Poisson、V4 export/validate/import/fusion和retry通过 |
| N | 所有新SP低于原门槛时NO_BET、stake0，不为演示复式强行出票 |
| O | 同票同场THREE_WAY+TOTAL_GOALS cross-market choice graph拒绝 |
| P | HOME_OTHER、DRAW_OTHER、AWAY_OTHER使用自身SP，不匹配nearest explicit score |

Fixture定义：[market_expansion_v1.json](../data/fixtures/market_expansion_v1.json)。
Unit手算SP族（A2、B6/8、C40/100、D6）F/G/H最大单倍毛奖金为3200、523200、3794400 fen；
完整持久化fixture（A4、B6/8、C40/100、D5）实际毛返还160000、3545600、5430400 fen，
对应各自封存倍数与预算。不同fixture分别按自己的输入验证，不能误述为真实收益。

## 两个 V4 blocker 的关闭证据

### Abstention semantics

- quant MODEL_UNAVAILABLE必须UNAVAILABLE/MODEL_UNAVAILABLE。
- quant AVAILABLE可以VALID，也可UNAVAILABLE/INSUFFICIENT_EVIDENCE、INVALID_CONTEXT、
  SKIPPED_DISABLED；不能声称MODEL_UNAVAILABLE。
- 存在P_base的abstention逐值保留P_final=P_base、influence=0、真实fallback_code，
  不构造假P_llm或调用correction；原packet/context/evidence绑定仍强制执行。

### Structured scenarios

- MarketScenarioV4包含scenario_id、MAIN/SECONDARY/UPSET、description、outcomes、
  trigger_conditions和evidence_refs。
- 独立MarketCounterScenarioV4包含if_scenario_id、alternative_scenario_id、
  fails_outcomes、rationale和evidence_refs；引用必须存在于同一market review unit。
- Scenario IDs、preferred/avoid各列表、risk_tags、limitations及各evidence-ref列表唯一；
  preferred与avoid不重叠。合法evidence可在不同scenario复用。

修订新增41项针对性测试全部通过，包括真实repository import/fusion/retry；
generic_correction函数AST与修订前基线一致，其余业务冻结文件没有改动。
详见 [V4合同](analysis_packet_v4_contract.md)。

## Existing-data upgrade 与 frozen regressions

升级证明使用Git不可变v0.7.0 tree运行原代码创建旧数据库及三种V1 plans，再执行新
migration。对所有旧表定义、旧触发器SQL、旧rows和serialized artifacts逐项比较，
原值保持。V1 read/show/replay/save/settle继续工作，不自动转换成V2；空图回退/重升、
populated downgrade refusal、deferred完整图seal与corruption detection均有测试。

[发布版golden](../data/fixtures/legacy_v070_market_goldens.json)由原v0.7.0 wheel独立生成，
reference wheel SHA256为763967f327bee230e71e04889e27dacd7868e926ddac94937d52634f320f7b45。
Packet/review canonical bytes、normalization及V1票据/结算hash均被核对；固定向量逐值
对照旧THREE_WAY correction kernel。模型/config与旧wire没有为新版本改写。

## 最终 release gates

本次0.8.0 version bump后已重新运行完整本地release gates：

| 门禁 | 本地结果 |
|---|---|
| pytest | 122个有界隔离分区，2625个collected/unique node IDs全覆盖；2624 passed / 1 skipped，零failure/error/timeout |
| 跳过项 | Windows上的POSIX dir_fd/O_NOFOLLOW专用检查，非业务用例失败 |
| Ruff / compileall | 全仓Ruff、src/tests/migrations/scripts compileall通过 |
| fresh migration/check | 全新临时SQLite upgrade至39526d8f40cb，command.check无待生成操作 |
| v0.7.0 existing-data upgrade | 从不可变v0.7.0原代码创建旧库，旧表、触发器SQL、rows、serialized artifacts保持原值；V1 read/show/replay/settle通过 |
| populated downgrade / 完整性 | 有数据回退拒绝；空图downgrade/re-upgrade、deferred seal、corrupt graph拒绝均通过 |
| wheel build / installed E2E | dist/football_system-0.8.0-py3-none-any.whl成功构建；70资源、隔离import、原历史/V1流程及V4/复式/OTHER完整链路通过 |
| diff / secret-pattern scan | 发布提交前核验完整diff及限定版本/文档文件，原有工作区修改和真实材料不stage |

既有SQLAlchemy deferred FK环排序warning保留，未suppress。Release gate没有暴露需要
修改已接受业务逻辑的bug；此次发布仅同步版本和验收状态。

候选HEAD完整CI必须Success后才merge main、创建annotated v0.8.0。随后分别核验main
与tag的exact SHA及完整CI终态。最终main SHA、tag object/target SHA、三条CI URL在最终
交付消息列明，避免报告自引用反复制造发布提交。CI流程不缩减为版本或文档smoke。

迁移head保持 **39526d8f40cb**，wheel显式资源保持 **70**。旧候选70-resource wheel
已经通过隔离import、原历史/V1流程、新V4/复式/OTHER全链路；本次0.8.0 wheel也已重新验证。

## Retained known limitations 与数据边界

- SQLite-only；四种正式市场，无HALF_FULL、同票同场跨市场compound。
- 新cohort/odds ingestion当前为显式synthetic离线fixture边界；不授予真实数据权利，
  真实provider capability和模型表现尚未验证。
- Poisson为BASELINE_UNCALIBRATED independent baseline；home/away各至少5、league10，
  最多1024 facts、lambda20、score256、|handicap|20；越界/unavailable不fallback或调参。
- V2默认preferred choices2/absolute3、每票96 atomic、全计划4096 atomic、512 candidates、
  12 matches、256可重建states、source8MiB；硬拒绝超限，不截断、抽样或OOM后近似。
- OR/AND与比分representatives是确定性结构风险，不是统计correlation。
- VOID、取消/延期退款、官方串关降级、加时/点球/赛中结算仍unsupported；缺赛果不按输处理。
- CVaR、Kelly、收益分布概率及portfolio return-distribution optimization留0.9。
- 本轮不发真实provider/LLM HTTP，不读取或重建已清理的0.6 acceptance材料；不重训、
  不重绑定历史code revision/hash，不改v0.6.0/v0.7.0历史和验收工件。

v0.8.0 main/tag CI全部Success后立即停止，不进入0.9。
