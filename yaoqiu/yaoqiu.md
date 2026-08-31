# 足球比赛智能分析与竞彩串关决策系统

## 项目总体设计与开发要求

你现在作为本项目的首席软件架构师、量化分析工程师、足球数据工程师和 Python 开发工程师，协助我从 0 开始设计并实现一个长期可维护的足球比赛智能分析与竞彩串关决策系统。

本项目不是一个简单的“预测足球胜平负”程序，也不是单纯根据赔率计算 EV 的脚本。

系统最终希望建立一种：

**固定量化计算框架 + 实时足球数据 + GPT-5.6 Sol 动态分析 + 串关组合优化 + 风险控制 + 历史回测**

相结合的混合决策系统。

系统主要用于个人足球竞彩研究和决策辅助，不实现自动下注。

---

# 一、项目总体目标

系统最终需要逐步具备以下能力。

每天自动获取约 20～100 场足球比赛。

自动获取和保存：

* 比赛基本信息
* 联赛信息
* 球队信息
* 开赛时间
* 竞彩官方固定奖金
* 国际市场赔率
* 赔率历史变化
* 联赛排名
* 球队近期表现
* 主客场表现
* 进球和失球数据
* xG 等高级统计
* 伤停
* 停赛
* 预计首发
* 正式首发
* 赛程密度
* 历史交锋

以后可继续增加：

* 新闻
* 教练发布会信息
* 球员状态
* 天气
* 场地
* 旅行距离
* 战意
* 欧战或杯赛轮换
* 市场异常变化

系统根据这些数据分别得到：

* 市场概率 P_market
* 量化模型概率 P_quant
* GPT 动态分析概率 P_llm
* 最终融合概率 P_final

然后计算：

* 赔率价值
* EV
* 风险
* 数据可信度
* 市场异常
* 主剧本
* 次剧本
* 冷门剧本

随后自动生成和比较：

* 单场候选方向
* 2串1
* 3串4
* 4串11
* 简单复式
* 主攻票
* 次主攻票
* 冷门票
* 反向高赔率保护票

最后根据：

* 100 元预算
* 200 元预算
* 或用户输入的任意预算

自动给出不超过 4 张的最终候选票，并说明：

* 为什么选择
* 每张票的定位
* 投入金额
* 理论收益
* 风险
* 哪场比赛是核心风险点
* 哪张票对哪种主剧本失败提供保护

---

# 二、项目最重要的设计理念

本项目不要把所有判断写死在代码中。

系统必须明确区分：

## 1. 固定计算部分

这些功能需要保持稳定、确定、可重复和可回测。

包括：

* 数据获取
* 数据标准化
* 数据库
* 比赛身份映射
* 球队身份映射
* 赔率保存
* 赔率时间序列
* 特征工程
* 市场概率计算
* EV 计算
* 量化概率模型
* 概率校准
* 串关组合枚举
* 收益计算
* 风险计算
* 资金优化
* 回测
* 日志
* 结果统计

这些模块不能因为每天分析不同比赛而频繁修改。

---

## 2. GPT 动态分析部分

GPT-5.6 Sol 负责处理那些难以完全写成固定规则，同时每天情况都会变化的信息。

例如：

* 战意
* 轮换
* 杯赛影响
* 主力缺阵的重要程度
* 新教练影响
* 球队近期状态是否具有欺骗性
* 市场赔率变化是否合理
* 热门球队是否被高估
* 是否存在赔率和基本面背离
* 是否适合作为串关主轴
* 是否存在明显冷门风险
* 主队如果不赢，最可能出现什么剧本
* 某场失败后应该用什么方向进行保护
* 哪几场比赛存在相同的风险来源

GPT 的任务不是代替数学模型，而是给固定模型提供动态判断。

---

# 三、系统核心思想

系统最终形成如下数据流程：

```text
比赛原始数据
     ↓
数据清洗和标准化
     ↓
统一比赛身份
     ↓
特征工程
     ↓
┌────────────────────────────┐
│                            │
市场赔率                  基本面数据
│                            │
↓                            ↓
P_market                  P_quant
│                            │
└─────────────┬──────────────┘
              ↓
        GPT-5.6 Sol
              ↓
           P_llm
              ↓
      概率融合与风险评估
              ↓
           P_final
              ↓
         单场价值计算
              ↓
        串关候选生成
              ↓
        组合风险分析
              ↓
          资金优化
              ↓
       最终最多4张票
```

---

# 四、必须区分四类概率

系统必须长期保存并分别评估以下四类概率。

## P_market

市场隐含概率。

来源主要包括：

* 中国竞彩赔率
* 国际博彩公司赔率
* 多家公司平均赔率

需要考虑庄家利润率。

例如原始隐含概率：

$$
q_i=\frac{1}{O_i}
$$

去除 overround 后：

$$
P_{market,i}
=
\frac{q_i}{\sum_j q_j}
$$

后续可以进一步采用更合理的去水方法。

---

## P_quant

固定量化模型产生的概率。

初期可以从简单模型开始。

例如：

* Elo
* 主客场
* 近期进球
* 近期失球
* xG
* xGA
* 联赛排名
* 休息时间
* 赛程密度
* 伤停
* 历史状态

第一版不要立即上复杂深度学习。

优先考虑：

* Logistic Regression
* Poisson
* Elo
* XGBoost
* LightGBM
* CatBoost

后期再根据回测决定是否升级。

---

## P_llm

GPT-5.6 Sol 根据实时信息得到的动态判断概率。

GPT 的输入需要包括：

* 比赛信息
* 当前赔率
* 历史赔率
* P_market
* P_quant
* 球队数据
* 近期表现
* 伤停
* 首发
* 赛程
* 新闻摘要
* 数据质量

GPT 需要返回结构化结果，而不是自由文本。

---

## P_final

最终用于 EV 和串关优化的概率。

初期可以设计为：

$$
P_{final}
=
f(
P_{market},
P_{quant},
P_{llm},
confidence,
data\_quality
)
$$

但不要把权重永久写死。

例如不要永久写成：

```text
P_final =
0.3 * P_market +
0.4 * P_quant +
0.3 * P_llm
```

权重以后需要根据历史回测学习和校准。

---

# 五、GPT 动态分析接口

必须建立独立的：

```text
LLMStrategyProvider
```

核心业务不得直接依赖 GPT-5.6 Sol。

以后应该能够替换成其他模型而不修改其他模块。

建议接口输入：

```text
MatchContext
```

其中至少包含：

```text
match_id
competition
home_team
away_team
kickoff_time

sporttery_odds
market_odds
odds_history

league_table
recent_form
home_form
away_form

xg
xga

injuries
suspensions
expected_lineup
confirmed_lineup

rest_days
schedule_density

p_market
p_quant

data_quality
```

GPT 输出必须经过严格 Schema 校验。

例如：

```text
LLMMatchAssessment
```

包含：

```text
match_id

main_scenario
secondary_scenario
upset_scenario

home_win_probability
draw_probability
away_win_probability

confidence

risk_score

market_interpretation

lineup_impact
injury_impact
schedule_impact
motivation_assessment

favorite_overvaluation_risk

recommended_markets
avoid_markets

main_bet_candidates
hedge_candidates

correlation_tags

reasoning_summary
```

所有概率必须满足：

```text
0 <= probability <= 1
```

并验证：

```text
home + draw + away = 1
```

允许一定数值误差。

出现以下问题时必须自动降级：

* JSON 无效
* 字段缺失
* 概率异常
* 概率总和错误
* 模型调用失败
* 网络失败
* 返回内容明显不合理

GPT 失败不能导致整个系统无法运行。

---

# 六、剧本机制

本系统不能只保存一个预测结果。

每场比赛至少需要有：

```text
main_scenario
secondary_scenario
upset_scenario
```

例如：

```text
main_scenario:
主胜

secondary_scenario:
平局

upset_scenario:
客胜
```

以后可以进一步扩展：

```text
main_score:
2-1

secondary_score:
1-1

upset_score:
0-1
```

这样做的目的不是追求比分预测准确率。

主要是为了帮助串关系统设计：

* 主攻
* 次主攻
* 冷门
* 反向保护

---

# 七、EV 计算

基础 EV：

$$
EV=p\times O-1
$$

其中：

```text
p = P_final
O = 实际竞彩固定奖金
```

注意：

真正出票时使用的是：

```text
Sporttery Odds
```

而不是 Bet365、Pinnacle 等国外赔率。

国外赔率主要用于：

```text
P_market
市场变化
赔率共识
市场分歧
```

竞彩固定奖金主要用于：

```text
实际收益
实际EV
串关赔率
资金分配
```

这两类赔率在数据库中必须严格区分。

---

# 八、赔率分析

赔率必须保存完整时间序列，而不能只保存最新值。

至少记录：

```text
captured_at
source
bookmaker
market
selection
odds
```

需要支持计算：

* 初盘
* 当前盘
* 临场盘
* 最大变化
* 最近1小时变化
* 最近6小时变化
* 多公司平均变化
* 市场离散程度

例如：

$$
\Delta O
=
\frac{O_t-O_0}{O_0}
$$

后续需要识别：

* 持续降赔
* 持续升赔
* 突然降赔
* 临场反弹
* 市场共识
* 市场分歧
* 热度和赔率背离

但第一阶段不要过早写复杂规则。

先保证完整保存数据。

---

# 九、数据源设计

必须采用 Provider / Adapter 模式。

核心业务不能直接调用某个网站。

---

## 1. 中国竞彩网

主要数据源：

```text
https://www.sporttery.cn/
```

定位：

中国体育彩票竞彩足球官方数据源。

主要用于：

* 竞彩比赛编号
* 竞彩受注比赛
* 胜平负
* 让球胜平负
* 比分
* 总进球
* 半全场
* 混合过关
* 固定奖金
* 开奖赛果

需要设计：

```text
SportteryProvider
```

第一阶段先使用 Mock。

不要马上写复杂爬虫。

---

## 2. API-Football

候选主要足球综合数据源。

主要考虑：

* fixtures
* teams
* standings
* lineups
* injuries
* statistics
* players
* head-to-head
* odds

设计：

```text
APIFootballProvider
```

API Key 使用环境变量。

例如：

```text
API_FOOTBALL_KEY
```

不得写入 Git。

---

## 3. Sportmonks

作为后续高级数据源。

主要用于：

* fixtures
* lineups
* injuries
* sidelined
* statistics
* xG
* odds
* predictions

系统必须保证未来能够切换数据源。

---

## 4. The Odds API

主要用于国际市场赔率。

例如：

* bookmaker odds
* market consensus
* odds movement
* dispersion

国际赔率主要用于：

```text
P_market
```

不要替代竞彩固定奖金。

---

## 5. football-data.org

作为基础赛程、球队、积分榜和历史数据补充源。

---

# 十、多数据源比赛身份统一

这是整个系统非常重要的基础模块。

同一场比赛在不同网站可能具有不同 ID。

因此需要建立系统自己的：

```text
internal_match_id
```

所有核心计算只能使用：

```text
internal_match_id
```

而不能依赖任何第三方 ID。

建议建立：

```text
CompetitionIdentity

TeamIdentity

MatchIdentity

ProviderMapping
```

例如：

```text
internal_match_id

sporttery_match_id
api_football_fixture_id
sportmonks_fixture_id
odds_api_event_id
```

球队也需要解决：

* 中文名
* 英文名
* 简称
* 全称
* 历史名称
* 同名球队
* 女足
* 青年队
* B队
* 预备队
* 国家队

比赛匹配建议综合：

```text
competition
home_team
away_team
kickoff_time
```

并允许一定时间误差。

---

# 十一、时间规范

数据库内部统一使用 UTC。

例如：

```text
kickoff_time_utc
captured_at_utc
```

显示层再转换到本地时间。

不要把北京时间、日本时间、欧洲时间直接混在数据库中。

---

# 十二、串关优化不是简单选最高 EV

预测模块和投注组合模块必须严格分离。

预测模块输出：

```text
SelectionCandidate
```

例如：

```text
match_id
market
selection
odds
probability
ev
confidence
risk
```

组合模块输入多个 SelectionCandidate。

---

# 十三、支持的竞彩结构

第一阶段：

```text
2串1
```

以后加入：

```text
3串4
4串11
简单复式
```

需要准确建模这些过关方式真实包含的子组合。

例如 3串4 不应该当成普通 3场全部命中。

需要明确展开其实际子票。

---

# 十四、我的投注偏好

这是组合优化模块的重要约束。

本项目不是传统的：

“大量资金买低赔率强行保本”。

我更偏好：

**高赔率主攻 + 小额反向高赔率保护**

即非对称风险配置。

典型结构：

```text
主攻票1
中高赔率

主攻票2
中高赔率

主攻票3
中高赔率

保护票
小金额高赔率反向剧本
```

如果某场主攻：

```text
主胜
让胜
```

则保护票可以考虑：

```text
平
客队方向
受让
相邻比分剧本
```

并与其他高价值比赛组合。

目的不是：

所有赛果都保本。

而是：

避免某一个关键判断错误导致全部票同时死亡。

---

# 十五、出票操作要求

最终最多：

```text
4张票
```

优先：

```text
2串1
3串4
4串11
少量复式
```

尽量避免：

```text
大量拆票
十几张小票
复杂人工金额矩阵
```

系统优化不能只追求理论数学最优。

还需要考虑实际操作复杂度。

可以设计：

```text
operational_complexity_penalty
```

---

# 十六、组合风险

不能简单把概率相乘然后认为所有比赛完全独立。

需要预留：

```text
correlation_risk
```

例如以下组合可能存在类似风险：

* 多场热门主队
* 多场欧战后强队
* 多场主力轮换队伍
* 多场大热让胜
* 同一联赛相同市场环境

后期可以建立：

```text
risk_tags
```

例如：

```text
heavy_favorite
rotation_risk
european_fatigue
low_motivation
relegation_pressure
market_overheated
```

若一张票包含多个同类风险，需要增加风险惩罚。

---

# 十七、串关概率

基础情况下：

$$
P_{joint}
=
\prod_i P_i
$$

但是必须预留相关性调整。

例如：

$$
P_{joint}^{adj}
=
P_{joint}\times C
$$

其中 C 为相关风险修正。

第一阶段可以先假设独立。

但接口不能写死。

---

# 十八、资金优化

预算输入：

```text
100
200
任意整数预算
```

优化目标不能只是最大化：

```text
expected_return
```

而应该综合：

```text
EV
expected_return
risk
confidence
odds
portfolio concentration
hedge coverage
correlation
operational complexity
```

可以设计统一评分：

$$
J
=
w_1 EV
+
w_2 Return
+
w_3 Confidence
+
w_4 Hedge
-
w_5 Risk
-
w_6 Correlation
-
w_7 Concentration
-
w_8 Complexity
$$

第一阶段参数可以配置。

不要写死。

所有权重放入配置文件。

---

# 十九、主攻与反向保护

需要给每个 Ticket 一个角色：

```text
PRIMARY
SECONDARY
HEDGE
LONGSHOT
```

系统需要分析：

如果 PRIMARY 的核心场失败，

哪些 HEDGE 仍然能够存活。

因此需要建立：

```text
ScenarioExposure
```

例如：

```text
Match A

Scenario 1:
Home Win

Scenario 2:
Draw

Scenario 3:
Away Win
```

然后检查整个投注组合在不同比赛剧本下：

```text
portfolio_return
portfolio_loss
tickets_alive
```

后期可以通过 Monte Carlo 进行模拟。

---

# 二十、禁止伪保本

系统不能输出类似：

```text
70元 × 1.25赔率
```

只为了回收少量本金，

同时剩余资金买高赔率。

除非数学模型明确证明这种结构具有合理收益风险价值。

不要把“低赔率”自动等同于“安全”。

---

# 二十一、临场重新计算

系统未来需要支持多个时间节点。

例如：

```text
T-24h
T-12h
T-6h
T-3h
T-1h
T-30min
T-15min
```

当出现：

* 赔率重大变化
* 主力缺阵
* 首发变化
* 阵型变化
* 核心球员替补
* 天气异常
* 盘口异常

时重新运行：

```text
feature update
↓
P_quant update
↓
GPT update
↓
P_final update
↓
EV update
↓
portfolio optimize
```

系统需要保存：

```text
旧方案
新方案
变化原因
```

不能直接覆盖历史方案。

---

# 二十二、完整历史记录

每一次正式分析必须保存快照。

至少包括：

```text
analysis_id

analysis_time

match_id

odds_snapshot

features_snapshot

P_market
P_quant
P_llm
P_final

confidence
risk

GPT reasoning summary

recommended selection

ticket_id
ticket_role

stake

potential_return

result

actual_profit
```

以后能够重新还原：

“当时为什么下注”。

---

# 二十三、回测体系

这是项目长期最重要的部分之一。

不能只记录：

```text
中了多少场
```

需要分别评估：

## 单场概率质量

* Log Loss
* Brier Score
* Calibration Curve

## 投注价值

* ROI
* Yield
* EV
* Profit
* Drawdown

## 串关组合

* Ticket hit rate
* Portfolio ROI
* Max drawdown
* Profit distribution
* Risk-adjusted return

## GPT 价值

比较：

```text
P_quant
vs
P_final
```

回答：

* GPT 修正是否真正提高预测质量
* GPT 是否改善 ROI
* GPT 在哪些联赛有效
* GPT 在哪些联赛无效
* GPT 是否过度修正热门球队
* GPT 冷门判断是否存在系统性偏差
* GPT 对伤停的判断是否过强
* GPT 对市场变化解释是否有价值

最终形成：

```text
LLM Alpha Analysis
```

---

# 二十四、不要出现数据泄漏

回测必须严格按时间。

例如 T-24h 回测只能使用：

T-24h 当时已经存在的数据。

不能使用：

* 最终首发
* 临场赔率
* 赛后统计
* 后来公布的伤停

否则回测无效。

所有数据最好具有：

```text
available_at
```

字段。

---

# 二十五、数据库

第一阶段：

```text
SQLite
```

后续：

```text
PostgreSQL
```

建议 SQLAlchemy。

但不要为了未来迁移而过度设计。

初期重点确保 Schema 清晰。

建议核心表包括：

```text
competitions
teams
team_aliases

matches
provider_match_mapping

odds_snapshots

team_statistics

injuries
lineups

match_features

market_probabilities
quant_predictions
llm_predictions
final_predictions

bet_candidates

tickets
ticket_legs

analysis_runs

match_results

backtest_runs
```

最终结构由你根据实际职责设计。

---

# 二十六、配置系统

不要把任何策略参数写死。

例如：

```text
min_ev
max_tickets
budget
risk_weight
llm_weight
quant_weight
market_weight
max_exposure
```

都放到：

```text
config/
```

建议 YAML 或 TOML。

配置使用 Pydantic Settings。

---

# 二十七、API Key 和敏感信息

全部通过：

```text
.env
```

例如：

```text
OPENAI_API_KEY
API_FOOTBALL_KEY
SPORTMONKS_KEY
ODDS_API_KEY
```

提供：

```text
.env.example
```

但真实 Key 绝不能提交 Git。

---

# 二十八、技术栈

优先：

```text
Python 3.12+
```

建议：

```text
pydantic
sqlalchemy
httpx
pandas 或 polars
numpy
scipy
scikit-learn
pytest
```

优化器后期可以考虑：

```text
scipy.optimize
OR-Tools
PuLP
CVXPY
```

根据实际问题再选择。

不要第一阶段就引入大量依赖。

---

# 二十九、工程结构

不要写成一个：

```text
football_predictor.py
```

建议采用类似：

```text
football-betting-system/

README.md
pyproject.toml
.env.example
.gitignore

config/

docs/
    architecture.md
    data_model.md
    betting_model.md
    llm_strategy.md
    decisions/

src/
    football_system/

        domain/

        providers/

        ingestion/

        identity/

        database/

        features/

        probability/

        llm/

        strategy/

        betting/

        optimizer/

        backtest/

        reporting/

        cli/

        utils/

tests/

scripts/

data/
```

但不要机械采用。

请根据职责重新评估后确定最终结构。

---

# 三十、领域模型优先

核心业务对象尽量采用明确的数据模型。

例如：

```text
Match
Team
Competition

OddsSnapshot

MatchFeatures

ProbabilityPrediction

LLMAssessment

BetSelection

Ticket

Portfolio

AnalysisRun
```

不要到处传递没有类型约束的 dict。

---

# 三十一、Provider 接口

不同数据源应该实现统一协议。

例如：

```python
class FixtureProvider(Protocol):
    ...

class OddsProvider(Protocol):
    ...

class TeamDataProvider(Protocol):
    ...

class InjuryProvider(Protocol):
    ...
```

这样以后能够：

```text
Mock
API-Football
Sportmonks
Sporttery
```

自由替换。

---

# 三十二、系统第一阶段不要直接抓网页

第一阶段必须先实现：

```text
MockFixtureProvider
MockSportteryProvider
MockOddsProvider
```

使用人工构造的 5～10 场比赛。

先验证整个业务流程。

必须做到：

```text
Mock数据
↓
数据库
↓
P_market
↓
手动P_quant
↓
EV
↓
2串1
↓
资金配置
↓
最终票
```

全流程可运行。

---

# 三十三、第一阶段 MVP

第一阶段只完成以下功能。

## 数据

* Competition
* Team
* Match
* OddsSnapshot

## Provider

* MockFixtureProvider
* MockSportteryProvider

## 数据库

SQLite。

## Probability

实现：

```text
P_market
```

以及手动输入：

```text
P_quant
```

暂时不训练 ML。

## Betting

支持：

```text
胜平负
2串1
```

## EV

实现：

$$
EV=p\times odds-1
$$

## Portfolio

支持：

```text
100元
200元
最多4张票
```

## 输出

CLI 打印：

```text
比赛
方向
赔率
概率
EV

最终票
金额
组合赔率
估计概率
预期收益
```

## Persistence

所有分析保存数据库。

## Tests

核心模块必须具有 pytest。

---

# 三十四、第二阶段

接入真实数据。

优先顺序：

```text
竞彩
+
API-Football
```

然后实现：

* 比赛自动获取
* 球队匹配
* 联赛匹配
* 竞彩赔率
* 赔率历史
* standings
* recent form
* injuries
* lineups

同时建立：

```text
Identity Resolver
```

---

# 三十五、第三阶段

实现 P_quant。

从简单模型开始：

```text
Elo
Poisson
Logistic Regression
```

然后比较。

再根据历史数据尝试：

```text
LightGBM
CatBoost
XGBoost
```

重点不是模型复杂度。

重点是：

* calibration
* out-of-sample
* time-based validation
* ROI

---

# 三十六、第四阶段

正式接入 GPT-5.6 Sol。

实现：

```text
LLMStrategyProvider
```

必须支持结构化 JSON。

实现：

```text
P_llm
main_scenario
secondary_scenario
upset_scenario
confidence
risk
market_interpretation
hedge_candidates
```

然后建立 P_final。

---

# 三十七、第五阶段

升级组合优化。

加入：

```text
2串1
3串4
4串11
复式
```

加入：

```text
PRIMARY
SECONDARY
HEDGE
LONGSHOT
```

加入：

* 风险相关性
* 剧本覆盖
* 单场风险暴露
* 最大回撤约束
* 操作复杂度约束

---

# 三十八、第六阶段

实现自动临场更新。

包括：

* 定时抓赔率
* 赔率变化检测
* 正式首发
* 伤停更新
* 临场 P_llm
* 重新计算 EV
* 自动生成新方案
* 对比旧方案

---

# 三十九、第七阶段

建立完整回测与模型评价体系。

输出：

```text
Dashboard
```

以后可显示：

* 今日比赛
* 今日推荐
* EV
* 概率
* 赔率曲线
* GPT 修正
* Portfolio
* 历史 ROI
* Drawdown
* Calibration
* League Performance
* LLM Alpha

前端不是当前重点。

---

# 四十、代码质量要求

代码必须：

* 可读
* 模块化
* 有类型标注
* 可测试
* 可回测
* 可扩展
* 可解释

避免：

* 巨型类
* 巨型函数
* 全局状态
* 隐式副作用
* 大量魔法数字
* 无意义 abstraction
* 过度设计

---

# 四十一、测试要求

至少测试：

```text
odds → implied probability

去水

EV

串关赔率

串关概率

2串1枚举

资金总额约束

最多4张票

概率合法性

LLM Schema

数据源标准化

Match Identity
```

以后加入 regression tests。

---

# 四十二、日志

每一次分析要有：

```text
analysis_run_id
```

所有关键日志能够关联到一次运行。

例如：

```text
data loaded
odds updated
P_market computed
P_quant computed
LLM called
P_final generated
tickets generated
portfolio optimized
```

---

# 四十三、失败降级

系统不能因为：

* 某个 API 挂了
* GPT 挂了
* 伤停数据缺失
* xG 缺失

就完全无法分析。

需要建立：

```text
data_quality
```

以及：

```text
fallback
```

例如：

GPT 不可用：

```text
P_final = fuse(P_market, P_quant)
```

伤停不可用：

降低 confidence。

赔率历史不足：

不执行 movement signal。

---

# 四十四、README 和文档

每完成一个阶段需要同步维护：

```text
README.md
docs/architecture.md
docs/data_model.md
docs/betting_model.md
docs/llm_strategy.md
```

重要架构决策记录为 ADR。

例如：

```text
docs/decisions/0001-provider-abstraction.md
```

这样以后不同 AI coding agent 进入项目时能够快速理解系统。

---

# 四十五、Git 提交原则

不要一次提交大量完全不同的改动。

建议：

```text
feat(domain): add core match models

feat(provider): add mock fixtures

feat(probability): calculate market probability

feat(betting): add 2-leg parlay

test(betting): add EV tests
```

每次保证测试通过。

---

# 四十六、不要让 AI 随意改策略

未来 GPT-5.6 Sol 作为动态分析器时：

GPT 可以改变：

```text
P_llm
confidence
scenario
risk assessment
```

GPT 不能自行修改：

```text
EV公式
数据库
资金约束
串关规则
核心源码
历史数据
```

如果认为策略需要修改，GPT 应产生：

```text
StrategyProposal
```

由人工或单独实验确认以后再修改配置。

---

# 四十七、系统最终希望回答的问题

系统最后不只是告诉我：

“今天买什么”。

还应该能够回答：

```text
为什么买？

市场怎么看？

量化模型怎么看？

GPT怎么看？

哪一步产生分歧？

赔率有没有价值？

最危险的是哪场？

如果这场爆冷，哪张票可以存活？

为什么投入40而不是20？

为什么选择2串1而不是3串4？

GPT修正长期有没有价值？

哪些联赛最适合这个系统？

哪些类型比赛应该直接放弃？
```

---

# 四十八、一个重要原则

系统必须允许：

```text
NO BET
```

如果所有比赛都没有足够价值，

输出：

```text
今日无值得下注组合
```

是完全正确的结果。

不允许为了每天必须生成 4 张票而强行下注。

---

# 四十九、第一阶段你的工作方式

现在不要直接试图实现整个系统。

请严格按照以下流程开始。

## Step 1

重新阅读所有需求。

总结：

* 项目目标
* 核心业务
* 系统边界
* 哪些属于固定程序
* 哪些属于 GPT 动态策略

## Step 2

指出当前需求中可能存在：

* 设计冲突
* 技术风险
* 数据风险
* 回测风险
* 数据泄漏风险
* 赔率建模风险

## Step 3

给出总体架构。

解释每个模块职责。

## Step 4

设计核心 Domain Model。

重点设计：

```text
Match
OddsSnapshot
Prediction
LLMAssessment
BetSelection
Ticket
Portfolio
AnalysisRun
```

## Step 5

设计数据库 Schema。

## Step 6

设计 Provider 接口。

## Step 7

设计：

```text
P_market
P_quant
P_llm
P_final
```

的数据流。

## Step 8

重点设计：

```text
LLMStrategyProvider
```

确保未来更换模型不影响系统。

## Step 9

设计：

```text
Betting Engine
Portfolio Optimizer
```

的输入输出。

## Step 10

提出第一阶段 MVP 目录结构。

## Step 11

列出第一阶段需要创建的文件。

## Step 12

建立工程骨架。

## Step 13

运行测试。

## Step 14

确认 MVP 通过以后，再进入真实数据源阶段。

---

# 五十、当前阶段禁止事项

现在不要：

* 开发 Web 前端
* 写几十个网站爬虫
* 接所有 API
* 训练神经网络
* 做复杂 Agent 系统
* 做自动下注
* 上微服务
* Docker/Kubernetes 过度设计
* 建消息队列
* 上 Redis
* 使用复杂分布式架构

当前目标是：

```text
先建立一个可靠、干净、可扩展的核心。
```

---

# 五十一、最终开发原则

整个项目始终遵循：

```text
先数据
再概率

先概率
再价值

先价值
再组合

先组合
再资金

先记录
再回测

先回测
再优化
```

任何复杂功能都必须建立在可验证基础上。

不要因为 GPT 很强，就让 GPT 代替所有固定计算。

也不要因为量化程序稳定，就把所有动态足球信息写死成规则。

本项目核心思想始终是：

**固定程序负责计算，GPT-5.6 Sol 负责理解，历史数据负责验证。**

现在请从：

**“项目需求审查 + 总体架构设计”**

开始。

暂时不要一次性生成整个项目全部代码。

首先向我提交：

1. 对项目的理解
2. 需求冲突和风险
3. 推荐总体架构
4. 核心 Domain Model
5. 数据库初步设计
6. Provider 设计
7. LLMStrategyProvider 设计
8. MVP 范围
9. 推荐目录结构
10. 第一阶段开发顺序

完成以上内容后，再开始实际创建工程。
