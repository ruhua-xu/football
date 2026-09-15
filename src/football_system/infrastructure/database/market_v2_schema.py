"""Additive closed-type market graph for migration 39526d8f40cb.

Registry headers require deferred completeness seals. All semantic references
also have concrete-type foreign keys; JSON alone is not a lineage key.
"""

import sqlalchemy as sa

# column: (JSON path, FK table, FK column, nullable)
TYPE_SPECS = {
    "OFFLINE_MARKET_SOURCE_V1": ("mm_sources", {}),
    "GOAL_TRAINING_COHORT_V1": (
        "mm_goal_cohorts",
        {
            "source_id": ("admission_reference", "mm_sources", "artifact_id", False),
            "competition_id": (
                "competition_id",
                "competitions",
                "competition_id",
                False,
            ),
        },
    ),
    "POISSON_SCORE_GRID_V1": ("mm_score_grids", {}),
    "POISSON_GOALS_STATE_V1": (
        "mm_goal_states",
        {
            "cohort_id": (
                "cohort.artifact_id",
                "mm_goal_cohorts",
                "artifact_id",
                False,
            ),
            "grid_id": ("grid.artifact_id", "mm_score_grids", "artifact_id", True),
            "match_id": ("request.match_id", "matches", "internal_match_id", False),
        },
    ),
    "FOOTBALL_EVIDENCE_V1": (
        "mm_evidence",
        {"match_id": ("match_id", "matches", "internal_match_id", False)},
    ),
    "MARKET_ODDS_SNAPSHOT_V2": (
        "mm_market_odds",
        {
            "match_id": ("match_id", "matches", "internal_match_id", False),
            "source_id": ("source_artifact_id", "mm_sources", "artifact_id", False),
        },
    ),
    "SPORTTERY_FIXED_BONUS_SNAPSHOT_V2": (
        "mm_sporttery_sp",
        {
            "match_id": ("match_id", "matches", "internal_match_id", False),
            "source_id": ("source_artifact_id", "mm_sources", "artifact_id", False),
        },
    ),
    "MARKET_CONSENSUS_V2": (
        "mm_consensus",
        {"match_id": ("match_id", "matches", "internal_match_id", False)},
    ),
    "LEGACY_THREE_WAY_INPUT_V1": (
        "mm_legacy_threeway",
        {
            "run_id": ("analysis_run_id", "analysis_runs", "analysis_run_id", False),
            "state_id": (
                "model_lineage.state_id",
                "quant_model_states",
                "quant_model_state_id",
                False,
            ),
            "match_id": ("identity.match_id", "matches", "internal_match_id", False),
        },
    ),
    "MARKET_ANALYSIS_UNIT_V1": (
        "mm_analysis_units",
        {
            "match_id": ("identity.match_id", "matches", "internal_match_id", False),
            "sp_id": ("sporttery.artifact_id", "mm_sporttery_sp", "artifact_id", False),
            "consensus_id": (
                "consensus.artifact_id",
                "mm_consensus",
                "artifact_id",
                True,
            ),
            "goal_id": (
                "goal_state.artifact_id",
                "mm_goal_states",
                "artifact_id",
                True,
            ),
            "legacy_id": (
                "legacy_source.artifact_id",
                "mm_legacy_threeway",
                "artifact_id",
                True,
            ),
        },
    ),
    "MULTI_MARKET_ANALYSIS_V1": ("mm_analyses", {}),
    "MARKET_REVIEW_CONTEXT_V4": (
        "mm_review_contexts",
        {"match_id": ("identity.match_id", "matches", "internal_match_id", False)},
    ),
    "ANALYSIS_PACKET_V4": (
        "mm_packets",
        {"analysis_id": ("analysis.artifact_id", "mm_analyses", "artifact_id", False)},
    ),
    "IMPORTED_LLM_REVIEW_V4": (
        "mm_reviews",
        {"packet_id": ("packet.artifact_id", "mm_packets", "artifact_id", False)},
    ),
    "GENERIC_FUSION_RUN_V1": (
        "mm_fusions",
        {
            "analysis_id": (
                "analysis.artifact_id",
                "mm_analyses",
                "artifact_id",
                False,
            ),
            "review_id": ("review.artifact_id", "mm_reviews", "artifact_id", False),
        },
    ),
    "OUTCOME_CANDIDATE_V1": (
        "mm_outcome_candidates",
        {
            "analysis_id": (
                "analysis.artifact_id",
                "mm_analyses",
                "artifact_id",
                False,
            ),
            "fusion_id": ("fusion.artifact_id", "mm_fusions", "artifact_id", False),
            "unit_id": ("unit_id", "mm_analysis_units", "artifact_id", False),
            "sp_id": ("snapshot.artifact_id", "mm_sporttery_sp", "artifact_id", False),
            "match_id": ("match_id", "matches", "internal_match_id", False),
        },
    ),
    "STRATEGY_SOURCE_V2": (
        "mm_strategy_sources",
        {
            "analysis_id": (
                "analysis.artifact_id",
                "mm_analyses",
                "artifact_id",
                False,
            ),
            "review_id": ("review.artifact_id", "mm_reviews", "artifact_id", False),
            "fusion_id": ("fusion.artifact_id", "mm_fusions", "artifact_id", False),
        },
    ),
    "MATCH_CHOICE_SET_V1": (
        "mm_choice_sets",
        {"match_id": ("match_id", "matches", "internal_match_id", False)},
    ),
    "EXPANDED_ATOMIC_BET_V2": ("mm_atomic_bets", {}),
    "SYSTEM_TICKET_CANDIDATE_V2": (
        "mm_ticket_candidates",
        {
            "source_id": (
                "source.artifact_id",
                "mm_strategy_sources",
                "artifact_id",
                False,
            )
        },
    ),
    "SYSTEM_TICKET_V2": (
        "mm_tickets",
        {
            "candidate_id": (
                "candidate.artifact_id",
                "mm_ticket_candidates",
                "artifact_id",
                False,
            )
        },
    ),
    "STRATEGY_PASS_PLAN_V2": (
        "mm_plans",
        {
            "source_id": (
                "source.artifact_id",
                "mm_strategy_sources",
                "artifact_id",
                False,
            )
        },
    ),
    "STRATEGY_SETTLEMENT_V2": (
        "mm_settlements",
        {
            "plan_id": ("plan.artifact_id", "mm_plans", "artifact_id", False),
            "previous_id": (
                "previous.artifact_id",
                "mm_settlements",
                "artifact_id",
                True,
            ),
        },
    ),
}
# schema, JSON collection path, child table, projected child keys + concrete FKs
CHILD_SPECS = (
    (
        "GOAL_TRAINING_COHORT_V1",
        "facts",
        "mm_goal_facts",
        {
            "result_id": (
                "result.match_result_id",
                "match_results",
                "match_result_id",
                False,
            ),
            "match_id": ("result.match_id", "matches", "internal_match_id", False),
        },
    ),
    ("MARKET_ODDS_SNAPSHOT_V2", "prices", "mm_odds_prices", {}),
    ("SPORTTERY_FIXED_BONUS_SNAPSHOT_V2", "prices", "mm_sp_prices", {}),
    (
        "MARKET_CONSENSUS_V2",
        "snapshots",
        "mm_consensus_books",
        {"target_id": ("artifact_id", "mm_market_odds", "artifact_id", False)},
    ),
    (
        "MARKET_ANALYSIS_UNIT_V1",
        "evidence",
        "mm_unit_evidence",
        {"target_id": ("artifact_id", "mm_evidence", "artifact_id", False)},
    ),
    (
        "MULTI_MARKET_ANALYSIS_V1",
        "units",
        "mm_analysis_members",
        {"target_id": ("artifact_id", "mm_analysis_units", "artifact_id", False)},
    ),
    (
        "MARKET_REVIEW_CONTEXT_V4",
        "evidence",
        "mm_context_evidence",
        {"target_id": ("artifact_id", "mm_evidence", "artifact_id", False)},
    ),
    (
        "ANALYSIS_PACKET_V4",
        "market_units",
        "mm_packet_units",
        {
            "target_id": (
                "review_context.artifact_id",
                "mm_review_contexts",
                "artifact_id",
                False,
            )
        },
    ),
    (
        "IMPORTED_LLM_REVIEW_V4",
        "submission.market_reviews",
        "mm_review_units",
        {
            "target_id": (
                "review_context_id",
                "mm_review_contexts",
                "artifact_id",
                False,
            )
        },
    ),
    (
        "GENERIC_FUSION_RUN_V1",
        "results",
        "mm_fusion_results",
        {"target_id": ("unit_id", "mm_analysis_units", "artifact_id", False)},
    ),
    (
        "STRATEGY_SOURCE_V2",
        "selections",
        "mm_source_selections",
        {"target_id": ("artifact_id", "mm_outcome_candidates", "artifact_id", False)},
    ),
    (
        "MATCH_CHOICE_SET_V1",
        "candidates",
        "mm_choice_outcomes",
        {
            "target_id": ("artifact_id", "mm_outcome_candidates", "artifact_id", False),
            "match_id": ("match_id", "matches", "internal_match_id", False),
        },
    ),
    (
        "EXPANDED_ATOMIC_BET_V2",
        "legs",
        "mm_atomic_legs",
        {
            "target_id": ("artifact_id", "mm_outcome_candidates", "artifact_id", False),
            "match_id": ("match_id", "matches", "internal_match_id", False),
        },
    ),
    (
        "SYSTEM_TICKET_CANDIDATE_V2",
        "choice_sets",
        "mm_candidate_choices",
        {
            "target_id": ("artifact_id", "mm_choice_sets", "artifact_id", False),
            "match_id": ("match_id", "matches", "internal_match_id", False),
        },
    ),
    (
        "SYSTEM_TICKET_CANDIDATE_V2",
        "atomic_bets",
        "mm_candidate_atomics",
        {"target_id": ("artifact_id", "mm_atomic_bets", "artifact_id", False)},
    ),
    (
        "STRATEGY_PASS_PLAN_V2",
        "candidates",
        "mm_plan_candidates",
        {"target_id": ("artifact_id", "mm_ticket_candidates", "artifact_id", False)},
    ),
    (
        "STRATEGY_PASS_PLAN_V2",
        "tickets",
        "mm_plan_tickets",
        {"target_id": ("artifact_id", "mm_tickets", "artifact_id", False)},
    ),
    (
        "STRATEGY_SETTLEMENT_V2",
        "results",
        "mm_settlement_results",
        {
            "result_id": ("match_result_id", "match_results", "match_result_id", False),
            "match_id": ("match_id", "matches", "internal_match_id", False),
        },
    ),
    (
        "STRATEGY_SETTLEMENT_V2",
        "tickets",
        "mm_settlement_tickets",
        {"target_id": ("ticket_id", "mm_tickets", "artifact_id", False)},
    ),
)
MARKET_SCHEMAS = {
    "MARKET_ODDS_SNAPSHOT_V2",
    "SPORTTERY_FIXED_BONUS_SNAPSHOT_V2",
    "MARKET_CONSENSUS_V2",
    "MARKET_ANALYSIS_UNIT_V1",
    "MARKET_REVIEW_CONTEXT_V4",
    "OUTCOME_CANDIDATE_V1",
    "MATCH_CHOICE_SET_V1",
}
MARKET_V2_TABLES = (
    "mm_market_keys",
    "mm_outcome_catalog",
    "mm_catalog_seals",
    "mm_artifacts",
    *(v[0] for v in TYPE_SPECS.values()),
    *(x[2] for x in CHILD_SPECS),
    "mm_artifact_seals",
)


def market_v2_tables(metadata):
    def c(name, type_=sa.String(160), **kw):
        return sa.Column(name, type_, nullable=kw.pop("nullable", False), **kw)

    def fk(keys, table, targets=None, alter=False):
        return sa.ForeignKeyConstraint(
            keys,
            [f"{table}.{k}" for k in (targets or keys)],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=alter,
        )

    for table, key in (
        ("matches", "internal_match_id"),
        ("match_results", "match_result_id"),
        ("competitions", "competition_id"),
        ("analysis_runs", "analysis_run_id"),
        ("quant_model_states", "quant_model_state_id"),
    ):
        if table not in metadata.tables:
            sa.Table(table, metadata, c(key, primary_key=True))
    tables = {}

    def t(name, *items):
        tables[name] = sa.Table(name, metadata, *items)
        return tables[name]

    t(
        "mm_market_keys",
        c("market_hash", primary_key=True),
        c("canonical", unique=True),
        c("market_json", sa.Text()),
        c("catalog_json", sa.Text()),
        fk(["market_hash"], "mm_catalog_seals", alter=True),
        sa.CheckConstraint(
            "json_valid(market_json) AND json_valid(catalog_json) AND json_array_length(catalog_json) IN (3,8,31)"
        ),
    )
    t(
        "mm_outcome_catalog",
        c("market_hash", primary_key=True),
        c("outcome_key", primary_key=True),
        c("outcome_no", sa.Integer()),
        fk(["market_hash"], "mm_market_keys"),
        sa.UniqueConstraint("market_hash", "outcome_no"),
        sa.CheckConstraint("outcome_no BETWEEN 0 AND 30"),
    )
    t(
        "mm_catalog_seals",
        c("market_hash", primary_key=True),
        fk(["market_hash"], "mm_market_keys"),
    )
    versions = ",".join(f"'{s}'" for s in TYPE_SPECS)
    t(
        "mm_artifacts",
        c("artifact_id", primary_key=True),
        c("schema_version"),
        c("content_hash", sa.String(64), unique=True),
        c("artifact_json", sa.Text()),
        fk(["artifact_id"], "mm_artifact_seals", alter=True),
        sa.CheckConstraint(f"schema_version IN ({versions})"),
        sa.CheckConstraint(
            "json_valid(artifact_json) AND json_type(artifact_json)='object' AND length(content_hash)=64 AND content_hash NOT GLOB '*[^0-9a-f]*'"
        ),
    )
    for schema, (name, fields) in TYPE_SPECS.items():
        columns = [
            c("artifact_id", primary_key=True),
            fk(["artifact_id"], "mm_artifacts"),
        ]
        for col, (_, target, key, nullable) in fields.items():
            columns.extend((c(col, nullable=nullable), fk([col], target, [key])))
        if schema in MARKET_SCHEMAS:
            columns.extend((c("market_hash"), fk(["market_hash"], "mm_market_keys")))
        if name == "mm_outcome_candidates":
            columns.extend(
                (
                    c("outcome_key"),
                    fk(["market_hash", "outcome_key"], "mm_outcome_catalog"),
                )
            )
        if name == "mm_settlements":
            columns.append(sa.UniqueConstraint("previous_id"))
        t(name, *columns)
    for schema, path, name, fields in CHILD_SPECS:
        columns = [
            c("parent_id", primary_key=True),
            c("position", sa.Integer(), primary_key=True),
            c("item_json", sa.Text()),
            fk(["parent_id"], TYPE_SPECS[schema][0], ["artifact_id"]),
            sa.CheckConstraint("position>=0 AND json_valid(item_json)"),
        ]
        for col, (_, target, key, nullable) in fields.items():
            columns.extend((c(col, nullable=nullable), fk([col], target, [key])))
        if "target_id" in fields:
            columns.append(sa.UniqueConstraint("parent_id", "target_id"))
        if name in {
            "mm_atomic_legs",
            "mm_candidate_choices",
            "mm_goal_facts",
            "mm_settlement_results",
        }:
            columns.append(sa.UniqueConstraint("parent_id", "match_id"))
        if name in {"mm_odds_prices", "mm_sp_prices"}:
            columns.extend(
                (
                    c("market_hash"),
                    c("outcome_key"),
                    c("price", sa.Text()),
                    fk(["market_hash", "outcome_key"], "mm_outcome_catalog"),
                    sa.UniqueConstraint("parent_id", "outcome_key"),
                    sa.CheckConstraint("CAST(price AS NUMERIC)>1"),
                )
            )
        t(name, *columns)
    t(
        "mm_artifact_seals",
        c("artifact_id", primary_key=True),
        fk(["artifact_id"], "mm_artifacts"),
    )
    return tables


def market_v2_triggers():
    sql = {}

    def trigger(name, table, when, operation="INSERT"):
        sql[name] = (
            f"CREATE TRIGGER {name} BEFORE {operation} ON {table} WHEN {when} BEGIN SELECT RAISE(ABORT,'immutable multi-market graph violation'); END"
        )

    for name, table in market_v2_tables(sa.MetaData()).items():
        for op in ("UPDATE", "DELETE"):
            trigger(f"trg_{name}_{op.lower()}_v1", name, "1", op)
        keys = " AND ".join(
            f"x.{c.name}=NEW.{c.name}" for c in table.primary_key.columns
        )
        trigger(
            f"trg_{name}_replace_v1",
            name,
            f"EXISTS(SELECT 1 FROM {name} x WHERE {keys})",
        )
    trigger(
        "trg_mm_header_v1",
        "mm_artifacts",
        "json_extract(NEW.artifact_json,'$.schema_version') IS NOT NEW.schema_version OR json_extract(NEW.artifact_json,'$.artifact_id') IS NOT NEW.artifact_id OR json_extract(NEW.artifact_json,'$.content_hash') IS NOT NEW.content_hash",
    )
    trigger(
        "trg_mm_catalog_item_v1",
        "mm_outcome_catalog",
        "EXISTS(SELECT 1 FROM mm_catalog_seals s WHERE s.market_hash=NEW.market_hash) OR NOT EXISTS(SELECT 1 FROM mm_market_keys k WHERE k.market_hash=NEW.market_hash AND json_extract(k.catalog_json,'$['||NEW.outcome_no||']')=NEW.outcome_key)",
    )
    trigger(
        "trg_mm_catalog_complete_v1",
        "mm_catalog_seals",
        "NOT EXISTS(SELECT 1 FROM mm_market_keys k WHERE k.market_hash=NEW.market_hash AND (SELECT COUNT(*) FROM mm_outcome_catalog c WHERE c.market_hash=k.market_hash)=json_array_length(k.catalog_json))",
    )
    for schema, (name, fields) in TYPE_SPECS.items():
        checks = [f"a.schema_version='{schema}'"]
        checks.extend(
            f"NEW.{col} IS json_extract(a.artifact_json,'$.{path}')"
            for col, (path, *_) in fields.items()
        )
        if schema in MARKET_SCHEMAS:
            checks.append(
                "EXISTS(SELECT 1 FROM mm_market_keys k WHERE k.market_hash=NEW.market_hash AND json(k.market_json)=json(json_extract(a.artifact_json,'$.market_key')))"
            )
        if name == "mm_outcome_candidates":
            checks.append(
                "NEW.outcome_key IS json_extract(a.artifact_json,'$.outcome')"
            )
        trigger(
            f"trg_{name}_binding_v1",
            name,
            f"EXISTS(SELECT 1 FROM mm_artifact_seals s WHERE s.artifact_id=NEW.artifact_id) OR NOT EXISTS(SELECT 1 FROM mm_artifacts a WHERE a.artifact_id=NEW.artifact_id AND {' AND '.join(checks)})",
        )
    for schema, path, name, fields in CHILD_SPECS:
        checks = [
            f"a.schema_version='{schema}'",
            f"json(NEW.item_json)=json(json_extract(a.artifact_json,'$.{path}['||NEW.position||']'))",
        ]
        checks.extend(
            f"NEW.{col} IS json_extract(NEW.item_json,'$.{p}')"
            for col, (p, *_) in fields.items()
        )
        if name in {"mm_odds_prices", "mm_sp_prices"}:
            checks.extend(
                (
                    "NEW.outcome_key IS json_extract(NEW.item_json,'$.outcome')",
                    "NEW.price IS json_extract(NEW.item_json,'$.price')",
                    f"NEW.market_hash=(SELECT market_hash FROM {TYPE_SPECS[schema][0]} WHERE artifact_id=NEW.parent_id)",
                )
            )
        trigger(
            f"trg_{name}_binding_v1",
            name,
            f"EXISTS(SELECT 1 FROM mm_artifact_seals s WHERE s.artifact_id=NEW.parent_id) OR NOT EXISTS(SELECT 1 FROM mm_artifacts a WHERE a.artifact_id=NEW.parent_id AND {' AND '.join(checks)})",
        )
    cases = []
    for schema, (table, _) in TYPE_SPECS.items():
        checks = [
            f"a.schema_version='{schema}'",
            f"EXISTS(SELECT 1 FROM {table} t WHERE t.artifact_id=a.artifact_id)",
        ]
        checks.extend(
            f"(SELECT COUNT(*) FROM {child} c WHERE c.parent_id=a.artifact_id)=json_array_length(a.artifact_json,'$.{path}')"
            for kind, path, child, _ in CHILD_SPECS
            if kind == schema
        )
        cases.append("(" + " AND ".join(checks) + ")")
    trigger(
        "trg_mm_complete_v1",
        "mm_artifact_seals",
        "NOT EXISTS(SELECT 1 FROM mm_artifacts a WHERE a.artifact_id=NEW.artifact_id AND ("
        + " OR ".join(cases)
        + "))",
    )
    trigger(
        "trg_mm_atomic_match_v1",
        "mm_atomic_legs",
        "NOT EXISTS(SELECT 1 FROM mm_outcome_candidates c JOIN mm_artifacts a ON a.artifact_id=c.artifact_id WHERE c.artifact_id=NEW.target_id AND c.match_id=NEW.match_id AND json_extract(a.artifact_json,'$.status')='ELIGIBLE')",
    )
    trigger(
        "trg_mm_choice_market_v1",
        "mm_choice_outcomes",
        "NOT EXISTS(SELECT 1 FROM mm_choice_sets s JOIN mm_outcome_candidates c ON c.artifact_id=NEW.target_id JOIN mm_artifacts a ON a.artifact_id=c.artifact_id WHERE s.artifact_id=NEW.parent_id AND s.match_id=c.match_id AND s.market_hash=c.market_hash AND json_extract(a.artifact_json,'$.status')='ELIGIBLE')",
    )
    trigger(
        "trg_mm_settlement_chain_v1",
        "mm_settlements",
        "(NEW.previous_id IS NULL AND EXISTS(SELECT 1 FROM mm_settlements s WHERE s.plan_id=NEW.plan_id)) OR (NEW.previous_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM mm_settlements s WHERE s.artifact_id=NEW.previous_id AND s.plan_id=NEW.plan_id))",
    )
    return sql


def install_market_v2_triggers(connection):
    if connection.scalar(
        sa.text(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='mm_artifacts'"
        )
    ):
        for name, sql in market_v2_triggers().items():
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
            connection.exec_driver_sql(sql)
