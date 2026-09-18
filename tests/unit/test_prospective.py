from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_UP, localcontext
import hashlib

import pytest

from football_system.application.prospective_requests import LockWorkflowRequestV1, PrepareProspectiveRequestV1
from football_system.domain.archive import canonical_json
from football_system.domain.market_v2 import MarketKeyV2, MarketProbabilityDistributionV1
from football_system.domain.prospective_evidence import (
    AbsenceFactV1, AssertionClass, FactCategory, FormFactV1, LineupFactV1, ManualVerifiedImportV1,
    ProviderEntitlementV1, ScheduleFactV1, freshness, provider_capability,
)
from football_system.domain.services.prospective_validation import calibration, correction_outcome, layer_summary, score_distribution
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock, verified_manual_bytes

NOW = datetime(2026, 1, 22, tzinfo=timezone.utc)


@pytest.mark.parametrize("published,expected", [(None, "UNKNOWN"), (NOW, "FRESH"), (NOW-timedelta(seconds=3600), "FRESH"), (NOW-timedelta(seconds=3601), "STALE")])
def test_f_source_time_freshness(published, expected):
    assert freshness(published, NOW, 3600) == expected


def test_g_lineup_unknown_expected_and_confirmed_are_not_interchangeable():
    unknown = LineupFactV1(category="LINEUP", team_id="team", status="UNKNOWN")
    assert not unknown.starting_xi
    with pytest.raises(ValueError, match="CONFIRMED_LINEUP"):
        LineupFactV1(category="LINEUP", team_id="team", status="CONFIRMED")
    players = tuple(dict(player_id="p"+str(i), team_id="team") for i in range(11))
    for category, status in (("LINEUP", "UNKNOWN"), ("EXPECTED_LINEUP", "CONFIRMED")):
        with pytest.raises(ValueError):
            LineupFactV1(category=category, team_id="team", status=status, starting_xi=players, confirmation_reference="fixture-proof")
    assert LineupFactV1(category="LINEUP", team_id="team", status="CONFIRMED", starting_xi=players, confirmation_reference="fixture-proof").status == "CONFIRMED"


def test_h_absence_unknown_is_not_empty_or_an_inferred_ban():
    for category in ("INJURY", "SUSPENSION"):
        assert AbsenceFactV1(category=category, team_id="team", knowledge_status="UNKNOWN", as_of_at_utc=NOW).records == ()
        for status in ("NONE_REPORTED", "RECORDS_AVAILABLE"):
            with pytest.raises(ValueError):
                AbsenceFactV1(category=category, team_id="team", knowledge_status=status, as_of_at_utc=NOW)
    record = dict(player={"player_id": "p", "team_id": "team"}, status="ACTIVE", reason_category="INJURY",
        reason="Reviewed fact", starts_at_utc=NOW-timedelta(days=1), available_at_utc=NOW, source_timestamp_utc=NOW, confidence="1")
    with pytest.raises(ValueError, match="EXPLICIT_BAN"):
        AbsenceFactV1(category="SUSPENSION", team_id="team", knowledge_status="RECORDS_AVAILABLE", as_of_at_utc=NOW, records=(record,))
    record["reason_category"] = "BAN"
    with pytest.raises(ValueError, match="BAN_IS_NOT_INJURY"):
        AbsenceFactV1(category="INJURY", team_id="team", knowledge_status="RECORDS_AVAILABLE", as_of_at_utc=NOW, records=(record,))
    record["source_timestamp_utc"] = NOW+timedelta(seconds=1)
    with pytest.raises(ValueError):
        AbsenceFactV1(category="SUSPENSION", team_id="team", knowledge_status="RECORDS_AVAILABLE", as_of_at_utc=NOW, records=(record,))


@pytest.mark.parametrize("capability", [*FactCategory, "RESULT_REVISION_CHAIN"])
def test_j_provider_remains_unavailable_without_activation(capability):
    assert provider_capability("SPORTMONKS", capability, NOW).status == "UNAVAILABLE"
    entitlement = ProviderEntitlementV1(provider="SPORTMONKS", evidence_reference="synthetic-reviewed-license", evidence_sha256="a"*64,
        source_identity="synthetic-license", effective_at_utc=NOW, expires_at_utc=NOW+timedelta(days=1), permitted_uses=("PROSPECTIVE_EVIDENCE",))
    assert provider_capability("SPORTMONKS", capability, NOW, entitlement).status == "UNAVAILABLE"
    assert provider_capability("SPORTMONKS", capability, NOW, entitlement).http_sends == 0


def test_k_schedule_rest_form_windows_and_future_finished():
    target = dict(match_id="target", home_team_id="home", away_team_id="away", kickoff_at_utc=NOW+timedelta(days=2), status="SCHEDULED", known_at_utc=NOW)
    past = dict(target, match_id="past", kickoff_at_utc=NOW-timedelta(days=3), status="FINISHED")
    data = dict(category="REST", team_id="home", as_of_at_utc=NOW, window_start_utc=NOW-timedelta(days=5),
        window_end_utc=NOW+timedelta(days=5), fixtures=(past, target), coverage="PROVIDER_SCOPE_ONLY", competition_scope=("league",),
        target_fixture_id="target", previous_fixture_id="past", rest_days=5)
    assert ScheduleFactV1(**data).rest_days == 5
    for bad in (dict(target, status="FINISHED"), dict(target, known_at_utc=NOW+timedelta(seconds=1))):
        with pytest.raises(ValueError):
            ScheduleFactV1(**{**data, "fixtures": (past, bad)})
    with pytest.raises(ValueError):
        ScheduleFactV1(**{**data, "rest_days": 4})
    with pytest.raises(ValueError):
        FormFactV1(team_id="home", as_of_at_utc=NOW, window_start_utc=NOW-timedelta(days=1), window_end_utc=NOW+timedelta(days=1), result_ids=())


def probability(values):
    key = MarketKeyV2(market_type="THREE_WAY")
    return MarketProbabilityDistributionV1(market_key=key, outcomes=tuple(dict(outcome=o, probability=p) for o, p in zip(key.catalog, values, strict=True)))


def test_l_m_n_o_brier_logloss_calibration_oracle_and_decimal_context():
    base = probability(("0.5", "0.3", "0.2"))
    better = probability(("0.6", "0.2", "0.2"))
    worse = probability(("0.4", "0.4", "0.2"))
    b = score_distribution(base, "HOME_WIN")
    assert b["brier"] == Decimal("0.38")
    assert b["log_loss"].quantize(Decimal("0.000001")) == Decimal("0.693147")
    assert correction_outcome(b, score_distribution(better, "HOME_WIN"), "VALID") == "improved"
    assert correction_outcome(b, score_distribution(worse, "HOME_WIN"), "VALID") == "worsened"
    assert correction_outcome(b, b, "VALID") == "neutral"
    assert correction_outcome(b, b, "UNAVAILABLE") == "abstained"
    zero = score_distribution(probability(("0", "0.5", "0.5")), "HOME_WIN")
    assert zero["log_loss"] is None and zero["log_loss_status"] == "INFINITE_ZERO_TRUE_PROBABILITY"
    records = [(base, "HOME_WIN", base), (better, "AWAY_WIN", better)]
    expected = canonical_json(layer_summary(records))
    with localcontext() as context:
        context.prec = 7
        context.rounding = ROUND_UP
        assert canonical_json(layer_summary(records)) == expected
    calibrated = calibration(records)
    assert calibrated["one_vs_rest_outcome_count"] == 6
    assert sum(row["observed_count"] for row in calibrated["bins"]) == 2
    assert calibrated["buckets"]["DRAW"]["observed_count"] == 0


@pytest.mark.parametrize("field", ["locked_at_utc", "prepared_at_utc", "data_classification", "p_final", "sp", "objective_weights"])
def test_public_commands_reject_override_fields(field):
    with pytest.raises(ValueError):
        LockWorkflowRequestV1(request_key="key", run_id="run", **{field: "injected"})
    with pytest.raises(ValueError):
        PrepareProspectiveRequestV1(request_key="key", epoch_id="epoch", analysis_id="analysis", slate_key="slate", slate_date="2026-01-22", budget_fen=0, **{field: "injected"})


def test_i_manual_reader_provenance_closed_roots_and_expiry(tmp_path):
    data = b"self-authored synthetic evidence"
    (tmp_path / "fact.json").write_bytes(data)
    claim = ManualVerifiedImportV1(match_id="match", data_classification="SYNTHETIC", source_identity="manual",
        source_reference="self-authored", source_file="fact.json", source_hash=hashlib.sha256(data).hexdigest(), rights_basis="SELF_OBSERVED",
        rights_reference="self-authored", retention_until_utc=NOW+timedelta(days=1), verified_by="reviewer", verified_at_utc=NOW,
        captured_at_utc=NOW, published_at_utc=None, available_at_utc=NOW, fact_category="OTHER_VERIFIED_FACT", assertion_class=AssertionClass.FACT,
        confidence="1", structured_payload=dict(category="OTHER_VERIFIED_FACT", statement="fixture", supporting_references=["fixture"], inference_basis="DIRECT_EVIDENCE"))
    clock = SyntheticProspectiveClock(NOW)
    assert verified_manual_bytes(tmp_path, claim, clock) == claim.source_hash
    for path in ("../fact.json", "C:/fact.json", ".env", "provider_capability_20260917/fact.json", "bundesliga_acceptance_20260911/fact.json"):
        with pytest.raises(ValueError):
            verified_manual_bytes(tmp_path, ManualVerifiedImportV1(**{**claim.model_dump(), "source_file": path}), clock)
    clock.at += timedelta(days=1)
    with pytest.raises(ValueError, match="EXPIRED_DELETE_ONLY"):
        verified_manual_bytes(tmp_path, claim, clock)
