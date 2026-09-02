# Football System 0.4.0 Overnight Implementation Task

当前 GitHub 最新设计提交已经确认。

本次允许进行一个较长的连续开发任务。

目标是在现有 0.3.0 基础上，尽可能完整实现：

**Historical Data Archive + MatchResult + Settlement + Walk-Forward Backtest + Metrics + CLI + CI**

本任务允许连续工作数小时。

不要每完成一个小模块就停止等待人工确认。

只要没有遇到以下情况，请自行选择最保守、可回测、可解释的实现继续推进：

* 需要真实 API Key
* 需要付费账号
* 需要接受无法确认的数据授权条款
* 需要删除或覆盖不可恢复历史数据
* 需要修改已经冻结的 0.3.0 LLM 文件协议
* 发现会导致历史数据泄漏的根本设计冲突

如果遇到普通代码错误、测试失败、migration 问题、类型问题、Schema 问题或算法实现问题，请自行修复并继续，不要停下来等待用户。

---

# 一、冻结现有稳定能力

不得重写或破坏：

P_market

P_quant

P_final

QUANT_ONLY_V1

MARKET_QUANT_BLEND_V1

LLM_REVIEW_DELTA_V1

Selection EV

竞彩2元计奖逻辑

2串1 TicketCandidate

Portfolio Optimizer

Risk Exposure

Stress Test

ANALYSIS_PACKET_V1/V2

LLM_REVIEW_V1/V2

FusionRun

PortfolioRevision

AnalysisRun append-only / immutable 规则

现有 0.3.0 Web GPT 离线协作闭环必须保持全部测试通过。

---

# 二、本次版本目标

若全部验收通过，将项目版本提升为：

```text
0.4.0
```

本阶段最终数据流应达到：

```text
Historical Archive
       ↓
历史 Fixture / Odds / Sporttery / Quant
       ↓
decision_as_of_at_utc
       ↓
immutable AnalysisRun
       ↓
P_market / P_quant / P_final
       ↓
SelectionCandidate
       ↓
Ticket / Portfolio
       ↓
Risk / Stress
       ↓
evaluation_as_of_at_utc
       ↓
MatchResult
       ↓
Ticket Settlement
       ↓
Portfolio Settlement
       ↓
Backtest Metrics
       ↓
Backtest Report
```

第一阶段暂时不把网页 GPT 加入历史回测。

历史回测默认只验证：

P_market

P_quant

deterministic FusionPolicy

EV

Portfolio

Risk

Settlement

---

# 三、建立历史归档格式

不要先绑定真实网站。

定义 provider-neutral 的版本化历史归档格式。

建议：

```text
HistoricalArchiveManifest

archive_schema_version
archive_id
provider_code
dataset_kind
created_at_utc
source_reference
source_description
license_note
data_mode
payload_sha256
record_count
```

至少支持以下归档类型：

```text
FIXTURES
MARKET_ODDS
SPORTTERY_BONUS
MANUAL_QUANT
MATCH_RESULTS
PROVIDER_MAPPINGS
```

每个归档文件必须：

* UTF-8
* Schema 版本明确
* checksum 可验证
* 不接受 NaN / Infinity
* 不接受重复业务主键
* 时间必须 timezone-aware
* 不静默修正非法记录
* 导入前执行完整 validation
* 导入后保持 append-only

禁止把一个巨型 JSON 同时装所有业务对象。

不同数据类型保持独立文件或独立 logical section。

---

# 四、HistoricalDataProvider 正式实现

基于已经冻结的 Port，实现：

```text
LocalArchiveHistoricalDataProvider
```

用于读取本地只读历史 MatchResult Archive。

必须支持：

```text
MatchResultQuery
match_ids
as_of_at_utc
```

行为：

只返回 cutoff 前可用的结果。

同一比赛存在多个结果版本时：

选择 cutoff 前最新合法版本。

结果更正通过：

```text
supersedes_match_result_id
```

表示。

不能修改旧 MatchResult。

必须实现 Contract Tests，包括：

正常结果

空结果

部分结果

赛果更正

cutoff 前旧结果

cutoff 后更正结果不可见

重复比赛结果版本

错误 mapping

错误 hash

时间字段非法

跨 provider mapping

---

# 五、为历史决策输入建立 Local Archive Provider

为了能够真正跑历史 AnalysisRun，增加本地归档 Adapter。

建议实现：

```text
HistoricalArchiveFixtureProvider
HistoricalArchiveMarketOddsProvider
HistoricalArchiveSportteryProvider
HistoricalArchiveQuantProvider
```

它们实现现有 Port，不修改现有 Application 层接口。

第一版数据可以使用静态测试归档。

所有 Provider 必须按照：

```text
as_of_at_utc
```

选择确定性的合法版本。

赔率不能使用比赛之后的快照。

竞彩固定奖金不能使用比赛之后的奖金。

若找不到合法输入：

显式缺失。

不得自动寻找未来最近值。

---

# 六、明确历史回测 Data Mode

这里需要特别谨慎。

保留并明确区分：

```text
LIVE_STRICT
SOURCE_TIME_RESEARCH
```

如果你认为目前架构不适合立即实现第二种模式，可以先完成 Domain/ADR/Schema 边界，但不能把二者混在一起。

`LIVE_STRICT`：

适用于系统真正实时保存的数据。

要求：

```text
captured/observed <= available <= ingested <= decision cutoff
```

`SOURCE_TIME_RESEARCH`：

用于后来获得的历史归档。

例如 2026 年下载 2024 年已经公开的历史赔率数据。

这种模式不能伪装成“本系统 2024 年已经保存”。

如果实现该模式：

必须显式标记：

```text
retrospective = true
```

决策 cutoff 应约束可信的 source observed/available time。

本地实际 import 时间单独保存。

研究模式结果必须在 Backtest Report 中醒目标记：

```text
RETROSPECTIVE_SOURCE_TIME_RESEARCH
```

不得与 LIVE_STRICT 指标静默混合。

如果无法可靠确定 source historical availability timestamp：

记录为不满足严格 point-in-time。

不要伪造 timestamp。

请把最终裁决写成 ADR。

---

# 七、数据库正式加入 MatchResult

新增 append-only 表。

建议：

```text
match_results

match_result_id PK
internal_match_id FK
provider_id FK
home_goals
away_goals

observed_at_utc
available_at_utc
ingested_at_utc

source_result_key
payload_hash

supersedes_match_result_id NULL
```

必要约束：

```text
home_goals >= 0
away_goals >= 0
```

同 provider/source_result_key 不重复。

supersedes 必须指向：

同一比赛
同一 provider
更早版本

MatchResult 不属于 AnalysisRun 决策输入。

绝对不能加入：

```text
input_manifest
AnalysisMatchContext
analysis_packet
LLM review context
```

---

# 八、Settlement Engine

实现正式：

```text
SettlementService
```

第一版只支持：

```text
THREE_WAY
2X1
BACKTEST
```

不支持：

取消
腰斩
VOID
退款
加时
点球
串关降级

遇到这些情况必须明确返回：

```text
UNSUPPORTED_SETTLEMENT_CASE
```

而不是猜测规则。

Ticket Settlement 必须使用：

冻结 Ticket

冻结 stake

冻结 potential_gross_payout

冻结 payout_policy_version

不能重新读取当前赔率。

对于2串1：

两腿全部命中：

```text
status = WON
gross_payout = frozen potential_gross_payout
```

任意一腿失败：

```text
status = LOST
gross_payout = 0
```

结果缺失：

不创建假 Settlement。

记录 coverage 缺失原因。

---

# 九、Portfolio Settlement

实现：

```text
PortfolioSettlement
```

聚合：

Ticket settlements

原 CashPosition

计算：

```text
budget
deployed_stake
cash
gross_ticket_payout
ending_capital
profit_loss
roi_on_budget
roi_on_deployed
```

其中：

```text
ending_capital =
cash +
sum(ticket gross payout)
```

必须区分：

```text
ROI on budget
ROI on deployed capital
```

防止大量留 Cash 时指标误导。

---

# 十、支持原 AnalysisRun 与 PortfolioRevision 两种结算

Settlement scope 必须支持：

```text
BASE_ANALYSIS_RUN
PORTFOLIO_REVISION
```

但第一版历史回测可以只使用：

```text
BASE_ANALYSIS_RUN
```

接口和数据库需要预留 Revision。

未来真实前瞻数据可以比较：

```text
原 Portfolio
vs
GPT Review PortfolioRevision
```

不得为了实现历史回测修改已有 PortfolioRevision。

---

# 十一、Settlement append-only

新增：

```text
ticket_settlements
portfolio_settlements
```

必须 append-only。

赛果后来更正时：

```text
Settlement V2
supersedes_settlement_id -> Settlement V1
```

禁止：

UPDATE
DELETE
INSERT OR REPLACE

继续沿用现有 immutable trigger 设计。

---

# 十二、Backtest Domain

新增：

```text
BacktestRun

BacktestSlice

BacktestStrategySnapshot

BacktestMetrics
```

BacktestRun 至少包含：

```text
backtest_run_id

backtest_version

data_mode

date_from
date_to

strategy_config_json
strategy_config_hash

code_revision

created_at_utc

status
```

BacktestSlice：

```text
slice_id
backtest_run_id

decision_as_of_at_utc
evaluation_as_of_at_utc

analysis_run_id

match_count
settled_ticket_count
unsettled_ticket_count
coverage
```

所有 BacktestRun 必须可以回放。

---

# 十三、Walk-forward Backtest Engine

实现：

```text
WalkForwardBacktestService
```

第一版使用固定 slate policy。

不要直接实现复杂 T-24h per-match policy。

建议一个 slate：

```text
同一天 / 同一批比赛
```

共享：

```text
decision_as_of
evaluation_as_of
```

完整流程：

```text
for each chronological slate:

    select historical inputs <= decision cutoff

    run immutable AnalysisRun

    freeze predictions / EV / Portfolio / Risk

    load results <= evaluation cutoff

    settle every complete Ticket

    aggregate Portfolio result

    save BacktestSlice

finally:
    aggregate BacktestMetrics
```

严格禁止：

先读取 result
再创建 AnalysisRun。

代码结构也应让这一点尽可能不可能发生。

---

# 十四、实现概率评价指标

对每场已结算比赛分别评价：

```text
P_market
P_quant
P_final
```

至少实现：

### Multiclass Brier Score

以及三项 outcome 分项统计。

### Multiclass Log Loss

必须使用版本化 epsilon clip，防止：

```text
log(0)
```

epsilon 从配置读取。

### Calibration

第一版实现简单 probability bins。

例如：

```text
0.0-0.1
0.1-0.2
...
0.9-1.0
```

输出：

预测平均概率

实际发生率

样本数

可以计算简单：

```text
ECE
```

但不要用复杂 calibration library。

---

# 十五、实现投注与资金指标

Backtest 至少输出：

```text
slate_count

match_count
settled_match_count
match_coverage

ticket_count
settled_ticket_count
ticket_coverage

total_budget
total_stake
total_cash

gross_payout
profit_loss

ROI_on_budget
ROI_on_deployed

ticket_hit_rate

NO_BET_count
NO_BET_ratio

max_drawdown

max_consecutive_losing_slates
```

同时报告：

```text
average_ticket_odds
average_ticket_probability
average_selection_EV
```

---

# 十六、风险实现效果统计

把现有 Portfolio Risk 和历史实现结果连接起来。

至少统计：

```text
max_match_exposure
max_selection_exposure

realized_loss_when_top_exposure_failed

realized_loss_when_top_two_exposure_failed
```

第一版不用训练风险参数。

只把数据记录下来。

以后再比较：

```text
0.40
0.50
0.60
```

不同 exposure 上限。

---

# 十七、实现策略比较能力

增加一个简单：

```text
backtest compare
```

第一版至少比较：

```text
QUANT_ONLY_V1
MARKET_QUANT_BLEND_V1
```

相同数据

相同时间窗口

相同 budget

相同 EV threshold

相同 Portfolio constraints

分别运行。

输出并排：

```text
Brier
LogLoss
ROI
Drawdown
NO_BET
Ticket Hit Rate
```

不要自动宣布“最优策略”。

只报告结果。

---

# 十八、不要现在自动调参

禁止实现：

网格搜索自动选择最佳参数

贝叶斯优化

遗传算法

自动挑最高 ROI 参数

利用整个历史区间反向选择 threshold

这会过早导致 overfit。

本阶段允许：

用户显式指定多套 Strategy Config

然后系统分别回测。

---

# 十九、准备确定性历史测试集

为了让 0.4.0 今晚可以完整验收，即使没有真实历史 API，也必须准备一套静态：

```text
data/fixtures/historical_acceptance/
```

建议至少：

```text
10～20 个 chronological slates
每 slate 4～8 场比赛
总计至少约 60 场
```

这个数据集可以是明确标注的：

```text
SYNTHETIC_ACCEPTANCE_DATA
```

绝对不能声称是真实历史赛事。

所有记录必须静态写入文件。

不要测试运行时随机生成。

需要包含：

主胜
平局
客胜

高赔率
低赔率

正 EV
负 EV

NO_BET slate

Portfolio 留 Cash

赛果更正

结果缺失

赔率 cutoff 边界

赔率在 cutoff 后才出现

Mapping 冲突

数据缺失

这样能测试整个系统。

---

# 二十、如果能联网，可以做真实数据源研究，但不能阻塞主任务

完成核心 0.4.0 后，如果还有时间，可以研究：

football-data.org

football-data.co.uk

Sportmonks

竞彩官方历史数据

但是：

不要在没有确认授权和时间字段语义的情况下把第三方数据提交 GitHub。

如需下载研究样本：

放在：

```text
data/raw/
```

并加入 `.gitignore`。

保存：

source URL

downloaded_at

SHA256

license / terms note

字段说明

生成：

```text
fankui/historical_source_research.md
```

如果网络失败或许可不明确：

记录原因。

不要阻塞 0.4.0 其他实现。

---

# 二十一、CLI

实现清晰 CLI。

建议至少：

```text
football-system historical-archive validate

football-system historical-archive import

football-system match-results list

football-system settlement create

football-system settlement report

football-system backtest run

football-system backtest report

football-system backtest compare
```

不要创建多个临时脚本替代正式 Application Service。

临时验收脚本只能放：

```text
scripts/
```

---

# 二十二、Backtest 配置

增加版本化：

```text
config/backtest.toml
```

建议：

```text
[backtest]
version = "BACKTEST_V1"
data_mode = "LIVE_STRICT"
log_loss_epsilon = "0.000001"

[backtest.slates]
policy = "DAILY_FIXED_CUTOFF_V1"

[settlement]
policy = "THREE_WAY_2X1_BACKTEST_V1"
```

具体时间和预算通过 CLI 或独立 fixture config 输入。

不要把测试参数散落写死在 Python。

---

# 二十三、数据库迁移

新增正式 Alembic migration。

至少包含：

```text
match_results

ticket_settlements

portfolio_settlements

backtest_runs

backtest_slices

backtest_metric_snapshots
```

如果某些指标更适合 JSON Snapshot，也可以保留 JSON，但必须：

版本化

hash

append-only

Repository 校验。

继续测试：

fresh database upgrade

0.3.0 -> 0.4.0 upgrade

foreign_key_check

alembic check

runtime schema vs migration schema

---

# 二十四、GitHub Actions

现在必须加入 CI。

创建：

```text
.github/workflows/ci.yml
```

至少在：

push

pull_request

执行：

```text
Python 3.12

pip install project/dev dependencies

ruff check

python -m compileall

pytest

alembic upgrade head

alembic check

wheel build
```

如果某项在 CI 环境需要临时 SQLite：

自行创建。

不要依赖本机路径。

CI 不允许使用真实 API Key。

---

# 二十五、Secret Safety

仓库目前是 public。

执行一次安全检查。

确认 Git 中没有：

.env

真实 API Key

token

cookie

真实付费数据

私人数据库

如果可以，增加轻量 secret scan。

至少：

```text
git grep
```

检查常见 Key pattern。

不要因为仓库公开而上传真实历史付费数据。

---

# 二十六、测试要求

新增测试至少覆盖：

Historical Archive Schema

checksum

重复记录

非法时间

Provider cutoff

result correction

MatchResult three-way outcome

Settlement WON

Settlement LOST

result missing

wrong match result

cross-scope settlement

Settlement correction

Portfolio cash aggregation

ROI on budget

ROI on deployed

decision/evaluation cutoff isolation

future result leakage rejection

future odds leakage rejection

walk-forward chronological order

Brier

LogLoss

Calibration bin

max drawdown

loss streak

NO_BET

partial coverage

QUANT_ONLY vs MARKET_QUANT comparison

fresh migration

0.3 -> 0.4 migration

append-only MatchResult

append-only Settlement

append-only BacktestRun

CLI e2e

wheel e2e

---

# 二十七、测试数据必须有 Golden Case

至少创建几个手工可计算案例。

例如：

Ticket:

A HOME @ 1.90

B DRAW @ 3.20

stake 20 元

最终：

A 主胜

B 平局

程序计算的返还必须能人工验证。

再创建一个：

一腿失败

返还 0。

再创建：

Portfolio

两个 Ticket

Cash 40 元

确保：

```text
ending_capital
profit_loss
ROI
```

人工计算一致。

---

# 二十八、回测验收样例

今晚最终必须跑出至少一套完整的 synthetic walk-forward。

例如：

```text
60+ matches
10+ slates
```

输出：

```text
P_market Brier
P_quant Brier
P_final Brier

P_market LogLoss
P_quant LogLoss
P_final LogLoss

total budget
stake
cash

gross payout
profit
ROI

max drawdown

NO_BET ratio

ticket hit rate
```

再执行：

```text
QUANT_ONLY_V1
vs
MARKET_QUANT_BLEND_V1
```

并生成：

```text
exchange/backtest_acceptance_report.md
```

数据必须明确写：

```text
SYNTHETIC ACCEPTANCE DATA
NOT REAL HISTORICAL PERFORMANCE
```

避免以后误认为是真实回测结果。

---

# 二十九、文档

更新：

README.md

CHANGELOG.md

fankui/historical_data_backtest.md

新增：

```text
fankui/backtest_v1_contract.md

fankui/phase_4_implementation_report.md

fankui/historical_source_research.md
```

如果 research 阶段没有完成，可以说明未完成原因。

---

# 三十、版本管理

建议在新分支：

```text
feature/0.4.0-historical-backtest
```

开发。

分阶段 commit：

```text
feat: add historical archive ingestion

feat: add match result persistence

feat: add backtest settlement engine

feat: add walk-forward backtest

feat: add backtest metrics and reports

test: add historical backtest acceptance suite

ci: add python validation workflow

docs: complete 0.4.0 historical backtest documentation
```

每次 commit 前运行相关测试。

最终所有验收通过后：

合并或 fast-forward 到 main。

push main。

版本更新为：

```text
0.4.0
```

如果仍有阻断性测试失败：

不要强行把失败版本 merge 到 main。

把 feature branch push 到 GitHub。

生成 blocker report。

保留可继续工作的现场。

---

# 三十一、完成条件

只有同时满足以下条件，才能宣告 0.4.0 完成：

现有 0.3.0 所有测试仍通过。

Historical Archive 可验证和导入。

MatchResult append-only。

Settlement 正确。

Portfolio Settlement 正确。

Walk-forward 无结果泄漏。

Brier/LogLoss/Calibration 正确。

ROI/Drawdown 等指标正确。

QUANT_ONLY / MARKET_QUANT 可比较。

synthetic 60+ match acceptance backtest 完整运行。

fresh migration 成功。

0.3 -> 0.4 migration 成功。

CLI 成功。

wheel 安装后 CLI 成功。

GitHub Actions 配置完成。

没有 secret 泄漏。

最终报告生成。

---

# 三十二、完成后停止

0.4.0 完成后不要继续开发：

机器学习 P_quant

Elo

Poisson

LightGBM

CatBoost

真实 LLM API

自动下注

3串4

4串11

Web Dashboard

定时任务

真实生产数据抓取

这些留到下一阶段单独审查。

---

# 三十三、最终报告

生成：

```text
fankui/phase_4_implementation_report.md
```

至少说明：

Git commit

version

migration head

测试数量

CI 配置

新增数据库表

Historical Archive 合同

Settlement 规则

Backtest 时间语义

synthetic acceptance 数据量

QUANT_ONLY 结果

MARKET_QUANT_BLEND 结果

Brier

LogLoss

ROI

Drawdown

coverage

已知限制

未实现项目

下一阶段建议

最后 push GitHub。

完成后停止等待下一次架构审查。
