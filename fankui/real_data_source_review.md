# Football System 0.5.0 真实数据源审查

## 审查结论

审查截止时间为 `2026-09-01`。以下结论区分三件不同的事：

- `ACCEPT` 表示来源及合同适合继续实现 adapter，不表示当前已经取得 API key、付费授权或真实数据。
- `DEFER` 表示在 key、费用、许可、时间语义或接口稳定性证据补齐前，不允许把来源接入正式运行。
- `REJECT` 表示当前条款或数据合同不适合本公开仓库的目标使用方式。

当前可执行裁决为：

| 数据范围 | 来源 | 裁决 | 当前实现建议 |
| --- | --- | --- | --- |
| 基础赛程、球队、赛果 | Sportmonks Football API v3 | `ACCEPT` adapter 合同；真实调用 `DEFER` | 实现 provider-neutral parser、受控 HTTP client 和 sanitized contract fixtures；取得 token 后才运行真实调用 |
| 基础赛程、球队、赛果 | football-data.org v4 | `DEFER` | 不提交真实响应；先确认永久本地保留、取消订阅后的使用及公开测试摘录权利 |
| 基础赛程、球队、赛果 | API-Football v3 | `REJECT` 当前公开仓库接入 | 官方条款不提供数据发布许可；除非另行取得权利，不保存或提交响应 |
| 国际赛前 1X2 赔率 | The Odds API v4 | `ACCEPT` adapter 合同；真实历史调用 `DEFER` | 采用 `h2h` decimal odds 和 `MARKET_CONSENSUS_MEDIAN_V1`；历史接口需要付费 key |
| 国际赛前 1X2 赔率 | Sportmonks Odds | `DEFER` | 套餐、目标联赛历史深度和快照可用时间确认后再启用 |
| 国际赛前 1X2 赔率 | API-Football Odds | `REJECT` 当前公开仓库接入 | 与 API-Football 相同的数据发布权问题 |
| 中国竞彩受注与固定奖金 | 中国竞彩网公开页面 | 自动接入 `DEFER` | 不编写脆弱爬虫；实现严格的 `SportteryManualArchiveProvider` JSON/CSV 导入 |
| 无 key 整季赔率探索 | football-data.co.uk CSV | 正式回测 `DEFER` | 只允许本地、忽略的探索；不得进入 `HISTORICAL_ARCHIVE_V1` 或提交原始/派生逐场数据 |

没有真实 API key 被读取或使用，没有第三方真实 payload 被加入仓库，也没有真实历史回测或真实 daily packet 可供声明。本文是工程尽调记录，不是法律意见。

## 统一准入标准

每个来源必须明确以下信息，缺失项不能由程序推断：

| 字段 | 准入要求 |
| --- | --- |
| source | 发布主体、产品、API 版本和正式入口可识别 |
| planned usage | fixture、result、market odds 或 Sporttery 固定奖金用途明确 |
| coverage | 目标联赛、赛季、市场和状态覆盖可量化 |
| pricing/free limit | 免费范围、付费门槛和历史 add-on 明确 |
| rate limit | 配额单位、响应 header、`429` 语义和重置规则明确 |
| timestamp semantics | kickoff、source update、snapshot、local request/receipt/ingestion 时间不得混用 |
| historical availability | 能证明记录在历史 decision cutoff 前可用；后来下载时间不能代替 source time |
| license/terms note | 获取、保存、加工、内部研究、展示和再分发边界分别记录 |
| raw payload retention | 是否允许长期保存、取消订阅后保留及公开测试摘录明确 |
| known limitations | 修订、删除、延迟、状态、缺失和覆盖风险显式记录 |
| implementation recommendation | `ACCEPT`、`REJECT` 或 `DEFER`，附工程 go gate |

`LIVE_STRICT` 只能使用本系统真实请求时记录的 request、receipt、availability 和 ingestion 证据。后来取得的历史数据只有在来源时间可审计时才可使用 `SOURCE_TIME_RESEARCH`；该模式不是补造缺失 timestamp 的通道。

## 基础数据候选

### Sportmonks Football API v3

**裁决：adapter `ACCEPT`；真实调用 `DEFER`。**

实现状态：`live ingest-fixtures` 已具备显式网络入口、raw archive 和 append-only SQLite lineage，但自动化验收不访问外网。该入口可用不改变真实调用的 `DEFER` 裁决；首次账户调用仍须先完成下列 entitlement go gate。

| 维度 | 已核验事实 |
| --- | --- |
| planned usage | fixtures、competitions、teams、match results；第一版不读取 logo、照片或预测产品 |
| coverage | 官方宣传覆盖大量联赛；实际可见联赛取决于订阅 entitlement，必须用 token 核验 |
| pricing/free limit | 官方存在免费计划和付费联赛套餐；目标联赛和历史包必须按运行时 entitlement 核验 |
| rate limit | 官方 v3 文档描述按 entity/hour 的限流和 `429`；client 必须保留并校验响应 `rate_limit` metadata，失败请求采用有界退避 |
| timestamp semantics | `starting_at` 是 kickoff，不是 availability；`fixtures/latest` 的更新窗口也不是不可变 source publication timestamp |
| historical availability | 较老赛季可能需要历史 add-on；不能仅凭当前响应重建过去修订链 |
| license/terms note | [Terms of Service](https://www.sportmonks.com/terms-of-service/) 允许保存 service data，但禁止未经同意直接转售；logo/照片权利另行处理 |
| raw payload retention | 条款允许 data storage；本仓库仍只提交自造 sanitized contract fixtures，不提交账户响应或媒体资产 |
| test support | 官方 [Demo response files](https://docs.sportmonks.com/v3/api/demo-response-files) 明确用于 mock、testing 和 integration planning |
| known limitations | v3 仍有字段变更；删除和修订不是 append-only 历史；真实可用时间必须使用本地接收证据 |

实现 go gate：

1. token 只从 `SPORTMONKS_KEY` 读取，并使用 `Authorization` header，不能进入 URL、日志、raw metadata 或 Git。
2. 首次真实调用前核对 entitlement、联赛、字段和限流 metadata。
3. 保存完整本地 request/receipt 时间和 payload SHA-256，更新记录只能追加。
4. provider 状态必须显式映射；无法映射的取消、腰斩、判罚、加时或点球状态 fail-closed。

### football-data.org v4

**裁决：`DEFER`。**

| 维度 | 已核验事实 |
| --- | --- |
| planned usage | fixtures、schedules、teams、league tables 和 results |
| coverage | [Pricing](https://www.football-data.org/pricing) 显示免费计划覆盖 12 项赛事；具体数据深度按套餐变化 |
| pricing/free limit | Free 为 10 calls/minute；ML Light 标示 10 seasons history；Odds add-on 另收费 |
| rate limit | [API policies](https://docs.football-data.org/general/v4/policies.html) 与 pricing 页面对部分付费档位的数字不完全一致，必须以 key entitlement/response 为准 |
| timestamp semantics | `utcDate` 是 kickoff；`lastUpdated` 的修订保证和 publication clock 未形成可用于历史 cutoff 的正式合同 |
| historical availability | 支持赛季查询，但免费和各付费档位历史深度并非统一保证 |
| license/terms note | 要求 attribution；取消服务后的继续使用及原始响应长期保留边界需书面确认 |
| raw payload retention | 未找到足以支持公开 raw/sanitized response 再分发的明确授权 |
| known limitations | match status 比当前四值枚举更丰富；免费 score/schedule 有延迟；历史修订链不可重建 |

在取得书面确认前不实现真实网络激活，不提交 provider 真实值。

### API-Football v3

**裁决：当前公开仓库使用方式 `REJECT`。**

技术上 API 提供 fixtures、teams、results、odds 和稳定 fixture IDs，也提供免费/付费配额；但审查时官方条款页返回 `403`，已核验条款文本明确说明服务本身不授予数据“使用和发布”许可，用户需自行从联赛、足协或其他权利方取得发布许可。该边界与本项目需要公开 contract fixtures 和可审计归档不兼容。

除非 owner 取得独立书面数据权利，否则不实现此 provider、不保存响应、不把其值改写成 sanitized fixture。

## 国际赔率候选

### The Odds API v4

**裁决：adapter `ACCEPT`；真实历史调用 `DEFER`。**

| 维度 | 已核验事实 |
| --- | --- |
| planned usage | 足球 `h2h` decimal odds，保留全部 bookmaker 快照，再派生单一 consensus |
| coverage | 官方历史页列出 EPL、Bundesliga、La Liga 等足球赛事；具体 bookmaker/market 覆盖随时间变化 |
| pricing/free limit | current API 需要 key；[Historical Odds Data](https://the-odds-api.com/historical-odds-data/) 明确只在 paid plans 提供 |
| rate limit | quota cost 按 region 与 market 计；响应含 `x-requests-remaining/used/last` |
| timestamp semantics | 历史响应包含 snapshot `timestamp`、`previous_timestamp`、`next_timestamp`；bookmaker/market 含 `last_update` |
| historical availability | featured markets 自 2020-06-06 起通常每 10 分钟快照，2022-09 起通常每 5 分钟；具体 sport 从其 coverage 起点开始 |
| license/terms note | [Terms, 2026-08-31](https://the-odds-api.com/terms-and-conditions.html) 允许长期保存、研究和派生值，但禁止把 raw data 作为独立产品转售、重打包或再分发 |
| raw payload retention | 私有本地保留可接受；GitHub 只提交自造结构 fixture，不提交真实 raw response |
| known limitations | completed score endpoint 仅回看短窗口，不能替代整季 results source；历史接口会消耗付费 quota |

第一版派生策略固定为：

```text
MARKET_CONSENSUS_MEDIAN_V1
```

对同一 `match_id + captured_at + THREE_WAY`：

1. 只接受同时包含 HOME_WIN、DRAW、AWAY_WIN 且 decimal odds 均大于 1 的 bookmaker。
2. 对每家 bookmaker 分别执行 `NORMALIZED_INVERSE_V1` 去水，得到三项概率。
3. 分别取三项概率的确定性中位数；偶数样本取中间两值算术平均。
4. 将三个中位数再次归一化到和为 1。
5. bookmaker 按 provider key 排序，派生 snapshot hash 必须绑定全部 constituent snapshot IDs/hashes。
6. 没有完整 bookmaker 时返回 unavailable；不得随机挑选单一 bookmaker。

### Sportmonks Odds

**裁决：`DEFER`。**

Sportmonks 条款的数据保存边界比多数候选更清晰，官方 demo 也包含 pre-match odds 样例；但实际赔率 entitlement、目标联赛、bookmaker 覆盖、历史 add-on 和历史快照保留期限仍需 token/套餐核验。第一版不同时接入第二个赔率 API。

### API-Football Odds

**裁决：当前公开仓库使用方式 `REJECT`。**

原因与 API-Football 基础数据相同。技术可访问不等于已取得存储、公开测试摘录或发布权。

## 中国竞彩来源

### 官方公开页面自动接入

**裁决：`DEFER`。**

审查确认公开页面曾展示受注赛程、竞彩编号、HAD/HHAD、固定奖金、销售状态和 90 分钟赛果。第一方页面脚本和 transport 路径在历史上发生过迁移，原始状态值的页面解释也发生过变化；当前未找到稳定、文档化、带授权边界和兼容承诺的公开开发者 API。官方页面还存在访问限制和禁止未经授权复制/再发布的声明。

因此：

- 不把页面内部 gateway 当公开 API 合同。
- 不编写网页爬虫，不绕过访问控制。
- 不硬编码未文档化的 raw status 数字含义。
- 只有取得覆盖自动读取、保存、转换和预期展示的书面授权后，才评估 `SportteryRealProvider`。

### SportteryManualArchiveProvider

**裁决：工程路径 `ACCEPT`。**

人工导入不是许可绕过机制；操作者仍需对输入数据拥有适当使用权。第一版只接受经过独立复核的 JSON/CSV，至少绑定：

```text
schema_version
snapshot_id
captured_at_utc
source_reference
source_artifact_sha256
entered_by
reviewed_by
reviewed_at_utc
sporttery_match_no
match_number_date
competition
home_team
away_team
kickoff_at_utc
market_type = THREE_WAY
sale_status
HOME_WIN / DRAW / AWAY_WIN fixed bonus
```

奖金以 decimal string 解析；match number 必须与日期组合识别；未知字段拒绝；更正通过新 snapshot 追加而不是覆盖。CSV 必须为 UTF-8 RFC 4180，空值与空字符串不可混淆。

## 整季真实历史 pilot 审查

### football-data.co.uk EPL 2023/24

**裁决：正式 `SOURCE_TIME_RESEARCH` 回测 `DEFER`；仅允许本地私有探索。**

样本审计对象为：

```text
https://www.football-data.co.uk/mmz4281/2324/E0.csv
```

核验到 380 场、20 队、106 列，赛果字段完整；`B365CH/B365CD/B365CA` 为完整 closing 1X2 triplet。官方 [data page](https://www.football-data.co.uk/data.php) 与 [field notes](https://www.football-data.co.uk/notes.txt) 说明 `C` 表示 closing odds，并说明一般性采集安排。

它仍不能进入当前严格 archive，原因是：

- 每行没有 odds capture/publication/availability UTC timestamp。
- “closing”只描述相对 kickoff 的阶段，不提供可审计的精确 source time。
- 当前下载时间、HTTP `Last-Modified`、比赛 kickoff 或一般性周二/周五采集说明均不能替代逐条 `available_at_utc`。
- 累积 CSV 会被后续修订，文件本身没有 append-only revision lineage。
- “free for prediction/analysis”不是明确的开放数据许可证，未找到允许公开再分发 raw/normalized match-level odds 的授权。

若 owner 仅授权本地探索，raw CSV 必须放在被忽略的 `data/research/`，报告必须写明 `NOT ADMITTED TO HISTORICAL_ARCHIVE_V1`，且不得把探索结果称为 point-in-time backtest。

### 合规 pilot 解锁条件

第一个可声明的真实 `P_market` pilot 需要以下任一路径：

1. 取得 The Odds API paid historical key，核验一个完整赛季的 snapshot coverage，并配对一个许可清晰、赛果语义明确的来源。
2. 从 Football-Data 或其他来源取得书面许可，以及每条赔率的不可变 capture/publication timestamp 证据。
3. 由用户提供已经授权的完整赛季导出，附字段字典、权利说明、来源时间和 artifact hash。

在此之前，`exchange/real_history_pilot_report.md` 只能生成阻塞报告，必须明确：

```text
REAL PILOT NOT RUN
NO ADMISSIBLE POINT-IN-TIME ODDS ARCHIVE
NOT STRATEGY VALIDATION
```

## 实现选择

0.5.0 第一轮实现范围固定为：

1. `data/raw/`、`data/live/`、`data/research/` 物理隔离及 secret-safe raw archive。
2. 有界 timeout/retry/backoff、`429` 和 provider request audit。
3. Sportmonks fixture parser/adapter，默认不联网；真实调用必须显式提供环境变量 key。
4. The Odds API parser/adapter 与 `MARKET_CONSENSUS_MEDIAN_V1`，默认不联网。
5. 严格 `SportteryManualArchiveProvider` JSON/CSV。
6. 确定性 Team、Competition 和 Match identity resolver；歧义返回 `AMBIGUOUS_MATCH_MAPPING`。
7. `mock | live | research` 启动隔离；live 发现 mock provenance 立即拒绝。
8. 真实数据不足时输出 `NO_ANALYSIS_INSUFFICIENT_DATA`，不伪造 P_market、P_quant、Evidence 或 Packet。

不修改冻结的投注、融合、Settlement、Backtest V1 或 LLM Review V2 合同。真实整季回测和真实 daily packet 只有在上述外部 go gate 满足并产生可审计工件后才能标记完成。
