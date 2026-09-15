"""Concrete, closed-type offline market graph with exact read/save replay."""

from __future__ import annotations

import json

from pydantic import BaseModel
from sqlalchemy import select

from football_system.application.review_v4 import (
    export_packet_v4,
    fuse_v4,
    parse_v4_json,
)
from football_system.domain.archive import canonical_json
from football_system.domain.goal_model import GoalTrainingCohortV1, GoalTrainingFactV1
from football_system.domain.market_analysis import (
    LegacyThreeWayInputV1,
    MarketMatchIdentityV1,
    MarketModelLineageV1,
)
from football_system.domain.market_artifacts import ARTIFACT_TYPES
from football_system.domain.market_v2 import (
    MarketArtifact,
    MarketKeyV2,
    MarketProbabilityDistributionV1,
    revalidate,
    content_hash,
)
from football_system.domain.offline_market_source import OfflineMarketSourceV1
from football_system.domain.review import PacketModelQuantLineageV3
from football_system.domain.services.settlement_v2 import settle_strategy_v2
from football_system.domain.services.strategy_pass_v2 import outcome_candidates
from football_system.infrastructure.database.historical_repositories import (
    _match_result,
)
from football_system.infrastructure.database.models import (
    Base,
    CanonicalMatchIdentityRecord,
    FinalPredictionRecord,
    MatchRecord,
    MatchResultRecord,
    TeamRecord,
)
from football_system.infrastructure.database.post_review_repositories import (
    _completed_run,
    _final_prediction,
)
from football_system.infrastructure.database.review_repositories import (
    SqlAlchemyReviewArtifactRepository,
)
from football_system.infrastructure.database.market_v2_schema import (
    TYPE_SPECS,
    CHILD_SPECS,
    MARKET_SCHEMAS,
)
from football_system.infrastructure.providers.exact_market_fixture import (
    parse_exact_market_fixture,
)

MAX_LOCAL_ARTIFACT_BYTES = 64 * 1024 * 1024


def at(value, path):
    for key in path.split("."):
        if value is None:
            return None
        value = value.get(key) if isinstance(value, dict) else getattr(value, key)
    return value


def rows_for(value):
    name, fields = TYPE_SPECS[value.schema_version]
    row = {
        "artifact_id": value.artifact_id,
        **{col: at(value, path) for col, (path, *_) in fields.items()},
    }
    if value.schema_version in MARKET_SCHEMAS:
        row["market_hash"] = value.market_key.market_hash
    if name == "mm_outcome_candidates":
        row["outcome_key"] = value.outcome.value
    rows = {
        "mm_artifacts": [
            dict(
                artifact_id=value.artifact_id,
                schema_version=value.schema_version,
                content_hash=value.content_hash,
                artifact_json=canonical_json(value),
            )
        ],
        name: [row],
    }
    for schema, path, table, projections in CHILD_SPECS:
        if value.schema_version != schema:
            continue
        children = []
        for index, item in enumerate(at(value, path)):
            child = dict(
                parent_id=value.artifact_id,
                position=index,
                item_json=canonical_json(item),
            )
            child.update({col: at(item, p) for col, (p, *_) in projections.items()})
            if table in {"mm_odds_prices", "mm_sp_prices"}:
                child.update(
                    market_hash=value.market_key.market_hash,
                    outcome_key=item.outcome.value,
                    price=str(item.price),
                )
            children.append(child)
        rows[table] = children
    rows["mm_artifact_seals"] = [dict(artifact_id=value.artifact_id)]
    return rows


def nested(value):
    if isinstance(value, BaseModel):
        for key in type(value).model_fields:
            item = getattr(value, key)
            yield from nested(item)
        if isinstance(value, (MarketArtifact, MarketKeyV2)):
            yield value
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from nested(item)


class SqlAlchemyMultiMarketRepository:
    def __init__(self, sessions):
        self._sessions = sessions

    def save(self, artifact):
        artifact = revalidate(artifact)
        if len(canonical_json(artifact).encode()) > MAX_LOCAL_ARTIFACT_BYTES:
            raise ValueError("sealed multi-market artifact byte bound exceeded")
        with self._sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            seen = {}
            markets = set()
            for item in nested(artifact):
                if isinstance(item, MarketKeyV2):
                    if item.market_hash not in markets:
                        self._market(session, item)
                        markets.add(item.market_hash)
                    continue
                if (
                    item.schema_version not in ARTIFACT_TYPES
                    or type(item) is not ARTIFACT_TYPES[item.schema_version]
                ):
                    raise ValueError("unsupported multi-market artifact type")
                prior = seen.get(item.artifact_id)
                if prior is not None:
                    if prior.content_hash != item.content_hash:
                        raise ValueError("conflicting nested immutable artifact")
                    continue
                seen[item.artifact_id] = item
                self._verify_semantics(session, item)
                expected = rows_for(item)
                if _rows(session, "mm_artifacts", artifact_id=item.artifact_id):
                    self._verify_rows(session, item)
                    session.info.setdefault("market_v2_verified", {})[
                        item.artifact_id
                    ] = item
                    continue
                for table, rows in expected.items():
                    if rows:
                        session.execute(Base.metadata.tables[table].insert(), rows)
                self._verify_rows(session, item)
                session.info.setdefault("market_v2_verified", {})[item.artifact_id] = (
                    item
                )
            return artifact

    def load(self, artifact_id, schema_version=None):
        with self._sessions.begin() as session:
            value = self._load(session, artifact_id)
            if schema_version is not None and value.schema_version != schema_version:
                raise ValueError("wrong artifact schema version")
            return value

    def _load(self, session, artifact_id, seen=None):
        # Memoize only within this isolated SQLite transaction. All referenced
        # rows are append-only; no authorization or cross-call cache is retained.
        cache = session.info.setdefault("market_v2_verified", {})
        if artifact_id in cache:
            return cache[artifact_id]
        seen = set() if seen is None else seen
        if artifact_id in seen:
            raise ValueError("cyclic artifact reference")
        seen.add(artifact_id)
        row = _one(session, "mm_artifacts", artifact_id=artifact_id)
        if len(row["artifact_json"].encode()) > MAX_LOCAL_ARTIFACT_BYTES:
            raise ValueError("stored artifact byte bound exceeded")
        cls = ARTIFACT_TYPES.get(row["schema_version"])
        if cls is None:
            raise ValueError("unknown stored artifact version")
        value = cls.model_validate_json(row["artifact_json"])
        market_cache = session.info.setdefault("market_v2_catalogs", set())
        for child in nested(value):
            if isinstance(child, MarketKeyV2):
                if child.market_hash not in market_cache:
                    self._verify_market(session, child)
                    market_cache.add(child.market_hash)
            else:
                prior = cache.get(child.artifact_id)
                if prior is not None:
                    if prior.content_hash != child.content_hash:
                        raise ValueError("conflicting embedded artifact identity")
                    continue
                self._verify_rows(session, child)
                if child.artifact_id != artifact_id:
                    self._verify_semantics(session, child, seen)
                    cache[child.artifact_id] = child
        self._verify_semantics(session, value, seen)
        seen.remove(artifact_id)
        cache[artifact_id] = value
        return value

    def _verify_rows(self, session, value):
        for name, expected in rows_for(value).items():
            key = (
                "parent_id"
                if name
                not in {
                    "mm_artifacts",
                    "mm_artifact_seals",
                    TYPE_SPECS[value.schema_version][0],
                }
                else "artifact_id"
            )
            stored = _rows(session, name, **{key: value.artifact_id})
            if sorted(canonical_json(dict(r)) for r in stored) != sorted(
                canonical_json(r) for r in expected
            ):
                raise ValueError(f"corrupt sealed multi-market graph: {name}")

    def _market(self, session, key):
        if _rows(session, "mm_market_keys", market_hash=key.market_hash):
            self._verify_market(session, key)
            return
        session.execute(
            Base.metadata.tables["mm_market_keys"].insert(),
            dict(
                market_hash=key.market_hash,
                canonical=key.canonical,
                market_json=canonical_json(key),
                catalog_json=canonical_json(key.catalog),
            ),
        )
        session.execute(
            Base.metadata.tables["mm_outcome_catalog"].insert(),
            [
                dict(market_hash=key.market_hash, outcome_key=o.value, outcome_no=i)
                for i, o in enumerate(key.catalog)
            ],
        )
        session.execute(
            Base.metadata.tables["mm_catalog_seals"].insert(),
            dict(market_hash=key.market_hash),
        )

    def _verify_market(self, session, key):
        row = _one(session, "mm_market_keys", market_hash=key.market_hash)
        if dict(row) != dict(
            market_hash=key.market_hash,
            canonical=key.canonical,
            market_json=canonical_json(key),
            catalog_json=canonical_json(key.catalog),
        ):
            raise ValueError("market key canonical/hash mismatch")
        rows = _rows(session, "mm_outcome_catalog", market_hash=key.market_hash)
        if sorted((r["outcome_no"], r["outcome_key"]) for r in rows) != list(
            enumerate(key.catalog)
        ) or not _rows(session, "mm_catalog_seals", market_hash=key.market_hash):
            raise ValueError("market outcome catalog incomplete or corrupt")

    def _verify_semantics(self, session, value, seen=None):
        schema = value.schema_version
        if schema in {"MARKET_ODDS_SNAPSHOT_V2", "SPORTTERY_FIXED_BONUS_SNAPSHOT_V2"}:
            source = self._load(session, value.source_artifact_id, seen)
            if (
                source.content_hash != value.source_artifact_hash
                or parse_exact_market_fixture(source.raw_json.encode())[1] != value
            ):
                raise ValueError(
                    "snapshot differs from exact raw fixture semantics/catalog"
                )
        elif schema == "GOAL_TRAINING_COHORT_V1":
            source = self._load(session, value.admission_reference, seen)
            if value != self._cohort(session, source):
                raise ValueError("goal cohort differs from admitted normalized facts")
        elif schema == "POISSON_GOALS_STATE_V1":
            for fact in value.cohort.facts:
                if session.scalar(
                    select(MatchResultRecord.match_result_id).where(
                        MatchResultRecord.supersedes_match_result_id
                        == fact.result.match_result_id,
                        MatchResultRecord.available_at_utc
                        < value.request.training_cutoff_at_utc,
                        MatchResultRecord.ingested_at_utc
                        < value.request.training_cutoff_at_utc,
                    )
                ):
                    raise ValueError(
                        "Poisson cannot fall behind a visible corrected fact"
                    )
        elif schema == "LEGACY_THREE_WAY_INPUT_V1":
            if value != self._legacy(
                session, value.analysis_run_id, value.identity.match_id
            ):
                raise ValueError("legacy THREE_WAY source changed")
        elif schema == "ANALYSIS_PACKET_V4":
            analysis = self._load(session, value.analysis.artifact_id, seen)
            if value != export_packet_v4(analysis):
                raise ValueError("V4 export differs from sealed source")
        elif schema == "IMPORTED_LLM_REVIEW_V4":
            if self._load(session, value.packet.artifact_id, seen) != value.packet:
                raise ValueError("import packet mismatch")
        elif schema == "GENERIC_FUSION_RUN_V1":
            analysis = self._load(session, value.analysis.artifact_id, seen)
            review = self._load(session, value.review.artifact_id, seen)
            if value != fuse_v4(analysis, review, value.policy):
                raise ValueError("generic fusion source replay mismatch")
        elif schema == "MARKET_ANALYSIS_UNIT_V1":
            self._identity(session, value.identity)
        elif schema == "OUTCOME_CANDIDATE_V1":
            catalogs = session.info.setdefault("market_v2_outcome_catalogs", {})
            key = (value.analysis.artifact_id, value.fusion.artifact_id)
            if key not in catalogs:
                analysis = self._load(session, key[0], seen)
                fusion = self._load(session, key[1], seen)
                catalogs[key] = {
                    c.artifact_id: c for c in outcome_candidates(analysis, fusion)
                }
            if catalogs[key].get(value.artifact_id) != value:
                raise ValueError("outcome candidate differs from frozen parent gate")
        elif schema == "SYSTEM_TICKET_CANDIDATE_V2":
            source = self._load(session, value.source.artifact_id, seen)
            if source.content_hash != value.source.content_hash or any(
                c not in source.selections
                for choice in value.choice_sets
                for c in choice.candidates
            ):
                raise ValueError("ticket choices differ from frozen source")
        elif schema == "SYSTEM_TICKET_V2":
            from football_system.domain.services.payout import calculate_stake_fen

            source = self._load(session, value.candidate.source.artifact_id, seen)
            if value.stake_fen != calculate_stake_fen(
                value.candidate.expanded_atomic_bet_count,
                value.multiplier,
                source.analysis.rules,
            ):
                raise ValueError("ticket exceeds original source money rules")
        elif schema == "STRATEGY_SETTLEMENT_V2":
            plan = self._load(session, value.plan.artifact_id, seen)
            previous = None
            if value.previous:
                row = _one(
                    session, "mm_settlements", artifact_id=value.previous.artifact_id
                )
                if row["plan_id"] != plan.artifact_id:
                    raise ValueError("settlement correction crosses plan")
                previous = self._load(session, value.previous.artifact_id, seen)
                if previous.content_hash != value.previous.content_hash:
                    raise ValueError("previous settlement hash mismatch")
            actual = self._results(
                session, tuple(r.match_result_id for r in value.results)
            )
            if actual != value.results or value != settle_strategy_v2(
                plan,
                actual,
                value.settled_at_utc,
                issues=value.issues,
                previous=previous,
            ):
                raise ValueError(
                    "expanded settlement differs from normalized score replay"
                )

    def _identity(self, session, identity):
        match = session.get(MatchRecord, identity.match_id)
        season = session.get(CanonicalMatchIdentityRecord, identity.match_id)
        if (
            match is None
            or season is None
            or (
                match.competition_id,
                match.home_team_id,
                match.away_team_id,
                match.kickoff_at_utc,
                season.season,
            )
            != (
                identity.competition_id,
                identity.home_team_id,
                identity.away_team_id,
                identity.kickoff_at_utc,
                identity.season_id,
            )
        ):
            raise ValueError(
                "multi-market identity differs from canonical match/season"
            )
        if (
            session.get(TeamRecord, match.home_team_id).name,
            session.get(TeamRecord, match.away_team_id).name,
        ) != (identity.home_team_name, identity.away_team_name):
            raise ValueError("canonical team label mismatch")

    def admit_cohort(self, source):
        self.save(source)
        with self._sessions() as session:
            value = self._cohort(session, source)
        return self.save(value)

    def _cohort(self, session, source):
        from football_system.application.market_v2 import GoalCohortRequestV1

        if not isinstance(source, OfflineMarketSourceV1):
            raise ValueError("cohort admission requires exact offline source")
        request = GoalCohortRequestV1.model_validate(
            parse_v4_json(source.raw_json.encode())
        )
        if source.source_reference != request.source_reference:
            raise ValueError("cohort raw source reference mismatch")
        facts = []
        for result in self._results(session, request.result_ids):
            if not result.provider_code.startswith(("MOCK", "SYNTHETIC")):
                raise ValueError(
                    "0.8 fixture admission does not authorize real sources"
                )
            if session.scalar(
                select(MatchResultRecord.match_result_id).where(
                    MatchResultRecord.supersedes_match_result_id
                    == result.match_result_id,
                    MatchResultRecord.available_at_utc <= request.admitted_at_utc,
                    MatchResultRecord.ingested_at_utc <= request.admitted_at_utc,
                )
            ):
                raise ValueError("cohort cannot admit a superseded normalized result")
            match = session.get(MatchRecord, result.match_id)
            season = session.get(CanonicalMatchIdentityRecord, result.match_id)
            if (
                match is None
                or season is None
                or (match.competition_id, season.season)
                != (request.competition_id, request.season_id)
            ):
                raise ValueError("goal cohort is not one canonical competition/season")
            facts.append(
                GoalTrainingFactV1(
                    result=result,
                    competition_id=match.competition_id,
                    season_id=season.season,
                    home_team_id=match.home_team_id,
                    away_team_id=match.away_team_id,
                    kickoff_at_utc=match.kickoff_at_utc,
                )
            )
        return GoalTrainingCohortV1.freeze(
            competition_id=request.competition_id,
            season_id=request.season_id,
            facts=tuple(
                sorted(
                    facts,
                    key=lambda f: (
                        f.kickoff_at_utc,
                        f.result.match_id,
                        f.result.match_result_id,
                    ),
                )
            ),
            admitted_at_utc=request.admitted_at_utc,
            admission_reference=source.artifact_id,
            source_artifact_hash=source.content_hash,
        )

    def legacy_threeway(self, analysis_run_id, match_id):
        with self._sessions() as session:
            value = self._legacy(session, analysis_run_id, match_id)
        return self.save(value)

    def _legacy(self, session, run_id, match_id):
        cache = session.info.setdefault("market_v2_legacy_sources", {})
        if (run_id, match_id) in cache:
            return cache[(run_id, match_id)]
        run = _completed_run(session, run_id)
        config = json.loads(run.config_json)
        if config.get("settings", {}).get("runtime", {}).get(
            "environment"
        ) != "mock" or config.get("request", {}).get("production_model_release_id"):
            raise ValueError("0.8 offline legacy adapter requires a synthetic/mock run")
        packets = session.info.setdefault("market_v2_legacy_packets", {})
        if run_id not in packets:
            packets[run_id] = SqlAlchemyReviewArtifactRepository._load_packet_source_v3(
                session, run_id
            )
        source = packets[run_id]
        unit = next((m for m in source.matches if m.match_id == match_id), None)
        if unit is None or not isinstance(unit.p_quant, PacketModelQuantLineageV3):
            raise ValueError(
                "THREE_WAY requires existing Elo model lineage, not manual or Poisson"
            )
        state = next(
            s
            for s in source.quant_model_states
            if s.quant_model_state_id == unit.p_quant.evaluation.quant_model_state_id
        )
        if state.model_name != "ELO_THREE_WAY_BASELINE_V1":
            raise ValueError("unrecognized legacy THREE_WAY model")
        from football_system.domain.services.elo_baseline import EloBaselineConfig

        if state.config_hash != EloBaselineConfig().config_hash:
            raise ValueError("legacy adapter requires the frozen Elo configuration")
        season = session.get(CanonicalMatchIdentityRecord, match_id)
        if season is None:
            raise ValueError("legacy market requires canonical season binding")
        identity = MarketMatchIdentityV1(
            match_id=unit.match_id,
            competition_id=unit.competition_id,
            season_id=season.season,
            home_team_id=unit.home_team_id,
            away_team_id=unit.away_team_id,
            home_team_name=unit.home_team_name,
            away_team_name=unit.away_team_name,
            kickoff_at_utc=unit.kickoff_at_utc,
        )
        record = session.scalar(
            select(FinalPredictionRecord).where(
                FinalPredictionRecord.analysis_run_id == run_id,
                FinalPredictionRecord.internal_match_id == match_id,
            )
        )
        final = _final_prediction(session, record) if record else None
        q = unit.p_quant.prediction
        lineage = MarketModelLineageV1(
            model_name=state.model_name,
            model_version=state.model_version,
            calibration_label=state.calibration_label,
            state_id=state.quant_model_state_id,
            state_hash=state.state_hash,
            config_hash=state.config_hash,
            training_data_hash=state.training_data_hash,
            training_cutoff_at_utc=state.cutoff_at_utc,
            generated_at_utc=state.generated_at_utc,
        )
        value = LegacyThreeWayInputV1.freeze(
            analysis_run_id=run_id,
            analysis_run_hash=content_hash(
                "LEGACY_ANALYSIS_RUN_V1",
                {c.name: getattr(run, c.name) for c in run.__table__.columns},
            ),
            identity=identity,
            decision_cutoff=run.as_of_at_utc,
            model_lineage=lineage,
            p_market=MarketProbabilityDistributionV1.from_three_way(
                unit.p_market.probabilities
            ),
            p_quant=MarketProbabilityDistributionV1.from_three_way(q.probabilities)
            if q
            else None,
            p_base=MarketProbabilityDistributionV1.from_three_way(final.probabilities)
            if final
            else None,
            unavailable_reason=unit.p_quant.evaluation.unavailable_reason,
        )
        cache[(run_id, match_id)] = value
        return value

    def results(self, result_ids):
        with self._sessions() as session:
            return self._results(session, result_ids)

    def _results(self, session, ids):
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate normalized result ID")
        values = []
        for identity in ids:
            record = session.get(MatchResultRecord, identity)
            if record is None:
                raise KeyError("unknown normalized MatchResult")
            values.append(_match_result(session, record))
        return tuple(sorted(values, key=lambda r: r.match_id))


def _rows(session, name, **keys):
    table = Base.metadata.tables[name]
    return (
        session.execute(
            select(table).where(*(table.c[k] == v for k, v in keys.items()))
        )
        .mappings()
        .all()
    )


def _one(session, name, **keys):
    rows = _rows(session, name, **keys)
    if len(rows) != 1:
        raise KeyError(f"unknown/incomplete {name}")
    return rows[0]
