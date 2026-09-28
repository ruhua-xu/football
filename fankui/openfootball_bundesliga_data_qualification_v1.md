# OpenFootball Bundesliga 数据资格报告 V1

日期：2026-09-22。软件基线：v1.1.0 / `5ed940a8af8077be80549603a2da38aea77fc1bf`。

**OpenFootball CC0来源已获用户批准用于本轮有限获取和bootstrap准备。** 本报告不再把其公开许可候选归为不合格。

实际数据资格：**BLOCKED_SOURCE_EXCEPTIONS**；canonical mapping：**IDENTITY_UNRESOLVED**。
这是候选文件逐行解析结果，没有执行production training，也没有自行选定训练窗口。

## 1. 精确获取与文件身份

- repository：`openfootball/football.json`
- exact commit：**`40b3e1b7391932d133287115106304444bf297e1`**
- 私有training evidence root：`D:\文档\xs\football_training_evidence\openfootball_stage3_20260922\`
- 获取授权：用户Stage 3的四文件有限获取指令；4次原始文件HTTP发送，4次成功，0重试，0重定向。
- 只取得下表四个该仓库文件，合计**189364 bytes**。原始bytes、acquisition manifest、候选资料及逐行qualification保存在上述独立根，未放入public Git或交审包。

| 原始文件 / URL | bytes | SHA256 | capture_at_utc |
|---|---:|---|---|
| [2024-25/de.1.json](https://raw.githubusercontent.com/openfootball/football.json/40b3e1b7391932d133287115106304444bf297e1/2024-25/de.1.json) | 88232 | `f473d6595e6c29ebedc08b09177aed6a6b2ff0fa8378b82f51dea469139f44c3` | 2026-09-22T08:07:44.843790+00:00 |
| [2025-26/de.1.json](https://raw.githubusercontent.com/openfootball/football.json/40b3e1b7391932d133287115106304444bf297e1/2025-26/de.1.json) | 88247 | `17d0999db6281e6365823acdbce41a4f01dd469eac63e1f897e4e47c02906672` | 2026-09-22T08:07:46.738055+00:00 |
| [README.md](https://raw.githubusercontent.com/openfootball/football.json/40b3e1b7391932d133287115106304444bf297e1/README.md) | 6330 | `aadc464ae476ba4d5467785f6f2be435d1a583f589f4b2b0617fdf86fc18e29c` | 2026-09-22T08:07:47.785363+00:00 |
| [LICENSE.md](https://raw.githubusercontent.com/openfootball/football.json/40b3e1b7391932d133287115106304444bf297e1/LICENSE.md) | 6555 | `36ffd9dc085d529a7e60e1276d73ae5a030b020313e6c5408593a6ae2af39673` | 2026-09-22T08:07:48.786214+00:00 |

capture时间是原始response读取完成的实际本地UTC。它不表示历史publication/finalization；没有使用Git commit时间或kickoff+2h。
README/LICENSE hash与两个dataset hash分别记录，不能互相替代。

## 2. 实际解析计数

数据由新`OPENFOOTBALL_JSON_ADAPTER_V1`处理，精确四文件身份/bytes检查先于dataset解析。统计不由306场等赛制理论数推算。

| 指标 | 2024/25 | 2025/26 |
|---|---:|---:|
| source name | Deutsche Bundesliga 2024/25 | Deutsche Bundesliga 2025/26 |
| raw match count | **306** | **306** |
| unique match count | **306** | **306** |
| 有效且完整date数 | 306 | 306 |
| 有效且完整time数 | 306 | 306 |
| 原始`score.ft`字段数量 | 306 | 294 |
| direct score array数量 | 0 | **12** |
| missing/null score数量 | 0 | 0 |
| duplicate数量 | 0 | 0 |
| team count | 18 | 18 |
| matchday coverage | 1–34，每轮实际9条 | 1–34，每轮实际9条 |
| date min | 2024-08-23 | 2025-08-22 |
| date max | 2025-05-17 | 2026-05-16 |
| malformed records | 0 | 0 |
| schema exceptions | 0 | **12** |
| abnormal status记录 | **1（awarded）** | 0 |
| A：普通`score.ft`候选 | 305 | 294 |
| B：直接array，时段未证实 | 0 | 12 |
| C：missing score | 0 | 0 |
| D：abnormal representation/status | 1 | 0 |

说明：

- unique身份统计按该常规联赛赛季的有向主客队对计算，重复日期/比分变体也不能冒充另一场。此键只用于源文件去重，**不是canonical match/team ID**。
- `score.ft`数量是字段计数，包含一条`awarded`，所以不等于A类或合格常规时间结果数。
- B类在JSON结构上可以合法，因此malformed=0与schema exceptions=12并不矛盾；缺的是可靠的比分时段语义。
- 305和294仅为通过本适配器结构检查的记录数，**不是已选定的599场训练集**。全部612条原始记录及13条阻塞记录仍完整保留；没有默默删除例外或重新封存训练窗口。

逐轮完整计数与规范摘要：[`data-qualification-summary.json`](../upload/openfootball-elo-bootstrap-v1/data-qualification-summary.json)。

## 3. Score语义调查与异常定位

### A类

固定README及[官方Football.TXT spec](https://openfootball.github.io/spec/)的JSON例子使用`score.ft`，可有`ht`。本adapter只将常规联赛、合法date/time、无异常status且比分一致的此类记录标为**待审核候选**，并校验ht不大于ft；这不构成production admission。

### B类：找到的是“reported时段可能未知”的证据

2026-09-22只读调查了生成器与上游源码，没有取得其他match dataset：

1. [yorobot/football.json quick/helper.rb](https://github.com/yorobot/football.json/blob/master/quick/helper.rb)：生成`matches`使用`doc.matches.as_json`。
2. [Fbtxt::Model::Match](https://github.com/sportdb/sport.db.v2/blob/master/document/lib/fbtxt/document/models/match.rb)：`data['score'] = @score.as_json`；所见目录metadata的blob ID为`f2a51122c5661b84b385f78a30c9c357a45ab6bb`。
3. [Fbtxt::Model::Score](https://github.com/sportdb/sport.db.v2/blob/master/document/lib/fbtxt/document/models/score.rb)，所见blob ID为[`4b53e4d585f2d80f8ead44051480ee749706175c`](https://api.github.com/repos/sportdb/sport.db.v2/git/blobs/4b53e4d585f2d80f8ead44051480ee749706175c)：

```ruby
elsif score.is_a?(Array)
  new( reported: score )
```

该类对reported的注释明确说明：

> period is not known (might be full-time or aet)
> or undefined e.g. for abandoned or awarded (administered) score

`Score#as_json`在`@reported`存在时原样输出array，只有其他分支才输出`ft/ht/et/p/agg`对象。Match中的“assume full-time … why? why not?”是疑问注释，不能覆盖Score明确的未知时段语义。

这些上游文档/代码线索**没有证明本固定dataset的12条array等价于普通FT**，也不声称已恢复生成本commit时的完整generator环境。因此全部保持`SOURCE_SCHEMA_EXCEPTION / REPORTED_SCORE_PERIOD_UNPROVEN`，`regular_time_score_candidate=null`。

固定文件`2025-26/de.1.json`中的原始RFC6901 pointers：

```text
/matches/8    /matches/44   /matches/53   /matches/75
/matches/99   /matches/127  /matches/129  /matches/133
/matches/153  /matches/159  /matches/223  /matches/274
```

### D类：administrative awarded

`2024-25/de.1.json` **`/matches/120`**：原始`status=awarded`。即使有`score.ft`，也不当作已证明的普通常规时间最终赛果。保留`ABNORMAL_MATCH_STATUS`及原始pointer/hash，正常比分候选置null，等待明确例外政策和事实审核；没有改写原始比分或自动排除该场。

## 4. 时间转换

源文件time没有UTC offset。本轮显式采用候选adapter policy：

```text
timezone=Europe/Berlin
tzdata_version=2025.2
iana_version=2025b
Europe/Berlin TZif SHA256=a7fd9932d785d4d690900b834c3563c1810c1cf2e01711bcc0926af6c0767cb7
adapter_policy_hash=a237ea1875560ccbbc15efa3102463f025baa825be04004863a1e5a4c9e36f75
```

从固定IANA文件构建ZoneInfo，不读取宿主机时区/系统TZPATH。CET为UTC+1、CEST为UTC+2；DST不存在时间和歧义时间均拒绝。输入格式固定YYYY-MM-DD + HH:MM，显式补`:00`秒用于UTC表示，保留**MINUTE来源精度**，不声称来源提供秒或微秒测量。

每条候选保留local date/time原文、original pointer、record hash、source filename/commit/file SHA256、capture UTC和adapter policy hash。两个provider历史时间始终null/UNKNOWN。

## 5. 两季team set变化与canonical mapping plan

两季各18队，交集**16**，并集**20**；离开2024/25集合的是 **Holstein Kiel、VfL Bochum 1848**；加入2025/26集合的是 **1. FC Köln、Hamburger SV**。这是文件集合差，不据此额外推导资格或训练窗口。

已生成显式映射候选：`canonical_mapping_plan.candidate.json`，23项按kind/source_label排序。所有canonical ID与catalog证据目前为null；没有把源队名当team_id或自动猜alias。

| OpenFootball team name | 2024/25 | 2025/26 | canonical team identity |
|---|:---:|:---:|---|
| 1. FC Heidenheim 1846 | ✓ | ✓ | IDENTITY_UNRESOLVED |
| 1. FC Köln | — | ✓ | IDENTITY_UNRESOLVED |
| 1. FC Union Berlin | ✓ | ✓ | IDENTITY_UNRESOLVED |
| 1. FSV Mainz 05 | ✓ | ✓ | IDENTITY_UNRESOLVED |
| Bayer 04 Leverkusen | ✓ | ✓ | IDENTITY_UNRESOLVED |
| Borussia Dortmund | ✓ | ✓ | IDENTITY_UNRESOLVED |
| Borussia Mönchengladbach | ✓ | ✓ | IDENTITY_UNRESOLVED |
| Eintracht Frankfurt | ✓ | ✓ | IDENTITY_UNRESOLVED |
| FC Augsburg | ✓ | ✓ | IDENTITY_UNRESOLVED |
| FC Bayern München | ✓ | ✓ | IDENTITY_UNRESOLVED |
| FC St. Pauli 1910 | ✓ | ✓ | IDENTITY_UNRESOLVED |
| Hamburger SV | — | ✓ | IDENTITY_UNRESOLVED |
| Holstein Kiel | ✓ | — | IDENTITY_UNRESOLVED |
| RB Leipzig | ✓ | ✓ | IDENTITY_UNRESOLVED |
| SC Freiburg | ✓ | ✓ | IDENTITY_UNRESOLVED |
| SV Werder Bremen | ✓ | ✓ | IDENTITY_UNRESOLVED |
| TSG 1899 Hoffenheim | ✓ | ✓ | IDENTITY_UNRESOLVED |
| VfB Stuttgart | ✓ | ✓ | IDENTITY_UNRESOLVED |
| VfL Bochum 1848 | ✓ | — | IDENTITY_UNRESOLVED |
| VfL Wolfsburg | ✓ | ✓ | IDENTITY_UNRESOLVED |

另外三项：`Deutsche Bundesliga → canonical Bundesliga`，`2024/25 → canonical season ID`，`2025/26 → canonical season ID`，均等待catalog中的实际ID和identity证据。匹配必须精确、显式、可审核；大小写/缩写/Unicode变体不做模糊合并。

mapping plan content hash：`42ff15a62c2d7a7a9a59aff8df5efca5c435049da9583e296caa38be87eb8e75`。

## 6. Rights candidate与历史验证

`source_rights.candidate.json`绑定固定repository/commit、**CC0 1.0 Universal**、LICENSE/README精确hash和以下候选用途：

`ACQUIRE, STORE_LOCAL, NORMALIZE, INTERNAL_RESEARCH, MODEL_TRAINING, DERIVED_STATE_RETENTION, AUDIT_RETENTION`。

本轮有限获取/准备授权已明确；软件中的正式rights/review仍为`CANDIDATE_NOT_APPROVED`，trusted pins/review artifact=null，production_training_authorized=false。operator本人可作为authorized_reviewer，只须真实trusted pins覆盖其source/schema/时间范围，且review绑定精确candidate payload/hash；不要求第三方律师或另找外部审核员。

```text
TRAINING DATA VALIDITY = BLOCKED_SOURCE_EXCEPTIONS + IDENTITY_UNRESOLVED
HISTORICAL PREDICTIVE VALIDATION = UNAVAILABLE
reason = UNPROVEN_HISTORICAL_VERSION_TIME
historical Brier / LogLoss / calibration / availability metrics = null
```

缺少历史版本时间本身不否定未来以合法当前快照构建candidate state的路线；本次实际阻塞是源异常、尚未完成的身份/审核及窗口确认。未计算历史预测指标或Elo state。

## 7. 候选材料定位

私有根内：

- `acquisition_request.json`、`acquisition_manifest.json`：有限获取请求与4次真实文件回执。
- `adapter_policy.candidate.json`：显式Berlin/IANA/比分/异常政策。
- `current_snapshot_scope.candidate.json`、`source_rights.candidate.json`、`canonical_mapping_plan.candidate.json`：仅候选。
- `qualification_v1.json`：全部原始记录的解析/拒绝谱系，SHA256 **`65bc03c8d16b9ce7b644912a6a270eac96e3259b0897fe2044a0b3103de18115`**。

内容hash与文件SHA256是不同域；候选hash、文件hash、许可hash都不是Elo training_data_hash/state_hash。

窗口规则恢复及最终阻塞结论见[Stage 3报告](openfootball_elo_bootstrap_stage3_report.md)。
