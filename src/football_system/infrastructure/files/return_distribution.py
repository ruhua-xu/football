"""Local fixed configuration/code identity and strict JSON for return use cases."""

from functools import lru_cache
import hashlib
from importlib.resources import files
import json

from football_system.domain.return_distribution import ReturnDistributionPolicyV1, ReturnObjectiveProfileV1

CODE_FILES = (
    "domain/return_distribution.py",
    "domain/services/return_distribution.py",
    "domain/services/return_optimizer.py",
)
MAX_RETURN_ARTIFACT_BYTES = 64 * 1024 * 1024


@lru_cache(maxsize=1)
def return_code_hash():
    digest = hashlib.sha256(b"RETURN_DISTRIBUTION_CODE_LF_V1\0")
    for name in CODE_FILES:
        digest.update(name.encode() + b"\0")
        digest.update(files("football_system").joinpath(name).read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()


def default_return_configuration():
    return ReturnDistributionPolicyV1.freeze(code_hash=return_code_hash()), ReturnObjectiveProfileV1.freeze()


def strict_return_json(raw, *, limit=4*1024*1024):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise ValueError("return JSON byte bound exceeded")
    def reject(_):
        raise ValueError("float/NaN/Infinity JSON numbers are forbidden")
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value
    return json.loads(raw.decode("utf-8"), parse_float=reject, parse_constant=reject, object_pairs_hook=pairs)


def verify_resource_configuration(root):
    policy, objective = default_return_configuration()
    expected = (
        ("return_distribution_policy_v1.json", policy.model_dump(mode="json", exclude={"artifact_id", "content_hash", "code_hash"})),
        ("return_objective_profile_v1.json", objective.model_dump(mode="json", exclude={"artifact_id", "content_hash"})),
    )
    for name, data in expected:
        if strict_return_json((root / "config" / name).read_bytes()) != data:
            raise ValueError("fixed return configuration changed; explicit reviewed config version required")
    return policy, objective
