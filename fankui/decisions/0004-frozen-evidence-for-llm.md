# ADR-0004：LLM 只分析冻结 Evidence

## 状态

Accepted

## 背景

如果 LLM 在每次推理时自行浏览互联网，系统无法证明它当时看到了什么，也无法回放历史分析。回测还可能无意使用后来发布的伤停、首发或新闻。

## 决策

生产分析采用：

```text
External Source
-> Evidence Provider
-> Append-only Evidence
-> Evidence Snapshot
-> MatchContext
-> LLM
```

只有 `available_at_utc <= analysis_run.as_of_at_utc` 的 Evidence 可以进入 Snapshot。LLM 不能在分析过程中获得浏览工具。

LLM 可以输出语义级 `preferred_outcomes`、`avoid_outcomes`、`counter_scenarios` 和 `scenario_relationships`，但不得输出 EV、金额、P_final、串关结构、最终 Ticket 或 NO_BET。

## 结果

- 每次 P_llm 都能追溯到确定的 Evidence 集合。
- 历史决策回放不重新访问互联网或调用模型。
- 动态信息采集成为独立 Provider 能力。
- Evidence 存储增加数据量，但换取可审计性和 point-in-time 安全。
