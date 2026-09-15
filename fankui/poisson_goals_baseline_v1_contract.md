# POISSON_GOALS_BASELINE_V1

## 范围与事实来源

Model/version：POISSON_GOALS_BASELINE_V1 / 1；calibration_label=BASELINE_UNCALIBRATED。
它只服务新市场，THREE_WAY继续使用原Elo和原config。固定配置见
[`poisson_goals_v1.json`](../config/poisson_goals_v1.json)。这不是ROI、alpha或校准质量证明。

本候选的显式离线cohort admission只接受SYNTHETIC_ACCEPTANCE_DATA声明、已存在的
normalized MatchResult IDs及canonical match/season。Repository复验比分payload hash、
主客身份、competition/season、admission时间和supersession；同cohort完整事实参与
league averages，禁止从球队全场次平均值替代home/away split。
所有事实及cohort admission必须严格早于training cutoff；target不能在训练facts中。
可见更正不能回退到旧事实。历史state按自身cutoff重演，不按当前时间重写旧结果。

## 固定公式

令league_home_avg/league_away_avg为同一准入cohort全部常规时间主/客进球均值：

```text
home_attack  = home_team_home_GF_avg / league_home_avg
home_defense = home_team_home_GA_avg / league_away_avg
away_attack  = away_team_away_GF_avg / league_away_avg
away_defense = away_team_away_GA_avg / league_home_avg
lambda_home  = league_home_avg * home_attack * away_defense
lambda_away  = league_away_avg * away_attack * home_defense
```

home主场样本>=5、away客场样本>=5、league样本>=10。缺任何项分别为
INSUFFICIENT_HOME_HISTORY、INSUFFICIENT_AWAY_HISTORY、INSUFFICIENT_LEAGUE_HISTORY；
league均值0时ZERO_LEAGUE_GOAL_AVERAGE。禁止league-average fallback。
lambda量化到1e-18，允许0的退化Poisson；lambda>20为INTENSITY_OUTSIDE_FIXED_BOUND，
不clamp、不自动调整。Cohort最多1024事实；固定参数、公式、数值边界全部进入config hash。

State封存cohort/admission、training fact IDs/hash、training_data_hash、config/hash、
competition/season、target、training cutoff、generated_at、home/away/league counts及
lambda/score grid。所有计算使用显式Decimal precision80；没有binary float路径。

## 无限支持与尾部合同

PMF递推 `p(0)=exp(-lambda)`、`p(k)=p(k-1)*lambda/k`。分别计算home/away的adaptive
cutoff，至少覆盖0..6，直到各自tail<=1e-18；hard score bound256。
ScoreGrid封存两个marginal数组、各tail及矩形外tail；joint有限单元可由两marginal乘积
确定性重建。tail不会当作0，也不丢弃后重新normalize。

TOTAL_GOALS直接使用独立Poisson相加性质：T~Poisson(lambda_home+lambda_away)，
0..6按PMF，7+精确取 `1 - sum(P(T=0..6))`，吸收全部无限tail。

HANDICAP和比分OTHER用full-away-CDF积分及区间认证：对每个H=0..n的有限row，
HOME/DRAW/AWAY分别使用F_A(H+handicap-1)、P_A(H+handicap)、1-F_A(H+handicap)。
这些row已经包含所有away tail。剩余H>n的质量为T_H；其中偏离HOME的质量不超过：

```text
u = T_H * P(A > n + home_handicap)
HOME lies in [finite_home + T_H - u, finite_home + T_H]
DRAW lies in [finite_draw, finite_draw + u]
AWAY lies in [finite_away, finite_away + u]
```

显式比分直接用PMF乘积。各OTHER为对应胜平负的无限质量减去本catalog显式比分，
因此包含未列出的有限比分和正确方向的tail。区间额外外扩固定1e-60数值误差预算。
在lambda<=20、precision80、score<=256的有限递推/累加边界下，该预算保守覆盖Decimal
舍入误差。只有每个outcome区间两端ROUND_HALF_EVEN到1e-12得到同一值，才输出概率。
否则增加n直到认证或hard reject。因而输出是**无限支持概率的可认证舍入值**，不是
把未知tail任意塞进某个OTHER。最后仅按最大项/canonical tie顺序闭合量化残差。

## 验证与保留限制

固定lambda对至少0.6/0.5、1.2/1.0、1.8/1.3、2.5/0.8、3.0/2.5，另覆盖0/0与20/20。
验证hash/replay、无负数、三个新市场各自sum=1、7+完整tail及OTHER质量。

只有independent Poisson；无Dixon-Coles、bivariate、Bayesian/ML、赔率反推lambda、
参数搜索或自动调参。新市场handicap映射固定|home_handicap|<=20；无法认证数值的输入
明确失败。真实训练权利/数据接入与模型表现另需验证，已删除的0.6材料不作依赖。
