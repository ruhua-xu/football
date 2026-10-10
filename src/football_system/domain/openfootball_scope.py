"""P1-A closed scope contracts, not qualification/admission or a production reader.

Refs are inert until P1-B resolves current authority, immutable evidence and FKs
in the caller's transaction. No V1 object, writer, fact universe or routing changes.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import PurePosixPath
import re
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from football_system.domain.archive import canonical_json, match_result_payload_sha256
from football_system.domain.common import UtcDateTime, stable_id
from football_system.domain.openfootball_snapshot import Digest, Label, SnapshotModel
from football_system.domain.openfootball_production import CONFIG_HASH, SOURCE_ID, fixed_window, require
from football_system.domain.services.elo_baseline import EloBaselineState, EloRegularTimeResult, EloThreeWayBaseline
from football_system.domain.training_admission import tagged_canonical_sha256


class ScopeEvidenceRefV2(SnapshotModel):
    evidence_id: Label
    evidence_sha256: Digest


class ScopeFileV2(SnapshotModel):
    path: Label
    role: Literal["MATCHES", "README", "LICENSE"]
    bytes: int = Field(strict=True, gt=0)
    sha256: Digest
    record_count: int = Field(strict=True, ge=0)
    season_id: Label | None = None
    captured_at_utc: UtcDateTime

    @model_validator(mode="after")
    def safe_file(self):
        path = PurePosixPath(self.path)
        require(path.parts and not path.is_absolute() and path.as_posix() == self.path and ":" not in self.path and "\0" not in self.path
            and "\\" not in self.path and all(p not in {".", ".."} for p in path.parts), "SCOPE_SOURCE_PATH_INVALID")
        require((self.record_count > 0 and self.season_id is not None) if self.role == "MATCHES"
            else (self.record_count == 0 and self.season_id is None), "SCOPE_SOURCE_ROLE_MISMATCH")
        return self


class ScopeTimePolicyV2(SnapshotModel):
    timezone: Label
    tzdata_version: Literal["2025.2"] = "2025.2"
    iana_version: Literal["2025b"] = "2025b"
    tzif_sha256: Digest
    source_precision: Literal["MINUTE"] = "MINUTE"
    fold_policy: Literal["REJECT_AMBIGUOUS"] = "REJECT_AMBIGUOUS"
    gap_policy: Literal["REJECT_NONEXISTENT"] = "REJECT_NONEXISTENT"
    source_time_review: ScopeEvidenceRefV2


class ScopeSeasonV2(SnapshotModel):
    sequence: int = Field(strict=True, ge=0)
    season_id: Label
    source_label: Label
    role: Literal["WARMUP", "PILOT_TARGET", "PRODUCTION_TARGET"]


class ScopeMappingV2(SnapshotModel):
    kind: Literal["TEAM", "COMPETITION", "SEASON"]
    source_label: Label
    canonical_id: Label
    method: Literal["REUSE_EXISTING_REVIEWED", "EXPLICIT_NEW_REVIEWED"]
    evidence: ScopeEvidenceRefV2

    @model_validator(mode="after")
    def opaque(self):
        require(self.canonical_id != self.source_label, "DISPLAY_LABEL_IS_NOT_CANONICAL_ID")
        return self


class ScopeRecordV2(SnapshotModel):
    file: Label
    index: int = Field(strict=True, ge=0)
    raw_record_sha256: Digest
    disposition: Literal["FACT_CANDIDATE", "EXCEPTION"]
    candidate_result: EloRegularTimeResult | None = None
    reason: Literal["REPORTED_SCORE_PERIOD_UNPROVEN", "ABNORMAL_MATCH_STATUS", "MISSING_OR_INVALID_SCORE",
        "IDENTITY_UNRESOLVED", "SOURCE_TIME_UNPROVEN"] | None = None
    review: ScopeEvidenceRefV2

    @model_validator(mode="after")
    def exclusive(self):
        require((self.candidate_result is not None and self.reason is None) if self.disposition == "FACT_CANDIDATE"
            else (self.candidate_result is None and self.reason is not None), "SCOPE_RECORD_DISPOSITION_MISMATCH")
        return self


class CompetitionScopeProfileV2(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_COMPETITION_PROFILE_V2"] = "OPENFOOTBALL_COMPETITION_PROFILE_V2"
    competition_id: Label
    source_competition_id: Label
    source_competition_label: Label
    competition_type: Literal["DOMESTIC_LEAGUE"] = "DOMESTIC_LEAGUE"
    stage: Literal["REGULAR_LEAGUE_MATCHES"] = "REGULAR_LEAGUE_MATCHES"
    repository: Literal["openfootball/football.json"] = "openfootball/football.json"
    commit: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{40}$")]
    files: tuple[ScopeFileV2, ...]
    parser_policy_hash: Digest
    time_policy: ScopeTimePolicyV2
    mapping: tuple[ScopeMappingV2, ...]
    window: tuple[ScopeSeasonV2, ...]
    records: tuple[ScopeRecordV2, ...]
    cohort_review: ScopeEvidenceRefV2
    model_name: Literal["ELO_THREE_WAY_BASELINE_V1"] = "ELO_THREE_WAY_BASELINE_V1"
    model_version: Literal["1"] = "1"
    config_hash: Literal[CONFIG_HASH] = CONFIG_HASH
    source_data_mode: Literal["SOURCE_TIME_RESEARCH"] = "SOURCE_TIME_RESEARCH"
    evidence_basis: Literal["CURRENT_SNAPSHOT_OBSERVED"] = "CURRENT_SNAPSHOT_OBSERVED"
    provider_publication_at_utc: None = None
    provider_finalized_at_utc: None = None
    historical_version_time: Literal["UNKNOWN"] = "UNKNOWN"
    exception_selection: Literal["SOURCE_SEMANTICS_ONLY_NO_MODEL_OR_ROI_SELECTION"] = "SOURCE_SEMANTICS_ONLY_NO_MODEL_OR_ROI_SELECTION"

    @model_validator(mode="after")
    def complete_scope(self):
        names = tuple(f.path for f in self.files)
        require(names == tuple(sorted(set(names))) and len(names) >= 4, "SCOPE_FILE_MANIFEST_REQUIRED")
        require(sum(f.role == "README" for f in self.files) == sum(f.role == "LICENSE" for f in self.files) == 1,
            "SCOPE_LICENSE_AND_SCHEMA_EVIDENCE_REQUIRED")
        require(len(self.window) >= 3 and tuple(s.sequence for s in self.window) == tuple(range(len(self.window)))
            and tuple(s.role for s in self.window[-2:]) == ("PILOT_TARGET", "PRODUCTION_TARGET")
            and all(s.role == "WARMUP" for s in self.window[:-2]), "SCOPE_ORDERED_WINDOW_REQUIRED")
        require(len({s.season_id for s in self.window}) == len({s.source_label for s in self.window}) == len(self.window),
            "SCOPE_SEASON_COLLISION")
        data = {f.path: f for f in self.files if f.role == "MATCHES"}
        require({f.season_id for f in data.values()} == {s.season_id for s in self.window[:-1]}, "SCOPE_ZERO_FACT_TARGET_REQUIRED")
        keys = tuple((m.kind, m.source_label) for m in self.mapping)
        require(keys == tuple(sorted(set(keys))), "SCOPE_MAPPING_KEYS_REQUIRED")
        mapped = {(m.kind, m.source_label): m.canonical_id for m in self.mapping}
        require({key for key in mapped if key[0] != "TEAM"} == {("COMPETITION", self.source_competition_label)}
            | {("SEASON", s.source_label) for s in self.window}, "SCOPE_CANONICAL_CONTEXT_REQUIRED")
        require(mapped[("COMPETITION", self.source_competition_label)] == self.competition_id
            and all(mapped[("SEASON", s.source_label)] == s.season_id for s in self.window), "SCOPE_IDENTITY_BINDING_MISMATCH")
        kinds = {}
        for m in self.mapping:
            require(m.canonical_id not in kinds or kinds[m.canonical_id] == m.kind == "TEAM", "SCOPE_CANONICAL_ID_COLLISION")
            kinds[m.canonical_id] = m.kind
        expected = {(f.path, i) for f in data.values() for i in range(f.record_count)}
        record_keys = tuple((r.file, r.index) for r in self.records)
        require(record_keys == tuple(sorted(expected)), "SCOPE_COMPLETE_RAW_CENSUS_REQUIRED")
        facts = [r.candidate_result for r in self.records if r.disposition == "FACT_CANDIDATE"]
        require(facts and len({f.match_id for f in facts}) == len({f.match_result_id for f in facts}) == len(facts), "SCOPE_FACT_ID_COLLISION")
        teams = {m.canonical_id for m in self.mapping if m.kind == "TEAM"}
        for record in self.records:
            if record.candidate_result is not None:
                fact, source = record.candidate_result, data[record.file]
                require(fact.season_id == source.season_id and {fact.home_team_id, fact.away_team_id} <= teams,
                    "SCOPE_FACT_OWNERSHIP_MISMATCH")
                require(fact.kickoff_at_utc < source.captured_at_utc <= fact.available_at_utc <= fact.ingested_at_utc,
                    "SCOPE_OBSERVED_TIMELINE_INVALID")
                require(fact.supersedes_match_result_id is None, "SCOPE_INITIAL_COHORT_NO_IMPLICIT_CORRECTION")
        return self

    @property
    def source_id(self):
        return stable_id("OPENFOOTBALL_SCOPE_SOURCE_V2", self.repository, self.commit, self.competition_id, self.source_competition_id)

    @property
    def mapping_root(self):
        return tagged_canonical_sha256("OFP_SCOPE_MAPPING_V2", self.mapping)

    @property
    def window_hash(self):
        return tagged_canonical_sha256("OFP_SCOPE_WINDOW_V2", dict(competition_id=self.competition_id, seasons=self.window))

    @property
    def facts_root(self):
        return tagged_canonical_sha256("OFP_SCOPE_FACTS_V2", tuple(r for r in self.records if r.disposition == "FACT_CANDIDATE"))

    @property
    def exceptions_root(self):
        return tagged_canonical_sha256("OFP_SCOPE_EXCEPTIONS_V2", tuple(r for r in self.records if r.disposition == "EXCEPTION"))


class ScopeIdentityV2(SnapshotModel):
    scope_id: Label
    scope_hash: Digest
    competition_id: Label

    @classmethod
    def of(cls, profile: CompetitionScopeProfileV2):
        value = CompetitionScopeProfileV2.model_validate(profile)
        return cls(scope_id=stable_id("OPENFOOTBALL_QUALIFIED_SCOPE_V2", value.content_hash),
            scope_hash=value.content_hash, competition_id=value.competition_id)


class ScopeAuthorityRefV2(SnapshotModel):
    owner: ScopeIdentityV2
    kind: Literal["QUALIFICATION", "SOURCE_RIGHTS", "TRUSTED_AUTHORITY"]
    artifact_id: Label
    artifact_hash: Digest


class QualifiedCompetitionScopeV2(SnapshotModel):
    """An immutable contract declaration. A caller cannot self-assert verified authority."""
    schema_version: Literal["OPENFOOTBALL_QUALIFIED_SCOPE_V2"] = "OPENFOOTBALL_QUALIFIED_SCOPE_V2"
    identity: ScopeIdentityV2
    profile: CompetitionScopeProfileV2
    qualification: ScopeAuthorityRefV2
    rights: ScopeAuthorityRefV2
    authority: ScopeAuthorityRefV2

    @model_validator(mode="after")
    def refs(self):
        require(self.identity == ScopeIdentityV2.of(self.profile), "SCOPE_SEAL_MISMATCH")
        for name, kind in (("qualification", "QUALIFICATION"), ("rights", "SOURCE_RIGHTS"), ("authority", "TRUSTED_AUTHORITY")):
            ref = getattr(self, name)
            require(ref.owner == self.identity and ref.kind == kind, "SCOPE_AUTHORITY_OWNERSHIP_MISMATCH")
        require(len({r.artifact_id for r in (self.qualification, self.rights, self.authority)}) == 3, "SCOPE_AUTHORITY_REF_COLLISION")
        return self


class LegacyBundesligaScopeV1(SnapshotModel):
    """Inert V1 discriminator; the unchanged existing repository remains authoritative."""
    schema_version: Literal["LEGACY_BUNDESLIGA_SCOPE_V1"] = "LEGACY_BUNDESLIGA_SCOPE_V1"
    source_id: Literal[SOURCE_ID] = SOURCE_ID
    competition_id: Literal["4ed332b3-3b22-5645-bcbf-3c1895ea653e"] = "4ed332b3-3b22-5645-bcbf-3c1895ea653e"
    window_hash: Literal["0e4c4ddbaafee6600de070d05f344ce9384742b81ff4191dc98b36cef9367116"] = fixed_window().content_hash
    historical_counts: tuple[Literal[612], Literal[13], Literal[599]] = (612, 13, 599)


CompetitionScopeContract = Annotated[LegacyBundesligaScopeV1 | QualifiedCompetitionScopeV2, Field(discriminator="schema_version")]


class ScopeInstanceArtifactRefV2(SnapshotModel):
    owner: ScopeIdentityV2
    bootstrap_instance_id: Label
    kind: Literal["APPROVAL", "STATE_BINDING", "RELEASE", "MODEL_PIN"]
    artifact_id: Label
    artifact_hash: Digest


class ScopedModelBindingV2(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_SCOPED_MODEL_BINDING_V2"] = "OPENFOOTBALL_SCOPED_MODEL_BINDING_V2"
    owner: ScopeIdentityV2
    bootstrap_instance_id: Label
    source_id: Label
    facts_root: Digest
    window_hash: Digest
    program_id: Label
    target_match_ids: tuple[Label, ...]
    state_hash: Digest
    training_data_hash: Digest
    config_hash: Literal[CONFIG_HASH] = CONFIG_HASH
    approval: ScopeInstanceArtifactRefV2
    state_binding: ScopeInstanceArtifactRefV2
    release: ScopeInstanceArtifactRefV2
    pin: ScopeInstanceArtifactRefV2

    @model_validator(mode="after")
    def ownership(self):
        require(self.target_match_ids and self.target_match_ids == tuple(sorted(set(self.target_match_ids))), "SCOPE_EXACT_TARGETS_REQUIRED")
        refs = []
        for name, kind in (("approval", "APPROVAL"), ("state_binding", "STATE_BINDING"), ("release", "RELEASE"), ("pin", "MODEL_PIN")):
            ref = getattr(self, name)
            require((ref.owner, ref.bootstrap_instance_id, ref.kind) == (self.owner, self.bootstrap_instance_id, kind),
                "SCOPE_MODEL_GRAPH_OWNERSHIP_MISMATCH")
            refs.append(ref.artifact_id)
        require(len(set(refs)) == 4, "SCOPE_MODEL_REF_COLLISION")
        return self


def logical_bootstrap_key(profile: CompetitionScopeProfileV2, target_match_ids: tuple[str, ...]) -> str:
    """P1-B must UNIQUE this key; changing a request/instance/reviewer/cutoff cannot retry."""
    value = CompetitionScopeProfileV2.model_validate(profile)
    require(target_match_ids and target_match_ids == tuple(sorted(set(target_match_ids))), "SCOPE_EXACT_TARGETS_REQUIRED")
    return tagged_canonical_sha256("OFP_LOGICAL_BOOTSTRAP_V2", dict(competition_id=value.competition_id,
        repository=value.repository, commit=value.commit, source_competition_id=value.source_competition_id,
        files=tuple((f.path, f.sha256) for f in value.files if f.role == "MATCHES"), window=value.window,
        canonical_mapping=tuple((m.kind, m.source_label, m.canonical_id) for m in value.mapping), targets=target_match_ids))


def validate_scoped_model_state(scope: QualifiedCompetitionScopeV2, binding: ScopedModelBindingV2, state: EloBaselineState) -> None:
    """Pure exact replay/ownership check, never a substitute for current repository authority."""
    scope = QualifiedCompetitionScopeV2.model_validate(scope)
    binding = ScopedModelBindingV2.model_validate(binding)
    state = EloBaselineState.model_validate(state.model_dump(mode="python"))
    profile = scope.profile
    require(binding.owner == scope.identity and binding.source_id == profile.source_id
        and binding.facts_root == profile.facts_root and binding.window_hash == profile.window_hash, "SCOPE_MODEL_BINDING_MISMATCH")
    require(binding.bootstrap_instance_id == stable_id("OFP_BOOTSTRAP_INSTANCE_V2", logical_bootstrap_key(profile, binding.target_match_ids)),
        "SCOPE_LOGICAL_INSTANCE_MISMATCH")
    facts = tuple(r.candidate_result for r in profile.records if r.candidate_result is not None)
    require(not set(binding.target_match_ids) & {f.match_id for f in facts}
        and all(f.ingested_at_utc < state.cutoff_at_utc for f in facts), "SCOPE_MODEL_CUTOFF_OR_TARGET_INTERSECTION")
    rebuilt = EloThreeWayBaseline().rebuild_state(facts, state.cutoff_at_utc, target_season_id=profile.window[-1].season_id)
    require(state == rebuilt and (state.state_hash, state.training_data_hash, state.config_hash)
        == (binding.state_hash, binding.training_data_hash, binding.config_hash), "SCOPE_INDEPENDENT_STATE_REPLAY_MISMATCH")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "SCOPE_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def validate_scope_source_bytes(profile: CompetitionScopeProfileV2, raw_files: Mapping[str, bytes], tzif_bytes: bytes) -> None:
    """Complete raw census, record/mapping/FT binding; rights and source-time review remain external."""
    value = CompetitionScopeProfileV2.model_validate(profile)
    require(set(raw_files) == {f.path for f in value.files}, "SCOPE_EXACT_RAW_FILE_SET_REQUIRED")
    require(hashlib.sha256(tzif_bytes).hexdigest() == value.time_policy.tzif_sha256, "SCOPE_TZIF_BYTES_MISMATCH")
    zone = ZoneInfo.from_file(BytesIO(tzif_bytes), key=value.time_policy.timezone)
    mapped = {(m.kind, m.source_label): m.canonical_id for m in value.mapping}
    records = {(r.file, r.index): r for r in value.records}
    team_labels = set()
    for file in value.files:
        raw = raw_files[file.path]
        require(len(raw) == file.bytes and hashlib.sha256(raw).hexdigest() == file.sha256, "SCOPE_SOURCE_FILE_HASH_MISMATCH")
        if file.role != "MATCHES":
            continue
        document = json.loads(raw, object_pairs_hook=_unique_object)
        require(isinstance(document, dict) and isinstance(document.get("matches"), list)
            and len(document["matches"]) == file.record_count, "SCOPE_RAW_CENSUS_MISMATCH")
        for index, row in enumerate(document["matches"]):
            record = records[(file.path, index)]
            require(isinstance(row, dict) and hashlib.sha256(canonical_json(row).encode()).hexdigest() == record.raw_record_sha256,
                "SCOPE_RAW_RECORD_HASH_MISMATCH")
            require(all(isinstance(row.get(key), str) and row[key] for key in ("team1", "team2")), "SCOPE_RAW_TEAM_LABEL_REQUIRED")
            team_labels.update((row["team1"], row["team2"]))
            if record.candidate_result is not None:
                fact, score = record.candidate_result, row.get("score")
                require(isinstance(score, dict) and isinstance(score.get("ft"), list) and len(score["ft"]) == 2
                    and all(type(goal) is int and goal >= 0 for goal in score["ft"])
                    and row.get("status") in (None, "finished")
                    and (fact.home_goals, fact.away_goals) == tuple(score["ft"]), "SCOPE_EXPLICIT_REGULAR_TIME_SCORE_REQUIRED")
                require((fact.home_team_id, fact.away_team_id) == (mapped.get(("TEAM", row["team1"])), mapped.get(("TEAM", row["team2"]))),
                    "SCOPE_RAW_MAPPING_MISMATCH")
                date, time = row.get("date"), row.get("time")
                require(isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
                    and isinstance(time, str) and re.fullmatch(r"\d{2}:\d{2}", time), "SCOPE_SOURCE_LOCAL_TIME_REQUIRED")
                local = datetime.strptime(date + " " + time, "%Y-%m-%d %H:%M")
                candidates = {local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc) for fold in (0, 1)
                    if local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == local}
                require(len(candidates) == 1, "SCOPE_DST_AMBIGUOUS_OR_NONEXISTENT")
                require(fact.kickoff_at_utc == next(iter(candidates))
                    and fact.payload_hash == match_result_payload_sha256(fact.home_goals, fact.away_goals), "SCOPE_NORMALIZED_FACT_MISMATCH")
    require({m.source_label for m in value.mapping if m.kind == "TEAM"} == team_labels, "SCOPE_COMPLETE_TEAM_MAPPING_REQUIRED")
