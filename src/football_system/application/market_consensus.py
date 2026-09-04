from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal, ROUND_HALF_EVEN
from inspect import getattr_static

from pydantic import Field, model_validator

from football_system.application.environment import (
    ProviderRuntimeProvenanceMismatchError,
    RuntimeProvenance,
    is_mock_provider_code,
    require_provider_runtime_provenance,
)
from football_system.application.ports.data_providers import (
    MarketOddsBatch,
    MarketOddsProvider,
    MarketOddsReconciliationIssue,
    SnapshotQuery,
)
from football_system.domain.archive import HistoricalDataMode, canonical_payload_sha256
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    stable_id,
)
from football_system.domain.market import (
    MarketKey,
    MarketType,
    SelectionKey,
    ThreeWayMarketOdds,
    ThreeWayProbability,
)
from football_system.domain.match import (
    MarketOddsSnapshot,
    OddsQuote,
    ProviderMatchMapping,
)
from football_system.domain.services.probability import (
    normalized_inverse_probability,
    quantize_three_way_probability,
)

MARKET_CONSENSUS_MEDIAN_V1 = "MARKET_CONSENSUS_MEDIAN_V1"
_THREE_WAY_MARKET = MarketKey(market_type=MarketType.THREE_WAY)
_STORAGE_ODDS_QUANTUM = Decimal("0.000001")


class MarketConsensusError(ValueError):
    code = "MARKET_CONSENSUS_ERROR"

    def __init__(self, detail: str) -> None:
        super().__init__(f"{self.code}: {detail}")


class ConsensusConstituent(DomainModel):
    provider_code: Identifier
    bookmaker_code: Identifier
    snapshot_id: Identifier
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConsensusLineage(DomainModel):
    policy: Identifier = MARKET_CONSENSUS_MEDIAN_V1
    match_id: Identifier
    market: MarketKey
    source_snapshot_key: Identifier
    constituents: tuple[ConsensusConstituent, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_lineage(self) -> ConsensusLineage:
        if self.policy != MARKET_CONSENSUS_MEDIAN_V1:
            raise ValueError("consensus lineage policy is unsupported")
        identities = tuple(
            (item.snapshot_id, item.payload_hash) for item in self.constituents
        )
        if identities != tuple(sorted(identities)):
            raise ValueError(
                "consensus constituents must be sorted by ID and payload hash"
            )
        if len({item.snapshot_id for item in self.constituents}) != len(
            self.constituents
        ):
            raise ValueError("consensus constituents must have unique snapshot IDs")
        return self


class ThreeWayOddsChange(DomainModel):
    home_win: Decimal = Field(allow_inf_nan=False)
    draw: Decimal = Field(allow_inf_nan=False)
    away_win: Decimal = Field(allow_inf_nan=False)


class MarketMovementSummary(DomainModel):
    match_id: Identifier
    market: MarketKey
    opening_odds: ThreeWayMarketOdds
    latest_odds: ThreeWayMarketOdds
    absolute_change: ThreeWayOddsChange
    relative_change: ThreeWayOddsChange
    opening_provider_code: Identifier
    latest_provider_code: Identifier
    opening_available_at_utc: UtcDateTime
    opening_ingested_at_utc: UtcDateTime
    latest_available_at_utc: UtcDateTime
    latest_ingested_at_utc: UtcDateTime
    opening_captured_from_utc: UtcDateTime
    opening_captured_to_utc: UtcDateTime
    latest_captured_from_utc: UtcDateTime
    latest_captured_to_utc: UtcDateTime
    opening_bookmaker_count: int = Field(ge=1, strict=True)
    latest_bookmaker_count: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def validate_range(self) -> MarketMovementSummary:
        opening_observation = (
            self.opening_available_at_utc,
            self.opening_ingested_at_utc,
            self.opening_provider_code,
        )
        latest_observation = (
            self.latest_available_at_utc,
            self.latest_ingested_at_utc,
            self.latest_provider_code,
        )
        if opening_observation >= latest_observation:
            raise ValueError("market movement requires two ordered observations")
        if self.opening_captured_from_utc > self.opening_captured_to_utc:
            raise ValueError("opening bookmaker freshness range is invalid")
        if self.latest_captured_from_utc > self.latest_captured_to_utc:
            raise ValueError("latest bookmaker freshness range is invalid")
        return self


class ConsensusMarketOddsProvider(MarketOddsProvider):
    """Derives one reproducible fair-odds snapshot per match from all bookmakers."""

    provider_code = MARKET_CONSENSUS_MEDIAN_V1
    bookmaker_code = MARKET_CONSENSUS_MEDIAN_V1

    def __init__(self, providers: Iterable[MarketOddsProvider]) -> None:
        self._providers = tuple(providers)
        if not self._providers:
            raise ValueError("market consensus requires at least one source provider")
        self._lineage_by_snapshot_id: dict[str, ConsensusLineage] = {}
        self._runtime_source_provenance: tuple[RuntimeProvenance, ...] | None = None

    @property
    def lineage_by_snapshot_id(self) -> dict[str, ConsensusLineage]:
        return dict(self._lineage_by_snapshot_id)

    @property
    def lineages(self) -> tuple[ConsensusLineage, ...]:
        return tuple(
            self._lineage_by_snapshot_id[snapshot_id]
            for snapshot_id in sorted(self._lineage_by_snapshot_id)
        )

    @property
    def runtime_provenance(self) -> RuntimeProvenance:
        sources = self._source_runtime_provenance()
        return RuntimeProvenance(
            environment=sources[0].environment,
            provider_code=self.provider_code,
            provenance="market consensus from "
            + ",".join(sorted(source.provider_code or "UNKNOWN" for source in sources)),
            is_mock=any(
                source.is_mock or is_mock_provider_code(source.provider_code)
                for source in sources
            )
            or any(is_mock_provider_code(code) for code in self.issue_provider_codes),
            data_mode=sources[0].data_mode,
        )

    @property
    def issue_provider_codes(self) -> tuple[str, ...]:
        sources = self._source_runtime_provenance()
        codes: set[str] = set()
        for provider, source in zip(self._providers, sources, strict=True):
            if source.provider_code is not None:
                codes.add(source.provider_code)
            try:
                getattr_static(provider, "issue_provider_codes")
            except AttributeError:
                continue
            nested_codes = getattr(provider, "issue_provider_codes")
            if not isinstance(nested_codes, tuple) or any(
                not isinstance(code, str) or not code.strip() for code in nested_codes
            ):
                raise ProviderRuntimeProvenanceMismatchError(
                    "market consensus source issue provenance is invalid"
                )
            codes.update(nested_codes)
        return tuple(sorted(codes))

    def _source_runtime_provenance(self) -> tuple[RuntimeProvenance, ...]:
        current = tuple(
            require_provider_runtime_provenance(
                provider,
                "market consensus source",
            )
            for provider in self._providers
        )
        _validate_source_runtime_boundaries(current)
        if self._runtime_source_provenance is None:
            self._runtime_source_provenance = current
        elif current != self._runtime_source_provenance:
            raise ProviderRuntimeProvenanceMismatchError(
                "market consensus source runtime provenance changed"
            )
        return current

    async def fetch_market_odds(self, query: SnapshotQuery) -> MarketOddsBatch:
        if self._runtime_source_provenance is not None:
            sources: tuple[RuntimeProvenance | None, ...] = (
                self._source_runtime_provenance()
            )
        else:
            sources = tuple(
                _optional_provider_runtime_provenance(provider)
                for provider in self._providers
            )
            _validate_source_runtime_boundaries(sources)
            if all(source is not None for source in sources):
                self._runtime_source_provenance = tuple(
                    source for source in sources if source is not None
                )
        batches: list[MarketOddsBatch] = []
        for provider, source in zip(self._providers, sources, strict=True):
            batch = await provider.fetch_market_odds(query)
            if source is not None:
                _validate_source_batch_provenance(provider, batch, source)
            batches.append(batch)
        source_snapshots, source_mappings, source_issues = _collect_source_data(
            batches, query
        )
        by_match_bookmaker: defaultdict[tuple[str, str], list[MarketOddsSnapshot]] = (
            defaultdict(list)
        )
        for snapshot in source_snapshots:
            by_match_bookmaker[(snapshot.match_id, snapshot.bookmaker_code)].append(
                snapshot
            )

        latest_by_match: defaultdict[str, list[MarketOddsSnapshot]] = defaultdict(list)
        for (match_id, _), snapshots in by_match_bookmaker.items():
            latest_by_match[match_id].append(_latest_bookmaker_snapshot(snapshots))

        consensus_snapshots: list[MarketOddsSnapshot] = []
        consensus_mappings: list[ProviderMatchMapping] = []
        lineage_by_snapshot_id: dict[str, ConsensusLineage] = {}
        for match_id in sorted(latest_by_match):
            constituents = tuple(
                sorted(
                    latest_by_match[match_id],
                    key=lambda item: (
                        item.bookmaker_code,
                        item.provider_code,
                        item.snapshot_id,
                    ),
                )
            )
            for constituent in constituents:
                mapping = source_mappings.get(
                    (constituent.provider_code, constituent.match_id)
                )
                if mapping is None:
                    raise MarketConsensusError(
                        "a constituent snapshot has no source provider mapping"
                    )
                if mapping.available_at_utc > query.as_of_at_utc:
                    raise MarketConsensusError(
                        "a constituent mapping is not visible at the requested cutoff"
                    )
            snapshot, mapping, lineage = _derive_consensus(match_id, constituents)
            consensus_snapshots.append(snapshot)
            consensus_mappings.append(mapping)
            lineage_by_snapshot_id[snapshot.snapshot_id] = lineage

        self._lineage_by_snapshot_id = lineage_by_snapshot_id
        return MarketOddsBatch(
            snapshots=tuple(consensus_snapshots),
            mappings=tuple(consensus_mappings),
            issues=source_issues,
        )


def market_movement_summary(
    snapshots: Iterable[MarketOddsSnapshot],
) -> MarketMovementSummary | None:
    """Summarize one match/market stream; one provider response is not movement."""

    values = tuple(snapshots)
    if not values:
        return None
    first = values[0]
    if any(
        item.match_id != first.match_id or item.market != first.market
        for item in values
    ):
        raise ValueError("market movement snapshots must share one match and market")
    by_observation: defaultdict[
        tuple[str, UtcDateTime, UtcDateTime], list[MarketOddsSnapshot]
    ] = defaultdict(list)
    for snapshot in values:
        key = (
            snapshot.provider_code,
            snapshot.available_at_utc,
            snapshot.ingested_at_utc,
        )
        by_observation[key].append(snapshot)
    observation_keys = tuple(
        sorted(by_observation, key=lambda item: (item[1], item[2], item[0]))
    )
    consensus_by_observation = {
        key: _observation_consensus(by_observation[key]) for key in observation_keys
    }
    if len(observation_keys) < 2:
        return None
    opening_key = observation_keys[0]
    latest_key = observation_keys[-1]
    opening, opening_constituents = consensus_by_observation[opening_key]
    latest, latest_constituents = consensus_by_observation[latest_key]
    return MarketMovementSummary(
        match_id=first.match_id,
        market=first.market,
        opening_odds=opening,
        latest_odds=latest,
        absolute_change=_odds_change(opening, latest),
        relative_change=_relative_odds_change(opening, latest),
        opening_provider_code=opening_key[0],
        latest_provider_code=latest_key[0],
        opening_available_at_utc=opening_key[1],
        opening_ingested_at_utc=opening_key[2],
        latest_available_at_utc=latest_key[1],
        latest_ingested_at_utc=latest_key[2],
        opening_captured_from_utc=min(
            item.captured_at_utc for item in opening_constituents
        ),
        opening_captured_to_utc=max(
            item.captured_at_utc for item in opening_constituents
        ),
        latest_captured_from_utc=min(
            item.captured_at_utc for item in latest_constituents
        ),
        latest_captured_to_utc=max(
            item.captured_at_utc for item in latest_constituents
        ),
        opening_bookmaker_count=len(opening_constituents),
        latest_bookmaker_count=len(latest_constituents),
    )


def summarize_market_movements(
    snapshots: Iterable[MarketOddsSnapshot],
) -> tuple[MarketMovementSummary, ...]:
    grouped: defaultdict[tuple[str, str], list[MarketOddsSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        grouped[(snapshot.match_id, snapshot.market.canonical)].append(snapshot)
    summaries = (market_movement_summary(grouped[key]) for key in sorted(grouped))
    return tuple(summary for summary in summaries if summary is not None)


def _optional_provider_runtime_provenance(
    provider: object,
) -> RuntimeProvenance | None:
    try:
        getattr_static(provider, "runtime_provenance")
    except AttributeError:
        return None
    return require_provider_runtime_provenance(provider, "market consensus source")


def _validate_source_runtime_boundaries(
    sources: tuple[RuntimeProvenance | None, ...],
) -> None:
    known = tuple(source for source in sources if source is not None)
    if not known:
        return
    if len(known) != len(sources):
        raise ProviderRuntimeProvenanceMismatchError(
            "market consensus cannot mix declared and undeclared runtime provenance"
        )
    environments = {source.environment for source in known}
    data_modes = {source.data_mode for source in known}
    if len(environments) != 1 or len(data_modes) != 1:
        raise ProviderRuntimeProvenanceMismatchError(
            "market consensus sources cross runtime or data-mode boundaries"
        )


def _validate_source_batch_provenance(
    provider: object,
    batch: MarketOddsBatch,
    provenance: RuntimeProvenance,
) -> None:
    expected = provenance.provider_code
    actual_codes = {
        *(snapshot.provider_code for snapshot in batch.snapshots),
        *(mapping.provider_code for mapping in batch.mappings),
    }
    if any(code != expected for code in actual_codes):
        raise ProviderRuntimeProvenanceMismatchError(
            "market consensus source emitted data outside declared provenance"
        )
    allowed_issue_codes = {expected}
    try:
        getattr_static(provider, "issue_provider_codes")
    except AttributeError:
        nested_codes = ()
    else:
        nested_codes = getattr(provider, "issue_provider_codes")
    if not isinstance(nested_codes, tuple) or any(
        not isinstance(code, str) or not code.strip() for code in nested_codes
    ):
        raise ProviderRuntimeProvenanceMismatchError(
            "market consensus source issue provenance is invalid"
        )
    allowed_issue_codes.update(nested_codes)
    if any(issue.provider_code not in allowed_issue_codes for issue in batch.issues):
        raise ProviderRuntimeProvenanceMismatchError(
            "market consensus source emitted an issue outside declared provenance"
        )
    if provenance.data_mode is HistoricalDataMode.SOURCE_TIME_RESEARCH and any(
        snapshot.ingested_at_utc != snapshot.available_at_utc
        for snapshot in batch.snapshots
    ):
        raise ProviderRuntimeProvenanceMismatchError(
            "source-time market snapshots must use the source-time ingestion boundary"
        )


def _collect_source_data(
    batches: Iterable[MarketOddsBatch],
    query: SnapshotQuery,
) -> tuple[
    tuple[MarketOddsSnapshot, ...],
    dict[tuple[str, str], ProviderMatchMapping],
    tuple[MarketOddsReconciliationIssue, ...],
]:
    requested = set(query.match_ids)
    snapshot_by_id: dict[str, MarketOddsSnapshot] = {}
    mapping_by_scope: dict[tuple[str, str], ProviderMatchMapping] = {}
    issue_by_id: dict[str, MarketOddsReconciliationIssue] = {}
    for batch in batches:
        for issue in batch.issues:
            previous = issue_by_id.get(issue.issue_id)
            if previous is not None and previous != issue:
                raise MarketConsensusError(
                    "conflicting duplicate reconciliation issues"
                )
            issue_by_id[issue.issue_id] = issue
        for mapping in batch.mappings:
            if mapping.internal_match_id not in requested:
                continue
            scope = (mapping.provider_code, mapping.internal_match_id)
            previous = mapping_by_scope.get(scope)
            if previous is not None and previous != mapping:
                raise MarketConsensusError("conflicting duplicate provider mappings")
            mapping_by_scope[scope] = mapping
        for snapshot in batch.snapshots:
            if snapshot.match_id not in requested:
                continue
            if snapshot.market != _THREE_WAY_MARKET:
                continue
            if not _snapshot_is_visible(snapshot, query):
                continue
            if not (
                snapshot.captured_at_utc
                <= snapshot.available_at_utc
                <= snapshot.ingested_at_utc
            ):
                raise MarketConsensusError(
                    "constituent snapshot timestamps are not causally ordered"
                )
            if (
                canonical_payload_sha256(snapshot.three_way_odds())
                != snapshot.payload_hash
            ):
                raise MarketConsensusError(
                    "constituent snapshot payload hash does not match its odds"
                )
            previous = snapshot_by_id.get(snapshot.snapshot_id)
            if previous is not None and previous != snapshot:
                raise MarketConsensusError("conflicting duplicate snapshot IDs")
            snapshot_by_id[snapshot.snapshot_id] = snapshot
    return (
        tuple(
            sorted(
                snapshot_by_id.values(),
                key=lambda item: (
                    item.match_id,
                    item.bookmaker_code,
                    item.provider_code,
                    item.snapshot_id,
                ),
            )
        ),
        mapping_by_scope,
        tuple(issue_by_id[issue_id] for issue_id in sorted(issue_by_id)),
    )


def _latest_bookmaker_snapshot(
    snapshots: Iterable[MarketOddsSnapshot],
) -> MarketOddsSnapshot:
    values = tuple(snapshots)
    if not values:
        raise MarketConsensusError("a bookmaker group cannot be empty")
    by_version: dict[tuple[object, ...], MarketOddsSnapshot] = {}
    for snapshot in values:
        version = (
            snapshot.available_at_utc,
            snapshot.captured_at_utc,
            snapshot.ingested_at_utc,
        )
        previous = by_version.get(version)
        if previous is not None and (
            previous.payload_hash != snapshot.payload_hash
            or previous.three_way_odds() != snapshot.three_way_odds()
        ):
            raise MarketConsensusError("conflicting duplicate bookmaker versions")
        if previous is None or snapshot.snapshot_id > previous.snapshot_id:
            by_version[version] = snapshot
    return max(
        by_version.values(),
        key=lambda item: (
            item.available_at_utc,
            item.captured_at_utc,
            item.ingested_at_utc,
            item.snapshot_id,
        ),
    )


def _derive_consensus(
    match_id: str,
    constituents: tuple[MarketOddsSnapshot, ...],
) -> tuple[MarketOddsSnapshot, ProviderMatchMapping, ConsensusLineage]:
    if not constituents:
        raise MarketConsensusError("consensus requires at least one constituent")
    fair_probabilities = [
        normalized_inverse_probability(snapshot.three_way_odds())[0]
        for snapshot in constituents
    ]
    median_probabilities = {
        selection: _median(
            [
                probabilities.for_selection(selection)
                for probabilities in fair_probabilities
            ]
        )
        for selection in SelectionKey
    }
    total = sum(median_probabilities.values(), Decimal(0))
    if total <= 0:
        raise MarketConsensusError("consensus median probabilities are invalid")
    fair = quantize_three_way_probability(
        ThreeWayProbability(
            home_win=median_probabilities[SelectionKey.HOME_WIN] / total,
            draw=median_probabilities[SelectionKey.DRAW] / total,
            away_win=median_probabilities[SelectionKey.AWAY_WIN] / total,
        )
    )
    fair_odds = ThreeWayMarketOdds(
        home_win=_storage_odds(Decimal(1) / fair.home_win),
        draw=_storage_odds(Decimal(1) / fair.draw),
        away_win=_storage_odds(Decimal(1) / fair.away_win),
    )
    lineage_constituents = tuple(
        sorted(
            (
                ConsensusConstituent(
                    provider_code=item.provider_code,
                    bookmaker_code=item.bookmaker_code,
                    snapshot_id=item.snapshot_id,
                    payload_hash=item.payload_hash,
                )
                for item in constituents
            ),
            key=lambda item: (item.snapshot_id, item.payload_hash),
        )
    )
    source_snapshot_key = stable_id(
        "market-consensus-source",
        MARKET_CONSENSUS_MEDIAN_V1,
        match_id,
        _THREE_WAY_MARKET.canonical,
        canonical_payload_sha256(lineage_constituents),
    )
    lineage = ConsensusLineage(
        match_id=match_id,
        market=_THREE_WAY_MARKET,
        source_snapshot_key=source_snapshot_key,
        constituents=lineage_constituents,
    )
    payload_hash = canonical_payload_sha256(fair_odds)
    captured_at_utc = max(item.captured_at_utc for item in constituents)
    available_at_utc = max(item.available_at_utc for item in constituents)
    ingested_at_utc = max(item.ingested_at_utc for item in constituents)
    snapshot = MarketOddsSnapshot(
        snapshot_id=stable_id(
            "market-consensus",
            MARKET_CONSENSUS_MEDIAN_V1,
            source_snapshot_key,
            payload_hash,
        ),
        match_id=match_id,
        provider_code=MARKET_CONSENSUS_MEDIAN_V1,
        bookmaker_code=MARKET_CONSENSUS_MEDIAN_V1,
        market=_THREE_WAY_MARKET,
        quotes=tuple(
            OddsQuote(selection=selection, odds=value)
            for selection, value in fair_odds.items()
        ),
        captured_at_utc=captured_at_utc,
        available_at_utc=available_at_utc,
        ingested_at_utc=ingested_at_utc,
        source_snapshot_key=source_snapshot_key,
        payload_hash=payload_hash,
    )
    mapping = ProviderMatchMapping(
        mapping_id=stable_id(
            "provider-mapping",
            MARKET_CONSENSUS_MEDIAN_V1,
            "consensus",
            source_snapshot_key,
        ),
        provider_code=MARKET_CONSENSUS_MEDIAN_V1,
        external_namespace="market_consensus",
        external_match_id=stable_id(
            "market-consensus-external",
            match_id,
            source_snapshot_key,
        ),
        internal_match_id=match_id,
        resolution_method=MARKET_CONSENSUS_MEDIAN_V1,
        confidence=Decimal(1),
        available_at_utc=available_at_utc,
    )
    return snapshot, mapping, lineage


def derive_market_consensus(
    match_id: str,
    constituents: tuple[MarketOddsSnapshot, ...],
) -> tuple[MarketOddsSnapshot, ProviderMatchMapping, ConsensusLineage]:
    """Derive and expose the frozen consensus artifact for persistence checks."""

    return _derive_consensus(match_id, constituents)


def _median(values: list[Decimal]) -> Decimal:
    if not values:
        raise MarketConsensusError("median requires at least one value")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _storage_odds(value: Decimal) -> Decimal:
    return value.quantize(_STORAGE_ODDS_QUANTUM, rounding=ROUND_HALF_EVEN)


def _snapshot_is_visible(
    snapshot: MarketOddsSnapshot,
    query: SnapshotQuery,
) -> bool:
    return all(
        timestamp <= query.as_of_at_utc
        for timestamp in (
            snapshot.captured_at_utc,
            snapshot.available_at_utc,
            snapshot.ingested_at_utc,
        )
    )


def _observation_consensus(
    snapshots: Iterable[MarketOddsSnapshot],
) -> tuple[ThreeWayMarketOdds, tuple[MarketOddsSnapshot, ...]]:
    by_bookmaker: defaultdict[str, list[MarketOddsSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        by_bookmaker[snapshot.bookmaker_code].append(snapshot)
    constituents = tuple(
        _latest_bookmaker_snapshot(values) for _, values in sorted(by_bookmaker.items())
    )
    consensus = ThreeWayMarketOdds(
        home_win=_median([item.three_way_odds().home_win for item in constituents]),
        draw=_median([item.three_way_odds().draw for item in constituents]),
        away_win=_median([item.three_way_odds().away_win for item in constituents]),
    )
    return consensus, constituents


def _odds_change(
    opening: ThreeWayMarketOdds,
    latest: ThreeWayMarketOdds,
) -> ThreeWayOddsChange:
    return ThreeWayOddsChange(
        home_win=latest.home_win - opening.home_win,
        draw=latest.draw - opening.draw,
        away_win=latest.away_win - opening.away_win,
    )


def _relative_odds_change(
    opening: ThreeWayMarketOdds,
    latest: ThreeWayMarketOdds,
) -> ThreeWayOddsChange:
    return ThreeWayOddsChange(
        home_win=(latest.home_win - opening.home_win) / opening.home_win,
        draw=(latest.draw - opening.draw) / opening.draw,
        away_win=(latest.away_win - opening.away_win) / opening.away_win,
    )
