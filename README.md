# Football System

可回放的足球比赛分析、中国竞彩简单2串1组合决策、Portfolio 风险分析和离线 LLM 文件协作工具。

## 发布状态

- `v0.1.0` 固定在提交 `fceb945d07218290dc85b465e885a47ae9912c3f`，保留原始 MVP 行为。
- 当前主分支为 `0.2.0`，在不改写概率、EV、竞彩计奖和不可变 AnalysisRun 的前提下增加风险层与离线文件桥。
- 项目不连接真实比赛 API 或真实 LLM API，不读取 API Key，不执行自动下注。

## 当前能力

- Mock 比赛、国际市场赔率、竞彩固定奖金和手工 `P_quant`。
- `THREE_WAY` 去水、`QUANT_ONLY_V1` 与 `MARKET_QUANT_BLEND_V1`。
- Selection EV、简单2串1、2元投注单位和官方奖金舍入。
- 显式 `CashPosition`、`NO_BET` 和未使用预算。
- Portfolio Risk、比赛/方向 Exposure 和确定性 Stress Test。
- SQLite、SQLAlchemy、Alembic、不可变 AnalysisRun 和追加型审计工件。
- `analysis_packet` 导出、`llm_review` 严格校验和离线导入。

## 本地运行

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m pytest
.venv\Scripts\football-system --budget-yuan 100 200
```

## 离线文件桥

先运行分析，再从已封存的 AnalysisRun 导出白名单数据包：

```powershell
.venv\Scripts\football-system analysis-packet export `
  --database-url "sqlite:///data/football_mvp.db" `
  --analysis-run-id "run-001" `
  --output "exchange/analysis_packet.json"
```

外部协作者根据该文件生成 UTF-8 `llm_review.json`。Review 必须返回绝对 `P_llm`，并原样绑定 `analysis_run_id`、`packet_id` 和 `packet_hash`。Packet 不包含 `P_final`、EV、Ticket、预算、stake 或 Portfolio。

纯文件校验不会打开数据库：

```powershell
.venv\Scripts\football-system llm-review validate `
  --packet "exchange/analysis_packet.json" `
  --review "exchange/llm_review.json"
```

校验通过后，将 Review 作为追加型工件导入：

```powershell
.venv\Scripts\football-system llm-review import `
  --database-url "sqlite:///data/football_mvp.db" `
  --packet "exchange/analysis_packet.json" `
  --review "exchange/llm_review.json"
```

导入不会更新原 AnalysisRun、`P_final`、候选、Ticket 或 Portfolio。相同规范化 Review 重复导入是幂等操作。

当前严格文件合同和完整 JSON 示例见 `fankui/llm_review_v1_contract.md`；wheel 安装后位于 `football_system_resources/fankui/`。`fankui/llm_strategy.md` 描述的是后续扩展设计，不能直接作为 `LLM_REVIEW_V1` 输入。

## 范围边界

真实数据源、真实 LLM 调用、LLM 融合回写、3串4、4串11、复式、实际赛果结算、Web 和自动下注尚未实现。架构与模型资料位于 `fankui/`。
