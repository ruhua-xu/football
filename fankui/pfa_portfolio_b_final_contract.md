# PF-A Portfolio B Final Contract — v1, for implementation review

**架构 B 已获原则批准；本文件和可执行约束模型是 PF-A 审查产物，不是 PF-B 实施验收。**

正式实现基线为已批准 P0 `9429bcf695527d5c1d2663b2915faa388ab4a065`。
独立分支 `feature/pf-a-portfolio-contract`。本分支不修改 production source、migration、
原有 operator 或数学代码。`contracts/portfolio_b_schema.py` 仅定义审查 Schema；
`contracts/portfolio_b_ownership_model.sql` 仅在隔离 SQLite 约束模型中运行，禁止当作生产 migration。

## 1. 架构与不变量

```text
独立 qualified scope/model A → program A → MODEL admission → pin A → anchor/epoch/run A
独立 qualified scope/model B → program B → MODEL admission → pin B → anchor/epoch/run B
→ PREDICTION_SOURCE_SNAPSHOT_V1（不认领 head/slot，不占预算）
→ PORTFOLIO_ASSEMBLY_V1（不认领 head/slot，不占预算）
→ ATOMIC_PORTFOLIO_LOCK_V1（全部 head/slot/budget 同一事务认领）
→ PORTFOLIO_SETTLEMENT_V1（组合资金只结算一次）
```

模型 qualification、准确人工 approval、release/target/state-binding 是上游前提。
Source run 保留原 program/epoch/anchor/精确 UTC bucket；不合并旧 run、不伪装 DecisionLockV2。
首版支持已 verified 的 OFP V1 和 qualified V2 competition source；未证明 competition
归属的 legacy model source 不自动升级为 Portfolio 合格来源。legacy 独立 DecisionLock 继续保留。

S1：Snapshot/Assembly 没有正式预测、资金或 head 所有权。
S2：一个 source head 只能成功被 legacy LOCK、legacy PRE_LOCK REPLACE、Portfolio LOCK 之一消费。
S3：一个 `(program_id, canonical_match_id, THREE_WAY_market_hash)` slot 只有一个 owner family。
S4：一份独立 budget authority 只允许一次 Portfolio Lock；不汇总 member 的 budget/stake/cash。
S5：完整 member set、全部概率 frame、资金、claims 和 final seal 同事务提交或全部回滚。
S6：资格、赔率新鲜度、最早 kickoff lead 在实际发布时仍成立；任何 member 失败则整体拒绝。

## 2. 正式 Schema 与序列化

Schema 源为 `contracts/portfolio_b_schema.py`，导出九份 JSON Schema。各对象 closed/frozen、
unknown fields 拒绝；不接受 caller-supplied cutoff、概率、赔率或 budget amount 作为 lock request。
Seal 使用现有 `SealedReleaseArtifactV1` 规则：schema-tagged canonical content hash 与派生 ID，
日期规范化 UTC，金额为严格非负 integer fen，概率沿用既有 Decimal/完整分布类型。
JSON Schema 无法表达的跨引用、完整集合、时钟与重放要求以本规范和 repository 校验为准。

| Schema | 必须封存的内容 |
|---|---|
| `PREDICTION_SOURCE_SNAPSHOT_V1` | 原 run/program/epoch/anchor/bucket/model pin、qualified scope hash、competition/season、analysis/packet/完整 V4 review 或显式 V4 abstention/audit/fusion、全体 LockedProbabilityUnitV1、source cutoff/receipt、configuration/implementation/dependency/census hashes |
| `PORTFOLIO_BUDGET_AUTHORITY_V1` | funding scope、唯一 allocation/cycle、CNY_FEN、授权 budget、完整 target census、policy、operator authority、准确 approval、有效期、独占划拨规则 |
| `PORTFOLIO_ASSEMBLY_V1` | 有序唯一 snapshot refs、budget authority、全 member/census/dependency/projection hashes、trusted common cutoff、最早 member kickoff、原 kernel 的 plan/optimizer/return refs、ALLOCATED 或 NO_BET、stake/cash |
| `ATOMIC_PORTFOLIO_LOCK_V1` | exact assembly/budget refs、member/ownership/revalidation hashes、common cutoff、实际 lock time、earliest kickoff、trusted receipt 与单份财务结果 |
| `PORTFOLIO_SETTLEMENT_V1` | exact Portfolio Lock、previous settlement、合法 result observation 集合、结果版本 hash、SETTLED/MISSING_RESULT/UNSUPPORTED、单份 budget/stake/cash/payout/P&L |
| 四类 Request | snapshot、assembly、lock、settlement；只接收已存在工件 ID 和幂等 request_key |

Snapshot 封存一个完整 bucket 的全部单位，不只是最终票选中的腿。只允许同一 bucket 的
单一 qualified model/state，所有 P_market/P_quant/P_base/P_final 必须 AVAILABLE；P_llm
可按原 V4 abstention/fallback 规则不可用。一次 Assembly 至少两份 snapshot、全体单位最多
64，不放大既有 numerical limits。重复 source run/snapshot 或跨 program 的同一 canonical
match/market 在完整输入集合中都拒绝；不能仅在选中票上去重。

原 whole-bucket UNAVAILABLE 保留；缺模型/缺价格目标留在 census，整体返回明确
UNAVAILABLE/拒绝结果，不丢成员凑票。NO_BET 仅是合格输入上 kernel 的合法计算结果。

## 3. API 合约

| 操作 | 输入 | 成功输出与状态 |
|---|---|---|
| `snapshot.prepare` | source_run_id、imported_review_id、review_mode、request_key | 完整 verified immutable snapshot；run 仍 PREPARING，0 claims |
| `budget.approval-prepare` | 独占 allocation、精确 census/policy、金额、有效期 | 审核载荷；STOP 人工确认，不自动授予预算 |
| `budget.approval-record` | exact payload/hash/authorized reviewer evidence | sealed budget authority；不来自 source run cash |
| `portfolio.prepare` | ordered snapshot_ids、budget_authority_id、ticket_request_bundle_id、request_key | sealed Assembly 与 2X1/3X4/4X11 等既有合法 ticket plans，0 claims |
| `portfolio.lock` | assembly_id、request_key | 原子 Portfolio Lock，重新校验当前全部输入；同 request+same hash exact retry 返回原结果 |
| `portfolio.settle` | portfolio_lock_id、合法 result_observation_set_id、request_key | append-only settlement/revision；缺结果不伪造 payout |
| `portfolio.audit` | lock/assembly ID、trusted as-of watermark | 新 versioned ownership/financial view；原 legacy artifacts 按原 schema/watermark 重放 |

同 request_key 不同载荷拒绝；失败请求保留独立 audit receipt，但不得留下部分 ownership/
budget/financial lock。Service 只能由共享 session/transaction 的可信 repository 构建数值来源；
不接受调用者上传“已验证 descriptor”或 P_final 数组绕过原 V4 import/fusion/source 校验。

## 4. 数据库图与完整约束

PF-B 的 additive migration 增加：

| 表族 | 主键/关系/门禁 |
|---|---|
| `pf_artifacts`, `pf_receipts`, `pf_seals` | ID/schema/hash 三元唯一；可信 receipt 顺序/时间；完整 typed parents/children；append-only |
| `pf_snapshots`, `pf_snapshot_members` | header FK；source run/program/pin/bucket/原 frames exact projection；source run+snapshot revision 不被当成 claim |
| `pf_assemblies`, `pf_assembly_members` | ordered snapshot membership；unique assembly+snapshot/run；完整 canonical match/market 去重；全体 ≤64 |
| `pf_locks`, `pf_lock_members`, `pf_lock_seals` | 一个 assembly 至多一个 lock；每 member 必须与 assembly 相同；完整 claims/budget/frames/数值重放后才允许 seal |
| `pf_shared_heads` | **PK parent_run_id**；typed legacy LOCK/REPLACE event 或 PF lock ownership，不允许 nullable 混合 owner |
| `pf_shared_slots` | **PK program_id,match_id,market_hash**；初始 LEGACY 或 PORTFOLIO owner family 不可改 |
| `pf_shared_versions` | 上述 key+version PK；previous=version-1、唯一 successor、同 family、typed lock FK；首版 PF 只 version0 |
| `pf_budget_authorities`, `pf_budget_claims` | 合法 grant/allocation/cycle/hash FK；unique funding_scope+cycle、unique allocation、unique authority claim、unique lock；独占资金约束 |
| `pf_settlements`, `pf_result_edges` | lock FK、唯一 append-only successor、完整结果来源/修订关系；final-only financial projection |
| `pf_migration_proofs` | 原 ledger rows/DDL 摘要、影子映射 bijection、guard signatures、migration head；没有完备证明不开放 writer |

所有新表拒绝 UPDATE/DELETE 与覆盖型 INSERT。与旧表的 FK 必须具体，不用一个泛型
string/hash 字段代替实际关系。PF claim 到 final lock seal 使用 deferred FK，COMMIT
前必须有完整 seal；seal trigger 必须验证所有 member heads/slots/versions、预算、完整
frame census、trusted receipt、math dependency 和 revalidation proof，缺一个也不能发布。

旧 `rb_head_consumptions`、`rb_prediction_slots`、**`rb_prediction_versions`** 的列、FK、
已有行及所有已发布 guards 保留。另加唯一命名的 BEFORE exclusion / AFTER mirror triggers，
使旧 INSERT 必须参与同一 registry。新 PF writer 直接写同一 registry，不能另建独立所有权。
legacy shadow 记录还须反查对应旧行的 exact projection，不能伪造 legacy owner 绕过 typed FK。

## 5. 旧 writer / 新 writer 互斥证明

### 假设与定义

两者访问同一受控 SQLite DB，已原子安装且完整 backfill 的 guards/registry 未被管理员删除，
读写入口验证 schema/head。应用 writer 不能通过修改 schema 获权。成功指完成并提交合法 seal，
不是只有未封存临时 header 或一条失败 receipt。正常连接开启 FK；关键互斥还由 PK 与 triggers
独立保证。SQL 模型分别覆盖 recursive_triggers=OFF/ON。

### Head

令 H(r) 为 `pf_shared_heads[parent_run_id=r]`。旧 LOCK/REPLACE 写旧 head 表之前必须
通过 BEFORE exclusion；其 AFTER mirror 必须插入 H(r)。PF 必须插入同一 H(r)。
所有 H(r) 插入先由 BEFORE trigger 对既有 key 执行 `RAISE(ABORT)`，再由 PK 约束。
SQLite 同一时刻只有一个 write transaction。因此两事务若都成功，H(r) 必须有两个 owner，
与唯一 key 矛盾。无论谁先提交，后者拒绝；先提交的是 REPLACE 也一样，旧 snapshot 不能再锁。

### Prediction slot

令 S(p,m,k) 为 shared slot 主键。旧初始 slot INSERT 同样 BEFORE 检查、AFTER mirror；
PF 对同一主键写入。相同唯一性论证禁止两个 owner family。旧 post-lock version 可以在
**LEGACY family** 上继续原合法 revision，不能在 PORTFOLIO family 上 append 或重新插入
legacy 初始 slot。PF 首版不支持 post-lock replacement，也不能转移 legacy slots。

### SQLite conflict-policy 陷阱

**仅有 AFTER mirror + UNIQUE 不足以证明互斥。** 外层 `INSERT OR IGNORE/REPLACE`
可影响 trigger 内的 conflict policy，造成旧表成功而 mirror 被忽略/覆盖。因此必须同时有
显式 BEFORE `RAISE(ABORT)`，在旧表与 shared registry 两侧防重复，并保留 UPDATE/DELETE
禁令。测试实际覆盖普通 INSERT、OR IGNORE、OR REPLACE，两种 trigger 递归设置，以及
head collision、不同 head 的同 slot collision、PRE_LOCK replacement race。

### 证明证据的边界

SQL 模型用原 ownership seam 的真实列名/主键，但省略其他旧业务 guards，是更弱的旧 writer
对手模型；它仍不能重复占用。它验证上述 exclusion、mirror、版本 family、预算唯一性和
事务回滚论证。它**不**替代实际旧库 migration、真实旧 binary、完整 source/receipt/seal
和真实 Portfolio API 集成测试；这些全部列为 PF-B 发布前硬门禁。

## 6. 原子 Lock、PRE_LOCK race 与发布时钟

```text
BEGIN IMMEDIATE
  从 sealed assembly 解析 exact member 集合与唯一预算授权
  核对每个 run 当前仍 active PREPARING、head 未消费、不是 legacy post-lock successor
  同一 session 重验 qualification/pin/state/authority/rights/撤销/过期/fixture/已知结果
  验证 source snapshots 与原 packet/V4 review/audit/fusion/完整 frames 完全一致
  拒绝重复 run、重复 canonical match/market、任何 shared head/slot/version/budget 冲突
  重建 typed Portfolio numerical projection；用冻结 kernel 核对 plan/optimizer/return
  最后取得可信 publication clock，重新验证所有到期条件及最早 kickoff strict lead
  写全部 claims、唯一 budget claim、lock graph、完整 final seal
COMMIT
```

任何 member/clock/budget/constraint 失败必须 rollback 整个事务；deferred seal FK 使未封存
PF claims 不能 COMMIT。数据库忙、旧 read snapshot 失效等也 fail closed，不借客户端时间重试。
不能先锁一部分 source，再在另一个事务补剩余 source。Snapshot/Assembly 的早期存在不占资源。

PRE_LOCK replacement 先成功：旧 head 已被 REPLACE 消费，旧 snapshot 拒绝；只能从正常
新 head 重新生成 snapshot/assembly。PF 先成功：旧 replacement 的 head INSERT 被 shared
guard 拒绝。价格更新必须走合法 replacement；不能把旧 snapshot 的赔率字段改成新值。

## 7. Cutoff / kickoff / freshness

保留不同时间：原 run/source cutoff；各 capture/available/ingested times；V4 review/audit
接收时间；snapshot seal time；assembly common cutoff；最终 lock publication time。
V4 review 可以晚于原 run prepare，但必须早于 snapshot/assembly 使用它的可信 cutoff。
这不把 model/market 原 cutoff 向未来移动。所有用于组合的信息 receipt ≤ common cutoff。

最终必须 `common_cutoff <= publication_time` 且
**`publication_time + 60 seconds < earliest kickoff of ALL member frames`**。
等号拒绝。最早 kickoff 不只从最终选中票计算，不能丢掉早场未选单位借用晚场 deadline。
Market 和 SP 各按原 policy/freshness/retention/bookmaker/market identity 在准备及发布时复验；
capture time 不当作 publication time，旧 quote 不重标为 fresh。已开赛或已知结果拒绝。

## 8. 预算、数值投影与重复预测

Budget authority 必须为事先明确、人工认可的独占 portfolio 资金划拨；不能复用 standalone
legacy 仍获授权使用的资金。对共享 funding scope 的所有 allocation，repository 必须验证
授权总额与未解除 reservation 总额不超额；caller 不能换 cycle/grant ID 重新铸造资金。
初版采用独占隔离 allocation；如不能证明与既有 legacy 资金授权隔离，预算准入拒绝。
模型中的 grant 表仅证明唯一认领，不证明外部资金来源或人工授权。

Snapshot/Assembly 不占款；Lock 原子认领完整 allocation（stake+未花现金），即使 NO_BET
也消费该次逻辑决策的 authority，避免以新 request 重复下注。结算前现金未花出但不能被另一个
live allocation 重复使用；结算后按 append-only 唯一资金事件释放 cash+payout。

独立 typed `PORTFOLIO_NUMERICAL_SOURCE_V1` 由 verified refs 构造，只投影原概率和合法赔率，
采用明确 portfolio budget 与共同兼容 policy。禁止伪造 RealMultiMarketAnalysis 的单 bucket
身份或删除单-source checks。数值 policy identities 必须一致/经明确兼容约束，预算必须符合
共同 profile 和各来源允许范围；冲突时拒绝，不取更宽松规则。原 Elo/Poisson/fusion/EV/
Strategy/Pass/Return/Decimal/结算函数保持；PF-B 必须证明 adapter 输入/输出重放等价。

认领全部 member prediction frames，不只是有下注的腿。跨不同 program 的相同物理 match/
market 在同一组合中拒绝。跨组合同一 program slot 不能重用；不同 program 的同场观测不被
统计为独立赛事样本。完整 census/eligible-but-unselected/unavailable 原因可审计。

## 9. Invalidation、settlement 与 P&L

Lock 前任一 source/model/rights invalidation 导致整体拒绝。Lock 后追加 invalidation/audit，
保留原 lock、ownership、资金与实际票历史，不自动退款、释放 slots、删除预算或授权重下注。
首版无 PF post-lock replacement；混合已锁 legacy source、跨 run 修订/退款另行定义合约。

使用合法常规时间 result observations，保留 void/unsupported/missing-result 原规则。
`ending_capital = cash + gross_payout`；`P&L = gross_payout - stake`，不再加一次 budget。
未齐或unsupported时 payout/ending/P&L 全为 null。Result revision 追加唯一 successor，
历史结算可重放，当前财务 view 只取有效最后版本；不能把各版 P&L 相加。
预测样本按原 program/match/market 记一次，portfolio 是资金账本，不重复累加 source run 的钱。

## 10. Historical backfill、旧 reader 与 rollback

在协调维护窗口和单一 migration transaction 中：检查旧 sealed graphs/FK/uniqueness；
安装新表/guards；逐一将所有旧 head、initial slot、全部 version 映射到 typed registry；
逐 key/owner/count/hash 双向校验；记录旧 rows/DDL 摘要不变及 migration proof，再提交新 head。
旧事务若先成功，其行必须被 backfill；若 migration 先获得写锁，旧事务必须重新验证或失败。
任何部分 backfill/冲突/未封存历史图都拒绝，不跳过“异常旧行”打开新 writer。

旧二进制通常因未知 migration head 直接 fail closed；互斥证明仍通过旧 SQL 触发器成立，
不依赖应用升级或单一进程锁。新 audit/status reader 必须理解 Portfolio ownership；遇到旧
报告无法表达的新所有权时明确拒绝或使用新报告版本，不能把已消费 run 仍展示为 PREPARING。
既有历史报告按原 schema/watermark 重放，不改旧 JSON、hash 或 source event 时间。

Rollback：失败 migration 事务整体回滚；无 PF-native artifacts/authorities/claims 时可在
验证旧 ledger 不变、shadow 可完全再生后演练 empty downgrade/re-upgrade。有任何 PF-native
snapshot/assembly/budget/lock/claim/settlement 时拒绝 destructive downgrade。历史 backfill
产生的纯 legacy shadows 与新 PF 业务记录分清；不能删除真实资金/预测/授权史以满足空库条件。

## 11. PF-B 验收矩阵与停止点

| Gate | 必须证明 |
|---|---|
| SCHEMA | extra/unknown kinds、变造 seal/ref、caller 时间/概率/金额覆盖拒绝 |
| LEGACY | 实际旧 v1.3 库、旧 writer/新 writer、全部原 guards/rows/hash/watermark兼容 |
| OWNER | 双向 head/slot collision、OR IGNORE/REPLACE、trigger OFF/ON、跨进程 race 只有一胜 |
| REPLACE | PRE_LOCK replacement 与 PF lock 双向竞争；stale snapshot 不能借新赔率锁定 |
| ATOMIC | 第N个member/budget/seal失败、publication超时、process crash，无部分 committed claims |
| BUDGET | 无预算相加、重复grant/cycle/资金allocation拒绝、超授权金额拒绝、NO_BET唯一决策 |
| MODEL | 至少两个独立 qualified competition/state/approval/release/pin/program/run；不共享测试state |
| TICKETS | ≥4 eligible future matches、不同 kickoff buckets，跨联赛 2X1/3X4/4X11完整重放 |
| TIME | 过期 market/SP、已开赛、lead等号、cutoff后证据、撤销/非法pin、已知结果全部拒绝 |
| DEDUP | 相同snapshot/run、跨program相同match/market、跨epoch同slot重复全部拒绝 |
| RESULT | 跨时点结果收齐、missing/void/unsupported/revision，单一 P&L/forecast计数 |
| MIGRATE | 全历史backfill bijection、部分失败回滚、empty roundtrip、populated refusal |
| RELEASE | 完整 regression、Windows/exact CI、installed-wheel、冻结数学及新versioned audit验收 |

本阶段交付 Schema/API/DB约束与可执行互斥模型。上述实际-ledger、真实旧 binary、完整
Portfolio source/预算/时钟/settlement 集成验收仍是 PF-B 的硬前提，**本阶段不进行 PF-B 大规模实现或生产迁移**。
