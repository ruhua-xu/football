# Phase 9 — Return Distribution Optimizer implementation candidate

状态：**完整本地门禁通过 / Ready for Architecture Review / ADR Proposed**。

## Baseline and scope

- Branch：`feature/0.9.0-return-distribution-optimizer`。
- Base / main / v0.8.0 target：`cc95d3242218a64712f860fff4979066d4a72e1d`。
- Package metadata保持`0.8.0`；本轮没有main merge、tag或release bump。
- 全程HTTP=0；只使用synthetic/fixed fixtures、正式v0.8.0代码与封存V2图。
- 独立的既有provider capability脚本/报告及用户工作区修改不属于0.9 implementation变更。

## Candidate identity

- Implementation内容manifest SHA256：`d53e3cc59978bbab8136bfe202316e6452fdf9efb74cefff69543a8c0cb0b069`。覆盖29份implementation/config/contract/test文件的LF-normalized内容及base；本报告因自引用单独记录hash。
- 当前是未提交working-tree candidate；Git commit SHA尚未创建，不将base SHA冒充新实现提交。最终交付同时提供候选snapshot标识。
- 核心algorithm code hash：`76995ed699a8d054f3865ffbbc0063a46af2123f18ab34aa6b27a91690dc7aa6`。
- Distribution policy hash：`bd9cf8b2c3d36ce13327cb49b9513c4d6c995762387606ce26bba37ef0769b47`。
- Objective profile hash：`9bb39a771ac2390cb537030c809a7e214dcf88bcce2be17808bc7b3dcbfe7c30`。
- Intended scope：30文件（23新增、7修改）；最终line diff随门禁封存记录汇总。

## Contracts and architecture

- [ADR-0011](decisions/0011-return-distribution-optimizer.md)：**Proposed**，等待唯一一次整体Architecture Review。
- [Return Distribution V1正式候选合同](return_distribution_v1_contract.md)。
- 固定配置：`config/return_distribution_policy_v1.json`、`config/return_objective_profile_v1.json`。
- A–T固定fixture：`data/fixtures/return_distribution_v1.json`。
- Domain：`domain/return_distribution.py`、`domain/services/return_distribution.py`、`domain/services/return_optimizer.py`。
- Application/CLI：`application/return_distribution.py`、`interfaces/return_distribution_cli.py`。
- 新数据库图：25张`rd_*`表；migration **4a637e9051dc**，parent **39526d8f40cb**。

## Mathematics and boundaries

1. 同一match在portfolio内只能一个完整MarketKey，跨market即`CROSS_MARKET_JOINT_UNAVAILABLE`。没有伪造same-match joint概率。
2. 每个unique match只枚举一次selected-outcome/REST状态。不同match采用明确`INDEPENDENT_MATCHES_V1`，不是现实独立性声明。
3. 连通组件内exact Cartesian；独立组件exact convolution，同gross聚合唯一support。
4. 固定512 Decimal context；probabilities不量化、不采样、不丢极小正质量，每层exact sum=1。近似输入和显式unavailable，不重写P_final。
5. 直接消费V2 atomic gross amounts和multiplier；没有重新实现SP乘积或payout rounding。金额与正式Settlement V2逐值对照。
6. Cash纳入ending/profit/threshold/median。Budget0 ratio=null；NO_BET保留全部budget。
7. 固定未校准objective、正向weights与loss/deep-loss penalties；不调预测模型或基于历史结果自动调权。
8. Marginal search先开票后加倍，每步仅正utility增量；V2风险/roles/auxiliary规则保留。旧selected仅benchmark，不是锚点。
9. 支配筛选覆盖当轮feasible increment；计数覆盖独特已评估feasible allocations。不是global optimum或全空间frontier。
10. Catalog、component worlds、support、unique matches、tickets、convolution products、CPU work和artifact bytes均有显式hard bounds。无top-N或近似fallback。

## A–T evidence map（全部通过）

| Case | 验证证据 |
| --- | --- |
| A | NO_BET与budget0 cash/全部metrics |
| B | 单2X1固定P/SP手算support |
| C | AB/AC共享A只乘一次，both-win为0.125而非0.0625 |
| D | 正式sealed V2候选跨market，在enumeration前拒绝；unavailable可持久化/replay |
| E | HOME/DRAW OR及REST概率、不同payout支持点 |
| F | 3X4完整8 worlds，support 0/800/4000 |
| G | 4X11支持、expected gross、median、2x/3x，与full-catalog oracle一致 |
| H | AB/AC、DE、FG组件8+4+4，与完整128 relevant-world oracle一致 |
| I | budget10000/stake6000/cash4000全输ending仍4000 |
| J | 五点支持逐项threshold/expectation/median/shortfall goldens |
| K | 高overlap ABC/ABD与较低ROI独立EFG；第二次开票选择EFG |
| L | 有V2 witness的HEDGE只在正增量时选；负增量不强制选择 |
| M | LONGSHOT小额/高回报/正p3贡献；负utility或不合格EV不恢复 |
| N | 所有增量非正时NO_BET胜出，与旧V2 allocation可不同 |
| O | Catalog33>32、world/support/convolution/CPU等各独立guard，无partial优化结果 |
| P | 不同hash seed、candidate/request顺序、ambient precision/rounding产生相同bytes/hash |
| Q | SQL不可变/typed FK/deferred seals、exact retry、support/probability/multiplier/source/metric篡改检测 |
| R | 真正v0.8.0代码创建旧库；升级后所有旧定义/trigger/rows/serialized artifacts不变，旧read/replay/settle通过 |
| S | 冻结文件与v0.8.0逐字节LF-normalized比较，所有旧migration不改 |
| T | 2X1/3X4/4X11、mixed markets、handicap和OTHER concrete结果逐值等于Settlement V2 |

独立oracle只在测试代码中枚举小fixture的完整catalog/world及小candidate/multiplier组合。
正常4票/12-match组件benchmark只需32 component worlds，而非4096 whole worlds。

## Gate status

完整pytest：**2745 passed / 1 skipped**，2746 collected/verified unique nodes、127个有界隔离分区，0 failures、0 errors，最终failed_groups为空。
唯一skip为Windows上的POSIX `dir_fd/O_NOFOLLOW`验证。全工作区计数包含既有synthetic诊断测试，没有将provider capture或真实数据分析当作测试覆盖。
工具会话中断曾导致部分进程`0xC000013A`及截断回执；保留35个已验证成功分区，恢复92个未完成/未验证分区。最终每个node只计一次，中断attempt既不计通过也不重复计数。
最终receipt：`C:/Users/93428/AppData/Local/Temp/opencode/r9/summary.json`，`complete=true`，绑定本报告列出的implementation内容hash。
Ruff全仓、compileall、fresh migration/check、empty downgrade/re-upgrade、真正v0.8.0旧库upgrade及populated downgrade refusal已通过。
旧deferred-FK cycle排序SAWarning保持可见，未suppress；不属于本轮新功能失败。
Wheel build与offline installed-wheel E2E已通过，实际 **76** 个显式资源，版本metadata **0.8.0**，head **4a637e9051dc**。
候选wheel SHA256：`662d28a50c260c08aefb8d13eddb3436bfe091242b55f25938b398a449cc44cd`，位于独立`phase9-built`目录，正式v0.8.0 wheel未覆盖。
依赖来自本机既有installed distributions的临时offline wheelhouse；pip显式`--no-index`，没有下载或更新依赖。

Installed-wheel追加验收：evaluate、optimize、show、exact retry、NO_BET、J阈值fixture及正式V2结算一致性全部通过。

| 原封存V2 pass | Evaluated support数 | Optimizer结果 | 与Settlement V2一致的gross（fen） |
| --- | --- | --- | --- |
| 2X1 | 3 | NO_BET | 160000 |
| 3X4 | 13 | OPTIMIZED | 3545600 |
| 4X11 | 30 | OPTIMIZED | 5430400 |

这些gross只验证冻结source的数学一致性，不是优化后的真实收益或alpha。
Scoped secret-pattern/已知环境credential扫描：30份intended文件，**0 findings**；不输出credential值。
`git diff --check`通过；现有CRLF提示保留。

| Required gate | 终态 |
| --- | --- |
| Full pytest / old golden regressions / A–T | PASS：2745 passed、1 platform skip |
| Ruff / compileall | PASS |
| Fresh upgrade / migration check | PASS，head 4a637e9051dc |
| Empty downgrade / re-upgrade | PASS |
| Real v0.8.0 code existing-data upgrade | PASS，旧定义/trigger/rows/serialized artifacts逐值保持 |
| Populated downgrade refusal | PASS |
| Wheel build / isolated installed-wheel E2E | PASS，76 resources，offline dependency installation |
| Whitespace / scoped secret scan | PASS / 0 findings |

远端CI：**NOT_RUN / 本轮HTTP=0**。未创建远端候选提交或触发CI；本地门禁不冒充远端Success。

## Known limitations / interpretation

- Same-match跨market joint、真实统计correlation、copula、Monte Carlo均不支持。
- 独立比赛假设和uncalibrated objective不是现实收益保证；legacy comparison只是software comparison。
- Marginal search不保证global optimum；不会回退为exhaustive solver或在加倍阶段重新开启被拒绝票。
- 所有exact结论限定在封存输入、正式policy和hard bounds内；超限明确unavailable。
- SQLite-only；新support金额用signed-int64 fen，budget最多128位，单artifact最多64MiB。
- 0.9工件重放要求匹配的算法code hash；未实现跨implementation版本兼容执行器。
- 没有Kelly、formal CVaR、跨日bankroll/赛季模拟、新market或新概率模型。
- 没有真实training/prediction/LLM/backtest/betting。完整旧回归套件中的synthetic例程不代表新真实业务运行。

完成本候选后停止，等待网页GPT的唯一一次0.9 Architecture Review。

## Local handoff artifacts

- 候选wheel：`C:/Users/93428/AppData/Local/Temp/opencode/phase9-built/football_system-0.8.0-py3-none-any.whl`。
- Installed-wheel evidence：`C:/Users/93428/AppData/Local/Temp/opencode/phase9-installed/return-distribution-acceptance/acceptance-summary.json`。
- Scope/hash/secret-scan manifest：`C:/Users/93428/AppData/Local/Temp/opencode/phase9-manifest.json`。
- Review snapshot、patch、zip的标识随最终交付记录提供；snapshot是Git tree，不是新commit，不改变真实index/main/tag。
