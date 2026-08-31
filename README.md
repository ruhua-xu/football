# Football System MVP

可回放的足球比赛分析和中国竞彩简单2串1组合决策 MVP。

当前实现范围：

- Mock 比赛、国际市场赔率和竞彩固定奖金。
- `THREE_WAY` 市场概率去水。
- 手工 `P_quant`。
- `QUANT_ONLY_V1` 与 `MARKET_QUANT_BLEND_V1`。
- Selection EV、简单2串1、官方奖金舍入。
- 100元、200元和自定义预算 Portfolio。
- 默认优先最多4张顶层 Ticket、配置化绝对上限、正整数倍数和 `NO_BET`。
- SQLite、SQLAlchemy、Alembic、CLI 和 pytest。
- 版本化完整输入 Manifest、payload hash 复核、封存触发器和可读取审计记录。

架构和模型文档位于 `fankui/`。

## 本地运行

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m pytest
.venv\Scripts\football-system --budget-yuan 100 200
```

项目也可构建为 wheel；配置、Mock fixture 和 Alembic migration 会随 wheel 一并安装。

真实数据源、真实 LLM、3串4、4串11、复式、Web 和自动下注不在 MVP 范围内。
