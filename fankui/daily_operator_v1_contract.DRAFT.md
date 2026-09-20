# DAILY_OPERATOR_V1 — INPUT_PREPARATION contract DRAFT

**Preparation实现范围已授权；本契约待Production Activation Design Review。** 不实现真实decision adapter或PRE_LOCK_REPLACEMENT，不更改v1.0.0冻结文件、wheel、版本、算法、配置或risk。

## 1. 唯一入口与固定banner

仓库根目录`daily.cmd`启动新增的`scripts/daily_operator.py`。入口只接受交互菜单，不接受任意CLI子命令/路径/force/skip/backdate参数。优先使用仓库`.venv`，缺少时使用系统Python；启动验证实际加载的frozen 1.0.0和五个冻结hashes，版本/依赖不符明确停止，不联网安装。

这是仓库外置glue，不改已有wheel/entry point：需要已配置的Python 3.12+、v1.0依赖及本地Git；脚本只从该仓库冻结`src`加载核心。没有依赖时交维护者按既有发行材料配置，不自动下载。

安装manifest另绑定`DAILY_OPERATOR_GLUE_LF_V1`代码hash（daily.cmd及两份新脚本，LF规范化）。它与冻结的prospective implementation hash分开；glue改变后旧安装停止并要求维护审核，不能静默沿用旧运行身份。

```text
MODE = INPUT_PREPARATION
PRODUCTION DECISION = UNAVAILABLE
REAL PERFORMANCE = INSUFFICIENT_PROSPECTIVE_SAMPLE

1 今日准备
2 导入竞彩SP
3 导入赛前事实
4 查看缺项
5 数据/文件校验
6 备份
0 退出
```

即使资料完整，banner也不改变。既有核心provider/decision/model入口不出现在菜单，不提供参数透传或任意Python/shell执行。只按固定白名单调用原有离线input services；不导出analysis_packet，不生成epoch/run/lock/settlement/performance。

## 2. 首次初始化与数据库身份

默认位置固定为代码仓库同级：

```text
football_runtime\v1.0.0\
  operator-install.json
  db\production.sqlite
  db\synthetic.sqlite
  inbox\<package-name>\
  ops\p\<operation-hash-prefix>\
  ops\s\<operation-hash-prefix>\

football_backups\v1.0.0\<backup-id>\
```

首次初始化显示完整绝对路径、两个数据库及INPUT_PREPARATION含义，要求输入`INIT <完整runtime路径>`，否则零创建。必须是新、干净目录；现有部分初始化、丢失manifest或DB时不“修复性创建”，而是RECOVERY_REQUIRED交维护者核验。

仅初始化阶段通过冻结Alembic迁移建立两个独立文件。安装manifest绑定绝对位置、installation ID、每个DB的不同SQLite application_id及文件身份；后续操作只用SQLite `mode=rw/ro`打开已存在文件，禁止隐式mkdir/new DB。日常检查head必须为`5b748fa162ed`，不自动upgrade。

物理目录采用短名`ops/p`、`ops/s`及operation SHA前20位，避免Windows路径长度放大；回执始终保留完整64位operation ID并核验目录碰撞。冻结manual fixture adapter产生的允许本地副本放在对应operation的`c/`中，其字节/hash也纳入校验和retention。

Production与synthetic的文件/operation目录/身份分开。公开菜单只使用production；测试代码在临时目录显式选择synthetic。改名、复制synthetic为production、换路径、软链接/junction/hardlink或DB替换均不能当作原安装继续。主机管理员可更改文件不等于外部公证；受控恢复必须独立核验，不提供自动恢复/覆盖生产库按钮。

## 3. 输入包（operational envelope，不改变v1.0业务schema）

每包放在`inbox/<package-name>/`，包含`package.json`、一个原有格式入口文件及其相对引用的证据。包名/相对路径均需contained；绝对路径、`..`、Windows alternate streams、links、credentials类文件以及已永久关闭的restricted capture根不接收。

```json
{
  "schema_version": "DAILY_INPUT_PACKAGE_V1",
  "kind": "SLATE",
  "file": "slate.json",
  "rights_reference": "operator reviewed permitted source and retention",
  "retention_until_utc": "<真实的保留截止UTC>",
  "durable_ledger_authorized": true,
  "local_backup_authorized": true
}
```

`kind`闭集为`SLATE / FIXTURE / SPORTTERY / EVIDENCE`。日期占位符必须换成真实声明；这只是包格式示意，不是有效业务输入。保留/使用声明必须真实，不由程序授予数据权利；没有durable structured-ledger许可不得把资料写进append-only ledger。

- SLATE：`SPORTTERY_DAILY_SLATE_INPUT_V1`或SPORTTERY_MANUAL_ARCHIVE_V2 JSON/CSV；只生成NO_ANALYSIS计划及精确UTC分桶。
- FIXTURE：既有`REVIEWED_FIXTURE_MANUAL_ARCHIVE_V1`；作为菜单1可选身份准备子步骤，复用冻结manual fixture导入，不引入网络。
- SPORTTERY：仅`SPORTTERY_MANUAL_ARCHIVE_V2`、THREE_WAY；原JSON/CSV、reviewer、source-file SHA不改。
- EVIDENCE：原`EvidenceImportRequestV1` + `MANUAL_VERIFIED_IMPORT_V1`；生产要求REAL_SOURCE_DATA，测试库要求SYNTHETIC；复用源文件实际SHA、system receipt及freshness语义。包retention不得晚于claim retention。

JSON拒绝重复key、float/NaN/Infinity及extra envelope字段。Price/probability使用原契约的Decimal字符串，byte/文件数量限制只是I/O保护，不改数学。入口先检查包许可/retention，再读取source bytes；不把过期文件重新读一遍再宣布过期。

当前I/O界限：每个输入/操作文件4 MiB、每包总16 MiB、最多64个目录条目。可接收JSON/CSV、文本/Markdown、PDF、PNG/JPEG/WebP、HTML；不能把`.raw`当新输入直接恢复。Binary图片不执行/OCR，人工先复核再交原格式导入。

## 4. 六项行为

| 菜单 | 精确范围 | 禁止隐含动作 |
|---|---|---|
| 1 今日准备 | 可选审核fixture身份导入；本地slate校验/identity plan；按exact UTC kickoff分桶并封存操作回执 | 不请求The Odds API，不创建analysis/packet/run；不按赛果分桶 |
| 2 导入竞彩SP | 调用既有SportteryManualArchiveCaptureProvider和LiveSportteryIngestionService | 不实现新SP/normalization/consensus数学；不猜ID/部分报价补齐 |
| 3 导入赛前事实 | 调用既有verified manual reader和prospective evidence-import repository路径 | 不调用ProspectiveService.prepare/lock；UNKNOWN不升级为confirmed |
| 4 查看缺项 | 本地资料/数据库计数、最近准备回执、未解决identity/资料状态；production adapter始终UNAVAILABLE，model pin资格未核验 | 不把“表里有记录”当fresh/完整/可用model；不输出真实绩效 |
| 5 数据/文件校验 | DB身份/head/integrity/FK及操作文件hash，过期材料只标EXPIRED_DELETE_ONLY、不再读bytes | 不修复DB、不重算预测、不生成fake audit pass |
| 6 备份 | SQLite一致性backup API、操作回执与仍在许可/retention内的文件；写manifest及hash验证 | 不复制活动DB主文件代替snapshot，不恢复旧restricted raw，不自动覆盖production |

菜单1中的完整准备仍返回`NO_ANALYSIS`。分桶包含规范UTC时间、对应候选/已解析match IDs、slate hash；任何一秒之差必须分开，空slate保留空结果。身份不清就列缺项；SP导入带issues的正式回执显示BLOCKED/ISSUES，不能显示可以决策。

注册了fixture不一定已有SPORTTERY provider namespace映射。遇到SP identity issues，入口展示原有reconciliation报告，不自动推断/注册别名；维护者须按既有身份审核流程完成明确映射，再以新的经审核快照重试。该首次输入映射准备不是模型训练，也不是DecisionLock。

## 5. 原子边界、精确重试与失败

Operator对本地操作使用单写者文件锁，防止双击两个入口并发。每个输入包按内容hash/安装DB身份/kind生成operation ID，保存不可覆盖的intent、源文件副本/manifest、输出与receipt。Core写入仍由原repository事务负责，operator journal不是DecisionLock。

提交前发现格式/路径/retention错误不执行core。普通失败保留failed记录；存在intent但缺最终receipt时显示`RECOVERY_REQUIRED`，停止后续写入，不猜测事务没提交。没有force/unlock/skip菜单，维护者需核对原core receipt和高水位后才能恢复。在有效期内校验同包字节后，精确重试返回原operator回执而不重复入库；过期包拒绝再次导入，只可按既有operation ID读取已保存回执，不重读raw。

系统时间不得倒退到上次operator记录之前；公开入口无clock override。所有新准备/事实必须确实仍赛前；缺失已知kickoff不能伪装赛前。错误只显示稳定code/下一步提示，不输出输入中的凭据或完整异常payload。

## 6. Backup / recovery

- 默认备份目录与runtime分离并绑定安装身份；同盘不等于灾备，离线第二份由operator另外确认。
- SQLite backup得到一致数据库，复制同一operator串行边界内的回执和文件；所有文件SHA写入backup manifest，最后生成COMPLETE标志。partial备份不是可恢复快照。
- 出现未完成backup目录时，校验/缺项报告列出并停止新的输入写入；不能把重试生成的另一份COMPLETE当作旧partial已经解决。维护者核验后处置，入口不提供force或自动删除现场。
- Manifest另绑定数据库application_id、pv receipt watermark、每个operation的retention deadline、未收尾操作及operator_state；复制文件再次与原receipt hash核对。COMPLETE只表示备份步骤完成，若operator_state为RECOVERY_REQUIRED仍不能直接恢复后继续写入。
- 过期包payload及manual raw衍生副本不读取/复制，manifest只列原hash、expiry和EXPIRED_DELETE_ONLY；允许长期保留的ledger/操作元数据不冒充原raw。既有备份中的有期限材料同样须按retention处置；程序不提供从备份恢复已过期资料的捷径。
- 恢复先在隔离位置核对manifest、hash、DB integrity/FKs、core receipts与最新operator journal，确认无丢失/分叉。不是把旧DB拷回原路径继续写；文件身份变更会阻止普通入口启动，受控重新绑定/清理需维护审核。
- 第一次初始化中断、intent未收尾、文件变化、DB被替换、clock回退、恢复高水位不明时一律停止写入。没有自动“从头再来”的第二个production库。

## 7. 验收范围与状态

本轮可执行的测试必须在临时目录、synthetic数据库、人工构造材料中完成，网络连接在测试中阻断；不在默认真实目录执行初始化/导入，不使用真实provider/LLM，也不训练模型。

需覆盖：未确认零创建；初始化exact paths和双库隔离；错路径/缺DB/复制DB/links/head不符；六项白名单及所有decision/force/backdate参数拒绝；真实SP/evidence格式校验与原API调用；exact kickoff分桶；相同包retry；intent不确定中断；hash变化/expiry；SQLite backup含WAL已提交数据及partial/过期保护；冻结v1文件/五hash不变。

与两个activation/replacement草案共同提交网页GPT审核。INPUT_PREPARATION可用不代表Production Decision或真实performance已启用。

## 8. 本轮实现/验证记录

- 新增`daily.cmd`、`scripts/daily_operator.py`、`scripts/preparation_inputs.py`；没有更改v1.0 core、migration、config、wheel或package version。
- 新增27个operator验收用例，全部使用临时目录和synthetic库/自造材料；provider/LLM网络连接在测试中禁止（Windows asyncio的本地socketpair唤醒管道不是HTTP）。
- 与原有fixture/SP contract、daily-slate及market consensus回归一起执行：**90 passed**。Ruff及新增文件compileall通过。
- 覆盖明确确认、缺DB不重建、双库复制/身份混淆、错head/hardlink、禁止命令、UTC等价与1秒/1微秒分桶、SP未解析issues及明确审核后的导入、UNKNOWN evidence、重复请求、提交后失败、clock regression、WAL一致备份、hash篡改、过期不读取、partial backup与跨安装journal。
- 数据库连接释放与Windows短路径处理仅修正在新glue中；没有改动冻结adapter行为。SP namespace身份审核继续使用旧正式API，不在glue里猜测。
- 默认`football_runtime\v1.0.0`和`football_backups\v1.0.0`本轮未创建；没有实际初始化真实库，没有真实provider/LLM HTTP、模型训练或下注。
- 仅实现Preparation Mode。`PRE_LOCK_REPLACEMENT_V1`及真实decision source/epoch binding没有代码实现，等待本次Production Activation Design Review。
