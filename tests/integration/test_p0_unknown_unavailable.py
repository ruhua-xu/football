"""Reading UNKNOWN never grants scheduled-fixture or real decision eligibility."""
from datetime import datetime,timedelta,timezone

import pytest
from sqlalchemy import text

from football_system.domain.match import MatchStatus
from football_system.infrastructure.database.identity_repositories import SqlAlchemyMatchIdentityRepository
from tests.integration import test_real_bridge_repository as real_fixture
from tests.integration.test_fixture_ingestion_persistence import _capture

bridge=real_fixture.bridge


@pytest.mark.parametrize("unrecognized",["unknown","UNREVIEWED","ABANDONED",""])
def test_only_the_exact_unknown_status_is_added(unrecognized):
    assert MatchStatus("UNKNOWN").value=="UNKNOWN"
    with pytest.raises(ValueError):
        MatchStatus(unrecognized)


def test_existing_observation_gate_still_refuses_unknown_and_rolls_back(bridge):
    p=bridge
    run=real_fixture.prepare(p)
    at=p["clock"].now()+timedelta(minutes=2)
    identity=SqlAlchemyMatchIdentityRepository(p["sessions"],clock=lambda:at)
    tables=("fixture_ingestion_captures","fixture_observations","matches","canonical_match_identities","provider_match_mappings")
    with p["sessions"]() as s:
        before={t:s.execute(text('SELECT * FROM "'+t+'"')).all() for t in tables}
    with pytest.raises(ValueError,match="fixture ingestion conflicts with stored immutable data") as failed:
        identity.register_fixture_ingestion(_capture("p0-unknown",available_at=at-timedelta(minutes=1),status=MatchStatus.UNKNOWN))
    assert "ck_fixture_observation_status" in str(failed.value.__cause__)
    p["clock"].at=at
    catalog=identity.load_catalog(as_of_at_utc=at,kickoff_from_utc=datetime.min.replace(tzinfo=timezone.utc),kickoff_to_utc=datetime.max.replace(tzinfo=timezone.utc))
    assert len(catalog.canonical_anchors)==1 and catalog.canonical_anchors[0].match.status is MatchStatus.SCHEDULED
    with p["sessions"]() as s:
        assert {t:s.execute(text('SELECT * FROM "'+t+'"')).all() for t in tables}==before
        assert s.scalar(text("SELECT count(*) FROM fixture_observations WHERE status='UNKNOWN'"))==0
        assert s.scalar(text("SELECT count(*) FROM rb_locks"))==0
        assert s.scalar(text("SELECT count(*) FROM rb_prediction_slots"))==0
    assert p["repo"].load(run.artifact_id)==run
