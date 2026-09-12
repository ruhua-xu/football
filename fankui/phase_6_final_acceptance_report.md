# Phase 6 最终真实验收与 v0.6.0 发布收尾报告

## 结论与边界

**用户已明确接受 0.6 real acceptance，功能冻结。** 本轮只做 final report、version bump、final CI、main merge、annotated tag 与 main/tag CI 核验；不再增加 0.6 功能，也不提前实施 0.7。

真实验收基于软件候选 `2209242ef98e98e5e00dd7ab3686f349dfd0bd28` 与报告提交 `ece063cfbbef982a5dd2a786973b67dda4f2ff95`。候选的 [完整 CI #21](https://github.com/ruhua-xu/football/actions/runs/34463350386) 已核验 Success，用户随后接受完整真实闭环。它不代替本次 version bump 后最终候选、main 或 tag 的 CI。

本公开报告只保存工程范围、非行级验收结论与发布门禁。真实 raw、具体比赛 SP/概率/EV、私有审批/模型/Packet 的 ID/hash 清单和数据库仍只在原受限本地目录内；不将有期限的 state/audit retention 变成公开 Git 的永久副本。

## 已实际完成的验收

| 环节 | 已核验事实 |
| --- | --- |
| 来源用途 | workspace-owner 明确批准受限用途与 reviewer 身份；使用既有正式 source-rights recorder，不把软件授权当作数据许可 |
| 完整 cohort | Bundesliga 2025/2026 source-defined 全赛季，7 页、306 场；不按比分、模型输出或效果选择，未复用旧三场诊断证据 |
| 当前快照准入 | 原生身份、season、FT 常规时间比分与 period 一致性通过；7 个 local capture receipts，1 个完整原子 admission，306 个 observed records/normalized results |
| 时间依据 | CURRENT_SNAPSHOT_OBSERVED，SOURCE_TIME_RESEARCH、retrospective=true；未知上游发布时间/版本仍为未知，不能代替严格历史 source-time |
| Structural replay | 固定 Elo、顺序/真实 season/目标排除与确定性重放通过；一次失败 attempt 保留，随后成功，共同进入完整 terminal attempt root |
| 精确生产批准 | 实际 manifest 形成后由 workspace-owner 对精确 payload/hash 单独审核，通过既有 ApprovalV2 recorder；四项 grants 与有界 retention 未扩张 |
| Sealed release | 使用精确 approved manifest/state 构建并实际提交，文件与数据库工件一致；中断构建经 SQLite 自身回滚恢复后按原 request key 完成，没有重造审核或伪造 commit time |
| 真实 live 输入 | 当前官方截图与用户真实采集时间声明；分钟级精度单列说明，未用 mtime/EXIF/当前时钟替代 capture；用户确认 SELF_REVIEWED 与销售状态，既有 Manual V2 与精确身份/live ingest 通过 |
| 国际市场输入 | 一次 current The Odds API h2h 请求，零自动重试；目标获得24家 bookmaker 的共识。其他同联赛事件的问题记录保留，目标输入质量全部通过 |
| Target / live | 实际 target plan 提交早于声明的 decision cutoff；只消费 pinned sealed release 和 ready live preparation，research training provider 为 None；真实 P_quant 为 MODEL/AVAILABLE，非 null，概率合计1，不复制 P_market |
| V3 / audit | 原 V3 Packet、mandatory sidecar 和 bundle manifest 已导出、读回；原 V3 wire/golden 不变，Packet 无 P_final/EV/ticket/budget/stake，全部9场预声明未来比赛均不在训练事实中 |
| 网页 Review | 用户提供的真实 LLM_REVIEW_V3 验证为 VALID，精确绑定 Packet/context/match/evidence；原 Review bytes 不改，经 concrete audit/current-authorization 门禁导入 |
| 后审闭环 | 原配置生成并持久化 FusionRun、PortfolioRevision；P_final/EV 与既有公式复核一致，未修改原 AnalysisRun 或 Packet |
| 最终结果 | NO_BET，实际原因码 NO_BET_NO_FEASIBLE_TICKET；三项 selection 均负 EV 且低于原门槛，另有单场2串1限制与原零预算，不能将工程结果误述为收益或模型效果 |
| 完整性 | 最终数据库确认各1个 AnalysisRun、Packet、Review、FusionRun/result、PortfolioRevision，外键检查无错误；所有失败及中断记录保留 |

严格历史性能始终是 `UNAVAILABLE / UNPROVEN_HISTORICAL_VERSION_TIME / metrics=null`。本次是一场真实工程闭环，不是独立 out-of-sample、校准或回测收益证明。源图复验耗时较长；真实运行跨过系统时间间隔后沿原进程继续，输入 cutoff 与 actual run/publication times 分开披露，未将旧输入重标成新抓取。

## 发布冻结与历史工件保护

- 固定 `ELO_THREE_WAY_BASELINE_V1`、version `1`、原参数、数学与 `selection_ev` 公式不改。
- 不改 `MVP_INPUT_MANIFEST_V3`、`ANALYSIS_PACKET_V3`、`LLM_REVIEW_V3`、`OFFLINE_REVIEW_VALIDATOR_V3` wire/字节语义。
- 不修改真实审核、source/manifest/release/state/Packet/Review/Fusion/Revision 工件、历史0.5运行或旧三场诊断材料。
- 发布版本 `0.6.0` 与固定模型 version `1` 是两个概念。Package version 更新不触发新训练或重放写入，不将历史记录的 code revision 替换为发布后的新 revision。
- 既有验收工件保留候选代码下的原 pins；将来需要核验历史时，应使用其原始受授权代码/证据，而不是为适配新 package metadata 改写它们。
- 发布不增加玩法，正式市场仍为 THREE_WAY、pass type 仍为现有简单2X1；无自动下注、账户支付或新 LLM API。

## 本轮发布变更

仅修改 package/runtime version、SQLite-only 版本提示、wheel 验收期望与版本测试文件名，以及 README/CHANGELOG/本报告。迁移 head 仍为 `17304b6d28a9`，wheel resource allowlist 仍为55项；不新增 schema 或资源范围。

`fankui/phase_6_implementation_report.md` 保留早期实施与阶段门禁事实，本文作为最终验收汇总；不把其中历史时点的 pending 状态误当成本次终态。现有 exchange/yaoqiu 工作区改动不纳入本发布提交。

## 质量门禁

| 门禁 | 本报告首次封存时状态 |
| --- | --- |
| 真实功能验收 | 已获用户接受；原候选及 CI #21 保留 |
| 候选实现本地覆盖（历史证据） | 2390 passed / 1 skipped，2391 collected 的完整分区覆盖，不冒充单次 pytest |
| V3/quant golden（真实验收期间） | 39 passed；没有改动 golden 以迁就结果 |
| 发布版本一致性 / Ruff / compile / targeted tests | Ruff、compileall 通过；现有 wheel-script / review-bridge / quant-model 定向测试45 passed（1.64秒）；没有修改数学/V3 golden |
| Fresh migration | 全新临时 SQLite upgrade head + command.check 通过；head `17304b6d28a9`，No new upgrade operations detected；原 deferred FK 环排序 warning 保留，未 suppress |
| Wheel build / installed-wheel E2E | `football_system-0.6.0-py3-none-any.whl` 构建通过；显式选择新 wheel 隔离安装验收通过，55资源、版本0.6.0、CLI/schema、8条历史路径、2×10 slices、settlement/report 与 head 均验证；旧 wheel 保留 |
| Whitespace / 变更范围 | git diff --check 通过；版本与公开文档之外无功能改动，原真实受限目录不 stage、不打包 |
| 最终完整候选 CI | 发布提交后核验，不能以原 CI #21 代替 |
| main merge / annotated v0.6.0 | 仅在最终候选 CI 成功后进行，禁止覆盖已存在 tag 或强推 |
| main/tag CI | 必须分别核验对应 ref 和 exact commit 的完整终态；在终态成功前不宣布正式关闭 |

发布 commit、main/tag refs、annotated tag object 与 CI run URL/终态在最终交付消息中列明，避免为报告自引用反复制造新的发布提交。完整 CI 仍运行现有 Ubuntu/Python3.12 流程：lint、compile、全仓 pytest、fresh migration/check、wheel build、installed-wheel E2E、whitespace；不削减检查或改为仅文档 smoke。

## 私有证据与 retention（不变）

真实材料仍在已批准的 `data/research/bundesliga_acceptance_20260911/` 隔离根，详细验收结果见其中既有 `POST_REVIEW_RESULT.md`、`post-review-result-summary.json` 和原工件。本文不复制受限内容或将它们打入 wheel。用户上传 Review 的原目录保持原样；导入时只使用三份原 bundle 文件的字节一致工作副本。

- 数据请求总计19/20，最后1次 contingency 未使用；一次 Sporttery官方 HTTP567 失败原样保留、未重试。发布/CI 不新增 provider 数据请求。
- Raw、derived model state、audit 继续截止 `2026-09-15T00:00:00Z`，或更早实际账号/供应商限制；到有效 cutoff 停止使用，并按原授权最迟1小时内清理。不可为未完成的发布、CI 或后续阶段延长。
- 清理责任人为 workspace-owner，执行委托 OpenCode；现有一次性 Python allowlist 任务 `OpenCode-0.6-Acceptance-Cleanup-20260915` 的批准根、参数与时间不改。
- 任务为当前 Windows 用户的 Limited/Interactive 模式，需保持登录、机器开机或可唤醒；不虚称对关机/未登录状态有一小时完成保证。若确认更早限制，仍须按原义务提前停止/清理。
- 原0.5数据、旧三场诊断目录及其他未授权材料不在该任务删除目标中，其各自既有保留义务不因发布改变。最终只留获准的非敏感 cleanup receipt。

## 关闭后停止

在 `v0.6.0` annotated tag、main 与各自 CI 完成正式核验后停止，等待用户对 **0.7 Strategy Profile + Pass Type Engine** 的独立指令。本文不授权设计或实现0.7。
