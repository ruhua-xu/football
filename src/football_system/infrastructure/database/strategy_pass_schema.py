"""Additive SQLite schema V1. Headers cannot commit without complete child seals."""

import sqlalchemy as sa

PLAN = "strategy_pass_plans"
SEAL = "strategy_pass_plan_seals"
SELECTION = "strategy_pass_selections"
TICKET = "system_tickets"
ATOMIC = "atomic_bets"
LEG = "atomic_bet_legs"
SETTLEMENT = "strategy_pass_settlements"
RESULT = "strategy_pass_settlement_results"
RESULT_SEAL = "strategy_pass_settlement_seals"
STRATEGY_TABLES = (
    PLAN,
    SELECTION,
    TICKET,
    ATOMIC,
    LEG,
    SEAL,
    SETTLEMENT,
    RESULT,
    RESULT_SEAL,
)


def strategy_pass_tables_v1(metadata):
    def c(name, type_=sa.String(160), **kw):
        return sa.Column(name, type_, nullable=kw.pop("nullable", False), **kw)

    for name, key in (
        ("analysis_runs", "analysis_run_id"),
        ("portfolio_revisions", "portfolio_revision_id"),
        ("fusion_runs", "fusion_run_id"),
        ("bet_candidates", "candidate_id"),
        ("matches", "internal_match_id"),
        ("sporttery_bonus_snapshots", "snapshot_id"),
        ("match_results", "match_result_id"),
    ):
        if name not in metadata.tables:
            sa.Table(name, metadata, c(key, primary_key=True))

    def fk(keys, table, targets=None):
        return sa.ForeignKeyConstraint(
            keys,
            [f"{table}.{k}" for k in (targets or keys)],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=table in {SEAL, RESULT_SEAL},
        )

    def table(name, *items):
        return sa.Table(name, metadata, *items)

    def json_col():
        return c("artifact_json", sa.Text())

    def valid_json():
        return sa.CheckConstraint(
            "json_valid(artifact_json) AND json_type(artifact_json)='object'"
        )

    plan = table(
        PLAN,
        c("plan_id", primary_key=True),
        c("plan_hash", sa.String(64), unique=True),
        c("parent_analysis_run_id"),
        c("source_kind"),
        c("source_id"),
        c("portfolio_revision_id", nullable=True),
        c("fusion_run_id", nullable=True),
        c("budget_fen", sa.Integer()),
        c("total_stake_fen", sa.Integer()),
        c("ticket_count", sa.Integer()),
        json_col(),
        valid_json(),
        fk(["parent_analysis_run_id"], "analysis_runs", ["analysis_run_id"]),
        fk(["portfolio_revision_id"], "portfolio_revisions"),
        fk(["fusion_run_id"], "fusion_runs"),
        fk(["plan_id"], SEAL),
        sa.CheckConstraint(
            "typeof(budget_fen)='integer' AND budget_fen>=0 AND typeof(total_stake_fen)='integer' AND total_stake_fen BETWEEN 0 AND budget_fen AND ticket_count BETWEEN 0 AND 8"
        ),
        sa.CheckConstraint("length(plan_hash)=64 AND plan_hash NOT GLOB '*[^0-9a-f]*'"),
    )
    selection = table(
        SELECTION,
        c("plan_id", primary_key=True),
        c("selection_id", primary_key=True),
        c("selection_no", sa.Integer()),
        c("base_candidate_id", nullable=True),
        c("internal_match_id"),
        c("snapshot_id"),
        c("status"),
        json_col(),
        valid_json(),
        fk(["plan_id"], PLAN),
        fk(["base_candidate_id"], "bet_candidates", ["candidate_id"]),
        fk(["internal_match_id"], "matches"),
        fk(["snapshot_id"], "sporttery_bonus_snapshots"),
        sa.UniqueConstraint("plan_id", "selection_no"),
        sa.UniqueConstraint(
            "plan_id", "selection_id", "internal_match_id", "snapshot_id"
        ),
        sa.CheckConstraint("selection_no>=0 AND status IN ('ELIGIBLE','REJECTED')"),
    )
    ticket = table(
        TICKET,
        c("plan_id", primary_key=True),
        c("ticket_id", primary_key=True),
        c("ticket_no", sa.Integer()),
        c("candidate_id"),
        c("equivalence_hash", sa.String(64)),
        c("pass_type"),
        c("role"),
        c("multiplier", sa.Integer()),
        c("stake_fen", sa.Integer()),
        c("max_payout_fen", sa.Integer()),
        c("atomic_count", sa.Integer()),
        json_col(),
        valid_json(),
        fk(["plan_id"], PLAN),
        sa.UniqueConstraint("plan_id", "ticket_no"),
        sa.UniqueConstraint("plan_id", "equivalence_hash"),
        sa.UniqueConstraint("plan_id", "candidate_id"),
        sa.CheckConstraint(
            "ticket_no BETWEEN 1 AND 8 AND typeof(multiplier)='integer' AND multiplier BETWEEN 1 AND 50 AND typeof(stake_fen)='integer' AND stake_fen=200*atomic_count*multiplier AND stake_fen<=600000 AND max_payout_fen>0"
        ),
        sa.CheckConstraint(
            "(pass_type='2X1' AND atomic_count=1) OR (pass_type='3X4' AND atomic_count=4) OR (pass_type='4X11' AND atomic_count=11)"
        ),
        sa.CheckConstraint("role IN ('PRIMARY','SECONDARY','HEDGE','LONGSHOT')"),
    )
    atomic = table(
        ATOMIC,
        c("plan_id", primary_key=True),
        c("ticket_id", primary_key=True),
        c("atomic_bet_id", primary_key=True),
        c("atomic_no", sa.Integer()),
        c("leg_count", sa.Integer()),
        c("gross_payout_fen", sa.Integer()),
        json_col(),
        valid_json(),
        fk(["plan_id", "ticket_id"], TICKET),
        sa.UniqueConstraint("plan_id", "ticket_id", "atomic_no"),
        sa.CheckConstraint(
            "atomic_no BETWEEN 0 AND 10 AND leg_count BETWEEN 2 AND 4 AND typeof(gross_payout_fen)='integer' AND gross_payout_fen>0"
        ),
    )
    leg = table(
        LEG,
        c("plan_id", primary_key=True),
        c("ticket_id", primary_key=True),
        c("atomic_bet_id", primary_key=True),
        c("leg_no", sa.Integer(), primary_key=True),
        c("selection_id"),
        c("internal_match_id"),
        c("snapshot_id"),
        c("fixed_bonus", sa.Text()),
        fk(["plan_id", "ticket_id", "atomic_bet_id"], ATOMIC),
        fk(["plan_id", "selection_id", "internal_match_id", "snapshot_id"], SELECTION),
        sa.UniqueConstraint(
            "plan_id", "ticket_id", "atomic_bet_id", "internal_match_id"
        ),
        sa.CheckConstraint("leg_no BETWEEN 0 AND 3 AND CAST(fixed_bonus AS NUMERIC)>1"),
    )
    seal = table(SEAL, c("plan_id", primary_key=True), fk(["plan_id"], PLAN))
    settlement = table(
        SETTLEMENT,
        c("settlement_id", primary_key=True),
        c("plan_id"),
        c("settlement_hash", sa.String(64), unique=True),
        c("supersedes_settlement_id", nullable=True, unique=True),
        json_col(),
        valid_json(),
        fk(["plan_id"], SEAL),
        fk(["supersedes_settlement_id"], SETTLEMENT, ["settlement_id"]),
        fk(["settlement_id"], RESULT_SEAL),
        sa.CheckConstraint(
            "length(settlement_hash)=64 AND settlement_hash NOT GLOB '*[^0-9a-f]*'"
        ),
    )
    result = table(
        RESULT,
        c("settlement_id", primary_key=True),
        c("match_result_id", primary_key=True),
        c("result_no", sa.Integer()),
        c("internal_match_id"),
        json_col(),
        valid_json(),
        fk(["settlement_id"], SETTLEMENT),
        fk(["match_result_id"], "match_results"),
        fk(["internal_match_id"], "matches"),
        sa.UniqueConstraint("settlement_id", "internal_match_id"),
        sa.UniqueConstraint("settlement_id", "result_no"),
        sa.CheckConstraint("result_no>=0"),
    )
    result_seal = table(
        RESULT_SEAL,
        c("settlement_id", primary_key=True),
        fk(["settlement_id"], SETTLEMENT),
    )
    return {
        t.name: t
        for t in (
            plan,
            selection,
            ticket,
            atomic,
            leg,
            seal,
            settlement,
            result,
            result_seal,
        )
    }


def strategy_pass_trigger_sql_v1():
    sql = {}

    def trigger(
        name, table, condition, operation="INSERT", message="invalid strategy graph"
    ):
        sql[name] = (
            f"CREATE TRIGGER {name} BEFORE {operation} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'{message}'); END"
        )

    tables = strategy_pass_tables_v1(sa.MetaData())
    for name, t in tables.items():
        for operation in ("UPDATE", "DELETE"):
            trigger(
                f"trg_{name}_{operation.lower()}_v1",
                name,
                "1",
                operation,
                "strategy artifacts are append-only",
            )
        keys = " AND ".join(f"x.{c.name}=NEW.{c.name}" for c in t.primary_key.columns)
        trigger(
            f"trg_{name}_replace_v1",
            name,
            f"EXISTS (SELECT 1 FROM {name} x WHERE {keys})",
            message="strategy replacement is forbidden",
        )

    def j(path):
        return f"json_extract(NEW.artifact_json,'{path}')"

    parent = "$.source.parent"
    trigger(
        "trg_strategy_plan_binding_v1",
        PLAN,
        f"""
        {j("$.schema_version")} IS NOT 'STRATEGY_PASS_PLAN_V1' OR {j("$.plan_id")} IS NOT NEW.plan_id OR {j("$.plan_hash")} IS NOT NEW.plan_hash
        OR {j(parent + ".analysis_run_id")} IS NOT NEW.parent_analysis_run_id OR {j(parent + ".kind")} IS NOT NEW.source_kind OR {j(parent + ".source_id")} IS NOT NEW.source_id
        OR {j(parent + ".portfolio_revision_id")} IS NOT NEW.portfolio_revision_id OR {j(parent + ".fusion_run_id")} IS NOT NEW.fusion_run_id
        OR {j("$.source.budget_fen")} IS NOT NEW.budget_fen OR json_type(NEW.artifact_json,'$.source.budget_fen') IS NOT 'integer'
        OR {j("$.total_stake_fen")} IS NOT NEW.total_stake_fen OR json_array_length(NEW.artifact_json,'$.tickets') IS NOT NEW.ticket_count
        OR NOT EXISTS (SELECT 1 FROM analysis_runs r WHERE r.analysis_run_id=NEW.parent_analysis_run_id AND r.status='COMPLETED')
        OR NOT ( (NEW.source_kind='ANALYSIS_RUN' AND NEW.source_id=NEW.parent_analysis_run_id AND NEW.portfolio_revision_id IS NULL AND NEW.fusion_run_id IS NULL
            AND EXISTS (SELECT 1 FROM portfolios p WHERE p.analysis_run_id=NEW.parent_analysis_run_id AND p.budget_fen=NEW.budget_fen))
          OR (NEW.source_kind='PORTFOLIO_REVISION' AND NEW.source_id=NEW.portfolio_revision_id AND EXISTS (
            SELECT 1 FROM portfolio_revisions r,json_each(r.revision_json,'$.portfolios') p WHERE r.portfolio_revision_id=NEW.portfolio_revision_id
            AND r.parent_analysis_run_id=NEW.parent_analysis_run_id AND r.fusion_run_id=NEW.fusion_run_id AND json_extract(p.value,'$.budget_fen')=NEW.budget_fen)))
    """,
    )
    for name in (SELECTION, TICKET, ATOMIC, LEG):
        trigger(
            f"trg_{name}_sealed_insert_v1",
            name,
            f"EXISTS (SELECT 1 FROM {SEAL} WHERE plan_id=NEW.plan_id)",
            message="sealed strategy graph cannot be extended",
        )
    trigger(
        "trg_strategy_selection_binding_v1",
        SELECTION,
        f"""
        {j("$.candidate_id")} IS NOT NEW.selection_id OR {j("$.match_id")} IS NOT NEW.internal_match_id
        OR {j("$.sporttery_bonus_snapshot_id")} IS NOT NEW.snapshot_id OR {j("$.status")} IS NOT NEW.status
        OR NOT EXISTS (SELECT 1 FROM {PLAN} p WHERE p.plan_id=NEW.plan_id AND
          json(NEW.artifact_json)=json(json_extract(p.artifact_json,'$.source.selections['||NEW.selection_no||']')) AND
          ((p.source_kind='ANALYSIS_RUN' AND NEW.base_candidate_id=NEW.selection_id AND EXISTS (
              SELECT 1 FROM bet_candidates b WHERE b.candidate_id=NEW.base_candidate_id AND b.analysis_run_id=p.source_id AND b.eligibility_status=NEW.status
              AND b.internal_match_id=NEW.internal_match_id AND b.sporttery_bonus_snapshot_id=NEW.snapshot_id AND b.selection_key={j("$.selection")}
              AND b.probability_used=CAST({j("$.probability")} AS NUMERIC) AND b.fixed_bonus=CAST({j("$.fixed_bonus")} AS NUMERIC) AND b.ev=CAST({j("$.ev")} AS NUMERIC)))
           OR (p.source_kind='PORTFOLIO_REVISION' AND NEW.base_candidate_id IS NULL AND EXISTS (
              SELECT 1 FROM portfolio_revisions r,json_each(r.revision_json,'$.selection_candidates') s WHERE r.portfolio_revision_id=p.source_id
              AND json_extract(s.value,'$.candidate_id')=NEW.selection_id AND json_extract(s.value,'$.status')=NEW.status
              AND json_extract(s.value,'$.match_id')=NEW.internal_match_id AND json_extract(s.value,'$.sporttery_bonus_snapshot_id')=NEW.snapshot_id
              AND json_extract(s.value,'$.selection')={j("$.selection")} AND CAST(json_extract(s.value,'$.probability') AS NUMERIC)=CAST({j("$.probability")} AS NUMERIC)
              AND CAST(json_extract(s.value,'$.ev') AS NUMERIC)=CAST({j("$.ev")} AS NUMERIC)))))
    """,
    )
    fields = {
        "ticket_id": "$.ticket_id",
        "ticket_no": "$.ticket_no",
        "candidate_id": "$.candidate.candidate_id",
        "equivalence_hash": "$.candidate.equivalence_hash",
        "pass_type": "$.candidate.pass_type",
        "role": "$.role",
        "multiplier": "$.multiplier",
        "stake_fen": "$.stake_fen",
        "max_payout_fen": "$.max_payout_fen",
    }
    trigger(
        "trg_system_ticket_binding_v1",
        TICKET,
        " OR ".join(f"{j(path)} IS NOT NEW.{key}" for key, path in fields.items())
        + f"""
        OR json_array_length(NEW.artifact_json,'$.candidate.atomic_bets') IS NOT NEW.atomic_count
        OR NOT EXISTS (SELECT 1 FROM {PLAN} p WHERE p.plan_id=NEW.plan_id AND json(NEW.artifact_json)=json(json_extract(p.artifact_json,'$.tickets['||(NEW.ticket_no-1)||']')))
    """,
    )
    trigger(
        "trg_atomic_bet_binding_v1",
        ATOMIC,
        f"""
        {j("$.atomic_bet_id")} IS NOT NEW.atomic_bet_id OR {j("$.gross_payout_fen")} IS NOT NEW.gross_payout_fen
        OR json_array_length(NEW.artifact_json,'$.selection_ids') IS NOT NEW.leg_count OR {j("$.base_stake_fen")} IS NOT 200
        OR NOT EXISTS (SELECT 1 FROM {TICKET} t WHERE t.plan_id=NEW.plan_id AND t.ticket_id=NEW.ticket_id
            AND json(NEW.artifact_json)=json(json_extract(t.artifact_json,'$.candidate.atomic_bets['||NEW.atomic_no||']')))
    """,
    )
    trigger(
        "trg_atomic_leg_binding_v1",
        LEG,
        f"""
        NOT EXISTS (SELECT 1 FROM {ATOMIC} a JOIN {SELECTION} s ON s.plan_id=a.plan_id AND s.selection_id=NEW.selection_id
            WHERE a.plan_id=NEW.plan_id AND a.ticket_id=NEW.ticket_id AND a.atomic_bet_id=NEW.atomic_bet_id AND s.status='ELIGIBLE'
            AND s.internal_match_id=NEW.internal_match_id AND s.snapshot_id=NEW.snapshot_id
            AND json_extract(a.artifact_json,'$.selection_ids['||NEW.leg_no||']')=NEW.selection_id
            AND json_extract(a.artifact_json,'$.match_ids['||NEW.leg_no||']')=NEW.internal_match_id
            AND json_extract(a.artifact_json,'$.fixed_bonuses['||NEW.leg_no||']')=NEW.fixed_bonus)
    """,
    )
    trigger(
        "trg_strategy_plan_complete_v1",
        SEAL,
        f"""
        NOT EXISTS (SELECT 1 FROM {PLAN} p WHERE p.plan_id=NEW.plan_id
          AND (SELECT COUNT(*) FROM {SELECTION} s WHERE s.plan_id=p.plan_id)=json_array_length(p.artifact_json,'$.source.selections')
          AND (SELECT COUNT(*) FROM {TICKET} t WHERE t.plan_id=p.plan_id)=p.ticket_count
          AND COALESCE((SELECT SUM(t.stake_fen) FROM {TICKET} t WHERE t.plan_id=p.plan_id),0)=p.total_stake_fen)
        OR EXISTS (SELECT 1 FROM {TICKET} t WHERE t.plan_id=NEW.plan_id AND
          (SELECT COUNT(*) FROM {ATOMIC} a WHERE a.plan_id=t.plan_id AND a.ticket_id=t.ticket_id)<>t.atomic_count)
        OR EXISTS (SELECT 1 FROM {ATOMIC} a WHERE a.plan_id=NEW.plan_id AND
          (SELECT COUNT(*) FROM {LEG} l WHERE l.plan_id=a.plan_id AND l.ticket_id=a.ticket_id AND l.atomic_bet_id=a.atomic_bet_id)<>a.leg_count)
    """,
        message="strategy graph must be complete before sealing",
    )
    trigger(
        "trg_strategy_settlement_binding_v1",
        SETTLEMENT,
        f"""
        {j("$.schema_version")} IS NOT 'STRATEGY_SETTLEMENT_V1' OR {j("$.settlement_kind")} IS NOT 'BACKTEST'
        OR {j("$.policy_version")} IS NOT 'THREE_WAY_SYSTEM_PASS_BACKTEST_V1' OR {j("$.plan_id")} IS NOT NEW.plan_id
        OR {j("$.settlement_id")} IS NOT NEW.settlement_id OR {j("$.settlement_hash")} IS NOT NEW.settlement_hash
        OR {j("$.supersedes_settlement_id")} IS NOT NEW.supersedes_settlement_id
        OR NOT EXISTS (SELECT 1 FROM {PLAN} p WHERE p.plan_id=NEW.plan_id AND p.plan_hash={j("$.plan_hash")}
            AND json(json_extract(p.artifact_json,'$.source.parent'))=json({j("$.parent")}))
        OR (NEW.supersedes_settlement_id IS NULL AND EXISTS (SELECT 1 FROM {SETTLEMENT} x WHERE x.plan_id=NEW.plan_id))
        OR (NEW.supersedes_settlement_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {SETTLEMENT} x WHERE x.settlement_id=NEW.supersedes_settlement_id AND x.plan_id=NEW.plan_id))
    """,
    )
    trigger(
        "trg_strategy_settlement_result_binding_v1",
        RESULT,
        f"""
        {j("$.match_result_id")} IS NOT NEW.match_result_id OR {j("$.match_id")} IS NOT NEW.internal_match_id
        OR EXISTS (SELECT 1 FROM {RESULT_SEAL} s WHERE s.settlement_id=NEW.settlement_id)
        OR NOT EXISTS (SELECT 1 FROM {SETTLEMENT} s JOIN match_results r ON r.match_result_id=NEW.match_result_id
            WHERE s.settlement_id=NEW.settlement_id AND r.internal_match_id=NEW.internal_match_id
            AND r.home_goals={j("$.home_goals")} AND r.away_goals={j("$.away_goals")} AND r.payload_hash={j("$.payload_hash")}
            AND json(NEW.artifact_json)=json(json_extract(s.artifact_json,'$.match_results['||NEW.result_no||']')))
    """,
    )
    trigger(
        "trg_strategy_settlement_complete_v1",
        RESULT_SEAL,
        f"""
        NOT EXISTS (SELECT 1 FROM {SETTLEMENT} s WHERE s.settlement_id=NEW.settlement_id AND
            (SELECT COUNT(*) FROM {RESULT} r WHERE r.settlement_id=s.settlement_id)=json_array_length(s.artifact_json,'$.match_results'))
    """,
    )
    return sql


def install_strategy_pass_triggers(connection):
    if connection.scalar(
        sa.text(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='strategy_pass_plans'"
        )
    ):
        for name, sql in strategy_pass_trigger_sql_v1().items():
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
            connection.exec_driver_sql(sql)
