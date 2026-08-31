# Manual Review Acceptance Comparison

> Audit report only. Do not use this file as web GPT input.

## Identity

- AnalysisRun ID: `manual-review-acceptance-001`
- Packet ID: `ff2d80b6-1c12-58b6-8ce5-2a89a497e27b`
- Packet hash: `d1e03f46a2e18e0acf70f07c9b08b968090b8adbddb689332f315947bc6be40b`
- LLMReviewArtifact ID: `6f2da5a4-db83-5fd1-ad00-b291c082f7b2`
- FusionRun ID: `19a59c30-1941-525e-832c-08d8445e2fe7`
- PortfolioRevision ID: `fbe3954c-dd1c-5e26-ab26-8af3b902fcdf`
- Handshake: `SUCCESS` (6 results, 0 fallbacks)
- AnalysisPacket: `D:\文档\xs\football\exchange\analysis_packet_v2.json`
- SQLite database: `D:\文档\xs\football\data\manual_review_acceptance.db`

## Probability Changes

| Match | Original P_final | P_llm | Review P_final | Applied delta | Clipped | Fallback |
|---|---|---|---|---|---|---|
| `match-001` | 60.00%/24.00%/16.00% | 56.05%/25.17%/18.78% | 59.65%/24.10%/16.24% | -0.35%/+0.10%/+0.24% | NO | - |
| `match-002` | 52.00%/27.00%/21.00% | 49.02%/27.38%/23.61% | 51.74%/27.03%/21.23% | -0.26%/+0.03%/+0.23% | NO | - |
| `match-003` | 63.00%/22.00%/15.00% | 60.29%/23.25%/16.46% | 62.76%/22.11%/15.13% | -0.24%/+0.11%/+0.13% | NO | - |
| `match-004` | 43.00%/29.00%/28.00% | 39.97%/29.22%/30.82% | 42.73%/29.02%/28.25% | -0.27%/+0.02%/+0.25% | NO | - |
| `match-005` | 36.00%/31.00%/33.00% | 35.40%/30.18%/34.43% | 35.95%/30.93%/33.12% | -0.05%/-0.07%/+0.12% | NO | - |
| `match-006` | 30.00%/34.00%/36.00% | 30.12%/31.88%/38.01% | 30.01%/33.81%/36.18% | +0.01%/-0.19%/+0.18% | NO | - |

## SelectionCandidate Changes

| Match/market/outcome | Original status | Revision status | Probability delta | EV delta |
|---|---|---|---:|---:|
| `match-001/THREE_WAY/AWAY_WIN` | REJECTED | REJECTED | +0.24% | +1.21% |
| `match-001/THREE_WAY/DRAW` | REJECTED | REJECTED | +0.10% | +0.35% |
| `match-001/THREE_WAY/HOME_WIN` | ELIGIBLE | ELIGIBLE | -0.35% | -0.66% |
| `match-002/THREE_WAY/AWAY_WIN` | REJECTED | REJECTED | +0.23% | +0.74% |
| `match-002/THREE_WAY/DRAW` | REJECTED | REJECTED | +0.03% | +0.11% |
| `match-002/THREE_WAY/HOME_WIN` | ELIGIBLE | ELIGIBLE | -0.26% | -0.56% |
| `match-003/THREE_WAY/AWAY_WIN` | REJECTED | REJECTED | +0.13% | +0.66% |
| `match-003/THREE_WAY/DRAW` | REJECTED | REJECTED | +0.11% | +0.41% |
| `match-003/THREE_WAY/HOME_WIN` | ELIGIBLE | ELIGIBLE | -0.24% | -0.42% |
| `match-004/THREE_WAY/AWAY_WIN` | REJECTED | REJECTED | +0.25% | +0.68% |
| `match-004/THREE_WAY/DRAW` | REJECTED | REJECTED | +0.02% | +0.06% |
| `match-004/THREE_WAY/HOME_WIN` | ELIGIBLE | ELIGIBLE | -0.27% | -0.65% |
| `match-005/THREE_WAY/AWAY_WIN` | REJECTED | REJECTED | +0.12% | +0.30% |
| `match-005/THREE_WAY/DRAW` | REJECTED | REJECTED | -0.07% | -0.23% |
| `match-005/THREE_WAY/HOME_WIN` | REJECTED | REJECTED | -0.05% | -0.15% |
| `match-006/THREE_WAY/AWAY_WIN` | REJECTED | REJECTED | +0.18% | +0.44% |
| `match-006/THREE_WAY/DRAW` | ELIGIBLE | ELIGIBLE | -0.19% | -0.58% |
| `match-006/THREE_WAY/HOME_WIN` | REJECTED | REJECTED | +0.01% | +0.03% |

## Portfolio, Cash, Exposure, and Stress Changes

### Budget CNY 100.00

- Status: `RECOMMENDED` -> `RECOMMENDED`
- Stake: CNY 100.00 -> CNY 100.00
- Cash: CNY 0.00 -> CNY 0.00
- More cash retained: `NO`
- Added tickets: none
- Deleted tickets: none
- Retained ticket allocation changes: none

| Match exposure | Original | Revision | Change |
|---|---:|---:|---:|
| `match-001` | CNY 60.00 | CNY 60.00 | +0.00 CNY |
| `match-002` | CNY 60.00 | CNY 60.00 | +0.00 CNY |
| `match-003` | CNY 60.00 | CNY 60.00 | +0.00 CNY |
| `match-006` | CNY 20.00 | CNY 20.00 | +0.00 CNY |

- Maximum match exposure: CNY 60.00 -> CNY 60.00
- Risk concentration improved: `NO`

| Stress scenario | Original | Revision |
|---|---|---|
| `ALL_EXPOSED_MATCHES_ADVERSE` | P/L -100.00 CNY; recovery 0.00% | P/L -100.00 CNY; recovery 0.00% |
| `TOP_EXPOSURE_MATCH_ADVERSE` | capital CNY 0.00..CNY 153.00 | capital CNY 0.00..CNY 153.00 |
| `TOP_TWO_EXPOSURE_MATCHES_ADVERSE` | P/L -100.00 CNY; recovery 0.00% | P/L -100.00 CNY; recovery 0.00% |

### Budget CNY 200.00

- Status: `RECOMMENDED` -> `RECOMMENDED`
- Stake: CNY 200.00 -> CNY 200.00
- Cash: CNY 0.00 -> CNY 0.00
- More cash retained: `NO`
- Added tickets: none
- Deleted tickets: none
- Retained ticket allocation changes: none

| Match exposure | Original | Revision | Change |
|---|---:|---:|---:|
| `match-001` | CNY 120.00 | CNY 120.00 | +0.00 CNY |
| `match-002` | CNY 120.00 | CNY 120.00 | +0.00 CNY |
| `match-003` | CNY 120.00 | CNY 120.00 | +0.00 CNY |
| `match-006` | CNY 40.00 | CNY 40.00 | +0.00 CNY |

- Maximum match exposure: CNY 120.00 -> CNY 120.00
- Risk concentration improved: `NO`

| Stress scenario | Original | Revision |
|---|---|---|
| `ALL_EXPOSED_MATCHES_ADVERSE` | P/L -200.00 CNY; recovery 0.00% | P/L -200.00 CNY; recovery 0.00% |
| `TOP_EXPOSURE_MATCH_ADVERSE` | capital CNY 0.00..CNY 306.00 | capital CNY 0.00..CNY 306.00 |
| `TOP_TWO_EXPOSURE_MATCHES_ADVERSE` | P/L -200.00 CNY; recovery 0.00% | P/L -200.00 CNY; recovery 0.00% |
