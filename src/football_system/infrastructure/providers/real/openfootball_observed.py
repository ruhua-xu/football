"""Offline OpenFootball inspection. No network, database, approval or Elo fit.

Inspection of arbitrary bytes is UNVERIFIED_SCAN, useful for diagnostics/tests.
Only the separate four-file evidence boundary can establish pinned acquisition.
All rows, including rejected rows, survive in the qualification result.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timezone
import hashlib
from importlib.resources import files
from io import BytesIO
import re
from zoneinfo import ZoneInfo

from football_system.domain.archive import canonical_json
from football_system.domain.common import normalize_utc
from football_system.domain.openfootball_snapshot import (
    OpenFootballAdapterPolicyV1,
    OpenFootballHistoricalValidationV1,
    OpenFootballRecordProvenanceV1,
    SEASONS,
)
from football_system.domain.training_admission import tagged_canonical_sha256
from football_system.infrastructure.files.training_evidence import strict_json_bytes


def _berlin(policy: OpenFootballAdapterPolicyV1) -> ZoneInfo:
    policy = OpenFootballAdapterPolicyV1.model_validate(policy)
    try:
        import tzdata
    except ImportError:
        raise ValueError("OPENFOOTBALL_PINNED_TZDATA_REQUIRED") from None
    if (tzdata.__version__, tzdata.IANA_VERSION) != (policy.tzdata_version, policy.iana_version):
        raise ValueError("OPENFOOTBALL_TZDATA_VERSION_MISMATCH")
    raw = files("tzdata").joinpath("zoneinfo/Europe/Berlin").read_bytes()
    if hashlib.sha256(raw).hexdigest() != policy.tzif_sha256:
        raise ValueError("OPENFOOTBALL_TZIF_HASH_MISMATCH")
    # Deliberately bypass host TZPATH and host ZoneInfo caches.
    return ZoneInfo.from_file(BytesIO(raw), key=policy.timezone)


def _date(value: object) -> date:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("DATE_REQUIRED")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("INVALID_LOCAL_DATE") from None


def _time(value: object) -> time:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{2}:[0-9]{2}", value) is None:
        raise ValueError("TIME_REQUIRED")
    try:
        return time.fromisoformat(value)
    except ValueError:
        raise ValueError("INVALID_LOCAL_TIME") from None


def _convert(local_date: date, local_time: time, zone: ZoneInfo) -> datetime:
    wall = datetime.combine(local_date, local_time)
    possibilities = set()
    for fold in (0, 1):
        utc = wall.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == wall:
            possibilities.add(utc)
    if not possibilities:
        raise ValueError("NONEXISTENT_LOCAL_TIME")
    if len(possibilities) != 1:
        raise ValueError("AMBIGUOUS_LOCAL_TIME")
    return next(iter(possibilities))


def local_kickoff_to_utc(date_text: str, time_text: str, *, policy: OpenFootballAdapterPolicyV1) -> datetime:
    """Minute-precision Berlin wall time; reject DST gap/fold instead of guessing."""
    return _convert(_date(date_text), _time(time_text), _berlin(policy))


def _goals(value: object) -> bool:
    return isinstance(value, list) and len(value) == 2 and all(type(n) is int and n >= 0 for n in value)


def inspect_openfootball_file(
    payload: bytes,
    *,
    filename: str,
    capture_at_utc: datetime,
    policy: OpenFootballAdapterPolicyV1,
) -> dict:
    """Scan native representation without admitting it or resolving team aliases.

    `score.ft` with optional consistent `ht` is a review candidate only. Direct
    score arrays mean upstream `reported`, whose period can be unknown; never FT.
    Any status (including awarded), extra period or unfamiliar field blocks use.
    The exact original pointer/file hash is kept independently of semantic order.
    """
    if type(payload) is not bytes or not 0 < len(payload) <= 2 * 1024 * 1024:
        raise ValueError("BOUNDED_ORIGINAL_BYTES_REQUIRED")
    if filename not in SEASONS:
        raise ValueError("OPENFOOTBALL_SOURCE_FILE_NOT_ALLOWED")
    capture_at_utc = normalize_utc(capture_at_utc)
    policy = OpenFootballAdapterPolicyV1.model_validate(policy)
    zone = _berlin(policy)
    root = strict_json_bytes(payload)
    season = SEASONS[filename]
    if not isinstance(root, dict) or set(root) != {"name", "matches"}:
        raise ValueError("SOURCE_SCHEMA_EXCEPTION_ROOT")
    if root["name"] != "Deutsche Bundesliga " + season:
        raise ValueError("BUNDESLIGA_REGULAR_LEAGUE_SCOPE_REQUIRED")
    if not isinstance(root["matches"], list) or not root["matches"]:
        raise ValueError("NONEMPTY_MATCH_ARRAY_REQUIRED")
    raw_hash, policy_hash = hashlib.sha256(payload).hexdigest(), policy.content_hash
    rows, dates, teams, rounds, identities = [], [], set(), Counter(), Counter()
    ft_count = array_count = missing_count = valid_date_count = valid_time_count = 0
    for index, original in enumerate(root["matches"]):
        pointer = f"/matches/{index}"
        diagnostics = []

        def issue(code, reason):
            diagnostics.append({"code": code, "reason": reason})

        record = original if isinstance(original, dict) else {}
        if not isinstance(original, dict):
            issue("MALFORMED_RECORD", "EXPECTED_OBJECT")
        if set(record) - {"round", "date", "time", "team1", "team2", "score", "status"}:
            issue("SOURCE_SCHEMA_EXCEPTION", "UNSUPPORTED_RECORD_FIELDS")
        local_date = local_time = kickoff = None
        try:
            local_date = _date(record.get("date"))
            dates.append(local_date.isoformat())
            valid_date_count += 1
        except ValueError as error:
            issue("MALFORMED_RECORD", str(error))
        try:
            local_time = _time(record.get("time"))
            valid_time_count += 1
        except ValueError as error:
            issue("MISSING_OR_INVALID_TIME", str(error))
        if local_date is not None and local_time is not None:
            try:
                kickoff = _convert(local_date, local_time, zone)
            except ValueError as error:
                issue("TIMEZONE_CONVERSION_REJECTED", str(error))
        if kickoff is not None and kickoff >= capture_at_utc:
            issue("CAPTURE_NOT_AFTER_KICKOFF", "FUTURE_OR_CURRENT_FIXTURE")
        names = [record.get(key) for key in ("team1", "team2")]
        identity = None
        if all(isinstance(n, str) and n and n == n.strip() for n in names) and names[0] != names[1]:
            teams.update(names)
            # Within these regular league files, a directed team pair is one fixture.
            # Also catches conflicting dates/scores masquerading as another match.
            identity = (season, *names)
            identities[identity] += 1
        else:
            issue("MALFORMED_RECORD", "TWO_DISTINCT_EXACT_TEAM_NAMES_REQUIRED")
        round_text = record.get("round")
        matchday = re.fullmatch(r"Matchday ([1-9]|[12][0-9]|3[0-4])", round_text) if isinstance(round_text, str) else None
        if matchday is None:
            issue("CUP_OR_NON_REGULAR_ROUND", "MATCHDAY_1_TO_34_REQUIRED")
        else:
            rounds[int(matchday[1])] += 1

        score = record.get("score")
        ft_count += int(isinstance(score, dict) and "ft" in score)
        array_count += int(isinstance(score, list))
        missing_count += int(score is None)
        goals = None
        if score is None:
            classification = "C_MISSING_SCORE"
            issue("MISSING_SCORE", "NO_RESULT_IN_SOURCE")
        elif isinstance(score, list):
            classification = "B_DIRECT_ARRAY"
            issue("SOURCE_SCHEMA_EXCEPTION", "REPORTED_SCORE_PERIOD_UNPROVEN")
            if not _goals(score):
                issue("MALFORMED_RECORD", "INVALID_DIRECT_SCORE_ARRAY")
        elif isinstance(score, dict) and set(score) <= {"ft", "ht"} and _goals(score.get("ft")):
            classification = "A_SCORE_FT"
            if "ht" in score and (not _goals(score["ht"]) or any(h > f for h, f in zip(score["ht"], score["ft"], strict=True))):
                classification = "D_ABNORMAL"
                issue("MALFORMED_RECORD", "HALF_TIME_INCONSISTENT_WITH_FULL_TIME")
            else:
                goals = tuple(score["ft"])
        else:
            classification = "D_ABNORMAL"
            if isinstance(score, dict) and set(score) & {"et", "aet", "p", "pen", "penalties", "agg"}:
                issue("EXTRA_TIME_CUP_OR_PENALTIES", "UNSUPPORTED_SCORE_PERIOD")
            else:
                issue("SOURCE_SCHEMA_EXCEPTION", "UNSUPPORTED_SCORE_REPRESENTATION")
                issue("MALFORMED_RECORD", "INVALID_FULL_TIME_SCORE")
        if "status" in record:
            classification = "D_ABNORMAL"
            issue("ABNORMAL_MATCH_STATUS", "STATUS_REQUIRES_SEPARATE_REVIEW")

        provenance = OpenFootballRecordProvenanceV1(
            source_filename=filename, source_file_sha256=raw_hash,
            original_record_pointer=pointer,
            original_record_sha256=tagged_canonical_sha256("OPENFOOTBALL_ORIGINAL_RECORD_V1", original),
            capture_at_utc=capture_at_utc,
            local_date_text=record.get("date") if isinstance(record.get("date"), str) else None,
            local_time_text=record.get("time") if isinstance(record.get("time"), str) else None,
            adapter_policy_hash=policy_hash,
        )
        rows.append(dict(provenance=provenance.model_dump(mode="json"), season=season,
            source_team1=names[0] if isinstance(names[0], str) else None,
            source_team2=names[1] if isinstance(names[1], str) else None,
            kickoff_at_utc=kickoff.isoformat() if kickoff is not None else None,
            matchday=int(matchday[1]) if matchday else None,
            source_status=record.get("status"), score_class=classification,
            regular_time_score_candidate=goals if not diagnostics else None,
            diagnostics=diagnostics, identity_key=identity))

    for row in rows:
        if row["identity_key"] is not None and identities[row["identity_key"]] > 1:
            row["diagnostics"].append(dict(code="DUPLICATE_MATCH", reason="DIRECTED_PAIR_REPEATED_IN_SEASON"))
            row["regular_time_score_candidate"] = None
    rows.sort(key=lambda r: (r["kickoff_at_utc"] or "", r["source_team1"] or "", r["source_team2"] or "", r["provenance"]["original_record_pointer"]))
    semantic = [dict(season=r["season"], teams=(r["source_team1"], r["source_team2"]),
        kickoff=r["kickoff_at_utc"], score=r["regular_time_score_candidate"], score_class=r["score_class"],
        diagnostics=r["diagnostics"]) for r in rows]
    semantic.sort(key=canonical_json)
    categories = Counter(r["score_class"] for r in rows)
    quality = dict(raw_match_count=len(rows), unique_match_count=len(identities),
        date_complete_count=valid_date_count, time_complete_count=valid_time_count,
        score_ft_count=ft_count, direct_score_array_count=array_count, missing_score_count=missing_count,
        duplicate_count=sum(n - 1 for n in identities.values()), team_count=len(teams), teams=sorted(teams),
        matchday_coverage={str(k): v for k, v in sorted(rounds.items())}, date_min=min(dates) if dates else None,
        date_max=max(dates) if dates else None,
        malformed_records=sum(any(d["code"] == "MALFORMED_RECORD" for d in r["diagnostics"]) for r in rows),
        schema_exceptions=sum(any(d["code"] == "SOURCE_SCHEMA_EXCEPTION" for d in r["diagnostics"]) for r in rows),
        abnormal_records=categories["D_ABNORMAL"],
        structurally_eligible_record_count=sum(not r["diagnostics"] for r in rows),
        score_classes={k: categories[k] for k in ("A_SCORE_FT", "B_DIRECT_ARRAY", "C_MISSING_SCORE", "D_ABNORMAL")})
    return dict(schema_version="OPENFOOTBALL_FILE_INSPECTION_V1", source_binding="UNVERIFIED_SCAN",
        source_filename=filename, source_file_sha256=raw_hash, source_name=root["name"],
        adapter_policy_hash=policy_hash, quality=quality, records=rows,
        semantic_records_hash=tagged_canonical_sha256("OPENFOOTBALL_SEMANTIC_SCAN_V1", semantic),
        historical_validation=OpenFootballHistoricalValidationV1().model_dump(mode="json"),
        production_training_authorized=False)
