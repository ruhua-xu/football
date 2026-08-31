from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from football_system.domain.common import DomainModel, Identifier, UtcDateTime, stable_id
from football_system.domain.market import ThreeWayFixedBonus, ThreeWayMarketOdds, ThreeWayProbability
from football_system.domain.match import Competition, Team
from football_system.domain.prediction import ManualQuantInput


class MockMatchSeed(DomainModel):
    match_id: Identifier
    fixture_external_id: Identifier
    market_external_id: Identifier
    sporttery_match_no: Identifier
    competition_id: Identifier
    home_team_id: Identifier
    away_team_id: Identifier
    kickoff_at_utc: UtcDateTime
    available_at_utc: UtcDateTime
    market_captured_at_utc: UtcDateTime
    market_available_at_utc: UtcDateTime
    sporttery_captured_at_utc: UtcDateTime
    sporttery_available_at_utc: UtcDateTime
    market_odds: ThreeWayMarketOdds
    sporttery_bonus: ThreeWayFixedBonus
    manual_quant: ThreeWayProbability
    manual_quant_available_at_utc: UtcDateTime


class MockDataset(DomainModel):
    as_of_at_utc: UtcDateTime
    competitions: tuple[Competition, ...]
    teams: tuple[Team, ...]
    matches: tuple[MockMatchSeed, ...]

    @classmethod
    def from_json(cls, path: str | Path) -> MockDataset:
        raw = json.loads(Path(path).read_text(encoding="utf-8"), parse_float=Decimal)
        return cls.model_validate(raw)

    def manual_quant_inputs(
        self,
        match_ids: tuple[str, ...],
        as_of_at_utc: UtcDateTime,
    ) -> tuple[ManualQuantInput, ...]:
        selected = set(match_ids)
        market = _three_way_market()
        inputs: list[ManualQuantInput] = []
        for seed in self.matches:
            if (
                seed.match_id not in selected
                or seed.manual_quant_available_at_utc > as_of_at_utc
            ):
                continue
            digest = payload_hash(seed.manual_quant.model_dump(mode="json"))
            inputs.append(
                ManualQuantInput(
                    input_id=stable_id(
                        "manual-quant",
                        seed.match_id,
                        market.canonical,
                        seed.manual_quant_available_at_utc.isoformat(),
                        digest,
                    ),
                    match_id=seed.match_id,
                    market=market,
                    probabilities=seed.manual_quant,
                    available_at_utc=seed.manual_quant_available_at_utc,
                    payload_hash=digest,
                )
            )
        return tuple(inputs)


def payload_hash(payload: object) -> str:
    serialized = json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _three_way_market():
    from football_system.domain.market import MarketKey, MarketType

    return MarketKey(market_type=MarketType.THREE_WAY)
