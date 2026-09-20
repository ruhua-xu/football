"""Implementation pins and local proof bytes. Never builds a network transport."""

from pathlib import Path

from football_system.domain.real_bridge import require
from football_system.infrastructure.files.prospective import source_hash, default_prospective_policy
from football_system.infrastructure.files.return_distribution import default_return_configuration
from football_system.infrastructure.files.training_evidence import LocalTrainingEvidence

BRIDGE_FILES = ("domain/real_bridge.py", "domain/services/real_bridge.py", "application/real_bridge.py", "application/real_bridge_requests.py", "application/real_bridge_views.py",
    "infrastructure/database/real_bridge_repository.py", "infrastructure/database/real_bridge_schema.py", "infrastructure/database/real_bridge_projections.py",
    "infrastructure/database/real_bridge_sources.py", "infrastructure/files/real_bridge.py", "interfaces/real_bridge_cli.py")

FROZEN_IDENTITIES = dict(
    return_algorithm="76995ed699a8d054f3865ffbbc0063a46af2123f18ab34aa6b27a91690dc7aa6",
    return_policy="bd9cf8b2c3d36ce13327cb49b9513c4d6c995762387606ce26bba37ef0769b47",
    objective="9bb39a771ac2390cb537030c809a7e214dcf88bcce2be17808bc7b3dcbfe7c30",
    prospective_implementation="6b2a73c14720c60eb30f9bc5d4158bccf095d397748cd1157eb53f13933ddb1f",
    prospective_policy="06f9cb5289959cf6387b324f9a8ef617d1960a68e88e23cb1423e0c15ef847f8",
    market_consensus="c6c22bfb8a2c53aeb624b485c73c6e144c65f1a3cd8b6df2d7c5f32a88cc914f",
)


def identities():
    old = default_prospective_policy()
    policy, objective = default_return_configuration()
    hashes = dict(return_algorithm=policy.code_hash, return_policy=policy.content_hash, objective=objective.content_hash,
        prospective_implementation=old.implementation_hash, prospective_policy=old.content_hash,
        market_consensus=source_hash(("application/market_consensus.py", "domain/services/probability.py",
            "infrastructure/providers/real/the_odds_api.py"), "MARKET_CONSENSUS_MEDIAN_V1_LF"))
    require(hashes==FROZEN_IDENTITIES,"FROZEN_V100_MATH_IDENTITY_CHANGED")
    return source_hash(BRIDGE_FILES, "REAL_PROSPECTIVE_DECISION_ADAPTER_V1_LF"), hashes


class BridgeEvidence(LocalTrainingEvidence):
    def read(self, reference, expected_sha256=None):
        forbidden = {"bundesliga_acceptance_20260911", "bundesliga_observed_training_draft", "bundesliga_probe_20260908"}
        for path in (Path(self.root).absolute(), Path(self.root).resolve(), Path(self.root) / reference):
            require(not any(p.casefold() in forbidden or p.casefold().startswith("provider_capability_") for p in path.parts), "CLOSED_CAPTURE_ROOT_FORBIDDEN")
        require(not any(p.casefold().startswith(".env") or p.casefold() in {"credentials.json", "cookies.txt"} for p in Path(reference).parts), "SECRET_IS_NOT_SOURCE_EVIDENCE")
        return super().read(reference, expected_sha256)
