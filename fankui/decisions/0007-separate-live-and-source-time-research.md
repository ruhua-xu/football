# ADR-0007：LIVE_STRICT 与来源时间研究永久隔离

## 状态

Accepted

## 背景

历史回放同时涉及三类不同时间：来源事件及公开时间、本系统实际取得或注册数据的时间、回测任务实际执行时间。后来下载的历史文件可能可靠说明“来源在 2024 年已经公开”，却不能证明“本系统在 2024 年已经采集并保存”。

如果把来源时间写进 `ingested_at_utc`、`imported_at_utc` 或执行时间，研究数据会伪装成实时留存数据。这样既破坏 provenance，也会让未来数据泄漏无法审计。若把两种数据直接聚合或比较，即使策略参数相同，结果也不具备公平性。

## 决策

系统面向真实归档和生产运行只保留两个互斥的数据模式：

```text
LIVE_STRICT
SOURCE_TIME_RESEARCH
```

一次 archive validate/import、Provider store、BacktestRun、指标快照或策略比较只能使用一种模式。`HistoricalArchiveService` 拒绝混合模式目录；低层显式 selector 即使从目录中选择一种模式，也不能把未选模式带入同一运行。持久化必须保留 `data_mode`，比较必须要求两侧模式完全相同。

`LIVE_STRICT` 对所有真实归档只用于系统当时真实采集的数据。快照或赛果必须同时经过 captured/observed、available 和真实 ingested cutoff；record 使用 `retrospective=false` 且不携带回溯 import 时间。

`SOURCE_TIME_RESEARCH` 只用于后来取得且具有可信 source observed/available 时间的归档。record 必须使用 `retrospective=true`，实际取得时间单独保存在 `imported_at_utc`；快照和赛果以 `ingested_at_utc=available_at_utc` 明确 source-time boundary。所有报告必须精确显示：

```text
RETROSPECTIVE_SOURCE_TIME_RESEARCH
```

该标签不得省略、改写或与 `LIVE_STRICT` 指标静默合并。无法证明 source historical availability 时，数据不满足严格 point-in-time，系统必须拒绝将其伪装为任一严格模式。

固定的 `data/fixtures/historical_acceptance/` 是非生产、纯合成的测试工件，不是历史来源归档。原始验收要求将 `config/backtest.toml` 固定为 `data_mode=LIVE_STRICT`；在这个且仅这个明确分类的 synthetic corpus 中，`LIVE_STRICT` 命名被测试的 captured/observed、available、ingested 与 cutoff 时间校验规则，不构成“系统在相应历史时点实际采集或持有数据”的事实声明。这不是第三种 `HistoricalDataMode`，也不放宽任何真实归档的 `LIVE_STRICT` 含义。

该工件及其报告必须显式携带以下合同字段：

```text
classification = SYNTHETIC_ACCEPTANCE_DATA
performance_warning = NOT REAL HISTORICAL PERFORMANCE
```

报告必须将其分别渲染为以下精确 banner：

```text
SYNTHETIC ACCEPTANCE DATA
NOT REAL HISTORICAL PERFORMANCE
```

这两条 banner 的解释优先级高于 data mode 标签、合成时间戳、指标、`source_description`、`license_note` 或其他任何 performance/provenance 暗示。它们明确否定真实历史采集、真实历史表现和收益声明。Synthetic 与 non-synthetic 归档即使使用相同 data mode，也不得被视为相同 provenance、合并指标或作为公平比较的两侧；比较必须 fail closed。

真实时间不得回填：

1. Archive `created_at_utc` 是归档实际创建时间。
2. 研究 record 的 `imported_at_utc` 是本地实际取得时间。
3. 数据库 `historical_archive_imports.imported_at_utc` 是实际注册时间，必须不早于 manifest 创建时间。
4. `BacktestRun.created_at_utc`、AnalysisRun 的 started/completed 时间、BacktestSlice 的创建时间和 metrics 的 calculated 时间属于实际执行，必须不早于相应知识边界；生产 CLI 使用 `utc_now()`。
5. `decision_as_of_at_utc` 和 `evaluation_as_of_at_utc` 只是历史知识 cutoff，不能冒充 import、created、started、completed 或 calculated timestamp。
6. walk-forward Settlement 的 `settled_at_utc` 表示模拟结算有效的 evaluation as-of，不表示任务在该历史时刻真实执行。

## 理由

对真实归档，模式隔离使每个指标都能回答同一个问题：它来自当时真实留存，还是来自后来取得并按可信来源时间重放的数据。Synthetic acceptance 必须先按独立 classification 和 warning 解释，不能仅凭 mode 推断历史持有事实。保留真实 import/execution 时间可证明系统何时拥有文件、何时运行代码，同时仍用独立 cutoff 阻止未来输入进入历史决策。

不回填时间还保证 append-only 记录具有可解释顺序。来源更正、重新导入和回测重放只能追加或幂等复用，不能通过伪造较早时间获得优先级。比较层因此可以直接拒绝不同 mode、provenance、cutoff 或结果 lineage，而不是在报告后补充含糊免责声明。

## 结果

- 实时业绩与回溯研究结果永不混合。
- 研究报告始终带 `RETROSPECTIVE_SOURCE_TIME_RESEARCH` 标签。
- 历史 cutoff 与真实系统时间可以同时审计，不再共享一个含糊 timestamp。
- 后来取得的数据仍可做 source-time 研究，但不能声称系统当时已经保存。
- 合成验收只证明 temporal validator 和回测链路按合同工作，不证明历史采集或策略表现。
- Synthetic 与 non-synthetic provenance 永不因相同 `data_mode` 而变得等同或可比。
- 不可靠的 availability、错误 mapping 或无法表达的结算状态按 fail-closed 处理，不能靠回填时间或构造结果绕过。
- 存储和报告增加少量 provenance 字段，但换取可回放性、比较公平性和明确的数据泄漏边界。

## 被否决方案

将所有历史数据都标为 `LIVE_STRICT` 被否决，因为它虚构本系统历史持有事实。为 synthetic fixture 增加第三种 data mode 也被否决，因为验收分类是非生产测试属性，原始合同要求生产枚举恰好保持 `LIVE_STRICT` 与 `SOURCE_TIME_RESEARCH`。只保留一个宽松 research mode 被否决，因为它无法证明真正的实时采集。把真实 import/execution 时间改成 decision/evaluation cutoff 被否决，因为它把模拟有效时间伪装成系统事件时间，并破坏审计和更正顺序。
