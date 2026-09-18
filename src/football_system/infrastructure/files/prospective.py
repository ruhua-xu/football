"""Local-only evidence/provenance boundary and implementation identities."""

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
from importlib.resources import files
from pathlib import Path, PurePosixPath

from football_system.domain.common import normalize_utc
from football_system.domain.prospective import ProspectivePolicyV1
from football_system.infrastructure.files.training_evidence import LocalTrainingEvidence

MAX_MANUAL_BYTES = 4 * 1024 * 1024
MATH_FILES = (
    "domain/services/elo_baseline.py", "domain/services/poisson_goals.py", "domain/services/probability.py",
    "domain/services/payout.py", "domain/services/review_v4.py", "domain/review_v4.py",
    "domain/services/strategy_pass.py", "domain/services/strategy_pass_v2.py", "domain/services/settlement_v2.py",
    "domain/services/strategy_settlement.py", "domain/return_distribution.py",
    "domain/services/return_distribution.py", "domain/services/return_optimizer.py",
)
PROSPECTIVE_FILES = (
    "domain/prospective_evidence.py", "domain/prospective.py", "domain/services/prospective.py",
    "domain/services/prospective_validation.py", "infrastructure/database/prospective_repository.py",
    "infrastructure/database/prospective_schema.py", "infrastructure/files/prospective.py",
    "application/prospective.py", "application/prospective_requests.py", "interfaces/prospective_cli.py",
)


def source_hash(names, label):
    h = hashlib.sha256(label.encode()+b"\0")
    for name in names:
        h.update(name.encode()+b"\0")
        h.update(files("football_system").joinpath(name).read_bytes().replace(b"\r\n", b"\n"))
        h.update(b"\0")
    return h.hexdigest()


@lru_cache(maxsize=1)
def prospective_implementation_hash():
    return source_hash(PROSPECTIVE_FILES+MATH_FILES, "PROSPECTIVE_IMPLEMENTATION_LF_V1")


@lru_cache(maxsize=1)
def frozen_prediction_code_hash():
    return source_hash(MATH_FILES, "FROZEN_V090_PREDICTION_CODE_LF_V1")


def default_prospective_policy():
    return ProspectivePolicyV1.freeze(implementation_hash=prospective_implementation_hash(), frozen_math_hash=frozen_prediction_code_hash())


def verify_prospective_configuration(root):
    from football_system.infrastructure.files.return_distribution import strict_return_json
    policy = default_prospective_policy()
    expected = policy.model_dump(mode="json", exclude={"artifact_id", "content_hash", "implementation_hash", "frozen_math_hash"})
    if strict_return_json((Path(root) / "config/prospective_policy_v1.json").read_bytes()) != expected:
        raise ValueError("PROSPECTIVE_FIXED_RESOURCE_CONFIGURATION_CHANGED")
    return policy


class SystemProspectiveClock:
    basis = "LOCAL_SYSTEM_UTC"

    def now(self):
        return datetime.now(timezone.utc)


class SyntheticProspectiveClock:
    """Injection seam for fixtures only; its basis cannot qualify real evidence."""
    basis = "SYNTHETIC_TEST_CLOCK"

    def __init__(self, at):
        self.at = normalize_utc(at)

    def now(self):
        return self.at


def verified_manual_bytes(root, claim, clock):
    if clock.now() >= claim.retention_until_utc:
        raise ValueError("EVIDENCE_EXPIRED_DELETE_ONLY")
    root = Path(root)
    relative = PurePosixPath(claim.source_file)
    if relative.is_absolute() or any(p in {"", ".", ".."} for p in claim.source_file.split("/")) or "\\" in claim.source_file or ":" in claim.source_file:
        raise ValueError("CONTAINED_MANUAL_SOURCE_REQUIRED")
    forbidden = {"bundesliga_acceptance_20260911", "bundesliga_observed_training_draft", "bundesliga_probe_20260908"}
    for path in (root.absolute(), root.resolve(), root / claim.source_file):
        if any(part.casefold() in forbidden or part.casefold().startswith("provider_capability_") for part in path.parts):
            raise ValueError("CLOSED_CAPTURE_ROOT_FORBIDDEN")
    if any(p.casefold().startswith(".env") or p.casefold() in {"credentials", "credentials.json", "cookies", "cookies.txt"} for p in relative.parts):
        raise ValueError("SECRET_FILE_NOT_EVIDENCE")
    payload = LocalTrainingEvidence(root, trusted_authorities={}, max_bytes=MAX_MANUAL_BYTES).read(claim.source_file, claim.source_hash)
    return hashlib.sha256(payload).hexdigest()
