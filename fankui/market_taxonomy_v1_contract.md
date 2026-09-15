# Market Taxonomy V1 / MarketKey V2

## 状态与范围

0.8软件候选，待整体架构验收。实现于 `domain/market_v2.py`。旧 `market.py`、
SelectionKey、ThreeWayProbability及其wire不变。新增enum只包含THREE_WAY、
HANDICAP_THREE_WAY、TOTAL_GOALS、CORRECT_SCORE；HALF_FULL不进入本版。

## 数学与数据合同

MarketKeyV2含schema_version、market_type、home_handicap。只有HANDICAP_THREE_WAY
必须有显式integer home_handicap，其他市场该字段必须null。Canonical形如
`HANDICAP_THREE_WAY:HOME-1`、`HANDICAP_THREE_WAY:HOME+1`；不在adapter暗中翻转符号。
按 `home_goals + home_handicap` 与away_goals比较决定让球胜平负。

固定ordered OutcomeKeyV1与中文展示标签分离。Catalog严格如下：

- THREE_WAY / HANDICAP_THREE_WAY：HOME_WIN、DRAW、AWAY_WIN。
- TOTAL_GOALS：GOALS_0、GOALS_1、GOALS_2、GOALS_3、GOALS_4、GOALS_5、GOALS_6、GOALS_7_PLUS。
- CORRECT_SCORE（31）：
  - SCORE_1_0、SCORE_2_0、SCORE_2_1、SCORE_3_0、SCORE_3_1、SCORE_3_2、
    SCORE_4_0、SCORE_4_1、SCORE_4_2、SCORE_5_0、SCORE_5_1、SCORE_5_2、HOME_OTHER；
  - SCORE_0_0、SCORE_1_1、SCORE_2_2、SCORE_3_3、DRAW_OTHER；
  - SCORE_0_1、SCORE_0_2、SCORE_1_2、SCORE_0_3、SCORE_1_3、SCORE_2_3、
    SCORE_0_4、SCORE_1_4、SCORE_2_4、SCORE_0_5、SCORE_1_5、SCORE_2_5、AWAY_OTHER。

MarketProbabilityDistributionV1保存market_key与完整ordered outcome/probability。
每项Decimal、0..1、最多28位小数，总和沿用原PROBABILITY_TOLERANCE=0.000001；
程序生成的分布量化到1e-12并精确闭合为1。缺项、多项、重复、错序或跨市场均拒绝。
浮点数在新合同入口递归拒绝。显式ThreeWayProbability adapter逐值保留原Decimal
编码，只有THREE_WAY可以调用该adapter；新市场不能塞入旧三向类型。

## Odds / consensus 实现

MarketOddsSnapshotV2及SportteryFixedBonusSnapshotV2绑定match、market、完整ordered
outcome价格、provider/source、source artifact ID/hash、capture/available/ingested、
snapshot ID/hash。SP/odds必须>1，最多6位小数；Decimal字符串中的尾随零保留。
Snapshot及子quote append-only；时间依次单调且不得跨decision cutoff。

Fixture adapter只显式映射：FT_1X2、FT_HOME_HANDICAP_1X2、FT_EXACT_GOALS_0_7_PLUS、
FT_CORRECT_SCORE_31。provider label（如H、3:1、H_OTHER）在infrastructure映射，
不成为domain enum。O/U 2.5、两向Asian spread、不完整比分book不能替代上述市场。
语义不匹配或缺任何outcome返回MARKET_UNAVAILABLE；原fixture source保留，
该book不会形成可用snapshot，也不作为缺项为0/1的consensus输入。

Consensus V2对每家完整同语义book先独立inverse-odds devig，再逐outcome取median，
最后normalize和固定量化闭合。bookmaker唯一、snapshot排序确定；全部拒绝时概率为null。
测试覆盖3/8/31 outcomes。THREE_WAY旧pipeline的P_market从旧来源只读适配，
不被新的consensus实现重新计算或替换。

## 结算事实与限制

唯一事实为normalized regular-time MatchResult。总进球>=7一律GOALS_7_PLUS。
3-1/4-1/5-1分别命中显式SCORE_3_1/4_1/5_1；6-1为HOME_OTHER、4-4为DRAW_OTHER、
1-6为AWAY_OTHER。OTHER使用自己的SP，不做nearest-score匹配。

本轮仅验证synthetic exact-market fixture，不声明真实provider支持这些catalog。
真实来源接入须有独立授权与exact-market mapping；本候选没有新增HTTP调用。
