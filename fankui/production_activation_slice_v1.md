# PROSPECTIVE_ACTIVATION_SLICE_V1

Status: **DESIGN — pending Production Activation Design Review**。
前置activation plan已通过；当前唯一执行授权是**INPUT_PREPARATION**。本设计不授予provider HTTP、真实推理/decision/lock或下注权限。

冻结基线：v1.0.0 / `6d633d4425d5fd6d93918d60526f701a59020b3d`，migration `5b748fa162ed`。

```text
MODE = INPUT_PREPARATION
PRODUCTION DECISION = UNAVAILABLE
REAL PERFORMANCE = INSUFFICIENT_PROSPECTIVE_SAMPLE
```

## 1. 固定slice边界

- Market：**THREE_WAY only**，常规时间主胜/平/客胜；不扩market，不增加单关/串关类型或风险规则。
- **ONE RUN = ONE EXACT KICKOFF_AT_UTC BUCKET**。UTC完整时间戳完全相等才同桶；不四舍五入分钟、不容差合并、不把一天全部比赛组成一个run。
- P_market：**The Odds API current h2h → existing live ingest-market-odds → MARKET_CONSENSUS_MEDIAN_V1**。
- SP：`SPORTTERY_MANUAL_ARCHIVE_V2`，THREE_WAY完整三项报价、正式身份与人工复核。
- Evidence：`MANUAL_VERIFIED_IMPORT_V1`，来源/文件hash/receipt/freshness和typed事实。
- P_quant：只消费合法、当前有效、已pinned且适用的**现有**model release/state。缺少则`MODEL_UNAVAILABLE`；不得临时训练/重新训练/恢复restricted history。
- P_llm：网页GPT通过冻结V4 packet/review和独立correction sidecar协作，不调用LLM API。
- P_final、EV、Strategy、Return Distribution、objective、risk与Settlement继续使用v1.0冻结数学。缺少P_quant不擅自以P_market/GPT值替代，不临时改fusion来开跑。

## 2. Real THREE_WAY source graph（设计，尚未实现decision adapter）

```text
合法fixture / canonical identities / 赛前slate declaration
    └─ exact kickoff bucket + 固定match集合/THREE_WAY scope
The Odds API current h2h capture
    └─ request/response receipt + raw hash + exact event mapping
        └─ existing normalized bookmaker snapshots
            └─ MARKET_CONSENSUS_MEDIAN_V1 + constituent IDs/hashes
                └─ existing P_market derivation
SPORTTERY_MANUAL_ARCHIVE_V2 → reviewed fixed SP + source lineage
pinned existing model release/state → P_quant or MODEL_UNAVAILABLE
MANUAL_VERIFIED_IMPORT_V1 → EvidenceSnapshot / Binding
    └─ [待审 REAL source / epoch / analysis binding]
        └─ new frozen V4 packet → web GPT review + correction sidecar
            └─ unchanged fusion / EV / Strategy / Return
                └─ DecisionLock before exact kickoff
                    └─ ResultObservation → Settlement → full epoch census/report
```

每条reference包含schema、ID、content hash；原始数据与衍生概率、SP、模型state、evidence的身份/market/时间必须逐项绑定。不能把真实数据送入`EXACT_MARKET_FIXTURE_V1`或改写`SYNTHETIC_ACCEPTANCE_DATA`标签取得“真实”资格。

v1.0 `prepare_values`对REAL_PROSPECTIVE明确UNAVAILABLE，原MultiMarketAnalysisV1固定synthetic。该门禁保持不动；真实source graph的版本化契约、分发/replay入口需单独审核后才可实现。单纯shell包装不能激活它。

## 3. The Odds API → P_market lineage

已有真实adapter：`TheOddsApiMarketOddsProvider`（current h2h，LIVE_STRICT）。已有入口：`live ingest-market-odds`。已有共识：`application/market_consensus.py::ConsensusMarketOddsProvider` / `MARKET_CONSENSUS_MEDIAN_V1`。

须复用其normalization、完整三向bookmaker报价、身份reconciliation、去水/共识/存储精度及后续P_market计算；不在glue中重新算均值/中位数、重新去水或把SP当market odds。也不换用多市场fixture consensus替代该路线。

正式绑定至少保留：provider/event/bookmaker身份、请求范围和时间、received/available/ingested、raw artifact ID/hash、normalized snapshot IDs/payload hashes、consensus snapshot和每个constituent ID/hash、policy名、canonical match/THREE_WAY、decision cutoff与已有freshness/coverage检查结果。Provider last_update不等于本地提前收到；unknown历史publication/version不补造。

真实gap准确限定为：**有效credential/data-use条件、当前数据可用性、market source → REAL_PROSPECTIVE decision graph的正式绑定**。当前operator没有联网入口，不读取API key、不自动购买provider；未来网络使用须单独授权。

## 4. SP / P_quant / evidence

SP保留source document/evidence hashes、编号及所属日期、canonical比赛、主客、UTC kickoff、sale_status、全部三项Decimal字符串、capture/entry/review/ingestion谱系。旧快照不能覆盖；实际价格不由网页GPT修改。

P_quant的available分支必须绑定release/state/approval/target适用范围、model/config/training-data hashes及生成/可用时间；其参数与运算冻结。unavailable分支保留具体reason和`MODEL_UNAVAILABLE`，遵守V4的UNAVAILABLE语义。是否最终无可用决策由原规则决定，不制造“可用模型”。Preparation入口一律显示模型资格尚未核验，不能把一条数据库记录计数当作有效pin。

Evidence在新analysis/packet cutoff之前正式接收，FACT/ANALYSIS/SPECULATION及UNKNOWN含义不变。V4 refs使用FootballEvidence IDs，独立sidecar使用snapshot IDs；跨match、晚到或stale引用不得进入当前packet。新事实需要新的analysis/packet/run，见PRE_LOCK_REPLACEMENT_V1草案。

## 5. Exact kickoff grouping

分桶来自赛前审核slate，不读赛果。建议封存`slate_declaration_ref`、UTC kickoff、排序后的canonical match IDs、THREE_WAY、scope hash与bucket ID。没有identity时可先列外部candidate IDs，标记`IDENTITY_UNRESOLVED`，不能进入真实run。

同一桶内每个match只出现一次；同一slate中的相同match不得重复分配。桶不能根据推荐票或赛果重排/合并。一个match时不为方便新增单关。容量超限按原规则明确不可用，不能暗截top-N。

改期不是修改旧桶：新增来源记录，旧声明保留并标记需重规划；禁止锁定旧冲突输入。跨kickoff/成员变更不属于PRE_LOCK_REPLACEMENT的同链修改，须明确的新scope处理；不得借新桶绕过已存在的prediction唯一性。

## 6. Real epoch bootstrap（待审）

旧`prospective epoch`要求sealed V2 seed plan；这个API的存在不证明真实bootstrap已完成。不得用synthetic seed后换标签。

设计一个事前sealed的真实配置锚点，绑定观察program/scope、固定模型release/state和配置/training-data hash、既有market共识policy、fusion/Strategy/Return/objective/risk、预算集合、exact-bucket规则、result source identity及新adapter实现身份。配置来源必须来自已有合法工件，不根据本期赛果选择。

创建时间必须不晚于未来epoch start；窗口、允许数据scope及实现身份事前固定。v1.0实现/工件保持原身份，新接入执行身份独立记录，不冒用旧implementation hash。如何让真实配置锚点进入相应sealed/replay链是下一次架构审核内容，本轮不编写契约类、migration或真实执行器。

缺少有效pin/来源条件时可以保留明确的输入准备/不可用记录，但不能开一个假“模型可用”epoch。跨epoch也不得对同一program内同一match/market重复计入正式prediction；scope admission登记须防止用新epoch绕过限制。

## 7. PREPARING replacement / duplicate防护

采用独立、追加式`PRE_LOCK_REPLACEMENT_V1`（见专门草案）。只有当前未锁定head、同一epoch/program/exact bucket/成员/market、且仍赛前时可以替换。旧PREPARING永久保留，通过事件投影为`REPLACED_PRE_LOCK`；新analysis/packet/run保持完整来源和新可信cutoff。

真正锁定只能使用当前active head。锁与replacement竞争必须序列化；final effective LOCKED版本按canonical match+THREE_WAY唯一计数，替换过的未锁定attempt绝不参与prediction scoring。Epoch census仍包含全部attempt、替换关系、无效/失败/缺失记录；不删除不利case。

## 8. 当前已允许的Preparation Mode

`daily.cmd`只进入INPUT_PREPARATION菜单：今日准备（可含审核fixture身份导入及slate分桶）、导入竞彩SP、导入赛前事实、查看缺项、数据/文件校验、备份。

不包含真实model运行、market HTTP、epoch创建、prospective prepare、production packet export、lock、replacement、settle/report绩效。包中的任意字段不能指定CLI command、shell、DB路径、clock或override。未知参数/类型fail closed。

缺项报告分别列明：已观察到的本地输入与“未核验/未授权”。有SP/evidence或已有market rows不意味着当前完整、fresh、授权有效或可决策。`PRODUCTION_DECISION_ADAPTER_UNAVAILABLE`始终保留。

## 9. Activation acceptance tests（设计，不在本轮执行真实activation）

| ID | 后续必须证明 |
|---|---|
| ACT01 | THREE_WAY闭集；h2h确切normalization及MEDIAN_V1/P_market与冻结goldens相同 |
| ACT02 | credential/data-use缺失/失效不请求provider；current endpoint不伪装历史publication |
| ACT03 | event/match/market及全部bookmaker constituent谱系，partial/mismatch/future输入拒绝 |
| ACT04 | SP精确Decimal/完整三项/主客/编号日期/review谱系；不从fixture adapter伪装真实 |
| ACT05 | 仅合法有效pinned state可AVAILABLE，否则MODEL_UNAVAILABLE且不训练、不复制P_market作P_quant |
| ACT06 | 两个仅相差1秒的kickoff必须不同桶；时区等价时间才同桶；赛后不得重组 |
| ACT07 | real epoch事前bootstrap及implementation identity，无synthetic seed或backdate |
| ACT08 | pre-lock replacement原子性、竞态、完整历史、时钟和重复保护，详见PLR契约 |
| ACT09 | 仅最终有效LOCKED预测计分；所有attempt仍在census，跨slate/bucket/epoch不可重复增样本 |
| ACT10 | V4/sidecar严格绑定、未知事实/模型不可用与晚回传处理；原数学与风险不变 |
| ACT11 | input-only入口拒绝所有decision/force/skip/backdate路径，错误不能伪装NO_BET |
| ACT12 | 一致性备份、崩溃后提交不确定时停止、隔离恢复核验、retention和restricted-root保护 |

本轮只以合成材料验证Preparation入口；不取得真实provider响应、运行真实模型或生成真实prediction evidence。完成后停止，等待Production Activation Design Review。
