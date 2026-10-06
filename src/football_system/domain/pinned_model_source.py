"""Typed model-source references and verified read result; no forecast mathematics."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from football_system.domain.common import Identifier
from football_system.domain.market_analysis import MarketModelLineageV1
from football_system.domain.market_v2 import Hash, MultiMarketModel
from football_system.domain.services.elo_baseline import EloBaselineConfig, EloBaselineState


class LegacyModelSourceV1(MultiMarketModel):
    source_type: Literal["LEGACY"] = "LEGACY"


class OpenFootballModelSourceV1(MultiMarketModel):
    source_type: Literal["OPENFOOTBALL"] = "OPENFOOTBALL"
    release_id: Identifier
    release_hash: Hash
    binding_id: Identifier
    binding_hash: Hash


ModelSourceV1 = Annotated[LegacyModelSourceV1 | OpenFootballModelSourceV1, Field(discriminator="source_type")]


class VerifiedPinnedModelDescriptorV1(MultiMarketModel):
    """Returned by a verified reader only; never accepted as caller evidence."""
    source: ModelSourceV1
    state: EloBaselineState
    configuration: EloBaselineConfig
    lineage: MarketModelLineageV1
    release_hash: Hash
    authority_hash: Hash
    scope_match_ids: tuple[Identifier, ...]
    competition_id: Identifier | None = None
    season_id: Identifier

    @model_validator(mode="after")
    def consistent(self):
        if not (self.configuration.config_hash == self.state.config_hash == self.lineage.config_hash
                and self.state.state_hash == self.lineage.state_hash
                and self.state.training_data_hash == self.lineage.training_data_hash
                and self.state.season_id == self.season_id):
            raise ValueError("VERIFIED_MODEL_DESCRIPTOR_MISMATCH")
        if not self.scope_match_ids or self.scope_match_ids != tuple(sorted(set(self.scope_match_ids))):
            raise ValueError("VERIFIED_MODEL_SCOPE_REQUIRED")
        if self.source.source_type == "OPENFOOTBALL" and self.competition_id is None:
            raise ValueError("OFP_VERIFIED_COMPETITION_REQUIRED")
        return self
