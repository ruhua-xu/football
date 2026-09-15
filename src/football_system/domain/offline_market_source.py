import hashlib
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from football_system.domain.common import Identifier
from football_system.domain.market_v2 import Hash, MarketArtifact


class OfflineMarketSourceV1(MarketArtifact):
    schema_version: Literal["OFFLINE_MARKET_SOURCE_V1"] = "OFFLINE_MARKET_SOURCE_V1"
    source_reference: Identifier
    raw_json: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(
        min_length=1, max_length=4 * 1024 * 1024
    )
    raw_sha256: Hash
    classification: Literal["SYNTHETIC_ACCEPTANCE_DATA"] = "SYNTHETIC_ACCEPTANCE_DATA"

    @model_validator(mode="after")
    def raw_hash(self):
        if hashlib.sha256(self.raw_json.encode()).hexdigest() != self.raw_sha256:
            raise ValueError("offline fixture source hash mismatch")
        return self
