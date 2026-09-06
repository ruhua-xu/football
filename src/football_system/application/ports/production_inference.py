"""Internal pinned-inference contract, separate from model state and V3 packets."""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import model_validator

from football_system.domain.common import Identifier, UtcDateTime
from football_system.domain.production_release import (
    CurrentAuthorizationInputsV1,
    ProductionQuantModelReleaseV1,
    ProductionTargetAcceptancePlanV1,
    ReleaseArtifactRefV1,
    ReleaseSnapshotV1,
    RetentionHorizonV1,
    assert_authorization_progression,
)
from football_system.domain.training_admission import (
    Sha256Digest,
    tagged_canonical_sha256,
)


class ProductionInferenceBindingV1(ReleaseSnapshotV1):
    schema_version: Literal["PRODUCTION_INFERENCE_BINDING_V1"] = (
        "PRODUCTION_INFERENCE_BINDING_V1"
    )
    analysis_run_id: Identifier
    quant_model_state_id: Identifier
    release: ReleaseArtifactRefV1
    target_acceptance_plan: ReleaseArtifactRefV1
    released_state_core_hash: Sha256Digest
    training_cutoff_at_utc: UtcDateTime
    training_data_hash: Sha256Digest
    approved_facts_hash: Sha256Digest
    start_authorization: CurrentAuthorizationInputsV1
    completion_authorization: CurrentAuthorizationInputsV1
    state_retention_horizon: RetentionHorizonV1
    audit_retention_horizon: RetentionHorizonV1

    @property
    def binding_hash(self) -> str:
        return tagged_canonical_sha256(self.schema_version, self)

    @model_validator(mode="after")
    def validate_timeline(self):
        assert_authorization_progression(
            self.start_authorization, self.completion_authorization
        )
        if self.training_cutoff_at_utc >= self.start_authorization.actual_at_utc:
            raise ValueError("production training cutoff must precede inference")
        return self


class ProductionInferenceRepository(Protocol):
    """Reads verify durable evidence; no training provider or approval writer.

    Authorization returns inputs, not permission. Callers must additionally call
    release_active_for_inference at their actual operation boundaries.
    """

    def load_release(self, release_id: str) -> ProductionQuantModelReleaseV1: ...

    def load_target_plan(self, plan_id: str) -> ProductionTargetAcceptancePlanV1: ...

    def authorization(
        self, release_id: str, at_utc: datetime
    ) -> CurrentAuthorizationInputsV1: ...

    def load_binding(
        self, analysis_run_id: str
    ) -> ProductionInferenceBindingV1 | None: ...
