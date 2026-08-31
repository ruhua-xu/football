import json
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from football_system.infrastructure.database.models import (
    AnalysisPacketRecord,
    LLMReviewArtifactRecord,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from football_system.interfaces.cli import main


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _review_for(packet: dict) -> dict:
    return {
        "schema_version": "LLM_REVIEW_V1",
        "analysis_run_id": packet["analysis_run"]["analysis_run_id"],
        "packet_id": packet["packet_id"],
        "packet_hash": packet["packet_hash"],
        "match_reviews": [
            {
                "status": "VALID",
                "match_id": match["match_id"],
                "market_key": match["market_key"],
                "p_llm": match["p_quant"]["probabilities"],
                "assessment_confidence": "0.5",
                "scenarios": [],
                "preferred_outcomes": [],
                "avoid_outcomes": [],
                "counter_scenarios": [],
                "risk_tags": [],
                "reasoning_summary": "Offline file review.",
                "limitations": ["No external Evidence in packet"],
            }
            for match in packet["matches"]
        ],
    }


def test_cli_exports_validates_and_imports_append_only_review(tmp_path, capsys) -> None:
    database_path = tmp_path / "review-bridge.db"
    database_url = _database_url(database_path)
    packet_path = tmp_path / "analysis_packet.json"
    review_path = tmp_path / "llm_review.json"

    assert main(
        [
            "--database-url",
            database_url,
            "--budget-yuan",
            "100",
            "--analysis-run-id",
            "run-review-bridge",
        ]
    ) == 0
    assert main(
        [
            "analysis-packet",
            "export",
            "--database-url",
            database_url,
            "--analysis-run-id",
            "run-review-bridge",
            "--output",
            str(packet_path),
        ]
    ) == 0
    packet_bytes = packet_path.read_bytes()
    packet = json.loads(packet_bytes)
    serialized = packet_bytes.decode("utf-8").lower()
    for forbidden in ('"p_final"', '"ev"', '"budget"', '"stake"', '"tickets"'):
        assert forbidden not in serialized
    assert '"config_hash"' not in serialized
    assert main(
        [
            "analysis-packet",
            "export",
            "--database-url",
            database_url,
            "--analysis-run-id",
            "run-review-bridge",
            "--output",
            str(packet_path),
        ]
    ) == 0
    assert packet_path.read_bytes() == packet_bytes

    review_path.write_text(
        json.dumps(_review_for(packet), ensure_ascii=False), encoding="utf-8"
    )
    assert main(
        [
            "llm-review",
            "validate",
            "--packet",
            str(packet_path),
            "--review",
            str(review_path),
        ]
    ) == 0
    assert main(
        [
            "llm-review",
            "import",
            "--database-url",
            database_url,
            "--packet",
            str(packet_path),
            "--review",
            str(review_path),
        ]
    ) == 0
    assert main(
        [
            "llm-review",
            "import",
            "--database-url",
            database_url,
            "--packet",
            str(packet_path),
            "--review",
            str(review_path),
        ]
    ) == 0

    engine = create_database_engine(database_url)
    sessions = create_session_factory(engine)
    with sessions() as session:
        assert session.scalar(
            select(func.count()).select_from(AnalysisPacketRecord)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(LLMReviewArtifactRecord)
        ) == 1

    for statement in (
        "UPDATE analysis_packets SET schema_version = 'MUTATED'",
        "DELETE FROM llm_review_artifacts",
        "INSERT OR REPLACE INTO analysis_packets "
        "(packet_id, parent_analysis_run_id, schema_version, generated_at_utc, "
        "packet_json, packet_hash) SELECT packet_id, parent_analysis_run_id, "
        "schema_version, generated_at_utc, 'tampered', packet_hash "
        "FROM analysis_packets",
        "INSERT OR REPLACE INTO llm_review_artifacts "
        "(review_artifact_id, parent_analysis_run_id, packet_id, packet_hash, "
        "review_schema_version, imported_at_utc, raw_review_json, raw_review_hash, "
        "normalized_review_json, normalized_review_hash, validator_version, source_kind) "
        "SELECT review_artifact_id, parent_analysis_run_id, packet_id, packet_hash, "
        "review_schema_version, imported_at_utc, 'tampered', raw_review_hash, "
        "normalized_review_json, normalized_review_hash, validator_version, source_kind "
        "FROM llm_review_artifacts",
    ):
        with pytest.raises(IntegrityError):
            with sessions.begin() as session:
                session.execute(text(statement))

    output = capsys.readouterr().out
    assert "AnalysisPacket" in output
    assert "LLMReviewArtifact imported" in output


def test_validate_command_never_opens_database(tmp_path, monkeypatch, capsys) -> None:
    database_path = tmp_path / "source.db"
    database_url = _database_url(database_path)
    packet_path = tmp_path / "packet.json"
    review_path = tmp_path / "review.json"
    main(
        [
            "--database-url",
            database_url,
            "--budget-yuan",
            "100",
            "--analysis-run-id",
            "run-pure-validation",
        ]
    )
    main(
        [
            "analysis-packet",
            "export",
            "--database-url",
            database_url,
            "--analysis-run-id",
            "run-pure-validation",
            "--output",
            str(packet_path),
        ]
    )
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    review_path.write_text(json.dumps(_review_for(packet)), encoding="utf-8")
    monkeypatch.setattr(
        "football_system.interfaces.cli.upgrade_database",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("database opened")),
    )

    assert main(
        [
            "llm-review",
            "validate",
            "--packet",
            str(packet_path),
            "--review",
            str(review_path),
        ]
    ) == 0
    assert "valid for AnalysisPacket" in capsys.readouterr().out
