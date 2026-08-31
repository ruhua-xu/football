# 足球竞彩分析系统 MVP 实现报告

## 1. 交付结论

本轮已完成需求文件 `yaoqiu/yaoqiu08311111.md` 约定的 MVP，并纳入最新 Strategy Profile：

- 默认优先最多4张 Ticket。
- 绝对上限8张 Ticket。
- 超出 preferred 的 Ticket 必须满足更严格的 ROI、操作复杂度惩罚和新增比赛暴露条件。
- 数据库不再把4张写成永久硬约束，只保证 `ticket_no > 0`；运行时由冻结的 Strategy Profile 校验绝对上限。

MVP 已覆盖 Mock 数据输入、概率、Fusion、Selection EV、简单2串1、竞彩计奖、Portfolio、`NO_BET`、SQLite 持久化、输入血缘、封存不可变、CLI 和自动化测试。真实 API、真实 LLM、复杂过关和自动下注均未扩展。

最终 fresh 验收数据库：`data/mvp_release_acceptance.db`。

## 2. 已实现模块与关键文件

| 模块 | 实现内容 | 关键文件 |
|---|---|---|
| 配置 | 数据库、Fusion、EV、竞彩规则、Strategy Profile；Pydantic 启动校验 | `config/mvp.toml`、`src/football_system/config.py` |
| Domain 基础 | UTC 时间、稳定 ID、Decimal、不可变 Pydantic Domain Model | `src/football_system/domain/common.py` |
| Match 与来源快照 | Competition、Team、Match、ProviderMapping、国际赔率快照、竞彩固定奖金快照 | `src/football_system/domain/match.py` |
| Market | `MarketType`、`MarketKey`、三项概率、国际赔率、竞彩固定奖金、未支持市场错误 | `src/football_system/domain/market.py` |
| Prediction | `P_market`、`P_quant`、`P_final`、手工概率输入、FusionPolicy | `src/football_system/domain/prediction.py` |
| AnalysisRun | 知识截止时点、运行时间线、配置/hash、Manifest V2、MatchContext | `src/football_system/domain/analysis.py` |
| 概率服务 | 国际赔率归一化逆概率去水、12位概率量化、EV | `src/football_system/domain/services/probability.py` |
| Fusion | `QUANT_ONLY_V1`、`MARKET_QUANT_BLEND_V1`、输入一致性和 fallback 边界 | `src/football_system/domain/services/fusion.py` |
| 投注候选 | 三项 Selection EV、拒绝原因、简单2串1候选和收益指标 | `src/football_system/domain/services/betting.py` |
| 竞彩计奖 | 2元基础单位、正整数倍数、单票上限、官方 half-even 舍入 | `src/football_system/domain/services/payout.py` |
| Portfolio | `RECOMMENDED`/`NO_BET`、preferred/absolute Ticket 策略、预算分配、允许留存现金 | `src/football_system/domain/services/optimizer.py` |
| 应用编排 | Provider 并发读取、时点检查、payload hash 复算、完整 Manifest、全流程持久化 | `src/football_system/application/run_analysis.py` |
| Ports | Fixture、赔率、竞彩、手工概率 Provider 接口和 Repository 接口 | `src/football_system/application/ports/` |
| Mock Adapters | 6场 Fixture、国际赔率、竞彩固定奖金、手工 `P_quant` | `src/football_system/infrastructure/providers/mock/`、`data/fixtures/mvp_matches.json` |
| 数据库模型 | SQLAlchemy 表、FK、UNIQUE、CHECK、金额和概率精度 | `src/football_system/infrastructure/database/models.py` |
| Repository | 原子保存、来源冲突检测、血缘复核、hash 复核、Manifest 读取 | `src/football_system/infrastructure/database/repositories.py` |
| 不可变控制 | 来源 append-only、完整 AnalysisRun 后代封存、source aggregate sealed insert | `src/football_system/infrastructure/database/immutability.py` |
| Migration | 最终 Schema 基线和冻结的71个 SQLite 触发器 | `migrations/versions/1bec5f575834_create_mvp_schema.py`、`migrations/versions/e3754eb9a102_seal_all_analysis_artifacts.py` |
| CLI | 参数解析、UTF-8 输出、正常/NO_BET 演示、非有限 Decimal 拒绝、非源码目录资源定位 | `src/football_system/interfaces/cli.py` |
| Packaging | Python 3.12+、console script、wheel 内置配置/fixture/migration | `pyproject.toml` |
| 测试 | unit、contract、integration、e2e | `tests/` |

## 3. 关键实现约束

### 3.1 时点与输入血缘

- 应用层再次验证所有输入时间不晚于 `AnalysisRun.as_of_at_utc`，不只依赖 Provider 过滤。
- 强制 `as_of_at_utc <= started_at_utc <= completed_at_utc`。
- 赔率、竞彩奖金和手工概率均使用包含时间及 payload hash 的版本化 ID。
- 相同 ID 但内容不同会由 Repository 明确拒绝，不会静默复用旧事实。
- `AnalysisMatchContext` 结构化绑定赔率 Snapshot、竞彩 Snapshot 和 ManualQuantInput。
- `input_manifest_json` 包含规范化并稳定排序的 Competition、Team、Match、Mapping、赔率、奖金和手工概率事实。
- AnalysisRun 保存 `MVP_INPUT_MANIFEST_V2`、Manifest JSON/SHA-256、配置 JSON/SHA-256 和 `package:<sha256>` 源码 revision。
- Repository 提供 `load_input_manifest(analysis_run_id)` 并在读取时复核 hash。

### 3.2 Decimal 与计奖

- 金额全程使用整数分。
- 来源赔率/奖金限制为 `NUMERIC(18,6)` 可无损保存的范围。
- 来源概率和 Mapping confidence 限制为 `NUMERIC(18,12)` 可无损保存的范围。
- 算法概率在进入封存模型前量化到12位并修正残差，保证三项和为1。
- EV、ROI 和期望金额在进入封存模型前量化到8位。
- 竞彩命中毛返还使用官方 half-even 分级舍入策略。

### 3.3 Strategy Profile

当前配置：

```toml
[portfolio]
preferred_max_tickets = 4
absolute_max_tickets = 8
extra_ticket_min_roi = "0.20"
operational_complexity_penalty = "0.01"
```

Optimizer 先在 preferred 范围内选择候选。额外 Ticket 只有在以下条件同时满足时才可加入：

- 未达到 `absolute_max_tickets`。
- 调整后 ROI 不低于 `extra_ticket_min_roi`。
- 带来尚未覆盖的新比赛暴露。
- 预算仍能分配至少一个合法2元单位。

## 4. 测试与验证

### 4.1 pytest

最终命令：

```powershell
.venv\Scripts\python -m pytest
```

结果：`43 passed`。

| 测试层级 | 数量 | 覆盖内容 |
|---|---:|---|
| Unit | 24 | Market、去水、概率量化、Fusion、EV、2串1、计奖、Optimizer、配置 |
| Contract | 5 | 四类 Mock Provider、时点过滤、版本化 Snapshot/Input ID |
| Integration | 3 | Schema/FK、AnalysisRun 不可变、fresh Alembic upgrade |
| E2E CLI | 4 | 正常 CLI、NO_BET、非源码 cwd、NaN/Infinity 拒绝 |
| E2E Pipeline | 7 | 完整流程、双运行复用来源、blend、时点泄漏拒绝、Manifest、payload/精度、完整封存链路 |

已验证的风险场景包括：

- 封存后修改/删除概率 outcome、input relation、Ticket、TicketLeg、目录 Match 均失败。
- 封存后向已引用 source aggregate 追加合法 Selection 也由触发器拒绝。
- Provider 返回截止时点之后的数据会被 Use Case 拒绝。
- Provider 声明的 payload hash 与实际赔率不一致会被拒绝。
- 超过数据库 precision/scale 的赔率和 Mapping confidence 会被拒绝。
- 未支持 Market、负 EV/ROI 阈值、跨 AnalysisRun 引用会被拒绝。
- MatchContext 与实际预测/候选使用不同版本 Snapshot 会被拒绝。
- 不同手工概率会产生不同 ManualQuantInput ID 和 Manifest hash；仅调整输入顺序不会改变 Manifest hash。
- Domain 返回的概率/EV/ROI 与数据库封存值精确一致。

### 4.2 其他验证

| 检查 | 结果 |
|---|---|
| `python -m compileall -q src tests migrations` | 通过 |
| `git diff --check` | 通过 |
| `alembic upgrade head` | 通过 |
| `alembic check` | `No new upgrade operations detected` |
| `PRAGMA foreign_key_check` | 0条异常 |
| SQLite trigger 数量 | 71 |
| wheel build | `football_system-0.1.0-py3-none-any.whl` 构建成功 |
| 独立 wheel 安装 | 全新虚拟环境安装成功 |
| 非源码路径 wheel CLI | migration、Mock 资源和 NO_BET 冒烟通过 |

## 5. 已通过功能

- 固定6场 Mock 比赛加载与标准化。
- 国际市场赔率和竞彩固定奖金类型、Provider 和数据库表严格分离。
- `THREE_WAY` 国际赔率去水得到 `P_market`。
- 手工 `P_quant` 作为独立不可变输入保存。
- `QUANT_ONLY_V1` 得到 `P_final=P_quant`。
- `MARKET_QUANT_BLEND_V1` 按配置权重融合并量化。
- 18个 Selection 计算 break-even probability、EV、状态和拒绝原因。
- 只用不同比赛的合格 Selection 生成简单2串1。
- 单注毛返还、期望返还、期望利润和 ROI 计算。
- 2元单位、1至50倍和单票60万元上限校验。
- 100元、200元及自定义预算 Portfolio。
- 默认4张 Ticket 偏好、绝对8张上限和额外 Ticket 严格门槛。
- 合法未使用预算。
- 无价值或无可行票时稳定输出 `NO_BET`。
- SQLite 原子持久化、来源去重、完整输入 Manifest 和封存不可变。
- CLI 中文 UTF-8 输出。
- editable 安装和标准 wheel 安装。

## 6. 明确未实现

以下内容不属于本次 MVP，当前未实现：

- 真实赛程、赔率、竞彩官方 API 或网页采集。
- 真实 Evidence Collector、新闻/伤停证据和 LLM/GPT 调用。
- `P_llm`、EvidenceSnapshot 和 LLM 降级链路的实际运行。
- 让球胜平负、比分、总进球、半全场的概率与计奖。
- 3串4、4串11、复式和 AtomicBet 展开表。
- 比赛相关性修正、Monte Carlo 和组合风险模型。
- 赛果结算、收益归因和完整历史回测。
- Web、HTTP API、用户/权限、定时任务和通知。
- 自动下单、自动登录或任何形式的自动下注。

## 7. 当前 SQLite 表与样例数据

### 7.1 表清单

当前共28张表（含 Alembic 版本表）：

```text
alembic_version
providers, bookmakers, competitions, teams, matches
provider_match_mappings
market_odds_snapshots, market_odds_quotes
sporttery_bonus_snapshots, sporttery_bonus_quotes
manual_quant_inputs, manual_quant_input_outcomes
analysis_runs, analysis_run_matches
market_probabilities, market_probability_outcomes, market_probability_inputs
quant_predictions, quant_prediction_outcomes
final_predictions, final_prediction_outcomes
bet_candidates
ticket_candidates, ticket_candidate_legs
portfolios
tickets, ticket_legs
```

Alembic head：`e3754eb9a102`。

### 7.2 最终验收数据量

验收库包含正常和 NO_BET 两个 AnalysisRun：

| 表 | 行数 |
|---|---:|
| providers | 3 |
| bookmakers | 1 |
| competitions | 1 |
| teams | 12 |
| matches | 6 |
| provider_match_mappings | 18 |
| market_odds_snapshots / quotes | 6 / 18 |
| sporttery_bonus_snapshots / quotes | 6 / 18 |
| manual_quant_inputs / outcomes | 6 / 18 |
| analysis_runs / analysis_run_matches | 2 / 12 |
| market_probabilities / outcomes / inputs | 12 / 36 / 12 |
| quant_predictions / outcomes | 12 / 36 |
| final_predictions / outcomes | 12 / 36 |
| bet_candidates | 36 |
| ticket_candidates / legs | 10 / 20 |
| portfolios | 3 |
| tickets / legs | 8 / 16 |

两个运行复用了相同的6组赔率、6组竞彩奖金和6组手工概率输入，没有重复写入来源快照。结构化 lineage mismatch 查询结果为0。

### 7.3 AnalysisRun 样例

| analysis_run_id | 状态 | Manifest | 结果 |
|---|---|---|---|
| `mvp-release-main` | `COMPLETED` | `MVP_INPUT_MANIFEST_V2` | 100元、200元推荐组合 |
| `mvp-release-no-bet` | `COMPLETED` | `MVP_INPUT_MANIFEST_V2` | 100元 `NO_BET_NO_VALUE` |

两个运行输入相同，因此 Manifest hash 相同：

```text
a75fdb5cf202d071840f21a6ff522a0a3201eb780164c50ed433bb01cf8d6481
```

验收源码 revision：

```text
package:aefe7b80a746d51eb8624db7b4fc45a727475b9ce72385fd97ffd129bdf9c124
```

## 8. 正常案例输出

运行命令：

```powershell
.venv\Scripts\football-system `
  --database-url "sqlite:///data/mvp_release_acceptance.db" `
  --budget-yuan 100 200 `
  --analysis-run-id "mvp-release-main"
```

### 8.1 概率

FusionPolicy：`QUANT_ONLY_V1`，因此 `P_final=P_quant`。

| 比赛 | P_market H/D/A | P_quant = P_final H/D/A |
|---|---|---|
| match-001 | 52.11% / 26.34% / 21.55% | 60.00% / 24.00% / 16.00% |
| match-002 | 46.03% / 27.75% / 26.21% | 52.00% / 27.00% / 21.00% |
| match-003 | 57.59% / 24.49% / 17.92% | 63.00% / 22.00% / 15.00% |
| match-004 | 36.93% / 29.43% / 33.64% | 43.00% / 29.00% / 28.00% |
| match-005 | 34.79% / 29.36% / 35.85% | 36.00% / 31.00% / 33.00% |
| match-006 | 30.23% / 29.76% / 40.01% | 30.00% / 34.00% / 36.00% |

### 8.2 合格 Selection

18个 Selection 中5个合格：

| 比赛 | 方向 | 固定奖金 | 概率 | EV |
|---|---|---:|---:|---:|
| match-001 | HOME_WIN | 1.90 | 60.00% | 14.00% |
| match-003 | HOME_WIN | 1.78 | 63.00% | 12.14% |
| match-002 | HOME_WIN | 2.15 | 52.00% | 11.80% |
| match-006 | DRAW | 3.15 | 34.00% | 7.10% |
| match-004 | HOME_WIN | 2.45 | 43.00% | 5.35% |

### 8.3 简单2串1候选

共10个候选，ROI 从高到低为：

| 序号 | 两个方向 | 联合概率 | 单注毛返还 | ROI |
|---:|---|---:|---:|---:|
| 1 | match-003 HOME + match-001 HOME | 37.80% | 6.76元 | 27.76% |
| 2 | match-002 HOME + match-001 HOME | 31.20% | 8.17元 | 27.45% |
| 3 | match-002 HOME + match-003 HOME | 32.76% | 7.65元 | 25.31% |
| 4 | match-006 DRAW + match-001 HOME | 20.40% | 11.97元 | 22.09% |
| 5 | match-001 HOME + match-004 HOME | 25.80% | 9.31元 | 20.10% |
| 6 | match-006 DRAW + match-003 HOME | 21.42% | 11.21元 | 20.06% |
| 7 | match-006 DRAW + match-002 HOME | 17.68% | 13.54元 | 19.69% |
| 8 | match-003 HOME + match-004 HOME | 27.09% | 8.72元 | 18.11% |
| 9 | match-002 HOME + match-004 HOME | 22.36% | 10.54元 | 17.84% |
| 10 | match-006 DRAW + match-004 HOME | 14.62% | 15.44元 | 12.87% |

### 8.4 Portfolio

| 预算 | 状态 | Ticket 数 | 倍数 | 投入 | 未使用 |
|---:|---|---:|---|---:|---:|
| 100元 | `RECOMMENDED` | 4 | 13 / 13 / 12 / 12 | 100元 | 0元 |
| 200元 | `RECOMMENDED` | 4 | 25 / 25 / 25 / 25 | 200元 | 0元 |

100元组合：

| Ticket | 两个方向 | 倍数 | 投入 | 命中毛返还 | 期望利润 | ROI |
|---:|---|---:|---:|---:|---:|---:|
| 1 | match-003 HOME + match-001 HOME | 13 | 26.00元 | 87.88元 | 7.22元 | 27.76% |
| 2 | match-002 HOME + match-001 HOME | 13 | 26.00元 | 106.21元 | 7.14元 | 27.45% |
| 3 | match-002 HOME + match-003 HOME | 12 | 24.00元 | 91.80元 | 6.07元 | 25.31% |
| 4 | match-006 DRAW + match-001 HOME | 12 | 24.00元 | 143.64元 | 5.30元 | 22.09% |

200元组合使用相同4个候选，每张25倍、投入50元；对应命中毛返还为169.00元、204.25元、191.25元、299.25元。

## 9. NO_BET 案例输出

运行命令：

```powershell
.venv\Scripts\football-system `
  --database-url "sqlite:///data/mvp_release_acceptance.db" `
  --budget-yuan 100 `
  --analysis-run-id "mvp-release-no-bet" `
  --no-bet-demo
```

结果：

```text
Selection EV：合格 0 / 总计 18
简单2串1候选：0
Portfolio 预算：100.00元
状态：NO_BET
原因：NO_BET_NO_VALUE
Ticket：0
总投入：0.00元
未使用预算：100.00元
```

该运行已封存到同一 SQLite，来源快照未重复，AnalysisRun 和18个带拒绝原因的 SelectionCandidate 均保留。

## 10. 下一阶段建议

本报告完成后停止 MVP 功能扩展。若后续另行立项，建议按以下顺序推进：

1. 接入一个真实 Fixture Provider 和一个只读赔率 Provider，先完成契约测试、限流、重试和来源授权。
2. 增加正式 Replay/Verify 命令，从已保存 Manifest 加载冻结输入并对比新旧产物。
3. 建立赛果、结算和收益归因模型，再做 walk-forward 回测；避免在没有结算闭环前扩展策略复杂度。
4. 接入结构化 Evidence 后再实现真实 LLM Assessment，并保留“证据不足即降级”的严格边界。
5. 只有在简单2串1结算和回测稳定后，再设计 AtomicBet、3串4、4串11及复式。
6. 最后再考虑 HTTP API、Web UI、任务调度和通知；自动下注继续保持禁用，除非未来单独完成合规和风控评审。

## 11. 最终状态

- MVP：完成。
- 自动化测试：通过。
- fresh migration：通过。
- 正常案例：通过。
- `NO_BET` 案例：通过。
- SQLite 外键、血缘和封存检查：通过。
- wheel 独立安装与运行：通过。
- 超范围功能：未实现。
- 后续功能扩展：已停止。
