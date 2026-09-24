# v1.2.0 OpenFootball software release closeout

## 接受基线

- Approved feature：`feature/openfootball-elo-bootstrap-v1`。
- Accepted commit：`df47ba4cf34eb4f0a964c2e5ab5d0e88ea6a57f6`；tree：`034372ea5297d391bcfef74d76d92d4175461689`。
- Candidate CI #46 SUCCESS；GitHub 2910 passed / 2 skipped，Windows 2911 passed / 1 skipped。
- 本release版本为**1.2.0**；migration head **7d96abc3840f**。完整release gates、feature/main/tag身份及发布后runtime回执由`upload/v1.2.0/`独立记录，未完成的门禁不得声称PASS。

## Release-only变更

版本声明、SQLite版本提示及精确冻结projection更新为1.2.0；wheel依赖显式声明tzdata==2025.2，保持IANA2025b和原Berlin TZif。资源路径维持87项，无glob或资源删除。

Operator从candidate身份切换为正式`OPENFOOTBALL_RELEASE_V1`，继续V2安装契约；实际module/metadata必须1.2.0。正式菜单只使用非editable installed wheel，核验RECORD、源码投影和冻结数学。外置维护glue负责受控backup及原位upgrade/rebind，不扩展预测/输入业务语义。

现有runtime目录`football_runtime/v1.1.0`继续使用原installation ID和DB文件。发布后先备份双库，再安装正式wheel；production已在7d时不对它执行migration，只对旧6c head前向升级。两份installation manifest在有intent/receipt的维护边界更新；中断fail closed，不自动删锁或downgrade。升级后再做正式双库backup。

## 冻结资格与数学

- source records / exceptions / admitted facts：**612 / 13 / 599**。
- Mapping root：`67a96a44b05339fa4dd5d5f11ddc50a536c78fac4f8884cb072750dd31c9b589`。
- Training window hash：`0e4c4ddbaafee6600de070d05f344ce9384742b81ff4191dc98b36cef9367116`。
- Facts root：`9440470d48857e43e9607b3aaf85581087d3d40da77d337dbe099b9853cb2d15`。
- 2024/25=WARMUP；2025/26=PILOT_TARGET；2026/27=PRODUCTION_TARGET zero-fact。
- ELO_THREE_WAY_BASELINE_V1 / model_version=1 / config_hash=`c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4`。

| 冻结身份 | Hash |
|---|---|
| Return algorithm | `76995ed699a8d054f3865ffbbc0063a46af2123f18ab34aa6b27a91690dc7aa6` |
| Return policy | `bd9cf8b2c3d36ce13327cb49b9513c4d6c995762387606ce26bba37ef0769b47` |
| Objective | `9bb39a771ac2390cb537030c809a7e214dcf88bcce2be17808bc7b3dcbfe7c30` |
| Prospective implementation | `6b2a73c14720c60eb30f9bc5d4158bccf095d397748cd1157eb53f13933ddb1f` |
| Prospective policy | `06f9cb5289959cf6387b324f9a8ef617d1960a68e88e23cb1423e0c15ef847f8` |
| Market consensus | `c6c22bfb8a2c53aeb624b485c73c6e144c65f1a3cd8b6df2d7c5f32a88cc914f` |

Poisson、V4、fusion/P_final、EV、Strategy、Return/Objective/payout/Settlement全部保持原算法。版本metadata改变会改变package raw-byte implementation revision，该身份须按真实wheel重新记录；不重写旧data/approval/model工件，也不改变上述数学hashes。

## 门禁和发布顺序

完整pytest、Ruff、compileall、whitespace、fresh migration/check、genuine v1.1.0 populated升级、空downgrade/re-upgrade、populated OFP downgrade refusal、wheel build及isolated-wheel E2E全部重跑。分别记录Windows/Linux平台skip。

release-only feature commit→feature CI SUCCESS→保留业务提交历史merge main→main CI SUCCESS→annotated v1.2.0 tag→tag CI SUCCESS，随后才安装正式wheel和受控runtime维护。Tag指向最终main release commit。

## 固定停止点

**TARGET_WAITING_FOR_SPORTTERY_SLATE**。REAL_PROVIDER_HTTP=0、LLM_API_HTTP=0、REAL_PROSPECTIVE_OBSERVATIONS=0、AUTO_BETTING=NO。

不构造假target，不创建REAL_OBSERVATION_PROGRAM_V1、REAL_MODEL_PIN_V1、anchor、epoch、run或DecisionLock。仅待官方竞彩足球出现未来Bundesliga2026/27目标并完成全部编号/日期/主客/kickoff/sale/canonical IDs后，才按独立授权进入target exclusions→pilot→approval→model release→REAL_MODEL_PIN_READY。此次closeout完成即停止。
