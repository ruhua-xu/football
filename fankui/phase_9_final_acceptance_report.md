# Phase 9 — Final Acceptance / v0.9.0 Release Closeout

## Architecture Review acceptance

**0.9 Architecture Review PASSED，ADR-0011 Accepted。** Workspace-owner于2026-09-18明确接受：

| 标识 | 接受值 |
| --- | --- |
| Candidate Git tree | `52284cdd20111da15b7be6d5d70c0bce38b19a86` |
| Candidate content SHA256 | `d53e3cc59978bbab8136bfe202316e6452fdf9efb74cefff69543a8c0cb0b069` |
| Algorithm code hash | `76995ed699a8d054f3865ffbbc0063a46af2123f18ab34aa6b27a91690dc7aa6` |
| Distribution policy hash | `bd9cf8b2c3d36ce13327cb49b9513c4d6c995762387606ce26bba37ef0769b47` |
| Objective profile hash | `9bb39a771ac2390cb537030c809a7e214dcf88bcce2be17808bc7b3dcbfe7c30` |
| Formal v0.8.0 base | `cc95d3242218a64712f860fff4979066d4a72e1d` |

本次只完成release closeout。ADR仅更新状态与接受记录，技术决策正文不变。
Return distribution、component/convolution、objective weights、search semantics、complexity guards和persistence semantics保持已审查内容。

## Release identity and boundary

- Branch：`feature/0.9.0-return-distribution-optimizer`。
- Package/runtime version：**0.9.0**。
- Migration head：**4a637e9051dc**，parent **39526d8f40cb**；没有新增closeout migration。
- 25张`rd_*`表；wheel显式资源清单仍为**76**，本报告不增加runtime资源。
- 最终feature/main/tag SHAs及三条CI URLs由最终发布交付记录核验，避免为记录自身commit hash产生递归提交。
- 仅允许ADR状态、最终报告、版本声明/期望及release documentation偏离接受tree。

## A–T acceptance retained

| Case | 接受证据 |
| --- | --- |
| A | NO_BET现金baseline和budget0指标，无除零 |
| B | 固定2X1 P/SP手算support与程序一致 |
| C | AB/AC共享A只乘一次，both-win=0.125，不是0.0625 |
| D | 正式V2候选同场跨market在enumeration前拒绝，可持久化/replay unavailable |
| E | Simple multiple OR、selected outcomes/REST及不同payout正确 |
| F | 3X4完整8 worlds，聚合0/800/4000 support |
| G | 4X11 sum1、expected gross、median、2x/3x与full-catalog oracle一致 |
| H | Component enumeration 8+4+4与完整128 worlds oracle逐值一致 |
| I | budget10000/stake6000/cash4000，全输ending仍4000 |
| J | 五点support逐项threshold/expected/median/shortfall goldens |
| K | 高overlap与较低standalone ROI独立票，distribution-aware选择独立增量 |
| L | HEDGE需V2 witness及正utility；负增量不强制加入 |
| M | LONGSHOT小额/高回报/独立eligible outcome；EV或utility不足不恢复 |
| N | 所有实际增量非正则NO_BET胜出 |
| O | Catalog、component/support、convolution/work等超限明确拒绝，无partial optimized recommendation |
| P | Hash seed、input order和ambient Decimal context改变不影响bytes/hash/selection/metrics |
| Q | Typed FK、append-only/deferred seals、exact retry及support/probability/multiplier/source/metric篡改检测 |
| R | 正式v0.8.0代码生成旧库，升级后旧表/trigger/rows/serialized artifacts逐值保持，旧read/replay/settle正常 |
| S | Elo/Poisson/P_final/Fusion/V4/EV/payout/Strategy Pass/Settlement及旧migrations冻结回归 |
| T | 完整normalized results下，2X1/3X4/4X11、mixed markets、handicap/OTHER与正式Settlement V2逐值一致 |

接受阶段完整working-tree测试为2745 passed / 1 skipped（2746 nodes，含43个既有未提交provider诊断synthetic tests）。
版本升级后的final gates另行完整运行；发布tree不会混入这些不属于接受tree的诊断文件或upload工件。

## Exact assumptions and interpretation

- Exact仅限原封存P_final及**INDEPENDENT_MATCHES_V1**，不宣称现实统计独立。
- 每个unique match状态只枚举一次；同一portfolio内必须同一完整MarketKey，否则**CROSS_MARKET_JOINT_UNAVAILABLE**。
- Marginals必须实际sum1；REST为未选质量；不修改P_final，不对同场跨market独立相乘，不Poisson替换/IPF/copula。
- 固定Decimal 512；概率不量化、不丢极小正质量，component/final支持均exact closure。无限利润比例仅按已接受固定context处理。
- 只消费V2 sealed atomic gross；cash正确计入ending capital/profit，未重新定义SP payout/EV。
- **UNCALIBRATED_STRATEGY_POLICY_V1**为固定策略参数，不是预测模型调参。
- **RETURN_DISTRIBUTION_MARGINAL_V1**不是global optimum；Pareto计数不代表未遍历全空间的frontier。
- Synthetic acceptance和legacy utility comparison不证明真实ROI、alpha或收益优势。

## Final release gates

当前状态：**0.9.0版本升级后的完整本地final release gates已通过**。

- 干净发布tree完整pytest：**2702 passed / 1 skipped**；2703 collected/verified unique nodes、126个隔离分区，0 failures、0 errors、failed_groups为空。
- 唯一skip为Windows上的POSIX `dir_fd/O_NOFOLLOW`验证。工作区43个既有未提交诊断测试另行通过，合计2745 passed / 1 skipped；这43项不混入发布tree。
- 测试tree：`21ca296aaed7eee19246d74192bcd2ccf9fd3a88`；测试后仅补充本最终报告的实测门禁记录。
- 完整pytest receipt：`C:/Users/93428/AppData/Local/Temp/opencode/rel9/summary.json`，`complete=true`。
- 正式wheel：`football_system-0.9.0-py3-none-any.whl`，**929694 bytes**，**76 resources**。
- Wheel SHA256：**`a8360f56a7b52ac60e0bf4baed714f011b090b3da6aa8269b0af1ddf547ebf3d`**。
- Wheel来自独立`rel9-built`目录；isolated installed-wheel E2E使用本地offline wheelhouse与显式`--no-index`，完整旧路径及新增return-distribution路径全部通过。
- Installed-wheel summary：`C:/Users/93428/AppData/Local/Temp/opencode/rel9-installed/return-distribution-acceptance/acceptance-summary.json`。

| 原封存V2 pass | Exact support数 | Optimizer状态 | 对照Settlement V2的gross（fen） |
| --- | --- | --- | --- |
| 2X1 | 3 | NO_BET | 160000 |
| 3X4 | 13 | OPTIMIZED | 3545600 |
| 4X11 | 30 | OPTIMIZED | 5430400 |

这些金额仅是synthetic frozen-input一致性证据，不是实际收益。

| Gate | Closeout状态 |
| --- | --- |
| Full pytest / A–T / frozen goldens | PASS：2702 passed / 1 platform skip |
| Ruff / compileall | PASS |
| Fresh migration/check | PASS，head 4a637e9051dc |
| Empty downgrade/re-upgrade | PASS |
| Existing-data upgrade from formal v0.8.0 | PASS，旧定义/trigger/rows/serialized artifacts逐值保持 |
| Populated downgrade refusal | PASS |
| Wheel build / isolated installed-wheel E2E | PASS，0.9.0 / 76 resources |
| git diff --check / secret scan | PASS / 35个intended文件0 findings |
| Final tree vs accepted tree allowlist | PASS，仅11个获准closeout文件差异 |

接受tree核验已通过：所有30份候选文件与已接受hash相符；119份v0.8 domain/application/config/migration文件内容保持一致。
最终index已直接比较旧Git blob bytes；119份v0.8 domain/application/config/migration文件均完全一致，除closeout allowlist外的完整tree与接受tree一致。
Algorithm/policy/objective hashes仍逐值等于本报告顶部接受值；release gate没有暴露需要改动业务逻辑的bug。
旧deferred-FK cycle排序SAWarning保持可见，不抑制。

## Known limitations retained

- Same-match跨market joint、统计correlation ML、copula、Monte Carlo均不支持。
- Catalog32、component worlds/support各65536、unique matches12、preferred4/absolute8，以及额外CPU/金额/bytes bounds按原合同保留；超限explicit unavailable，无top-N/approximation fallback。
- SQLite-only；gross支持signed-int64 fen，budget最多128位，单artifact最多64MiB。
- 0.9工件重放要求匹配算法code hash；没有跨implementation版本兼容执行器。
- 没有Kelly、formal CVaR、跨日bankroll/赛季资金模拟、新market、新概率模型或真实资金下注。

## Network and stop point

**真实provider/LLM HTTP=0；没有真实training/prediction/LLM/backtest/betting。** 完整门禁包含既有synthetic回归/模拟例程，不代表真实业务运行。
仅按本次授权进行GitHub push、CI状态查询及发布refs操作；不调用Sportmonks/The Odds API/竞彩，不读取已清理的capability raw。
Candidate HEAD CI完整Success后才合入main并创建annotated `v0.9.0`；main/tag CI同样必须完整Success，不能提前宣告。
完成后立即停止，等待1.0 Prospective Validation / Production Closeout指令。
