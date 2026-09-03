from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from football_system.application.backtest import (
    WalkForwardBacktestResult,
    validate_walk_forward_backtest_result,
)
from football_system.application.backtest_v2 import (
    WalkForwardBacktestV2Result,
    WalkForwardBacktestV2SlateResult,
    validate_walk_forward_backtest_v2_result,
)
from football_system.application.ports.data_providers import MatchResultBatch
from football_system.domain.archive import (
    HistoricalArchiveManifest,
    match_result_payload_sha256,
)
from football_system.domain.backtest import (
    BacktestArchiveProvenance,
    BacktestMetrics,
    BacktestMetricsConfig,
    BacktestRun,
    BacktestRunStatus,
    BacktestSlice,
    BacktestSlateSnapshot,
    BacktestStrategySnapshot,
)
from football_system.domain.backtest_v2 import (
    BACKTEST_METRICS_V2,
    BACKTEST_V2,
    BACKTEST_V2_SLICE_V1,
    BacktestV2DecisionSnapshot,
    BacktestV2Metrics,
    BacktestV2Slice,
)
from football_system.domain.betting import (
    CandidateStatus,
    CashPosition,
    NoBetReason,
    PassType,
    Portfolio,
    PortfolioConstraints,
    PortfolioStatus,
    SelectionCandidate,
    SportteryRules,
    TicketAllocation,
    TicketCandidate,
)
from football_system.domain.common import stable_id
from football_system.domain.market import (
    MarketKey,
    MarketType,
    SelectionKey,
    ThreeWayProbability,
)
from football_system.domain.match import ProviderMatchMapping, SaleStatus
from football_system.domain.post_review import PortfolioRevision
from football_system.domain.prediction import FusionPolicyName
from football_system.domain.services.payout import (
    calculate_stake_fen,
    official_gross_payout_fen,
)
from football_system.domain.services.probability import (
    quantize_metric,
    quantize_probability,
    selection_ev,
)
from football_system.domain.services.backtest_v2_metrics import (
    calculate_backtest_v2_metrics,
)
from football_system.domain.settlement import (
    MatchResult,
    PortfolioSettlement,
    PortfolioSettlementResult,
    Settlement,
    SettlementStatus,
)
from football_system.infrastructure.database.models import (
    AnalysisRunMatchRecord,
    AnalysisRunRecord,
    BacktestMetricSettlementRecord,
    BacktestMetricSnapshotRecord,
    BacktestMetricTicketSettlementRecord,
    BacktestRunRecord,
    BacktestSliceRecord,
    BacktestV2EvaluationRefRecord,
    BacktestV2MetricSnapshotRecord,
    BacktestV2ResultSourceRecord,
    BacktestV2RunArchiveRecord,
    BacktestV2RunRecord,
    BacktestV2SliceRecord,
    BacktestV2SliceTicketSettlementRecord,
    BacktestV2TrainingSourceRecord,
    BetCandidateRecord,
    FinalPredictionOutcomeRecord,
    FinalPredictionRecord,
    HistoricalArchiveImportRecord,
    MatchRecord,
    MatchResultRecord,
    MarketProbabilityOutcomeRecord,
    MarketProbabilityRecord,
    PortfolioCashPositionRecord,
    PortfolioRecord,
    PortfolioRevisionRecord,
    PortfolioSettlementRecord,
    PortfolioSettlementTicketRecord,
    ProviderMatchMappingRecord,
    ProviderRecord,
    QuantModelEvaluationRecord,
    QuantModelStateRecord,
    QuantModelTrainingFactRecord,
    QuantPredictionOutcomeRecord,
    QuantPredictionRecord,
    SportteryBonusQuoteRecord,
    SportteryBonusSnapshotRecord,
    TicketCandidateLegRecord,
    TicketCandidateRecord,
    TicketLegRecord,
    TicketRecord,
    TicketSettlementMatchResultRecord,
    TicketSettlementRecord,
)


class SqlAlchemyHistoricalRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def append_historical_archive_import(
        self,
        manifest: HistoricalArchiveManifest,
        imported_at_utc: datetime,
    ) -> HistoricalArchiveImportRecord:
        return self.append_historical_archive_imports((manifest,), imported_at_utc)[0]

    def append_historical_archive_imports(
        self,
        manifests: Sequence[HistoricalArchiveManifest],
        imported_at_utc: datetime,
    ) -> tuple[HistoricalArchiveImportRecord, ...]:
        records = _historical_archive_import_records(manifests, imported_at_utc)
        with self._session_factory.begin() as session:
            stored = _append_historical_archive_imports(session, records)
            values = tuple(_detached_record(record) for record in stored)
        return values

    def append_archive_import(
        self,
        manifest: HistoricalArchiveManifest,
        imported_at_utc: datetime,
    ) -> HistoricalArchiveImportRecord:
        return self.append_historical_archive_import(manifest, imported_at_utc)

    def find_historical_archive_import(
        self,
        archive_id: str,
    ) -> HistoricalArchiveImportRecord | None:
        with self._session_factory() as session:
            record = session.get(HistoricalArchiveImportRecord, archive_id)
            if record is not None:
                _verify_historical_archive_import(record)
            return record

    def find_archive_import(
        self,
        archive_id: str,
    ) -> HistoricalArchiveImportRecord | None:
        return self.find_historical_archive_import(archive_id)

    def find_historical_archive_manifest(
        self,
        archive_id: str,
    ) -> HistoricalArchiveManifest | None:
        record = self.find_historical_archive_import(archive_id)
        return historical_archive_manifest(record) if record is not None else None

    def list_historical_archive_imports(
        self,
        *,
        provider_code: str | None = None,
        dataset_kind: str | None = None,
        data_mode: str | None = None,
    ) -> tuple[HistoricalArchiveImportRecord, ...]:
        statement = select(HistoricalArchiveImportRecord)
        if provider_code is not None:
            statement = statement.where(
                HistoricalArchiveImportRecord.provider_code == provider_code
            )
        if dataset_kind is not None:
            statement = statement.where(
                HistoricalArchiveImportRecord.dataset_kind == dataset_kind
            )
        if data_mode is not None:
            statement = statement.where(
                HistoricalArchiveImportRecord.data_mode == data_mode
            )
        statement = statement.order_by(
            HistoricalArchiveImportRecord.imported_at_utc,
            HistoricalArchiveImportRecord.archive_id,
        )
        with self._session_factory() as session:
            records = tuple(session.scalars(statement))
            for record in records:
                _verify_historical_archive_import(record)
            return records

    def list_archive_imports(
        self,
        *,
        provider_code: str | None = None,
        dataset_kind: str | None = None,
        data_mode: str | None = None,
    ) -> tuple[HistoricalArchiveImportRecord, ...]:
        return self.list_historical_archive_imports(
            provider_code=provider_code,
            dataset_kind=dataset_kind,
            data_mode=data_mode,
        )

    def historical_archive_manifests(
        self,
        *,
        provider_code: str | None = None,
        dataset_kind: str | None = None,
        data_mode: str | None = None,
    ) -> tuple[HistoricalArchiveManifest, ...]:
        return tuple(
            historical_archive_manifest(record)
            for record in self.list_historical_archive_imports(
                provider_code=provider_code,
                dataset_kind=dataset_kind,
                data_mode=data_mode,
            )
        )

    def append_match_result(self, result: MatchResult) -> MatchResult:
        return self.append_match_results((result,))[0]

    def append_match_results(
        self,
        results: Iterable[MatchResult],
    ) -> tuple[MatchResult, ...]:
        values = tuple(results)
        if len({item.match_result_id for item in values}) != len(values):
            raise ValueError("match result IDs must be unique")
        with self._session_factory.begin() as session:
            return _append_match_results(session, values)

    def append_match_result_batch(
        self,
        batch: MatchResultBatch,
    ) -> MatchResultBatch:
        with self._session_factory.begin() as session:
            _preflight_match_result_batches(session, (batch,))
            stored = _materialize_match_result_batches(session, (batch,))
        return batch.model_copy(
            update={
                "results": tuple(
                    stored[result.match_result_id] for result in batch.results
                )
            }
        )

    def find_match_result(self, match_result_id: str) -> MatchResult | None:
        with self._session_factory() as session:
            record = session.get(MatchResultRecord, match_result_id)
            return _match_result(session, record) if record is not None else None

    def latest_match_results(
        self,
        match_ids: Sequence[str],
        as_of_at_utc: datetime,
        provider_code: str | None = None,
    ) -> tuple[MatchResult, ...]:
        cutoff = _aware_utc(as_of_at_utc, "match result cutoff")
        requested = tuple(match_ids)
        if not requested or len(set(requested)) != len(requested):
            raise ValueError("match IDs must be nonempty and unique")
        successor = aliased(MatchResultRecord)
        statement = (
            select(MatchResultRecord)
            .join(
                ProviderRecord,
                ProviderRecord.provider_id == MatchResultRecord.provider_id,
            )
            .join(
                ProviderMatchMappingRecord,
                ProviderMatchMappingRecord.mapping_id
                == MatchResultRecord.provider_mapping_id,
            )
            .where(
                MatchResultRecord.internal_match_id.in_(requested),
                MatchResultRecord.available_at_utc <= cutoff,
                MatchResultRecord.ingested_at_utc <= cutoff,
                ProviderMatchMappingRecord.provider_id == MatchResultRecord.provider_id,
                ProviderMatchMappingRecord.internal_match_id
                == MatchResultRecord.internal_match_id,
                ProviderMatchMappingRecord.available_at_utc
                <= MatchResultRecord.available_at_utc,
                ProviderMatchMappingRecord.available_at_utc <= cutoff,
                ~select(successor.match_result_id)
                .where(
                    successor.supersedes_match_result_id
                    == MatchResultRecord.match_result_id,
                    successor.available_at_utc <= cutoff,
                    successor.ingested_at_utc <= cutoff,
                )
                .exists(),
            )
        )
        if provider_code is not None:
            statement = statement.where(ProviderRecord.code == provider_code)
        with self._session_factory() as session:
            results = tuple(
                _match_result(session, record) for record in session.scalars(statement)
            )
        latest_by_match: dict[str, MatchResult] = {}
        for result in results:
            previous = latest_by_match.get(result.match_id)
            if previous is None or _match_result_order(result) > _match_result_order(
                previous
            ):
                latest_by_match[result.match_id] = result
        return tuple(
            latest_by_match[match_id]
            for match_id in requested
            if match_id in latest_by_match
        )

    def load_base_portfolio(self, portfolio_id: str) -> Portfolio:
        with self._session_factory() as session:
            record = session.get(PortfolioRecord, portfolio_id)
            if record is None:
                raise KeyError(f"unknown base portfolio: {portfolio_id}")
            return _base_portfolio(session, record)

    def append_ticket_settlement(self, settlement: Settlement) -> Settlement:
        with self._session_factory.begin() as session:
            return _append_ticket_settlement(session, settlement)

    def find_ticket_settlement(self, settlement_id: str) -> Settlement | None:
        with self._session_factory() as session:
            record = session.get(TicketSettlementRecord, settlement_id)
            return _ticket_settlement(session, record) if record is not None else None

    def latest_ticket_settlements(
        self,
        decision_scope_id: str,
        as_of_at_utc: datetime,
        ticket_ids: Sequence[str] | None = None,
    ) -> tuple[Settlement, ...]:
        cutoff = _aware_utc(as_of_at_utc, "settlement cutoff")
        successor = aliased(TicketSettlementRecord)
        statement = select(TicketSettlementRecord).where(
            TicketSettlementRecord.decision_scope_id == decision_scope_id,
            TicketSettlementRecord.settled_at_utc <= cutoff,
            ~select(successor.settlement_id)
            .where(
                successor.supersedes_settlement_id
                == TicketSettlementRecord.settlement_id,
                successor.settled_at_utc <= cutoff,
            )
            .exists(),
        )
        if ticket_ids is not None:
            requested = tuple(ticket_ids)
            if len(set(requested)) != len(requested):
                raise ValueError("ticket IDs must be unique")
            if not requested:
                return ()
            statement = statement.where(TicketSettlementRecord.ticket_id.in_(requested))
        statement = statement.order_by(
            TicketSettlementRecord.portfolio_id,
            TicketSettlementRecord.ticket_id,
            TicketSettlementRecord.settled_at_utc,
        )
        with self._session_factory() as session:
            return tuple(
                _ticket_settlement(session, record)
                for record in session.scalars(statement)
            )

    def append_portfolio_settlement(
        self,
        record: PortfolioSettlementRecord | PortfolioSettlement,
        ticket_settlement_ids: Sequence[str] | None = None,
    ) -> PortfolioSettlementRecord | PortfolioSettlement:
        if isinstance(record, PortfolioSettlement):
            if (
                ticket_settlement_ids is not None
                and tuple(ticket_settlement_ids) != record.ticket_settlement_ids
            ):
                raise ValueError(
                    "PortfolioSettlement ticket references conflict with the record"
                )
            source_ids = _unique_ids(
                record.ticket_settlement_ids,
                "ticket settlement",
            )
            value = record
            record = _portfolio_settlement_record(value)
            with self._session_factory.begin() as session:
                stored = _append_portfolio_settlement_record(
                    session,
                    record,
                    source_ids,
                )
                value = _portfolio_settlement_value(stored, source_ids)
            return value
        if ticket_settlement_ids is None:
            raise ValueError("portfolio settlement requires ticket settlement IDs")
        source_ids = _unique_ids(ticket_settlement_ids, "ticket settlement")
        with self._session_factory.begin() as session:
            stored = _append_portfolio_settlement_record(session, record, source_ids)
            value = _detached_record(stored)
        return value

    def load_portfolio_settlement(
        self,
        portfolio_settlement_id: str,
    ) -> PortfolioSettlement:
        with self._session_factory() as session:
            record = session.get(PortfolioSettlementRecord, portfolio_settlement_id)
            if record is None:
                raise KeyError(
                    f"unknown portfolio settlement: {portfolio_settlement_id}"
                )
            _verify_portfolio_settlement(session, record)
            settlement_ids = tuple(
                session.scalars(
                    select(PortfolioSettlementTicketRecord.settlement_id)
                    .where(
                        PortfolioSettlementTicketRecord.portfolio_settlement_id
                        == portfolio_settlement_id
                    )
                    .order_by(PortfolioSettlementTicketRecord.settlement_no)
                )
            )
            return _portfolio_settlement_value(record, settlement_ids)

    def find_portfolio_settlement(
        self,
        portfolio_settlement_id: str,
    ) -> PortfolioSettlementRecord | None:
        with self._session_factory() as session:
            record = session.get(PortfolioSettlementRecord, portfolio_settlement_id)
            if record is not None:
                _verify_portfolio_settlement(session, record)
            return record

    def latest_portfolio_settlements(
        self,
        decision_scope_id: str,
        as_of_at_utc: datetime,
        portfolio_ids: Sequence[str] | None = None,
    ) -> tuple[PortfolioSettlementRecord, ...]:
        cutoff = _aware_utc(as_of_at_utc, "portfolio settlement cutoff")
        successor = aliased(PortfolioSettlementRecord)
        statement = select(PortfolioSettlementRecord).where(
            PortfolioSettlementRecord.decision_scope_id == decision_scope_id,
            PortfolioSettlementRecord.settled_at_utc <= cutoff,
            ~select(successor.portfolio_settlement_id)
            .where(
                successor.supersedes_portfolio_settlement_id
                == PortfolioSettlementRecord.portfolio_settlement_id,
                successor.settled_at_utc <= cutoff,
            )
            .exists(),
        )
        if portfolio_ids is not None:
            requested = tuple(portfolio_ids)
            if len(set(requested)) != len(requested):
                raise ValueError("portfolio IDs must be unique")
            if not requested:
                return ()
            statement = statement.where(
                PortfolioSettlementRecord.portfolio_id.in_(requested)
            )
        statement = statement.order_by(PortfolioSettlementRecord.portfolio_id)
        with self._session_factory() as session:
            records = tuple(session.scalars(statement))
            for record in records:
                _verify_portfolio_settlement(session, record)
            return records

    def append_backtest_run(
        self,
        record: BacktestRunRecord | BacktestRun,
        *,
        replay_of_backtest_run_id: str | None = None,
    ) -> BacktestRunRecord | BacktestRun:
        if isinstance(record, BacktestRun):
            value = record
            record = backtest_run_record(
                value,
                replay_of_backtest_run_id=replay_of_backtest_run_id,
            )
            with self._session_factory.begin() as session:
                stored = _append_backtest_run_record(session, record)
                result = backtest_run_value(stored)
            return result
        if (
            replay_of_backtest_run_id is not None
            and record.replay_of_backtest_run_id != replay_of_backtest_run_id
        ):
            raise ValueError("BacktestRun replay reference conflicts with the record")
        with self._session_factory.begin() as session:
            stored = _append_backtest_run_record(session, record)
            result = _detached_record(stored)
        return result

    def find_backtest_run(self, backtest_run_id: str) -> BacktestRunRecord | None:
        with self._session_factory() as session:
            record = session.get(BacktestRunRecord, backtest_run_id)
            if record is not None:
                _verify_backtest_run(session, record)
            return record

    def find_backtest_run_value(self, backtest_run_id: str) -> BacktestRun | None:
        record = self.find_backtest_run(backtest_run_id)
        return backtest_run_value(record) if record is not None else None

    def latest_backtest_runs(
        self,
        as_of_at_utc: datetime,
    ) -> tuple[BacktestRunRecord, ...]:
        cutoff = _aware_utc(as_of_at_utc, "backtest run cutoff")
        with self._session_factory() as session:
            records = tuple(
                session.scalars(
                    select(BacktestRunRecord)
                    .where(BacktestRunRecord.created_at_utc <= cutoff)
                    .order_by(
                        BacktestRunRecord.created_at_utc,
                        BacktestRunRecord.backtest_run_id,
                    )
                )
            )
            for record in records:
                _verify_backtest_run(session, record)
            return records

    def append_backtest_slice(
        self,
        record: BacktestSliceRecord | BacktestSlice,
        *,
        slice_no: int = 1,
        created_at_utc: datetime | None = None,
    ) -> BacktestSliceRecord | BacktestSlice:
        if isinstance(record, BacktestSlice):
            value = record
            record = backtest_slice_record(
                value,
                slice_no=slice_no,
                created_at_utc=created_at_utc,
            )
            with self._session_factory.begin() as session:
                stored = _append_backtest_slice_record(session, record)
                result = backtest_slice_value(stored)
            return result
        with self._session_factory.begin() as session:
            stored = _append_backtest_slice_record(session, record)
            result = _detached_record(stored)
        return result

    def find_backtest_slice_value(
        self,
        backtest_slice_id: str,
    ) -> BacktestSlice | None:
        record = self.find_backtest_slice(backtest_slice_id)
        return backtest_slice_value(record) if record is not None else None

    def find_backtest_slice(
        self,
        backtest_slice_id: str,
    ) -> BacktestSliceRecord | None:
        with self._session_factory() as session:
            record = session.get(BacktestSliceRecord, backtest_slice_id)
            if record is not None:
                _verify_backtest_slice(session, record)
            return record

    def backtest_slices(
        self,
        backtest_run_id: str,
        evaluation_as_of_at_utc: datetime | None = None,
    ) -> tuple[BacktestSliceRecord, ...]:
        statement = select(BacktestSliceRecord).where(
            BacktestSliceRecord.backtest_run_id == backtest_run_id
        )
        if evaluation_as_of_at_utc is not None:
            cutoff = _aware_utc(
                evaluation_as_of_at_utc,
                "backtest slice cutoff",
            )
            statement = statement.where(
                BacktestSliceRecord.evaluation_as_of_at_utc <= cutoff
            )
        statement = statement.order_by(
            BacktestSliceRecord.slice_no,
            BacktestSliceRecord.backtest_slice_id,
        )
        with self._session_factory() as session:
            records = tuple(session.scalars(statement))
            for record in records:
                _verify_backtest_slice(session, record)
            return records

    def backtest_slice_values(
        self,
        backtest_run_id: str,
        evaluation_as_of_at_utc: datetime | None = None,
    ) -> tuple[BacktestSlice, ...]:
        return tuple(
            backtest_slice_value(record)
            for record in self.backtest_slices(
                backtest_run_id,
                evaluation_as_of_at_utc,
            )
        )

    def append_backtest_metric_snapshot(
        self,
        record: BacktestMetricSnapshotRecord,
        portfolio_settlement_ids: Sequence[str] | None = None,
    ) -> BacktestMetricSnapshotRecord:
        source_ids = (
            _metric_settlement_ids(record)
            if portfolio_settlement_ids is None
            else _unique_ids(portfolio_settlement_ids, "portfolio settlement")
        )
        ticket_settlement_ids = _metric_ticket_settlement_ids(record)
        with self._session_factory.begin() as session:
            stored = _append_backtest_metric_snapshot_record(
                session,
                record,
                source_ids,
                ticket_settlement_ids,
            )
            result = _detached_record(stored)
        return result

    def save_walk_forward_backtest_result(
        self,
        result: WalkForwardBacktestResult,
        *,
        calculated_at_utc: datetime | None = None,
    ) -> BacktestRun:
        result = validate_walk_forward_backtest_result(result)
        final_cutoff = result.request.slates[-1].evaluation_as_of_at_utc
        calculated_at = _aware_utc(
            result.backtest_run.created_at_utc
            if calculated_at_utc is None
            else calculated_at_utc,
            "walk-forward calculation timestamp",
        )
        if calculated_at < final_cutoff:
            raise ValueError(
                "walk-forward calculation timestamp cannot precede the final "
                "evaluation cutoff"
            )

        run_record = backtest_run_record(result.backtest_run)
        slice_records = tuple(
            backtest_slice_record(
                slate.backtest_slice,
                slice_no=slice_no,
                created_at_utc=calculated_at,
            )
            for slice_no, slate in enumerate(result.slate_results, start=1)
        )
        portfolio_settlement_ids = tuple(
            settlement.portfolio_settlement_id
            for settlement in result.portfolio_settlements
        )
        ticket_settlement_ids = tuple(
            settlement.settlement_id for settlement in result.ticket_settlements
        )
        slice_lineage = tuple(
            (
                slate.backtest_slice.slice_id,
                slate.backtest_slice.match_result_ids,
            )
            for slate in result.slate_results
        )
        metric_snapshot_id = stable_id(
            "backtest-metric-snapshot",
            result.backtest_run.backtest_run_id,
            result.metrics.metrics_version,
            "RUN",
            "AGGREGATE",
        )
        metric_record = backtest_metric_snapshot_record(
            result.metrics,
            metric_snapshot_id,
            final_cutoff,
            calculated_at_utc=calculated_at,
            metric_key="AGGREGATE",
            portfolio_settlement_ids=portfolio_settlement_ids,
            ticket_settlement_ids=ticket_settlement_ids,
            slice_lineage=slice_lineage,
        )

        with self._session_factory.begin() as session:
            _preflight_walk_forward_graph(
                session,
                result,
                run_record,
                slice_records,
                metric_record,
                portfolio_settlement_ids,
                ticket_settlement_ids,
            )
            _materialize_match_result_batches(session, result.match_result_batches)
            for settlement in _ordered_ticket_settlements(result.ticket_settlements):
                _append_ticket_settlement(session, settlement)
            for settlement in _ordered_portfolio_settlements(
                result.portfolio_settlements
            ):
                _append_portfolio_settlement_record(
                    session,
                    _portfolio_settlement_record(settlement),
                    settlement.ticket_settlement_ids,
                )
            stored_run = _append_backtest_run_record(session, run_record)
            for record in slice_records:
                _append_backtest_slice_record(session, record)
            _append_backtest_metric_snapshot_record(
                session,
                metric_record,
                portfolio_settlement_ids,
                ticket_settlement_ids,
            )
            return backtest_run_value(stored_run)

    def save_walk_forward_backtest_v2_result(
        self,
        result: WalkForwardBacktestV2Result,
        *,
        calculated_at_utc: datetime | None = None,
    ) -> BacktestRun:
        result = validate_walk_forward_backtest_v2_result(result)
        calculated_at = _aware_utc(
            result.backtest_run.created_at_utc
            if calculated_at_utc is None
            else calculated_at_utc,
            "BACKTEST_V2 calculation timestamp",
        )
        final_cutoff = result.request.slates[-1].evaluation_as_of_at_utc
        if calculated_at < final_cutoff:
            raise ValueError(
                "BACKTEST_V2 calculation timestamp cannot precede its final cutoff"
            )

        run_record = backtest_v2_run_record(result.backtest_run)
        archive_records = backtest_v2_run_archive_records(result.backtest_run)
        slice_records = tuple(
            backtest_v2_slice_record(slate, slice_no, calculated_at)
            for slice_no, slate in enumerate(result.slate_results, start=1)
        )
        training_records = tuple(
            record
            for slate in result.slate_results
            for record in backtest_v2_training_source_records(slate)
        )
        evaluation_records = tuple(
            record
            for slate in result.slate_results
            for record in backtest_v2_evaluation_ref_records(slate)
        )
        result_records = tuple(
            record
            for slate in result.slate_results
            for record in backtest_v2_result_source_records(slate)
        )
        ticket_links = tuple(
            record
            for slate in result.slate_results
            for record in backtest_v2_slice_ticket_settlement_records(slate)
        )
        metric_record = backtest_v2_metric_snapshot_record(result, calculated_at)

        with self._session_factory.begin() as session:
            _preflight_walk_forward_v2_graph(
                session,
                result,
                run_record,
                archive_records,
                slice_records,
                training_records,
                evaluation_records,
                result_records,
                ticket_links,
                metric_record,
            )
            existing = session.get(BacktestV2RunRecord, run_record.backtest_run_id)
            if existing is not None:
                return backtest_v2_run_value(existing)

            _materialize_match_result_batches(
                session,
                tuple(
                    slate.match_result_batch.to_match_result_batch()
                    for slate in result.slate_results
                ),
            )
            for settlement in _ordered_ticket_settlements(
                tuple(
                    settlement
                    for slate in result.slate_results
                    for settlement in slate.ticket_settlements
                )
            ):
                _append_ticket_settlement(session, settlement)
            for settlement in _ordered_portfolio_settlements(
                tuple(
                    slate.portfolio_settlement
                    for slate in result.slate_results
                    if slate.portfolio_settlement is not None
                )
            ):
                _append_portfolio_settlement_record(
                    session,
                    _portfolio_settlement_record(settlement),
                    settlement.ticket_settlement_ids,
                )

            staged_run = BacktestV2RunRecord(
                **{
                    **_record_payload(run_record, set()),
                    "status": BacktestRunStatus.RUNNING.value,
                }
            )
            session.add(staged_run)
            session.flush()
            session.add_all(archive_records)
            session.flush()
            session.add_all(slice_records)
            session.flush()
            session.add_all(training_records)
            session.add_all(evaluation_records)
            session.add_all(result_records)
            session.add_all(ticket_links)
            session.flush()
            session.add(metric_record)
            session.flush()
            staged_run.status = BacktestRunStatus.COMPLETED.value
            session.flush()
            _verify_backtest_v2_graph(session, staged_run)
            return backtest_v2_run_value(staged_run)

    def find_backtest_v2_run_value(
        self,
        backtest_run_id: str,
    ) -> BacktestRun | None:
        with self._session_factory() as session:
            record = session.get(BacktestV2RunRecord, backtest_run_id)
            if record is None:
                return None
            _verify_backtest_v2_graph(session, record)
            return backtest_v2_run_value(record)

    def backtest_v2_slice_values(
        self,
        backtest_run_id: str,
    ) -> tuple[BacktestV2Slice, ...]:
        with self._session_factory() as session:
            run = session.get(BacktestV2RunRecord, backtest_run_id)
            if run is None:
                raise KeyError(f"unknown BACKTEST_V2 run: {backtest_run_id}")
            _verify_backtest_v2_graph(session, run)
            records = tuple(
                session.scalars(
                    select(BacktestV2SliceRecord)
                    .where(BacktestV2SliceRecord.backtest_run_id == backtest_run_id)
                    .order_by(BacktestV2SliceRecord.slice_no)
                )
            )
            return tuple(backtest_v2_slice_value(record) for record in records)

    def find_backtest_v2_metrics_value(
        self,
        backtest_run_id: str,
    ) -> BacktestV2Metrics | None:
        with self._session_factory() as session:
            run = session.get(BacktestV2RunRecord, backtest_run_id)
            if run is None:
                return None
            _verify_backtest_v2_graph(session, run)
            record = session.scalar(
                select(BacktestV2MetricSnapshotRecord).where(
                    BacktestV2MetricSnapshotRecord.backtest_run_id
                    == backtest_run_id
                )
            )
            if record is None:
                raise ValueError("stored BACKTEST_V2 run is missing metrics")
            return backtest_v2_metrics_value(record)

    def backtest_v2_table_counts(self) -> dict[str, int]:
        tables = {
            "backtest_v2_runs": BacktestV2RunRecord,
            "backtest_v2_run_archives": BacktestV2RunArchiveRecord,
            "backtest_v2_slices": BacktestV2SliceRecord,
            "backtest_v2_training_sources": BacktestV2TrainingSourceRecord,
            "backtest_v2_evaluation_refs": BacktestV2EvaluationRefRecord,
            "backtest_v2_result_sources": BacktestV2ResultSourceRecord,
            "backtest_v2_slice_ticket_settlements": (
                BacktestV2SliceTicketSettlementRecord
            ),
            "backtest_v2_metric_snapshots": BacktestV2MetricSnapshotRecord,
        }
        with self._session_factory() as session:
            return {
                name: int(session.scalar(select(func.count()).select_from(model)) or 0)
                for name, model in tables.items()
            }

    def find_backtest_metric_snapshot(
        self,
        metric_snapshot_id: str,
    ) -> BacktestMetricSnapshotRecord | None:
        with self._session_factory() as session:
            record = session.get(BacktestMetricSnapshotRecord, metric_snapshot_id)
            if record is not None:
                _verify_backtest_metric(session, record)
            return record

    def latest_backtest_metric_snapshots(
        self,
        backtest_run_id: str,
        as_of_at_utc: datetime,
    ) -> tuple[BacktestMetricSnapshotRecord, ...]:
        cutoff = _aware_utc(as_of_at_utc, "backtest metric cutoff")
        with self._session_factory() as session:
            records = tuple(
                session.scalars(
                    select(BacktestMetricSnapshotRecord)
                    .where(
                        BacktestMetricSnapshotRecord.backtest_run_id == backtest_run_id,
                        BacktestMetricSnapshotRecord.as_of_at_utc <= cutoff,
                    )
                    .order_by(
                        BacktestMetricSnapshotRecord.metric_scope,
                        BacktestMetricSnapshotRecord.metric_key,
                        BacktestMetricSnapshotRecord.snapshot_no,
                        BacktestMetricSnapshotRecord.calculated_at_utc,
                    )
                )
            )
            latest: dict[tuple[str, str], BacktestMetricSnapshotRecord] = {}
            for record in records:
                _verify_backtest_metric(session, record)
                key = (record.metric_scope, record.metric_key)
                previous = latest.get(key)
                if previous is None or (
                    record.snapshot_no,
                    record.calculated_at_utc,
                    record.metric_snapshot_id,
                ) > (
                    previous.snapshot_no,
                    previous.calculated_at_utc,
                    previous.metric_snapshot_id,
                ):
                    latest[key] = record
            return tuple(latest[key] for key in sorted(latest))

    def verify_ticket_settlement(self, settlement_id: str) -> None:
        if self.find_ticket_settlement(settlement_id) is None:
            raise KeyError(f"unknown ticket settlement: {settlement_id}")

    def verify_historical_archive_import(self, archive_id: str) -> None:
        if self.find_historical_archive_import(archive_id) is None:
            raise KeyError(f"unknown historical archive import: {archive_id}")

    def verify_archive_import(self, archive_id: str) -> None:
        self.verify_historical_archive_import(archive_id)

    def verify_portfolio_settlement(self, portfolio_settlement_id: str) -> None:
        if self.find_portfolio_settlement(portfolio_settlement_id) is None:
            raise KeyError(f"unknown portfolio settlement: {portfolio_settlement_id}")

    def verify_backtest_run(self, backtest_run_id: str) -> None:
        if self.find_backtest_run(backtest_run_id) is None:
            raise KeyError(f"unknown backtest run: {backtest_run_id}")

    def verify_backtest_slice(self, backtest_slice_id: str) -> None:
        if self.find_backtest_slice(backtest_slice_id) is None:
            raise KeyError(f"unknown backtest slice: {backtest_slice_id}")

    def verify_backtest_metric_snapshot(self, metric_snapshot_id: str) -> None:
        if self.find_backtest_metric_snapshot(metric_snapshot_id) is None:
            raise KeyError(f"unknown backtest metric snapshot: {metric_snapshot_id}")


def portfolio_settlement_hash(
    record: PortfolioSettlementRecord,
    ticket_settlement_ids: Sequence[str],
) -> str:
    payload = _record_payload(record, {"settlement_hash"})
    payload["ticket_settlement_ids"] = list(ticket_settlement_ids)
    return _sha256(_canonical_json(payload))


def historical_archive_import_record(
    manifest: HistoricalArchiveManifest,
    imported_at_utc: datetime,
) -> HistoricalArchiveImportRecord:
    imported_at = _aware_utc(imported_at_utc, "archive import timestamp")
    if manifest.created_at_utc > imported_at:
        raise ValueError("archive cannot be imported before it was created")
    return HistoricalArchiveImportRecord(
        archive_id=manifest.archive_id,
        archive_schema_version=manifest.archive_schema_version,
        provider_code=manifest.provider_code,
        dataset_kind=manifest.dataset_kind.value,
        created_at_utc=manifest.created_at_utc,
        source_reference=manifest.source_reference,
        source_description=manifest.source_description,
        license_note=manifest.license_note,
        data_mode=manifest.data_mode.value,
        payload_sha256=manifest.payload_sha256,
        record_count=manifest.record_count,
        imported_at_utc=imported_at,
    )


def historical_archive_manifest(
    record: HistoricalArchiveImportRecord,
) -> HistoricalArchiveManifest:
    return HistoricalArchiveManifest(
        archive_schema_version=record.archive_schema_version,
        archive_id=record.archive_id,
        provider_code=record.provider_code,
        dataset_kind=record.dataset_kind,
        created_at_utc=record.created_at_utc,
        source_reference=record.source_reference,
        source_description=record.source_description,
        license_note=record.license_note,
        data_mode=record.data_mode,
        payload_sha256=record.payload_sha256,
        record_count=record.record_count,
    )


def backtest_run_record(
    run: BacktestRun,
    *,
    replay_of_backtest_run_id: str | None = None,
) -> BacktestRunRecord:
    manifest_json = _canonical_json(
        {
            "backtest_version": run.backtest_version,
            "data_mode": run.data_mode,
            "date_from": run.date_from,
            "date_to": run.date_to,
            "strategy_snapshot": _domain_model_payload(run.strategy_snapshot),
            "code_revision": run.code_revision,
            "archive_provenance": [
                _domain_model_payload(item) for item in run.archive_provenance
            ],
            "expected_slice_ids": list(run.expected_slice_ids),
        }
    )
    record = BacktestRunRecord(
        backtest_run_id=run.backtest_run_id,
        schema_version="BACKTEST_RUN_RECORD_V1",
        backtest_version=run.backtest_version,
        data_mode=run.data_mode.value,
        date_from=run.date_from,
        date_to=run.date_to,
        strategy_version=run.strategy_version,
        strategy_config_json=run.strategy_config_json,
        strategy_config_hash=run.strategy_config_hash,
        code_revision=run.code_revision,
        status=run.status.value,
        engine_version=run.backtest_version,
        backtest_mode="STRICT_POINT_IN_TIME",
        started_at_utc=run.created_at_utc,
        completed_at_utc=run.created_at_utc,
        created_at_utc=run.created_at_utc,
        config_json=run.strategy_config_json,
        config_hash=run.strategy_config_hash,
        input_manifest_version="BACKTEST_REPLAY_INPUT_V2",
        input_manifest_json=manifest_json,
        input_manifest_hash=_sha256(manifest_json),
        run_hash="0" * 64,
        replay_of_backtest_run_id=replay_of_backtest_run_id,
    )
    record.run_hash = backtest_run_hash(record)
    return record


def backtest_run_value(record: BacktestRunRecord) -> BacktestRun:
    manifest = _canonical_payload(
        record.input_manifest_json,
        "BacktestRun input manifest",
    )
    if not isinstance(manifest, dict):
        raise ValueError("BacktestRun input manifest must be an object")
    provenance_payload = manifest.get("archive_provenance", [])
    expected_slice_ids = manifest.get("expected_slice_ids", [])
    if not isinstance(provenance_payload, list) or not isinstance(
        expected_slice_ids, list
    ):
        raise ValueError("BacktestRun input manifest lineage is invalid")
    try:
        archive_provenance = tuple(
            BacktestArchiveProvenance.model_validate(item)
            for item in provenance_payload
        )
    except (TypeError, ValueError) as error:
        raise ValueError("BacktestRun archive provenance is invalid") from error
    if any(not isinstance(value, str) or not value for value in expected_slice_ids):
        raise ValueError("BacktestRun expected slice IDs are invalid")
    strict_manifest = {
        "archive_provenance",
        "expected_slice_ids",
    } <= set(manifest)
    if record.input_manifest_version == "BACKTEST_REPLAY_INPUT_V2":
        if not strict_manifest:
            raise ValueError("BacktestRun V2 manifest is missing replay lineage")
    elif record.input_manifest_version != "BACKTEST_REPLAY_INPUT_V1":
        raise ValueError("unsupported BacktestRun input manifest version")
    run = BacktestRun(
        backtest_run_id=record.backtest_run_id,
        backtest_version=record.backtest_version,
        data_mode=record.data_mode,
        date_from=record.date_from,
        date_to=record.date_to,
        strategy_snapshot=BacktestStrategySnapshot(
            strategy_version=record.strategy_version,
            strategy_config_json=record.strategy_config_json,
            strategy_config_hash=record.strategy_config_hash,
        ),
        code_revision=record.code_revision,
        created_at_utc=record.created_at_utc,
        status=record.status,
        archive_provenance=archive_provenance,
        expected_slice_ids=tuple(expected_slice_ids),
    )
    expected_manifest = {
        "backtest_version": record.backtest_version,
        "data_mode": record.data_mode,
        "date_from": record.date_from.isoformat(),
        "date_to": record.date_to.isoformat(),
        "strategy_snapshot": _domain_model_payload(run.strategy_snapshot),
        "code_revision": record.code_revision,
    }
    if strict_manifest:
        expected_manifest.update(
            {
                "archive_provenance": [
                    _domain_model_payload(item) for item in run.archive_provenance
                ],
                "expected_slice_ids": list(run.expected_slice_ids),
            }
        )
    if (
        manifest != _canonical_value(expected_manifest)
        or run.backtest_run_id != record.backtest_run_id
        or run.backtest_version != record.backtest_version
        or run.data_mode.value != record.data_mode
        or run.date_from != record.date_from
        or run.date_to != record.date_to
        or run.strategy_version != record.strategy_version
        or run.strategy_config_json != record.strategy_config_json
        or run.strategy_config_hash != record.strategy_config_hash
        or run.code_revision != record.code_revision
        or run.created_at_utc != record.created_at_utc
        or run.status.value != record.status
    ):
        raise ValueError("stored BacktestRun columns are inconsistent")
    return run


def backtest_slice_record(
    value: BacktestSlice,
    *,
    slice_no: int = 1,
    created_at_utc: datetime | None = None,
) -> BacktestSliceRecord:
    manifest_json = _canonical_json(_domain_model_payload(value))
    record = BacktestSliceRecord(
        backtest_slice_id=value.slice_id,
        backtest_run_id=value.backtest_run_id,
        slice_no=slice_no,
        slice_version="BACKTEST_SLICE_RECORD_V2",
        parent_analysis_run_id=value.analysis_run_id,
        data_mode=value.data_mode.value,
        scope_kind="ANALYSIS_RUN",
        decision_scope_id=value.analysis_run_id,
        portfolio_revision_id=None,
        decision_as_of_at_utc=value.decision_as_of_at_utc,
        evaluation_as_of_at_utc=value.evaluation_as_of_at_utc,
        created_at_utc=(
            value.evaluation_as_of_at_utc
            if created_at_utc is None
            else _aware_utc(created_at_utc, "BacktestSlice creation timestamp")
        ),
        slice_manifest_json=manifest_json,
        slice_manifest_hash=_sha256(manifest_json),
        slice_hash="0" * 64,
        match_count=value.match_count,
        settled_match_count=value.settled_match_count,
        settled_ticket_count=value.settled_ticket_count,
        unsettled_ticket_count=value.unsettled_ticket_count,
        coverage=value.coverage,
    )
    record.slice_hash = backtest_slice_hash(record)
    return record


def backtest_slice_value(record: BacktestSliceRecord) -> BacktestSlice:
    if record.slice_version != "BACKTEST_SLICE_RECORD_V2":
        raise ValueError("unsupported BacktestSlice record version")
    payload = _canonical_payload(record.slice_manifest_json, "BacktestSlice manifest")
    value = BacktestSlice.model_validate(payload)
    if (
        value.slice_id != record.backtest_slice_id
        or value.backtest_run_id != record.backtest_run_id
        or value.data_mode.value != record.data_mode
        or value.decision_as_of_at_utc != record.decision_as_of_at_utc
        or value.evaluation_as_of_at_utc != record.evaluation_as_of_at_utc
        or value.analysis_run_id != record.parent_analysis_run_id
        or value.match_count != record.match_count
        or value.settled_match_count != record.settled_match_count
        or value.settled_ticket_count != record.settled_ticket_count
        or value.unsettled_ticket_count != record.unsettled_ticket_count
        or value.coverage != record.coverage
        or record.scope_kind != "ANALYSIS_RUN"
        or record.decision_scope_id != record.parent_analysis_run_id
        or record.portfolio_revision_id is not None
    ):
        raise ValueError("stored BacktestSlice columns are inconsistent")
    return value


def backtest_metric_snapshot_record(
    metrics: BacktestMetrics,
    metric_snapshot_id: str,
    as_of_at_utc: datetime,
    *,
    calculated_at_utc: datetime | None = None,
    backtest_slice_id: str | None = None,
    snapshot_no: int = 1,
    metric_key: str = "AGGREGATE",
    portfolio_settlement_ids: Sequence[str] = (),
    ticket_settlement_ids: Sequence[str] = (),
    slice_lineage: Sequence[tuple[str, Sequence[str]]] = (),
) -> BacktestMetricSnapshotRecord:
    settlement_ids = _unique_ids(portfolio_settlement_ids, "portfolio settlement")
    ticket_ids = _unique_ids(ticket_settlement_ids, "ticket settlement")
    frozen_slice_lineage = _normalized_slice_lineage(slice_lineage)
    metrics_json = _canonical_json(_domain_model_payload(metrics))
    lineage_json = _canonical_json(
        {
            "backtest_slice_ids": [item[0] for item in frozen_slice_lineage],
            "portfolio_settlement_ids": sorted(settlement_ids),
            "slice_result_ids": [
                {
                    "backtest_slice_id": slice_id,
                    "match_result_ids": list(result_ids),
                }
                for slice_id, result_ids in frozen_slice_lineage
            ],
            "ticket_settlement_ids": sorted(ticket_ids),
        }
    )
    as_of = _aware_utc(as_of_at_utc, "backtest metric cutoff")
    calculated_at = (
        as_of
        if calculated_at_utc is None
        else _aware_utc(calculated_at_utc, "backtest metric calculation timestamp")
    )
    record = BacktestMetricSnapshotRecord(
        metric_snapshot_id=metric_snapshot_id,
        backtest_run_id=metrics.backtest_run_id,
        backtest_slice_id=backtest_slice_id,
        snapshot_no=snapshot_no,
        metric_scope="SLICE" if backtest_slice_id is not None else "RUN",
        metric_key=metric_key,
        metric_version=metrics.metrics_version,
        as_of_at_utc=as_of,
        calculated_at_utc=calculated_at,
        metrics_json=metrics_json,
        metrics_hash=_sha256(metrics_json),
        lineage_json=lineage_json,
        lineage_hash=_sha256(lineage_json),
        snapshot_hash="0" * 64,
    )
    record.snapshot_hash = backtest_metric_snapshot_hash(
        record,
        settlement_ids,
        ticket_ids,
    )
    return record


def backtest_metrics_value(record: BacktestMetricSnapshotRecord) -> BacktestMetrics:
    payload = _canonical_payload(record.metrics_json, "backtest metrics")
    metrics = BacktestMetrics.model_validate(payload)
    if (
        metrics.backtest_run_id != record.backtest_run_id
        or metrics.metrics_version != record.metric_version
    ):
        raise ValueError("stored backtest metric columns are inconsistent")
    return metrics


def backtest_v2_run_record(run: BacktestRun) -> BacktestV2RunRecord:
    if run.backtest_version != BACKTEST_V2:
        raise ValueError("BACKTEST_V2 record requires a BACKTEST_V2 run")
    if run.status is not BacktestRunStatus.COMPLETED:
        raise ValueError("BACKTEST_V2 record requires a completed run")
    run_json = _canonical_json(_domain_model_payload(run))
    return BacktestV2RunRecord(
        backtest_run_id=run.backtest_run_id,
        schema_version="BACKTEST_V2_RUN_RECORD_V1",
        backtest_version=run.backtest_version,
        data_mode=run.data_mode.value,
        date_from=run.date_from,
        date_to=run.date_to,
        strategy_version=run.strategy_version,
        strategy_config_json=run.strategy_config_json,
        strategy_config_hash=run.strategy_config_hash,
        code_revision=run.code_revision,
        status=BacktestRunStatus.COMPLETED.value,
        created_at_utc=run.created_at_utc,
        expected_slice_count=len(run.expected_slice_ids),
        run_json=run_json,
        run_hash=_sha256(run_json),
    )


def backtest_v2_run_value(record: BacktestV2RunRecord) -> BacktestRun:
    if record.schema_version != "BACKTEST_V2_RUN_RECORD_V1":
        raise ValueError("unsupported BACKTEST_V2 run record version")
    if record.status != BacktestRunStatus.COMPLETED.value:
        raise ValueError("stored BACKTEST_V2 run is not complete")
    payload = _validate_canonical_json_hash(
        record.run_json,
        record.run_hash,
        "BACKTEST_V2 run",
    )
    run = BacktestRun.model_validate(payload)
    if (
        run.backtest_run_id != record.backtest_run_id
        or run.backtest_version != record.backtest_version
        or run.data_mode.value != record.data_mode
        or run.date_from != record.date_from
        or run.date_to != record.date_to
        or run.strategy_version != record.strategy_version
        or run.strategy_config_json != record.strategy_config_json
        or run.strategy_config_hash != record.strategy_config_hash
        or run.code_revision != record.code_revision
        or run.created_at_utc != record.created_at_utc
        or run.status.value != record.status
        or len(run.expected_slice_ids) != record.expected_slice_count
    ):
        raise ValueError("stored BACKTEST_V2 run columns are inconsistent")
    return run


def backtest_v2_run_archive_records(
    run: BacktestRun,
) -> tuple[BacktestV2RunArchiveRecord, ...]:
    return tuple(
        BacktestV2RunArchiveRecord(
            backtest_run_id=run.backtest_run_id,
            archive_id=archive.archive_id,
            archive_no=archive_no,
            archive_payload_sha256=archive.payload_sha256,
        )
        for archive_no, archive in enumerate(run.archive_provenance, start=1)
    )


def backtest_v2_slice_record(
    slate: WalkForwardBacktestV2SlateResult,
    slice_no: int,
    created_at_utc: datetime,
) -> BacktestV2SliceRecord:
    value = slate.backtest_slice
    decision = value.decision_snapshot
    portfolio = slate.model_decision.analysis_artifacts.portfolios[0]
    decision_json = _canonical_json(_domain_model_payload(decision))
    slice_json = _canonical_json(_domain_model_payload(value))
    settlement_json = _canonical_json(
        _domain_model_payload(slate.portfolio_settlement_result)
    )
    slate_json = _canonical_json(_domain_model_payload(slate.slate_snapshot))
    return BacktestV2SliceRecord(
        backtest_slice_id=value.slice_id,
        backtest_run_id=value.backtest_run_id,
        slice_no=slice_no,
        slice_version=value.slice_version,
        analysis_run_id=decision.analysis_run_id,
        quant_model_state_id=decision.quant_model_state_id,
        portfolio_id=portfolio.portfolio_id,
        portfolio_settlement_id=(
            slate.portfolio_settlement.portfolio_settlement_id
            if slate.portfolio_settlement is not None
            else None
        ),
        data_mode=value.data_mode.value,
        decision_as_of_at_utc=value.decision_as_of_at_utc,
        evaluation_as_of_at_utc=value.evaluation_as_of_at_utc,
        created_at_utc=_aware_utc(created_at_utc, "BACKTEST_V2 slice timestamp"),
        planned_target_count=len(decision.expected_match_ids),
        decision_target_count=len(decision.analyzed_match_ids),
        result_target_count=len(value.match_snapshots),
        quant_available_count=sum(
            evaluation.status.value == "AVAILABLE"
            for evaluation in decision.evaluations
        ),
        quant_unavailable_count=sum(
            evaluation.status.value == "UNAVAILABLE"
            for evaluation in decision.evaluations
        ),
        decision_snapshot_json=decision_json,
        decision_snapshot_hash=decision.snapshot_hash,
        slice_json=slice_json,
        slice_hash=value.slice_hash,
        settlement_result_json=settlement_json,
        settlement_result_hash=_sha256(settlement_json),
        slate_snapshot_json=slate_json,
        slate_snapshot_hash=_sha256(slate_json),
    )


def backtest_v2_slice_value(record: BacktestV2SliceRecord) -> BacktestV2Slice:
    decision_payload = _canonical_payload(
        record.decision_snapshot_json,
        "BACKTEST_V2 decision snapshot",
    )
    decision = BacktestV2DecisionSnapshot.model_validate(decision_payload)
    slice_payload = _canonical_payload(record.slice_json, "BACKTEST_V2 slice")
    value = BacktestV2Slice.model_validate(slice_payload)
    settlement = backtest_v2_settlement_result_value(record)
    slate = backtest_v2_slate_snapshot_value(record)
    if (
        record.slice_version != BACKTEST_V2_SLICE_V1
        or decision != value.decision_snapshot
        or decision.snapshot_hash != record.decision_snapshot_hash
        or value.slice_hash != record.slice_hash
        or value.slice_id != record.backtest_slice_id
        or value.backtest_run_id != record.backtest_run_id
        or value.data_mode.value != record.data_mode
        or value.decision_as_of_at_utc != record.decision_as_of_at_utc
        or value.evaluation_as_of_at_utc != record.evaluation_as_of_at_utc
        or decision.analysis_run_id != record.analysis_run_id
        or decision.quant_model_state_id != record.quant_model_state_id
        or len(decision.expected_match_ids) != record.planned_target_count
        or len(decision.analyzed_match_ids) != record.decision_target_count
        or len(value.match_snapshots) != record.result_target_count
        or sum(item.status.value == "AVAILABLE" for item in decision.evaluations)
        != record.quant_available_count
        or sum(item.status.value == "UNAVAILABLE" for item in decision.evaluations)
        != record.quant_unavailable_count
        or settlement.portfolio_id != record.portfolio_id
        or slate.backtest_run_id != record.backtest_run_id
        or slate.slice_id != record.backtest_slice_id
    ):
        raise ValueError("stored BACKTEST_V2 slice columns are inconsistent")
    settlement_id = (
        settlement.portfolio_settlement.portfolio_settlement_id
        if settlement.portfolio_settlement is not None
        else None
    )
    if settlement_id != record.portfolio_settlement_id:
        raise ValueError("stored BACKTEST_V2 portfolio settlement is inconsistent")
    return value


def backtest_v2_settlement_result_value(
    record: BacktestV2SliceRecord,
) -> PortfolioSettlementResult:
    payload = _validate_canonical_json_hash(
        record.settlement_result_json,
        record.settlement_result_hash,
        "BACKTEST_V2 settlement result",
    )
    return PortfolioSettlementResult.model_validate(payload)


def backtest_v2_slate_snapshot_value(
    record: BacktestV2SliceRecord,
) -> BacktestSlateSnapshot:
    payload = _validate_canonical_json_hash(
        record.slate_snapshot_json,
        record.slate_snapshot_hash,
        "BACKTEST_V2 slate snapshot",
    )
    return BacktestSlateSnapshot.model_validate(payload)


def backtest_v2_training_source_records(
    slate: WalkForwardBacktestV2SlateResult,
) -> tuple[BacktestV2TrainingSourceRecord, ...]:
    return tuple(
        BacktestV2TrainingSourceRecord(
            backtest_slice_id=slate.backtest_slice.slice_id,
            training_sequence=source.sequence,
            match_result_id=source.match_result_id,
            archive_id=source.archive_id,
            source_payload_hash=source.source_payload_hash,
            fact_hash=source.fact_hash,
            archive_payload_sha256=source.archive_payload_sha256,
        )
        for source in slate.backtest_slice.decision_snapshot.training_sources
    )


def backtest_v2_evaluation_ref_records(
    slate: WalkForwardBacktestV2SlateResult,
) -> tuple[BacktestV2EvaluationRefRecord, ...]:
    return tuple(
        BacktestV2EvaluationRefRecord(
            backtest_slice_id=slate.backtest_slice.slice_id,
            decision_no=decision_no,
            internal_match_id=evaluation.match_id,
            quant_model_evaluation_id=evaluation.quant_model_evaluation_id,
            status=evaluation.status.value,
            output_hash=evaluation.output_hash,
            model_prediction_hash=evaluation.model_prediction_hash,
            market_prediction_id=evaluation.market_prediction_id,
            quant_prediction_id=evaluation.quant_prediction_id,
            final_prediction_id=evaluation.final_prediction_id,
        )
        for decision_no, evaluation in enumerate(
            slate.backtest_slice.decision_snapshot.evaluations,
            start=1,
        )
    )


def backtest_v2_result_source_records(
    slate: WalkForwardBacktestV2SlateResult,
) -> tuple[BacktestV2ResultSourceRecord, ...]:
    source_by_id = {
        source.result.match_result_id: source
        for source in slate.match_result_batch.sources
    }
    records: list[BacktestV2ResultSourceRecord] = []
    for result_no, snapshot in enumerate(
        slate.backtest_slice.match_snapshots,
        start=1,
    ):
        source = source_by_id.get(snapshot.match_result_id)
        if source is None or source.result.match_id != snapshot.match_id:
            raise ValueError("BACKTEST_V2 match snapshot lacks its archived result")
        records.append(
            BacktestV2ResultSourceRecord(
                backtest_slice_id=slate.backtest_slice.slice_id,
                result_no=result_no,
                internal_match_id=snapshot.match_id,
                match_result_id=snapshot.match_result_id,
                archive_id=source.archive.archive_id,
                source_payload_hash=snapshot.match_result_payload_hash,
                archive_payload_sha256=source.archive.payload_sha256,
            )
        )
    return tuple(records)


def backtest_v2_slice_ticket_settlement_records(
    slate: WalkForwardBacktestV2SlateResult,
) -> tuple[BacktestV2SliceTicketSettlementRecord, ...]:
    return tuple(
        BacktestV2SliceTicketSettlementRecord(
            backtest_slice_id=slate.backtest_slice.slice_id,
            settlement_no=settlement_no,
            settlement_id=settlement.settlement_id,
        )
        for settlement_no, settlement in enumerate(
            slate.ticket_settlements,
            start=1,
        )
    )


def backtest_v2_metric_snapshot_record(
    result: WalkForwardBacktestV2Result,
    calculated_at_utc: datetime,
) -> BacktestV2MetricSnapshotRecord:
    metrics_json = _canonical_json(_domain_model_payload(result.metrics))
    lineage_json = _canonical_json(
        {
            "backtest_slice_ids": [
                slate.backtest_slice.slice_id for slate in result.slate_results
            ],
            "decision_snapshot_hashes": [
                slate.backtest_slice.decision_snapshot.snapshot_hash
                for slate in result.slate_results
            ],
            "portfolio_settlement_ids": [
                slate.portfolio_settlement.portfolio_settlement_id
                for slate in result.slate_results
                if slate.portfolio_settlement is not None
            ],
            "slate_snapshot_hashes": [
                _sha256(_canonical_json(_domain_model_payload(slate.slate_snapshot)))
                for slate in result.slate_results
            ],
            "slice_hashes": [
                slate.backtest_slice.slice_hash for slate in result.slate_results
            ],
            "ticket_settlement_ids": [
                settlement.settlement_id
                for slate in result.slate_results
                for settlement in slate.ticket_settlements
            ],
        }
    )
    record = BacktestV2MetricSnapshotRecord(
        metric_snapshot_id=stable_id(
            "backtest-v2-metric-snapshot",
            result.backtest_run.backtest_run_id,
            result.metrics.metrics_version,
        ),
        backtest_run_id=result.backtest_run.backtest_run_id,
        metric_version=result.metrics.metrics_version,
        as_of_at_utc=result.request.slates[-1].evaluation_as_of_at_utc,
        calculated_at_utc=_aware_utc(
            calculated_at_utc,
            "BACKTEST_V2 metric calculation timestamp",
        ),
        metrics_json=metrics_json,
        metrics_hash=_sha256(metrics_json),
        lineage_json=lineage_json,
        lineage_hash=_sha256(lineage_json),
        snapshot_hash="0" * 64,
    )
    record.snapshot_hash = backtest_v2_metric_snapshot_hash(record)
    return record


def backtest_v2_metrics_value(
    record: BacktestV2MetricSnapshotRecord,
) -> BacktestV2Metrics:
    payload = _validate_canonical_json_hash(
        record.metrics_json,
        record.metrics_hash,
        "BACKTEST_V2 metrics",
    )
    metrics = BacktestV2Metrics.model_validate(payload)
    if (
        record.metric_version != BACKTEST_METRICS_V2
        or metrics.metrics_version != record.metric_version
        or metrics.backtest_run_id != record.backtest_run_id
        or backtest_v2_metric_snapshot_hash(record) != record.snapshot_hash
    ):
        raise ValueError("stored BACKTEST_V2 metrics are inconsistent")
    _validate_canonical_json_hash(
        record.lineage_json,
        record.lineage_hash,
        "BACKTEST_V2 metric lineage",
    )
    return metrics


def backtest_v2_metric_snapshot_hash(
    record: BacktestV2MetricSnapshotRecord,
) -> str:
    return _sha256(_canonical_json(_record_payload(record, {"snapshot_hash"})))


def _portfolio_settlement_record(
    settlement: PortfolioSettlement,
) -> PortfolioSettlementRecord:
    is_base = settlement.scope_kind.value == "ANALYSIS_RUN"
    record = PortfolioSettlementRecord(
        portfolio_settlement_id=settlement.portfolio_settlement_id,
        settlement_kind=settlement.settlement_kind,
        scope_kind=settlement.scope_kind.value,
        parent_analysis_run_id=settlement.parent_analysis_run_id,
        decision_scope_id=settlement.decision_scope_id,
        portfolio_revision_id=(None if is_base else settlement.decision_scope_id),
        portfolio_id=settlement.portfolio_id,
        base_portfolio_id=settlement.portfolio_id if is_base else None,
        budget_fen=settlement.budget_fen,
        total_stake_fen=settlement.deployed_stake_fen,
        cash_fen=settlement.original_cash_fen,
        gross_payout_fen=settlement.gross_ticket_payout_fen,
        profit_loss_fen=settlement.profit_loss_fen,
        ticket_count=len(settlement.ticket_settlement_ids),
        settlement_policy_version=settlement.settlement_policy_version,
        settled_at_utc=settlement.settled_at_utc,
        settlement_hash="0" * 64,
        supersedes_portfolio_settlement_id=(
            settlement.supersedes_portfolio_settlement_id
        ),
    )
    record.settlement_hash = portfolio_settlement_hash(
        record,
        settlement.ticket_settlement_ids,
    )
    return record


def _portfolio_settlement_value(
    record: PortfolioSettlementRecord,
    ticket_settlement_ids: Sequence[str],
) -> PortfolioSettlement:
    ending_capital_fen = record.cash_fen + record.gross_payout_fen
    return PortfolioSettlement(
        portfolio_settlement_id=record.portfolio_settlement_id,
        settlement_kind=record.settlement_kind,
        scope_kind=record.scope_kind,
        parent_analysis_run_id=record.parent_analysis_run_id,
        decision_scope_id=record.decision_scope_id,
        portfolio_id=record.portfolio_id,
        ticket_settlement_ids=tuple(ticket_settlement_ids),
        budget_fen=record.budget_fen,
        deployed_stake_fen=record.total_stake_fen,
        original_cash_fen=record.cash_fen,
        gross_ticket_payout_fen=record.gross_payout_fen,
        ending_capital_fen=ending_capital_fen,
        profit_loss_fen=record.profit_loss_fen,
        roi_on_budget=(
            None
            if record.budget_fen == 0
            else Decimal(record.profit_loss_fen) / Decimal(record.budget_fen)
        ),
        roi_on_deployed=(
            None
            if record.total_stake_fen == 0
            else Decimal(record.profit_loss_fen) / Decimal(record.total_stake_fen)
        ),
        settlement_policy_version=record.settlement_policy_version,
        settled_at_utc=record.settled_at_utc,
        supersedes_portfolio_settlement_id=(record.supersedes_portfolio_settlement_id),
    )


def backtest_run_hash(record: BacktestRunRecord) -> str:
    return _sha256(_canonical_json(_record_payload(record, {"run_hash"})))


def backtest_slice_hash(record: BacktestSliceRecord) -> str:
    return _sha256(_canonical_json(_record_payload(record, {"slice_hash"})))


def backtest_metric_snapshot_hash(
    record: BacktestMetricSnapshotRecord,
    portfolio_settlement_ids: Sequence[str] = (),
    ticket_settlement_ids: Sequence[str] = (),
) -> str:
    payload = _record_payload(record, {"snapshot_hash"})
    payload["portfolio_settlement_ids"] = sorted(portfolio_settlement_ids)
    payload["ticket_settlement_ids"] = sorted(ticket_settlement_ids)
    return _sha256(_canonical_json(payload))


def _metric_settlement_ids(
    record: BacktestMetricSnapshotRecord,
) -> tuple[str, ...]:
    lineage = _canonical_payload(
        record.lineage_json,
        "backtest metric lineage",
    )
    values = (
        lineage.get("portfolio_settlement_ids") if isinstance(lineage, dict) else None
    )
    if not isinstance(values, list) or any(
        not isinstance(value, str) for value in values
    ):
        raise ValueError("backtest metric lineage requires portfolio settlement IDs")
    return _unique_ids(tuple(values), "portfolio settlement")


def _metric_ticket_settlement_ids(
    record: BacktestMetricSnapshotRecord,
) -> tuple[str, ...]:
    lineage = _canonical_payload(
        record.lineage_json,
        "backtest metric lineage",
    )
    values = (
        lineage.get("ticket_settlement_ids", []) if isinstance(lineage, dict) else None
    )
    if not isinstance(values, list) or any(
        not isinstance(value, str) for value in values
    ):
        raise ValueError("backtest metric lineage has invalid ticket settlement IDs")
    return _unique_ids(tuple(values), "ticket settlement")


def _metric_slice_lineage(
    record: BacktestMetricSnapshotRecord,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    lineage = _canonical_payload(
        record.lineage_json,
        "backtest metric lineage",
    )
    values = lineage.get("slice_result_ids", []) if isinstance(lineage, dict) else None
    if not isinstance(values, list):
        raise ValueError("backtest metric lineage has invalid slice results")
    parsed: list[tuple[str, tuple[str, ...]]] = []
    for item in values:
        if not isinstance(item, dict):
            raise ValueError("backtest metric slice lineage must contain objects")
        slice_id = item.get("backtest_slice_id")
        result_ids = item.get("match_result_ids")
        if (
            not isinstance(slice_id, str)
            or not slice_id
            or not isinstance(result_ids, list)
            or any(not isinstance(result_id, str) for result_id in result_ids)
        ):
            raise ValueError("backtest metric slice lineage is invalid")
        parsed.append((slice_id, tuple(result_ids)))
    return _normalized_slice_lineage(parsed)


def _normalized_slice_lineage(
    values: Sequence[tuple[str, Sequence[str]]],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    result = tuple(
        (slice_id, _unique_ids(result_ids, "slice MatchResult"))
        for slice_id, result_ids in values
    )
    if any(not slice_id for slice_id, _ in result) or len(
        {slice_id for slice_id, _ in result}
    ) != len(result):
        raise ValueError("metric BacktestSlice IDs must be nonempty and unique")
    return result


def _historical_archive_import_records(
    manifests: Sequence[HistoricalArchiveManifest],
    imported_at_utc: datetime,
) -> tuple[HistoricalArchiveImportRecord, ...]:
    imported_at = _aware_utc(imported_at_utc, "archive import timestamp")
    values = tuple(manifests)
    archive_ids = tuple(manifest.archive_id for manifest in values)
    identities = tuple(_archive_identity(manifest) for manifest in values)
    if len(set(archive_ids)) != len(archive_ids):
        raise ValueError("historical archive IDs must be unique")
    if len(set(identities)) != len(identities):
        raise ValueError(
            "historical archive provider/kind/checksum identities must be unique"
        )
    return tuple(
        historical_archive_import_record(manifest, imported_at) for manifest in values
    )


def _archive_identity(
    value: HistoricalArchiveManifest | HistoricalArchiveImportRecord,
) -> tuple[str, str, str]:
    dataset_kind = value.dataset_kind
    return (
        value.provider_code,
        dataset_kind.value if isinstance(dataset_kind, Enum) else dataset_kind,
        value.payload_sha256,
    )


def _append_historical_archive_imports(
    session: Session,
    records: Sequence[HistoricalArchiveImportRecord],
) -> tuple[HistoricalArchiveImportRecord, ...]:
    resolved: list[HistoricalArchiveImportRecord | None] = []
    pending: list[HistoricalArchiveImportRecord] = []

    # Resolve every immutable identity before adding anything to the session.
    for record in records:
        by_id = session.get(HistoricalArchiveImportRecord, record.archive_id)
        by_identity = session.scalar(
            select(HistoricalArchiveImportRecord).where(
                HistoricalArchiveImportRecord.provider_code == record.provider_code,
                HistoricalArchiveImportRecord.dataset_kind == record.dataset_kind,
                HistoricalArchiveImportRecord.payload_sha256 == record.payload_sha256,
            )
        )
        if (
            by_id is not None
            and by_identity is not None
            and by_id.archive_id != by_identity.archive_id
        ):
            raise ValueError(
                "immutable historical archive import conflicts with stored data"
            )
        existing = by_id or by_identity
        if existing is None:
            pending.append(record)
            resolved.append(None)
            continue
        _verify_historical_archive_import(existing)
        if existing.archive_id != record.archive_id or historical_archive_manifest(
            existing
        ) != historical_archive_manifest(record):
            raise ValueError(
                "immutable historical archive import conflicts with stored data"
            )
        resolved.append(existing)

    if pending:
        session.add_all(pending)
        session.flush()

    pending_by_id = {record.archive_id: record for record in pending}
    result = tuple(
        existing or pending_by_id[record.archive_id]
        for record, existing in zip(records, resolved, strict=True)
    )
    for record in result:
        _verify_historical_archive_import(record)
    return result


def _provider_metadata(provider_code: str) -> tuple[str, dict[str, str]]:
    return stable_id("provider", provider_code), {
        "code": provider_code,
        "name": provider_code.replace("_", " ").title(),
        "provider_kind": _provider_kind(provider_code),
    }


def _provider_kind(provider_code: str) -> str:
    if "SPORTTERY" in provider_code:
        return "SPORTTERY"
    if "ODDS" in provider_code or "MARKET" in provider_code:
        return "MARKET_ODDS"
    return "FIXTURE"


def _provider_mapping_record(
    mapping: ProviderMatchMapping,
) -> ProviderMatchMappingRecord:
    provider_id, _ = _provider_metadata(mapping.provider_code)
    return ProviderMatchMappingRecord(
        mapping_id=mapping.mapping_id,
        provider_id=provider_id,
        external_namespace=mapping.external_namespace,
        external_match_id=mapping.external_match_id,
        internal_match_id=mapping.internal_match_id,
        resolution_method=mapping.resolution_method,
        confidence=mapping.confidence,
        available_at_utc=mapping.available_at_utc,
        supersedes_mapping_id=None,
    )


def _preflight_provider(session: Session, provider_code: str) -> None:
    provider_id, values = _provider_metadata(provider_code)
    by_id = session.get(ProviderRecord, provider_id)
    by_code = session.scalar(
        select(ProviderRecord).where(ProviderRecord.code == provider_code)
    )
    if (
        by_id is not None
        and by_code is not None
        and by_id.provider_id != by_code.provider_id
    ):
        raise ValueError(f"provider identity conflicts for {provider_code}")
    existing = by_id or by_code
    if existing is None:
        return
    mismatched = [
        field for field, value in values.items() if getattr(existing, field) != value
    ]
    if existing.provider_id != provider_id:
        mismatched.insert(0, "provider_id")
    if mismatched:
        raise ValueError(
            f"provider identity conflicts for {provider_code}: {', '.join(mismatched)}"
        )


def _preflight_provider_mapping(
    session: Session,
    mapping: ProviderMatchMapping,
) -> None:
    expected = _provider_mapping_record(mapping)
    if session.get(MatchRecord, mapping.internal_match_id) is None:
        raise ValueError(
            "provider match mapping references an unknown MatchRecord: "
            f"{mapping.internal_match_id}"
        )
    by_id = session.get(ProviderMatchMappingRecord, mapping.mapping_id)
    by_external = session.scalar(
        select(ProviderMatchMappingRecord).where(
            ProviderMatchMappingRecord.provider_id == expected.provider_id,
            ProviderMatchMappingRecord.external_namespace
            == expected.external_namespace,
            ProviderMatchMappingRecord.external_match_id == expected.external_match_id,
        )
    )
    if (
        by_id is not None
        and by_external is not None
        and by_id.mapping_id != by_external.mapping_id
    ):
        raise ValueError("immutable provider match mapping conflicts with stored data")
    existing = by_id or by_external
    if existing is not None:
        _assert_same_record(existing, expected, "provider match mapping")


def _unique_batch_mappings(
    batches: Sequence[MatchResultBatch],
) -> tuple[ProviderMatchMapping, ...]:
    by_id: dict[str, ProviderMatchMapping] = {}
    by_external: dict[tuple[str, str, str], ProviderMatchMapping] = {}
    ordered: list[ProviderMatchMapping] = []
    for batch in batches:
        validated = MatchResultBatch.model_validate(batch.model_dump(mode="python"))
        if validated != batch:
            raise ValueError("match result batch normalization is inconsistent")
        for mapping in batch.mappings:
            previous = by_id.get(mapping.mapping_id)
            if previous is not None:
                if previous != mapping:
                    raise ValueError("match result mapping ID conflicts within batches")
                continue
            identity = (
                mapping.provider_code,
                mapping.external_namespace,
                mapping.external_match_id,
            )
            external_previous = by_external.get(identity)
            if external_previous is not None and external_previous != mapping:
                raise ValueError(
                    "match result provider mapping identity conflicts within batches"
                )
            by_id[mapping.mapping_id] = mapping
            by_external[identity] = mapping
            ordered.append(mapping)
    return tuple(ordered)


def _unique_batch_results(
    batches: Sequence[MatchResultBatch],
) -> tuple[MatchResult, ...]:
    by_id: dict[str, MatchResult] = {}
    by_source: dict[tuple[str, str], MatchResult] = {}
    ordered: list[MatchResult] = []
    for batch in batches:
        for result in batch.results:
            previous = by_id.get(result.match_result_id)
            if previous is not None:
                if previous != result:
                    raise ValueError("MatchResult ID conflicts within batches")
                continue
            identity = (
                result.provider_code,
                result.source_result_key,
            )
            source_previous = by_source.get(identity)
            if source_previous is not None and source_previous != result:
                raise ValueError("MatchResult source identity conflicts within batches")
            by_id[result.match_result_id] = result
            by_source[identity] = result
            ordered.append(result)
    return tuple(ordered)


def _preflight_match_result_batches(
    session: Session,
    batches: Sequence[MatchResultBatch],
) -> None:
    values = tuple(batches)
    mappings = _unique_batch_mappings(values)
    results = _unique_batch_results(values)
    provider_codes = {mapping.provider_code for mapping in mappings} | {
        result.provider_code for result in results
    }
    for provider_code in sorted(provider_codes):
        _preflight_provider(session, provider_code)
    for mapping in mappings:
        _preflight_provider_mapping(session, mapping)

    result_mappings = _batch_result_mappings(mappings, results)
    by_id = {result.match_result_id: result for result in results}
    successor_by_id: dict[str, MatchResult] = {}
    roots_by_stream: dict[tuple[str, str], MatchResult] = {}
    for result in results:
        _validate_match_result_payload(result)
        if session.get(MatchRecord, result.match_id) is None:
            raise ValueError(
                f"MatchResult references an unknown MatchRecord: {result.match_id}"
            )
        mapping = result_mappings[result.match_result_id]
        provider_id, _ = _provider_metadata(result.provider_code)
        by_result_id = session.get(MatchResultRecord, result.match_result_id)
        by_source = session.scalar(
            select(MatchResultRecord).where(
                MatchResultRecord.provider_id == provider_id,
                MatchResultRecord.source_result_key == result.source_result_key,
            )
        )
        if (
            by_result_id is not None
            and by_source is not None
            and by_result_id.match_result_id != by_source.match_result_id
        ):
            raise ValueError("immutable MatchResult conflicts with stored data")
        existing = by_result_id or by_source
        if existing is not None:
            if (
                existing.provider_mapping_id != mapping.mapping_id
                or _match_result(session, existing) != result
            ):
                raise ValueError("immutable MatchResult conflicts with stored data")

        previous_id = result.supersedes_match_result_id
        if previous_id is None:
            stream = (result.provider_code, result.match_id)
            input_root = roots_by_stream.get(stream)
            if input_root is not None and input_root != result:
                raise ValueError("a MatchResult stream can have only one root")
            roots_by_stream[stream] = result
            stored_root = session.scalar(
                select(MatchResultRecord).where(
                    MatchResultRecord.provider_id == provider_id,
                    MatchResultRecord.internal_match_id == result.match_id,
                    MatchResultRecord.supersedes_match_result_id.is_(None),
                )
            )
            if (
                stored_root is not None
                and stored_root.match_result_id != result.match_result_id
            ):
                raise ValueError("a MatchResult stream can have only one root")
            continue
        input_previous = by_id.get(previous_id)
        stored_previous = (
            None
            if input_previous is not None
            else session.get(MatchResultRecord, previous_id)
        )
        if input_previous is not None:
            valid_previous = (
                input_previous.match_id == result.match_id
                and input_previous.provider_code == result.provider_code
                and input_previous.available_at_utc <= result.available_at_utc
                and input_previous.ingested_at_utc < result.ingested_at_utc
            )
        else:
            valid_previous = (
                stored_previous is not None
                and stored_previous.internal_match_id == result.match_id
                and stored_previous.provider_id == provider_id
                and stored_previous.available_at_utc <= result.available_at_utc
                and stored_previous.ingested_at_utc < result.ingested_at_utc
            )
        if not valid_previous:
            raise ValueError(
                "MatchResult supersession must reference an earlier version "
                "for the same match and provider"
            )
        input_successor = successor_by_id.get(previous_id)
        if input_successor is not None and input_successor != result:
            raise ValueError("a MatchResult version can be superseded only once")
        successor_by_id[previous_id] = result
        stored_successor = session.scalar(
            select(MatchResultRecord).where(
                MatchResultRecord.supersedes_match_result_id == previous_id
            )
        )
        if (
            stored_successor is not None
            and stored_successor.match_result_id != result.match_result_id
        ):
            raise ValueError("a MatchResult version can be superseded only once")

    _ordered_match_results(session, results)


def _batch_result_mappings(
    mappings: Sequence[ProviderMatchMapping],
    results: Sequence[MatchResult],
) -> dict[str, ProviderMatchMapping]:
    by_pair: dict[tuple[str, str], list[ProviderMatchMapping]] = {}
    for mapping in mappings:
        by_pair.setdefault(
            (mapping.provider_code, mapping.internal_match_id), []
        ).append(mapping)
    resolved: dict[str, ProviderMatchMapping] = {}
    for result in results:
        candidates = by_pair.get((result.provider_code, result.match_id), [])
        if len(candidates) != 1:
            raise ValueError(
                "each MatchResult requires exactly one supplied provider mapping"
            )
        mapping = candidates[0]
        if (
            mapping.available_at_utc > result.available_at_utc
            or mapping.available_at_utc > result.ingested_at_utc
        ):
            raise ValueError("MatchResult cannot precede its provider mapping")
        resolved[result.match_result_id] = mapping
    return resolved


def _append_provider(session: Session, provider_code: str) -> ProviderRecord:
    _preflight_provider(session, provider_code)
    provider_id, values = _provider_metadata(provider_code)
    existing = session.get(ProviderRecord, provider_id)
    if existing is not None:
        return existing
    record = ProviderRecord(provider_id=provider_id, **values)
    session.add(record)
    return record


def _append_provider_mapping(
    session: Session,
    mapping: ProviderMatchMapping,
) -> ProviderMatchMappingRecord:
    _preflight_provider_mapping(session, mapping)
    expected = _provider_mapping_record(mapping)
    existing = session.get(ProviderMatchMappingRecord, mapping.mapping_id)
    if existing is None:
        existing = session.scalar(
            select(ProviderMatchMappingRecord).where(
                ProviderMatchMappingRecord.provider_id == expected.provider_id,
                ProviderMatchMappingRecord.external_namespace
                == expected.external_namespace,
                ProviderMatchMappingRecord.external_match_id
                == expected.external_match_id,
            )
        )
    if existing is not None:
        return existing
    session.add(expected)
    return expected


def _materialize_match_result_batches(
    session: Session,
    batches: Sequence[MatchResultBatch],
) -> dict[str, MatchResult]:
    values = tuple(batches)
    mappings = _unique_batch_mappings(values)
    results = _unique_batch_results(values)
    provider_codes = {mapping.provider_code for mapping in mappings} | {
        result.provider_code for result in results
    }
    for provider_code in sorted(provider_codes):
        _append_provider(session, provider_code)
    session.flush()
    for mapping in mappings:
        _append_provider_mapping(session, mapping)
    session.flush()
    result_mappings = _batch_result_mappings(mappings, results)
    return {
        result.match_result_id: _append_match_result(
            session,
            result,
            result_mappings[result.match_result_id].mapping_id,
        )
        for result in _ordered_match_results(session, results)
    }


def _ordered_match_results(
    session: Session,
    results: Sequence[MatchResult],
) -> tuple[MatchResult, ...]:
    by_id = {result.match_result_id: result for result in results}
    if len(by_id) != len(results):
        raise ValueError("match result IDs must be unique")
    ordered: list[MatchResult] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(result: MatchResult) -> None:
        result_id = result.match_result_id
        if result_id in visited:
            return
        if result_id in visiting:
            raise ValueError("MatchResult supersession graph contains a cycle")
        visiting.add(result_id)
        previous_id = result.supersedes_match_result_id
        if previous_id is not None:
            previous = by_id.get(previous_id)
            if previous is not None:
                visit(previous)
            elif session.get(MatchResultRecord, previous_id) is None:
                raise ValueError(f"unknown superseded MatchResult: {previous_id}")
        visiting.remove(result_id)
        visited.add(result_id)
        ordered.append(result)

    for result in results:
        visit(result)
    return tuple(ordered)


def _append_match_results(
    session: Session,
    results: Sequence[MatchResult],
) -> tuple[MatchResult, ...]:
    stored = {
        result.match_result_id: _append_match_result(session, result)
        for result in _ordered_match_results(session, results)
    }
    return tuple(stored[result.match_result_id] for result in results)


def _append_match_result(
    session: Session,
    result: MatchResult,
    provider_mapping_id: str | None = None,
) -> MatchResult:
    _validate_match_result_payload(result)
    provider = session.scalar(
        select(ProviderRecord).where(ProviderRecord.code == result.provider_code)
    )
    if provider is None:
        raise ValueError(f"unknown MatchResult provider: {result.provider_code}")
    existing = session.get(MatchResultRecord, result.match_result_id)
    if existing is None:
        existing = session.scalar(
            select(MatchResultRecord).where(
                MatchResultRecord.provider_id == provider.provider_id,
                MatchResultRecord.source_result_key == result.source_result_key,
            )
        )
    if existing is not None:
        stored = _match_result(session, existing)
        if stored != result or (
            provider_mapping_id is not None
            and existing.provider_mapping_id != provider_mapping_id
        ):
            raise ValueError("immutable MatchResult conflicts with stored data")
        return stored
    mapping = _match_result_mapping(
        session,
        provider.provider_id,
        result,
        provider_mapping_id,
    )
    if result.supersedes_match_result_id is None:
        root = session.scalar(
            select(MatchResultRecord.match_result_id).where(
                MatchResultRecord.provider_id == provider.provider_id,
                MatchResultRecord.internal_match_id == result.match_id,
                MatchResultRecord.supersedes_match_result_id.is_(None),
            )
        )
        if root is not None:
            raise ValueError("a MatchResult stream can have only one root")
    if result.supersedes_match_result_id is not None:
        previous = session.get(
            MatchResultRecord,
            result.supersedes_match_result_id,
        )
        if (
            previous is None
            or previous.internal_match_id != result.match_id
            or previous.provider_id != provider.provider_id
            or previous.available_at_utc > result.available_at_utc
            or previous.ingested_at_utc >= result.ingested_at_utc
        ):
            raise ValueError(
                "MatchResult supersession must reference an earlier version "
                "for the same match and provider"
            )
    record = MatchResultRecord(
        match_result_id=result.match_result_id,
        internal_match_id=result.match_id,
        provider_id=provider.provider_id,
        provider_mapping_id=mapping.mapping_id,
        home_goals=result.home_goals,
        away_goals=result.away_goals,
        observed_at_utc=result.observed_at_utc,
        available_at_utc=result.available_at_utc,
        ingested_at_utc=result.ingested_at_utc,
        source_result_key=result.source_result_key,
        payload_hash=result.payload_hash,
        supersedes_match_result_id=result.supersedes_match_result_id,
    )
    session.add(record)
    session.flush()
    return _match_result(session, record)


def _match_result_mapping(
    session: Session,
    provider_id: str,
    result: MatchResult,
    provider_mapping_id: str | None,
) -> ProviderMatchMappingRecord:
    if provider_mapping_id is not None:
        mapping = session.get(ProviderMatchMappingRecord, provider_mapping_id)
        candidates = () if mapping is None else (mapping,)
    else:
        candidates = tuple(
            session.scalars(
                select(ProviderMatchMappingRecord)
                .where(
                    ProviderMatchMappingRecord.provider_id == provider_id,
                    ProviderMatchMappingRecord.internal_match_id == result.match_id,
                    ProviderMatchMappingRecord.available_at_utc
                    <= result.available_at_utc,
                    ProviderMatchMappingRecord.available_at_utc
                    <= result.ingested_at_utc,
                )
                .order_by(
                    ProviderMatchMappingRecord.available_at_utc.desc(),
                    ProviderMatchMappingRecord.mapping_id.desc(),
                )
            )
        )
    if len(candidates) != 1 and provider_mapping_id is not None:
        raise ValueError("MatchResult requires its exact provider mapping")
    if not candidates:
        raise ValueError("MatchResult requires a visible provider match mapping")
    mapping = candidates[0]
    if (
        mapping.provider_id != provider_id
        or mapping.internal_match_id != result.match_id
        or mapping.available_at_utc > result.available_at_utc
        or mapping.available_at_utc > result.ingested_at_utc
    ):
        raise ValueError("MatchResult provider mapping lineage is inconsistent")
    return mapping


def _ticket_settlement_record(settlement: Settlement) -> TicketSettlementRecord:
    settlement_json = _canonical_json(settlement.model_dump(mode="json"))
    is_base = settlement.scope_kind.value == "ANALYSIS_RUN"
    return TicketSettlementRecord(
        settlement_id=settlement.settlement_id,
        settlement_kind=settlement.settlement_kind,
        scope_kind=settlement.scope_kind.value,
        parent_analysis_run_id=settlement.parent_analysis_run_id,
        decision_scope_id=settlement.decision_scope_id,
        portfolio_revision_id=None if is_base else settlement.decision_scope_id,
        portfolio_id=settlement.portfolio_id,
        ticket_id=settlement.ticket_id,
        base_portfolio_id=settlement.portfolio_id if is_base else None,
        base_ticket_id=settlement.ticket_id if is_base else None,
        status=settlement.status.value,
        stake_fen=settlement.stake_fen,
        gross_payout_fen=settlement.gross_payout_fen,
        profit_loss_fen=settlement.profit_loss_fen,
        payout_policy_version=settlement.payout_policy_version,
        settlement_policy_version=settlement.settlement_policy_version,
        settled_at_utc=settlement.settled_at_utc,
        settlement_json=settlement_json,
        settlement_hash=_sha256(settlement_json),
        supersedes_settlement_id=settlement.supersedes_settlement_id,
    )


def _existing_ticket_settlement(
    session: Session,
    settlement: Settlement,
) -> TicketSettlementRecord | None:
    expected = _ticket_settlement_record(settlement)
    by_id = session.get(TicketSettlementRecord, settlement.settlement_id)
    by_hash = session.scalar(
        select(TicketSettlementRecord).where(
            TicketSettlementRecord.settlement_hash == expected.settlement_hash
        )
    )
    if (
        by_id is not None
        and by_hash is not None
        and by_id.settlement_id != by_hash.settlement_id
    ):
        raise ValueError("immutable ticket settlement conflicts with stored data")
    existing = by_id or by_hash
    if existing is not None:
        _assert_same_record(existing, expected, "ticket settlement")
        if _ticket_settlement(session, existing) != settlement:
            raise ValueError("immutable ticket settlement conflicts with stored data")
    return existing


def _append_ticket_settlement(
    session: Session,
    settlement: Settlement,
) -> Settlement:
    existing = _existing_ticket_settlement(session, settlement)
    if existing is not None:
        return _ticket_settlement(session, existing)
    result_records = _validate_ticket_settlement_lineage(session, settlement)
    record = _ticket_settlement_record(settlement)
    session.add(record)
    session.flush()
    session.add_all(
        TicketSettlementMatchResultRecord(
            settlement_id=record.settlement_id,
            leg_no=leg_no,
            match_result_id=result.match_result_id,
            internal_match_id=result.internal_match_id,
        )
        for leg_no, result in enumerate(result_records, start=1)
    )
    session.flush()
    return _ticket_settlement(session, record)


def _existing_portfolio_settlement(
    session: Session,
    record: PortfolioSettlementRecord,
    ticket_settlement_ids: Sequence[str],
) -> PortfolioSettlementRecord | None:
    source_ids = _unique_ids(ticket_settlement_ids, "ticket settlement")
    expected_hash = portfolio_settlement_hash(record, source_ids)
    _require_hash(record.settlement_hash, expected_hash, "portfolio settlement")
    by_id = session.get(
        PortfolioSettlementRecord,
        record.portfolio_settlement_id,
    )
    by_hash = session.scalar(
        select(PortfolioSettlementRecord).where(
            PortfolioSettlementRecord.settlement_hash == record.settlement_hash
        )
    )
    if (
        by_id is not None
        and by_hash is not None
        and by_id.portfolio_settlement_id != by_hash.portfolio_settlement_id
    ):
        raise ValueError("immutable portfolio settlement conflicts with stored data")
    existing = by_id or by_hash
    if existing is not None:
        _assert_same_record(existing, record, "portfolio settlement")
        _verify_portfolio_settlement(session, existing, source_ids)
    return existing


def _append_portfolio_settlement_record(
    session: Session,
    record: PortfolioSettlementRecord,
    ticket_settlement_ids: Sequence[str],
) -> PortfolioSettlementRecord:
    source_ids = _unique_ids(ticket_settlement_ids, "ticket settlement")
    existing = _existing_portfolio_settlement(session, record, source_ids)
    if existing is not None:
        return existing
    _validate_portfolio_settlement_lineage(session, record, source_ids)
    session.add(record)
    session.flush()
    session.add_all(
        PortfolioSettlementTicketRecord(
            portfolio_settlement_id=record.portfolio_settlement_id,
            settlement_no=settlement_no,
            settlement_id=settlement_id,
        )
        for settlement_no, settlement_id in enumerate(source_ids, start=1)
    )
    session.flush()
    _verify_portfolio_settlement(session, record, source_ids)
    return record


def _existing_backtest_run(
    session: Session,
    record: BacktestRunRecord,
) -> BacktestRunRecord | None:
    _verify_backtest_run_hashes(record)
    by_id = session.get(BacktestRunRecord, record.backtest_run_id)
    by_hash = session.scalar(
        select(BacktestRunRecord).where(BacktestRunRecord.run_hash == record.run_hash)
    )
    if (
        by_id is not None
        and by_hash is not None
        and by_id.backtest_run_id != by_hash.backtest_run_id
    ):
        raise ValueError("immutable backtest run conflicts with stored data")
    existing = by_id or by_hash
    if existing is not None:
        _assert_same_record(existing, record, "backtest run")
        _verify_backtest_run(session, existing)
    return existing


def _append_backtest_run_record(
    session: Session,
    record: BacktestRunRecord,
) -> BacktestRunRecord:
    existing = _existing_backtest_run(session, record)
    if existing is not None:
        return existing
    _verify_backtest_run(session, record)
    session.add(record)
    session.flush()
    return record


def _existing_backtest_slice(
    session: Session,
    record: BacktestSliceRecord,
) -> BacktestSliceRecord | None:
    _verify_backtest_slice_hashes(record)
    candidates = tuple(
        candidate
        for candidate in (
            session.get(BacktestSliceRecord, record.backtest_slice_id),
            session.scalar(
                select(BacktestSliceRecord).where(
                    BacktestSliceRecord.slice_hash == record.slice_hash
                )
            ),
            session.scalar(
                select(BacktestSliceRecord).where(
                    BacktestSliceRecord.backtest_run_id == record.backtest_run_id,
                    BacktestSliceRecord.slice_no == record.slice_no,
                )
            ),
        )
        if candidate is not None
    )
    if len({candidate.backtest_slice_id for candidate in candidates}) > 1:
        raise ValueError("immutable backtest slice conflicts with stored data")
    existing = candidates[0] if candidates else None
    if existing is not None:
        _assert_same_record(existing, record, "backtest slice")
        _verify_backtest_slice(session, existing)
    return existing


def _append_backtest_slice_record(
    session: Session,
    record: BacktestSliceRecord,
) -> BacktestSliceRecord:
    existing = _existing_backtest_slice(session, record)
    if existing is not None:
        return existing
    _verify_backtest_slice(session, record)
    session.add(record)
    session.flush()
    return record


def _existing_backtest_metric_snapshot(
    session: Session,
    record: BacktestMetricSnapshotRecord,
    portfolio_settlement_ids: Sequence[str],
    ticket_settlement_ids: Sequence[str],
) -> BacktestMetricSnapshotRecord | None:
    source_ids = _unique_ids(portfolio_settlement_ids, "portfolio settlement")
    ticket_ids = _unique_ids(ticket_settlement_ids, "ticket settlement")
    _verify_metric_hashes(record, source_ids, ticket_ids)
    candidates = tuple(
        candidate
        for candidate in (
            session.get(BacktestMetricSnapshotRecord, record.metric_snapshot_id),
            session.scalar(
                select(BacktestMetricSnapshotRecord).where(
                    BacktestMetricSnapshotRecord.snapshot_hash == record.snapshot_hash
                )
            ),
            session.scalar(
                select(BacktestMetricSnapshotRecord).where(
                    BacktestMetricSnapshotRecord.backtest_run_id
                    == record.backtest_run_id,
                    BacktestMetricSnapshotRecord.metric_scope == record.metric_scope,
                    BacktestMetricSnapshotRecord.metric_key == record.metric_key,
                    BacktestMetricSnapshotRecord.snapshot_no == record.snapshot_no,
                )
            ),
        )
        if candidate is not None
    )
    if len({candidate.metric_snapshot_id for candidate in candidates}) > 1:
        raise ValueError(
            "immutable backtest metric snapshot conflicts with stored data"
        )
    existing = candidates[0] if candidates else None
    if existing is not None:
        _assert_same_record(existing, record, "backtest metric snapshot")
        _verify_backtest_metric(session, existing, source_ids, ticket_ids)
    return existing


def _append_backtest_metric_snapshot_record(
    session: Session,
    record: BacktestMetricSnapshotRecord,
    portfolio_settlement_ids: Sequence[str],
    ticket_settlement_ids: Sequence[str],
) -> BacktestMetricSnapshotRecord:
    source_ids = _unique_ids(portfolio_settlement_ids, "portfolio settlement")
    ticket_ids = _unique_ids(ticket_settlement_ids, "ticket settlement")
    existing = _existing_backtest_metric_snapshot(
        session,
        record,
        source_ids,
        ticket_ids,
    )
    if existing is not None:
        return existing
    _verify_backtest_metric_lineage(session, record, source_ids, ticket_ids)
    session.add(record)
    session.flush()
    session.add_all(
        BacktestMetricSettlementRecord(
            metric_snapshot_id=record.metric_snapshot_id,
            portfolio_settlement_id=settlement_id,
        )
        for settlement_id in source_ids
    )
    session.add_all(
        BacktestMetricTicketSettlementRecord(
            metric_snapshot_id=record.metric_snapshot_id,
            settlement_id=settlement_id,
        )
        for settlement_id in ticket_ids
    )
    session.flush()
    _verify_backtest_metric(session, record, source_ids, ticket_ids)
    return record


def _ordered_ticket_settlements(
    settlements: Sequence[Settlement],
) -> tuple[Settlement, ...]:
    return _ordered_supersessions(
        settlements,
        "settlement_id",
        "supersedes_settlement_id",
        "ticket settlement",
    )


def _ordered_portfolio_settlements(
    settlements: Sequence[PortfolioSettlement],
) -> tuple[PortfolioSettlement, ...]:
    return _ordered_supersessions(
        settlements,
        "portfolio_settlement_id",
        "supersedes_portfolio_settlement_id",
        "portfolio settlement",
    )


def _ordered_supersessions(
    values: Sequence[object],
    id_field: str,
    previous_field: str,
    label: str,
) -> tuple:
    by_id = {getattr(value, id_field): value for value in values}
    if len(by_id) != len(values):
        raise ValueError(f"{label} IDs must be unique")
    ordered: list[object] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(value: object) -> None:
        value_id = getattr(value, id_field)
        if value_id in visited:
            return
        if value_id in visiting:
            raise ValueError(f"{label} supersession graph contains a cycle")
        visiting.add(value_id)
        previous_id = getattr(value, previous_field)
        if previous_id in by_id:
            visit(by_id[previous_id])
        visiting.remove(value_id)
        visited.add(value_id)
        ordered.append(value)

    for value in values:
        visit(value)
    return tuple(ordered)


def _preflight_walk_forward_graph(
    session: Session,
    result: WalkForwardBacktestResult,
    run_record: BacktestRunRecord,
    slice_records: Sequence[BacktestSliceRecord],
    metric_record: BacktestMetricSnapshotRecord,
    portfolio_settlement_ids: Sequence[str],
    ticket_settlement_ids: Sequence[str],
) -> None:
    _preflight_match_result_batches(session, result.match_result_batches)
    if len(slice_records) != len(result.slate_results):
        raise ValueError("walk-forward result has incomplete slice persistence data")
    if (
        result.backtest_run.created_at_utc
        < result.request.slates[-1].evaluation_as_of_at_utc
    ):
        raise ValueError("BacktestRun cannot be created before its final evaluation")
    if tuple(record.slice_no for record in slice_records) != tuple(
        range(1, len(slice_records) + 1)
    ):
        raise ValueError("walk-forward slice numbers must be contiguous from one")
    if len({record.backtest_slice_id for record in slice_records}) != len(
        slice_records
    ):
        raise ValueError("walk-forward slice IDs must be unique")

    ticket_settlements = result.ticket_settlements
    portfolio_settlements = result.portfolio_settlements
    _ordered_ticket_settlements(ticket_settlements)
    _ordered_portfolio_settlements(portfolio_settlements)
    if tuple(
        settlement.portfolio_settlement_id for settlement in portfolio_settlements
    ) != tuple(portfolio_settlement_ids):
        raise ValueError("walk-forward metric settlement lineage is incomplete")
    if tuple(item.settlement_id for item in ticket_settlements) != tuple(
        ticket_settlement_ids
    ):
        raise ValueError("walk-forward metric ticket lineage is incomplete")

    all_results = {
        item.match_result_id: item
        for batch in result.match_result_batches
        for item in batch.results
    }
    if len(all_results) != sum(
        len(batch.results) for batch in result.match_result_batches
    ):
        raise ValueError("walk-forward MatchResult IDs must be unique")

    for slate, slice_record in zip(
        result.slate_results,
        slice_records,
        strict=True,
    ):
        plan = slate.plan
        artifacts = slate.analysis_artifacts
        analysis_value = artifacts.analysis_run
        analysis = _completed_analysis_run(session, analysis_value.analysis_run_id)
        expected_analysis = {
            "run_kind": analysis_value.run_kind,
            "as_of_at_utc": analysis_value.as_of_at_utc,
            "status": analysis_value.status.value,
            "started_at_utc": analysis_value.started_at_utc,
            "completed_at_utc": analysis_value.completed_at_utc,
            "pipeline_version": analysis_value.pipeline_version,
            "code_revision": analysis_value.code_revision,
            "config_json": analysis_value.config_json,
            "config_hash": analysis_value.config_hash,
            "input_manifest_version": analysis_value.input_manifest_version,
            "input_manifest_json": analysis_value.input_manifest_json,
            "input_manifest_hash": analysis_value.input_manifest_hash,
            "replay_of_run_id": analysis_value.replay_of_run_id,
        }
        mismatched_analysis = [
            field
            for field, value in expected_analysis.items()
            if getattr(analysis, field) != value
        ]
        if mismatched_analysis:
            raise ValueError(
                "stored AnalysisRun conflicts with walk-forward artifacts: "
                + ", ".join(mismatched_analysis)
            )

        match_ids = {match.match_id for match in artifacts.matches}
        if (
            slate.match_result_batch.as_of_at_utc != plan.evaluation_as_of_at_utc
            or any(
                item.match_id not in match_ids
                for item in slate.match_result_batch.results
            )
            or any(
                item.internal_match_id not in match_ids
                for item in slate.match_result_batch.mappings
            )
        ):
            raise ValueError("walk-forward result batch crosses its slate lineage")
        if (
            slice_record.backtest_run_id != run_record.backtest_run_id
            or slice_record.parent_analysis_run_id != analysis.analysis_run_id
            or slice_record.decision_as_of_at_utc != plan.decision_as_of_at_utc
            or slice_record.evaluation_as_of_at_utc != plan.evaluation_as_of_at_utc
        ):
            raise ValueError("walk-forward BacktestSlice crosses its slate lineage")

        if len(artifacts.portfolios) != 1:
            raise ValueError("walk-forward slate requires one persisted portfolio")
        portfolio = artifacts.portfolios[0]
        stored_portfolio = session.get(PortfolioRecord, portfolio.portfolio_id)
        if (
            stored_portfolio is None
            or stored_portfolio.analysis_run_id != analysis.analysis_run_id
            or stored_portfolio.budget_fen != portfolio.budget_fen
            or stored_portfolio.total_stake_fen != portfolio.total_stake_fen
            or stored_portfolio.unused_budget_fen != portfolio.unused_budget_fen
            or stored_portfolio.status != portfolio.status.value
            or stored_portfolio.no_bet_reason
            != (
                portfolio.no_bet_reason.value
                if portfolio.no_bet_reason is not None
                else None
            )
        ):
            raise ValueError("stored portfolio conflicts with walk-forward artifacts")
        stored_tickets = {
            ticket.ticket_id: ticket
            for ticket in session.scalars(
                select(TicketRecord).where(
                    TicketRecord.portfolio_id == portfolio.portfolio_id
                )
            )
        }
        if set(stored_tickets) != {ticket.ticket_id for ticket in portfolio.tickets}:
            raise ValueError("stored portfolio ticket graph is incomplete")
        for ticket in portfolio.tickets:
            stored_ticket = stored_tickets[ticket.ticket_id]
            if (
                stored_ticket.ticket_no != ticket.ticket_no
                or stored_ticket.stake_fen != ticket.stake_fen
                or stored_ticket.potential_gross_payout_fen
                != ticket.potential_gross_payout_fen
                or stored_ticket.payout_policy_version
                != ticket.candidate.payout_policy_version
            ):
                raise ValueError("stored ticket conflicts with walk-forward artifacts")

        batch_result_ids = {
            item.match_result_id for item in slate.match_result_batch.results
        }
        for settlement in slate.ticket_settlements:
            if (
                settlement.parent_analysis_run_id != analysis.analysis_run_id
                or settlement.portfolio_id != portfolio.portfolio_id
                or settlement.settled_at_utc != plan.evaluation_as_of_at_utc
                or not set(settlement.match_result_ids) <= batch_result_ids
            ):
                raise ValueError("ticket settlement crosses its walk-forward slate")
            _existing_ticket_settlement(session, settlement)
        if slate.portfolio_settlement is not None:
            settlement = slate.portfolio_settlement
            if (
                settlement.parent_analysis_run_id != analysis.analysis_run_id
                or settlement.portfolio_id != portfolio.portfolio_id
                or settlement.settled_at_utc != plan.evaluation_as_of_at_utc
                or settlement.ticket_settlement_ids
                != tuple(item.settlement_id for item in slate.ticket_settlements)
            ):
                raise ValueError("portfolio settlement crosses its walk-forward slate")
            _existing_portfolio_settlement(
                session,
                _portfolio_settlement_record(settlement),
                settlement.ticket_settlement_ids,
            )

    _existing_backtest_run(session, run_record)
    for record in slice_records:
        existing_run = session.get(BacktestRunRecord, run_record.backtest_run_id)
        if existing_run is not None:
            _existing_backtest_slice(session, record)
        else:
            _verify_backtest_slice_hashes(record)
    existing_run = session.get(BacktestRunRecord, run_record.backtest_run_id)
    if existing_run is not None:
        _existing_backtest_metric_snapshot(
            session,
            metric_record,
            portfolio_settlement_ids,
            ticket_settlement_ids,
        )
    else:
        _verify_metric_hashes(
            metric_record,
            portfolio_settlement_ids,
            ticket_settlement_ids,
        )


def _preflight_walk_forward_v2_graph(
    session: Session,
    result: WalkForwardBacktestV2Result,
    run_record: BacktestV2RunRecord,
    archive_records: Sequence[BacktestV2RunArchiveRecord],
    slice_records: Sequence[BacktestV2SliceRecord],
    training_records: Sequence[BacktestV2TrainingSourceRecord],
    evaluation_records: Sequence[BacktestV2EvaluationRefRecord],
    result_records: Sequence[BacktestV2ResultSourceRecord],
    ticket_links: Sequence[BacktestV2SliceTicketSettlementRecord],
    metric_record: BacktestV2MetricSnapshotRecord,
) -> None:
    batches = tuple(
        slate.match_result_batch.to_match_result_batch()
        for slate in result.slate_results
    )
    _preflight_match_result_batches(session, batches)
    if len(slice_records) != len(result.slate_results):
        raise ValueError("BACKTEST_V2 persistence data has incomplete slices")
    if tuple(record.slice_no for record in slice_records) != tuple(
        range(1, len(slice_records) + 1)
    ):
        raise ValueError("BACKTEST_V2 slice numbers must be contiguous from one")
    if tuple(record.backtest_slice_id for record in slice_records) != (
        result.backtest_run.expected_slice_ids
    ):
        raise ValueError("BACKTEST_V2 persisted slice order is inconsistent")
    backtest_v2_run_value(run_record)
    for record in slice_records:
        backtest_v2_slice_value(record)
    backtest_v2_metrics_value(metric_record)

    for archive in result.backtest_run.archive_provenance:
        stored = session.get(HistoricalArchiveImportRecord, archive.archive_id)
        if stored is None or (
            stored.archive_schema_version != archive.archive_schema_version
            or stored.provider_code != archive.provider_code
            or stored.dataset_kind != archive.dataset_kind.value
            or stored.data_mode != result.backtest_run.data_mode.value
            or stored.payload_sha256 != archive.payload_sha256
        ):
            raise ValueError(
                f"BACKTEST_V2 archive import is missing or inconsistent: "
                f"{archive.archive_id}"
            )

    existing = session.get(BacktestV2RunRecord, run_record.backtest_run_id)
    hash_collision = session.scalar(
        select(BacktestV2RunRecord).where(
            BacktestV2RunRecord.run_hash == run_record.run_hash
        )
    )
    if (
        existing is not None
        and hash_collision is not None
        and existing.backtest_run_id != hash_collision.backtest_run_id
    ):
        raise ValueError("immutable BACKTEST_V2 run conflicts with stored data")
    if hash_collision is not None and (
        hash_collision.backtest_run_id != run_record.backtest_run_id
    ):
        raise ValueError("immutable BACKTEST_V2 run hash already exists")
    if existing is not None:
        _verify_backtest_v2_graph(session, existing)
        _assert_same_record(existing, run_record, "BACKTEST_V2 run")
        _assert_expected_records(session, archive_records, "BACKTEST_V2 archive")
        _assert_expected_records(session, slice_records, "BACKTEST_V2 slice")
        _assert_expected_records(session, training_records, "BACKTEST_V2 training")
        _assert_expected_records(
            session,
            evaluation_records,
            "BACKTEST_V2 evaluation",
        )
        _assert_expected_records(session, result_records, "BACKTEST_V2 result")
        _assert_expected_records(
            session,
            ticket_links,
            "BACKTEST_V2 ticket settlement",
        )
        _assert_expected_records(
            session,
            (metric_record,),
            "BACKTEST_V2 metrics",
        )
        return

    _reject_existing_records(session, archive_records, "BACKTEST_V2 archive")
    _reject_existing_records(session, slice_records, "BACKTEST_V2 slice")
    _reject_existing_records(session, training_records, "BACKTEST_V2 training")
    _reject_existing_records(session, evaluation_records, "BACKTEST_V2 evaluation")
    _reject_existing_records(session, result_records, "BACKTEST_V2 result")
    _reject_existing_records(
        session,
        ticket_links,
        "BACKTEST_V2 ticket settlement",
    )
    _reject_existing_records(session, (metric_record,), "BACKTEST_V2 metrics")
    if session.scalar(
        select(BacktestV2SliceRecord).where(
            BacktestV2SliceRecord.slice_hash.in_(
                tuple(record.slice_hash for record in slice_records)
            )
        )
    ) is not None:
        raise ValueError("immutable BACKTEST_V2 slice hash already exists")
    if session.scalar(
        select(BacktestV2MetricSnapshotRecord).where(
            BacktestV2MetricSnapshotRecord.snapshot_hash
            == metric_record.snapshot_hash
        )
    ) is not None:
        raise ValueError("immutable BACKTEST_V2 metric hash already exists")

    for slate, record in zip(
        result.slate_results,
        slice_records,
        strict=True,
    ):
        _verify_backtest_v2_analysis_dependencies(session, slate, record)


def _verify_backtest_v2_analysis_dependencies(
    session: Session,
    slate: WalkForwardBacktestV2SlateResult,
    record: BacktestV2SliceRecord,
) -> None:
    artifacts = slate.model_decision.analysis_artifacts
    run = artifacts.analysis_run
    stored_run = _completed_analysis_run(session, run.analysis_run_id)
    expected_run = {
        "as_of_at_utc": run.as_of_at_utc,
        "started_at_utc": run.started_at_utc,
        "completed_at_utc": run.completed_at_utc,
        "pipeline_version": run.pipeline_version,
        "code_revision": run.code_revision,
        "config_json": run.config_json,
        "config_hash": run.config_hash,
        "input_manifest_version": run.input_manifest_version,
        "input_manifest_json": run.input_manifest_json,
        "input_manifest_hash": run.input_manifest_hash,
    }
    if any(getattr(stored_run, field) != value for field, value in expected_run.items()):
        raise ValueError("stored AnalysisRun conflicts with BACKTEST_V2 decision")
    decision = slate.backtest_slice.decision_snapshot
    state = session.get(QuantModelStateRecord, decision.quant_model_state_id)
    if state is None or (
        state.analysis_run_id != run.analysis_run_id
        or state.model_name != decision.model_name
        or state.model_version != decision.model_version
        or state.calibration_label != decision.calibration_label
        or state.config_hash != decision.model_config_hash
        or state.state_hash != decision.state_hash
        or state.state_payload_hash != decision.state_payload_hash
        or state.training_data_hash != decision.training_data_hash
    ):
        raise ValueError("stored model state conflicts with BACKTEST_V2 decision")
    portfolio = artifacts.portfolios[0]
    stored_portfolio = session.get(PortfolioRecord, portfolio.portfolio_id)
    if stored_portfolio is None or (
        stored_portfolio.analysis_run_id != run.analysis_run_id
        or stored_portfolio.budget_fen != portfolio.budget_fen
        or stored_portfolio.total_stake_fen != portfolio.total_stake_fen
        or stored_portfolio.unused_budget_fen != portfolio.unused_budget_fen
        or stored_portfolio.status != portfolio.status.value
        or record.portfolio_id != portfolio.portfolio_id
    ):
        raise ValueError("stored portfolio conflicts with BACKTEST_V2 decision")


def _verify_backtest_v2_graph(
    session: Session,
    record: BacktestV2RunRecord,
) -> None:
    run = backtest_v2_run_value(record)
    archive_records = tuple(
        session.scalars(
            select(BacktestV2RunArchiveRecord)
            .where(
                BacktestV2RunArchiveRecord.backtest_run_id == run.backtest_run_id
            )
            .order_by(BacktestV2RunArchiveRecord.archive_no)
        )
    )
    expected_archives = backtest_v2_run_archive_records(run)
    if len(archive_records) != len(expected_archives):
        raise ValueError("stored BACKTEST_V2 archive lineage is incomplete")
    for stored, expected, archive in zip(
        archive_records,
        expected_archives,
        run.archive_provenance,
        strict=True,
    ):
        _assert_same_record(stored, expected, "BACKTEST_V2 archive")
        imported = session.get(HistoricalArchiveImportRecord, archive.archive_id)
        if imported is None or (
            imported.archive_schema_version != archive.archive_schema_version
            or imported.provider_code != archive.provider_code
            or imported.dataset_kind != archive.dataset_kind.value
            or imported.data_mode != run.data_mode.value
            or imported.payload_sha256 != archive.payload_sha256
        ):
            raise ValueError("stored BACKTEST_V2 archive source is inconsistent")

    slices = tuple(
        session.scalars(
            select(BacktestV2SliceRecord)
            .where(BacktestV2SliceRecord.backtest_run_id == run.backtest_run_id)
            .order_by(BacktestV2SliceRecord.slice_no)
        )
    )
    if (
        len(slices) != record.expected_slice_count
        or tuple(item.backtest_slice_id for item in slices) != run.expected_slice_ids
    ):
        raise ValueError("stored BACKTEST_V2 slice graph is incomplete")
    values: list[BacktestV2Slice] = []
    slates: list[BacktestSlateSnapshot] = []
    for slice_record in slices:
        values.append(_verify_backtest_v2_slice_graph(session, run, slice_record))
        slates.append(backtest_v2_slate_snapshot_value(slice_record))

    metrics_records = tuple(
        session.scalars(
            select(BacktestV2MetricSnapshotRecord).where(
                BacktestV2MetricSnapshotRecord.backtest_run_id
                == run.backtest_run_id
            )
        )
    )
    if len(metrics_records) != 1:
        raise ValueError("stored BACKTEST_V2 run requires one metric snapshot")
    metric_record = metrics_records[0]
    metrics = backtest_v2_metrics_value(metric_record)
    metric_config = BacktestMetricsConfig(
        log_loss_clip_version=metrics.log_loss_clip_version,
        log_loss_epsilon=metrics.log_loss_epsilon,
    )
    expected_metrics = calculate_backtest_v2_metrics(
        run,
        values,
        slates,
        metric_config,
    )
    if metrics != expected_metrics:
        raise ValueError("stored BACKTEST_V2 metrics are not reproducible")
    expected_lineage = _backtest_v2_metric_lineage(session, slices)
    lineage = _canonical_payload(
        metric_record.lineage_json,
        "BACKTEST_V2 metric lineage",
    )
    if lineage != _canonical_value(expected_lineage):
        raise ValueError("stored BACKTEST_V2 metric lineage is inconsistent")
    if (
        metric_record.as_of_at_utc != slices[-1].evaluation_as_of_at_utc
        or metric_record.calculated_at_utc < metric_record.as_of_at_utc
    ):
        raise ValueError("stored BACKTEST_V2 metric timeline is inconsistent")


def _verify_backtest_v2_slice_graph(
    session: Session,
    run: BacktestRun,
    record: BacktestV2SliceRecord,
) -> BacktestV2Slice:
    value = backtest_v2_slice_value(record)
    if value.backtest_run_id != run.backtest_run_id or value.data_mode != run.data_mode:
        raise ValueError("stored BACKTEST_V2 slice crosses its run")
    decision = value.decision_snapshot
    analysis = _completed_analysis_run(session, decision.analysis_run_id)
    state = session.get(QuantModelStateRecord, decision.quant_model_state_id)
    if state is None or (
        analysis.input_manifest_version != "MVP_INPUT_MANIFEST_V3"
        or analysis.input_manifest_hash != decision.decision_input_manifest_hash
        or analysis.as_of_at_utc != decision.decision_as_of_at_utc
        or state.analysis_run_id != analysis.analysis_run_id
        or state.model_name != decision.model_name
        or state.model_version != decision.model_version
        or state.calibration_label != decision.calibration_label
        or state.config_hash != decision.model_config_hash
        or state.state_hash != decision.state_hash
        or state.state_payload_hash != decision.state_payload_hash
        or state.training_data_hash != decision.training_data_hash
        or state.training_fact_count != len(decision.training_sources)
    ):
        raise ValueError("stored BACKTEST_V2 model-state lineage is inconsistent")
    portfolio = session.get(PortfolioRecord, record.portfolio_id)
    if portfolio is None or portfolio.analysis_run_id != analysis.analysis_run_id:
        raise ValueError("stored BACKTEST_V2 portfolio lineage is inconsistent")

    _verify_backtest_v2_training_records(session, record, decision)
    _verify_backtest_v2_evaluation_records(session, record, decision)
    _verify_backtest_v2_result_records(session, record, value)
    _verify_backtest_v2_financial_records(session, record, portfolio)
    return value


def _verify_backtest_v2_training_records(
    session: Session,
    record: BacktestV2SliceRecord,
    decision: BacktestV2DecisionSnapshot,
) -> None:
    stored = tuple(
        session.scalars(
            select(BacktestV2TrainingSourceRecord)
            .where(
                BacktestV2TrainingSourceRecord.backtest_slice_id
                == record.backtest_slice_id
            )
            .order_by(BacktestV2TrainingSourceRecord.training_sequence)
        )
    )
    if len(stored) != len(decision.training_sources):
        raise ValueError("stored BACKTEST_V2 training lineage is incomplete")
    for row, source in zip(stored, decision.training_sources, strict=True):
        fact = session.get(
            QuantModelTrainingFactRecord,
            (record.quant_model_state_id, source.sequence),
        )
        result = session.get(MatchResultRecord, source.match_result_id)
        archive = session.get(HistoricalArchiveImportRecord, source.archive_id)
        run_archive = session.get(
            BacktestV2RunArchiveRecord,
            (record.backtest_run_id, source.archive_id),
        )
        if fact is None or result is None or archive is None or run_archive is None:
            raise ValueError("stored BACKTEST_V2 training source is missing")
        if (
            row.training_sequence != source.sequence
            or row.match_result_id != source.match_result_id
            or row.archive_id != source.archive_id
            or row.source_payload_hash != source.source_payload_hash
            or row.fact_hash != source.fact_hash
            or row.archive_payload_sha256 != source.archive_payload_sha256
            or fact.match_result_id != source.match_result_id
            or fact.internal_match_id != source.match_id
            or fact.source_payload_hash != source.source_payload_hash
            or fact.fact_hash != source.fact_hash
            or result.internal_match_id != source.match_id
            or result.payload_hash != source.source_payload_hash
            or result.available_at_utc != source.available_at_utc
            or result.ingested_at_utc != source.ingested_at_utc
            or result.available_at_utc > record.decision_as_of_at_utc
            or result.ingested_at_utc > record.decision_as_of_at_utc
            or archive.archive_schema_version != source.archive_schema_version
            or archive.provider_code != source.archive_provider_code
            or archive.dataset_kind != "MATCH_RESULTS"
            or archive.payload_sha256 != source.archive_payload_sha256
            or run_archive.archive_payload_sha256 != source.archive_payload_sha256
            or source.match_id in decision.expected_match_ids
        ):
            raise ValueError("stored BACKTEST_V2 training source is inconsistent")


def _verify_backtest_v2_evaluation_records(
    session: Session,
    record: BacktestV2SliceRecord,
    decision: BacktestV2DecisionSnapshot,
) -> None:
    stored = tuple(
        session.scalars(
            select(BacktestV2EvaluationRefRecord)
            .where(
                BacktestV2EvaluationRefRecord.backtest_slice_id
                == record.backtest_slice_id
            )
            .order_by(BacktestV2EvaluationRefRecord.decision_no)
        )
    )
    if len(stored) != len(decision.evaluations):
        raise ValueError("stored BACKTEST_V2 evaluation lineage is incomplete")
    for decision_no, (row, expected) in enumerate(
        zip(stored, decision.evaluations, strict=True),
        start=1,
    ):
        evaluation = session.get(
            QuantModelEvaluationRecord,
            expected.quant_model_evaluation_id,
        )
        market = session.get(MarketProbabilityRecord, expected.market_prediction_id)
        if evaluation is None or market is None:
            raise ValueError("stored BACKTEST_V2 evaluation source is missing")
        if (
            row.decision_no != decision_no
            or row.internal_match_id != expected.match_id
            or row.quant_model_evaluation_id
            != expected.quant_model_evaluation_id
            or row.status != expected.status.value
            or row.output_hash != expected.output_hash
            or row.model_prediction_hash != expected.model_prediction_hash
            or row.market_prediction_id != expected.market_prediction_id
            or row.quant_prediction_id != expected.quant_prediction_id
            or row.final_prediction_id != expected.final_prediction_id
            or evaluation.analysis_run_id != record.analysis_run_id
            or evaluation.quant_model_state_id != record.quant_model_state_id
            or evaluation.internal_match_id != expected.match_id
            or evaluation.status != expected.status.value
            or evaluation.output_hash != expected.output_hash
            or evaluation.model_prediction_hash != expected.model_prediction_hash
            or market.analysis_run_id != record.analysis_run_id
            or market.internal_match_id != expected.match_id
            or _probability_outcomes(
                session,
                MarketProbabilityOutcomeRecord,
                "market_probability_id",
                expected.market_prediction_id,
            )
            != expected.p_market
        ):
            raise ValueError("stored BACKTEST_V2 evaluation source is inconsistent")
        _verify_backtest_v2_projection_records(session, record, expected)


def _verify_backtest_v2_projection_records(
    session: Session,
    record: BacktestV2SliceRecord,
    expected,
) -> None:
    if expected.quant_prediction_id is None:
        quant = session.scalar(
            select(QuantPredictionRecord).where(
                QuantPredictionRecord.quant_model_evaluation_id
                == expected.quant_model_evaluation_id
            )
        )
        final = session.scalar(
            select(FinalPredictionRecord).where(
                FinalPredictionRecord.analysis_run_id == record.analysis_run_id,
                FinalPredictionRecord.internal_match_id == expected.match_id,
            )
        )
        if quant is not None or final is not None:
            raise ValueError("unavailable BACKTEST_V2 evaluation has a projection")
        return
    quant = session.get(QuantPredictionRecord, expected.quant_prediction_id)
    final = session.get(FinalPredictionRecord, expected.final_prediction_id)
    if quant is None or final is None or (
        quant.analysis_run_id != record.analysis_run_id
        or quant.internal_match_id != expected.match_id
        or quant.quant_model_evaluation_id != expected.quant_model_evaluation_id
        or final.analysis_run_id != record.analysis_run_id
        or final.internal_match_id != expected.match_id
        or final.market_probability_id
        not in {None, expected.market_prediction_id}
        or final.quant_prediction_id != expected.quant_prediction_id
        or _probability_outcomes(
            session,
            QuantPredictionOutcomeRecord,
            "quant_prediction_id",
            expected.quant_prediction_id,
        )
        != expected.p_quant
        or _probability_outcomes(
            session,
            FinalPredictionOutcomeRecord,
            "final_prediction_id",
            expected.final_prediction_id,
        )
        != expected.p_final
    ):
        raise ValueError("stored BACKTEST_V2 projection lineage is inconsistent")


def _verify_backtest_v2_result_records(
    session: Session,
    record: BacktestV2SliceRecord,
    value: BacktestV2Slice,
) -> None:
    stored = tuple(
        session.scalars(
            select(BacktestV2ResultSourceRecord)
            .where(
                BacktestV2ResultSourceRecord.backtest_slice_id
                == record.backtest_slice_id
            )
            .order_by(BacktestV2ResultSourceRecord.result_no)
        )
    )
    if len(stored) != len(value.match_snapshots):
        raise ValueError("stored BACKTEST_V2 result lineage is incomplete")
    for result_no, (row, snapshot) in enumerate(
        zip(stored, value.match_snapshots, strict=True),
        start=1,
    ):
        source = session.get(MatchResultRecord, snapshot.match_result_id)
        archive = session.get(
            HistoricalArchiveImportRecord,
            snapshot.match_result_archive_id,
        )
        run_archive = session.get(
            BacktestV2RunArchiveRecord,
            (record.backtest_run_id, snapshot.match_result_archive_id),
        )
        if source is None or archive is None or run_archive is None:
            raise ValueError("stored BACKTEST_V2 result source is missing")
        if (
            row.result_no != result_no
            or row.internal_match_id != snapshot.match_id
            or row.match_result_id != snapshot.match_result_id
            or row.archive_id != snapshot.match_result_archive_id
            or row.source_payload_hash != snapshot.match_result_payload_hash
            or row.archive_payload_sha256
            != snapshot.match_result_archive_payload_sha256
            or source.internal_match_id != snapshot.match_id
            or source.payload_hash != snapshot.match_result_payload_hash
            or source.available_at_utc > record.evaluation_as_of_at_utc
            or source.ingested_at_utc > record.evaluation_as_of_at_utc
            or archive.archive_schema_version
            != snapshot.match_result_archive_schema_version
            or archive.provider_code != snapshot.match_result_archive_provider_code
            or archive.dataset_kind != "MATCH_RESULTS"
            or archive.payload_sha256
            != snapshot.match_result_archive_payload_sha256
            or run_archive.archive_payload_sha256
            != snapshot.match_result_archive_payload_sha256
            or _match_result(session, source).three_way_selection() != snapshot.outcome
        ):
            raise ValueError("stored BACKTEST_V2 result source is inconsistent")


def _verify_backtest_v2_financial_records(
    session: Session,
    record: BacktestV2SliceRecord,
    portfolio: PortfolioRecord,
) -> None:
    settlement_result = backtest_v2_settlement_result_value(record)
    slate = backtest_v2_slate_snapshot_value(record)
    links = tuple(
        session.scalars(
            select(BacktestV2SliceTicketSettlementRecord)
            .where(
                BacktestV2SliceTicketSettlementRecord.backtest_slice_id
                == record.backtest_slice_id
            )
            .order_by(BacktestV2SliceTicketSettlementRecord.settlement_no)
        )
    )
    expected_settlements = tuple(
        result.settlement
        for result in settlement_result.ticket_results
        if result.settlement is not None
    )
    if tuple(link.settlement_id for link in links) != tuple(
        settlement.settlement_id for settlement in expected_settlements
    ):
        raise ValueError("stored BACKTEST_V2 ticket settlement links are inconsistent")
    for settlement in expected_settlements:
        stored = session.get(TicketSettlementRecord, settlement.settlement_id)
        if stored is None or _ticket_settlement(session, stored) != settlement:
            raise ValueError("stored BACKTEST_V2 ticket settlement is inconsistent")
    expected_portfolio_settlement = settlement_result.portfolio_settlement
    if expected_portfolio_settlement is None:
        if record.portfolio_settlement_id is not None:
            raise ValueError("stored BACKTEST_V2 has an unexpected portfolio settlement")
    else:
        stored = session.get(
            PortfolioSettlementRecord,
            expected_portfolio_settlement.portfolio_settlement_id,
        )
        if stored is None:
            raise ValueError("stored BACKTEST_V2 portfolio settlement is missing")
        settlement_ids = tuple(
            session.scalars(
                select(PortfolioSettlementTicketRecord.settlement_id)
                .where(
                    PortfolioSettlementTicketRecord.portfolio_settlement_id
                    == stored.portfolio_settlement_id
                )
                .order_by(PortfolioSettlementTicketRecord.settlement_no)
            )
        )
        if (
            record.portfolio_settlement_id != stored.portfolio_settlement_id
            or _portfolio_settlement_value(stored, settlement_ids)
            != expected_portfolio_settlement
        ):
            raise ValueError("stored BACKTEST_V2 portfolio settlement is inconsistent")
    ticket_count = int(
        session.scalar(
            select(func.count())
            .select_from(TicketRecord)
            .where(TicketRecord.portfolio_id == portfolio.portfolio_id)
        )
        or 0
    )
    if (
        settlement_result.portfolio_id != portfolio.portfolio_id
        or slate.budget_fen != portfolio.budget_fen
        or slate.stake_fen != portfolio.total_stake_fen
        or slate.cash_fen != portfolio.unused_budget_fen
        or slate.ticket_count != ticket_count
        or slate.settled_ticket_count != len(expected_settlements)
    ):
        raise ValueError("stored BACKTEST_V2 financial snapshot is inconsistent")


def _backtest_v2_metric_lineage(
    session: Session,
    slices: Sequence[BacktestV2SliceRecord],
) -> dict[str, list[str]]:
    ticket_ids: list[str] = []
    for record in slices:
        ticket_ids.extend(
            session.scalars(
                select(BacktestV2SliceTicketSettlementRecord.settlement_id)
                .where(
                    BacktestV2SliceTicketSettlementRecord.backtest_slice_id
                    == record.backtest_slice_id
                )
                .order_by(BacktestV2SliceTicketSettlementRecord.settlement_no)
            )
        )
    return {
        "backtest_slice_ids": [record.backtest_slice_id for record in slices],
        "decision_snapshot_hashes": [
            record.decision_snapshot_hash for record in slices
        ],
        "portfolio_settlement_ids": [
            record.portfolio_settlement_id
            for record in slices
            if record.portfolio_settlement_id is not None
        ],
        "slate_snapshot_hashes": [record.slate_snapshot_hash for record in slices],
        "slice_hashes": [record.slice_hash for record in slices],
        "ticket_settlement_ids": ticket_ids,
    }


def _probability_outcomes(
    session: Session,
    model,
    identity_field: str,
    identity: str,
) -> ThreeWayProbability:
    field = getattr(model, identity_field)
    rows = tuple(
        session.execute(
            select(model.selection_key, model.probability)
            .where(field == identity)
            .order_by(model.selection_key)
        )
    )
    values = {SelectionKey(selection): probability for selection, probability in rows}
    if set(values) != set(SelectionKey):
        raise ValueError("stored probability distribution is incomplete")
    return ThreeWayProbability(
        home_win=values[SelectionKey.HOME_WIN],
        draw=values[SelectionKey.DRAW],
        away_win=values[SelectionKey.AWAY_WIN],
    )


def _record_identity(record: object):
    columns = tuple(getattr(record, "__table__").primary_key.columns)
    values = tuple(getattr(record, column.name) for column in columns)
    return values[0] if len(values) == 1 else values


def _assert_expected_records(
    session: Session,
    records: Sequence[object],
    label: str,
) -> None:
    for expected in records:
        stored = session.get(type(expected), _record_identity(expected))
        if stored is None:
            raise ValueError(f"stored {label} graph is incomplete")
        _assert_same_record(stored, expected, label)


def _reject_existing_records(
    session: Session,
    records: Sequence[object],
    label: str,
) -> None:
    for record in records:
        if session.get(type(record), _record_identity(record)) is not None:
            raise ValueError(f"immutable {label} record already exists")


def _match_result(session: Session, record: MatchResultRecord) -> MatchResult:
    provider = session.get(ProviderRecord, record.provider_id)
    if provider is None:
        raise ValueError("stored MatchResult is missing its provider")
    mapping = session.get(
        ProviderMatchMappingRecord,
        record.provider_mapping_id,
    )
    if (
        mapping is None
        or mapping.provider_id != record.provider_id
        or mapping.internal_match_id != record.internal_match_id
        or mapping.available_at_utc > record.available_at_utc
        or mapping.available_at_utc > record.ingested_at_utc
    ):
        raise ValueError("stored MatchResult provider mapping is invalid")
    result = MatchResult(
        match_result_id=record.match_result_id,
        match_id=record.internal_match_id,
        provider_code=provider.code,
        home_goals=record.home_goals,
        away_goals=record.away_goals,
        observed_at_utc=record.observed_at_utc,
        available_at_utc=record.available_at_utc,
        ingested_at_utc=record.ingested_at_utc,
        source_result_key=record.source_result_key,
        payload_hash=record.payload_hash,
        supersedes_match_result_id=record.supersedes_match_result_id,
    )
    _validate_match_result_payload(result)
    if record.supersedes_match_result_id is not None:
        previous = session.get(MatchResultRecord, record.supersedes_match_result_id)
        if (
            previous is None
            or previous.internal_match_id != record.internal_match_id
            or previous.provider_id != record.provider_id
            or previous.available_at_utc > record.available_at_utc
            or previous.ingested_at_utc >= record.ingested_at_utc
        ):
            raise ValueError("stored MatchResult supersession lineage is invalid")
    return result


def _validate_ticket_settlement_lineage(
    session: Session,
    settlement: Settlement,
) -> tuple[MatchResultRecord, ...]:
    _completed_analysis_run(session, settlement.parent_analysis_run_id)
    is_base = settlement.scope_kind.value == "ANALYSIS_RUN"
    if is_base:
        portfolio = session.get(PortfolioRecord, settlement.portfolio_id)
        ticket = session.get(TicketRecord, settlement.ticket_id)
        if (
            portfolio is None
            or ticket is None
            or portfolio.analysis_run_id != settlement.parent_analysis_run_id
            or ticket.portfolio_id != portfolio.portfolio_id
            or settlement.decision_scope_id != settlement.parent_analysis_run_id
            or ticket.stake_fen != settlement.stake_fen
            or ticket.payout_policy_version != settlement.payout_policy_version
            or (
                settlement.status.value == "WON"
                and ticket.potential_gross_payout_fen != settlement.gross_payout_fen
            )
        ):
            raise ValueError("base ticket settlement lineage is inconsistent")
        frozen_legs = tuple(
            (row.internal_match_id, SelectionKey(row.selection_key))
            for row in session.execute(
                select(
                    TicketLegRecord.internal_match_id,
                    BetCandidateRecord.selection_key,
                )
                .join(
                    BetCandidateRecord,
                    BetCandidateRecord.candidate_id == TicketLegRecord.candidate_id,
                )
                .where(TicketLegRecord.ticket_id == settlement.ticket_id)
                .order_by(TicketLegRecord.leg_no)
            )
        )
        potential_gross_payout_fen = ticket.potential_gross_payout_fen
    else:
        _, ticket_payload = _revision_portfolio_ticket(
            session,
            settlement.decision_scope_id,
            settlement.parent_analysis_run_id,
            settlement.portfolio_id,
            settlement.ticket_id,
        )
        candidate = ticket_payload.get("candidate")
        if not isinstance(candidate, dict):
            raise ValueError("revision ticket is missing its frozen candidate")
        legs = candidate.get("legs")
        if not isinstance(legs, list):
            raise ValueError("revision ticket is missing its frozen legs")
        try:
            frozen_legs = tuple(
                (leg["match_id"], SelectionKey(leg["selection"]))
                for leg in legs
                if isinstance(leg, dict)
            )
        except (KeyError, ValueError) as error:
            raise ValueError("revision ticket has invalid frozen legs") from error
        potential_gross_payout_fen = ticket_payload.get("potential_gross_payout_fen")
        if (
            ticket_payload.get("stake_fen") != settlement.stake_fen
            or candidate.get("payout_policy_version")
            != settlement.payout_policy_version
            or (
                settlement.status.value == "WON"
                and ticket_payload.get("potential_gross_payout_fen")
                != settlement.gross_payout_fen
            )
        ):
            raise ValueError("revision ticket settlement lineage is inconsistent")
    result_records = tuple(
        _required_match_result(session, result_id)
        for result_id in settlement.match_result_ids
    )
    expected_match_ids = tuple(match_id for match_id, _ in frozen_legs)
    results_by_match = {
        record.internal_match_id: _match_result(session, record)
        for record in result_records
    }
    if (
        len(frozen_legs) != 2
        or len(set(expected_match_ids)) != 2
        or set(results_by_match) != set(expected_match_ids)
    ):
        raise ValueError("ticket settlement results do not cover its frozen legs")
    won = all(
        results_by_match[match_id].three_way_selection() is selection
        for match_id, selection in frozen_legs
    )
    expected_status = SettlementStatus.WON if won else SettlementStatus.LOST
    expected_payout = potential_gross_payout_fen if won else 0
    if (
        type(expected_payout) is not int
        or settlement.status is not expected_status
        or settlement.gross_payout_fen != expected_payout
        or settlement.profit_loss_fen != expected_payout - settlement.stake_fen
    ):
        raise ValueError("ticket settlement outcome contradicts its frozen legs")
    for result in result_records:
        if result.ingested_at_utc > settlement.settled_at_utc:
            raise ValueError("ticket settlement uses a result after its cutoff")
        if (
            session.scalar(
                select(MatchResultRecord.match_result_id).where(
                    MatchResultRecord.supersedes_match_result_id
                    == result.match_result_id,
                    MatchResultRecord.available_at_utc <= settlement.settled_at_utc,
                    MatchResultRecord.ingested_at_utc <= settlement.settled_at_utc,
                )
            )
            is not None
        ):
            raise ValueError("ticket settlement does not use the latest visible result")
    if settlement.supersedes_settlement_id is None:
        existing_root = session.scalar(
            select(TicketSettlementRecord.settlement_id).where(
                TicketSettlementRecord.scope_kind == settlement.scope_kind.value,
                TicketSettlementRecord.parent_analysis_run_id
                == settlement.parent_analysis_run_id,
                TicketSettlementRecord.decision_scope_id
                == settlement.decision_scope_id,
                TicketSettlementRecord.portfolio_id == settlement.portfolio_id,
                TicketSettlementRecord.ticket_id == settlement.ticket_id,
                TicketSettlementRecord.supersedes_settlement_id.is_(None),
            )
        )
        if existing_root is not None and existing_root != settlement.settlement_id:
            raise ValueError("a ticket settlement stream can have only one root")
    if settlement.supersedes_settlement_id is not None:
        previous = session.get(
            TicketSettlementRecord,
            settlement.supersedes_settlement_id,
        )
        if (
            previous is None
            or previous.scope_kind != settlement.scope_kind.value
            or previous.parent_analysis_run_id != settlement.parent_analysis_run_id
            or previous.decision_scope_id != settlement.decision_scope_id
            or previous.portfolio_id != settlement.portfolio_id
            or previous.ticket_id != settlement.ticket_id
            or previous.settled_at_utc > settlement.settled_at_utc
        ):
            raise ValueError(
                "settlement correction must reference a not-later settlement "
                "for the same scope and ticket"
            )
        previous_result_ids = tuple(
            session.scalars(
                select(TicketSettlementMatchResultRecord.match_result_id)
                .where(
                    TicketSettlementMatchResultRecord.settlement_id
                    == previous.settlement_id
                )
                .order_by(TicketSettlementMatchResultRecord.leg_no)
            )
        )
        if len(previous_result_ids) != len(result_records):
            raise ValueError("settlement correction result lineage is incomplete")
        changed = False
        for result, previous_result_id in zip(
            result_records,
            previous_result_ids,
            strict=True,
        ):
            if result.match_result_id == previous_result_id:
                continue
            if result.supersedes_match_result_id != previous_result_id:
                raise ValueError(
                    "settlement correction results must be unchanged or direct "
                    "successors"
                )
            changed = True
        if not changed:
            raise ValueError(
                "settlement correction must change at least one MatchResult"
            )
    return result_records


def _ticket_settlement(
    session: Session,
    record: TicketSettlementRecord,
) -> Settlement:
    _require_hash(
        record.settlement_hash,
        _sha256(record.settlement_json),
        "stored ticket settlement",
    )
    payload = _canonical_payload(record.settlement_json, "ticket settlement")
    settlement = Settlement.model_validate(payload)
    result_rows = tuple(
        session.scalars(
            select(TicketSettlementMatchResultRecord)
            .where(
                TicketSettlementMatchResultRecord.settlement_id == record.settlement_id
            )
            .order_by(TicketSettlementMatchResultRecord.leg_no)
        )
    )
    result_ids = tuple(row.match_result_id for row in result_rows)
    is_base = settlement.scope_kind.value == "ANALYSIS_RUN"
    if (
        len(result_rows) != 2
        or result_ids != settlement.match_result_ids
        or settlement.settlement_id != record.settlement_id
        or settlement.settlement_kind != record.settlement_kind
        or settlement.scope_kind.value != record.scope_kind
        or settlement.parent_analysis_run_id != record.parent_analysis_run_id
        or settlement.decision_scope_id != record.decision_scope_id
        or settlement.portfolio_id != record.portfolio_id
        or settlement.ticket_id != record.ticket_id
        or settlement.status.value != record.status
        or settlement.stake_fen != record.stake_fen
        or settlement.gross_payout_fen != record.gross_payout_fen
        or settlement.profit_loss_fen != record.profit_loss_fen
        or settlement.payout_policy_version != record.payout_policy_version
        or settlement.settlement_policy_version != record.settlement_policy_version
        or settlement.settled_at_utc != record.settled_at_utc
        or settlement.supersedes_settlement_id != record.supersedes_settlement_id
        or record.portfolio_revision_id
        != (None if is_base else settlement.decision_scope_id)
        or record.base_portfolio_id != (settlement.portfolio_id if is_base else None)
        or record.base_ticket_id != (settlement.ticket_id if is_base else None)
    ):
        raise ValueError("stored ticket settlement columns are inconsistent")
    result_records = _validate_ticket_settlement_lineage(session, settlement)
    if any(
        row.internal_match_id != result.internal_match_id
        for row, result in zip(result_rows, result_records, strict=True)
    ):
        raise ValueError("stored ticket settlement result lineage is inconsistent")
    return settlement


def _validate_portfolio_settlement_lineage(
    session: Session,
    record: PortfolioSettlementRecord,
    ticket_settlement_ids: Sequence[str],
) -> None:
    _completed_analysis_run(session, record.parent_analysis_run_id)
    if record.scope_kind == "ANALYSIS_RUN":
        portfolio = session.get(PortfolioRecord, record.portfolio_id)
        if (
            portfolio is None
            or record.decision_scope_id != record.parent_analysis_run_id
            or record.base_portfolio_id != record.portfolio_id
            or record.portfolio_revision_id is not None
            or portfolio.analysis_run_id != record.parent_analysis_run_id
            or portfolio.budget_fen != record.budget_fen
            or portfolio.total_stake_fen != record.total_stake_fen
            or portfolio.unused_budget_fen != record.cash_fen
        ):
            raise ValueError("base portfolio settlement lineage is inconsistent")
    elif record.scope_kind == "PORTFOLIO_REVISION":
        portfolio_payload, _ = _revision_portfolio_ticket(
            session,
            record.decision_scope_id,
            record.parent_analysis_run_id,
            record.portfolio_id,
            None,
        )
        if (
            record.portfolio_revision_id != record.decision_scope_id
            or record.base_portfolio_id is not None
            or portfolio_payload.get("budget_fen") != record.budget_fen
            or portfolio_payload.get("total_stake_fen") != record.total_stake_fen
            or portfolio_payload.get("unused_budget_fen") != record.cash_fen
        ):
            raise ValueError("revision portfolio settlement lineage is inconsistent")
    else:
        raise ValueError(f"unsupported portfolio settlement scope: {record.scope_kind}")
    ticket_records = []
    seen_ticket_ids: set[str] = set()
    for settlement_id in ticket_settlement_ids:
        ticket_record = session.get(TicketSettlementRecord, settlement_id)
        if ticket_record is None:
            raise ValueError(
                "portfolio settlement references an unknown ticket settlement"
            )
        _ticket_settlement(session, ticket_record)
        if (
            ticket_record.scope_kind != record.scope_kind
            or ticket_record.parent_analysis_run_id != record.parent_analysis_run_id
            or ticket_record.decision_scope_id != record.decision_scope_id
            or ticket_record.portfolio_id != record.portfolio_id
            or ticket_record.settlement_policy_version
            != record.settlement_policy_version
            or ticket_record.settled_at_utc > record.settled_at_utc
            or ticket_record.ticket_id in seen_ticket_ids
        ):
            raise ValueError("portfolio ticket settlement lineage is inconsistent")
        if (
            session.scalar(
                select(TicketSettlementRecord.settlement_id).where(
                    TicketSettlementRecord.supersedes_settlement_id
                    == ticket_record.settlement_id,
                    TicketSettlementRecord.settled_at_utc <= record.settled_at_utc,
                )
            )
            is not None
        ):
            raise ValueError("portfolio settlement uses a superseded ticket settlement")
        seen_ticket_ids.add(ticket_record.ticket_id)
        ticket_records.append(ticket_record)
    if (
        record.ticket_count != len(ticket_records)
        or record.total_stake_fen != sum(item.stake_fen for item in ticket_records)
        or record.gross_payout_fen
        != sum(item.gross_payout_fen for item in ticket_records)
        or record.profit_loss_fen
        != sum(item.profit_loss_fen for item in ticket_records)
    ):
        raise ValueError("portfolio settlement aggregate is inconsistent")
    if record.supersedes_portfolio_settlement_id is None:
        existing_root = session.scalar(
            select(PortfolioSettlementRecord.portfolio_settlement_id).where(
                PortfolioSettlementRecord.scope_kind == record.scope_kind,
                PortfolioSettlementRecord.parent_analysis_run_id
                == record.parent_analysis_run_id,
                PortfolioSettlementRecord.decision_scope_id == record.decision_scope_id,
                PortfolioSettlementRecord.portfolio_id == record.portfolio_id,
                PortfolioSettlementRecord.supersedes_portfolio_settlement_id.is_(None),
            )
        )
        if (
            existing_root is not None
            and existing_root != record.portfolio_settlement_id
        ):
            raise ValueError("a portfolio settlement stream can have only one root")
    if record.supersedes_portfolio_settlement_id is not None:
        previous = session.get(
            PortfolioSettlementRecord,
            record.supersedes_portfolio_settlement_id,
        )
        if (
            previous is None
            or previous.scope_kind != record.scope_kind
            or previous.parent_analysis_run_id != record.parent_analysis_run_id
            or previous.decision_scope_id != record.decision_scope_id
            or previous.portfolio_id != record.portfolio_id
            or previous.settled_at_utc > record.settled_at_utc
        ):
            raise ValueError(
                "portfolio correction must reference a not-later aggregate"
            )
        previous_ticket_records = tuple(
            session.scalars(
                select(TicketSettlementRecord)
                .join(
                    PortfolioSettlementTicketRecord,
                    PortfolioSettlementTicketRecord.settlement_id
                    == TicketSettlementRecord.settlement_id,
                )
                .where(
                    PortfolioSettlementTicketRecord.portfolio_settlement_id
                    == previous.portfolio_settlement_id
                )
                .order_by(PortfolioSettlementTicketRecord.settlement_no)
            )
        )
        previous_by_ticket = {item.ticket_id: item for item in previous_ticket_records}
        current_by_ticket = {item.ticket_id: item for item in ticket_records}
        if len(previous_by_ticket) != len(previous_ticket_records) or set(
            previous_by_ticket
        ) != set(current_by_ticket):
            raise ValueError(
                "portfolio correction must preserve its logical ticket set"
            )
        changed = False
        for ticket_id, current in current_by_ticket.items():
            previous_ticket = previous_by_ticket[ticket_id]
            if current.settlement_id == previous_ticket.settlement_id:
                continue
            if current.supersedes_settlement_id != previous_ticket.settlement_id:
                raise ValueError(
                    "portfolio correction tickets must be unchanged or direct "
                    "successors"
                )
            changed = True
        if not changed:
            raise ValueError(
                "portfolio correction must change at least one ticket settlement"
            )


def _verify_portfolio_settlement(
    session: Session,
    record: PortfolioSettlementRecord,
    expected_ids: Sequence[str] | None = None,
) -> None:
    stored_ids = tuple(
        session.scalars(
            select(PortfolioSettlementTicketRecord.settlement_id)
            .where(
                PortfolioSettlementTicketRecord.portfolio_settlement_id
                == record.portfolio_settlement_id
            )
            .order_by(PortfolioSettlementTicketRecord.settlement_no)
        )
    )
    if expected_ids is not None and tuple(expected_ids) != stored_ids:
        raise ValueError("stored portfolio settlement references are inconsistent")
    _require_hash(
        record.settlement_hash,
        portfolio_settlement_hash(record, stored_ids),
        "stored portfolio settlement",
    )
    _validate_portfolio_settlement_lineage(session, record, stored_ids)


def _verify_historical_archive_import(
    record: HistoricalArchiveImportRecord,
) -> None:
    historical_archive_manifest(record)
    _sha256_value(record.payload_sha256, "archive payload hash")
    imported_at = _aware_utc(record.imported_at_utc, "archive import timestamp")
    if record.created_at_utc > imported_at:
        raise ValueError("stored archive import timeline is inconsistent")


def _verify_backtest_run_hashes(record: BacktestRunRecord) -> None:
    _validate_canonical_json_hash(
        record.strategy_config_json,
        record.strategy_config_hash,
        "BacktestRun strategy config",
    )
    _validate_canonical_json_hash(
        record.config_json,
        record.config_hash,
        "BacktestRun config",
    )
    _validate_canonical_json_hash(
        record.input_manifest_json,
        record.input_manifest_hash,
        "BacktestRun input manifest",
    )
    backtest_run_value(record)
    _require_hash(record.run_hash, backtest_run_hash(record), "BacktestRun")


def _verify_backtest_run(session: Session, record: BacktestRunRecord) -> None:
    _verify_backtest_run_hashes(record)
    run_value = backtest_run_value(record)
    if record.backtest_mode != "STRICT_POINT_IN_TIME":
        raise ValueError("only strict point-in-time backtests are supported")
    if not (record.started_at_utc <= record.completed_at_utc <= record.created_at_utc):
        raise ValueError("BacktestRun timeline is inconsistent")
    for provenance in run_value.archive_provenance:
        archive = session.get(HistoricalArchiveImportRecord, provenance.archive_id)
        if archive is None:
            raise ValueError("BacktestRun references an unregistered archive import")
        _verify_historical_archive_import(archive)
        if (
            archive.archive_schema_version != provenance.archive_schema_version
            or archive.provider_code != provenance.provider_code
            or archive.dataset_kind != provenance.dataset_kind.value
            or archive.payload_sha256 != provenance.payload_sha256
            or archive.data_mode != record.data_mode
        ):
            raise ValueError("BacktestRun archive provenance conflicts with its import")
    if record.replay_of_backtest_run_id is not None:
        previous = session.get(BacktestRunRecord, record.replay_of_backtest_run_id)
        if (
            previous is None
            or previous.backtest_run_id == record.backtest_run_id
            or previous.status != "COMPLETED"
            or previous.completed_at_utc > record.started_at_utc
            or previous.backtest_version != record.backtest_version
            or previous.data_mode != record.data_mode
            or previous.date_from != record.date_from
            or previous.date_to != record.date_to
            or previous.strategy_version != record.strategy_version
            or previous.strategy_config_hash != record.strategy_config_hash
            or previous.code_revision != record.code_revision
            or previous.schema_version != record.schema_version
            or previous.engine_version != record.engine_version
            or previous.config_hash != record.config_hash
            or previous.input_manifest_version != record.input_manifest_version
            or previous.input_manifest_hash != record.input_manifest_hash
        ):
            raise ValueError("BacktestRun replay lineage is inconsistent")
        _verify_backtest_run_hashes(previous)


def _verify_backtest_slice_hashes(record: BacktestSliceRecord) -> None:
    _validate_canonical_json_hash(
        record.slice_manifest_json,
        record.slice_manifest_hash,
        "BacktestSlice manifest",
    )
    backtest_slice_value(record)
    _require_hash(record.slice_hash, backtest_slice_hash(record), "BacktestSlice")


def _verify_backtest_slice(session: Session, record: BacktestSliceRecord) -> None:
    _verify_backtest_slice_hashes(record)
    value = backtest_slice_value(record)
    run = session.get(BacktestRunRecord, record.backtest_run_id)
    if run is None:
        raise ValueError("BacktestSlice references an unknown BacktestRun")
    _verify_backtest_run(session, run)
    run_value = backtest_run_value(run)
    analysis = _completed_analysis_run(session, record.parent_analysis_run_id)
    if (
        record.slice_no <= 0
        or record.decision_as_of_at_utc >= record.evaluation_as_of_at_utc
        or analysis.as_of_at_utc != record.decision_as_of_at_utc
        or run.data_mode != record.data_mode
        or record.decision_as_of_at_utc > value.kickoff_from_utc
        or value.kickoff_from_utc > value.kickoff_to_utc
        or value.kickoff_to_utc >= record.evaluation_as_of_at_utc
        or value.kickoff_from_utc.date() < run.date_from
        or value.kickoff_to_utc.date() > run.date_to
    ):
        raise ValueError("BacktestSlice cutoff or timeline is inconsistent")
    strict_lineage = bool(run_value.expected_slice_ids)
    if strict_lineage:
        try:
            expected_slice_no = (
                run_value.expected_slice_ids.index(record.backtest_slice_id) + 1
            )
        except ValueError as error:
            raise ValueError(
                "BacktestSlice ID is absent from its BacktestRun manifest"
            ) from error
        if record.slice_no != expected_slice_no:
            raise ValueError("BacktestSlice number conflicts with its run manifest")
    if value.decision_input_manifest_hash != analysis.input_manifest_hash:
        raise ValueError(
            "BacktestSlice decision manifest conflicts with its AnalysisRun"
        )
    decision_match_ids = _analysis_manifest_match_ids(analysis)
    stored_decision_match_ids = tuple(
        session.scalars(
            select(AnalysisRunMatchRecord.internal_match_id).where(
                AnalysisRunMatchRecord.analysis_run_id == analysis.analysis_run_id
            )
        )
    )
    if len(stored_decision_match_ids) != len(set(stored_decision_match_ids)) or set(
        stored_decision_match_ids
    ) != set(decision_match_ids):
        raise ValueError("AnalysisRun decision match lineage is inconsistent")
    missing_match_ids = set(value.missing_decision_match_ids)
    if set(value.missing_decision_match_ids) & set(stored_decision_match_ids):
        raise ValueError("BacktestSlice marks an AnalysisRun match as missing")
    if (
        tuple(
            match_id
            for match_id in value.expected_match_ids
            if match_id not in missing_match_ids
        )
        != decision_match_ids
    ):
        raise ValueError(
            "BacktestSlice expected match sequence conflicts with its AnalysisRun"
        )
    for match_id in decision_match_ids:
        match = session.get(MatchRecord, match_id)
        if match is None or not (
            value.kickoff_from_utc <= match.kickoff_at_utc <= value.kickoff_to_utc
        ):
            raise ValueError("BacktestSlice kickoff lineage is inconsistent")
    if len(value.match_result_ids) != value.settled_match_count:
        raise ValueError("BacktestSlice result IDs do not cover its settled matches")
    result_match_ids: set[str] = set()
    for result_id in value.match_result_ids:
        result = _required_match_result(session, result_id)
        if result.internal_match_id in result_match_ids:
            raise ValueError(
                "BacktestSlice contains multiple MatchResult versions for one match"
            )
        if (
            result.available_at_utc > record.evaluation_as_of_at_utc
            or result.ingested_at_utc > record.evaluation_as_of_at_utc
            or result.internal_match_id not in decision_match_ids
        ):
            raise ValueError(
                "BacktestSlice MatchResult lineage crosses its evaluation cutoff"
            )
        if (
            session.scalar(
                select(MatchResultRecord.match_result_id).where(
                    MatchResultRecord.supersedes_match_result_id
                    == result.match_result_id,
                    MatchResultRecord.available_at_utc
                    <= record.evaluation_as_of_at_utc,
                    MatchResultRecord.ingested_at_utc <= record.evaluation_as_of_at_utc,
                )
            )
            is not None
        ):
            raise ValueError("BacktestSlice does not use the latest visible result")
        result_match_ids.add(result.internal_match_id)
    for issue in value.match_result_issues:
        if (
            issue.match_id in result_match_ids
            or issue.match_id not in decision_match_ids
            or session.scalar(
                select(ProviderMatchMappingRecord.mapping_id).where(
                    ProviderMatchMappingRecord.internal_match_id == issue.match_id,
                    ProviderMatchMappingRecord.available_at_utc
                    <= record.evaluation_as_of_at_utc,
                )
            )
            is None
        ):
            raise ValueError(
                "BacktestSlice result issue crosses its evaluation lineage"
            )
    if record.scope_kind == "ANALYSIS_RUN":
        if (
            record.decision_scope_id != record.parent_analysis_run_id
            or record.portfolio_revision_id is not None
        ):
            raise ValueError("base BacktestSlice scope is inconsistent")
    elif record.scope_kind == "PORTFOLIO_REVISION":
        revision = session.get(
            PortfolioRevisionRecord,
            record.portfolio_revision_id,
        )
        if (
            revision is None
            or record.portfolio_revision_id != record.decision_scope_id
            or revision.parent_analysis_run_id != record.parent_analysis_run_id
        ):
            raise ValueError("revision BacktestSlice scope is inconsistent")
        _validated_revision_payload(revision)
    else:
        raise ValueError(f"unsupported BacktestSlice scope: {record.scope_kind}")


def _verify_metric_hashes(
    record: BacktestMetricSnapshotRecord,
    portfolio_settlement_ids: Sequence[str],
    ticket_settlement_ids: Sequence[str],
) -> None:
    _validate_canonical_json_hash(
        record.metrics_json,
        record.metrics_hash,
        "backtest metrics",
    )
    backtest_metrics_value(record)
    lineage = _validate_canonical_json_hash(
        record.lineage_json,
        record.lineage_hash,
        "backtest metric lineage",
    )
    if not isinstance(lineage, dict):
        raise ValueError("backtest metric lineage must be an object")
    slice_lineage = _metric_slice_lineage(record)
    slice_ids = [slice_id for slice_id, _ in slice_lineage]
    stored_slice_ids = lineage.get("backtest_slice_ids", [])
    stored_ticket_ids = lineage.get("ticket_settlement_ids", [])
    if (
        lineage.get("portfolio_settlement_ids") != sorted(portfolio_settlement_ids)
        or stored_ticket_ids != sorted(ticket_settlement_ids)
        or stored_slice_ids != slice_ids
    ):
        raise ValueError(
            "backtest metric lineage JSON does not match settlement references"
        )
    _require_hash(
        record.snapshot_hash,
        backtest_metric_snapshot_hash(
            record,
            portfolio_settlement_ids,
            ticket_settlement_ids,
        ),
        "backtest metric snapshot",
    )


def _verify_backtest_metric(
    session: Session,
    record: BacktestMetricSnapshotRecord,
    expected_portfolio_ids: Sequence[str] | None = None,
    expected_ticket_ids: Sequence[str] | None = None,
) -> None:
    stored_portfolio_ids = tuple(
        session.scalars(
            select(BacktestMetricSettlementRecord.portfolio_settlement_id)
            .where(
                BacktestMetricSettlementRecord.metric_snapshot_id
                == record.metric_snapshot_id
            )
            .order_by(BacktestMetricSettlementRecord.portfolio_settlement_id)
        )
    )
    stored_ticket_ids = tuple(
        session.scalars(
            select(BacktestMetricTicketSettlementRecord.settlement_id)
            .where(
                BacktestMetricTicketSettlementRecord.metric_snapshot_id
                == record.metric_snapshot_id
            )
            .order_by(BacktestMetricTicketSettlementRecord.settlement_id)
        )
    )
    if (
        expected_portfolio_ids is not None
        and tuple(sorted(expected_portfolio_ids)) != stored_portfolio_ids
    ):
        raise ValueError("stored metric settlement references are inconsistent")
    if (
        expected_ticket_ids is not None
        and tuple(sorted(expected_ticket_ids)) != stored_ticket_ids
    ):
        raise ValueError("stored metric ticket references are inconsistent")
    _verify_metric_hashes(record, stored_portfolio_ids, stored_ticket_ids)
    _verify_backtest_metric_lineage(
        session,
        record,
        stored_portfolio_ids,
        stored_ticket_ids,
    )


def _verify_backtest_metric_lineage(
    session: Session,
    record: BacktestMetricSnapshotRecord,
    portfolio_settlement_ids: Sequence[str],
    ticket_settlement_ids: Sequence[str],
) -> None:
    run = session.get(BacktestRunRecord, record.backtest_run_id)
    if run is None:
        raise ValueError("metric snapshot references an unknown BacktestRun")
    _verify_backtest_run(session, run)
    if not (record.snapshot_no > 0 and record.as_of_at_utc <= record.calculated_at_utc):
        raise ValueError("backtest metric timeline is inconsistent")
    metrics = backtest_metrics_value(record)
    if (
        metrics.backtest_version != run.backtest_version
        or metrics.data_mode.value != run.data_mode
    ):
        raise ValueError("backtest metric run metadata is inconsistent")
    slice_record: BacktestSliceRecord | None = None
    if record.metric_scope == "SLICE":
        if record.backtest_slice_id is None:
            raise ValueError("slice metric requires a BacktestSlice")
        slice_record = session.get(BacktestSliceRecord, record.backtest_slice_id)
        if (
            slice_record is None
            or slice_record.backtest_run_id != record.backtest_run_id
            or slice_record.evaluation_as_of_at_utc != record.as_of_at_utc
        ):
            raise ValueError("slice metric lineage is inconsistent")
        _verify_backtest_slice(session, slice_record)
    elif record.metric_scope != "RUN" or record.backtest_slice_id is not None:
        raise ValueError("run metric scope is inconsistent")
    slice_lineage = _metric_slice_lineage(record)
    run_value = backtest_run_value(run)
    if slice_record is not None:
        relevant_slices = (slice_record,)
    else:
        relevant_slices = tuple(
            session.scalars(
                select(BacktestSliceRecord)
                .where(
                    BacktestSliceRecord.backtest_run_id == record.backtest_run_id,
                    BacktestSliceRecord.evaluation_as_of_at_utc <= record.as_of_at_utc,
                )
                .order_by(
                    BacktestSliceRecord.slice_no,
                    BacktestSliceRecord.backtest_slice_id,
                )
            )
        )
    expected_slice_ids = tuple(item.backtest_slice_id for item in relevant_slices)
    if run_value.expected_slice_ids:
        required_slice_ids = (
            (record.backtest_slice_id,)
            if slice_record is not None
            else run_value.expected_slice_ids
        )
        if expected_slice_ids != required_slice_ids:
            raise ValueError("backtest metric does not cover its exact run slices")
    if slice_lineage:
        if tuple(item[0] for item in slice_lineage) != expected_slice_ids:
            raise ValueError("backtest metric slice lineage is incomplete")
        for stored_slice, (slice_id, result_ids) in zip(
            relevant_slices,
            slice_lineage,
            strict=True,
        ):
            _verify_backtest_slice(session, stored_slice)
            if (
                slice_id != stored_slice.backtest_slice_id
                or backtest_slice_value(stored_slice).match_result_ids != result_ids
            ):
                raise ValueError("backtest metric slice result lineage is inconsistent")
    elif run_value.expected_slice_ids:
        raise ValueError("strict backtest metric lineage requires every slice")

    for settlement_id in portfolio_settlement_ids:
        settlement = session.get(PortfolioSettlementRecord, settlement_id)
        if settlement is None:
            raise ValueError("metric references an unknown portfolio settlement")
        _verify_portfolio_settlement(session, settlement)
        if settlement.settled_at_utc > record.as_of_at_utc or not any(
            item.parent_analysis_run_id == settlement.parent_analysis_run_id
            and item.decision_scope_id == settlement.decision_scope_id
            and settlement.settled_at_utc <= item.evaluation_as_of_at_utc
            for item in relevant_slices
        ):
            raise ValueError("metric settlement lineage crosses its cutoff")
    for settlement_id in ticket_settlement_ids:
        settlement = session.get(TicketSettlementRecord, settlement_id)
        if settlement is None:
            raise ValueError("metric references an unknown ticket settlement")
        _ticket_settlement(session, settlement)
        if settlement.settled_at_utc > record.as_of_at_utc or not any(
            item.parent_analysis_run_id == settlement.parent_analysis_run_id
            and item.decision_scope_id == settlement.decision_scope_id
            and settlement.settled_at_utc <= item.evaluation_as_of_at_utc
            for item in relevant_slices
        ):
            raise ValueError("metric ticket lineage crosses its cutoff")

    if run_value.expected_slice_ids:
        expected_ticket_ids: set[str] = set()
        expected_portfolio_ids: set[str] = set()
        for item in relevant_slices:
            expected_ticket_ids.update(
                session.scalars(
                    select(TicketSettlementRecord.settlement_id).where(
                        TicketSettlementRecord.parent_analysis_run_id
                        == item.parent_analysis_run_id,
                        TicketSettlementRecord.decision_scope_id
                        == item.decision_scope_id,
                        TicketSettlementRecord.settled_at_utc
                        <= item.evaluation_as_of_at_utc,
                    )
                )
            )
            expected_portfolio_ids.update(
                session.scalars(
                    select(PortfolioSettlementRecord.portfolio_settlement_id).where(
                        PortfolioSettlementRecord.parent_analysis_run_id
                        == item.parent_analysis_run_id,
                        PortfolioSettlementRecord.decision_scope_id
                        == item.decision_scope_id,
                        PortfolioSettlementRecord.settled_at_utc
                        <= item.evaluation_as_of_at_utc,
                    )
                )
            )
        if set(ticket_settlement_ids) != expected_ticket_ids:
            raise ValueError("backtest metric omits ticket settlement lineage")
        if set(portfolio_settlement_ids) != expected_portfolio_ids:
            raise ValueError("backtest metric omits portfolio settlement lineage")


def _base_portfolio(session: Session, record: PortfolioRecord) -> Portfolio:
    run = _completed_analysis_run(session, record.analysis_run_id)
    constraints, rules, budgets = _base_run_configuration(run)
    stored_constraints = _stored_portfolio_constraints(record.strategy_config_json)
    if stored_constraints != constraints:
        raise ValueError(
            "stored Portfolio constraints conflict with its AnalysisRun configuration"
        )
    if record.budget_fen not in budgets:
        raise ValueError(
            "stored Portfolio budget is absent from its AnalysisRun request"
        )
    if record.portfolio_id != stable_id(
        "portfolio", record.analysis_run_id, record.budget_fen
    ):
        raise ValueError("stored Portfolio ID is inconsistent")
    if not record.strategy_version.strip():
        raise ValueError("stored Portfolio strategy version is empty")

    cash_rows = tuple(
        session.scalars(
            select(PortfolioCashPositionRecord).where(
                PortfolioCashPositionRecord.portfolio_id == record.portfolio_id
            )
        )
    )
    if len(cash_rows) != 1:
        raise ValueError("stored Portfolio requires exactly one cash position")
    cash_record = cash_rows[0]
    if (
        cash_record.cash_position_id != stable_id("cash", record.portfolio_id)
        or cash_record.amount_fen != record.unused_budget_fen
        or cash_record.expected_profit_fen != 0
    ):
        raise ValueError("stored Portfolio cash position is inconsistent")
    cash = CashPosition(
        position_id=cash_record.cash_position_id,
        amount_fen=cash_record.amount_fen,
        expected_profit_fen=cash_record.expected_profit_fen,
    )

    ticket_records = tuple(
        session.scalars(
            select(TicketRecord)
            .where(TicketRecord.portfolio_id == record.portfolio_id)
            .order_by(TicketRecord.ticket_no, TicketRecord.ticket_id)
        )
    )
    if tuple(ticket.ticket_no for ticket in ticket_records) != tuple(
        range(1, len(ticket_records) + 1)
    ):
        raise ValueError("stored Portfolio ticket numbers are not contiguous")
    tickets = tuple(
        _base_ticket(session, ticket_record, record, rules)
        for ticket_record in ticket_records
    )
    try:
        status = PortfolioStatus(record.status)
        no_bet_reason = (
            NoBetReason(record.no_bet_reason)
            if record.no_bet_reason is not None
            else None
        )
    except ValueError as error:
        raise ValueError("stored Portfolio status is invalid") from error

    portfolio = Portfolio(
        portfolio_id=record.portfolio_id,
        analysis_run_id=record.analysis_run_id,
        budget_fen=record.budget_fen,
        tickets=tickets,
        total_stake_fen=record.total_stake_fen,
        unused_budget_fen=record.unused_budget_fen,
        cash_position=cash,
        status=status,
        no_bet_reason=no_bet_reason,
        constraints=constraints,
        strategy_version=record.strategy_version,
    )
    if (
        portfolio.total_stake_fen != sum(ticket.stake_fen for ticket in tickets)
        or portfolio.total_stake_fen + portfolio.unused_budget_fen
        != portfolio.budget_fen
    ):
        raise ValueError("stored Portfolio capital balance is inconsistent")
    return portfolio


def _base_run_configuration(
    run: AnalysisRunRecord,
) -> tuple[PortfolioConstraints, SportteryRules, tuple[int, ...]]:
    payload = _validate_canonical_json_hash(
        run.config_json,
        run.config_hash,
        "AnalysisRun config",
    )
    _validate_canonical_json_hash(
        run.input_manifest_json,
        run.input_manifest_hash,
        "AnalysisRun input manifest",
    )
    if not isinstance(payload, dict):
        raise ValueError("AnalysisRun config must be a JSON object")
    settings = payload.get("settings")
    request = payload.get("request")
    if not isinstance(settings, dict) or not isinstance(request, dict):
        raise ValueError("AnalysisRun config is missing settings or request data")
    portfolio_payload = settings.get("portfolio")
    sporttery_payload = settings.get("sporttery")
    budgets_payload = request.get("budgets_fen")
    if (
        not isinstance(portfolio_payload, dict)
        or not isinstance(sporttery_payload, dict)
        or not isinstance(budgets_payload, list)
        or not budgets_payload
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in budgets_payload
        )
        or any(value < 0 for value in budgets_payload)
        or len(set(budgets_payload)) != len(budgets_payload)
    ):
        raise ValueError("AnalysisRun config has invalid Portfolio inputs")
    try:
        constraints = PortfolioConstraints.model_validate(portfolio_payload)
        rules = SportteryRules(
            version=sporttery_payload["rules_version"],
            base_stake_fen=sporttery_payload["base_stake_fen"],
            max_multiplier=sporttery_payload["max_multiplier"],
            max_ticket_stake_fen=sporttery_payload["max_ticket_stake_fen"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "AnalysisRun config has invalid frozen betting rules"
        ) from error
    return constraints, rules, tuple(budgets_payload)


def _stored_portfolio_constraints(value: str) -> PortfolioConstraints:
    payload = _canonical_payload(value, "Portfolio strategy config")
    if not isinstance(payload, dict):
        raise ValueError("Portfolio strategy config must be a JSON object")
    constraints = PortfolioConstraints.model_validate(payload)
    expected_json = _canonical_json(constraints.model_dump(mode="json"))
    if expected_json != value:
        raise ValueError(
            "Portfolio strategy config does not exactly encode its constraints"
        )
    return constraints


def _base_ticket(
    session: Session,
    record: TicketRecord,
    portfolio: PortfolioRecord,
    rules: SportteryRules,
) -> TicketAllocation:
    if record.portfolio_id != portfolio.portfolio_id:
        raise ValueError("stored Ticket references another Portfolio")
    candidate_record = session.get(
        TicketCandidateRecord,
        record.ticket_candidate_id,
    )
    if candidate_record is None:
        raise ValueError("stored Ticket is missing its TicketCandidate")
    candidate = _base_ticket_candidate(
        session,
        candidate_record,
        portfolio.analysis_run_id,
        rules,
    )
    ticket_leg_rows = tuple(
        session.scalars(
            select(TicketLegRecord)
            .where(TicketLegRecord.ticket_id == record.ticket_id)
            .order_by(TicketLegRecord.leg_no)
        )
    )
    expected_leg_bindings = tuple(
        (leg_no, leg.candidate_id, leg.match_id)
        for leg_no, leg in enumerate(candidate.legs, start=1)
    )
    actual_leg_bindings = tuple(
        (row.leg_no, row.candidate_id, row.internal_match_id) for row in ticket_leg_rows
    )
    if actual_leg_bindings != expected_leg_bindings:
        raise ValueError("stored Ticket legs conflict with its TicketCandidate")

    expected_stake = calculate_stake_fen(
        candidate.atomic_bet_count,
        record.multiplier,
        rules,
    )
    expected_values = {
        "ticket_candidate_id": candidate.ticket_candidate_id,
        "pass_type": candidate.pass_type.value,
        "role": None,
        "atomic_bet_count": candidate.atomic_bet_count,
        "base_stake_fen": candidate.base_stake_fen,
        "stake_fen": expected_stake,
        "potential_gross_payout_fen": candidate.gross_payout_fen * record.multiplier,
        "expected_gross_payout_fen": candidate.expected_gross_payout_fen
        * Decimal(record.multiplier),
        "expected_profit_fen": candidate.expected_profit_fen
        * Decimal(record.multiplier),
        "expected_roi": candidate.expected_roi,
        "probability_any_payout": candidate.joint_probability,
        "payout_policy_version": candidate.payout_policy_version,
    }
    mismatched = [
        field
        for field, expected in expected_values.items()
        if getattr(record, field) != expected
    ]
    if mismatched:
        raise ValueError(
            "stored Ticket redundant fields are inconsistent: " + ", ".join(mismatched)
        )
    if record.ticket_id != stable_id(
        "ticket", portfolio.portfolio_id, candidate.ticket_candidate_id
    ):
        raise ValueError("stored Ticket ID is inconsistent")
    return TicketAllocation(
        ticket_id=record.ticket_id,
        ticket_no=record.ticket_no,
        candidate=candidate,
        multiplier=record.multiplier,
        stake_fen=record.stake_fen,
        potential_gross_payout_fen=record.potential_gross_payout_fen,
        expected_gross_payout_fen=record.expected_gross_payout_fen,
        expected_profit_fen=record.expected_profit_fen,
        expected_roi=record.expected_roi,
        probability_any_payout=record.probability_any_payout,
    )


def _base_ticket_candidate(
    session: Session,
    record: TicketCandidateRecord,
    analysis_run_id: str,
    rules: SportteryRules,
) -> TicketCandidate:
    if record.analysis_run_id != analysis_run_id:
        raise ValueError("stored TicketCandidate crosses its AnalysisRun")
    leg_rows = tuple(
        session.scalars(
            select(TicketCandidateLegRecord)
            .where(
                TicketCandidateLegRecord.ticket_candidate_id
                == record.ticket_candidate_id
            )
            .order_by(TicketCandidateLegRecord.leg_no)
        )
    )
    if tuple(row.leg_no for row in leg_rows) != (1, 2):
        raise ValueError("stored TicketCandidate requires ordered legs 1 and 2")
    legs = tuple(
        _base_selection_candidate(
            session,
            row.candidate_id,
            analysis_run_id,
        )
        for row in leg_rows
    )
    if any(
        row.internal_match_id != leg.match_id
        for row, leg in zip(leg_rows, legs, strict=True)
    ):
        raise ValueError("stored TicketCandidate leg bindings are inconsistent")
    if any(
        leg.status is not CandidateStatus.ELIGIBLE or leg.rejection_code is not None
        for leg in legs
    ):
        raise ValueError("stored TicketCandidate contains an ineligible leg")
    try:
        pass_type = PassType(record.pass_type)
    except ValueError as error:
        raise ValueError("stored TicketCandidate pass type is invalid") from error

    expected_joint_probability = quantize_probability(
        legs[0].probability * legs[1].probability
    )
    expected_gross_payout = official_gross_payout_fen(
        (leg.fixed_bonus for leg in legs),
        rules,
    )
    expected_gross = quantize_metric(
        expected_joint_probability * Decimal(expected_gross_payout)
    )
    expected_profit = quantize_metric(expected_gross - Decimal(rules.base_stake_fen))
    expected_roi = quantize_metric(expected_profit / Decimal(rules.base_stake_fen))
    expected_values = {
        "atomic_bet_count": 1,
        "base_stake_fen": rules.base_stake_fen,
        "joint_probability": expected_joint_probability,
        "gross_payout_fen": expected_gross_payout,
        "expected_gross_payout_fen": expected_gross,
        "expected_profit_fen": expected_profit,
        "expected_roi": expected_roi,
        "payout_policy_version": rules.version,
    }
    mismatched = [
        field
        for field, expected in expected_values.items()
        if getattr(record, field) != expected
    ]
    if mismatched:
        raise ValueError(
            "stored TicketCandidate derived fields are inconsistent: "
            + ", ".join(mismatched)
        )
    expected_id = stable_id(
        "2x1",
        analysis_run_id,
        legs[0].candidate_id,
        legs[1].candidate_id,
    )
    if record.ticket_candidate_id != expected_id:
        raise ValueError("stored TicketCandidate ID is inconsistent")
    return TicketCandidate(
        ticket_candidate_id=record.ticket_candidate_id,
        analysis_run_id=record.analysis_run_id,
        pass_type=pass_type,
        legs=legs,
        atomic_bet_count=record.atomic_bet_count,
        base_stake_fen=record.base_stake_fen,
        joint_probability=record.joint_probability,
        gross_payout_fen=record.gross_payout_fen,
        expected_gross_payout_fen=record.expected_gross_payout_fen,
        expected_profit_fen=record.expected_profit_fen,
        expected_roi=record.expected_roi,
        payout_policy_version=record.payout_policy_version,
    )


def _base_selection_candidate(
    session: Session,
    candidate_id: str,
    analysis_run_id: str,
) -> SelectionCandidate:
    record = session.get(BetCandidateRecord, candidate_id)
    if record is None:
        raise ValueError("stored TicketCandidate is missing a BetCandidate leg")
    if record.analysis_run_id != analysis_run_id:
        raise ValueError("stored BetCandidate crosses its AnalysisRun")
    run_match = session.get(
        AnalysisRunMatchRecord,
        (analysis_run_id, record.internal_match_id),
    )
    if run_match is None:
        raise ValueError("stored BetCandidate match is absent from its AnalysisRun")
    _validate_canonical_json_hash(
        run_match.context_json,
        run_match.context_hash,
        "AnalysisRun match context",
    )
    market = _market_from_canonical(record.market_key)
    try:
        selection = SelectionKey(record.selection_key)
        status = CandidateStatus(record.eligibility_status)
    except ValueError as error:
        raise ValueError("stored BetCandidate enum value is invalid") from error
    if (status is CandidateStatus.ELIGIBLE and record.rejection_code is not None) or (
        status is CandidateStatus.REJECTED and record.rejection_code is None
    ):
        raise ValueError("stored BetCandidate status and rejection code conflict")

    prediction = session.get(FinalPredictionRecord, record.final_prediction_id)
    if prediction is None:
        raise ValueError("stored BetCandidate is missing its FinalPrediction")
    prediction_market = _market_from_columns(
        prediction.market_key,
        prediction.market_type,
        prediction.handicap_value,
    )
    _canonical_payload(
        prediction.fusion_config_json,
        "FinalPrediction fusion config",
    )
    if (
        prediction.analysis_run_id != analysis_run_id
        or prediction.internal_match_id != record.internal_match_id
        or prediction_market != market
    ):
        raise ValueError("stored BetCandidate FinalPrediction lineage is inconsistent")
    prediction_outcomes = tuple(
        session.scalars(
            select(FinalPredictionOutcomeRecord).where(
                FinalPredictionOutcomeRecord.final_prediction_id
                == prediction.final_prediction_id
            )
        )
    )
    try:
        probabilities = {
            SelectionKey(row.selection_key): row.probability
            for row in prediction_outcomes
        }
        fusion_policy = FusionPolicyName(prediction.fusion_policy)
    except ValueError as error:
        raise ValueError("stored FinalPrediction enum value is invalid") from error
    if (
        len(prediction_outcomes) != len(SelectionKey)
        or set(probabilities) != set(SelectionKey)
        or probabilities[selection] != record.probability_used
        or prediction.final_prediction_id
        != stable_id(
            "final",
            analysis_run_id,
            record.internal_match_id,
            market.canonical,
            fusion_policy,
        )
    ):
        raise ValueError("stored BetCandidate probability lineage is inconsistent")
    ThreeWayProbability(
        home_win=probabilities[SelectionKey.HOME_WIN],
        draw=probabilities[SelectionKey.DRAW],
        away_win=probabilities[SelectionKey.AWAY_WIN],
    )

    snapshot = session.get(
        SportteryBonusSnapshotRecord,
        record.sporttery_bonus_snapshot_id,
    )
    if snapshot is None:
        raise ValueError("stored BetCandidate is missing its Sporttery snapshot")
    snapshot_market = _market_from_columns(
        snapshot.market_key,
        snapshot.market_type,
        snapshot.handicap_value,
    )
    _sha256_value(snapshot.payload_hash, "Sporttery snapshot payload hash")
    quote_rows = tuple(
        session.scalars(
            select(SportteryBonusQuoteRecord).where(
                SportteryBonusQuoteRecord.snapshot_id == snapshot.snapshot_id
            )
        )
    )
    try:
        fixed_bonuses = {
            SelectionKey(row.selection_key): row.fixed_bonus for row in quote_rows
        }
        sale_status = SaleStatus(snapshot.sale_status)
    except ValueError as error:
        raise ValueError("stored Sporttery snapshot enum value is invalid") from error
    if (
        run_match.sporttery_bonus_snapshot_id != record.sporttery_bonus_snapshot_id
        or snapshot.internal_match_id != record.internal_match_id
        or snapshot_market != market
        or len(quote_rows) != len(SelectionKey)
        or set(fixed_bonuses) != set(SelectionKey)
        or fixed_bonuses[selection] != record.fixed_bonus
        or sale_status is not SaleStatus.OPEN
    ):
        raise ValueError("stored BetCandidate Sporttery lineage is inconsistent")
    expected_break_even = quantize_probability(Decimal(1) / record.fixed_bonus)
    expected_ev = selection_ev(record.probability_used, record.fixed_bonus)
    if record.break_even_probability != expected_break_even or record.ev != expected_ev:
        raise ValueError("stored BetCandidate derived fields are inconsistent")
    expected_id = stable_id(
        "selection",
        analysis_run_id,
        record.internal_match_id,
        market.canonical,
        selection,
    )
    if record.candidate_id != expected_id:
        raise ValueError("stored BetCandidate ID is inconsistent")
    return SelectionCandidate(
        candidate_id=record.candidate_id,
        analysis_run_id=record.analysis_run_id,
        match_id=record.internal_match_id,
        market=market,
        selection=selection,
        final_prediction_id=record.final_prediction_id,
        sporttery_bonus_snapshot_id=record.sporttery_bonus_snapshot_id,
        probability=record.probability_used,
        fixed_bonus=record.fixed_bonus,
        break_even_probability=record.break_even_probability,
        ev=record.ev,
        status=status,
        rejection_code=record.rejection_code,
    )


def _market_from_canonical(value: str) -> MarketKey:
    market_type_value, separator, handicap_text = value.partition(":")
    try:
        handicap = Decimal(handicap_text) if separator else None
        if handicap is not None and not handicap.is_finite():
            raise ValueError("market handicap must be finite")
        market = MarketKey(
            market_type=MarketType(market_type_value),
            handicap_value=handicap,
        )
    except (ArithmeticError, ValueError) as error:
        raise ValueError("stored market key is invalid") from error
    if market.canonical != value:
        raise ValueError("stored market key is not canonical")
    return market


def _market_from_columns(
    canonical: str,
    market_type: str,
    handicap_value: Decimal | None,
) -> MarketKey:
    try:
        market = MarketKey(
            market_type=MarketType(market_type),
            handicap_value=handicap_value,
        )
    except ValueError as error:
        raise ValueError("stored market columns are invalid") from error
    if market.canonical != canonical:
        raise ValueError("stored market columns are inconsistent")
    return market


def _completed_analysis_run(
    session: Session,
    analysis_run_id: str,
) -> AnalysisRunRecord:
    record = session.get(AnalysisRunRecord, analysis_run_id)
    if (
        record is None
        or record.status != "COMPLETED"
        or record.completed_at_utc is None
    ):
        raise ValueError("historical artifact requires a completed AnalysisRun")
    if _sha256(record.config_json) != record.config_hash:
        raise ValueError("stored AnalysisRun config failed hash verification")
    if _sha256(record.input_manifest_json) != record.input_manifest_hash:
        raise ValueError("stored AnalysisRun manifest failed hash verification")
    return record


def _analysis_manifest_match_ids(record: AnalysisRunRecord) -> tuple[str, ...]:
    payload = _canonical_payload(
        record.input_manifest_json,
        "AnalysisRun input manifest",
    )
    matches = payload.get("matches") if isinstance(payload, dict) else None
    if not isinstance(matches, list):
        raise ValueError("AnalysisRun input manifest has no match sequence")
    match_ids = tuple(
        item.get("match_id") if isinstance(item, dict) else None for item in matches
    )
    if any(not isinstance(match_id, str) or not match_id for match_id in match_ids):
        raise ValueError("AnalysisRun input manifest has an invalid match sequence")
    values = tuple(match_id for match_id in match_ids if isinstance(match_id, str))
    if len(values) != len(set(values)):
        raise ValueError("AnalysisRun input manifest has duplicate matches")
    return values


def _revision_portfolio_ticket(
    session: Session,
    revision_id: str,
    parent_analysis_run_id: str,
    portfolio_id: str,
    ticket_id: str | None,
) -> tuple[dict[str, object], dict[str, object]]:
    record = session.get(PortfolioRevisionRecord, revision_id)
    if record is None or record.parent_analysis_run_id != parent_analysis_run_id:
        raise ValueError("portfolio revision scope lineage is inconsistent")
    payload = _validated_revision_payload(record)
    portfolios = payload.get("portfolios")
    if not isinstance(portfolios, list):
        raise ValueError("PortfolioRevision is missing portfolios")
    matches = [
        item
        for item in portfolios
        if isinstance(item, dict) and item.get("portfolio_id") == portfolio_id
    ]
    if len(matches) != 1:
        raise ValueError("PortfolioRevision does not contain the requested portfolio")
    portfolio = matches[0]
    if ticket_id is None:
        return portfolio, {}
    tickets = portfolio.get("tickets")
    ticket_matches = (
        [
            item
            for item in tickets
            if isinstance(item, dict) and item.get("ticket_id") == ticket_id
        ]
        if isinstance(tickets, list)
        else []
    )
    if len(ticket_matches) != 1:
        raise ValueError("PortfolioRevision does not contain the requested ticket")
    return portfolio, ticket_matches[0]


def _validated_revision_payload(
    record: PortfolioRevisionRecord,
) -> dict[str, object]:
    payload = _canonical_payload(record.revision_json, "PortfolioRevision")
    if not isinstance(payload, dict):
        raise ValueError("PortfolioRevision JSON must be an object")
    revision = PortfolioRevision.model_validate(payload)
    hash_payload = dict(payload)
    hash_payload.pop("revision_hash", None)
    if (
        revision.portfolio_revision_id != record.portfolio_revision_id
        or revision.parent_analysis_run_id != record.parent_analysis_run_id
        or revision.revision_hash != record.revision_hash
        or _sha256(_canonical_json(hash_payload)) != record.revision_hash
    ):
        raise ValueError("stored PortfolioRevision failed integrity verification")
    return payload


def _required_match_result(session: Session, result_id: str) -> MatchResultRecord:
    record = session.get(MatchResultRecord, result_id)
    if record is None:
        raise ValueError(f"unknown MatchResult: {result_id}")
    _match_result(session, record)
    return record


def _record_payload(record: object, excluded: set[str]) -> dict[str, object]:
    table = getattr(record, "__table__")
    return {
        column.name: getattr(record, column.name)
        for column in table.columns
        if column.name not in excluded
    }


def _detached_record(record):
    return type(record)(**_record_payload(record, set()))


def _domain_model_payload(value: BaseModel) -> dict[str, object]:
    return value.model_dump(mode="python", exclude_computed_fields=True)


def _assert_same_record(stored: object, expected: object, label: str) -> None:
    columns = getattr(stored, "__table__").columns
    mismatched = [
        column.name
        for column in columns
        if getattr(stored, column.name) != getattr(expected, column.name)
    ]
    if mismatched:
        raise ValueError(
            f"immutable {label} conflicts on fields: {', '.join(mismatched)}"
        )


def _unique_ids(values: Sequence[str], label: str) -> tuple[str, ...]:
    result = tuple(values)
    if any(not value for value in result) or len(set(result)) != len(result):
        raise ValueError(f"{label} IDs must be nonempty and unique")
    return result


def _validate_canonical_json_hash(
    value: str,
    expected_hash: str,
    label: str,
) -> object:
    payload = _canonical_payload(value, label)
    _require_hash(expected_hash, _sha256(value), label)
    return payload


def _canonical_payload(value: str, label: str) -> object:
    def reject_constant(constant: str) -> None:
        raise ValueError(f"non-finite JSON number is forbidden: {constant}")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    try:
        payload = json.loads(
            value,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"{label} contains invalid JSON") from error
    if _canonical_json(payload) != value:
        raise ValueError(f"{label} is not canonical JSON")
    return payload


def _canonical_json(value: object) -> str:
    return json.dumps(
        _canonical_value(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite Decimal cannot be serialized")
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return _aware_utc(value, "JSON datetime").isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def _aware_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _sha256_value(value: str, label: str) -> None:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _validate_match_result_payload(result: MatchResult) -> None:
    _require_hash(
        result.payload_hash,
        match_result_payload_sha256(result.home_goals, result.away_goals),
        "MatchResult payload",
    )


def _require_hash(actual: str, expected: str, label: str) -> None:
    _sha256_value(actual, f"{label} hash")
    if actual != expected:
        raise ValueError(f"{label} failed hash verification")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _match_result_order(result: MatchResult) -> tuple[datetime, datetime, str, str]:
    return (
        result.ingested_at_utc,
        result.available_at_utc,
        result.provider_code,
        result.match_result_id,
    )
