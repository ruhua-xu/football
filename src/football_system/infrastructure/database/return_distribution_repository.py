"""Typed append-only return artifacts with transaction-local source/replay checks."""

from pydantic import BaseModel

from football_system.domain.archive import canonical_json
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import MarketArtifact, revalidate
from football_system.domain.return_distribution import RETURN_ARTIFACT_TYPES, ReturnDistributionPolicyV1
from football_system.domain.services.return_distribution import (
    binding_for, calculate_metrics, evaluate_prepared, prepare_source, relevant_matches, ticket_function,
)
from football_system.domain.services.return_optimizer import optimize_prepared
from football_system.infrastructure.database.market_v2_repository import (
    SqlAlchemyMultiMarketRepository, _one, _rows, at,
)
from football_system.infrastructure.database.models import Base
from football_system.infrastructure.database.return_distribution_schema import (
    TYPE_SPECS, CHILD_SPECS, NESTED_SPECS, SUPPORT_TABLES, MULTIPLIER_TABLES,
)
from football_system.infrastructure.files.return_distribution import (
    MAX_RETURN_ARTIFACT_BYTES, return_code_hash, strict_return_json,
)


def project(value, path):
    return at(value, path) if path else value


def graph_nodes(value):
    if isinstance(value, BaseModel):
        for key in type(value).model_fields:
            yield from graph_nodes(getattr(value, key))
        if isinstance(value, MarketArtifact):
            if type(value) is not RETURN_ARTIFACT_TYPES.get(value.schema_version):
                raise ValueError("unknown return graph artifact type")
            yield value
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from graph_nodes(item)


def rows_for_return(value):
    name, fields = TYPE_SPECS[value.schema_version]
    header = dict(artifact_id=value.artifact_id, schema_version=value.schema_version,
                  content_hash=value.content_hash, artifact_json=canonical_json(value))
    typed = dict(artifact_id=value.artifact_id, **{key: project(value, path) for key, (path, *_) in fields.items()})
    if name == "rd_match_states":
        typed["market_hash"] = value.market_key.market_hash
    result = {"rd_artifacts": [header], name: [typed]}

    def child(item, position, table, fields, group=None):
        row = dict(parent_id=value.artifact_id, position=position, item_json=canonical_json(item))
        if group is not None:
            row["group_no"] = group
        row.update({key: project(item, path) for key, (path, *_) in fields.items()})
        if table in SUPPORT_TABLES:
            row.update(gross_payout_fen=item.gross_payout_fen, probability=str(item.probability))
        if table == "rd_relevant_states":
            row.update(outcome_key=item.outcome.value if item.outcome is not None else None,
                       probability=str(item.probability), market_hash=value.market_key.market_hash)
        if table in MULTIPLIER_TABLES:
            row["multiplier"] = item.multiplier
        if table == "rd_selected_tickets":
            row["role"] = item.role.value
        return row

    for kind, path, table, fields in CHILD_SPECS:
        if value.schema_version == kind:
            result[table] = [child(item, i, table, fields) for i, item in enumerate(project(value, path))]
    for kind, outer, inner, table, _, fields in NESTED_SPECS:
        if value.schema_version == kind:
            result[table] = [child(item, i, table, fields, group=g) for g, parent in enumerate(project(value, outer))
                             for i, item in enumerate(project(parent, inner))]
    result["rd_seals"] = [dict(artifact_id=value.artifact_id)]
    return result


class SqlAlchemyReturnDistributionRepository:
    def __init__(self, sessions):
        self._sessions = sessions
        self._old = SqlAlchemyMultiMarketRepository(sessions)

    def load_plan(self, plan_id):
        return self._old.load(plan_id, "STRATEGY_PASS_PLAN_V2")

    def _prepared(self, session, binding):
        cached = session.info.setdefault("rd_prepared_sources", {})
        identity = binding.plan.artifact_id
        if identity not in cached:
            plan = self._old._load(session, identity)
            if plan.schema_version != "STRATEGY_PASS_PLAN_V2":
                raise ValueError("return source must be a sealed V2 plan")
            cached[identity] = prepare_source(plan)
        prepared = cached[identity]
        if prepared.binding != binding or binding_for(prepared.plan) != binding:
            raise ValueError("return binding differs from exact source plan/catalog/rules/budget")
        return prepared

    def _old_ref(self, session, reference, schema):
        value = self._old._load(session, reference.artifact_id)
        if value.schema_version != schema or ArtifactRefV1.of(value) != reference:
            raise ValueError("return old source reference hash/type mismatch")
        return value

    def _replay(self, session, value, stack=None):
        kind = value.schema_version
        if kind == "RETURN_DISTRIBUTION_POLICY_V1":
            if value.code_hash != return_code_hash():
                raise ValueError("return implementation code identity unavailable")
        elif kind == "RETURN_OBJECTIVE_PROFILE_V1":
            return
        elif kind == "RELEVANT_MATCH_STATE_V1":
            fusion = self._old_ref(session, value.fusion, "GENERIC_FUSION_RUN_V1")
            unit = self._old_ref(session, value.unit, "MARKET_ANALYSIS_UNIT_V1")
            actual = next((r for r in fusion.results if r.unit_id == value.unit.artifact_id), None)
            if (actual is None or actual.p_final != value.marginal or actual.match_id != value.match_id
                    or actual.market_key != value.market_key or value.sp_snapshot != ArtifactRefV1.of(unit.sporttery)):
                raise ValueError("relevant state differs from sealed P_final/SP unit")
        elif kind == "TICKET_RETURN_FUNCTION_V1":
            candidate = self._old_ref(session, value.candidate, "SYSTEM_TICKET_CANDIDATE_V2")
            if ticket_function(candidate) != value:
                raise ValueError("return function changed sealed atomic payout/graph")
        elif kind == "PORTFOLIO_RETURN_DISTRIBUTION_V1":
            prepared = self._prepared(session, value.binding)
            if any(a.ticket_candidate_id not in prepared.catalog for a in value.allocations):
                raise ValueError("distribution selected a non-catalog candidate")
            candidates = tuple(prepared.catalog[a.ticket_candidate_id] for a in value.allocations)
            if (value.matches != relevant_matches(prepared, candidates, value.policy)
                    or value.ticket_functions != tuple(ticket_function(c) for c in candidates)):
                raise ValueError("distribution differs from exact P_final/SP/candidate source")
        elif kind == "RETURN_DISTRIBUTION_METRICS_V1":
            distribution = self._load(session, value.distribution.artifact_id, stack)
            if (distribution.schema_version != "PORTFOLIO_RETURN_DISTRIBUTION_V1"
                    or ArtifactRefV1.of(distribution) != value.distribution or calculate_metrics(distribution) != value):
                raise ValueError("metrics differ from support replay")
        elif kind == "RETURN_EVALUATION_V1":
            expected = evaluate_prepared(self._prepared(session, value.binding), value.requested, value.policy, value.objective)
            if expected != value:
                raise ValueError("evaluation differs from source/risk/role replay")
        elif kind == "RETURN_OPTIMIZATION_RUN_V1":
            expected = optimize_prepared(self._prepared(session, value.binding), value.policy, value.objective)
            if expected != value:
                raise ValueError("optimizer differs from deterministic full replay")
        else:
            raise ValueError("unsupported return artifact")

    def _verify_rows(self, session, artifact):
        typed = TYPE_SPECS[artifact.schema_version][0]
        for name, expected in rows_for_return(artifact).items():
            key = "artifact_id" if name in {"rd_artifacts", "rd_seals", typed} else "parent_id"
            rows = _rows(session, name, **{key: artifact.artifact_id})
            if sorted(canonical_json(dict(r)) for r in rows) != sorted(canonical_json(r) for r in expected):
                raise ValueError(f"corrupt return graph: {name}")

    def save(self, artifact):
        if type(artifact) is not RETURN_ARTIFACT_TYPES.get(artifact.schema_version):
            raise ValueError("unsupported return artifact")
        artifact = revalidate(artifact)
        if len(canonical_json(artifact).encode()) > MAX_RETURN_ARTIFACT_BYTES:
            raise ValueError("DISTRIBUTION_COMPLEXITY_LIMIT: serialized artifact bytes")
        with self._sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            nodes = {}
            for node in graph_nodes(artifact):
                if node.artifact_id in nodes and nodes[node.artifact_id] != node:
                    raise ValueError("conflicting embedded return artifact")
                nodes[node.artifact_id] = node
                if isinstance(node, ReturnDistributionPolicyV1):
                    self._replay(session, node)
            # Full source/math/role/search replay before the first INSERT.
            self._replay(session, artifact)
            for node in nodes.values():
                if _rows(session, "rd_artifacts", artifact_id=node.artifact_id):
                    self._verify_rows(session, node)
                    continue
                for table, rows in rows_for_return(node).items():
                    if rows:
                        session.execute(Base.metadata.tables[table].insert(), rows)
                self._verify_rows(session, node)
            return artifact

    def _load(self, session, identity, stack=None):
        cache = session.info.setdefault("rd_verified_artifacts", {})
        if identity in cache:
            return cache[identity]
        stack = set() if stack is None else stack
        if identity in stack:
            raise ValueError("cyclic return artifact reference")
        stack.add(identity)
        row = _one(session, "rd_artifacts", artifact_id=identity)
        cls = RETURN_ARTIFACT_TYPES.get(row["schema_version"])
        if cls is None:
            raise ValueError("unknown return artifact version")
        value = cls.model_validate(strict_return_json(row["artifact_json"].encode(), limit=MAX_RETURN_ARTIFACT_BYTES))
        for node in graph_nodes(value):
            self._verify_rows(session, node)
            if isinstance(node, ReturnDistributionPolicyV1):
                self._replay(session, node)
        self._replay(session, value, stack)
        stack.remove(identity)
        cache[identity] = value
        return value

    def load(self, identity):
        with self._sessions.begin() as session:
            return self._load(session, identity)
