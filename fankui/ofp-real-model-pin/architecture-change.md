# OFP → REAL_MODEL_PIN：最小兼容设计（实现前）

基线：v1.2.0 / `8b7cfb3ac2e416a5c3c8f5046150245ab076f490`。
顺序证据：先运行三项vertical regression，结果**3 failed / 0 setup errors**；此前没有src/migration实现变更。`red-baseline.xml`记录G1、G2 reader及legacy FK阻塞。所有fixture均在独立synthetic DB，生产仅由测试外的文件hash/identity监控，不建立生产SQL连接。

## A. 为什么当前schema不能合法表示OFP pin

OFP release/state binding存在`ofp_artifacts`，其state/analysis逻辑ID不属于legacy表。`rb_model_pins.release_id/model_state_id/source_analysis_id`各自FK到legacy release/state/analysis。当前reader、replay和CLI还分别只读legacy、要求6c。

把OFP ID塞进旧列、复制成legacy state、改provenance或传test_state均不能形成合法的OFP pin。

## B. Additive schema / repository方案

1. 新增typed model source：`LEGACY`与`OPENFOOTBALL`。旧请求/旧pin缺省等价LEGACY；序列化时省略缺省source字段，保持既有canonical JSON/hash/request identity不变。
2. `ExistingPinnedModelAccess`仍是唯一正式verified入口，按typed source调度reader；上层消费统一、不可变的verified descriptor（state、configuration、lineage、release/authority hash、competition/season/scope及source refs）。旧`verify` tuple接口保持兼容。
3. OPENFOOTBALL source仅引用明确的release artifact ID/hash与state-binding artifact ID/hash。不得信任bare ID，必须从OFP图推导并验证logical state/analysis ID和完整父链。
4. 新增`ofp_real_model_pins` companion表：pin ID受FK约束到`rb_model_pins`，release/binding ID受FK约束到`ofp_artifacts`，另保存并核验精确hash/type投影。它不保存Elo state或training事实副本。
5. 旧`rb_model_pins`表/列/FK/trigger不删除、不重建。OFP pin的legacy专用列为NULL，只有typed OPENFOOTBALL描述符及必需companion才允许完整seal；这不是无约束NULL逃生口。Legacy pins继续使用原列和原FK。
6. 复用既有`rb_artifacts`、`rb_receipts`、`rb_model_pins`与complete seal生命周期。Repository原子写入companion关系后才seal；重放验证其精确投影。`REAL_MODEL_PIN_V1`仍由正式repository生成，不手写pin。

## C. Legacy compatibility

- 原有legacy API缺省、canonical JSON、artifact hash及request hash保持一致；新增source字段不会给旧wire补一个null/default。
- Legacy live-reader、synthetic test_state路径保持各自原规则；test_state仍只允许原有synthetic程序，不能进入OFP verified-source分支或真实程序。
- 旧rb表DDL、FK、indices、triggers及已存pin bytes不改写；升级测试必须证明非空旧库和旧pin可读/可audit。
- Program → MODEL admission → model-pin的顺序原样保留，epoch/run/lock/math不重设计。
- CLI允许经过明确验证的6c、7d和本扩展head，未知head拒绝；OFP写pin必须有扩展schema，不能把7d当已完成扩展migration。

## D. OFP integrity

Reader必须验证：DATA_BINDING/current rights、exact model approval/review/pinned authority及有效期、release/state binding/target plan、全部所需parent refs、state/训练数据/config hash、canonical competition/season及complete target scope、target-training disjointness。

复用现有OFP资格、facts与structural replay实现；不新增fit参数。创建pin时仍验证当前future target及完整live-input proof。读取/重放已封存pin时，验证其原绑定时点的输入与状态、并重新检查当前authority/retention，不能用缓存绕过到期，也不能把后来已开赛本身当历史pin篡改。

数据库级新增append-only/replace拒绝、typed/hash/parent关系投影及seal completeness guards；companion必须对应OFP typed pin，不允许挂在legacy pin上。任何缺失或不一致均整笔回滚/保持既有拒绝receipt规则。FK始终开启。

## E. Migration

需要新的additive revision：暂定`8ea7bcd49510`，down_revision=`7d96abc3840f`。仅新增companion表及其完整性guards，不移动或复制OFP/legacy历史工件。

Fresh/7d upgrade/check必须通过。空companion允许downgrade并恢复原7d对象；存在OFP pin时拒绝downgrade，保持head/全部数据不变。Legacy-only populated DB在没有OFP companion时可降回7d，旧pin仍可读。6c兼容另行验证。

## F. Hash / operator / release影响

Elo参数、P_market/P_quant/P_llm/P_final、EV、Strategy/Return/Settlement及prospective五/六个冻结数学hash不变。RealBridge adapter implementation hash、package raw-byte implementation revision将因新增reader/persistence而改变，必须真实记录，不沿用旧bridge hash。

源码operator glue如需适配candidate head/projection，必须显式区分已发布1.2软件语义与候选源码；不重绑正式runtime。实际开发在独立`feature/ofp-real-model-pin` worktree，正式主工作区、.venv、DB、v1.2.0 tag/wheel均不改。

本任务不release、不部署。因为扩展公开model-source合同和持久化schema，预计更适合**v1.3.0**而非单纯v1.2.1；最终在实现/回归后给出版本建议。当前package版本不作为已发布新版本声明。

## 验收边界

必须得到独立synthetic DB内由正式API生成的`REAL_MODEL_PIN_V1`，`real_model_pin_created=true`，provenance仍SYNTHETIC_SOFTWARE_ACCEPTANCE；不能只返回OFP候选描述。随后停止，不创建任何真实production program/pin/epoch/run/lock。

负例覆盖缺审批、wrong approval/hash、expired authority、wrong competition/source、missing parent、binding-release冲突、target-training交集、缺program/admission、admission source不符、unknown head、OFP篡改及legacy regression。

## 实现/回归补充

- OFP reader与RealBridge共用调用方session/transaction，拒绝不同DB，避免嵌套SQLite BEGIN及读写TOCTOU。
- Genuine v1.2.0旧pin/anchor的canonical JSON、hash与replay保持；历史anchor仅允许已发布aa55…或当前bridge implementation，不重写旧identity。
- 候选head为8e；候选operator使用`OPENFOOTBALL_PIN_CANDIDATE_V1`。这仅在独立源码worktree和隔离验收wheel中存在，正式D盘主工作区、.venv与runtime仍为原1.2.0/7d。
- 完整回归捕获SQLAlchemy 2.1 Windows URL编码与ConfigParser的既有兼容问题。新增独立percent-path红测后，仅将migration helper的URL中的`%`转义为`%%`交ConfigParser，恢复后仍把原URL交SQLAlchemy；未改变SQLite路径/权限、migration内容或数学。冻结projection独立核验这一行变更。
