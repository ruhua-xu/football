# OpenFootball candidate：upgrade / downgrade snapshot 差异报告

基线candidate：`3da997b3e73bf1d182ce1f067439f5904e26e8f3`；本轮仅CI、operator版本绑定和验收适配。
以下数据库均由pytest在隔离目录中创建，数据明确为synthetic。**未对真实production库执行upgrade/downgrade或写入**。

## 1. CI #45的原始差异

### 旧代码字节冻结测试

`test_prospective_upgrade.py::test_v090_domain_application_configuration_and_migrations_are_byte_frozen`将当前`application/quant_integrity.py`直接与v0.9.0比对，遇到已审核的4行`run_openfootball` additive入口。

这不是旧Elo/quant计算分支被改写。修订后的测试仅扣除独立、逐字声明的这一hook，要求其恰好出现一次，再逐字比较全部原domain/application/config/migration文件。没有忽略整个文件，也没有放宽任何模型或数据验证。

### 跨revision降级测试

`test_real_bridge_upgrade.py`从current head 7d请求降到5b：

1. 7d→6c：OpenFootball新表为空，按7d既有contract允许移除该空扩展。
2. 6c→5b：旧rb ledger已有数据，旧migration拒绝。

原断言把操作前7d的完整snapshot与第二步拒绝后的6c snapshot直接比较，导致合法移除的空ofp对象也被认作变化。Alembic多revision操作不是一个跨所有migration的原子事务。

现在显式验证两个边界：先执行允许的7d→6c并证明**唯一变化是空OpenFootball扩展**；再捕获6c snapshot，执行被拒绝的6c→5b并要求完整snapshot不变。没有仅更新一个expected值来掩盖变化。

## 2. 真实v1.1.0 populated升级证明

测试：`test_genuine_v110_populated_upgrade_exact_preservation_and_downgrade_contract`。

- 使用`git archive`导出正式v1.1.0 commit **`5ed940a8af8077be80549603a2da38aea77fc1bf`**。
- 在独立进程从导出的旧src导入，断言实际module origin及`__version__=1.1.0`。
- 使用旧migration和旧应用服务创建非空mm/rb等synthetic工件，初始head为6c859ab273fe；不是拿新models假装旧数据库。
- 随后仅升级到7d96abc3840f，并执行Alembic check、旧program replay及完整schema/row/index/FK/JSON比较。

定向验证结果：

| 检查对象 | 实际数量 / 结果 |
|---|---|
| 原有tables | **312** |
| 原有rows | **1399**，逐表row multiset完全不变 |
| 原有schema objects（含indexes/triggers） | **2328**，旧DDL原始字符串完全不变 |
| 原artifact_json值 | **141**，原字符串逐项不变，不parse/re-serialize冒充字节相同 |
| 原artifact JSON汇总SHA256 | `598cc656c32e6f37f3206ac6a0ae4d2e9e40c9fe190efb8df3fe7dedf7936641` |
| 原column/foreign-key/index结构 | 全部保持，包含PRAGMA table_xinfo、foreign_key_list、index_list/index_xinfo |
| 原program通过新reader重放 | PASS，与旧程序产生的canonical JSON一致 |
| 空ofp扩展降回6c | PASS，完整旧snapshot恢复一致 |
| ofp加入测试工件后降回6c | REFUSED，完整snapshot及7d head保持不变 |

## 3. 六类差异分类

| 类别 | 结论 |
|---|---|
| 1. 合法additive表/index/trigger/migration | 恰好新增6个ofp表、10个PK/UNIQUE autoindexes、15个ofp triggers；无额外view或其他对象。head由6c到7d是明示的唯一migration变化 |
| 2. 旧表DDL字节变化 | **0**。旧DDL一律原字节字符串比较，无通用SQL normalization |
| 3. 旧artifact JSON变化 | **0**。141个artifact_json原字符串及所有旧rows完全一致 |
| 4. 旧source/config代码变化 | CI #45差异为已审核的精确additive入口；本修复仅更新测试的精确projection。`src/`、`config/`、旧新migration和pyproject业务文件本轮未改 |
| 5. SQLite/Alembic/DDL排序差异 | 修复过程中新增的更严格DDL核验发现两个CHECK clauses的发射顺序不同。只对**新增表**规范化table-constraint顺序与非literal空白，并另核验每个实际PK/UNIQUE index；旧对象仍逐字比较 |
| 6. 真正migration regression | 在真实v1.1.0升级与显式revision边界内未发现旧数据/DDL/工件回归。原多步降级失败源自比较边界错误；7d migration实现未被改写 |

新增表：`ofp_artifacts`、`ofp_canonical_entities`、`ofp_operations`、`ofp_parent_links`、`ofp_phase_slots`、`ofp_source_records`。
新增trigger集合与已审核`openfootball_production_triggers()`逐项相等，其中`trg_ofp_legacy_training_forbidden`虽附着在旧training表上，但属于明确新增的保护；没有替换旧trigger。

## 4. Normalization的精确边界

- row顺序按完整序列化row排序，保留重复次数与BLOB原字节的hex表示。
- schema/index枚举按名称索引，不将反射遍历顺序视作身份。
- **所有旧DDL、old rows、old JSON、old constraints/indexes均精确比较。**
- 仅新增表：保留column顺序、类型/nullability、约束名称、完整表达式及quoted literals；只排序同一表内table-level constraints。
- 新PK/UNIQUE索引还核验实际数量、origin、unique/partial、列顺序、排序方向与collation；未因DDL中出现UNIQUE就忽略实际index。
- 新trigger只去掉SQLite不保留的`IF NOT EXISTS`和非literal格式空白，其余SQL一致。
- 负例已验证：删除旧index、删除新constraint index、放宽CHECK、删除旧row都会失败。

未使用“忽略constraint/index”“忽略整个schema”“统一重写artifact JSON”或更改snapshot常量的方法。

## 5. 修复范围及冻结

7d96abc3840f migration、OpenFootball资格/生产binding、Elo参数/算法、612/13/599数据、canonical mapping、rights/authority和training window均未修改。

已审核工作区的原始文件bytes未变，其业务实现身份仍为：

`package:128a4737803ab9c0998038038c86790e7c7b21b0df5cf72a214f70d4754c323b`

隔离验证使用从同一Git基线构建的干净worktree。Git checkout的CRLF/LF转换使该目录的原始byte身份为`package:e4fd89295082ab0a140bdf2256808fc94ef606b61e4cf3a3197a61119769daf1`。所有tracked `src/`文件已逐项验证：差异仅为CRLF/LF，没有其他byte变化；Git业务blob没有变动。完整列表记录在`upload/openfootball-ci-repair/full-gates-environment.json`。两种原始byte身份没有被冒充为相同，也没有修改hash算法或真实准入工件；新V2安装绑定其实际执行文件身份。

`scripts/real_bridge_acceptance.py`的验收summary改为读取测试数据库的实际`alembic_version`，不再硬编码旧6c head。这仅纠正验收元数据；隔离wheel的baseline/permuted验收均检查实际7d数据库，原业务计算与artifact比较保留。

新operator使用明确V2 installation/software identity，不将旧V1安装改成candidate。真实production仍为旧安装+新head的维护BLOCKED状态；未通过downgrade或修改operator-install绕过。

本报告证明的是软件兼容性与迁移完整性，不是新training、真实prediction或performance结果。目标状态继续为**TARGET_WAITING_FOR_SPORTTERY_SLATE**。

## 6. 完整本地复验

在干净候选worktree执行完整pytest（6个本地worker、无测试排除）：**2911 passed / 1 skipped / 0 failures / 0 errors**，2912个unique cases；唯一skip是Windows上不适用的POSIX dir_fd/O_NOFOLLOW测试。完整用时5380.90s。

完整suite再次生成同一旧artifact JSON汇总hash的genuine-v1.1.0升级证明；34个operator测试全部通过。Ruff、compileall、fresh Alembic upgrade/check、wheel build、isolated-wheel E2E及whitespace检查均通过。wheel显式资源由86增加至87；固定tzdata 2025.2 / IANA2025b的真实安装、TZif hash与CET/CEST转换通过。

完整证据索引：`upload/openfootball-ci-repair/README.md`；最终本地门禁：`verified-local-gates.json`；新candidate CI结果另行按exact commit记录，不能用本地成功替代CI。
