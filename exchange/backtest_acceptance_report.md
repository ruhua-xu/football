# Backtest Comparison

**data_mode_label: LIVE_STRICT**

**SYNTHETIC ACCEPTANCE DATA**

**NOT REAL HISTORICAL PERFORMANCE**

## Side-by-Side Metrics
| metric | left | right |
| --- | --- | --- |
| fusion_policy | QUANT_ONLY_V1 | MARKET_QUANT_BLEND_V1 |
| backtest_run_id | acceptance-quant-only-v040 | acceptance-market-quant-blend-v040 |
| Brier (P_final) | 0.395661016949 | 0.428734712113 |
| LogLoss (P_final) | 0.71739548562 | 0.763860053431 |
| ROI_on_budget | 0.62106 | 0.60279 |
| ROI_on_deployed | 5.750555555556 | 5.581388888889 |
| Drawdown (fen) | 0 | 0 |
| NO_BET_count | 1 | 1 |
| NO_BET_ratio | 0.1 | 0.1 |
| Ticket Hit Rate | 0.944444444444 | 0.944444444444 |

## Left Full Report
# Walk-Forward Backtest Report

**data_mode_label: LIVE_STRICT**

**SYNTHETIC ACCEPTANCE DATA**

**NOT REAL HISTORICAL PERFORMANCE**

**PARTIAL MATCH RESULT COVERAGE**
- match_result_coverage_warning: 0aed2d47-a260-5a78-a1f7-286bf9fc92a3; settled=5; decision_matches=6

## Run Lineage
- backtest_run_id: acceptance-quant-only-v040
- backtest_version: BACKTEST_V1
- fusion_policy: QUANT_ONLY_V1
- strategy_config_hash: f57c09e216612e8a25c6feb0494f9aa3f02f86fd6f2f63bb3f26083b181479ab
- strategy_config_json: `{"budget_fen":10000,"min_selection_ev":"0.05","min_ticket_roi":"0.05","portfolio_constraints":{"absolute_max_tickets":2,"concentration_penalty":"0","extra_ticket_min_roi":"0.2","max_match_exposure_ratio":"0.4","max_selection_exposure_ratio":"0.4","min_marginal_score":"0","operational_complexity_penalty":"0.01","preferred_max_tickets":2},"quant_weight":"1","slate_policy":"DAILY_FIXED_CUTOFF_V1"}`
- code_revision: package:d7c9b6c1733f1a8dc305c5db9f64d605456fba44f79167a2b3b39548eee24ae6
- date_from: 2025-01-06
- date_to: 2025-01-15
- created_at_utc: 2026-09-01T14:33:24.868104Z
- execution_created_at_utc: 2026-09-01T14:33:24.868104Z
- status: COMPLETED

## Probability Metrics
- metrics_version: BACKTEST_METRICS_V1
- log_loss_clip_version: EPSILON_CLIP_V1
- log_loss_epsilon: 0.000001

### P_market
- sample_count: 59
- multiclass_Brier: 0.513263875944
- Brier_H: 0.157492846655
- Brier_D: 0.202910200593
- Brier_A: 0.152860828696
- multiclass_LogLoss: 0.884115054646
- log_loss_clip_version: EPSILON_CLIP_V1
- log_loss_epsilon: 0.000001
- ECE: 0.230786582285
- calibration_bins:
  - 0.0-0.1: mean_probability=0.099405495153; frequency=0; absolute_gap=0.099405495153; count=1
  - 0.1-0.2: mean_probability=0.150213919826; frequency=0; absolute_gap=0.150213919826; count=2
  - 0.2-0.3: mean_probability=0.266997055965; frequency=0; absolute_gap=0.266997055965; count=75
  - 0.3-0.4: mean_probability=0.333333333333; frequency=0.333333333333; absolute_gap=0; count=60
  - 0.4-0.5: mean_probability=0.448393571188; frequency=1; absolute_gap=0.551606428812; count=29
  - 0.5-0.6: mean_probability=0.521903561298; frequency=1; absolute_gap=0.478096438702; count=8
  - 0.6-0.7: mean_probability=0.658970253645; frequency=1; absolute_gap=0.341029746355; count=1
  - 0.7-0.8: mean_probability=0.737775159338; frequency=1; absolute_gap=0.262224840662; count=1
  - 0.8-0.9: mean_probability=N/A; frequency=N/A; absolute_gap=N/A; count=0
  - 0.9-1.0: mean_probability=N/A; frequency=N/A; absolute_gap=N/A; count=0

### P_quant
- sample_count: 59
- multiclass_Brier: 0.395661016949
- Brier_H: 0.121320338983
- Brier_D: 0.160754237288
- Brier_A: 0.113586440678
- multiclass_LogLoss: 0.71739548562
- log_loss_clip_version: EPSILON_CLIP_V1
- log_loss_epsilon: 0.000001
- ECE: 0.308813559322
- calibration_bins:
  - 0.0-0.1: mean_probability=0.06; frequency=0; absolute_gap=0.06; count=1
  - 0.1-0.2: mean_probability=0.162; frequency=0; absolute_gap=0.162; count=20
  - 0.2-0.3: mean_probability=0.255733333333; frequency=0; absolute_gap=0.255733333333; count=75
  - 0.3-0.4: mean_probability=0.327083333333; frequency=0.125; absolute_gap=0.202083333333; count=24
  - 0.4-0.5: mean_probability=0.415454545455; frequency=0.954545454545; absolute_gap=0.539090909091; count=22
  - 0.5-0.6: mean_probability=0.5252; frequency=1; absolute_gap=0.4748; count=25
  - 0.6-0.7: mean_probability=0.6; frequency=1; absolute_gap=0.4; count=8
  - 0.7-0.8: mean_probability=0.78; frequency=1; absolute_gap=0.22; count=1
  - 0.8-0.9: mean_probability=0.82; frequency=1; absolute_gap=0.18; count=1
  - 0.9-1.0: mean_probability=N/A; frequency=N/A; absolute_gap=N/A; count=0

### P_final
- sample_count: 59
- multiclass_Brier: 0.395661016949
- Brier_H: 0.121320338983
- Brier_D: 0.160754237288
- Brier_A: 0.113586440678
- multiclass_LogLoss: 0.71739548562
- log_loss_clip_version: EPSILON_CLIP_V1
- log_loss_epsilon: 0.000001
- ECE: 0.308813559322
- calibration_bins:
  - 0.0-0.1: mean_probability=0.06; frequency=0; absolute_gap=0.06; count=1
  - 0.1-0.2: mean_probability=0.162; frequency=0; absolute_gap=0.162; count=20
  - 0.2-0.3: mean_probability=0.255733333333; frequency=0; absolute_gap=0.255733333333; count=75
  - 0.3-0.4: mean_probability=0.327083333333; frequency=0.125; absolute_gap=0.202083333333; count=24
  - 0.4-0.5: mean_probability=0.415454545455; frequency=0.954545454545; absolute_gap=0.539090909091; count=22
  - 0.5-0.6: mean_probability=0.5252; frequency=1; absolute_gap=0.4748; count=25
  - 0.6-0.7: mean_probability=0.6; frequency=1; absolute_gap=0.4; count=8
  - 0.7-0.8: mean_probability=0.78; frequency=1; absolute_gap=0.22; count=1
  - 0.8-0.9: mean_probability=0.82; frequency=1; absolute_gap=0.18; count=1
  - 0.9-1.0: mean_probability=N/A; frequency=N/A; absolute_gap=N/A; count=0

## Aggregate Metrics
- slate_count: 10
- settled_slate_count: 9
- slate_coverage: 0.9
- match_count: 60
- settled_match_count: 59
- match_coverage: 0.983333333333
- ticket_count: 18
- settled_ticket_count: 18
- ticket_coverage: 1
- total_budget: 100000 fen (1000.00 Yuan)
- total_stake: 10800 fen (108.00 Yuan)
- total_settled_stake: 10800 fen (108.00 Yuan)
- total_cash: 89200 fen (892.00 Yuan)
- gross_payout: 72906 fen (729.06 Yuan)
- profit_loss: 62106 fen (621.06 Yuan)
- ROI_on_budget: 0.62106
- ROI_on_deployed: 5.750555555556
- winning_ticket_count: 17
- ticket_hit_rate: 0.944444444444
- NO_BET_count: 1
- NO_BET_ratio: 0.1
- max_drawdown: 0 fen (0.00 Yuan)
- max_consecutive_losing_slates: 0
- average_ticket_odds: 7.233888888889
- average_ticket_probability: 0.206733333333
- average_selection_EV: 0.196611111111
- max_match_exposure: 1200 fen (12.00 Yuan)
- max_selection_exposure: 1200 fen (12.00 Yuan)
- realized_loss_when_top_exposure_failed: 0 fen (0.00 Yuan)
- realized_loss_when_top_two_exposure_failed: 600 fen (6.00 Yuan)

## Slices
### Slice 1
- slice_id: aad39059-2290-50e4-95c3-42b5372ce57c
- analysis_run_id: f8a563b6-ab5f-5818-bc13-6a5821d359f9
- decision_as_of_at_utc: 2025-01-06T09:00:00Z
- kickoff_from_utc: 2025-01-06T12:00:00Z
- kickoff_to_utc: 2025-01-06T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-07T03:00:00Z
- expected_match_ids: ha-20250106-01,ha-20250106-02,ha-20250106-03,ha-20250106-04,ha-20250106-05,ha-20250106-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: 797442dd0f18c35198e1bc416df26753fbe2a2e053f934c264420bbba981ec13
- match_result_ids: ha-result-ha-20250106-01-v1,ha-result-ha-20250106-02-v1,ha-result-ha-20250106-03-v1,ha-result-ha-20250106-04-v1,ha-result-ha-20250106-05-v1,ha-result-ha-20250106-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 2
- slice_id: fea066d1-9d01-5256-ac65-c90030c78c37
- analysis_run_id: ef544961-55f4-598e-b671-5a98363add53
- decision_as_of_at_utc: 2025-01-07T09:00:00Z
- kickoff_from_utc: 2025-01-07T12:00:00Z
- kickoff_to_utc: 2025-01-07T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-08T03:00:00Z
- expected_match_ids: ha-20250107-01,ha-20250107-02,ha-20250107-03,ha-20250107-04,ha-20250107-05,ha-20250107-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: 2e30bbfefa1c2dbb2482e1ca0b5a26e6a106741226e1d581de5a7d1d57dcb5be
- match_result_ids: ha-result-ha-20250107-01-v1,ha-result-ha-20250107-02-v1,ha-result-ha-20250107-03-v1,ha-result-ha-20250107-04-v1,ha-result-ha-20250107-05-v1,ha-result-ha-20250107-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 3
- slice_id: 7fb3b5d0-9f3d-5c0f-bba5-c1827fe7f9e9
- analysis_run_id: 6af54ec9-5100-5322-956c-4f95299e68a9
- decision_as_of_at_utc: 2025-01-08T09:00:00Z
- kickoff_from_utc: 2025-01-08T12:00:00Z
- kickoff_to_utc: 2025-01-08T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-09T03:00:00Z
- expected_match_ids: ha-20250108-01,ha-20250108-02,ha-20250108-03,ha-20250108-04,ha-20250108-05,ha-20250108-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: b2613ca2c0e27add7ec11d1a7d0792e26e4eeb258b34d967b1fb09f474328c27
- match_result_ids: ha-result-ha-20250108-01-v1,ha-result-ha-20250108-02-v1,ha-result-ha-20250108-03-v1,ha-result-ha-20250108-04-v1,ha-result-ha-20250108-05-v1,ha-result-ha-20250108-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 4
- slice_id: 84921ef5-0f35-584b-b831-69867789e103
- analysis_run_id: 3e212d7f-758d-527b-a6f9-a623ff959d28
- decision_as_of_at_utc: 2025-01-09T09:00:00Z
- kickoff_from_utc: 2025-01-09T12:00:00Z
- kickoff_to_utc: 2025-01-09T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-10T03:00:00Z
- expected_match_ids: ha-20250109-01,ha-20250109-02,ha-20250109-03,ha-20250109-04,ha-20250109-05,ha-20250109-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: 02ccf697dca7e57dd51dc108a873e2f00f739b2ac8dfaa8ec8c5a6e215a8c294
- match_result_ids: ha-result-ha-20250109-01-v1,ha-result-ha-20250109-02-v1,ha-result-ha-20250109-03-v1,ha-result-ha-20250109-04-v1,ha-result-ha-20250109-05-v1,ha-result-ha-20250109-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 5
- slice_id: 1db32182-2efc-5324-88be-f7500268104e
- analysis_run_id: 762c061d-9da6-5e23-9d2e-9343b0783d15
- decision_as_of_at_utc: 2025-01-10T09:00:00Z
- kickoff_from_utc: 2025-01-10T12:00:00Z
- kickoff_to_utc: 2025-01-10T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-11T03:00:00Z
- expected_match_ids: ha-20250110-01,ha-20250110-02,ha-20250110-03,ha-20250110-04,ha-20250110-05,ha-20250110-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: 574531fc857e5e8b12690d4feed4561f6c5e5a7481dbdb80a8603b743ed7ddfb
- match_result_ids: ha-result-ha-20250110-01-v1,ha-result-ha-20250110-02-v1,ha-result-ha-20250110-03-v1,ha-result-ha-20250110-04-v1,ha-result-ha-20250110-05-v1,ha-result-ha-20250110-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 6
- slice_id: 52ca73c1-1493-530b-985e-3a93bb2a1aa4
- analysis_run_id: eae8e22a-2931-51d3-9245-a41ea2405acd
- decision_as_of_at_utc: 2025-01-11T09:00:00Z
- kickoff_from_utc: 2025-01-11T12:00:00Z
- kickoff_to_utc: 2025-01-11T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-12T03:00:00Z
- expected_match_ids: ha-20250111-01,ha-20250111-02,ha-20250111-03,ha-20250111-04,ha-20250111-05,ha-20250111-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: a3600968ef11f93a77c13b20a3604aa0cf2981a3ebda2682af5d6877435c9961
- match_result_ids: ha-result-ha-20250111-01-v1,ha-result-ha-20250111-02-v1,ha-result-ha-20250111-03-v1,ha-result-ha-20250111-04-v1,ha-result-ha-20250111-05-v1,ha-result-ha-20250111-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 7
- slice_id: 89c83e7c-ad19-560a-902b-292e3521615d
- analysis_run_id: 3b2a97c9-497f-55c3-b16e-f57cbcabe74b
- decision_as_of_at_utc: 2025-01-12T09:00:00Z
- kickoff_from_utc: 2025-01-12T12:00:00Z
- kickoff_to_utc: 2025-01-12T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-13T03:00:00Z
- expected_match_ids: ha-20250112-01,ha-20250112-02,ha-20250112-03,ha-20250112-04,ha-20250112-05,ha-20250112-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: ad161bc4ace2d14cd868f23b10746cbcf79b355643fe7e4f716cf836f1e2b417
- match_result_ids: ha-result-ha-20250112-01-v1,ha-result-ha-20250112-02-v1,ha-result-ha-20250112-03-v1,ha-result-ha-20250112-04-v1,ha-result-ha-20250112-05-v1,ha-result-ha-20250112-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 0
- settled_ticket_count: 0
- ticket_coverage: N/A
### Slice 8
- slice_id: 64bdec57-fb7d-5768-a9ab-0be08a1b75bd
- analysis_run_id: f7e80d7f-323a-5f28-aeb5-26d3a91e48e8
- decision_as_of_at_utc: 2025-01-13T09:00:00Z
- kickoff_from_utc: 2025-01-13T12:00:00Z
- kickoff_to_utc: 2025-01-13T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-14T03:00:00Z
- expected_match_ids: ha-20250113-01,ha-20250113-02,ha-20250113-03,ha-20250113-04,ha-20250113-05,ha-20250113-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: 151e416ee4e1846028cc7517c0596693e13780c007c5c951140692732beee8c9
- match_result_ids: ha-result-ha-20250113-01-v1,ha-result-ha-20250113-02-v1,ha-result-ha-20250113-03-v1,ha-result-ha-20250113-04-v1,ha-result-ha-20250113-05-v1,ha-result-ha-20250113-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 9
- slice_id: 6ccc58df-9fd0-5434-8315-ec3cd94f7b04
- analysis_run_id: 3ab082de-4eae-5493-a81d-d3156be5d252
- decision_as_of_at_utc: 2025-01-14T09:00:00Z
- kickoff_from_utc: 2025-01-14T12:00:00Z
- kickoff_to_utc: 2025-01-14T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-15T03:00:00Z
- expected_match_ids: ha-20250114-01,ha-20250114-02,ha-20250114-03,ha-20250114-04,ha-20250114-05,ha-20250114-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: 1e60eddf1fbb638ec86d2d7077b6f70660457c0d5a83154a3bafe7f98b5997e9
- match_result_ids: ha-result-ha-20250114-01-v1,ha-result-ha-20250114-02-v1,ha-result-ha-20250114-03-v1,ha-result-ha-20250114-04-v1,ha-result-ha-20250114-05-v1,ha-result-ha-20250114-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 10
- slice_id: 0aed2d47-a260-5a78-a1f7-286bf9fc92a3
- analysis_run_id: 448a6edd-f4d5-59c5-b70e-032bf7b1bc1b
- decision_as_of_at_utc: 2025-01-15T09:00:00Z
- kickoff_from_utc: 2025-01-15T12:00:00Z
- kickoff_to_utc: 2025-01-15T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-16T03:00:00Z
- expected_match_ids: ha-20250115-01,ha-20250115-02,ha-20250115-03,ha-20250115-04,ha-20250115-05,ha-20250115-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: a258f1b63e6eebaabc5ae3b717497878323df766fcf0867ced11bdc1b30be9ae
- match_result_ids: ha-result-ha-20250115-01-v1,ha-result-ha-20250115-02-v1,ha-result-ha-20250115-03-v1,ha-result-ha-20250115-04-v1,ha-result-ha-20250115-05-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 5
- match_coverage: 0.833333333333
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1

## Archive Provenance
### FIXTURES
- archive_id: historical-acceptance-fixtures-v1
- archive_schema_version: HISTORICAL_ARCHIVE_V1
- provider_code: SYNTHETIC_ACCEPTANCE_V1
- payload_sha256: ffbaa93ec8bfa966778b30f9308caa46ccff32f5a688808501adef14220334ce
- source_reference: synthetic-acceptance://fixtures.json
- source_description: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE. Deterministic static acceptance fixture.
- license_note: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE
### MANUAL_QUANT
- archive_id: historical-acceptance-manual_quant-v1
- archive_schema_version: HISTORICAL_ARCHIVE_V1
- provider_code: SYNTHETIC_ACCEPTANCE_V1
- payload_sha256: 70a70c444f493412265038dbf3767e9c56a69575d4bca7375ea463de9983b7f4
- source_reference: synthetic-acceptance://manual_quant.json
- source_description: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE. Deterministic static acceptance fixture.
- license_note: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE
### MARKET_ODDS
- archive_id: historical-acceptance-market_odds-v1
- archive_schema_version: HISTORICAL_ARCHIVE_V1
- provider_code: SYNTHETIC_ACCEPTANCE_V1
- payload_sha256: 0ad75a56140427b864a5c0ad5fa56f9f7e94188605999350b13ddfaf36f70ab6
- source_reference: synthetic-acceptance://market_odds.json
- source_description: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE. Deterministic static acceptance fixture.
- license_note: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE
### MATCH_RESULTS
- archive_id: historical-acceptance-match_results-v1
- archive_schema_version: HISTORICAL_ARCHIVE_V1
- provider_code: SYNTHETIC_ACCEPTANCE_V1
- payload_sha256: bf02922605c7fe21ec13446549eb0b18d069bac5e3cdf0dca148e244d13a2b7e
- source_reference: synthetic-acceptance://match_results.json
- source_description: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE. Deterministic static acceptance fixture.
- license_note: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE
### PROVIDER_MAPPINGS
- archive_id: historical-acceptance-provider_mappings-v1
- archive_schema_version: HISTORICAL_ARCHIVE_V1
- provider_code: SYNTHETIC_ACCEPTANCE_V1
- payload_sha256: 8da43b07b0a44c654622d7ff6c4bfb27abc3916724d49595e897da9b384984a0
- source_reference: synthetic-acceptance://provider_mappings.json
- source_description: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE. Deterministic static acceptance fixture.
- license_note: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE
### SPORTTERY_BONUS
- archive_id: historical-acceptance-sporttery_bonus-v1
- archive_schema_version: HISTORICAL_ARCHIVE_V1
- provider_code: SYNTHETIC_ACCEPTANCE_V1
- payload_sha256: 204cac6b639c66949bd0add68f59f2de306aab5a13cc64100f133c88a3865dd2
- source_reference: synthetic-acceptance://sporttery_bonus.json
- source_description: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE. Deterministic static acceptance fixture.
- license_note: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE

## Right Full Report
# Walk-Forward Backtest Report

**data_mode_label: LIVE_STRICT**

**SYNTHETIC ACCEPTANCE DATA**

**NOT REAL HISTORICAL PERFORMANCE**

**PARTIAL MATCH RESULT COVERAGE**
- match_result_coverage_warning: 2255dba8-64fc-5a21-843e-7a4328511b3b; settled=5; decision_matches=6

## Run Lineage
- backtest_run_id: acceptance-market-quant-blend-v040
- backtest_version: BACKTEST_V1
- fusion_policy: MARKET_QUANT_BLEND_V1
- strategy_config_hash: 838c42fbadf055a900ad6ce03fb75c4762f1d9834d6ad6acdaae89ffd502a411
- strategy_config_json: `{"budget_fen":10000,"min_selection_ev":"0.05","min_ticket_roi":"0.05","portfolio_constraints":{"absolute_max_tickets":2,"concentration_penalty":"0","extra_ticket_min_roi":"0.2","max_match_exposure_ratio":"0.4","max_selection_exposure_ratio":"0.4","min_marginal_score":"0","operational_complexity_penalty":"0.01","preferred_max_tickets":2},"quant_weight":"0.7","slate_policy":"DAILY_FIXED_CUTOFF_V1"}`
- code_revision: package:d7c9b6c1733f1a8dc305c5db9f64d605456fba44f79167a2b3b39548eee24ae6
- date_from: 2025-01-06
- date_to: 2025-01-15
- created_at_utc: 2026-09-01T14:33:48.716757Z
- execution_created_at_utc: 2026-09-01T14:33:48.716757Z
- status: COMPLETED

## Probability Metrics
- metrics_version: BACKTEST_METRICS_V1
- log_loss_clip_version: EPSILON_CLIP_V1
- log_loss_epsilon: 0.000001

### P_market
- sample_count: 59
- multiclass_Brier: 0.513263875944
- Brier_H: 0.157492846655
- Brier_D: 0.202910200593
- Brier_A: 0.152860828696
- multiclass_LogLoss: 0.884115054646
- log_loss_clip_version: EPSILON_CLIP_V1
- log_loss_epsilon: 0.000001
- ECE: 0.230786582285
- calibration_bins:
  - 0.0-0.1: mean_probability=0.099405495153; frequency=0; absolute_gap=0.099405495153; count=1
  - 0.1-0.2: mean_probability=0.150213919826; frequency=0; absolute_gap=0.150213919826; count=2
  - 0.2-0.3: mean_probability=0.266997055965; frequency=0; absolute_gap=0.266997055965; count=75
  - 0.3-0.4: mean_probability=0.333333333333; frequency=0.333333333333; absolute_gap=0; count=60
  - 0.4-0.5: mean_probability=0.448393571188; frequency=1; absolute_gap=0.551606428812; count=29
  - 0.5-0.6: mean_probability=0.521903561298; frequency=1; absolute_gap=0.478096438702; count=8
  - 0.6-0.7: mean_probability=0.658970253645; frequency=1; absolute_gap=0.341029746355; count=1
  - 0.7-0.8: mean_probability=0.737775159338; frequency=1; absolute_gap=0.262224840662; count=1
  - 0.8-0.9: mean_probability=N/A; frequency=N/A; absolute_gap=N/A; count=0
  - 0.9-1.0: mean_probability=N/A; frequency=N/A; absolute_gap=N/A; count=0

### P_quant
- sample_count: 59
- multiclass_Brier: 0.395661016949
- Brier_H: 0.121320338983
- Brier_D: 0.160754237288
- Brier_A: 0.113586440678
- multiclass_LogLoss: 0.71739548562
- log_loss_clip_version: EPSILON_CLIP_V1
- log_loss_epsilon: 0.000001
- ECE: 0.308813559322
- calibration_bins:
  - 0.0-0.1: mean_probability=0.06; frequency=0; absolute_gap=0.06; count=1
  - 0.1-0.2: mean_probability=0.162; frequency=0; absolute_gap=0.162; count=20
  - 0.2-0.3: mean_probability=0.255733333333; frequency=0; absolute_gap=0.255733333333; count=75
  - 0.3-0.4: mean_probability=0.327083333333; frequency=0.125; absolute_gap=0.202083333333; count=24
  - 0.4-0.5: mean_probability=0.415454545455; frequency=0.954545454545; absolute_gap=0.539090909091; count=22
  - 0.5-0.6: mean_probability=0.5252; frequency=1; absolute_gap=0.4748; count=25
  - 0.6-0.7: mean_probability=0.6; frequency=1; absolute_gap=0.4; count=8
  - 0.7-0.8: mean_probability=0.78; frequency=1; absolute_gap=0.22; count=1
  - 0.8-0.9: mean_probability=0.82; frequency=1; absolute_gap=0.18; count=1
  - 0.9-1.0: mean_probability=N/A; frequency=N/A; absolute_gap=N/A; count=0

### P_final
- sample_count: 59
- multiclass_Brier: 0.428734712113
- Brier_H: 0.13149884913
- Brier_D: 0.172702121772
- Brier_A: 0.124533741211
- multiclass_LogLoss: 0.763860053431
- log_loss_clip_version: EPSILON_CLIP_V1
- log_loss_epsilon: 0.000001
- ECE: 0.267442497191
- calibration_bins:
  - 0.0-0.1: mean_probability=0.071821648546; frequency=0; absolute_gap=0.071821648546; count=1
  - 0.1-0.2: mean_probability=0.162627738319; frequency=0; absolute_gap=0.162627738319; count=11
  - 0.2-0.3: mean_probability=0.259618264659; frequency=0; absolute_gap=0.259618264659; count=84
  - 0.3-0.4: mean_probability=0.35370768472; frequency=0.47619047619; absolute_gap=0.12248279147; count=42
  - 0.4-0.5: mean_probability=0.477051773982; frequency=1; absolute_gap=0.522948226018; count=20
  - 0.5-0.6: mean_probability=0.552679831576; frequency=1; absolute_gap=0.447320168424; count=17
  - 0.6-0.7: mean_probability=N/A; frequency=N/A; absolute_gap=N/A; count=0
  - 0.7-0.8: mean_probability=0.769511811947; frequency=1; absolute_gap=0.230488188053; count=2
  - 0.8-0.9: mean_probability=N/A; frequency=N/A; absolute_gap=N/A; count=0
  - 0.9-1.0: mean_probability=N/A; frequency=N/A; absolute_gap=N/A; count=0

## Aggregate Metrics
- slate_count: 10
- settled_slate_count: 9
- slate_coverage: 0.9
- match_count: 60
- settled_match_count: 59
- match_coverage: 0.983333333333
- ticket_count: 18
- settled_ticket_count: 18
- ticket_coverage: 1
- total_budget: 100000 fen (1000.00 Yuan)
- total_stake: 10800 fen (108.00 Yuan)
- total_settled_stake: 10800 fen (108.00 Yuan)
- total_cash: 89200 fen (892.00 Yuan)
- gross_payout: 71079 fen (710.79 Yuan)
- profit_loss: 60279 fen (602.79 Yuan)
- ROI_on_budget: 0.60279
- ROI_on_deployed: 5.581388888889
- winning_ticket_count: 17
- ticket_hit_rate: 0.944444444444
- NO_BET_count: 1
- NO_BET_ratio: 0.1
- max_drawdown: 0 fen (0.00 Yuan)
- max_consecutive_losing_slates: 0
- average_ticket_odds: 7.064722222222
- average_ticket_probability: 0.187551625125
- average_selection_EV: 0.118507395
- max_match_exposure: 1200 fen (12.00 Yuan)
- max_selection_exposure: 1200 fen (12.00 Yuan)
- realized_loss_when_top_exposure_failed: 0 fen (0.00 Yuan)
- realized_loss_when_top_two_exposure_failed: 0 fen (0.00 Yuan)

## Slices
### Slice 1
- slice_id: c957a619-48c0-5dc5-a4ec-fc989b064df0
- analysis_run_id: 0a942420-84b7-5684-9b03-54d21dad57e5
- decision_as_of_at_utc: 2025-01-06T09:00:00Z
- kickoff_from_utc: 2025-01-06T12:00:00Z
- kickoff_to_utc: 2025-01-06T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-07T03:00:00Z
- expected_match_ids: ha-20250106-01,ha-20250106-02,ha-20250106-03,ha-20250106-04,ha-20250106-05,ha-20250106-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: 797442dd0f18c35198e1bc416df26753fbe2a2e053f934c264420bbba981ec13
- match_result_ids: ha-result-ha-20250106-01-v1,ha-result-ha-20250106-02-v1,ha-result-ha-20250106-03-v1,ha-result-ha-20250106-04-v1,ha-result-ha-20250106-05-v1,ha-result-ha-20250106-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 2
- slice_id: 14f1dfe8-f261-5871-93e0-1f578b134832
- analysis_run_id: e22513d2-3b8d-5c5b-b7e2-2809575e443b
- decision_as_of_at_utc: 2025-01-07T09:00:00Z
- kickoff_from_utc: 2025-01-07T12:00:00Z
- kickoff_to_utc: 2025-01-07T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-08T03:00:00Z
- expected_match_ids: ha-20250107-01,ha-20250107-02,ha-20250107-03,ha-20250107-04,ha-20250107-05,ha-20250107-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: 2e30bbfefa1c2dbb2482e1ca0b5a26e6a106741226e1d581de5a7d1d57dcb5be
- match_result_ids: ha-result-ha-20250107-01-v1,ha-result-ha-20250107-02-v1,ha-result-ha-20250107-03-v1,ha-result-ha-20250107-04-v1,ha-result-ha-20250107-05-v1,ha-result-ha-20250107-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 3
- slice_id: 25e4b5f0-bbd6-5c11-92e5-f9ea5461559a
- analysis_run_id: 8aefacc5-94ed-5c3c-9bae-e1eae4d457ce
- decision_as_of_at_utc: 2025-01-08T09:00:00Z
- kickoff_from_utc: 2025-01-08T12:00:00Z
- kickoff_to_utc: 2025-01-08T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-09T03:00:00Z
- expected_match_ids: ha-20250108-01,ha-20250108-02,ha-20250108-03,ha-20250108-04,ha-20250108-05,ha-20250108-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: b2613ca2c0e27add7ec11d1a7d0792e26e4eeb258b34d967b1fb09f474328c27
- match_result_ids: ha-result-ha-20250108-01-v1,ha-result-ha-20250108-02-v1,ha-result-ha-20250108-03-v1,ha-result-ha-20250108-04-v1,ha-result-ha-20250108-05-v1,ha-result-ha-20250108-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 4
- slice_id: ae81a0a7-34c1-51a9-8470-ef1116df53d1
- analysis_run_id: cbaf23a2-8dad-5e37-8323-e39185b4ee58
- decision_as_of_at_utc: 2025-01-09T09:00:00Z
- kickoff_from_utc: 2025-01-09T12:00:00Z
- kickoff_to_utc: 2025-01-09T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-10T03:00:00Z
- expected_match_ids: ha-20250109-01,ha-20250109-02,ha-20250109-03,ha-20250109-04,ha-20250109-05,ha-20250109-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: 02ccf697dca7e57dd51dc108a873e2f00f739b2ac8dfaa8ec8c5a6e215a8c294
- match_result_ids: ha-result-ha-20250109-01-v1,ha-result-ha-20250109-02-v1,ha-result-ha-20250109-03-v1,ha-result-ha-20250109-04-v1,ha-result-ha-20250109-05-v1,ha-result-ha-20250109-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 5
- slice_id: 3bea76d9-a512-5f37-b52b-9d5742f1760b
- analysis_run_id: 199b5db3-f8f9-57a3-9482-405da7c93435
- decision_as_of_at_utc: 2025-01-10T09:00:00Z
- kickoff_from_utc: 2025-01-10T12:00:00Z
- kickoff_to_utc: 2025-01-10T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-11T03:00:00Z
- expected_match_ids: ha-20250110-01,ha-20250110-02,ha-20250110-03,ha-20250110-04,ha-20250110-05,ha-20250110-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: 574531fc857e5e8b12690d4feed4561f6c5e5a7481dbdb80a8603b743ed7ddfb
- match_result_ids: ha-result-ha-20250110-01-v1,ha-result-ha-20250110-02-v1,ha-result-ha-20250110-03-v1,ha-result-ha-20250110-04-v1,ha-result-ha-20250110-05-v1,ha-result-ha-20250110-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 6
- slice_id: 7975966e-2944-55ad-b737-7e3df48ba8d6
- analysis_run_id: fca87fd9-52fc-5072-a147-b349210bc563
- decision_as_of_at_utc: 2025-01-11T09:00:00Z
- kickoff_from_utc: 2025-01-11T12:00:00Z
- kickoff_to_utc: 2025-01-11T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-12T03:00:00Z
- expected_match_ids: ha-20250111-01,ha-20250111-02,ha-20250111-03,ha-20250111-04,ha-20250111-05,ha-20250111-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: a3600968ef11f93a77c13b20a3604aa0cf2981a3ebda2682af5d6877435c9961
- match_result_ids: ha-result-ha-20250111-01-v1,ha-result-ha-20250111-02-v1,ha-result-ha-20250111-03-v1,ha-result-ha-20250111-04-v1,ha-result-ha-20250111-05-v1,ha-result-ha-20250111-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 7
- slice_id: 0cbbadb1-58fa-5f44-968b-a1e610e6c544
- analysis_run_id: 7c5c28d0-4174-5b6d-bdd1-6d29796890e7
- decision_as_of_at_utc: 2025-01-12T09:00:00Z
- kickoff_from_utc: 2025-01-12T12:00:00Z
- kickoff_to_utc: 2025-01-12T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-13T03:00:00Z
- expected_match_ids: ha-20250112-01,ha-20250112-02,ha-20250112-03,ha-20250112-04,ha-20250112-05,ha-20250112-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: ad161bc4ace2d14cd868f23b10746cbcf79b355643fe7e4f716cf836f1e2b417
- match_result_ids: ha-result-ha-20250112-01-v1,ha-result-ha-20250112-02-v1,ha-result-ha-20250112-03-v1,ha-result-ha-20250112-04-v1,ha-result-ha-20250112-05-v1,ha-result-ha-20250112-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 0
- settled_ticket_count: 0
- ticket_coverage: N/A
### Slice 8
- slice_id: 742841a5-6e56-5f78-af36-04b8c7909c07
- analysis_run_id: 5fa7e931-6bbe-5dd8-a6e5-7ba847591fd7
- decision_as_of_at_utc: 2025-01-13T09:00:00Z
- kickoff_from_utc: 2025-01-13T12:00:00Z
- kickoff_to_utc: 2025-01-13T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-14T03:00:00Z
- expected_match_ids: ha-20250113-01,ha-20250113-02,ha-20250113-03,ha-20250113-04,ha-20250113-05,ha-20250113-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: 151e416ee4e1846028cc7517c0596693e13780c007c5c951140692732beee8c9
- match_result_ids: ha-result-ha-20250113-01-v1,ha-result-ha-20250113-02-v1,ha-result-ha-20250113-03-v1,ha-result-ha-20250113-04-v1,ha-result-ha-20250113-05-v1,ha-result-ha-20250113-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 9
- slice_id: d4e98a50-7694-528f-96f4-e344dcf72da7
- analysis_run_id: f29c5a87-ea48-5cff-ac5c-bd22c7ec4480
- decision_as_of_at_utc: 2025-01-14T09:00:00Z
- kickoff_from_utc: 2025-01-14T12:00:00Z
- kickoff_to_utc: 2025-01-14T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-15T03:00:00Z
- expected_match_ids: ha-20250114-01,ha-20250114-02,ha-20250114-03,ha-20250114-04,ha-20250114-05,ha-20250114-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: 1e60eddf1fbb638ec86d2d7077b6f70660457c0d5a83154a3bafe7f98b5997e9
- match_result_ids: ha-result-ha-20250114-01-v1,ha-result-ha-20250114-02-v1,ha-result-ha-20250114-03-v1,ha-result-ha-20250114-04-v1,ha-result-ha-20250114-05-v1,ha-result-ha-20250114-06-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 6
- match_coverage: 1
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1
### Slice 10
- slice_id: 2255dba8-64fc-5a21-843e-7a4328511b3b
- analysis_run_id: df345f61-e94a-5bdc-bf14-704797c30089
- decision_as_of_at_utc: 2025-01-15T09:00:00Z
- kickoff_from_utc: 2025-01-15T12:00:00Z
- kickoff_to_utc: 2025-01-15T23:00:00Z
- evaluation_as_of_at_utc: 2025-01-16T03:00:00Z
- expected_match_ids: ha-20250115-01,ha-20250115-02,ha-20250115-03,ha-20250115-04,ha-20250115-05,ha-20250115-06
- decision_match_count: 6
- decision_coverage: 1
- missing_decision_match_ids: NONE
- decision_input_manifest_hash: a258f1b63e6eebaabc5ae3b717497878323df766fcf0867ced11bdc1b30be9ae
- match_result_ids: ha-result-ha-20250115-01-v1,ha-result-ha-20250115-02-v1,ha-result-ha-20250115-03-v1,ha-result-ha-20250115-04-v1,ha-result-ha-20250115-05-v1
- match_result_issues: NONE
- match_count: 6
- settled_match_count: 5
- match_coverage: 0.833333333333
- ticket_count: 2
- settled_ticket_count: 2
- ticket_coverage: 1

## Archive Provenance
### FIXTURES
- archive_id: historical-acceptance-fixtures-v1
- archive_schema_version: HISTORICAL_ARCHIVE_V1
- provider_code: SYNTHETIC_ACCEPTANCE_V1
- payload_sha256: ffbaa93ec8bfa966778b30f9308caa46ccff32f5a688808501adef14220334ce
- source_reference: synthetic-acceptance://fixtures.json
- source_description: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE. Deterministic static acceptance fixture.
- license_note: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE
### MANUAL_QUANT
- archive_id: historical-acceptance-manual_quant-v1
- archive_schema_version: HISTORICAL_ARCHIVE_V1
- provider_code: SYNTHETIC_ACCEPTANCE_V1
- payload_sha256: 70a70c444f493412265038dbf3767e9c56a69575d4bca7375ea463de9983b7f4
- source_reference: synthetic-acceptance://manual_quant.json
- source_description: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE. Deterministic static acceptance fixture.
- license_note: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE
### MARKET_ODDS
- archive_id: historical-acceptance-market_odds-v1
- archive_schema_version: HISTORICAL_ARCHIVE_V1
- provider_code: SYNTHETIC_ACCEPTANCE_V1
- payload_sha256: 0ad75a56140427b864a5c0ad5fa56f9f7e94188605999350b13ddfaf36f70ab6
- source_reference: synthetic-acceptance://market_odds.json
- source_description: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE. Deterministic static acceptance fixture.
- license_note: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE
### MATCH_RESULTS
- archive_id: historical-acceptance-match_results-v1
- archive_schema_version: HISTORICAL_ARCHIVE_V1
- provider_code: SYNTHETIC_ACCEPTANCE_V1
- payload_sha256: bf02922605c7fe21ec13446549eb0b18d069bac5e3cdf0dca148e244d13a2b7e
- source_reference: synthetic-acceptance://match_results.json
- source_description: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE. Deterministic static acceptance fixture.
- license_note: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE
### PROVIDER_MAPPINGS
- archive_id: historical-acceptance-provider_mappings-v1
- archive_schema_version: HISTORICAL_ARCHIVE_V1
- provider_code: SYNTHETIC_ACCEPTANCE_V1
- payload_sha256: 8da43b07b0a44c654622d7ff6c4bfb27abc3916724d49595e897da9b384984a0
- source_reference: synthetic-acceptance://provider_mappings.json
- source_description: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE. Deterministic static acceptance fixture.
- license_note: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE
### SPORTTERY_BONUS
- archive_id: historical-acceptance-sporttery_bonus-v1
- archive_schema_version: HISTORICAL_ARCHIVE_V1
- provider_code: SYNTHETIC_ACCEPTANCE_V1
- payload_sha256: 204cac6b639c66949bd0add68f59f2de306aab5a13cc64100f133c88a3865dd2
- source_reference: synthetic-acceptance://sporttery_bonus.json
- source_description: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE. Deterministic static acceptance fixture.
- license_note: SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE
