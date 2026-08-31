# ADR-0001：使用 MarketKey 抽象竞彩市场

## 状态

Accepted

## 背景

MVP 只计算胜平负1X2，但终态必须支持让球胜平负、比分、总进球和半全场。如果领域对象和数据库固定为 home/draw/away 三列，后续市场会要求重构核心接口和历史表。

## 决策

使用以下稳定标识：

```text
MarketKey(market_type, handicap_value)
SelectionKey
```

预留 MarketType：

```text
THREE_WAY
HANDICAP_THREE_WAY
CORRECT_SCORE
TOTAL_GOALS
HALF_FULL
```

MVP 只启用 `THREE_WAY`。赔率和竞彩奖金持久化采用 Snapshot Header + Outcome Quote 明细。`ThreeWayProbability`、`ThreeWayMarketOdds` 和 `ThreeWayFixedBonus` 继续作为1X2强类型对象。

## 结果

- 普通胜平负和让球胜平负通过不同 MarketKey 隔离。
- 后续市场可以增加 SelectionKey，不需要修改 Snapshot 主表。
- MVP 增加少量明细表和校验代码。
- 未实现的 Market 必须返回 `UNSUPPORTED_MARKET`，不得回退到1X2算法。
