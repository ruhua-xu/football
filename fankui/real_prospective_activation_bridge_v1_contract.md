# REAL_PROSPECTIVE_ACTIVATION_BRIDGE_V1 — unified contract

**Accepted — Bridge Review APPROVED / Production Activation Implementation Review APPROVED。无 implementation blocker。**

发布身份：**1.1.0 — Production Activation software release**；不是新预测算法版本。

- Approved bridge design SHA：`8b1cdf727c14510e590450406d89a9edae7ff759`。
- Approved implementation SHA：`b2544464055b0fda954b1fbed299f5d749dae6a0`；tree `6942954d9686b75543749fa4c918aec81bddf49c`；candidate CI #41 SUCCESS。
- Release migration head：`6c859ab273fe`。默认 `INPUT_PREPARATION`；真实观测数为0，`INSUFFICIENT_PROSPECTIVE_SAMPLE`。
- 正式接受及最终发布门禁见 [最终验收报告](production_activation_v1_final_acceptance_report.md)。

以下技术正文按获批设计原样保留。其中设计阶段的“拟议 / 本轮不实现 / 等待Review / 不创建1.1”等状态表述及旧基线head，按本接受记录和上述release identity解释；不构成新的实现限制或新的数学版本，也不授予真实activation。

## 0. 基线、范围和不可突破的边界

| 项目 | 固定值 |
|---|---|
| BASE_VERSION / BASE_SHA | v1.0.0 / `6d633d4425d5fd6d93918d60526f701a59020b3d` |
| 现有migration head | `5b748fa162ed`；本轮不新增revision |
| 当前运行模式 | INPUT_PREPARATION |
| Production decision adapter | UNAVAILABLE |
| Real performance | INSUFFICIENT_PROSPECTIVE_SAMPLE |
| REAL_PROVIDER_HTTP / LLM_API_HTTP | **0 / 0** |
| 新软件版本 / 模型训练 / 自动下注 | 不创建1.1，不bump version，不训练，不下注 |

三个冻结事实原样保留：

1. `MultiMarketAnalysisV1.classification == SYNTHETIC_ACCEPTANCE_DATA`。不改literal、不把真实源装进该类、不重标旧synthetic工件。
2. V1 `domain/services/prospective.py::prepare_values`对REAL_PROSPECTIVE继续返回`PRODUCTION_DECISION_ADAPTER_UNAVAILABLE`。新路径不调用它来“解锁”，也不改其成功路径分类。
3. V1 `SqlAlchemyProspectiveRepository.create_epoch()`继续要求`STRATEGY_PASS_PLAN_V2` seed并从其推导configuration。新真实epoch完全走新bootstrap；不造synthetic/match-specific比赛或票据plan来满足旧接口。

本合同所有新名称均为**拟议版本化契约**，不是已经存在的CLI/schema。v1.0代码、既有artifact bytes/验证语义、正常V1 synthetic路径均不改变。两张图是逻辑schema与状态约束的辅助视图，完整字段/FK以正文为准：

- [Schema / binding graph — SVG](diagrams/real_bridge_v1_schema.svg) · [PNG](diagrams/real_bridge_v1_schema.png)
- [Head consumption / state machine — SVG](diagrams/real_bridge_v1_state.svg) · [PNG](diagrams/real_bridge_v1_state.png)

## 1. 明确选择的契约族

不同时维护多个可选名称；本设计选择以下独立real族，保留旧V1类原义：

| 新契约 | 职责 |
|---|---|
| REAL_OBSERVATION_PROGRAM_V1 | 事前登记观察program、scope、评估单位、跨epoch唯一性域 |
| REAL_BRIDGE_IMPLEMENTATION_IDENTITY_V1 | 新binding/validator/replay代码身份与冻结数学依赖身份 |
| REAL_POLICY_VALUE_PIN_V1 | 对原非artifact配置值的独立sealed身份；不替换原policy artifact/hash |
| REAL_SOURCE_ADMISSION_FACT_V1 | 当次来源/数据使用/retention/credential可用性准入事实，无secret |
| REAL_MODEL_PIN_V1 | 已有合法pinned release/state/config/training-data与许可/适用范围 |
| REAL_EPOCH_CONFIGURATION_ANCHOR_V1 | 与具体比赛/票据无关的事前sealed配置锚点 |
| REAL_VALIDATION_EPOCH_V1 | 引用anchor而非StrategyPass seed的新真实epoch |
| REAL_SLATE_DECLARATION_V1 | 从经审核准备材料形成的事前完整cohort/fixture声明，不是operator可改JSON索引 |
| REAL_KICKOFF_BUCKET_V1 | 赛前scope声明中的一个精确UTC kickoff及固定canonical match集合 |
| REAL_MARKET_SOURCE_BINDING_V1 / REAL_SP_SOURCE_BINDING_V1 | 包装并严格引用已有live市场共识/SP事实，提供只读数值视图 |
| REAL_MODEL_AVAILABILITY_FACT_V1 | 对当前unit/cutoff的pin/state合法性、适用性及P_quant可用性 |
| REAL_ANALYSIS_UNIT_V1 / REAL_MULTI_MARKET_ANALYSIS_V1 | 真实三向分析及全部输入/模型/配置谱系 |
| REAL_PROSPECTIVE_PREPARE_V1 | 新request/operation契约，不是V1 prepare的别名 |
| REAL_PROSPECTIVE_RUN_V1 | 绑定real epoch/anchor/bucket/analysis/V4 packet的准备快照 |
| PRE_LOCK_REPLACEMENT_V1 | 对real PREPARING head的追加式、原子替换事件 |
| REAL_STRATEGY_SOURCE_V1 / REAL_STRATEGY_PLAN_V1 / REAL_CALCULATION_BINDING_V1 | 新真实类型/引用封装，复用原计算函数，不改变数值规则 |
| DECISION_LOCK_V2 | 新真实run及计算图的锁定，沿用V1 money/hash/time语义 |
| REAL_PRE_KICKOFF_INVALIDATION_V1 | 真正已锁定版本的赛前原子替代关系；不同于pre-lock replacement |
| REAL_PROSPECTIVE_SETTLEMENT_V1 / REAL_VALIDATION_REPORT_V1 | 新lock/run/epoch及完整attempt census的引用封装，数值计算不变 |
| REAL_EPOCH_CLOSE_V1 | 追加关闭记录，固定截止、census hash与实现/配置身份 |

`ANALYSIS_PACKET_V4`、`LLM_REVIEW_V4`及其JSON字段/绝对P_llm语义不变。必要的新引用绑定放在新real repository/sidecar，不增加网页V4字段。

### 通用封存与reference规则

- 新顶层artifact均有schema_version、artifact_id、content_hash、canonical JSON和完整依赖清单。Hash覆盖分类、所有引用、已知/未知状态、配置/实现、cutoff和真实receipt；ID派生自schema命名空间及hash。
- `ref = (artifact_id, schema_version, content_hash)`三者必须匹配；旧normalized rows不具此封装时，用**带明确表/复合键的typed source ref**加原payload hash，不捏造旧schema/ID。
- 所有真实工件显式`data_classification=REAL_SOURCE_DATA`；这只是必要条件。真实资格还须通过来源、模型、事件时钟、epoch、lock与完整重放；有字符串标签不构成资格。
- 数值沿用原Decimal/整数fen、catalog、排序/精度/规模限制，不接受浮点JSON、重复key、partial normalized distribution、未知字段或任意schema cast。
- clock由repository提供，真实事件仅`LOCAL_SYSTEM_UTC`。用户不能传事件时间；source publication/availability可以是经核验的事实字段，不能替代本地receipt。
- 全部API按闭集schema显式分发；`model_construct`、先造V1 synthetic对象再改字段、宽化V1 validator、直接写旧表均禁止。

## A. Real analysis contract

### A1. REAL_MULTI_MARKET_ANALYSIS_V1

| 字段 | 必须满足 |
|---|---|
| data_classification | Literal REAL_SOURCE_DATA，无synthetic分支 |
| program / real_epoch / anchor refs | 指向本合同新类型且配置/实现相同 |
| exact_kickoff_bucket_ref | 已封存赛前bucket；不是随时可改的日分组文件 |
| decision_cutoff / recorded_at | 新repository可信时间；不从导入analysis JSON回填 |
| units | canonical顺序、唯一match+THREE_WAY，完整等于bucket成员；不按收益/票据删unit |
| configuration_hash / implementation_ref | 与anchor/epoch完全一致 |
| rules / constraints / min_selection_ev / min_ticket_roi / allowed_budgets | anchor值的只读投影；不能每次analysis选择新参数 |
| input_manifest / full_dependency_hash | 包含所有source refs、真实admission refs、model/evidence/status/hash/时序 |
| analysis_status / reasons | READY或UNAVAILABLE；后者保留确切原因及缺失层，不伪造空概率 |

### A2. 每个REAL_ANALYSIS_UNIT_V1

必须绑定以下内容，不能只存一组概率：

1. **canonical match identity**：match/competition/season/home/away IDs、精确`kickoff_at_utc`及被引用的fixture observation；主客、market与bucket一致。
2. **THREE_WAY MarketKey**：沿用旧枚举、完整HOME_WIN/DRAW/AWAY_WIN顺序；无handicap、其他market或跨market compound。
3. **decision cutoff**：与本次real prepare/run一致。所有用于decision的事实与local receipt均不晚于cutoff；对source-only publication未知如实保持未知。
4. **market binding**：The Odds API current h2h源ingestion、request/response/raw hash、canonical mapping、normalized snapshots、MEDIAN_V1 consensus及全部bookmaker constituent IDs/payload hashes。
5. **SP binding**：`SPORTTERY_MANUAL_ARCHIVE_V2`、snapshot/ingestion/source document/evidence refs及hash、reviewer/review、sale status、完整三项准确价格及已知时间。
6. **evidence refs/uses**：EvidenceSnapshot、ProspectiveEvidenceBinding/FootballEvidence及其hash，分类、freshness、source/receipt与当前unit身份。V4引用FootballEvidence IDs，correction sidecar引用snapshot IDs。
7. **model lineage**：anchor的REAL_MODEL_PIN ref、已有model release/state/approval/target适用证据，以及当前availability fact；model name/version、config hash、training-data hash、state hash、training cutoff、generated/available/ingested时间。
8. **概率层**：P_market及distribution hash；P_quant或明确MODEL_UNAVAILABLE；P_base及其hash或null/原因；base-policy身份/hash与使用的固定参数。未知hash使用明确null/不可用原因，不用零hash占位冒充已知来源。
9. **model/data/config/implementation hashes**：与原model state、source snapshots及anchor分别一致，不用一个任意“整体SHA”代替具体谱系。

新unit只读视图可提供冻结计算函数需要的属性名（例如`identity / market_key / consensus / sporttery / p_quant / p_base / model_lineage / evidence`）。它仍是新的真实类型，自己的验证器必须重建并验证该视图；不是伪造`MarketAnalysisUnitV1`或`LegacyThreeWayInputV1`。

### A3. Market / P_market完整重放

唯一初始路线：**TheOddsApiMarketOddsProvider current h2h → existing live ingest-market-odds → MARKET_CONSENSUS_MEDIAN_V1 → 原P_market derivation**。

来源链必须能够复核：原`live_source_ingestions.capture_hash`与request/receipt，`live_source_ingestion_artifacts`的raw hash，exact provider event mapping，SOURCE/CONSENSUS membership，`live_market_consensus_lineages`、每个`live_market_consensus_constituents`及对应`market_odds_snapshots`/quotes。

- Constituents必须是原consensus所用的完整集合，数量、provider/bookmaker、顺序/ID/hash均一致；不能手选有利bookmaker或删掉冲突报价。
- 去水、component median、归一化、fair-odds存储精度及后续P_market计算完全调用v1.0原逻辑；不能直接从SP算概率，也不能拿fixture/V2 consensus替代MEDIAN_V1。
- 原quote/timestamp/scope不变；The Odds API last_update不是本地提前收到。Actual receipt、source available、ingested与prepare cutoff分别绑定。
- Replay从原sealed normalized事实与完整membership重新调用同一冻结函数验证数值；仅校验最后的概率hash不足以通过。
- Retention不允许保留或重读的raw不能被恢复。仍需可合法保留、足够独立重放的normalized事实/权利与hash证据；无法提供必需的重放证据则SOURCE_REPLAY_UNAVAILABLE，不能声称full audit pass。

MEDIAN policy在anchor中有固定名称和可重验hash：拟议`SHA256(domain-tag || canonical(policy name, v1.0 release/tree, 原函数路径及LF source hashes, 原固定参数/精度定义))`。它是旧policy的**身份封装**，不引入新公式/权重。须绑定`application/market_consensus.py`、原probability函数及normalizer依赖；实际值在后续软件验收中计算，不能填写伪造hash或把这个新pin hash当作旧artifact hash。

### A4. SP完整重放

REAL_SP_SOURCE_BINDING_V1引用旧live SP ingestion membership、Sporttery snapshot及quotes、manual provenance、原document/evidence hashes、编号日期、主客/market/UTC kickoff、review level/人员与时间。

新view只将合法旧THREE_WAY quote映射到原MarketPrice/Outcome catalog，不重新估价、重排主客或改变计奖/Decimal规则；与旧记录逐项比对。缺项、身份/market不符、错误hash或晚到不能成为合法输入。sale status原样传递，CLOSED/UNKNOWN按原eligibility/NO_BET规则处理，不强改OPEN，也不把正常NO_BET误写成模型不可用。已导入行存在不等于当前fresh/可用。

### A5. P_quant / P_base unavailable选择

**本合同选择：首次real epoch激活时必须有合法有效model pin；缺失/无效pin拒绝创建可运行real epoch。** 可追加无secret的拒绝/admission诊断，但不造state、seed plan或可运行epoch。

已合法创建epoch后，unit层仍可能出现MODEL_UNAVAILABLE：target不适用、state/数据权限过期或撤销、不可读/不可验证，或冻结模型返回明确不可用。Analysis保留anchor中已知的pin/state谱系及当前availability reason，`P_quant=null`；不得把P_market复制为P_quant，不把缺失state包装为有效state。

该不可用记录只引用仍可合法保留的pin元数据/准入事实，不为说明失败而读取过期state或restricted history。若连必要元数据/source graph也无法合法验证，只能拒绝请求，不产生伪造的完整analysis。

P_base只由anchor指定的**既有**三向base计算政策得到。若冻结规则不能形成合法P_base/P_final：保存可验证的UNAVAILABLE analysis/run；`P_base/P_final=null`，不生成可锁定plan，不修改fusion，不把UNAVAILABLE伪装NO_BET。

首个slice采用整桶门禁：任一unit缺必需合法概率层则整个bucket run UNAVAILABLE，全部成员保留；不暗删该场拼出另一桶。若连合法epoch/identity/必需source refs都不存在，则请求拒绝，不制造引用空壳的run。

只有既有state的合法读取/获准推理才进入available分支。本轮和未来bootstrap都不允许为启动而新增训练。若现有API不能提供要求的已pinned state使用语义，就保留MODEL_UNAVAILABLE，不能把临时fit称为“适配”。

## B. Real epoch configuration anchor / bootstrap

### B1. REAL_EPOCH_CONFIGURATION_ANCHOR_V1字段

| 字段组 | 内容 |
|---|---|
| program/scope | observation_program_id/ref；THREE_WAY；竞赛/赛季与事前cohort规则；scope hash；正式prediction单位 |
| epoch window | planned_start_at_utc、planned_end_at_utc；不可按本epoch成绩重写 |
| timestamps | created_at_utc、sealed_at_utc、LOCAL_SYSTEM_UTC、receipt/sequence |
| implementation | REAL_BRIDGE_IMPLEMENTATION_IDENTITY ref/hash；冻结v1.0基线、函数依赖与schema/replay版本 |
| provider/source policies | The Odds API current h2h、manual SP、manual evidence、source admission/retention policies的身份/hash；无credential secret |
| market consensus | MARKET_CONSENSUS_MEDIAN_V1名称、身份hash、原始数学/normalizer依赖hash |
| model pins | 有效既有release/state refs、approval/rights/target-scope policy refs；model/version/config/training-data/state hashes和时间 |
| probability/fusion | 既有base policy及固定参数；冻结fusion policy；绝不根据observations更新 |
| Strategy / Sporttery | 原StrategyProfile、SportteryRules及hash；允许pass types、catalog生成政策身份，不含具体票据 |
| risk / thresholds | 原PortfolioConstraints、min EV、min ticket ROI及hash |
| budgets | 事前允许的整数fen集合；run从中选，lock以后不可变 |
| Return | ReturnDistribution policy ref/hash、ReturnObjective profile ref/hash；原weights/guards不变 |
| bucket / result | EXACT_KICKOFF_BUCKET_V1身份/hash；固定result source identity/准入政策 |
| evidence / validation | 冻结evidence freshness/unknown policy、metric/coverage规则及原规模bounds的ref/hash |
| frozen math hashes | 本合同§10列出的旧math/code/policy identities及完整v1.0源码身份 |
| configuration_hash | 对全部配置值和refs的canonical hash；不含secret或可变“当前状态”字段 |

Anchor**不包含**某一比赛、slate、报价、prediction、review、StrategyPassPlan、ticket或本epoch赛果；不能以比赛plan作隐式配置来源。范围可以是事前定义的competition/season/cohort，但不能把待评估结果嵌入配置。

配置来自已接受的版本化policy值、合法model pin和单独核验的运算身份。默认Return policies可以从既有正式配置构造/核验独立常量工件，**不需要先跑synthetic epoch/optimizer**。新`rb_policy_values`可以保存原独立policy artifact的精确schema/ID/bytes/hash；原非artifact value object则用新的REAL_POLICY_VALUE_PIN_V1封装其原value schema/hash。引用角色检查固定，不能把pin外壳hash说成原Return policy hash，也不通过V1 seed副作用创建配置。

### B2. 时间与capability分离

要求：`created_at_utc <= sealed_at_utc < planned_start_at_utc < planned_end_at_utc`。即满足用户要求的`created_at <= epoch start`，并额外明确seal必须严格早于start。created/sealed/receipt由repository产生；start/end可以在request中事前声明，不能追溯。

Model pin的training cutoff、state生成/可用/接收时间必须先于anchor seal，且具有后续target的合法适用证据；不引用未来数据/赛果。Pin当前缺失或无效时记录MODEL_UNAVAILABLE并拒绝可运行epoch。

Credential是否可用、有效期、当前许可/retention和scope核验是**单独、追加式 REAL_SOURCE_ADMISSION_FACT_V1**。Anchor只固定准入政策身份，不写API key、Authorization header、cookie，也不把永远AVAILABLE写入配置。Admission保存无secret的核验人/实际时间、可核验许可ref/hash、用途/scope、effective/expiry、结果/reason及predecessor。

Source请求、prepare、lock都重新检查当时适用的admission/head：expiry/revocation禁止新的业务使用，不修改过去真实发生的receipt；历史报告按原as-of/sequence解释，不假装许可从未失效或自动延长。

### B3. REAL_VALIDATION_EPOCH_V1

新epoch字段：artifact身份、program ref、anchor ref/config hash、implementation ref/hash、mode=REAL_PROSPECTIVE、data_classification=REAL_SOURCE_DATA、starts/ends、created_at/receipt、clock_basis=LOCAL_SYSTEM_UTC、previous_real_epoch ref（可空）、open状态。

- 直接从已sealed anchor确定configuration，不接收`seed_plan_id`，不读取`STRATEGY_PASS_PLAN_V2`来bootstrap。
- `anchor.sealed_at <= epoch.created_at < starts`，窗口必须等于anchor；同program窗口不重叠。
- 非首个epoch要求previous epoch有完整close seal，close receipt先于新epoch创建；starts不早于previous.ends。同一program只允许一个首epoch及单一、无分叉的predecessor链。
- Open/close均追加事件，不update旧epoch；exact retry、完整型别/FK/receipt校验和source/config replay必须通过。
- 新epoch不使旧match获得第二次正式prediction资格，仍受program级slot唯一性约束。

## C. Real prepare / run binding

### C1. REAL_KICKOFF_BUCKET_V1

Bucket来自**赛前sealed slate declaration**，字段含program/epoch、declared_at/receipt、`kickoff_at_utc`、canonical match集合（排序唯一）、THREE_WAY、fixture observation refs、scope/member-set hash和bucket ID。

一个run的全部unit必须与桶中match集合一一对应，且`unit.kickoff == bucket.kickoff`精确相等；UTC规范化后按完整精度比较，不按日期/小时/容差合桶。只有一个match也不新增单关或改Strategy规则。

不同kickoff、成员/market变更不属于同链replacement；旧声明不改，记录冲突并停止原路线。后续重新声明必须通过相同program级重复预测防护；不能借改期、另一个slate或epoch规避slot。

### C2. REAL_PROSPECTIVE_PREPARE_V1请求

允许字段：request_key、real_epoch_ref、anchor_ref、bucket_ref、source manifest refs（market/SP/model/evidence/admission）、allowed budget选择，以及初始prepare或受控replacement operation身份。

禁止传入`prepared_at / decision_cutoff / receipt event time / clock basis / probabilities / configuration override`。不得传入任意外部自称REAL的sealed analysis作为受信来源；外部文件是来源声明，正式analysis由新repository核验生成。

执行顺序：

1. BEGIN IMMEDIATE，校验原request/hash是否已有回执；exact retry优先返回原工件，不重新读取过期raw或重新分配cutoff。
2. 校验新real epoch/anchor/implementation、active窗口、bucket/canonical scope、source types与admission；检查已知结果和kickoff变化。
3. 从repository LOCAL_SYSTEM_UTC取得本次cutoff及receipt序列边界；只使用同时在source availability及真实local ingestion边界内可见的完整输入。
4. 对既有normalized sources/state进行合法重放/读取，调用冻结数值路径，构造新的REAL_ANALYSIS_UNIT/MULTI_MARKET_ANALYSIS；不调用V1 prepare_values，不做训练。
5. 若可形成合法P_base，则按§E的严格V4投影生成新packet/context refs；否则形成明确UNAVAILABLE run（packet可空；模型未知且无法提供合法V4 lineage时不得造占位state）。
6. 校验生成图、完整dependency hash、时间及规模上限；提交前再次检查真实时钟与固定lead。任何越界拒绝整次publication，不自动裁剪成员/重调规则。
7. 原子seal analysis、packet绑定、run与receipt。文件JSON/MD在commit后从已封存字节导出；导出失败不重造run。

`prepared_at_utc == decision_cutoff == 本次可信事件时间`；另有source publication/capture时间，不把它们混成一列。长计算若超过现有时钟/提交窗口则拒绝，不能回填早一点的时间完成锁定。

### C3. REAL_PROSPECTIVE_RUN_V1字段

- real epoch、activation anchor、program、bucket refs及完整canonical match集合。
- real analysis ref、V4 packet ref（ready时必有；unavailable时显式nullable）、evidence uses及全部source/admission refs。
- budget、trusted decision_cutoff/prepared_at/receipt sequence、LOCAL_SYSTEM_UTC、REAL_SOURCE_DATA。
- implementation/config hashes、完整dependency hash；包含status/reason、packet、all layers/source refs和predecessor操作，不只hash最后P_final。
- immutable preparation状态为PREPARING或UNAVAILABLE；current状态由events投影。PREPARING不是已锁定/正式prediction，UNAVAILABLE不是NO_BET。
- replacement chain/root refs及kind（INITIAL或合法POST_LOCK_REVISION）；普通调用者不能通过改chain ID另起一份正式prediction。

生产guard仍为`locked_at + 60s < exact kickoff`，且适用epoch active窗口。新run不改变任何旧V1 run语义。真实输入资格不成立时，旧`PRODUCTION_DECISION_ADAPTER_UNAVAILABLE`也绝不能被菜单绿灯盖住。

## D. 已批准的 PRE_LOCK_REPLACEMENT_V1 接入

### D1. 作用对象与转换

该事件的typed old/new run refs都指向**REAL_PROSPECTIVE_RUN_V1**，不能借它修改旧V1 synthetic run。

```text
active PREPARING R0
  -> 新真实来源/新cutoff
  -> new REAL analysis A1 + new V4 packet P1 + new real run R1
  -> 同一事务 seal R1 与 PRE_LOCK_REPLACEMENT_V1
  -> R0投影REPLACED_PRE_LOCK，R1成为唯一active PREPARING head
```

旧run/analysis/packet/evidence/review全部保留。新packet不能继承旧review/context hash；需新V4回传或原有明确abstention语义。新事实若不能形成合法ready successor，则replacement请求拒绝、旧head不被半途消费；保留失败/admission原因，不能强行锁定旧的已知失效输入。

条件：同program、epoch、anchor/config、exact bucket、match集合/THREE_WAY；expected_head必须是当前未消费、未锁定的PREPARING head；新旧均满足赛前lead与无已知赛果。禁止假lock、原地改packet、删失败attempt、回填时间或赛后触发。

### D2. Head消费与race

`rb_head_consumptions`以`parent_run_id`为PK，action闭集PRE_LOCK_REPLACE或LOCK：

- REPLACE分支必须同时有new_run_id及replacement_event_id、lock_id为空；new_run及event有deferred typed FK。
- LOCK分支必须有lock_id、new_run_id为空；同一head不可能再被replace。
- SQL触发器/应用重放共同核验parent是active、同chain、仍赛前，引用schema/hash与sealed payload一致。新run_id UNIQUE，链position唯一且无环。
- Replacement/lock均持BEGIN IMMEDIATE，在**同一事务**检查并消费head；第一个commit胜出，后一个重读后拒绝STALE_HEAD/ALREADY_LOCKED。不能在CLI内先查“没锁”再分事务写。
- Exact retry只认原request内容/输出；因race输掉的请求不能自动改expected_head重新递交。

### D3. 与真正post-lock supersession区别

已LOCKED的错误只能使用冻结语义的赛前invalidation+replacement lock，而不是PRE_LOCK_REPLACEMENT。新真实引用需要`REAL_PRE_KICKOFF_INVALIDATION_V1`及V2 lock封装，不能修改旧V1 invalidation验证器。

为保留这一语义，拟议real attempt chain的root可以是INITIAL，或绑定一个当前有效old lock的POST_LOCK_REVISION。后者的新PREPARING链也可以合法pre-lock重准备；**old lock在replacement lock成功前仍有效**，不能提前抹除。最终事务同时消费新head、失效旧lock并推进全部prediction slots；新旧epoch/anchor/bucket/成员须相同、仍满足双deadline。一个old lock只允许一个revision chain和一个成功replacement lock。

开赛/关闭epoch后任何prediction replacement都拒绝。Result revision是另外一条赛后追加链，不能使prediction回到PREPARING。

## E. V4、计算与DecisionLock复用边界

### E1. 不把类型约束当作可以绕过的检查

实读v1.0代码可确认：

- `StrategySourceV2.analysis`具体绑定MultiMarketAnalysisV1。
- `build_strategy_v2`最后封存StrategyPassPlanV2；该plan内嵌上述旧source。
- Return `prepare_source`明确`type(plan) is StrategyPassPlanV2`；旧rd repository也只加载旧V2 plan并有旧mm_* typed FKs。
- 旧pv repository的epoch/analysis/lock/report引用和replay仍绑定旧图。

因此，不能把REAL分析塞进旧StrategySourceV2、伪造一个synthetic兼容plan，或删掉type check。**复用计算规则不等于复用所有旧高层工厂/数据库入口。**

### E2. 本设计明确的复用/版本化策略

| 层 | 复用的冻结部分 | 新的职责/禁止事项 |
|---|---|---|
| V4 packet/review | 原AnalysisPacketV4/contexts、LLMReviewV4、strict parser、import/check语义 | 新real unit的只读精确投影；packet.analysis ref指向REAL分析；新的typed存储，不放进旧mm_analyses FK；不加网页字段 |
| packet数值投影 | `export_packet_v4`需要的属性/原context构造规则 | 新真实类型的validated view提供同义值，不能伪造LegacyThreeWayInputV1或合成analysis；若旧调用不能安全接收则新builder仅封装ref/字段并逐字段等价验证 |
| fusion | 原`fuse_v4 / generic_correction`及policy | 新view必须通过自身完整真实谱系校验，原packet精确投影仍一致；无新的影响权重/cap |
| EV / Strategy | 原`outcome_candidates / plan_values`及其风险、候选生成、计奖helpers | REAL_STRATEGY_SOURCE/PLAN真实封装重验完整源图和plan_values；不调用拒绝真实source的旧高层工厂，不复制/修改分配算法 |
| Return | 原`binding_for / relevant_matches / evaluate_prepared / optimize_prepared`及完整guard/feasibility/utility | 新validated RealPreparedReturnInput只提供由真实sealed plan重建的binding/catalog/marginals/functions/roles；旧`prepare_source`拒绝语义不动，不cast成旧plan，不接受caller supplied prepared对象 |
| DecisionLock | 原全部money/layer/SP/review/strategy/optimizer依赖与时间约束 | 选用**DECISION_LOCK_V2**，新typed FKs与更多真实资格refs；不reinterpret旧LockV1 |
| Settlement / report | 原`settle_market`、payout/realized buckets、描述性Brier/log-loss/calibration/资金数值函数 | 新real lock/source/census绑定和元数据封装；不得改指标、normalization、权重或只选成功case |

数值leaf工件若其原合同只有generic refs而无synthetic literal，可按原class/原validator构造，用**新的real存储图**绑定。这包括原OutcomeCandidate、choice/atomic/ticket及Return计算工件；不意味着旧mm_/rd_ repository已接受它们。新real repository必须重新验证同样的完整来源、风险、roles、work/size bounds和math replay，不“只验hash”。

`RealPreparedReturnInput`是内部只读、已校验的计算输入视图，不是`PreparedReturnSource`/StrategyPassPlanV2的冒充对象。构造必须确认真实plan/selections/catalog与原kernel结果一致，并按原规则重建所有hints/roles/marginals；cache不是可导入事实。任何尚未证明安全的类型接缝都保持IMPLEMENTATION_UNAVAILABLE，不以duck typing/model_construct逃过验证。

比较原则：同一规范输入上的数学数值、候选/roles/分配规则、Decimal精度、全部风险/容量限制与原kernel一致。新schema/source refs会产生不同artifact IDs，不能要求其等于旧synthetic IDs；也不能为了复现旧synthetic赢家而篡改ID。原有canonical/ID tie-break规则继续用于真实graph，须单独做稳定性/等价性测试。

### E3. DECISION_LOCK_V2

保存real program/epoch/anchor/bucket/run refs；actual locked_at、LOCAL_SYSTEM_UTC、REAL_SOURCE_DATA；real analysis、原样V4 packet/review、独立correction audit、fusion、real strategy source/plan、return calculation binding/evaluation/optimizer refs。

锁定全部P_market/P_quant/P_base/P_llm/P_final及distribution hashes、model状态/谱系、SP refs、tickets/roles/multipliers、budget/stake/cash、configuration/implementation/frozen-policy hashes、source/admission/evidence uses及完整dependency hash。沿用V1的stake/cash等式、optimizer feasible/NO_BET语义、最早相关kickoff及60秒严格lead，不以“没有实际下单”放松时间门禁。

只有当前active PREPARING head可锁；formal lock与head消费、prediction slots/versions及seals同事务。UNAVAILABLE不能伪装成可锁NO_BET；合法NO_BET只来自原计算规则。已封存对象无字段更新。

### E4. Result / report版本边界

ResultObservationV1在已有合法manual来源/分类/receipt规则下可以作为真实结果事实被引用，不重标旧synthetic结果；必要的未来provider结果准入用独立binding，不改原V1结果历史语义。

选用REAL_PROSPECTIVE_SETTLEMENT_V1和REAL_VALIDATION_REPORT_V1，使新run/epoch/LockV2与REPLACED_PRE_LOCK状态可以安全引用。其内部数值通过原冻结函数获得；新代码只处理新的typed依赖/完整census资格和版本元数据。

报告必须封存program/epoch/anchor、as-of和receipt watermark、所有attempt及替换/锁定/结果/settlement关系。属于该epoch、但拒绝于run publication之前的请求也按REJECTED_REQUEST receipt列入操作census，不能因没有run ID而消失；无法合法归属epoch的请求只进program/全局安全审计，不捏造epoch FK。正式计分分母为截至as-of唯一、有效、合法的LOCKED prediction units，且有相应合格结果；被pre-lock替换/未锁定/不可用attempt不计输、不计样本，但始终在coverage中。超出冻结大小/工作上限明确报不可用，不截取成功记录。

全部layer配对比较、改好/改坏/neutral/abstained、NO_BET及缺失/unsupported/stale settlement的原意义不变。新结果修订追加settlement/report，旧report仍精确重放。真实少量样本仍INSUFFICIENT_PROSPECTIVE_SAMPLE，不把描述性指标或模拟锁定SP结算叫实盘ROI/alpha证据。

## F. Official prediction唯一性

逻辑key固定为 **`(observation_program_id, canonical_match_id, THREE_WAY_market_hash)`**，不包含epoch/slate/bucket/run ID。因此换这些名字不能取得第二个独立资格。

- `rb_prediction_slots`以该key作PK，首次正式lock一次性取得slot，并绑定initial_lock_id/owning_epoch。多个match的一个lock必须原子取得全部所需slots；有一个冲突就整体拒绝，不留partial lock。
- 后续合法赛前post-lock supersession不删除slot、也不另造slot；向`rb_prediction_versions`追加下一version，严格引用该slot当前version和replacement lock，并同时封存旧lock invalidation。
- 每slot只有version 0一个根；每个previous version最多一个successor，版本连续、无环。有效版本是给定as-of/sequence下的唯一末端；不是可改的active布尔列。
- Epoch/slate/bucket不同的独立lock企图再次INSERT同key会冲突。只有同epoch/anchor/bucket/成员的正式post-lock替代事务可推进versions，不能借跨epoch“更正”绕过。
- Program身份事前登记；不能在看到结果后换program并把重复cohort合并为一个性能报告。

## G. Additive schema / constraints设计（不执行DDL）

新表统一前缀`rb_`。不修改旧pv_/mm_/rd_定义、trigger、row或serialized artifacts；既有real input事实表可只读引用。以下是拟议必需表，不是migration代码。

### G1. 表与typed关系

| 新表 | 主键 / 主要typed FKs / 关键约束 |
|---|---|
| rb_receipts | PK request_key；operation闭集、request canonical hash、outcome、UTC/us、clock basis、单调sequence UNIQUE；COMMITTED response ref→rb_artifacts（deferred），REJECTED仅存安全reason |
| rb_artifacts | PK artifact_id；schema/content hash/canonical JSON/receipt ref；UNIQUE(id,schema,hash)；schema闭集；deferred自有seal FK |
| rb_seals | PK/FK artifact_id→rb_artifacts；seal前必须完整type projection/children/receipt |
| rb_programs | PK/FK artifact_id→rb_artifacts；observation_program_id UNIQUE，scope/assessment-unit identity |
| rb_implementations | PK/FK artifact_id；bridge代码/schema/replay身份、v1.0基线及冻结数学refs |
| rb_policy_values | PK/FK artifact_id；policy_kind闭集、exact value_schema/value_json/value_hash、code identity；禁止secret字段 |
| rb_market_keys | PK market_hash；仅允许冻结MarketKeyV2的THREE_WAY canonical JSON及其原hash，handicap为空；不引入新market数学 |
| rb_admissions | PK/FK artifact_id；program/provider/source policy、previous admission typed FK；scope/uses/effective/expiry/status及UTC；单链head规则 |
| rb_model_pins | PK/FK artifact_id；→production_quant_model_releases.release_id、quant_model_states.quant_model_state_id及相应原approval/target lineage；hash/scope/time验证 |
| rb_anchors | PK/FK artifact_id；program→rb_programs，implementation→rb_implementations；planned window/config hash；created<=sealed<start<end |
| rb_anchor_policies | PK(anchor_id,role)；anchor FK、policy FK→rb_policy_values，role强制对应policy_kind；每个规定role恰好一个 |
| rb_anchor_model_pins | PK(anchor_id,pin_id)；typed FK→rb_anchors/rb_model_pins；完整pin集合，不允许事后添child |
| rb_epochs | PK/FK artifact_id；anchor FK UNIQUE、program FK、previous_epoch FK；一个program首epoch、previous_epoch最多一个successor；无窗口重叠 |
| rb_epoch_closes | PK/FK artifact_id；epoch FK UNIQUE；close时间、census hash/watermark；已到end才允许关闭 |
| rb_slates / rb_slate_members | 声明PK/FK artifact_id；program/epoch FKs、原经审核slate/source/admission refs；member→matches及原fixture observation typed refs，完整候选/排除原因封存 |
| rb_buckets | PK/FK artifact_id；program/epoch、slate FK→rb_slates、exact kickoff、scope hash；声明时序/成员集恒定 |
| rb_bucket_members | PK(bucket_id,match_id,market_hash)；bucket FK、matches.internal_match_id FK、market_hash→rb_market_keys；member JSON与parent一致 |
| rb_market_bindings | PK/FK artifact_id；(ingestion_id,consensus_snapshot_id)→live_market_consensus_lineages复合FK；admission FK、match FK；MEDIAN_V1与current h2h来源检查 |
| rb_market_constituents | PK(binding_id,position)；binding FK；(ingestion_id,consensus_snapshot_id,source_snapshot_id)→live_market_consensus_constituents复合键，source snapshot→market_odds_snapshots；完整ID/hash集合唯一 |
| rb_sp_bindings | PK/FK artifact_id；(ingestion_id,snapshot_id)→live_source_ingestion_sporttery_snapshots复合FK及原manual provenance，snapshot→sporttery_bonus_snapshots；admission/match FK |
| rb_model_availability | PK/FK artifact_id；pin→rb_model_pins，match FK、目标准入/许可事实ref；AVAILABLE/MODEL_UNAVAILABLE与值/null一致 |
| rb_analyses | PK/FK artifact_id；epoch/anchor/bucket/program FKs；REAL_SOURCE_DATA CHECK；cutoff/complete input hash/status |
| rb_analysis_units | PK/FK artifact_id；analysis/match FKs、market_hash→rb_market_keys、market/SP/model availability FKs；UNIQUE(analysis,match,market)；unit集合等于bucket |
| rb_unit_evidence | PK(unit_id,position)；unit FK、snapshot→pv_evidence、binding→pv_evidence_bindings、football→mm_evidence；match/hash/cutoff/classification一致 |
| rb_packets / rb_contexts / rb_packet_units | Packet FK→rb_analyses；V4 context typed投影；PK(packet_id,position) child FK→context/unit；原V4 JSON字段/排序/identity不变 |
| rb_reviews | PK/FK artifact_id；packet FK→rb_packets；原raw bytes hash、V4校验、可信import receipt |
| rb_review_audits / rb_correction_reasons / rb_reason_evidence | audit→rb_runs/rb_reviews；reason位置及context FK；snapshot→pv_evidence；完整unit覆盖/typed fresh evidence，旧V4 wire不变 |
| rb_fusions / rb_fusion_units | fusion→rb_analyses/rb_reviews/policy；child→rb_analysis_units；完整原fusion数学重放 |
| rb_strategy_sources / rb_strategy_plans | 新真实source→analysis/review/fusion；plan→source/anchor策略pin；原source/plan数值函数输出重验，不能指向synthetic source |
| rb_math_nodes / rb_math_edges | 封存原generic math leaf图，closed kind及typed ref matrix见G2；不写入旧mm_/rd_图冒充旧source |
| rb_calculations | REAL_CALCULATION_BINDING根；FK real plan/fusion/analysis、return policy/objective pins、typed optimizer/evaluation math nodes；全部输入/数值图hash |
| rb_chains | PK chain_id；epoch/bucket、INITIAL或POST_LOCK_REVISION；original_root_run deferred FK；后者predecessor_lock→rb_locks UNIQUE；同scope只能一条INITIAL链 |
| rb_runs / rb_run_inputs | run→epoch/anchor/bucket/analysis/optional packet/chain；UNIQUE(chain,position)，root/predecessor ref；input child按role typed FK与完整set/hash封存 |
| rb_head_consumptions | PK parent_run_id→rb_runs；action REPLACE/LOCK CHECK；new_run FK UNIQUE、lock FK UNIQUE、replacement event FK；互斥nullable分支和current-head gate |
| rb_prelock_replacements | PK/FK artifact_id；old/new run FKs各UNIQUE，analysis/packet refs与run一致；同chain/bucket/config、无old lock、赛前 |
| rb_locks / rb_lock_frames / rb_lock_selected | LockV2→real run UNIQUE、epoch/anchor/analysis/packet/review/audit/fusion/plan/calculation；frame→unit/SP；selected→typed math candidate；money/time/hash一致 |
| rb_prediction_slots | PK(program_id,match_id,market_hash)；program/match/market typed FK，initial_lock→rb_locks（deferred），owning_epoch FK；首次取得唯一 |
| rb_prediction_versions | PK(slot key,version)；slot FK、previous version复合FK UNIQUE successor、lock FK、invalidation event FK；version0唯一根、以后连续且同owner epoch |
| rb_postlock_invalidations | PK/FK artifact_id；old/new locks各UNIQUE、new revision chain FK；单事务完整slot推进，旧lock仍满足赛前deadline |
| rb_settlements / rb_settlement_results | 新settlement→real run/LockV2/previous settlement；result child→pv_observations与match_results、source admission；只取epoch source当时完整heads |
| rb_reports / rb_report_census | report→real epoch/anchor/program，as-of/watermark；child_kind为RUN或REJECTED_REQUEST，分别typed FK→rb_runs或rb_receipts且互斥；run项连replacement/lock/invalidation/settlement；完整集合与仓库重建相同 |

旧表复合键必须完整引用，不能只凭字符串snapshot_id连一个无类型总表。旧row无artifact header时，parent新binding记录其typed键和原payload hash，replay读取原规范化记录及所属ingestion重新核验。

### G2. 数学子图的typed FK矩阵

为避免复刻算法或把真实源写进旧typed图，`rb_math_nodes`只接收这些已有**无synthetic literal**的计算类型：OUTCOME_CANDIDATE_V1、MATCH_CHOICE_SET_V1、EXPANDED_ATOMIC_BET_V2、SYSTEM_TICKET_CANDIDATE_V2、SYSTEM_TICKET_V2、RELEVANT_MATCH_STATE_V1、TICKET_RETURN_FUNCTION_V1、PORTFOLIO_RETURN_DISTRIBUTION_V1、RETURN_DISTRIBUTION_METRICS_V1、RETURN_EVALUATION_V1、RETURN_OPTIMIZATION_RUN_V1。

`rb_math_edges(owner_id,owner_schema,owner_hash,role,position,target_id,target_schema,target_hash)`两端均具有到`rb_artifacts(id,schema,hash)`的复合FK，且只能使用下表的有限role/type组合；不是任意schema/JSON reference通道。封seal时每个JSON引用必须有且只有对应projection，额外edge或缺edge都拒绝。

| Owner kind / role | 唯一允许的target kind |
|---|---|
| Outcome / analysis,fusion,unit,SP | REAL_MULTI_MARKET_ANALYSIS_V1、GENERIC_FUSION_RUN_V1（rb_fusions）、REAL_ANALYSIS_UNIT_V1、REAL_SP_SOURCE_BINDING_V1 |
| ChoiceSet / candidates；Atomic / legs | OUTCOME_CANDIDATE_V1 |
| TicketCandidate / source, choices, atomics | REAL_STRATEGY_SOURCE_V1、MATCH_CHOICE_SET_V1、EXPANDED_ATOMIC_BET_V2 |
| SystemTicket / candidate | SYSTEM_TICKET_CANDIDATE_V2 |
| RelevantState / unit,fusion,SP | REAL_ANALYSIS_UNIT_V1、rb_fusions中的GENERIC_FUSION_RUN_V1、REAL_SP_SOURCE_BINDING_V1 |
| TicketFunction / candidate,source,atomic,leg | SYSTEM_TICKET_CANDIDATE_V2、REAL_STRATEGY_SOURCE_V1、EXPANDED_ATOMIC_BET_V2、OUTCOME_CANDIDATE_V1 |
| Distribution / binding plan/source/analysis/fusion、matches/functions/allocations | 对应real plan/source/analysis/fusion，RELEVANT_MATCH_STATE_V1 / TICKET_RETURN_FUNCTION_V1 / SYSTEM_TICKET_CANDIDATE_V2 |
| Metrics / distribution | PORTFOLIO_RETURN_DISTRIBUTION_V1 |
| Evaluation / binding,distribution,metrics,selected | 对应real binding refs、PORTFOLIO_RETURN_DISTRIBUTION_V1、RETURN_DISTRIBUTION_METRICS_V1、SYSTEM_TICKET_CANDIDATE_V2 |
| Optimizer / binding,baseline/result/legacy,steps/catalog | 对应real binding refs、RETURN_EVALUATION_V1、SYSTEM_TICKET_CANDIDATE_V2及相应distribution hash关系 |

Policies/objective引用另由`rb_policy_values`按精确value schema/hash的typed pin验证；嵌入对象同样须index/seal，所有用于依赖的hash还须对应实际对象，不能留下“校验过字符串但不存在图”的洞。上述schema对应新的type-projection表存在性由complete seal保证；core refs不能任意落到synthetic mm对象。

### G3. Append-only / head / completeness / downgrade

必须落到数据库的唯一性包括：`request_key`、receipt sequence、`(id,schema,hash)`、`anchor_id`被epoch使用一次、`previous_epoch_id`最多一个successor、`UNIQUE(program_id) WHERE previous_epoch_id IS NULL`、`UNIQUE(epoch_id,bucket_id) WHERE chain_kind=INITIAL`、每个predecessor lock最多一条POST_LOCK_REVISION链、`(chain_id,position)`、head consumption的parent run PK、pre-lock event的old/new run分别UNIQUE，以及program级prediction slot PK。版本0与非根predecessor规则须有CHECK/trigger，不能依赖SQL对NULL的默认UNIQUE行为来假定只有一个根。

1. 所有新artifact、receipt、typed projection、edge、slot/version/consumption、seal表禁止UPDATE/DELETE及INSERT OR REPLACE覆盖；重复主键仅由repository验证exact equality后无写重试。
2. 已sealed parent禁止补添child。Canonical JSON、header/schema/hash、每个typed字段/child位置/引用三元组相符；未知schema/role/不完整catalog拒绝。
3. Header→seal、receipt→response及replacement/lock/slot图使用DEFERRABLE INITIALLY DEFERRED完整性。Seal gate先检查应有type row、精确child集合、跨对象一致性；事务提交前不能留下未封存root。
4. Head consumption PK在数据库内仲裁；应用校验与DB gate都检查current head/正确窗口，没有“先查后写”的两事务竞态。Program slot PK与version predecessor UNIQUE在数据库内防跨epoch重复。
5. Live system-clock receipt单调，UTC字符串与整数微秒相符；真实写入按现有clock/commit窗口约束。历史replay用事件当时的sequence/as-of，不因后来source修订或许可变化重解释旧工件。
6. Exact retry验证operation/request/hash/原输入引用后读原response并完整replay；冲突key拒绝。未知commit结果先查receipt，不自动换key、clock或DB。
   已知且合法归属epoch的拒绝保留REJECTED receipt，不消费head/slot。无法通过request schema的任意原文不直接落库，尤其禁止把意外传入的credential写进request_json；仅记录安全reason、bounded context及允许保存的请求摘要/hash。
7. Downgrade仅在全部新增rb_*数据为空时可移除新schema；任一artifact/receipt/slot/child有数据即拒绝，保持整个库及旧v1.0对象不变。绝不DROP/改写旧pv_*。
8. Additive upgrade测试必须用真正v1.0.0源码建立旧库，逐字比较所有旧定义/trigger/rows/serialized artifacts；不以新代码在旧head建库冒充保存证明。本轮只设计，没有实际DDL/migration file。

## H. 未来provider HTTP许可边界

本轮REAL_PROVIDER_HTTP=0。未来The Odds API调用只有同时具备：有效credential、用途/retention条件确认、activation source admission完成、operator明确触发，才允许调用**既有current h2h入口**。

Credential只在既有安全运行时通道使用；artifact/anchor/日志/审核材料不含secret值或可还原credential。无自动购买/续订，不以重新初始化数据库或复用旧trial当新授权。Sportmonks restricted raw保持永久关闭，不恢复。网页GPT仍通过V4文件协作，LLM API HTTP不因本合同自动获准。

## I. Acceptance design — RA01–RA20

**下表是必须实现的后续验收设计，不是本轮测试PASS声明。** 软件验收只用清楚标记的合成输入/隔离库，模拟“真实类型”结构不能宣称采集了真实performance sample；网络禁止，LOCAL_SYSTEM_UTC可在隔离测试中控制但测试来源标签永不升级。

| ID | 正向/负向设计与通过条件 |
|---|---|
| RA01 | 新real analysis只接受REAL_SOURCE_DATA和THREE_WAY；synthetic literal、错market/主客、缺source ref均拒绝 |
| RA02 | 旧V1 artifact不可重标；改classification/一致重封/伪造新schema或hash仍因真实源与typed链不符拒绝 |
| RA03 | 只有program+独立配置anchor+合法pins即可bootstrap新epoch；不创建任何match/ticket/StrategyPass seed，数据库无seed FK |
| RA04 | anchor和epoch必须事前sealed/created；等于或晚于start、未来source、caller event-time override拒绝 |
| RA05 | 缺失/失效/不适用pin拒绝可运行epoch；既有epoch后的target/model不可用形成明确availability fact/UNAVAILABLE，不能临时训练 |
| RA06 | P_quant null时不能复制P_market；冻结base/fusion无法形成合法层时无ready run/lock，不能伪造NO_BET |
| RA07 | The Odds API request/receipt、mapping、全部bookmaker/consensus lineage及原数值逐项重放；删constituent/篡改scope/time/normalization拒绝 |
| RA08 | Sporttery document/evidence/ingestion/quotes/身份/review完整重放；错SP、缺outcome、主客反转、late/旧hash拒绝 |
| RA09 | 等价时区同桶；1秒/1微秒不同kickoff必须不同桶；成员和market完整一致，不能按票据或赛果重组 |
| RA10 | cutoff/prepared/receipt来自repository；future/晚入资料、已知赛果、clock回退、prepare/lock提交跨deadline均fail closed |
| RA11 | real prepare生成精确新V4 packet与real analysis ref；原strict V4校验/场景/absolute P_llm/abstention正常；旧packet/review/hash错配拒绝 |
| RA12 | R0→R1→R2 pre-lock链全部保留；新analysis/packet/run，新cutoff；只最后active head能锁，旧bytes不变 |
| RA13 | replacement/replacement、replacement/lock多连接竞态，仅一个head消费；部分提交回滚；文件失败重取同一receipt |
| RA14 | 同program+match+THREE_WAY跨epoch/slate/bucket的独立lock冲突；合法同epoch赛前post-lock替代只推进同slot version，计数仍一份 |
| RA15 | 旧V1 synthetic lifecycle/export/fusion/Strategy/Return/lock/report原bytes与goldens精确回归；V1 REAL gate及seed guard原样拒绝 |
| RA16 | 真正v1.0.0 populated库additive upgrade保留所有旧定义/trigger/rows/artifacts和原操作；新数据存在时downgrade拒绝，空库往返通过 |
| RA17 | refs/schema/hash、policy/pin/source/head/slot/receipt/census篡改及一致重封攻击拒绝；不得仅依赖JSON hash |
| RA18 | 软件验收禁止provider/LLM HTTP及自动订阅/下注；mocked/fabricated测试不写入真实观察program，不把测试时钟当真实资格 |
| RA19 | 原normalizer、MEDIAN、Elo、base/fusion、EV、Strategy、Return、objective、payout及metrics source identities/goldens不变；无训练/自动调参；类型view只复制/绑定数据 |
| RA20 | 隔离安装wheel后，固定输入/clock/receipt序列下，anchor→epoch→bucket→real prepare→V4→replacement→lock→result revision→settlement→full report/audit的canonical bytes/hash确定；exact retry、不同输入顺序、hash seed、ambient Decimal及崩溃恢复不改变已封存意义 |

附加必测：整桶UNAVAILABLE不暗删成员；所有attempt出现在census；NO_BET/零预算/missing/unsupported/stale revision保持；无限log-loss不epsilon补值；无合法真实样本仍INSUFFICIENT_PROSPECTIVE_SAMPLE；不同schema的真实ID不得伪装旧synthetic ID来影响tie-break。

## 10. 冻结数学身份与审核停止点

以下值仍是v1.0冻结依赖的身份，不是新bridge已完成的代码hash：

| 身份 | 接受值 |
|---|---|
| Return algorithm | `76995ed699a8d054f3865ffbbc0063a46af2123f18ab34aa6b27a91690dc7aa6` |
| Return policy | `bd9cf8b2c3d36ce13327cb49b9513c4d6c995762387606ce26bba37ef0769b47` |
| Objective | `9bb39a771ac2390cb537030c809a7e214dcf88bcce2be17808bc7b3dcbfe7c30` |
| V1 prospective implementation | `6b2a73c14720c60eb30f9bc5d4158bccf095d397748cd1157eb53f13933ddb1f` |
| V1 prospective policy | `06f9cb5289959cf6387b324f9a8ef617d1960a68e88e23cb1423e0c15ef847f8` |

新bridge实施身份、real政策pin及schema仅在获得实现授权、完成RA验收时才能产生实际hash；本合同不制造这些值或实际工件。保持Preparation Mode冻结，无main merge、version bump、tag、真实adapter/epoch/lock或migration实现。

**交付本合同与两张schema/state图后停止，等待网页GPT最后一次Bridge Contract Review。**
