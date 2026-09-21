# PRE_LOCK_REPLACEMENT_V1 — contract / state transition

**Accepted — Bridge Review APPROVED / Production Activation Implementation Review APPROVED。无 implementation blocker。**

发布身份：**1.1.0 — Production Activation software release**；不是新预测算法版本。

- Approved bridge design SHA：`8b1cdf727c14510e590450406d89a9edae7ff759`。
- Approved implementation SHA：`b2544464055b0fda954b1fbed299f5d749dae6a0`；tree `6942954d9686b75543749fa4c918aec81bddf49c`；candidate CI #41 SUCCESS。
- Migration head：`6c859ab273fe`；v1.0.0 tag及旧pv语义继续冻结。
- 正式接受与发布门禁见 [最终验收报告](production_activation_v1_final_acceptance_report.md)。

以下技术正文原样保留。“拟议 / 只设计 / 等待Review”等为原设计阶段状态，由本接受记录取代；状态转换、时间门禁、唯一性与重放语义不变。发布后仍为INPUT_PREPARATION，真实观测数为0。

## 1. 语义与对象

当一个当前active `PREPARING` attempt已有packet、但尚无DecisionLock时，新赛前事实可以触发**新的analysis、packet、run**。旧对象全部永久保留，新增replacement事件将旧attempt投影为`REPLACED_PRE_LOCK`（拟议状态），不是UPDATE旧run/packet/evidence。

新的EvidenceSnapshot与SP/market快照只追加；未变化的合法来源ref可以复用。不得拿新事实配旧cutoff；旧review不能自动继承到新packet，必须按新context重新回传或明确走原有abstention路径。

### 拟议request（不允许用户提供事件时间）

- schema/version、request_key。
- observation_program/epoch ref。
- bucket declaration ref、exact `kickoff_at_utc`、canonical match集合/THREE_WAY scope hash。
- expected current preparing-head ref（ID/hash/schema），compare-and-swap前置条件。
- 新合法输入/source snapshot refs与完整input hash、reason及operator声明。
- 不允许`replaced_at`、`created_at`、`ingested_at`、`locked_at`等override；不允许概率、SP、预算、策略/风险参数的就地覆盖。

### 拟议sealed event

`PRE_LOCK_REPLACEMENT_V1`包含event ID/hash、request identity/hash、epoch/program/bucket refs、旧run/analysis/packet refs、新run/analysis/packet refs、新输入/证据refs、reason、可信recorded_at/clock basis/receipt sequence、implementation/configuration hashes。

Event ID按canonical content计算；所有refs用typed FK指向已封存对象。旧run/newrun不得相同；同一old head最多一个replacement successor，一个newrun只属于一个pre-lock链。事件与新run可用性发布原子完成，未完成的候选source工件不能单独变成active head。

## 2. 不变量

1. 只允许**同一program、epoch、exact kickoff bucket、match集合、market scope及冻结配置**内重准备。不是用来换比赛、换epoch、移到新日期或重组赛后组合。
2. 旧head必须PREPARING、未被replacement/lock/其他terminal事件消费；查询当前head而非只检查旧run原始status字段。
3. 事务提交时新旧cutoff与所有来源时序有效，新旧相关kickoff均满足原有赛前lead；采用冻结的60秒严格不等式，不为replacement缩短。
4. Replacement时没有任何lock写入。禁止先制造NO_BET/fake lock再用已有post-lock supersession补救。
5. 旧事实不改；新事实必须先正式接收、验证，再进入新analysis/packet/cutoff。无法取得合法source/model时，不发布一个虚假ready successor。
6. 不允许时钟回退、合成clock用于真实资格或赛后replacement。失败不能靠backdate、新slate-key或数据库回滚重试成功。
7. Exact retry返回原event/newrun；同一key不同内容拒绝。重复请求不能生成多份新prediction。

## 3. 状态转换（投影，旧行不变）

| 当前有效状态 | 操作与条件 | 新投影 | 计分资格 |
|---|---|---|---|
| PREPARING active head | 校验通过且仍赛前，原子replacement | old→REPLACED_PRE_LOCK；new→PREPARING active head | 两者目前都不计分 |
| PREPARING active head | 正式lock提交并通过原有门禁 | 当前head→LOCKED | 仅该有效LOCKED版本可作为正式prediction |
| REPLACED_PRE_LOCK | lock或再次以它作为head替换 | 拒绝STALE_PREPARING_HEAD | 永不计分 |
| LOCKED | PRE_LOCK_REPLACEMENT | 拒绝；不得把lock降回PREPARING | 按既有锁语义保留 |
| LOCKED | 真正的赛前错误更正 | 使用已有的atomic post-lock invalidation/replacement路线，保持其门禁 | 旧INVALIDATED版本不计，新有效LOCKED版本计一次 |
| PREPARING且kickoff/lead已到 | lock/replacement | 拒绝LOOKAHEAD_RISK；未锁定attempt不可用 | 不计分 |
| SETTLED / INVALIDATED / 不可用或epoch关闭 | PRE_LOCK_REPLACEMENT | 拒绝，不反向转换 | 不能复活旧prediction |

正常例子：`R0(PREPARING) → [E1] R1(PREPARING) → [E2] R2(PREPARING) → LOCK(R2)`。
Census中R0、R1为REPLACED_PRE_LOCK，R2最终LOCKED/SETTLED；一个match-market只有一次正式预测，不能把三份attempt算三次。

## 4. 事务、并发、文件失败

设计沿用数据库串行写边界：BEGIN IMMEDIATE → 核验request retry → 校验current head、epoch/config/bucket/source/已知结果 → 获取真实cutoff并形成新工件 → 再查clock/deadline → 原子seal新run/packet绑定与replacement event → commit。

必须以同一一致性边界同时判断**head有效性和lock存在性**。两个replacement竞争同一head只能一个成功；另一个为STALE_PREPARING_HEAD。Lock与replacement竞争：先commit的操作消费head，后者必须重读并拒绝冲突；不存在“旧已锁、新又替换”的双成功。

可在事务前准备/校验外部source文件，但这些不是active run。中途失败回滚replacement publication；旧head不被半途失效。已经独立封存的source事实可保留，但需标为未形成新active attempt，不能计分。

Packet文件在数据库commit后按sealed bytes导出。导出失败不撤销成功事务，按原receipt重取同一packet；不得修改DB时间或生成另一份假新run。原始review、错误attempt、crash记录均保留。

## 5. Persistence / replay（拟议，不含migration实现）

- 采用新增版本化event/typed关系，不ALTER/UPDATE历史v1.0 run、packet、lock行。
- `previous_head_id`和`new_run_id`各自唯一；event具有完整receipt及deferred completeness约束，禁止半图提交、循环、分叉、跨epoch/bucket引用。
- Current head、REPLACED_PRE_LOCK是事件序列投影，不依靠一份可改JSON文件声称已替换。
- 独立load/audit必须重放整个replacement链、新旧来源与时间门禁；一致重封但错误source/head/time/metric也应拒绝。
- 新真实接入与此event实现需要独立implementation身份和未来epoch记录，不沿用v1.0旧hash掩盖新执行实现。

## 6. Duplicate prediction / epoch census

最小prediction身份为`observation_program + canonical match_id + THREE_WAY`，一次正式观察不能因不同slate/bucket/attempt/epoch别名重复获得资格。程序/epoch的预先登记范围用于限定cohort，不能看结果后换program重跑并合并成绩。

数据库层保护有效head的唯一消费及同scope的prediction唯一性；不能只靠CLI内存集合。仅最后**有效且合格的LOCKED**版本参与正式probability/decision evaluation；有赛前post-lock invalidation时按原规则排除旧lock。

报告分开列出`attempt_count`、pre-lock replacements、未锁定/失败/不可用记录与`unique_locked_prediction_count`；这是拟议coverage，不改变Brier/log-loss、calibration、资金或Return计算。

完整epoch census包括全部attempt及replacement事件，按as-of和receipt watermark重放。后来追加的replacement不能改变已封存旧报告。未锁定或REPLACED_PRE_LOCK不当输、不填零收益，也不从审计中删除。容量限制触发时明确停止，不能删attempt释放“样本容量”。

## 7. Kickoff变化与scope变化

本契约只允许同一exact kickoff的同链新事实。任何kickoff/成员/market/配置改变均拒绝本操作并保留新来源冲突记录；不自动搬桶。后续真实scope重规划需独立、可审计的声明及唯一性检查，不能规避已有lock或将已开赛比赛重新当未开赛。

## 8. Architecture acceptance tests（只设计）

| ID | 必须验证 |
|---|---|
| PLR01 | 单次及连续多次replacement：旧对象bytes不变，新analysis/packet/run均新ID，链完整 |
| PLR02 | 提交中断无半replacement；文件导出失败可从receipt恢复且无重复新head |
| PLR03 | 相同请求exact retry，key冲突拒绝，旧head不能重复/分叉/循环使用 |
| PLR04 | replacement/replacement及replacement/lock竞态只有一个合法head消费 |
| PLR05 | LOCKED/SETTLED/invalidated、跨epoch/配置/bucket/成员、未来事实/已知结果均拒绝 |
| PLR06 | kickoff前lead边界、提交跨界、clock回退/override/赛后请求全部fail closed |
| PLR07 | 旧review不能绑定新packet；事实ref不可跨match、publication/receipt不可回填 |
| PLR08 | 三个attempt只产生一个正式prediction；全部attempt进入census、old report可重放 |
| PLR09 | 跨slate/bucket/epoch重复声明仍不能重复计分；不把未锁定attempt当输 |
| PLR10 | typed FK、seal/receipt缺项、篡改与重封错误链检测；旧v1.0库/行为/数学保持 |

当前Preparation Mode不调用也不模拟这个状态机，不生成PREPARING/DecisionLock或本契约事件。等待Architecture Review后才讨论实现。
