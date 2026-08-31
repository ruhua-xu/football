from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, TypeAlias
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


UtcDateTime: TypeAlias = Annotated[datetime, AfterValidator(normalize_utc)]
Identifier: TypeAlias = Annotated[str, Field(min_length=1, max_length=160)]


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


def new_id() -> str:
    return str(uuid4())


def stable_id(kind: str, *parts: object) -> str:
    payload = "|".join((kind, *(str(part) for part in parts)))
    return str(uuid5(NAMESPACE_URL, payload))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
