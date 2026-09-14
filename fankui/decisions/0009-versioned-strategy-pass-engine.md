# ADR-0009 — Versioned Strategy Profile / Pass Type Engine

- Status: Accepted — 0.7 Architecture Review PASSED
- Date: 2026-09-13
- Baseline: formal `v0.6.0`, `7ded7dbf62dc64a35b7788142a5bf81610353563`
- Accepted: 2026-09-14，用户明确通过本次整体架构验收。
- Accepted implementation: `dd13e0614b2cd4760d2af818cd17b867bc4086dd`

## 决策

落实ADR-0003的Ticket → AtomicBet → BetLeg和ADR-0006的数量偏好，以显式、版本化的
`StrategyProfileV1` / `StrategyPassPlanV1` / `SystemTicketV1` 增量实现。
2X1、3X4、4X11分别分解为1/4/11个子注。旧TicketCandidate仍只接受2X1，旧
PortfolioRevision、V3和settlement policy的序列化/数学保持不变。

来源是已封存父图中的候选、SP、budget、rules和gates。结构风险及角色资格只收紧可行域；
资金分配调用原边际score/exposure内核，保留原逐轮加倍方法。固定模型与融合不调整。
HEDGE须有合格新selection和具体核心失效场景中的存活子注，不能恢复负EV或承诺保本。

SQLite新增typed children、append-only触发器与deferred complete seals。Repository
验证canonical seal、完整子图、精确父来源和确定性重演；生产父工件保留原concrete
audit/current authorization，包含读取与exact retry。新结算policy按子注汇总，保留
unsupported/missing，按同源直接supersession追加更正。

## 后果

- 每张票每场仅一个THREE_WAY outcome，不是多选复式。
- expected payout沿用独立腿假设与期望线性性；结构指标不是统计相关性估计。
- 9/27/81个deterministic payout states不带状态概率；完整收益分布优化留0.9。
- 枚举有显式输入/candidate上限，超限整单拒绝；不能静默丢弃候选后宣称最优。
- 现有资金、eligibility、ROI阈值和数据/审核范围不因profile改变。
- 详见 [Strategy Pass V1合同](../strategy_pass_v1_contract.md)。
