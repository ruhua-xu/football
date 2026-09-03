import asyncio
import hashlib
import json
import tomllib
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from football_system.application.ports.data_providers import (
    FixtureQuery,
    MatchResultQuery,
    SnapshotQuery,
)
from football_system.application.run_analysis import (
    RunAnalysisRequest,
    RunAnalysisService,
)
from football_system.config import AppSettings
from football_system.domain.archive import (
    HISTORICAL_ARCHIVE_SCHEMA_VERSION,
    HistoricalArchiveDatasetKind,
    HistoricalDataMode,
    archive_payload_sha256,
)
from football_system.domain.betting import NoBetReason, PortfolioStatus
from football_system.domain.match import MatchStatus
from football_system.domain.prediction import FusionPolicyName
from football_system.infrastructure.providers.historical_archive import (
    ArchiveValidationError,
    HistoricalArchiveFixtureProvider,
    HistoricalArchiveMarketOddsProvider,
    HistoricalArchiveQuantProvider,
    HistoricalArchiveSportteryProvider,
    LocalArchiveHistoricalDataProvider,
    LocalArchiveStore,
    MissingArchiveInputError,
    load_historical_archive,
)

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_DIRECTORY = ROOT / "data" / "fixtures" / "historical_acceptance"
CONFIG_PATH = ACCEPTANCE_DIRECTORY / "acceptance_config.toml"
LABEL = "SYNTHETIC_ACCEPTANCE_DATA / NOT REAL HISTORICAL PERFORMANCE"


def _config() -> dict[str, object]:
    with CONFIG_PATH.open("rb") as stream:
        return tomllib.load(stream)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _store(config: dict[str, object]) -> LocalArchiveStore:
    return LocalArchiveStore(
        ACCEPTANCE_DIRECTORY,
        data_mode=HistoricalDataMode(str(config["data_mode"])),
    )


def _provider_code(config: dict[str, object]) -> str:
    return str(config["provider_code"])


def test_static_store_has_valid_checksums_labels_and_ten_daily_slates() -> None:
    config = _config()
    inventories = config["archives"]
    assert isinstance(inventories, list)
    expected_files = {str(item["filename"]) for item in inventories}
    assert expected_files == {
        "fixtures.json",
        "market_odds.json",
        "sporttery_bonus.json",
        "manual_quant.json",
        "match_results.json",
        "provider_mappings.json",
    }
    assert {path.name for path in ACCEPTANCE_DIRECTORY.glob("*.json")} == (
        expected_files
    )

    loaded_by_kind = {}
    for inventory in inventories:
        path = ACCEPTANCE_DIRECTORY / str(inventory["filename"])
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes.decode("utf-8"))
        manifest = raw["manifest"]
        assert manifest["record_count"] == len(raw["records"])
        assert manifest["record_count"] == inventory["record_count"]
        assert archive_payload_sha256(raw["records"]) == manifest["payload_sha256"]
        assert manifest["payload_sha256"] == inventory["payload_sha256"]
        assert hashlib.sha256(raw_bytes).hexdigest() == inventory["file_sha256"]

        loaded = load_historical_archive(path)
        assert loaded.manifest.archive_schema_version == (
            HISTORICAL_ARCHIVE_SCHEMA_VERSION
        )
        assert loaded.manifest.data_mode is HistoricalDataMode.LIVE_STRICT
        assert loaded.manifest.license_note == LABEL
        assert LABEL in loaded.manifest.source_description
        assert all(not record.retrospective for record in loaded.records)
        assert all(record.imported_at_utc is None for record in loaded.records)
        loaded_by_kind[loaded.manifest.dataset_kind] = loaded

    assert set(loaded_by_kind) == set(HistoricalArchiveDatasetKind) - {
        HistoricalArchiveDatasetKind.MARKET_ODDS_ISSUES
    }
    store = _store(config)
    assert len(store.manifests) == 6
    assert store.data_mode is HistoricalDataMode.LIVE_STRICT

    fixtures = loaded_by_kind[HistoricalArchiveDatasetKind.FIXTURES]
    fixture_match_ids = {record.payload.match.match_id for record in fixtures.records}
    slates = config["slates"]
    assert isinstance(slates, list)
    planned_match_ids = [
        match_id for slate in slates for match_id in slate["match_ids"]
    ]
    decisions = [_utc(str(slate["decision_as_of_at_utc"])) for slate in slates]
    assert config["classification"] == "SYNTHETIC_ACCEPTANCE_DATA"
    assert config["performance_warning"] == "NOT REAL HISTORICAL PERFORMANCE"
    assert config["manifest_label"] == LABEL
    assert config["market_bookmaker_code"] == "CONSENSUS"
    assert config["slate_count"] == len(slates) == 10
    assert all(len(slate["match_ids"]) == 6 for slate in slates)
    assert config["expected_match_count"] == len(planned_match_ids) == 60
    assert len(set(planned_match_ids)) == 60
    assert set(planned_match_ids) == fixture_match_ids
    assert decisions == sorted(decisions)
    assert all(
        (right - left).total_seconds() == 24 * 60 * 60
        for left, right in zip(decisions[:-1], decisions[1:], strict=True)
    )
    assert [item["name"] for item in config["strategies"]] == [
        "QUANT_ONLY_V1",
        "MARKET_QUANT_BLEND_V1",
    ]

    for path in ACCEPTANCE_DIRECTORY.glob("invalid_examples/**/*.json"):
        raw = json.loads(path.read_bytes().decode("utf-8"))
        assert raw["manifest"]["license_note"] == LABEL
        assert LABEL in raw["manifest"]["source_description"]
        assert raw["manifest"]["record_count"] == len(raw["records"])
        assert (
            archive_payload_sha256(raw["records"])
            == (raw["manifest"]["payload_sha256"])
        )


def test_invalid_mapping_examples_are_nested_and_rejected_independently() -> None:
    config = _config()
    invalid = config["invalid_examples"]
    conflict_directory = ACCEPTANCE_DIRECTORY / str(
        invalid["mapping_conflict_directory"]
    )
    missing_directory = ACCEPTANCE_DIRECTORY / str(invalid["mapping_missing_directory"])
    assert conflict_directory.parent.name == "invalid_examples"
    assert missing_directory.parent.name == "invalid_examples"

    with pytest.raises(ArchiveValidationError, match="provider external match key"):
        LocalArchiveStore(conflict_directory)
    with pytest.raises(ArchiveValidationError, match="no same-provider mapping"):
        LocalArchiveStore(missing_directory)

    # Nested invalid JSON must never enter the valid store's non-recursive glob.
    assert len(_store(config).manifests) == 6


def test_all_input_providers_obey_decision_cutoffs_and_version_boundaries() -> None:
    config = _config()
    store = _store(config)
    provider_code = _provider_code(config)
    fixture_provider = HistoricalArchiveFixtureProvider(store, provider_code)
    market_provider = HistoricalArchiveMarketOddsProvider(store, provider_code)
    sporttery_provider = HistoricalArchiveSportteryProvider(store, provider_code)
    quant_provider = HistoricalArchiveQuantProvider(store, provider_code)

    selected_fixture_ids: set[str] = set()
    selected_market_ids: set[str] = set()
    selected_sporttery_ids: set[str] = set()
    selected_quant_ids: set[str] = set()
    selected_market_by_match = {}
    decision_by_match = {}
    selected_odds: list[Decimal] = []

    for slate in config["slates"]:
        decision = _utc(str(slate["decision_as_of_at_utc"]))
        match_ids = tuple(slate["match_ids"])
        decision_by_match.update(dict.fromkeys(match_ids, decision))
        fixture_batch = asyncio.run(
            fixture_provider.fetch_fixtures(
                FixtureQuery(
                    kickoff_from_utc=_utc(str(slate["kickoff_from_utc"])),
                    kickoff_to_utc=_utc(str(slate["kickoff_to_utc"])),
                    as_of_at_utc=decision,
                )
            )
        )
        snapshot_query = SnapshotQuery(match_ids=match_ids, as_of_at_utc=decision)
        market_batch = asyncio.run(market_provider.fetch_market_odds(snapshot_query))
        sporttery_batch = asyncio.run(
            sporttery_provider.fetch_fixed_bonus(snapshot_query)
        )
        quant_batch = asyncio.run(quant_provider.fetch_manual_quant(snapshot_query))

        assert len(fixture_batch.matches) == 6
        assert len(market_batch.snapshots) == 6
        assert len(sporttery_batch.snapshots) == 6
        assert len(quant_batch.inputs) == 6
        assert {match.match_id for match in fixture_batch.matches} == set(match_ids)
        assert all(
            match.available_at_utc <= decision for match in fixture_batch.matches
        )
        assert all(
            mapping.available_at_utc <= decision
            for mapping in (
                fixture_batch.mappings
                + market_batch.mappings
                + sporttery_batch.mappings
            )
        )
        assert all(
            max(
                snapshot.captured_at_utc,
                snapshot.available_at_utc,
                snapshot.ingested_at_utc,
            )
            <= decision
            for snapshot in market_batch.snapshots + sporttery_batch.snapshots
        )
        assert all(item.available_at_utc <= decision for item in quant_batch.inputs)

        selected_fixture_ids.update(match.match_id for match in fixture_batch.matches)
        selected_market_ids.update(
            snapshot.match_id for snapshot in market_batch.snapshots
        )
        selected_sporttery_ids.update(
            snapshot.match_id for snapshot in sporttery_batch.snapshots
        )
        selected_quant_ids.update(item.match_id for item in quant_batch.inputs)
        selected_market_by_match.update(
            {snapshot.match_id: snapshot for snapshot in market_batch.snapshots}
        )
        selected_odds.extend(
            quote.odds
            for snapshot in market_batch.snapshots
            for quote in snapshot.quotes
        )

    assert len(selected_fixture_ids) == 60
    assert selected_fixture_ids == selected_market_ids
    assert selected_fixture_ids == selected_sporttery_ids
    assert selected_fixture_ids == selected_quant_ids
    assert min(selected_odds) == Decimal("1.28")
    assert max(selected_odds) == Decimal("9.50")

    cases = config["special_cases"]
    exact = cases["odds_exactly_at_cutoff"]
    exact_snapshot = selected_market_by_match[exact["match_id"]]
    assert exact_snapshot.snapshot_id == exact["snapshot_id"]
    assert exact_snapshot.ingested_at_utc == decision_by_match[exact["match_id"]]

    market_future = cases["market_future_version"]
    assert (
        selected_market_by_match[market_future["match_id"]].snapshot_id
        == market_future["decision_snapshot_id"]
    )
    future_market_batch = asyncio.run(
        market_provider.fetch_market_odds(
            SnapshotQuery(
                match_ids=(market_future["match_id"],),
                as_of_at_utc=_utc(str(market_future["visible_at_utc"])),
            )
        )
    )
    assert [item.snapshot_id for item in future_market_batch.snapshots] == [
        market_future["future_snapshot_id"]
    ]

    late_only = cases["odds_after_cutoff"]
    late_decision = decision_by_match[late_only["match_id"]]
    before_late = asyncio.run(
        market_provider.fetch_market_odds(
            SnapshotQuery(
                match_ids=(late_only["match_id"],),
                as_of_at_utc=late_decision,
            )
        )
    )
    after_late = asyncio.run(
        market_provider.fetch_market_odds(
            SnapshotQuery(
                match_ids=(late_only["match_id"],),
                as_of_at_utc=_utc(str(late_only["visible_at_utc"])),
            )
        )
    )
    assert late_only["snapshot_id"] not in {
        item.snapshot_id for item in before_late.snapshots
    }
    assert late_only["snapshot_id"] in {
        item.snapshot_id for item in after_late.snapshots
    }
    assert len(before_late.snapshots) == 1
    assert len(after_late.snapshots) == 2
    assert {snapshot.bookmaker_code for snapshot in after_late.snapshots} == {
        "CONSENSUS",
        "LATE_BOOK",
    }

    first_decision = _utc(str(config["slates"][0]["decision_as_of_at_utc"]))
    sporttery_future = cases["sporttery_future_version"]
    sporttery_before = asyncio.run(
        sporttery_provider.fetch_fixed_bonus(
            SnapshotQuery(
                match_ids=(sporttery_future["match_id"],),
                as_of_at_utc=first_decision,
            )
        )
    )
    sporttery_after = asyncio.run(
        sporttery_provider.fetch_fixed_bonus(
            SnapshotQuery(
                match_ids=(sporttery_future["match_id"],),
                as_of_at_utc=_utc(str(sporttery_future["visible_at_utc"])),
            )
        )
    )
    assert (
        sporttery_before.snapshots[0].snapshot_id
        == (sporttery_future["decision_snapshot_id"])
    )
    assert (
        sporttery_after.snapshots[0].snapshot_id
        == (sporttery_future["future_snapshot_id"])
    )

    quant_future = cases["quant_future_version"]
    quant_before = asyncio.run(
        quant_provider.fetch_manual_quant(
            SnapshotQuery(
                match_ids=(quant_future["match_id"],),
                as_of_at_utc=first_decision,
            )
        )
    )
    quant_after = asyncio.run(
        quant_provider.fetch_manual_quant(
            SnapshotQuery(
                match_ids=(quant_future["match_id"],),
                as_of_at_utc=_utc(str(quant_future["visible_at_utc"])),
            )
        )
    )
    assert quant_before.inputs[0].input_id == quant_future["decision_input_id"]
    assert quant_after.inputs[0].input_id == quant_future["future_input_id"]

    fixture_future = cases["fixture_future_version"]
    fixture_slate = config["slates"][0]
    fixture_before = asyncio.run(
        fixture_provider.fetch_fixtures(
            FixtureQuery(
                kickoff_from_utc=_utc(str(fixture_slate["kickoff_from_utc"])),
                kickoff_to_utc=_utc(str(fixture_slate["kickoff_to_utc"])),
                as_of_at_utc=first_decision,
            )
        )
    )
    fixture_after = asyncio.run(
        fixture_provider.fetch_fixtures(
            FixtureQuery(
                kickoff_from_utc=_utc(str(fixture_slate["kickoff_from_utc"])),
                kickoff_to_utc=_utc(str(fixture_slate["kickoff_to_utc"])),
                as_of_at_utc=_utc(str(fixture_future["visible_at_utc"])),
            )
        )
    )
    before_match = next(
        item
        for item in fixture_before.matches
        if item.match_id == fixture_future["match_id"]
    )
    after_match = next(
        item
        for item in fixture_after.matches
        if item.match_id == fixture_future["match_id"]
    )
    assert before_match.status is MatchStatus.SCHEDULED
    assert after_match.status is MatchStatus.FINISHED


def test_results_cover_outcomes_correction_and_one_explicit_missing_match() -> None:
    config = _config()
    store = _store(config)
    provider = LocalArchiveHistoricalDataProvider(store, _provider_code(config))
    cases = config["special_cases"]
    correction = cases["result_correction"]
    missing_match_id = cases["missing_result"]["match_id"]
    all_match_ids: list[str] = []
    initially_visible_results = {}

    for slate in config["slates"]:
        match_ids = tuple(slate["match_ids"])
        evaluation = _utc(str(slate["evaluation_as_of_at_utc"]))
        batch = asyncio.run(
            provider.fetch_match_results(
                MatchResultQuery(match_ids=match_ids, as_of_at_utc=evaluation)
            )
        )
        by_match = {result.match_id: result for result in batch.results}
        assert len(batch.results) == slate["expected_settled_match_count"]
        assert all(
            max(
                result.observed_at_utc,
                result.available_at_utc,
                result.ingested_at_utc,
            )
            <= evaluation
            for result in batch.results
        )
        for match_id, expected_outcome in zip(
            match_ids, slate["evaluation_outcomes"], strict=True
        ):
            if expected_outcome == "MISSING_RESULT":
                assert match_id not in by_match
            else:
                assert (
                    by_match[match_id].three_way_selection().value == expected_outcome
                )
        initially_visible_results.update(by_match)
        all_match_ids.extend(match_ids)

    correction_slate = next(
        slate
        for slate in config["slates"]
        if correction["match_id"] in slate["match_ids"]
    )
    assert _utc(str(correction["visible_at_utc"])) > _utc(
        str(correction_slate["evaluation_as_of_at_utc"])
    )
    initial = initially_visible_results[correction["match_id"]]
    corrected_batch = asyncio.run(
        provider.fetch_match_results(
            MatchResultQuery(
                match_ids=(correction["match_id"],),
                as_of_at_utc=_utc(str(correction["visible_at_utc"])),
            )
        )
    )
    corrected = corrected_batch.results[0]
    assert initial.match_result_id == correction["initial_result_id"]
    assert initial.three_way_selection().value == correction["initial_outcome"]
    assert corrected.match_result_id == correction["corrected_result_id"]
    assert corrected.three_way_selection().value == correction["corrected_outcome"]
    assert corrected.supersedes_match_result_id == initial.match_result_id

    final_cutoff = max(manifest.created_at_utc for manifest in store.manifests)
    final_batch = asyncio.run(
        provider.fetch_match_results(
            MatchResultQuery(
                match_ids=tuple(all_match_ids),
                as_of_at_utc=final_cutoff,
            )
        )
    )
    final_by_match = {result.match_id: result for result in final_batch.results}
    outcome_counts = Counter(
        result.three_way_selection().value for result in final_batch.results
    )
    expected_counts = config["expected_final_outcome_counts"]
    assert len(final_batch.results) == 59
    assert missing_match_id not in final_by_match
    assert set(all_match_ids) - set(final_by_match) == {missing_match_id}
    assert outcome_counts == {
        "HOME_WIN": expected_counts["HOME_WIN"],
        "DRAW": expected_counts["DRAW"],
        "AWAY_WIN": expected_counts["AWAY_WIN"],
    }
    assert expected_counts["MISSING_RESULT"] == 1


class _DiscardAnalysisRepository:
    def save_analysis(self, artifacts, rules) -> None:
        del artifacts, rules


def _settings(config: dict[str, object], strategy: dict[str, object]) -> AppSettings:
    return AppSettings.model_validate(
        {
            "analysis": {
                "pipeline_version": "HISTORICAL_ACCEPTANCE_V1",
                "fusion_policy": strategy["name"],
                "quant_weight": strategy["quant_weight"],
                "min_selection_ev": config["min_selection_ev"],
                "min_ticket_roi": config["min_ticket_roi"],
            },
            "portfolio": config["portfolio"],
            "sporttery": config["sporttery"],
        }
    )


def test_configured_bookmaker_stream_is_explicit_and_run_analysis_safe() -> None:
    config = _config()
    store = _store(config)
    provider_code = _provider_code(config)
    bookmaker_code = str(config["market_bookmaker_code"])
    late_case = config["special_cases"]["odds_after_cutoff"]
    late_match_id = str(late_case["match_id"])
    late_visible = _utc(str(late_case["visible_at_utc"]))
    late_slate = next(
        slate for slate in config["slates"] if late_match_id in slate["match_ids"]
    )
    match_ids = tuple(late_slate["match_ids"])
    query = SnapshotQuery(match_ids=match_ids, as_of_at_utc=late_visible)

    unfiltered = asyncio.run(
        HistoricalArchiveMarketOddsProvider(store, provider_code).fetch_market_odds(
            query
        )
    )
    selected_provider = HistoricalArchiveMarketOddsProvider(
        store,
        provider_code,
        bookmaker_code=bookmaker_code,
    )
    selected = asyncio.run(selected_provider.fetch_market_odds(query))

    assert len(unfiltered.snapshots) == 7
    assert len(selected.snapshots) == len(match_ids) == 6
    assert all(
        snapshot.bookmaker_code == bookmaker_code for snapshot in selected.snapshots
    )
    assert tuple(snapshot.match_id for snapshot in selected.snapshots) == tuple(
        sorted(match_ids)
    )

    strategy = config["strategies"][0]
    service = RunAnalysisService(
        HistoricalArchiveFixtureProvider(store, provider_code),
        selected_provider,
        HistoricalArchiveSportteryProvider(store, provider_code),
        HistoricalArchiveQuantProvider(store, provider_code),
        _DiscardAnalysisRepository(),
        _settings(config, strategy),
    )
    artifacts = asyncio.run(
        service.run(
            RunAnalysisRequest(
                as_of_at_utc=late_visible,
                kickoff_from_utc=_utc(str(late_slate["kickoff_from_utc"])),
                kickoff_to_utc=_utc(str(late_slate["kickoff_to_utc"])),
                budgets_fen=(int(config["budget_fen"]),),
                fusion_policy=FusionPolicyName(str(strategy["name"])),
                min_selection_ev=Decimal(str(config["min_selection_ev"])),
                min_ticket_roi=Decimal(str(config["min_ticket_roi"])),
                analysis_run_id="ha-analysis-bookmaker-selector",
                execution_time_utc=late_visible,
            )
        )
    )
    assert len(artifacts.market_odds_snapshots) == len(match_ids)
    assert len(artifacts.market_predictions) == len(match_ids)
    assert all(
        snapshot.bookmaker_code == bookmaker_code
        for snapshot in artifacts.market_odds_snapshots
    )

    before_late = _utc(str(late_slate["decision_as_of_at_utc"]))
    with pytest.raises(MissingArchiveInputError, match="LATE_BOOK.*no legal"):
        asyncio.run(
            HistoricalArchiveMarketOddsProvider(
                store,
                provider_code,
                bookmaker_code="LATE_BOOK",
            ).fetch_market_odds(
                SnapshotQuery(
                    match_ids=(late_match_id,),
                    as_of_at_utc=before_late,
                )
            )
        )
    with pytest.raises(MissingArchiveInputError, match="UNKNOWN_BOOK.*no legal"):
        asyncio.run(
            HistoricalArchiveMarketOddsProvider(
                store,
                provider_code,
                bookmaker_code="UNKNOWN_BOOK",
            ).fetch_market_odds(
                SnapshotQuery(
                    match_ids=(late_match_id,),
                    as_of_at_utc=late_visible,
                )
            )
        )


def test_static_threshold_produces_no_bet_and_cash_for_both_strategies() -> None:
    config = _config()
    store = _store(config)
    provider_code = _provider_code(config)
    min_selection_ev = Decimal(str(config["min_selection_ev"]))
    statuses_by_strategy: dict[str, dict[str, str]] = {}
    all_evs: list[Decimal] = []

    for strategy in config["strategies"]:
        strategy_name = str(strategy["name"])
        service = RunAnalysisService(
            HistoricalArchiveFixtureProvider(store, provider_code),
            HistoricalArchiveMarketOddsProvider(store, provider_code),
            HistoricalArchiveSportteryProvider(store, provider_code),
            HistoricalArchiveQuantProvider(store, provider_code),
            _DiscardAnalysisRepository(),
            _settings(config, strategy),
        )
        expected_prefix = (
            "quant_only"
            if strategy_name == FusionPolicyName.QUANT_ONLY_V1.value
            else "market_quant_blend"
        )
        strategy_statuses = {}

        for slate in config["slates"]:
            decision = _utc(str(slate["decision_as_of_at_utc"]))
            artifacts = asyncio.run(
                service.run(
                    RunAnalysisRequest(
                        as_of_at_utc=decision,
                        kickoff_from_utc=_utc(str(slate["kickoff_from_utc"])),
                        kickoff_to_utc=_utc(str(slate["kickoff_to_utc"])),
                        budgets_fen=(int(config["budget_fen"]),),
                        fusion_policy=FusionPolicyName(strategy_name),
                        min_selection_ev=min_selection_ev,
                        min_ticket_roi=Decimal(str(config["min_ticket_roi"])),
                        analysis_run_id=(
                            f"ha-analysis-{strategy_name.lower()}-{slate['slate_id']}"
                        ),
                        execution_time_utc=decision,
                    )
                )
            )
            portfolio = artifacts.portfolios[0]
            expected_status = slate[f"{expected_prefix}_status"]
            expected_ticket_count = slate[f"{expected_prefix}_ticket_count"]
            expected_cash_fen = slate[f"{expected_prefix}_cash_fen"]
            strategy_statuses[str(slate["slate_id"])] = portfolio.status.value

            assert len(artifacts.matches) == 6
            assert len(artifacts.market_predictions) == 6
            assert len(artifacts.quant_predictions) == 6
            assert len(artifacts.final_predictions) == 6
            assert {
                prediction.fusion_policy.value
                for prediction in artifacts.final_predictions
            } == {strategy_name}
            assert portfolio.status.value == expected_status
            assert len(portfolio.tickets) == expected_ticket_count
            assert portfolio.cash_position.amount_fen == expected_cash_fen
            assert (
                portfolio.total_stake_fen + portfolio.cash_position.amount_fen
                == (config["budget_fen"])
            )
            all_evs.extend(candidate.ev for candidate in artifacts.selection_candidates)

            if portfolio.status is PortfolioStatus.NO_BET:
                assert portfolio.no_bet_reason is NoBetReason.NO_BET_NO_VALUE
                assert portfolio.total_stake_fen == 0
                assert (
                    max(candidate.ev for candidate in artifacts.selection_candidates)
                    < min_selection_ev
                )
            else:
                assert portfolio.total_stake_fen == 1_200
                assert 0 < portfolio.cash_position.amount_fen < portfolio.budget_fen

        statuses_by_strategy[strategy_name] = strategy_statuses
        actual_no_bet_slates = {
            slate_id
            for slate_id, status in strategy_statuses.items()
            if status == PortfolioStatus.NO_BET.value
        }
        assert actual_no_bet_slates == set(strategy["expected_no_bet_slate_ids"])

    assert set(statuses_by_strategy) == {
        FusionPolicyName.QUANT_ONLY_V1.value,
        FusionPolicyName.MARKET_QUANT_BLEND_V1.value,
    }
    assert min(all_evs) < 0
    assert max(all_evs) > min_selection_ev
