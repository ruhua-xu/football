# POST-1.0 PRODUCTION ACTIVATION / PROSPECTIVE OBSERVATION PREPARATION

## 0. 状态与结论

**方案状态：Activation Plan Review PASSED；只授权 INPUT_PREPARATION。真实 Production Decision / DecisionLock 未获授权。**

本轮落实与修订以 [Activation Slice V1](production_activation_slice_v1.md)、[PRE-LOCK replacement草案](pre_lock_replacement_v1_contract.DRAFT.md)、[operator契约草案](daily_operator_v1_contract.DRAFT.md) 为准。当前`daily.cmd`只提供“今日准备、导入竞彩SP、导入赛前事实、查看缺项、数据/文件校验、备份”；下文完整预测/lock流程是待审核的远期目标，当前入口不开放。

- 软件工程主体：**CLOSED**。
- 冻结版本/tag：**v1.0.0**。
- 冻结commit：`6d633d4425d5fd6d93918d60526f701a59020b3d`。
- Migration head：`5b748fa162ed`。
- **REAL PERFORMANCE = INSUFFICIENT_PROSPECTIVE_SAMPLE**。
- **PRODUCTION DECISION ADAPTER = UNAVAILABLE**；现有reason为`PRODUCTION_DECISION_ADAPTER_UNAVAILABLE`。
- 本轮只新增设计文档和INPUT_PREPARATION薄包装；v1.0冻结文件/算法不变，不创建1.1。首次运行库初始化须用户显式确认；本次开发不替用户初始化真实运行目录或执行真实业务。
- 本阶段provider HTTP、LLM API HTTP均为**0**；也没有实际网页资料查询、真实训练、预测、回测或下注。下文网页GPT查询职责是待授权的未来流程。

**建议：先把每日资料准备做成一个本地入口；真实决策接入另设一个明确的审核关口。**
v1.0已具有人工资料导入和封存/审计组件，但当前不能直接把真实比赛送进完整的prospective决策闭环。仅补一个启动脚本、填写`REAL_PROSPECTIVE`或换一个数据标签，都不能关闭这个缺口。

文中标记：**【现有】**表示v1.0已有实现；**【设计】**表示尚未实现的operator/glue方案；**【真实链路阻塞】**表示目前不得当作可运行的真实闭环。

## A. 当前 v1.0 能直接完成的每日步骤

“已有命令/组件”不代表本阶段已执行，也不代表当前数据、授权和模型state已具备。

| 每日环节 | 已有能力 | 必须保留的边界 |
| --- | --- | --- |
| 建立候选slate | `live plan-slate`读取经审核的本地每日清单或Sporttery manual archive，生成identity reconciliation/capture plan | `DAILY_SLATE_PLAN_V1.analysis_status`仍是`NO_ANALYSIS`；`READY_FOR_CAPTURE`不是可以预测、锁定或下注 |
| 录入比赛身份 | `live ingest-fixtures-manual`支持经审核的本地fixture材料，复用/注册canonical identity并记录来源 | 赛事、赛季、主客、kickoff等须准确；歧义必须处理，不能猜ID或直接改数据库 |
| 录入竞彩SP | `live ingest-sporttery`支持`SPORTTERY_MANUAL_ARCHIVE_V2`，可用JSON/CSV和本地证据 | 当前真实manual adapter仅支持**THREE_WAY**；不是通用多市场生产入口 |
| 当前真实市场赔率 | `live ingest-market-odds`、`TheOddsApiMarketOddsProvider`和`MARKET_CONSENSUS_MEDIAN_V1`已存在 | 实际调用须另有合法有效credential/data-use条件；当前Preparation入口不发provider HTTP |
| 动态事实 | `prospective evidence-import`支持`MANUAL_VERIFIED_IMPORT_V1`、实际source文件SHA及typed facts | 可以登记合法的`REAL_SOURCE_DATA`事实；须有canonical match。导入事实本身不是预测、锁定或真实performance sample |
| 网页review文件校验 | `market-v2 review-validate`可本地校验V4 packet/review完整绑定 | 校验通过不代表source可用于真实production，也不自动产生DecisionLock |
| 模型和计算组件 | 既有Elo/Poisson、fusion、EV、Strategy、Return/Settlement函数；旧live路径也有pinned production release入口 | 需要当前合法且适用的release/state及来源证明；不能把软件可调用等同于已获准真实推理。本阶段不执行这些运算 |
| Epoch/prepare/lock | 已有`prospective epoch / prepare / lock`及完整封存/重放机制 | **真实链路阻塞**：现有多市场分析声明为synthetic；真实epoch的prepare明确不可用 |
| 赛果事实 | `prospective result-import`可登记manual verified结果、receipt和revision chain | 需正确match/source/time/regular-time语义；没有合格赛前lock的比赛不能补成真实prospective样本 |
| 结算、报告、审计 | `prospective settle / report / show / audit` | 消费已封存且兼容的工件；本版本的synthetic验收结果不能冒充真实每日表现，空/不足样本如实报告 |

特别区分两个prepare：`live prepare-analysis`封存旧live输入；`prospective prepare`绑定已有analysis并建立prospective run。它们不是同一工件，也不是自动互通的生产桥。

## B. 目前缺失或尚未证明具备的真实 production inputs

本文没有检查/读取旧restricted raw，也没有假定任何旧trial、历史许可或模型release目前仍有效。

| 输入/运行条件 | 每日或首次需要什么 | 当前结论 |
| --- | --- | --- |
| 数据来源与用途 | 可访问、可用于相应处理/网页协作/保存的来源；source reference、rights reference、retention范围 | 待逐项提供与核验；以前的软件验收/订阅试用不自动延续授权 |
| 今日竞彩范围 | 官方编号及所属日期、赛事/赛季、主客、UTC kickoff、开售状态、完整候选范围 | 待当天取得；不能用测试slate或历史比赛补当前样本 |
| Canonical mapping | 比赛/球队/赛事ID、别名、主客方向及改期状态 | 待正式导入/核对；网页GPT不生成或猜测内部ID |
| 实际SP快照 | 确切market key、完整outcome/price、采集与复核信息及本地证据 | THREE_WAY已有manual入口；新旧结构到prospective真实source graph的衔接仍需审核 |
| P_market所需赔率 | The Odds API current h2h，经既有live ingestion及MARKET_CONSENSUS_MEDIAN_V1 | adapter及数学已有；缺口是有效credential/data-use条件、当前数据可用性，以及market source到REAL_PROSPECTIVE graph的正式绑定。不得以SP/GPT意见/synthetic代替 |
| P_quant模型输入 | 当前有效且适用的模型release/state、model/config/training-data hashes、scope及target准入 | 未核验已具备。旧pinned Elo组件可供接入评估，不等于今日可用；不能为开跑临时训练、调参或恢复已删数据 |
| 动态evidence | 赛前lineup/injury/suspension/schedule/rest/form等可追溯事实，缺失时明确UNKNOWN | manual合同已有，今日真实材料及规范化工作待完成；“UNKNOWN”不等于负面事实 |
| 真实decision source | 可以诚实承载真实classification、来源/时间/模型谱系且能被prepare/lock/replay接受的封存graph | **明确缺失：当前adapter UNAVAILABLE**，不是单纯缺一份输入JSON |
| Epoch启动材料 | 事前固定的窗口、scope、配置、预算集合、result source及合法seed/config来源 | 待设计真实bootstrap。现有`epoch`要求`seed_plan_id`；不能以synthetic seed替代真实接入证明 |
| 比赛结果 | 正确match的常规时间FT结果、来源时间、实际取得/验证时间、修订信息 | manual合同已有；每天比赛结束后新取得，不能预填或假装赛前已知 |
| 操作环境 | 冻结wheel、独立数据库、系统UTC时钟、目录权限、备份及恢复方案 | 待首次部署确认；本阶段只提出位置和流程，不创建/迁移真实库 |

缺少非必需动态事实时，按既有UNKNOWN/UNAVAILABLE/abstention规则呈现；不新加“一律必须确认首发”的业务规则。缺少身份、必要价格/分布、合法来源、真实adapter或锁定时间等必需条件时，停止相应决策步骤。

## C. 最小 activation gap

### C1. 必须如实识别的现有门禁

1. `domain/market_analysis.py::MultiMarketAnalysisV1`的classification固定为`SYNTHETIC_ACCEPTANCE_DATA`。
2. `domain/services/prospective.py::prepare_values`在`epoch.mode == REAL_PROSPECTIVE`时明确给出`PRODUCTION_DECISION_ADAPTER_UNAVAILABLE`；当前生成的run classification仍为`SYNTHETIC`。不能因epoch创建成功就认为真实决策已可用。
3. `infrastructure/providers/exact_market_fixture.py`及`market-v2 snapshot-import`是**fixture-only**，固定synthetic classification/provider；不能把真实SP或bookmaker数据填进它，再称为真实观察。
4. `prospective prepare`需要已有sealed `analysis_id`，不从一张CSV自动生成模型输入。Evidence必须在构建analysis时已绑定；不能prepare后再塞进同一个packet。
5. 现有epoch要求sealed V2 seed plan；真实配置的bootstrap、现有live/V3来源与V4/prospective source graph衔接尚无已批准生产路线。

这些是已知运行边界，不是要求现在修复的architecture blocker。保留`UNAVAILABLE`正是正确行为。

### C2. 待审核的最小闭合路径

**合法输入 → 精确identity/market规范化 → 有效冻结模型state/既定P_market输入 → 真实source graph与epoch配置 → 原样V4文件协作 → 原有锁定/结算/报告语义。**

最小接入设计需要回答四件事：

- **输入边界**：首个slice正式限定THREE_WAY；P_market复用The Odds API current h2h既有normalization与MARKET_CONSENSUS_MEDIAN_V1，SP复用SPORTTERY_MANUAL_ARCHIVE_V2。补齐的是许可、当前数据及真实graph绑定，不是重写adapter或共识数学。不同exact kickoff必须分桶，不能赛后重组。
- **模型与配置边界**：只消费经核验的既有合法release/state和固定参数，说明怎样产生可接受的真实epoch配置/seed及未来窗口。若state/history/授权缺失就继续不可用；本阶段不补训、不自动启用未pinned路径。
- **真实谱系边界**：必须有单独审核的production source/classification、receipt和replay绑定方案，打通合法来源到decision的链路。禁止重标synthetic、删除UNAVAILABLE判断、手工构造已封存对象、跳过repository校验或直接写数据库。
- **数学与锁定边界**：复用冻结数学；相同输入应与冻结实现有等价性证据；cutoff、lead、atomic supersession、result revision、complete census、风险/优化语义不得弱化。

**结论：仅写一层命令包装不能完成C2。** 如实现需要扩展sealed contracts/repository分发或增加真实source实现，必须先提交具体边界设计获得授权；v1.0.0的commit/tag与旧工件保持不动。任何新adapter及其执行实现身份须单独记录并进入新、事前声明的epoch，不能借用旧implementation hash掩盖不同执行路径。本方案不预设新软件版本、不创建1.1。

### C3. 分步启用，不把“资料齐了”当作“模型有效”

| 阶段 | 用户能做什么 | 进入下一步的条件 |
| --- | --- | --- |
| 已完成：PLAN_REVIEW | 本运行方案已由网页GPT审核通过 | 只授予INPUT_PREPARATION，不授予真实decision |
| 当前已授权：INPUT_PREPARATION | 六项菜单：今日准备、SP、赛前事实、缺项、校验、备份 | 实现薄包装；首次实际初始化须用户确认。本轮不代用户采集/导入真实资料，不计performance |
| 后续待审：ACTIVATION_ACCEPTANCE | 验证C2真实接入链、配置bootstrap及最小glue | 合法当前输入、时序/分类/replay/负向测试均满足；明确接受实现身份和启用范围 |
| 批准后：PROSPECTIVE_OBSERVATION | 每天赛前锁定，赛后观察/结算/全量报告 | 不回填activation以前的lock；保持不足样本/不可用等原有状态，不据此自动调参 |

这些阶段名是**operator设计标签**，不是v1.0新增数据库枚举。当前banner始终显示“准备模式：真实决策入口不可用”。达到30个观察等数量门槛也不是ROI、alpha或production activation的自动证明。

## D. 推荐每日用户操作流程

### D1. 一个入口、六个动作【设计，尚未实现】

当前已授权的独立入口为 **`daily.cmd`**，只实现INPUT_PREPARATION六项菜单，详情见operator契约草案。它不修改已发布的`football-system`命令或算法。以下完整观察流程保留为远期设计，不能据此执行真实prepare/export/lock。

菜单只保留：**①今日准备 → ②导入资料 → ③发给网页GPT → ④导回并锁定 → ⑤赛果结算 → ⑥报告备份**。
程序维护日期、IDs、request keys和路径；用户不需要编辑内部ID、hash或长命令。未获真实activation时，③中的正式分析packet与④真实lock必须禁用；可导出的只是清楚标记为“资料收集用”的清单。

| 用户动作 | 用户需要做的事 | 本地入口应给出的结果 |
| --- | --- | --- |
| ① 今日准备 | 确认日期/候选比赛范围和已批准预算选项 | 完整名单、身份状态、当地时间+UTC、最早kickoff、缺项清单；没有比赛就正常收工 |
| ② 导入资料 | 选择SP文件/允许保存的截图等来源材料；确认网页GPT整理的事实 | 身份/数值/source hash/时间/分类校验及导入回执；未知事实醒目标识 |
| ③ 发给网页GPT | 先完成事实收集；真实接入批准后，将新生成的packet JSON/MD发到网页GPT | 一次具体run绑定的冻结packet与review说明，不复用昨日packet |
| ④ 导回并锁定 | 放入review和reasons两份文件；核对摘要并显式确认“锁定观察方案” | 先验格式/引用/时序，再调用正式lock；显示ledger确认的LOCKED、时间余量及audit结果 |
| ⑤ 赛果结算 | 赛后填写/导入有来源的常规时间比分，确认更正是否为新revision | ResultObservation回执、完整性状态、settlement；缺失/取消等列为待处理，不假算输 |
| ⑥ 报告备份 | 查看今日处理结果及累计报告，确认备份成功 | 全epoch报告、audit、文件索引/校验和与可恢复备份；包括NO_BET、失败和未完成记录 |

这是**真实赛果上的前瞻观察**流程，不是下单流程。DecisionLock是锁定分析/模拟分配，不是购票确认；按锁定SP计算的结算金额不是已发生的真实账户现金收益。

### D2. DAILY_OPERATOR_CHECKLIST_V1【待审核设计】

定位：给非工程用户的一页清单。每项记录`PASS / BLOCKED / NOT_APPLICABLE`、检查时间、operator和对应文件/receipt ID；不是勾选框代替系统校验，也不写进冻结V4或pv_*契约。

| ID | 用户只需确认的内容 | 程序/证据依据 | 不满足时 |
| --- | --- | --- | --- |
| D01 | 今天入口显示正确日期、冻结版本与运行模式 | 本地release指纹、UTC/当地时间、activation状态 | 停止；当前UNAVAILABLE不得变绿 |
| D02 | 全部候选比赛、主客、开赛时间和编号日期正确 | slate文件、canonical mapping/reconciliation | 修正未封存输入或请求身份复核；不猜ID |
| D03 | SP与原始来源一致，市场没有混淆 | 完整outcomes、Decimal字符串、source hash、sale状态 | 停止相关输入；不补赔率、不用其他市场替代 |
| D04 | 事实有出处；不知道的仍写不知道 | evidence receipts、source时间、freshness/knowledge状态 | 按冻结规则标记UNKNOWN/UNAVAILABLE；禁止伪造确认 |
| D05 | 本次需要的生产source/model/epoch均已正式通过 | 有效来源、pinned state/config、adapter资格 | 只保存准备记录，不生成真实决策 |
| D06 | 网页GPT收到的是本轮冻结packet | run/packet/context IDs及hash；发送记录 | 重取正确文件，禁止手改packet |
| D07 | 网页GPT回传两份正确文件，或明确选择本日不使用review | V4校验、reasons全覆盖、已封存evidence引用 | 有错就停止；不能把坏review静默改成跳过 |
| D08 | 仍在赛前，程序显示锁定余量和正确预算 | 最早相关kickoff、冻结lead、源图/epoch校验 | 停止；不改电脑时钟、不回填时间 |
| D09 | 点击一次后，看到真正的LOCKED及审计通过 | DecisionLock ID/hash、`show(run)`、`audit(lock)` | 未确认前视为未锁定；超时先查receipt，再精确重试 |
| D10 | 赛后结果来源正确，常规时间与加时/点球区分清楚 | ResultObservation及原始证据；revision predecessor | 不猜比分，不覆盖旧结果 |
| D11 | 今日包含未完、NO_BET、失败/修订状态，累计报告未挑案例 | settlement、全epoch census、report as-of/watermark、audit | 缺失保持待处理；STALE_SETTLEMENT重结算 |
| D12 | 数据库与文件备份已完成，位置和校验和可查 | 一致性备份、日索引、retention清单 | 标记未完成；恢复问题交维护者，不能回滚重造lock |

**D01–D12是完整闭环的后续checklist设计；当前六项Preparation菜单没有真实packet/lock/结算绩效入口。不代用户打勾，不宣称已完成一次真实每日运行。**

## 1. 每日真实 slate 如何建立

1. 以当天允许使用的竞彩赛程为候选范围，由用户取得并保留许可范围内的原始证据。范围按事前规则确定，保留全部候选及排除/缺失原因，不能赛后只留下赢的比赛。
2. 可用现有`SPORTTERY_DAILY_SLATE_INPUT_V1`轻量JSON；已有SP时也可用`SPORTTERY_MANUAL_ARCHIVE_V2` JSON/CSV。轻量slate用于计划，不替代SP正式入库。
3. 人工核对官方编号及所属日期、赛事/赛季、主客队和开赛时间；转换UTC时保留原时区/时间出处。`match_number_date`/`match_date`按官方编号日期理解，不能仅凭“周一001”或队名作为唯一身份。
4. 缺少canonical identity时，先走经审核的`live ingest-fixtures-manual`；再用`live plan-slate`核对。唯一精确匹配才继续；歧义使用已有reconciliation/review机制，不在表里硬填一个ID。
5. 操作日可按北京时间展示；首个slice强制**ONE RUN = ONE EXACT KICKOFF_AT_UTC BUCKET**。先规范为UTC，再以完整时间戳精确相等分组，不按日期/小时/近似窗口合并。分桶随赛前slate封存；slate_date取该bucket的UTC日期，不得按赛果重组。
6. 无候选时使用允许空candidates的轻量slate，保留`NO_SPORTTERY_CANDIDATES / NO_ANALYSIS`，不伪造run。未锁定的准备记录不算prospective样本。

所有相关比赛都参与lock的最早kickoff计算，不能因某场没有入选ticket就忽略其时间。赛程变更须新证据和重新核对；v1对冲突历史保守拒绝，不擅自修改旧canonical记录。

## 2–3. 本地程序、网页 GPT 与人的分工

| 内容 | 本地程序 | 网页GPT / operator |
| --- | --- | --- |
| 数据身份、source bytes/hash、事件receipt | 正式校验、记录、生成ID；保存时序 | 人提供/复核出处和真实材料；GPT可指出冲突，不能编造本地ID/哈希/时间 |
| P_market、P_quant及模型谱系 | 批准接入后按冻结实现和当前合法state处理 | GPT不能代算后填成模型输出，不能训练/调参或用SP推断一个假的P_market |
| SP | 保留准确报价、完整market语义及所用快照 | 人核对数字/主客/市场；GPT可整理草稿但原来源是权威 |
| Lineup/injury/suspension/schedule等 | 校验typed payload、来源、时间、freshness及绑定 | GPT可在获准后查询可靠来源并列出处；最终资料复核/权利声明由真实operator承担 |
| V4 review | 验证全部context/evidence引用，保留原bytes | 网页GPT在冻结packet范围内给绝对P_llm、场景、限制、弃权及独立reasons |
| Fusion/P_final/EV/票/风险/Return | 本地冻结规则决定并在lock时封存 | GPT不决定或修改预算、weights、multiplier、风险阈值或最终tickets |
| Lock/结算/累计报告 | 可信本地事件时间、append-only及完整重放 | 人确认操作、赛后核对结果；不能手工填写锁定时间或改审计结论 |

**网页GPT分两轮协作：**

- **事实收集轮（prepare之前）**：按已允许的来源查询/审核club/league官方公告、阵容、伤停、停赛、赛程等，输出带出处的候选事实。网页可见不自动等于许可允许保存/上传。未知publication/coverage必须写未知；模型记忆、未访问链接、搜索摘要不当作已核实事实。
- **冻结review轮（prepare之后）**：阅读本轮`analysis_packet.json/md`，不再把新查询事实偷偷带入同一个决策上下文。若发现新资料，单独报出“需要新准备”，交回本地在合法赛前重新处理，不能把新资料伪装成旧cutoff前已入库。

GPT查询能力/网页访问也须以后单独授权；本阶段不执行。不得无证据从资本、赞助、母公司、地域或租借关系推断默契比赛。共享给网页GPT的资料受使用/保留范围约束，不上传API keys、cookies、登录材料或受限raw。

## 4. 竞彩足球 SP 的人工/结构化导入

**【现有】THREE_WAY路线：**用户从允许的竞彩来源取得完整主胜/平/客胜报价与比赛身份，保存证据文件；人工表格/网页GPT可以辅助转录，最终按`SPORTTERY_MANUAL_ARCHIVE_V2`提交JSON/CSV，调用`live ingest-sporttery`。

- Document至少含schema、snapshot ID、captured时间、source reference/path/SHA256、entered_by、review_level、reviewed_by、reviewed时间和records。
- Record含编号及日期、competition/season/type、两队provider身份/名称、UTC kickoff、sale_status、`market_type=THREE_WAY`及`home_win/draw/away_win`。
- SP使用**十进制字符串**，例如`"2.35"`；不传binary float，不用OCR结果未经复核直接入库。完整三项价格、market和主客方向都要确认。
- `SELF_REVIEWED`如实记录同一人录入/复核；`INDEPENDENT_REVIEWED`必须有真实的不同reviewer。网页GPT不作为虚构的第二位人类reviewer。
- 赔率改变时新建snapshot并保留旧出处/回执；不能覆盖已用于锁定的SP。网页GPT不得给出“更合理的SP”替换原价。

**【真实链路阻塞】**现有`market-v2 snapshot-import`支持的handicap/total-goals/correct-score等是synthetic fixture映射，不能借它导入真实多市场数据。通用真实市场、完整catalog/handicap语义及来源到V2 graph的映射属于C2待审核接入；不是把CSV列名改一下就已支持。

## 5. Lineup / injury / suspension / schedule 等 evidence 导入

流程：**候选资料 → operator确认来源/事实/许可 → 保存允许的原文件 → 生成manual request → `evidence-import` → 用返回binding构建新的合法analysis → prepare**。

每个`EvidenceImportRequestV1`含`request_key`和`evidence: ManualVerifiedImportV1`。后者须有match_id、`data_classification=REAL_SOURCE_DATA`（只用于确实真实且合法的事实）、source identity/reference、相对source_file、实际source_hash、rights basis/reference、retention、verified_by/time、capture/publication/availability、fact category、assertion class、confidence及structured payload。真实receipt/ingested时间由本地程序记录；外部文件不能回填。

| 类别 | 内容与核对重点 |
| --- | --- |
| LINEUP / EXPECTED_LINEUP | team/player身份、starting XI/bench/formation、UNKNOWN/EXPECTED/CONFIRMED；确认需完整11人和明确confirmation reference |
| INJURY | player/team、reason、ACTIVE/RESOLVED/UNKNOWN、起止/预计回归、source/availability及confidence；预计回归不是确认复出 |
| SUSPENSION | 独立停赛类别及原因、适用时间；不能把injury记录改名当ban |
| SCHEDULE / REST | 明确时间窗、competition scope、known-at、fixture状态/改期信息；休息日按冻结口径，不宣称未覆盖的全部赛事 |
| FORM | 窗口、home/away/any与既存MatchResult IDs；不能用未来、当前目标或可见已废弃revision |
| MOTIVATION / ODDS_CONTEXT / OTHER | 可检查的statement、supporting references、FACT/ANALYSIS/SPECULATION；没有证据不写成FACT |

Freshness原规则保持：LINEUP 2h、EXPECTED_LINEUP 24h、INJURY/SUSPENSION 48h、SCHEDULE/REST/FORM 7d、MOTIVATION/OTHER 24h、ODDS_CONTEXT 1h；依据source publication判断，不拿ingestion替代。未知publication保持UNKNOWN；STALE不能作为决策evidence。具体类别修正还须满足冻结sidecar规则，不能仅因文件存在就声称confirmed/active。

`evidence-import`返回的是binding：包含snapshot ref和投影到V4的FootballEvidence ref。**V4的`evidence_refs`用packet内的FootballEvidence IDs；correction sidecar的`evidence_snapshot_ids`用对应snapshot IDs**，两套ID不能互换。入口负责维护映射，用户无需手填。

材料应在首次prepare前收齐并绑定。已封存analysis不能被回填；legacy THREE_WAY还要求原source cutoff一致。若新事实需要新的model/live来源上下文，接入设计必须给出合法新分析路线，不能只把已有analysis的cutoff改晚。

## 6–7. 生成 packet 与回传 llm_review

### 正式packet【现有命令；真实source接入尚未就绪】

`prospective prepare`的request包含`request_key / epoch_id / analysis_id / slate_key / slate_date / budget_fen / evidence_binding_ids`，可有合法`supersedes_run_id`。它不接受用户指定prepared/cutoff时间。

仅当返回的run可准备、`packet`非空且source/epoch均符合批准路线时，才把输出的`analysis_packet.json`（原样V4）和`analysis_packet.md`作为正式review输入。MD是解释/证据companion；IDs、hashes及机器契约以JSON和ledger为准。

**不能只看进程exit code。** v1.0可以成功返回一个`UNAVAILABLE` run；CLI只有在`run.packet`非空时才写packet文件。服务也可能已保存独立的mm packet：不能绕过prospective状态，直接用`market-v2 packet-export`得到的文件或昨日残留文件冒充本轮真实packet。

在adapter未获准前，operator入口只可提供单独命名的资料收集清单，例如`fact_collection_brief.md`【设计】，明确“非ANALYSIS_PACKET、非预测、非lock输入”。

### 网页GPT回传要求

把同一轮packet JSON/MD与本地提供的V4 schema、context/evidence映射一起交给网页GPT，请其回传：

1. **`llm_review.json`**：严格`LLM_REVIEW_V4`，原analysis/packet/context IDs及hash，全market-unit覆盖、原canonical顺序；绝对`p_llm`完整分布及Decimal字符串、confidence、scenarios/counter-scenarios、limitations和合法evidence refs。它不是delta，也不包含下注操作、预算或SP覆盖字段。
2. **`correction_reasons.json`**：独立`CorrectionReasonV1`数组，按packet顺序覆盖每一unit；含match、market、review_context_id、canonical类别、assertion_class、rationale、已有snapshot refs。不要把这些新增字段塞进冻结的V4文件。

不足信息应按现有UNAVAILABLE/failure_code语义返回，不编数补齐“看起来有效”的概率；MODEL_UNAVAILABLE必须遵守V4对应规则。无review时可由operator明确选择不用，走原有abstention路径；错误文件、错packet、超时回传不自动当作“自愿不用review”。

下载/保存回传原bytes，先做本地validation，再执行lock。不得为了通过hash校验手改packet、补造context ID、改写GPT原review；校验失败保留错误记录，要求重新生成正确文件。入库后的更正用新工件/新合法流程，不覆盖旧bytes。

## 8–9. 执行 prospective lock 与确认时间

**【真实链路阻塞】当前v1.0真实run不能执行此闭环。以下是接入获准后必须保留的操作语义，而非现在可启动的真实指令。**

- 入口显示完整slate、当地时间+UTC、最早相关kickoff、固定预算选项、所有可用/不可用层与即将使用的packet/review。用户明确确认“锁定观察方案”；不是下单。
- 调用原有`prospective lock`流程校验review及sidecar，按冻结fusion/Strategy/Return规则计算，最后由repository再次检查源、epoch与可信时钟并提交DecisionLock。
- `LockWorkflowRequestV1`字段为`request_key / run_id / strategy_requests / invalidation_reason`。普通用户不编辑strategy_requests；catalog/请求生成政策必须预先批准固定，不能遇到容量上限就偷偷截取top-N或改变风险规则。
- 必须读取ledger中的DecisionLock、`show(run_id)`及`audit(lock_id)`，核对同一run/epoch/dependency hashes及当前状态。输出文件存在、菜单显示成功或进程退出0都不是唯一凭据。
- 显示并验证`locked_at_utc < earliest_kickoff_at_utc`；冻结规则更严格：**`locked_at_utc + 60秒 < earliest_kickoff_at_utc`**。最早时间覆盖全部run相关比赛，不只selected tickets。60秒来自冻结policy，不能为操作迟到调小。
- Operator应提前给网页协作和本地处理留时间；日程提醒只是工作安排，不是新数学/风险参数。系统时钟异常、未知/变更kickoff、已知赛果或临近/超过截止均停止；不改电脑时间或传测试clock冒充赛前。
- 首次lock只点一次。若通信/输出失败，不推断事务没提交；按原request key查receipt/ledger，只有相同内容可exact retry。

### 资料变化与supersession

锁定前尚未prepare的草稿可人工纠错；prepare后的新事实不能塞回旧packet。v1.0既有supersession要求旧run已经LOCKED。现正式提出独立的**PRE_LOCK_REPLACEMENT_V1**草案：以追加事件保留并失效旧PREPARING attempt，新事实产生新的analysis/packet/run，仅最终有效LOCKED版本进入正式prediction evaluation，census保留全部attempt。该状态机尚未实现，等待Architecture Review；Preparation入口没有prepare/lock能力，也不会以假lock或换slate key规避这个设计关口。

对已LOCKED的错误输入，仅在新旧run全部仍满足赛前门禁时，建立引用旧run的新analysis/prepare并提交replacement lock；旧invalidation与新lock必须由正式事务同时完成。赛后只能追加赛果事实/修订/结算/报告，绝不能再supersede prediction。所有准备尝试、无效run及失败原因保留在审计和census中。

## 10–11. ResultObservation 与每日 settle / report / audit

1. 比赛结束后从允许的来源取得结果，人工核对match ID、FT状态及**常规时间含伤停补时、不含加时/点球**。无法证明口径、取消/延期/void等使用实际status/UNPROVEN，不猜0:0或退款。
2. `ResultImportRequestV1`含`request_key`和`ManualResultImportV1`：match、真实classification、source identity/reference/file/hash、verified_by/time、observed/available/published时间、status/regular-time语义、比分、rights/retention及可选source version/predecessor。
3. 使用epoch事前固定的result source identity；网页GPT可辅助查来源与口径，operator复核。系统记录本次真实receipt，不把录入时间说成provider历史发布时间。
4. 有更正时追加新source文件与新observation，填写正确`supersedes_observation_id`，有确实可证的source version才填写对应链。不覆盖旧结果、不伪造完整provider revision history。
5. `settle`由仓库选择该run全部相关比赛的最新合格source heads，不让operator挑选有利result IDs。缺失/unsupported保持null金额及对应reason；NO_BET与零stake也正常保留。
6. 新revision使当前报告显示`STALE_SETTLEMENT`时，追加新settlement及新report；旧报告保留原as-of/watermark和重放能力。
7. 每日`report`是整个epoch截至当前合法as-of的累计census，不是挑今日盈利比赛。入口可以附只读“今日处理摘要”【设计】，但不能替代/过滤正式报告。`audit`核查run、lock和报告的依赖完整性。

### 现有命令形状参考（维护者用；本文未执行）

`<db-url>`使用首次配置固定的SQLite URL；其余尖括号内容由入口读取既有回执填写。下列是已有接口的正确参数，不是宣布真实接入已可用。身份/SP/evidence/result等独立资料写入按其自身授权与输入门禁处理；真实epoch/prepare/lock及相应prospective结算/报告还必须先闭合C2，目前仍阻塞。示例文件名不是伪造工件ID。

```text
football-system live ingest-fixtures-manual --database-url "<db-url>" --archive "<reviewed-fixtures.json>" --raw-archive "<permitted-local-captures>" --reconciliation-output "<fixture-reconciliation.json>"
football-system live plan-slate --database-url "<db-url>" --input "<slate-or-sporttery-archive.json>" --output "<daily-slate-plan.json>"
football-system live ingest-sporttery --database-url "<db-url>" --archive "<sporttery-manual.json>" --kickoff-from "<UTC-from>" --kickoff-to "<UTC-to>"
football-system prospective evidence-import --database-url "<db-url>" --input "<evidence-request.json>" --evidence-root "<local-evidence-root>" --output "<evidence-binding.json>"
football-system prospective epoch --database-url "<db-url>" --input "<epoch-request.json>" --output "<epoch.json>"
football-system prospective prepare --database-url "<db-url>" --input "<prepare-request.json>" --packet-dir "<new-packet-directory>" --output "<run.json>"
football-system market-v2 review-validate --packet "<analysis_packet.json>" --review "<llm_review.json>" --output "<review-validation.json>"
football-system prospective lock --database-url "<db-url>" --input "<lock-request.json>" --review "<llm_review.json>" --reasons "<correction_reasons.json>" --output "<decision-lock.json>"
football-system prospective show --database-url "<db-url>" --artifact-id "<run-id>" --output "<run-show.json>"
football-system prospective audit --database-url "<db-url>" --artifact-id "<lock-id>" --output "<lock-audit.json>"
football-system prospective result-import --database-url "<db-url>" --input "<result-request.json>" --evidence-root "<local-evidence-root>" --output "<result-observation.json>"
football-system prospective settle --database-url "<db-url>" --input "<settle-request.json>" --output "<settlement.json>"
football-system prospective report --database-url "<db-url>" --input "<report-request.json>" --output "<epoch-report.json>"
football-system prospective audit --database-url "<db-url>" --artifact-id "<report-id>" --output "<report-audit.json>"
```

- `settle-request.json`仅需`request_key / run_id`。
- `report-request.json`为`request_key / epoch_id / as_of_at_utc`；日常入口使用本次合法观测时间，不向普通用户提供“挑一个最好看的历史截止点”的捷径。历史报告读取须保留明确as-of标签。
- `epoch-request.json`需seed plan、name/mode、预先声明的starts/ends、result source identity等；不是每天新建来清空坏结果。窗口前创建，配置/hash固定，关闭后另开未来epoch。
- 每次真实变化使用新的请求/输出文件，精确重试使用原request key及原bytes；不要覆盖上一次文件。旧`live --as-of/--decision-as-of`能力不应作为日常回填事件时间的入口。
- 没有激活的真实lock就不能进行对应真实prospective结算/累计表现；只有赛后结果文件也不能补出赛前预测。

## 12–13. 允许人工与绝对禁止事项

**允许人工**：确认每日候选范围和身份；依法取得材料；填写/复核SP、事实、source时间与结果；在批准预算集合中选择；上传packet、下载review；明确选择不用可选review；确认赛前lock；说明更正原因；处理备份/恢复。人工声明保持真实，不虚构独立reviewer、source publication或数据授权。

**封存后不覆盖，尤其赛后绝对禁止修改：**

- 已锁定P_market/P_quant/P_base/P_llm/P_final、SP、raw review及reasons、evidence/cutoff、ticket/multiplier/预算、profile/objective/risk与optimizer结果。
- locked_at、ingested_at等可信事件时间，原match-market范围、classification、epoch配置/窗口或历史receipt序列。
- 赛后改预测、补一个“赛前lock”、换日期/数据库/slate key规避锁定或重复计样本，删除失败run或只保留正修正/赢球报告。
- 用赛果调模型/fusion/EV/weights后，把同段数据称为验证；把synthetic fixture/回放标为真实样本。

赛后允许的变化限于**追加**新的ResultObservation、正确的result revision、由此产生的新settlement/report及操作审计；旧工件不动。程序不连接下注、支付或购买provider功能；不自动使用凭据联网，不恢复已删除Sportmonks restricted raw。

## 14. 数据位置与备份【设计位置，尚未创建】

建议把运行数据放到代码仓库外，避免混入Git或被代码工作树切换影响：

```text
D:\文档\xs\football_runtime\v1.0.0\
  install\                         冻结wheel/依赖与校验清单
  operator-config\                 已审核路径、来源/retention、配置/epoch引用
  db\production.sqlite             唯一获准的真实运行ledger（当前未启用）
  db\synthetic.sqlite              如另获准演练，必须单独隔离
  daily\<操作日期>\<slate-key>\
    inputs\                        原始结构化录入及经允许保存的来源材料
    requests\                      每次请求及稳定request key
    packets\<attempt-id>\          packet JSON/MD、发送说明
    reviews\<attempt-id>\          回传原bytes、reasons、校验结果
    artifacts\                     run/lock/observation/settlement/report导出
    audit\                         audit结果、错误/重试与日索引

D:\文档\xs\football_backups\v1.0.0\<UTC时间戳>\
  production.sqlite
  daily-files\
  backup-manifest.json
  retention-manifest.json
```

- 同一D盘备份是逻辑恢复副本，不是磁盘故障灾备。首次配置另选一块实际可用的离线存储，记录真实路径；本文不假定某个盘符存在，不配置自动云上传。
- 日常入口在调用任何命令前核对既存数据库路径及身份，路径错误时停止，不能悄悄新建一个空生产库；首次初始化属于单独确认的部署动作。演练库/真实库不得互相替代。
- 使用SQLite一致性backup API，或确认全部连接关闭后的完整备份；不能只复制正在写入的`.sqlite`而遗漏WAL。数据库和日文件的manifest需绑定run IDs、receipt watermark、备份时间及SHA256。
- 入库/锁定后和每日结束做可核验备份；源文件先持久化再交给写入操作。若lock已提交但导出/备份失败，保留已提交事实，标记`BACKUP_REQUIRED`【operator标签】，停止新的写入并按原receipt恢复，不能撤销lock假装未发生。
- 原始材料和备份同样服从retention。不得通过备份/恢复续期旧材料，更不能恢复已删除的restricted capture。准入前明确哪些结构化事实/工件可长期留存；如授权与append-only保留冲突，先不准入，不靠后改SQL解决。
- 恢复先在隔离副本检查完整性、hash与audit，核对最新日索引和receipt序列；不能回滚生产库来重新制造lock。丢失赛前凭证时如实记不可核验，不靠记忆重建。
- 备份成功/恢复演练证据是运行准备检查，不是模型performance证据。

## 15. 每日最终应留下的 artifacts

| 分组 | 必须保留的内容（有则按许可保存，不能伪造不存在的工件） |
| --- | --- |
| 运行锚点 | 冻结release/adapter身份、配置/epoch引用、operator、日/slate范围、UTC/当地时间显示依据 |
| 候选范围 | reviewed slate、daily-slate plan、全部候选及mapping/reconciliation、未处理/排除原因 |
| 价格与事实 | 原始SP/archive与证据、ingestion receipts、EvidenceSnapshot/Binding IDs、freshness/unknown状态、rights/retention索引 |
| 模型输入谱系 | 获准后所用release/state/config/training-data及market来源引用；不可用时保留缺项原因 |
| 文件协作 | 正式packet JSON/MD、发送索引、回传review原bytes、独立reasons、validation/error记录 |
| 决策 | ProspectiveRun、请求receipt、DecisionLock、audit；如有赛前replacement，旧run/lock、invalidation与新lock全保留 |
| 结果 | 每场ResultObservation、来源证据、normalized result引用及全部revision链 |
| 结算与报告 | 每次settlement、全epoch report、as-of/watermark、run状态及audit；包括NO_BET、失败、缺失、unsupported、invalidated、stale等 |
| 操作与恢复 | checklist回执、重试/中断记录、日索引、文件SHA256、DB一致性备份与retention/恢复验证记录 |

IDs-only导出不替代数据库及完整source graph备份。UNAVAILABLE/未锁定日只能留下实际准备/失败记录，不能补齐假的lock、settlement或performance report。所有尝试进入操作索引；正式epoch报告仍由仓库完整census生成。

## E. 是否需要少量 post-1.0 glue code

**需要；INPUT_PREPARATION薄包装现已获准实现，真实source/epoch桥和PRE-LOCK replacement仍只做设计。** 分清两类工作：

| 工作 | 最小职责 | 可否只用薄包装完成 |
| --- | --- | --- |
| 本地operator入口 | 一个启动入口/菜单，固定环境与路径；读取冻结版本、schema、已批准设置 | 可以，独立于数学 |
| 人工输入辅助 | 表格/JSON映射、来源文件选择/本地hash、UTC展示、人工复核与字段校验 | 大部分可以；真实SP/market不支持的类型须明确阻塞，不能用synthetic adapter代替 |
| 流程与回执 | 按原API编排、IDs/双类evidence refs、稳定request keys、幂等重试、正确读取业务status | 可以；不直写pv_*，不提供skip-lock/force/backdate开关 |
| 网页协作 | 导出包/说明、检查完整回传，保存原bytes并把错误翻译成人能懂的提示 | 可以；不调用LLM API、不自动补造事实/概率 |
| 本地归档 | 一致性备份、retention清单、只读日摘要与恢复核验 | 可以；不改settlement/report数学、不选择性过滤epoch |
| 真正生产source/epoch桥 | C2要求的classification、数据/模型谱系、配置bootstrap及可接受的真实执行路径 | **不能承诺仅靠薄包装完成**；须独立审核接入设计，超出当前入口脚本工作 |

建议的最小operator状态记录【设计】：日/slate-key、operation、输入文件SHA、request key、run/epoch/packet/lock refs、core status/reason、对应检查项和backup manifest。它是可重建的操作索引，不是另一个DecisionLock、资金账本或性能指标引擎。重复点击、进程崩溃和提交后导出失败，均先读原receipt再决定下一步。

对非工程用户统一显示：**“缺什么 / 为什么不能继续 / 去哪个文件或步骤补充”**。必要条件不满足就停止对应写入；展示UNKNOWN与合法abstention不等于故障，也不能把技术错误显示成NO_BET。现有合法NO_BET按原算法保留。

### 后续实现审核应要求的最小证明

以下真实activation/replacement证明仍属于后续验收设计；本轮已实现入口的隔离合成Preparation测试另见`daily_operator_v1_contract.DRAFT.md`：

1. v1.0冻结文件/五个已接受hashes不被便利脚本修改；如新执行实现需要新身份，单独声明而非冒用旧hash。
2. 真实classification/来源合法性从输入到报告连续可验证；synthetic、无有效state、缺必要分布、错match/market都不能通过真实资格门禁。
3. 真实epoch bootstrap在未来窗口前固定配置；不通过synthetic seed或回溯时间补开。
4. packet→review→evidence refs与独立sidecar逐项绑定；新事实/错文件/浮点数/晚回传失败时不静默改写。
5. 跨kickoff、时钟异常、重复点击、同epoch重复prediction、赛后supersession都fail closed；时间由正式repository记录。
6. 断电/提交后导出失败可按原receipt恢复，backup/restore保留完整图与序列，不能创造第二条真实ledger历史。
7. Result revision、STALE_SETTLEMENT、missing/unsupported/NO_BET及全census保持；预算、weights、风险与数学goldens不变。
8. 无自动HTTP、凭据购买/订阅/下注；网页文件协作、材料使用与备份保留边界清楚。

## 供网页 GPT 审核的决策点与停止点

请审核：是否接受先做INPUT_PREPARATION；首批事前声明的联赛/market/时间范围；允许使用的来源与当前模型state；C2真实桥/epoch bootstrap的具体边界；网页GPT两轮文件协作；未锁定PREPARING遇到新资料的处理；operator入口和备份/retention方案。

当前结论保持：**真实闭环不可启动，adapter仍UNAVAILABLE，performance仍INSUFFICIENT_PROSPECTIVE_SAMPLE。**
首轮仅交付本文；本轮按明确授权增加INPUT_PREPARATION独立入口及三份后续设计。真实decision adapter和PRE-LOCK replacement仍只设计不实现，v1.0数学/标签不变；停止，等待网页GPT的Production Activation Design Review。

## 核对依据（v1.0.0本地只读代码/合同）

- `src/football_system/interfaces/cli.py`：`live plan-slate`、fixture/manual SP、prepare/run-analysis参数与pinned production入口。
- `src/football_system/infrastructure/files/daily_slate.py`：真实轻量slate输入、review/来源校验及空candidates。
- `src/football_system/infrastructure/providers/real/sporttery_manual.py`：V2 JSON/CSV、Decimal字符串及THREE_WAY限制。
- `src/football_system/infrastructure/providers/exact_market_fixture.py`、`domain/market_analysis.py`：synthetic-only边界。
- `src/football_system/application/prospective_requests.py`、`application/prospective.py`、`interfaces/prospective_cli.py`：现有请求/命令、packet输出、review/lock编排。
- `src/football_system/domain/prospective_evidence.py`、`domain/prospective.py`、`domain/review_v4.py`：typed证据、结果、V4和sidecar字段。
- `src/football_system/domain/services/prospective.py`、`infrastructure/database/prospective_repository.py`：真实prepare门禁、cutoff/lock/supersession、epoch seed及完整census/replay。
- `config/prospective_policy_v1.json`、[已接受合同](prospective_validation_v1_contract.md)、[最终验收报告](phase_10_final_acceptance_report.md)。
