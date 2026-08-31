from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from football_system.application.review_bridge import (
    _parse_analysis_packet,
    canonical_json,
    sha256_text,
)
from football_system.domain.common import stable_id
from football_system.domain.post_review import FusionRun, PortfolioRevision
from football_system.domain.review import AnalysisPacketV2
from football_system.infrastructure.database.post_review_repositories import (
    SqlAlchemyPostReviewRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)

OUTCOMES = ("HOME_WIN", "DRAW", "AWAY_WIN")
PROBABILITY_FIELDS = ("home_win", "draw", "away_win")
EXPECTED_MISSING_CONTEXT = {
    "confirmed_lineup",
    "expected_lineup",
    "home_away_form",
    "injuries",
    "odds_movement_summary",
    "recent_form",
    "rest_days",
    "schedule_context",
    "suspensions",
}
EXPECTED_AVAILABLE_CONTEXT = {
    "evidence",
    "international_odds",
    "sporttery_odds",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the manual V2 packet and report baseline/revision differences."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    baseline = subparsers.add_parser("baseline")
    _add_common_arguments(baseline)

    compare = subparsers.add_parser("compare")
    _add_common_arguments(compare)
    compare.add_argument("--fusion-run-id", required=True)
    compare.add_argument("--portfolio-revision-id", required=True)

    args = parser.parse_args()
    database_path = args.database.resolve()
    packet_path = args.packet.resolve()
    output_path = args.output.resolve()
    packet = validate_packet(packet_path, database_path, args.analysis_run_id)

    with _connect(database_path) as connection:
        if args.command == "baseline":
            report = baseline_report(connection, packet, packet_path, database_path)
        else:
            fusion, revision = _load_post_review(
                database_path,
                args.fusion_run_id,
                args.portfolio_revision_id,
                args.analysis_run_id,
            )
            report = comparison_report(
                connection,
                packet,
                fusion,
                revision,
                packet_path,
                database_path,
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report + "\n", encoding="utf-8")
    print(f"Acceptance report written to {output_path}")
    return 0


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--analysis-run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)


def validate_packet(
    packet_path: Path,
    database_path: Path,
    analysis_run_id: str,
) -> AnalysisPacketV2:
    packet_bytes = packet_path.read_bytes()
    packet = _parse_analysis_packet(packet_bytes)
    _require(isinstance(packet, AnalysisPacketV2), "packet is not ANALYSIS_PACKET_V2")
    _require(
        packet.analysis_run.analysis_run_id == analysis_run_id,
        "packet AnalysisRun ID is incorrect",
    )
    _require(len(packet.matches) == 6, "packet does not contain exactly six matches")

    payload = packet.model_dump(mode="json")
    forbidden = sorted(_forbidden_key_paths(payload))
    _require(not forbidden, f"packet contains forbidden keys: {', '.join(forbidden)}")

    for match in packet.matches:
        context = match.review_context
        context_json = canonical_json(context.model_dump(mode="json"))
        expected_hash = sha256_text(context_json)
        expected_id = stable_id(
            "match-review-context",
            analysis_run_id,
            match.match_id,
            expected_hash,
        )
        _require(match.review_context_hash == expected_hash, "context hash mismatch")
        _require(match.review_context_id == expected_id, "context ID mismatch")
        _require(match.p_market is not None, "P_market is missing")
        _require(match.p_quant is not None, "P_quant is missing")
        _require(context.international_odds is not None, "international odds missing")
        _require(context.sporttery_odds is not None, "Sporttery odds missing")
        _require(
            len(context.international_odds.odds.items()) == 3,
            "international three-way odds are incomplete",
        )
        _require(
            len(context.sporttery_odds.odds.items()) == 3,
            "Sporttery three-way fixed bonuses are incomplete",
        )
        _require(context.odds_movement_summary is None, "odds movement was invented")
        _require(context.recent_form is None, "recent form was invented")
        _require(context.home_away_form is None, "home/away form was invented")
        _require(context.rest_days is None, "rest days were invented")
        _require(context.schedule_context is None, "schedule context was invented")
        _require(not context.injuries, "injuries were invented")
        _require(not context.suspensions, "suspensions were invented")
        _require(not context.expected_lineup, "expected lineup was invented")
        _require(not context.confirmed_lineup, "confirmed lineup was invented")
        _require(
            set(context.data_quality.missing_fields) == EXPECTED_MISSING_CONTEXT,
            "data-quality missing fields are incomplete",
        )
        _require(
            set(context.data_quality.available_fields) == EXPECTED_AVAILABLE_CONTEXT,
            "data-quality available fields are incorrect",
        )
        _require(context.data_quality.status.value == "PARTIAL", "data quality is not PARTIAL")
        _require(len(context.evidence) == 2, "sealed odds evidence is incomplete")
        _require(
            {item.category for item in context.evidence}
            == {"INTERNATIONAL_ODDS", "SPORTTERY_ODDS"},
            "unexpected evidence categories",
        )
        _require(
            all(
                item.body
                and item.source_reference
                and item.source_record_id
                and item.source_payload_hash
                for item in context.evidence
            ),
            "evidence body or lineage is incomplete",
        )

    canonical_packet = canonical_json(packet.model_dump(mode="json"))
    with _connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT packet_id, parent_analysis_run_id, schema_version,
                   packet_hash, packet_json
            FROM analysis_packets
            WHERE parent_analysis_run_id = ?
            """,
            (analysis_run_id,),
        ).fetchone()
        _require(row is not None, "packet is not persisted")
        _require(row["packet_id"] == packet.packet_id, "stored packet ID mismatch")
        _require(
            row["parent_analysis_run_id"] == analysis_run_id,
            "stored AnalysisRun ID mismatch",
        )
        _require(row["schema_version"] == "ANALYSIS_PACKET_V2", "stored schema mismatch")
        _require(row["packet_hash"] == packet.packet_hash, "stored packet hash mismatch")
        _require(row["packet_json"] == canonical_packet, "stored packet JSON mismatch")
        _require(
            packet_bytes == (row["packet_json"] + "\n").encode("utf-8"),
            "exported packet bytes differ from the sealed database artifact",
        )
        run = connection.execute(
            """
            SELECT status, pipeline_version
            FROM analysis_runs
            WHERE analysis_run_id = ?
            """,
            (analysis_run_id,),
        ).fetchone()
        _require(run is not None and run["status"] == "COMPLETED", "AnalysisRun is not completed")
        policies = {
            item[0]
            for item in connection.execute(
                """
                SELECT DISTINCT fusion_policy
                FROM final_predictions
                WHERE analysis_run_id = ?
                """,
                (analysis_run_id,),
            ).fetchall()
        }
        _require(policies == {"QUANT_ONLY_V1"}, "original fusion policy is incorrect")
        schemas = {
            item[0]
            for item in connection.execute(
                "SELECT schema_version FROM analysis_packets"
            ).fetchall()
        }
        _require(schemas == {"ANALYSIS_PACKET_V2"}, "a non-V2 packet was exported")

    return packet


def baseline_report(
    connection: sqlite3.Connection,
    packet: AnalysisPacketV2,
    packet_path: Path,
    database_path: Path,
) -> str:
    run_id = packet.analysis_run.analysis_run_id
    _require(_count(connection, "analysis_runs") == 1, "database is not a fresh run")
    _require(_count(connection, "llm_review_artifacts") == 0, "review already exists")
    _require(_count(connection, "fusion_runs") == 0, "fusion already exists")
    _require(_count(connection, "portfolio_revisions") == 0, "revision already exists")

    lines = [
        "# Manual Review Acceptance Baseline",
        "",
        "> Audit report only. Do not use this file as web GPT input.",
        "",
        "## Identity",
        "",
        f"- AnalysisRun ID: `{run_id}`",
        f"- Packet ID: `{packet.packet_id}`",
        f"- Packet hash: `{packet.packet_hash}`",
        f"- AnalysisPacket: `{packet_path}`",
        f"- SQLite database: `{database_path}`",
        f"- Schema: `{packet.schema_version}`",
        f"- Matches: `{len(packet.matches)}`",
        "- Original fusion policy (local DB only): `QUANT_ONLY_V1`",
        "",
        "## Packet Validation",
        "",
        "- PASS: canonical packet hash and deterministic packet ID",
        "- PASS: exact persisted packet binding and exported file bytes",
        "- PASS: six matches with P_market and P_quant",
        "- PASS: per-match review context ID and hash",
        "- PASS: sealed international odds and Sporttery fixed bonuses",
        "- PASS: unavailable context is null/empty and marked PARTIAL",
        "- PASS: no final prediction, EV, ranking, ticket, portfolio, budget, stake, weight, or strategy fields",
        "- PASS: no ANALYSIS_PACKET_V1 was exported",
        "- PASS: no formal LLM review, FusionRun, or PortfolioRevision exists",
        "",
        "## Original P_final",
        "",
        "| Match | Home | Draw | Away |",
        "|---|---:|---:|---:|",
    ]
    for match_id, probabilities in _original_probabilities(connection, run_id).items():
        lines.append(
            f"| `{match_id}` | {_pct(probabilities['HOME_WIN'])} | "
            f"{_pct(probabilities['DRAW'])} | {_pct(probabilities['AWAY_WIN'])} |"
        )

    lines.extend(["", "## Original Portfolios", ""])
    for portfolio in _original_portfolios(connection, run_id):
        lines.extend(_render_original_portfolio(connection, portfolio))
    return "\n".join(lines)


def comparison_report(
    connection: sqlite3.Connection,
    packet: AnalysisPacketV2,
    fusion: FusionRun,
    revision: PortfolioRevision,
    packet_path: Path,
    database_path: Path,
) -> str:
    run_id = packet.analysis_run.analysis_run_id
    original_probabilities = _original_probabilities(connection, run_id)
    original_candidates = _original_candidates(connection, run_id)
    revised_candidates = {
        (item.match_id, item.market.canonical, item.selection.value): item
        for item in revision.selection_candidates
    }
    fusion_config = json.loads(fusion.config_json)
    max_delta = Decimal(str(fusion_config["max_probability_delta"]))

    lines = [
        "# Manual Review Acceptance Comparison",
        "",
        "> Audit report only. Do not use this file as web GPT input.",
        "",
        "## Identity",
        "",
        f"- AnalysisRun ID: `{run_id}`",
        f"- Packet ID: `{packet.packet_id}`",
        f"- Packet hash: `{packet.packet_hash}`",
        f"- LLMReviewArtifact ID: `{fusion.llm_review_artifact_id}`",
        f"- FusionRun ID: `{fusion.fusion_run_id}`",
        f"- PortfolioRevision ID: `{revision.portfolio_revision_id}`",
        f"- Handshake: `SUCCESS` ({len(fusion.results)} results, "
        f"{sum(item.fallback_code is not None for item in fusion.results)} fallbacks)",
        f"- AnalysisPacket: `{packet_path}`",
        f"- SQLite database: `{database_path}`",
        "",
        "## Probability Changes",
        "",
        "| Match | Original P_final | P_llm | Review P_final | Applied delta | Clipped | Fallback |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in sorted(fusion.results, key=lambda item: item.match_id):
        original = original_probabilities[result.match_id]
        _require(
            _probability_tuple(result.p_base) == tuple(original[item] for item in OUTCOMES),
            f"FusionRun base differs from original P_final for {result.match_id}",
        )
        scaled_delta = (
            None
            if result.raw_probability_delta is None
            else tuple(
                value * result.confidence_factor * result.data_quality_factor
                for _, value in result.raw_probability_delta.items()
            )
        )
        applied_delta = tuple(
            value for _, value in result.applied_probability_delta.items()
        )
        clipped = bool(
            scaled_delta
            and any(
                abs(scaled) - abs(applied) > Decimal("0.00000001")
                for scaled, applied in zip(scaled_delta, applied_delta, strict=True)
            )
        )
        _require(
            all(abs(value) <= max_delta for value in applied_delta),
            f"applied delta exceeds clipping cap for {result.match_id}",
        )
        lines.append(
            f"| `{result.match_id}` | {_probability_text(result.p_base)} | "
            f"{_probability_text(result.p_llm)} | {_probability_text(result.p_final)} | "
            f"{_delta_text(applied_delta)} | {'YES' if clipped else 'NO'} | "
            f"{result.fallback_code or '-'} |"
        )

    lines.extend(
        [
            "",
            "## SelectionCandidate Changes",
            "",
            "| Match/market/outcome | Original status | Revision status | Probability delta | EV delta |",
            "|---|---|---|---:|---:|",
        ]
    )
    candidate_rows = 0
    for key in sorted(set(original_candidates) | set(revised_candidates)):
        original = original_candidates.get(key)
        revised = revised_candidates.get(key)
        if original is None:
            lines.append(f"| `{_candidate_key(key)}` | ADDED | {revised.status.value} | - | - |")
            candidate_rows += 1
            continue
        if revised is None:
            lines.append(f"| `{_candidate_key(key)}` | {original['status']} | REMOVED | - | - |")
            candidate_rows += 1
            continue
        probability_delta = revised.probability - original["probability"]
        ev_delta = revised.ev - original["ev"]
        if (
            revised.status.value != original["status"]
            or probability_delta != 0
            or ev_delta != 0
        ):
            lines.append(
                f"| `{_candidate_key(key)}` | {original['status']} | "
                f"{revised.status.value} | {_signed_pct(probability_delta)} | "
                f"{_signed_pct(ev_delta)} |"
            )
            candidate_rows += 1
    if candidate_rows == 0:
        lines.append("| No changes | - | - | 0.00% | 0.00% |")

    lines.extend(["", "## Portfolio, Cash, Exposure, and Stress Changes", ""])
    original_portfolios = {
        item["budget_fen"]: item for item in _original_portfolios(connection, run_id)
    }
    revised_portfolios = {item.budget_fen: item for item in revision.portfolios}
    revised_risks = {
        report.budget_fen: report for report in revision.portfolio_risk_reports
    }
    for budget in sorted(set(original_portfolios) | set(revised_portfolios)):
        original = original_portfolios.get(budget)
        revised = revised_portfolios.get(budget)
        _require(original is not None and revised is not None, "portfolio budgets changed")
        original_tickets = _original_ticket_map(connection, original["portfolio_id"])
        revised_tickets = {
            _revision_ticket_key(ticket): ticket for ticket in revised.tickets
        }
        added = sorted(set(revised_tickets) - set(original_tickets))
        deleted = sorted(set(original_tickets) - set(revised_tickets))
        retained = sorted(set(original_tickets) & set(revised_tickets))
        lines.extend(
            [
                f"### Budget {_money(budget)}",
                "",
                f"- Status: `{original['status']}` -> `{revised.status.value}`",
                f"- Stake: {_money(original['total_stake_fen'])} -> {_money(revised.total_stake_fen)}",
                f"- Cash: {_money(original['cash_fen'])} -> {_money(revised.cash_position.amount_fen)}",
                f"- More cash retained: `{'YES' if revised.cash_position.amount_fen > original['cash_fen'] else 'NO'}`",
                f"- Added tickets: {', '.join(f'`{item}`' for item in added) or 'none'}",
                f"- Deleted tickets: {', '.join(f'`{item}`' for item in deleted) or 'none'}",
            ]
        )
        allocation_changes = [
            f"`{key}` {_money(original_tickets[key]['stake_fen'])} -> "
            f"{_money(revised_tickets[key].stake_fen)}"
            for key in retained
            if original_tickets[key]["stake_fen"] != revised_tickets[key].stake_fen
        ]
        lines.append(
            f"- Retained ticket allocation changes: {', '.join(allocation_changes) or 'none'}"
        )

        original_exposure = _original_exposure_map(
            connection, original["portfolio_id"]
        )
        revised_risk = revised_risks[budget]
        revised_exposure = {
            item.match_id: item.exposed_stake_fen
            for item in revised_risk.match_exposures
        }
        lines.extend(
            [
                "",
                "| Match exposure | Original | Revision | Change |",
                "|---|---:|---:|---:|",
            ]
        )
        for match_id in sorted(set(original_exposure) | set(revised_exposure)):
            before = original_exposure.get(match_id, 0)
            after = revised_exposure.get(match_id, 0)
            lines.append(
                f"| `{match_id}` | {_money(before)} | {_money(after)} | "
                f"{_signed_money(after - before)} |"
            )
        original_max = max(original_exposure.values(), default=0)
        concentration_improved = revised_risk.max_match_exposure_fen < original_max
        lines.extend(
            [
                "",
                f"- Maximum match exposure: {_money(original_max)} -> "
                f"{_money(revised_risk.max_match_exposure_fen)}",
                f"- Risk concentration improved: `{'YES' if concentration_improved else 'NO'}`",
                "",
                "| Stress scenario | Original | Revision |",
                "|---|---|---|",
            ]
        )
        original_stress = _original_stress_map(
            connection, original["portfolio_id"]
        )
        revised_stress = {
            item.scenario_key: _stress_text_model(item)
            for item in revised_risk.stress_results
        }
        for scenario in sorted(set(original_stress) | set(revised_stress)):
            lines.append(
                f"| `{scenario}` | {original_stress.get(scenario, 'missing')} | "
                f"{revised_stress.get(scenario, 'missing')} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def _load_post_review(
    database_path: Path,
    fusion_run_id: str,
    revision_id: str,
    analysis_run_id: str,
) -> tuple[FusionRun, PortfolioRevision]:
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_database_engine(database_url)
    repository = SqlAlchemyPostReviewRepository(create_session_factory(engine))
    try:
        fusion = repository.find_fusion_run(fusion_run_id)
        revision = repository.find_portfolio_revision(revision_id)
    finally:
        engine.dispose()
    _require(fusion is not None, f"unknown FusionRun: {fusion_run_id}")
    _require(revision is not None, f"unknown PortfolioRevision: {revision_id}")
    _require(fusion.parent_analysis_run_id == analysis_run_id, "FusionRun lineage mismatch")
    _require(
        revision.parent_analysis_run_id == analysis_run_id
        and revision.fusion_run_id == fusion_run_id,
        "PortfolioRevision lineage mismatch",
    )
    return fusion, revision


def _forbidden_key_paths(value: object, prefix: str = "$") -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.casefold()
            if (
                normalized == "p_final"
                or normalized == "ev"
                or "ranking" in normalized
                or "candidate" in normalized
                or "ticket" in normalized
                or "portfolio" in normalized
                or "budget" in normalized
                or "stake" in normalized
                or "weight" in normalized
                or "strategy" in normalized
                or normalized == "constraints"
            ):
                paths.add(f"{prefix}.{key}")
            paths.update(_forbidden_key_paths(item, f"{prefix}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.update(_forbidden_key_paths(item, f"{prefix}[{index}]"))
    return paths


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _original_probabilities(
    connection: sqlite3.Connection,
    analysis_run_id: str,
) -> dict[str, dict[str, Decimal]]:
    values: dict[str, dict[str, Decimal]] = defaultdict(dict)
    rows = connection.execute(
        """
        SELECT p.internal_match_id, o.selection_key, o.probability
        FROM final_predictions p
        JOIN final_prediction_outcomes o
          ON o.final_prediction_id = p.final_prediction_id
        WHERE p.analysis_run_id = ?
        ORDER BY p.internal_match_id, o.selection_key
        """,
        (analysis_run_id,),
    )
    for row in rows:
        values[row["internal_match_id"]][row["selection_key"]] = _decimal(
            row["probability"]
        )
    _require(len(values) == 6, "stored P_final set is incomplete")
    _require(all(set(item) == set(OUTCOMES) for item in values.values()), "P_final outcomes are incomplete")
    return dict(values)


def _original_candidates(
    connection: sqlite3.Connection,
    analysis_run_id: str,
) -> dict[tuple[str, str, str], dict[str, object]]:
    result = {}
    rows = connection.execute(
        """
        SELECT internal_match_id, market_key, selection_key,
               probability_used, ev, eligibility_status
        FROM bet_candidates
        WHERE analysis_run_id = ?
        """,
        (analysis_run_id,),
    )
    for row in rows:
        key = (row["internal_match_id"], row["market_key"], row["selection_key"])
        result[key] = {
            "probability": _decimal(row["probability_used"]),
            "ev": _decimal(row["ev"]),
            "status": row["eligibility_status"],
        }
    return result


def _original_portfolios(
    connection: sqlite3.Connection,
    analysis_run_id: str,
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT p.portfolio_id, p.budget_fen, p.total_stake_fen,
               p.unused_budget_fen, p.status, p.no_bet_reason,
               c.amount_fen AS cash_fen
        FROM portfolios p
        JOIN portfolio_cash_positions c ON c.portfolio_id = p.portfolio_id
        WHERE p.analysis_run_id = ?
        ORDER BY p.budget_fen
        """,
        (analysis_run_id,),
    ).fetchall()
    _require([row["budget_fen"] for row in rows] == [10_000, 20_000], "portfolio budgets are incorrect")
    return [dict(row) for row in rows]


def _render_original_portfolio(
    connection: sqlite3.Connection,
    portfolio: dict[str, object],
) -> list[str]:
    portfolio_id = str(portfolio["portfolio_id"])
    tickets = _original_ticket_map(connection, portfolio_id)
    risk = connection.execute(
        """
        SELECT total_stake_at_risk_fen, max_single_ticket_exposure_fen,
               max_match_exposure_fen, cash_ratio
        FROM portfolio_risk_reports
        WHERE portfolio_id = ?
        """,
        (portfolio_id,),
    ).fetchone()
    _require(risk is not None, "portfolio risk report is missing")
    lines = [
        f"### Budget {_money(int(portfolio['budget_fen']))}",
        "",
        f"- Status: `{portfolio['status']}`",
        f"- Total stake: {_money(int(portfolio['total_stake_fen']))}",
        f"- Cash position: {_money(int(portfolio['cash_fen']))}",
        f"- Tickets: `{len(tickets)}`",
        f"- Stake at risk: {_money(risk['total_stake_at_risk_fen'])}",
        f"- Maximum single-ticket exposure: {_money(risk['max_single_ticket_exposure_fen'])}",
        f"- Maximum match exposure: {_money(risk['max_match_exposure_fen'])}",
        "",
        "| Ticket | Multiplier | Stake | Potential gross payout | Expected profit | ROI |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, ticket in tickets.items():
        lines.append(
            f"| `{key}` | {ticket['multiplier']} | {_money(ticket['stake_fen'])} | "
            f"{_money(ticket['potential_gross_payout_fen'])} | "
            f"{_money_decimal(ticket['expected_profit_fen'])} | "
            f"{_pct(ticket['expected_roi'])} |"
        )
    lines.extend(
        [
            "",
            "| Match exposure | Stake | Budget ratio |",
            "|---|---:|---:|",
        ]
    )
    exposure_rows = connection.execute(
        """
        SELECT e.internal_match_id, e.exposed_stake_fen, e.budget_ratio
        FROM portfolio_match_exposures e
        JOIN portfolio_risk_reports r ON r.risk_report_id = e.risk_report_id
        WHERE r.portfolio_id = ?
        ORDER BY e.exposed_stake_fen DESC, e.internal_match_id
        """,
        (portfolio_id,),
    )
    for row in exposure_rows:
        lines.append(
            f"| `{row['internal_match_id']}` | {_money(row['exposed_stake_fen'])} | "
            f"{_pct(row['budget_ratio'])} |"
        )
    lines.extend(
        [
            "",
            "| Stress scenario | Exposed stake | Result |",
            "|---|---:|---|",
        ]
    )
    stress_rows = connection.execute(
        """
        SELECT scenario_key, scenario_exposed_stake_fen, is_complete,
               profit_loss_fen, capital_recovery_ratio,
               minimum_ending_capital_fen, maximum_ending_capital_fen
        FROM portfolio_stress_results
        WHERE portfolio_id = ?
        ORDER BY scenario_key
        """,
        (portfolio_id,),
    )
    for row in stress_rows:
        lines.append(
            f"| `{row['scenario_key']}` | {_money(row['scenario_exposed_stake_fen'])} | "
            f"{_stress_text_row(row)} |"
        )
    lines.append("")
    return lines


def _original_ticket_map(
    connection: sqlite3.Connection,
    portfolio_id: str,
) -> dict[str, dict[str, object]]:
    rows = connection.execute(
        """
        SELECT t.ticket_id, t.ticket_no, t.multiplier, t.stake_fen,
               t.potential_gross_payout_fen, t.expected_profit_fen,
               t.expected_roi, l.leg_no, b.internal_match_id, b.selection_key
        FROM tickets t
        JOIN ticket_candidate_legs l
          ON l.ticket_candidate_id = t.ticket_candidate_id
        JOIN bet_candidates b ON b.candidate_id = l.candidate_id
        WHERE t.portfolio_id = ?
        ORDER BY t.ticket_no, l.leg_no
        """,
        (portfolio_id,),
    ).fetchall()
    grouped: dict[str, dict[str, object]] = {}
    legs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in rows:
        ticket_id = row["ticket_id"]
        grouped.setdefault(
            ticket_id,
            {
                "ticket_no": row["ticket_no"],
                "multiplier": row["multiplier"],
                "stake_fen": row["stake_fen"],
                "potential_gross_payout_fen": row["potential_gross_payout_fen"],
                "expected_profit_fen": _decimal(row["expected_profit_fen"]),
                "expected_roi": _decimal(row["expected_roi"]),
            },
        )
        legs[ticket_id].append((row["internal_match_id"], row["selection_key"]))
    return {
        _ticket_key(legs[ticket_id]): grouped[ticket_id]
        for ticket_id in sorted(grouped, key=lambda item: grouped[item]["ticket_no"])
    }


def _original_exposure_map(
    connection: sqlite3.Connection,
    portfolio_id: str,
) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT e.internal_match_id, e.exposed_stake_fen
        FROM portfolio_match_exposures e
        JOIN portfolio_risk_reports r ON r.risk_report_id = e.risk_report_id
        WHERE r.portfolio_id = ?
        """,
        (portfolio_id,),
    )
    return {row["internal_match_id"]: row["exposed_stake_fen"] for row in rows}


def _original_stress_map(
    connection: sqlite3.Connection,
    portfolio_id: str,
) -> dict[str, str]:
    rows = connection.execute(
        """
        SELECT scenario_key, scenario_exposed_stake_fen, is_complete,
               profit_loss_fen, capital_recovery_ratio,
               minimum_ending_capital_fen, maximum_ending_capital_fen
        FROM portfolio_stress_results
        WHERE portfolio_id = ?
        """,
        (portfolio_id,),
    )
    return {row["scenario_key"]: _stress_text_row(row) for row in rows}


def _revision_ticket_key(ticket: object) -> str:
    return _ticket_key(
        [(leg.match_id, leg.selection.value) for leg in ticket.candidate.legs]
    )


def _ticket_key(legs: list[tuple[str, str]]) -> str:
    return " + ".join(f"{match_id}/{selection}" for match_id, selection in sorted(legs))


def _candidate_key(key: tuple[str, str, str]) -> str:
    return "/".join(key)


def _stress_text_row(row: sqlite3.Row) -> str:
    if row["is_complete"]:
        return (
            f"P/L {_signed_money(row['profit_loss_fen'])}; "
            f"recovery {_pct(row['capital_recovery_ratio'])}"
        )
    return (
        f"capital {_money(row['minimum_ending_capital_fen'])}.."
        f"{_money(row['maximum_ending_capital_fen'])}"
    )


def _stress_text_model(result: object) -> str:
    if result.is_complete:
        return (
            f"P/L {_signed_money(result.profit_loss_fen)}; "
            f"recovery {_pct(result.capital_recovery_ratio)}"
        )
    return (
        f"capital {_money(result.minimum_ending_capital_fen)}.."
        f"{_money(result.maximum_ending_capital_fen)}"
    )


def _probability_tuple(probabilities: object) -> tuple[Decimal, Decimal, Decimal]:
    return tuple(getattr(probabilities, field) for field in PROBABILITY_FIELDS)


def _probability_text(probabilities: object | None) -> str:
    if probabilities is None:
        return "-"
    return "/".join(_pct(value) for value in _probability_tuple(probabilities))


def _delta_text(values: tuple[Decimal, Decimal, Decimal]) -> str:
    return "/".join(_signed_pct(value) for value in values)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _money(value: int) -> str:
    return f"CNY {Decimal(value) / Decimal(100):.2f}"


def _money_decimal(value: Decimal) -> str:
    return f"CNY {value / Decimal(100):.2f}"


def _signed_money(value: int) -> str:
    return f"{Decimal(value) / Decimal(100):+.2f} CNY"


def _pct(value: object) -> str:
    return f"{_decimal(value) * Decimal(100):.2f}%"


def _signed_pct(value: Decimal) -> str:
    return f"{value * Decimal(100):+.2f}%"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


if __name__ == "__main__":
    raise SystemExit(main())
