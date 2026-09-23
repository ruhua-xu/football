# OpenFootball bootstrap 前置材料摘要（等待明确确认）

> 后续记录：用户已在question工具明确选择 **“确认前置准入后BLOCKED”**。下文保留当时展示的原始前置摘要；准入已完成，最终结果见[production_bootstrap_final_report.md](production_bootstrap_final_report.md)。该确认不包含model approval、pilot或release。

本文件不是production model approval。当前没有未来target资料，正式pilot尚未执行，因此manifest/model approval/release的精确ID/hash尚不存在，不能用空值或程序自行approved=true代替。

## 当前可确认的精确前置包

- 原始source commit：`40b3e1b7391932d133287115106304444bf297e1`。
- 两季expected各306，共612；固定exceptions 1+12；candidate included 305+294=599；原始612条永久保留。
- 2024/25 source SHA：`f473d6595e6c29ebedc08b09177aed6a6b2ff0fa8378b82f51dea469139f44c3`。
- 2025/26 source SHA：`17d0999db6281e6365823acdbce41a4f01dd469eac63e1f897e4e47c02906672`。
- CC0 LICENSE SHA：`36ffd9dc085d529a7e60e1276d73ae5a030b020313e6c5408593a6ae2af39673`。
- Data review payload hash：**`128c7e170a181a7a8258a9bd25fd5969fd73b01e75dcb053d682329257c0c947`**。
- Authority payload候选文件SHA：**`c79e6636c501ee3f5ca8bd5f20c6fc95c763e6391abfc5c0097d21588f39e60f`**。
- Canonical mapping root：**`67a96a44b05339fa4dd5d5f11ddc50a536c78fac4f8884cb072750dd31c9b589`**。
- ELO_TRAINING_WINDOW_V1 hash：**`0e4c4ddbaafee6600de070d05f344ce9384742b81ff4191dc98b36cef9367116`**。
- Window：2024/25 WARMUP → 2025/26 PILOT_TARGET → 2026/27 PRODUCTION_TARGET（zero-fact）。
- Config：initial1500/K20/home100/regression0.75/draw0.25/minimum5；config hash `c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4`。

## Authority/rights候选

- issuer / authorized_reviewer：`workspace-owner`；用户本人作为operator reviewer。
- source ID：`OPENFOOTBALL_FOOTBALL_JSON_40b3e1b7391932d133287115106304444bf297e1`。
- schemas：`OPENFOOTBALL_DATA_REVIEW_PAYLOAD_V1`、`OPENFOOTBALL_MODEL_APPROVAL_PAYLOAD_V1`。
- 本次提出的操作/review授权区间：**2026-09-23T02:43:27.544096Z ≤ t < 2027-07-01T00:00:00Z**。
- 用途：ACQUIRE、STORE_LOCAL、NORMALIZE、INTERNAL_RESEARCH、MODEL_TRAINING、DERIVED_STATE_RETENTION、AUDIT_RETENTION。
- Source原始记录永久保留；操作权限区间结束不意味着自动删除612条CC0 source records。
- Model production release仍要求pilot后的精确manifest approval，当前并未授予。Derived state的正式retention随该model approval确认；audit候选为个人私有系统长期保留。
- 当前只有authority payload候选，**没有激活trusted pins或生成approved=true的review文件**。

## 24项显式canonical映射

这些是新建catalog的确定性UUID5分配，不是猜测现有ID，不将队名充当内部ID。技术准备者为`opencode-technical-preparer`，时间为上述材料准备时刻；这不是冒充用户已作出的精确payload审批。

| kind | source_label | canonical_id |
|---|---|---|
| COMPETITION | Deutsche Bundesliga | 4ed332b3-3b22-5645-bcbf-3c1895ea653e |
| SEASON | 2024/25 | 4a25fb3c-cfa0-5042-90ff-ca7a848371ba |
| SEASON | 2025/26 | 339d9480-55f2-5183-868c-cbf92d65cd5d |
| SEASON | 2026/27 | 1ffdfd54-dd21-5da6-bdd3-e2133d0846a8 |
| TEAM | 1. FC Heidenheim 1846 | 27adc14c-fc3f-51fc-b29d-1188fcefdd27 |
| TEAM | 1. FC Köln | 755c5fd7-f30a-5d6e-92b0-1b92a5bfe40d |
| TEAM | 1. FC Union Berlin | d8d7a14f-247a-5e6b-8ca1-ecbacf7f1c01 |
| TEAM | 1. FSV Mainz 05 | 0b7be74e-4e88-5d5a-a2c8-a780455fd4b4 |
| TEAM | Bayer 04 Leverkusen | 33813447-6ab2-5629-b67d-e4f50aa42795 |
| TEAM | Borussia Dortmund | b050e627-5530-526d-98d7-55efa6cd2e31 |
| TEAM | Borussia Mönchengladbach | 68663ae8-842e-508b-a452-c055519316c0 |
| TEAM | Eintracht Frankfurt | 2c8fef43-9ff0-5cb1-a698-328d60b08e6a |
| TEAM | FC Augsburg | a40f2813-2593-5fcf-88cf-c6d4b67fe69a |
| TEAM | FC Bayern München | a9fa6c65-18cf-5e22-9a87-3041982e72e9 |
| TEAM | FC St. Pauli 1910 | a3ea6f26-aeda-59e6-a993-8af9f7491961 |
| TEAM | Hamburger SV | dd8a36e8-c3e4-5b69-aeee-1f17583c7b53 |
| TEAM | Holstein Kiel | c2acc3fe-1694-5eb3-9c32-49865b7f6a3f |
| TEAM | RB Leipzig | 8b09cc57-d5e9-5a26-b5f8-b5cdbb38364f |
| TEAM | SC Freiburg | b5136504-6df9-560d-89c8-f5ada36c1fed |
| TEAM | SV Werder Bremen | 7f5bbabe-2264-5833-a553-84accb6fe4fb |
| TEAM | TSG 1899 Hoffenheim | 3f4767eb-7a57-50d5-895d-8b861780b981 |
| TEAM | VfB Stuttgart | c5c83626-9839-58f2-aeaf-dc83f9449b61 |
| TEAM | VfL Bochum 1848 | 0762c57b-2bbe-5ffd-85a3-94f0c8b42c62 |
| TEAM | VfL Wolfsburg | 11dacebf-6297-51e0-960f-c88dbd41aeea |

逐项method、review evidence reference/hash、reviewed_by/at及entry content hash保存在私有：
`D:\文档\xs\football_training_evidence\openfootball_stage3_20260922\production_v1\openfootball_canonical_mapping_review_v1.json`。
该文件SHA256：`d5574b0db2eafe54a2324604761f14bd2bc883712da54f9740798f2010e36986`。

## 若确认前置准入，实际变更范围

- 先执行正式backup并校验，再将既有production SQLite升级`6c859ab273fe → 7d96abc3840f`；保持其application_id/物理身份。
- 在同一production库原子记录24个catalog条目、612个canonical historical match identities、全部612条source records及599条included observed facts/normalized results。
- `ofp_*`是独立版本化binding；旧CurrentSnapshotCollectionScopeV1、SPORTMONKS parser、strict historical artifacts保持原语义。
- 不写real program/source admission/model pin/anchor/epoch/run/lock，不调用The Odds API或LLM API。

## 当前缺口

正式runtime当前matches/results/model states/releases/live preparations/rb artifacts仍为0，生产DB字节未改变。未来Bundesliga 2026/27的canonical targets、精确kickoff与已有live input refs均未提供。

因此还不能封存具体target exclusions、正式pilot plan或pilot后的model approval payload。不能把本摘要当作对未知manifest/model hash的提前批准，也不能用历史比赛/合成target替代未来目标。

在继续真实写入前，需要用户决定：先补齐未来目标以保留pilot后的单一model确认点，或明确只确认本前置包、完成准入后以BLOCKED交付。本摘要自身没有执行任何批准。
