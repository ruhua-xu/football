# Phase 7 — Strategy Profile / Pass Type Engine 软件候选

- 分支：`feature/0.7.0-strategy-pass-engine`
- 基线：正式 `v0.6.0`，`7ded7dbf62dc64a35b7788142a5bf81610353563`
- Package/runtime metadata：`0.6.0`
- 状态：功能候选；提交后以同SHA的完整CI和最终交付记录核验门禁，等待一次整体架构验收。
- 合同：[Strategy Pass V1](strategy_pass_v1_contract.md)
- Profile：[默认配置](../config/strategy_profile_v1.json)
- 决策：[ADR-0009](decisions/0009-versioned-strategy-pass-engine.md)，Proposed。

## 实现摘要

1. 新增版本化 `StrategyProfileV1`、来源/父血缘、system ticket、AtomicBet、payout states、
   结构风险和完整plan seal。旧TicketCandidate限定2X1，旧序列化保持原值。
2. 2X1/3X4/4X11按combinations准确分成1/4/11子注。使用原200 fen单位、
   ROUND_HALF_EVEN、整数倍数≤50及票本金≤600000 fen；各子注先舍入再乘倍数、相加。
3. PRIMARY/SECONDARY/HEDGE/LONGSHOT与preferred4/absolute8；辅助票最多一张，默认
   stake≤原预算10%。HEDGE绑定具体核心失败/新合格selection/存活子注证据。
4. Match/selection exposure、overlap、按子注本金计的core dependency、共同单selection
   死亡检查同时作用于开票和加倍。沿用原边际score和逐轮加倍方法，允许现金和NO_BET。
5. Application/port/SQLite repository只读取已有AnalysisRun或PortfolioRevision的
   概率、SP、门槛、rules、constraints及已有预算，精确重验原候选。生产来源继续调用
   concrete ProductionAuditGuard，包含直接调用、读取和exact retry的当前授权检查。
6. 新增9张表及迁移 `28415c7e39ba`，父head `17304b6d28a9`。Typed FK、append-only、
   防REPLACE、完整子图deferred seals；保存/读取重演全部经济计算和来源，禁止部分提交。
7. 独立 `THREE_WAY_SYSTEM_PASS_BACKTEST_V1` 结算按实际中奖子注求和，保留partial payout。
   更正追加直接supersession，拒绝平行root/fork；missing或unsupported保留未结算状态。
8. CLI提供profile/schema、build/show、settle/settlement-show。显式选择父预算，
   不提供概率、EV或SP覆盖参数。新export有64MiB上限及相同内容幂等写入。

固定Elo模型/config、融合、EV、V3 wire、原资金函数、旧迁移和既有真实验收工件保持冻结。
本轮只运行synthetic/fixed fixtures，未请求数据API、LLM API或改写真实验收库。

## A–E 固定验收

数据：[strategy_pass_v1.json](../data/fixtures/strategy_pass_v1.json)，全部为invented
probabilities/prices。每场home probability=0.6，其他两向各0.2，其他两向SP=1.1。
下表为domain固定输入、10000 fen预算的可重演结果；repository/CLI另验证实际父工件接线。

| Case | 合格输入与生成能力 | 默认/指定profile结果 |
|---|---|---|
| A | 两场，SP2/3；仅1个2X1候选 | 1张2X1，stake10000，cash0 |
| B | 三场，SP2/3/4；3个2X1 + 1个3X4 | 按原ROI/结构约束选择1张2X1；3X4另以指定pass profile完成资金/结算验证 |
| C | 四场，SP2/3/4/5；6个2X1 + 4个3X4 + 1个4X11 | 默认4张3X4，各2400，cash400；4X11另以指定pass profile完成资金/结算验证 |
| D | 四场SP均1.1，所有EV低于threshold | NO_BET，stake0，cash10000 |
| E | 一场SP4、三场SP1.7；ticket ROI门槛0.20 | 2X1 profile的高价值票全部共用核心，减少为1张，记录STRUCTURAL_CONCENTRATION_LIMIT |

数量偏好不是必须填满的配额；生成某pass的能力和原ROI/风险最终选择分别有明确测试。
手算单倍golden：2X1本金200/最大毛奖金1200；3X4本金800/最大10000；
4X11本金2200/最大69000，其中6个二串一14200、4个三串一30800、1个四串一24000。
只有SP2/3正确时仅一个二串一中奖，毛奖金1200。独立测试同时覆盖倍数和fen舍入。

## 验证入口与记录

新增85项测试已在本地全部通过：domain/profile/pass/risk 44、system settlement 15、repository/migration 21、
mandatory production audit 1、CLI 4。包括字段篡改后重封、负EV角色hint、零预算、
单倍不可负担的pass降级、core约束在加倍时继续生效、旧PortfolioRevision字节保持、
SQL缺腿/错价/错比赛/替换/封存后追加、损坏读取、重试、结算更正和fork拒绝。

本地已核验fresh upgrade/check、带旧数据upgrade/check、空图downgrade/re-upgrade及有图
downgrade拒绝；Ruff、compileall和隔离wheel验证入口如下：

```text
python -m ruff check .
python -m compileall -q src tests migrations scripts
python -B -m pytest -o pythonpath=src
python -m build --wheel --outdir <isolated-candidate-directory>
python -B scripts/wheel_e2e.py <candidate-wheel> --work-dir <isolated-empty-directory>
git diff --check
```

隔离wheel结果：60个显式资源，metadata0.6.0，head28415c7e39ba；确认从隔离安装路径
import，原8条历史CLI、两套10-slate回测/报告/settlement，以及新3X4和4X11生成、
exact retry、normalized result refs与settlement均通过。

旧历史acceptance fixture的票上限600 fen正确阻止3X4；该拒绝保留为wheel回归。
正向system settlement使用另行创建的MOCK父工件，未放宽旧fixture的预算/规则。

上次单进程全仓运行在中途被中断，不能作为完整通过记录。最终交付须核验覆盖全部
collected node IDs的有界本地分区结果及同实现SHA的完整远端CI；具体终态和CI URL
随交付消息记录。[分支CI](https://github.com/ruhua-xu/football/actions/workflows/ci.yml?query=branch%3Afeature%2F0.7.0-strategy-pass-engine)。

## 已知限制与验收停点

- SQLite；每票每场一个THREE_WAY outcome，不支持多选复式。市场扩展留0.8。
- 独立腿期望及结构风险，不估计统计相关性。收益分布概率、CVaR、Kelly及分布优化留0.9。
- 默认最多12场/4096候选，显式上界拒绝整单，不静默截断或降低EV门槛。
- V1新来源要求SP三个时间均不晚于父cutoff；不补造迟到SP的历史可见性。
- 取消、VOID、退款、加时/点球及串关降级继续unsupported；缺赛果不虚构输赢。
- Synthetic验证只证明软件合同。候选停在feature分支，整体架构验收后再决定发布。
