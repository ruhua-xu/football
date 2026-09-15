# Phase 8 — Market Expansion + Simple Multiple Selection

## 状态与基线

- 软件候选分支：`feature/0.8.0-market-expansion`。
- 基线main/v0.7.0：`019a62fb00e60303732520e59890f4e8bfb6086d`。
- Package metadata继续 `0.7.0`，等待一次整体0.8 Architecture Review；本轮不发布0.8。
- 新migration head：`39526d8f40cb`，父head `28415c7e39ba`。
- 新增47张闭集typed graph tables；旧0.7表、触发器和数据保持原值。

## 正式合同与架构

- [ADR-0010](decisions/0010-generic-market-review-and-simple-multiple.md)（Proposed）。
- [Market taxonomy V1](market_taxonomy_v1_contract.md)：3/3/8/31 ordered outcomes、明确home handicap、generic Decimal distribution/odds。
- [Poisson baseline V1](poisson_goals_baseline_v1_contract.md)：固定home/away splits、minimum history、无限tail积分/认证。
- [Analysis Packet / LLM Review V4](analysis_packet_v4_contract.md)：per-market unit binding、严格byte boundary、local generic fusion。
- [Strategy Pass V2](strategy_pass_v2_contract.md)：OR choice sets、Cartesian atomic expansion、结构风险及结算。
- 配置示例：`config/poisson_goals_v1.json`、`config/strategy_profile_v2.json`。

Poisson固定config hash：`111f4b4301057e277a373ade8533108583a1075df8e46043b018615d767cc23b`。
Profile V2默认hash：`3ecd13fbc71528c44894033f2bae5917fb7dd1576f093ec10e00dc967a67cd13`。
模型名称/版本/公式/minimum history/tail/precision/truncation与lambda量化均进入config hash。

## 软件实现

Domain新增独立taxonomy、MarketKeyV2、generic distribution、odds、Poisson state/grid、
market units、V4 contexts/reviews、generic fusion、V2 choice/ticket/settlement。
Application提供显式fixture import、cohort admission、train、consensus、legacy adapter、
analysis、packet、review、fusion、plan、settle use cases及repository port。

Infrastructure的fixture adapter只映射完整同语义market；O/U2.5、两向Asian spread、
不完整比分book明确MARKET_UNAVAILABLE。SQLite用闭集artifact registry、具体类型FK、
quote/catalog/choice/atomic/result children及deferred seal保证完整原子图；所有source与
数学在保存/读取重演。Transaction-local DAG memoization只复用同一事务已验证数据。

CLI为 `football-system market-v2`，规则在domain/application，不在CLI硬编码。
V4只导出足球facts、generic P_market/P_quant与lineage/context，排除P_final/EV/money。
THREE_WAY从已存在、固定config的Elo source只读适配；新市场P_quant仅来自Poisson事实模型。

## A–P deterministic acceptance

| Case | 已验证结果 |
|---|---|
| A | 原2X1/3X4/4X11 V1 plan、ticket、settlement bytes/hash与发布版golden一致；单选本金/奖金和replay保持 |
| B | 2-1/-1→DRAW；3-1/-1→HOME_WIN；1-2/+1→DRAW；1-3/+1→AWAY_WIN |
| C | 2-1→GOALS_3；4-3和5-2→GOALS_7_PLUS；0-0→GOALS_0 |
| D | 3-1/4-1/5-1进入各自显式SCORE；6-1/4-4/1-6进入正确OTHER；0-0→SCORE_0_0 |
| E | 指定五组lambda及0/0、20/20验证确定性、无负值、三类新市场sum=1、完整7+ tail、OTHER与稳定hash |
| F | 1/2 choices的2X1展开2个atomic，unit stake400；互斥选项不能同时计入max payout |
| G | 1/2/2 choices的3X4，AB2+AC2+BC4+ABC4=12，unit stake2400 |
| H | 1/2/2/1 choices的4X11，pairs13+triples12+quad4=29，unit stake5800 |
| I | 跨比赛THREE_WAY与TOTAL_GOALS mixed-market生成、封存、raw normalized score结算通过 |
| J | SCORE_3_1/4_1合格，SCORE_5_1负EV时只保留前两项，并保存明确排除原因 |
| K | 过大atomic/state/candidate输入在materialization前拒绝；SQL artifact数量不变，无partial persistence |
| L | ANALYSIS_PACKET_V1/V2/V3、LLM_REVIEW_V1/V2/V3及normalization bytes与发布版golden一致 |
| M | 4 matches×4 markets=16个market units；含真实旧Elo来源适配、new-market consensus/Poisson、V4 export/validate/import/fusion及exact retry |
| N | 所有新SP fixture在原EV门槛下无价值时NO_BET，stake0，不强行生成资金票 |
| O | 同一ticket同一match混入THREE_WAY与TOTAL_GOALS choice graph被拒绝 |
| P | HOME_OTHER、DRAW_OTHER、AWAY_OTHER按各自OTHER SP计奖；不匹配nearest explicit score |

Unit手算SP fixture（A2、B6/8、C40/100、D6）：F最大单倍3200，G523200，H3794400 fen。
完整持久化fixture使用自己的A4、B6/8、C40/100、D5：实际F/G/H毛返还分别160000、
3545600、5430400 fen；倍数和原预算均封存，两个fixture族分别按各自输入验证。
这些是软件验收数字，不代表真实赔率、命中率或ROI。

## 旧合同与升级证明

`data/fixtures/legacy_v070_market_goldens.json`由原发布wheel独立生成，reference wheel
SHA256=`763967f327bee230e71e04889e27dacd7868e926ddac94937d52634f320f7b45`。
新测试对照packet/review canonical bytes、normalization、V1 plan/ticket/settlement
bytes/hash及单选数学；另以多组固定confidence/quality/cap逐值对照旧三向delta kernel。

升级测试从Git不可变v0.7.0 tree运行原代码创建库和三种V1 plans，然后用新migration
升级：比较所有旧表定义、旧触发器SQL和旧rows的原值。随后V1 read/show/replay/save/
settle继续工作，不转换为V2。Fresh downgrade/re-upgrade和populated downgrade refusal
单独测试。旧release/artifacts没有被重写以适应新的source code revision。

## 门禁与终态记录

本地全仓回归完整核对2584个collected/unique node IDs：2583 passed、1个Windows下的
POSIX专用skip；其中108项新增测试全部通过。采用121个有界隔离分区，唯一初始失败为
旧migration测试把“当前head”硬编码为0.7 head；改为查询实际Alembic head后复验该分区
通过。旧模型、数学、wire和V1业务实现没有为此修改，失败记录保留且不重复计数。

| 门禁 | 结果 |
|---|---|
| pytest / old goldens | 2584用例完整去重覆盖，2583 passed / 1 skipped；A–P、旧packet/review和V1票据/结算golden通过 |
| Ruff / compileall | 全仓通过 |
| fresh migration/check | 新库upgrade head39526d8f40cb及command.check通过 |
| empty downgrade/re-upgrade | 空图回退28415c7e39ba后再次升级/check通过 |
| existing-data upgrade | 从不可变v0.7.0代码创建旧库，旧表定义、触发器SQL、rows与serialized artifacts全部保持；V1 read/show/replay/settle通过 |
| populated downgrade / corruption | 拒绝有数据回退、部分图commit、UPDATE/DELETE/REPLACE；损坏expanded leg图读取被拒绝 |
| wheel build / installed E2E | 版本0.7.0、70资源、隔离import；原历史/V1流程及multi-market/V4/复式/OTHER完整链路通过 |
| whitespace / secret-pattern scan | 提交前核对完整diff和目标文件；不纳入真实材料或原有工作区修改 |

远端CI URL与最终implementation SHA在交付消息关联，不能以旧版本或旧候选CI代替。

Wheel显式资源70项，版本保持0.7.0。Installed-wheel验收继续执行原历史/V1流程，另以
隔离import运行完整multi-market/V4/simple-multiple/OTHER结算fixture。软件测试均offline，
没有真实provider或LLM HTTP请求，没有读取或重建已删除的0.6 acceptance数据。

## 已知限制

- 新cohort/odds ingestion当前为明确标注synthetic的离线fixture边界；不授予真实来源权利，
  不声明真实provider支持这些market catalogs。真实来源适配与模型表现尚未验证。
- 只有THREE_WAY、HANDICAP_THREE_WAY、TOTAL_GOALS、CORRECT_SCORE；无HALF_FULL。
- Poisson baseline未校准；minimum home/away各5、league10，最大1024 facts、lambda20、
  score256、|handicap|20，越界/unavailable不fallback、不调参。
- 少量same-market choices：preferred2/absolute3，默认每票96 atomic、全计划4096 atomic、
  512 candidates、12 matches、256可重建states、source8MiB。超限硬拒绝，不截断或抽样。
- OR/AND与常规比分representatives是确定性结构风险，不是统计correlation。
- VOID、取消/延期退款、官方串关降级、加时/点球/赛中结算仍unsupported；缺赛果不按输处理。
- CVaR、Kelly、P(return>0)、预算倍数概率及portfolio return-distribution optimization留0.9。

候选完成后停止，等待网页GPT的一次整体0.8 Architecture Review；不merge main、不bump0.8、不建v0.8.0 tag。
