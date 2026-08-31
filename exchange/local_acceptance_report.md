# Manual Review Acceptance Baseline

> Audit report only. Do not use this file as web GPT input.

## Identity

- AnalysisRun ID: `manual-review-acceptance-001`
- Packet ID: `ff2d80b6-1c12-58b6-8ce5-2a89a497e27b`
- Packet hash: `d1e03f46a2e18e0acf70f07c9b08b968090b8adbddb689332f315947bc6be40b`
- AnalysisPacket: `D:\文档\xs\football\exchange\analysis_packet_v2.json`
- SQLite database: `D:\文档\xs\football\data\manual_review_acceptance.db`
- Schema: `ANALYSIS_PACKET_V2`
- Matches: `6`
- Original fusion policy (local DB only): `QUANT_ONLY_V1`

## Packet Validation

- PASS: canonical packet hash and deterministic packet ID
- PASS: exact persisted packet binding and exported file bytes
- PASS: six matches with P_market and P_quant
- PASS: per-match review context ID and hash
- PASS: sealed international odds and Sporttery fixed bonuses
- PASS: unavailable context is null/empty and marked PARTIAL
- PASS: no final prediction, EV, ranking, ticket, portfolio, budget, stake, weight, or strategy fields
- PASS: no ANALYSIS_PACKET_V1 was exported
- PASS: no formal LLM review, FusionRun, or PortfolioRevision exists

## Original P_final

| Match | Home | Draw | Away |
|---|---:|---:|---:|
| `match-001` | 60.00% | 24.00% | 16.00% |
| `match-002` | 52.00% | 27.00% | 21.00% |
| `match-003` | 63.00% | 22.00% | 15.00% |
| `match-004` | 43.00% | 29.00% | 28.00% |
| `match-005` | 36.00% | 31.00% | 33.00% |
| `match-006` | 30.00% | 34.00% | 36.00% |

## Original Portfolios

### Budget CNY 100.00

- Status: `RECOMMENDED`
- Total stake: CNY 100.00
- Cash position: CNY 0.00
- Tickets: `4`
- Stake at risk: CNY 100.00
- Maximum single-ticket exposure: CNY 40.00
- Maximum match exposure: CNY 60.00

| Ticket | Multiplier | Stake | Potential gross payout | Expected profit | ROI |
|---|---:|---:|---:|---:|---:|
| `match-001/HOME_WIN + match-003/HOME_WIN` | 10 | CNY 20.00 | CNY 67.60 | CNY 5.55 | 27.76% |
| `match-001/HOME_WIN + match-002/HOME_WIN` | 10 | CNY 20.00 | CNY 81.70 | CNY 5.49 | 27.45% |
| `match-002/HOME_WIN + match-003/HOME_WIN` | 20 | CNY 40.00 | CNY 153.00 | CNY 10.12 | 25.31% |
| `match-001/HOME_WIN + match-006/DRAW` | 10 | CNY 20.00 | CNY 119.70 | CNY 4.42 | 22.09% |

| Match exposure | Stake | Budget ratio |
|---|---:|---:|
| `match-001` | CNY 60.00 | 60.00% |
| `match-002` | CNY 60.00 | 60.00% |
| `match-003` | CNY 60.00 | 60.00% |
| `match-006` | CNY 20.00 | 20.00% |

| Stress scenario | Exposed stake | Result |
|---|---:|---|
| `ALL_EXPOSED_MATCHES_ADVERSE` | CNY 100.00 | P/L -100.00 CNY; recovery 0.00% |
| `TOP_EXPOSURE_MATCH_ADVERSE` | CNY 60.00 | capital CNY 0.00..CNY 153.00 |
| `TOP_TWO_EXPOSURE_MATCHES_ADVERSE` | CNY 100.00 | P/L -100.00 CNY; recovery 0.00% |

### Budget CNY 200.00

- Status: `RECOMMENDED`
- Total stake: CNY 200.00
- Cash position: CNY 0.00
- Tickets: `4`
- Stake at risk: CNY 200.00
- Maximum single-ticket exposure: CNY 80.00
- Maximum match exposure: CNY 120.00

| Ticket | Multiplier | Stake | Potential gross payout | Expected profit | ROI |
|---|---:|---:|---:|---:|---:|
| `match-001/HOME_WIN + match-003/HOME_WIN` | 20 | CNY 40.00 | CNY 135.20 | CNY 11.11 | 27.76% |
| `match-001/HOME_WIN + match-002/HOME_WIN` | 20 | CNY 40.00 | CNY 163.40 | CNY 10.98 | 27.45% |
| `match-002/HOME_WIN + match-003/HOME_WIN` | 40 | CNY 80.00 | CNY 306.00 | CNY 20.25 | 25.31% |
| `match-001/HOME_WIN + match-006/DRAW` | 20 | CNY 40.00 | CNY 239.40 | CNY 8.84 | 22.09% |

| Match exposure | Stake | Budget ratio |
|---|---:|---:|
| `match-001` | CNY 120.00 | 60.00% |
| `match-002` | CNY 120.00 | 60.00% |
| `match-003` | CNY 120.00 | 60.00% |
| `match-006` | CNY 40.00 | 20.00% |

| Stress scenario | Exposed stake | Result |
|---|---:|---|
| `ALL_EXPOSED_MATCHES_ADVERSE` | CNY 200.00 | P/L -200.00 CNY; recovery 0.00% |
| `TOP_EXPOSURE_MATCH_ADVERSE` | CNY 120.00 | capital CNY 0.00..CNY 306.00 |
| `TOP_TWO_EXPOSURE_MATCHES_ADVERSE` | CNY 200.00 | P/L -200.00 CNY; recovery 0.00% |
