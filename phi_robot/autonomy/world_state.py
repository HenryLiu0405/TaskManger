"""Orthogonal, sourced, timestamped robot world-state dimensions."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Optional


WORLD_STATE_SCHEMA_VERSION = "1.0"


class StateDimension(str, Enum):
    MISSION = "mission"
    MOTION = "motion"
    PAYLOAD = "payload"
    PERCEPTION = "perception"
    CONTROL = "control"
    EXECUTION = "execution"


ALLOWED_VALUES: dict[StateDimension, frozenset[str]] = {
    StateDimension.MISSION: frozenset(
        {"idle", "ready", "running", "recovering", "paused", "completed", "failed"}
    ),
    StateDimension.MOTION: frozenset({"stopped", "moving", "arrived"}),
    StateDimension.PAYLOAD: frozenset(
        {"empty", "holding", "drop_suspected", "dropped"}
    ),
    StateDimension.PERCEPTION: frozenset({"fresh", "stale", "unavailable"}),
    StateDimension.CONTROL: frozenset({"autonomous", "operator", "estop"}),
    StateDimension.EXECUTION: frozenset(
        {"idle", "running", "cancel_requested", "reconciliation_required"}
    ),
}


@dataclass(frozen=True)
class StateValue:
    """One fact with provenance and explicit unknown semantics.

    ``value=None`` is the only representation of an unknown value.  No numeric
    zero, empty string, or guessed state is substituted by serialization.
    """

    value: Optional[str] = None
    source: str = "unknown"
    observed_at: Optional[float] = None
    valid_until: Optional[float] = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def known(self) -> bool:
        return self.value is not None

    def is_fresh(self, now: Optional[float] = None) -> bool:
        if not self.known or self.observed_at is None:
            return False
        return self.valid_until is None or self.valid_until >= (now if now is not None else time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "known": self.known,
            "source": self.source,
            "observed_at": self.observed_at,
            "valid_until": self.valid_until,
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StateValue":
        value = data.get("value")
        return cls(
            value=str(value) if value is not None else None,
            source=str(data.get("source", "unknown")),
            observed_at=(float(data["observed_at"]) if data.get("observed_at") is not None else None),
            valid_until=(float(data["valid_until"]) if data.get("valid_until") is not None else None),
            evidence=dict(data.get("evidence") or {}),
        )


def _unknown_dimensions() -> dict[str, StateValue]:
    return {dimension.value: StateValue() for dimension in StateDimension}


@dataclass(frozen=True)
class RobotWorldState:
    robot_id: str
    world_version: int = 0
    dimensions: Mapping[str, StateValue] = field(default_factory=_unknown_dimensions)
    updated_at: float = field(default_factory=time.time)
    schema_version: str = WORLD_STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.robot_id:
            raise ValueError("robot_id must be non-empty")
        if self.schema_version != WORLD_STATE_SCHEMA_VERSION:
            raise ValueError(f"unsupported world-state schema_version: {self.schema_version}")
        missing = {dimension.value for dimension in StateDimension} - set(self.dimensions)
        if missing:
            raise ValueError(f"world state is missing dimensions: {sorted(missing)}")
        for dimension in StateDimension:
            fact = self.dimensions[dimension.value]
            if not isinstance(fact, StateValue):
                raise ValueError(f"{dimension.value} must be a StateValue")
            if fact.value is not None and fact.value not in ALLOWED_VALUES[dimension]:
                raise ValueError(
                    f"invalid {dimension.value} state {fact.value!r}; "
                    f"expected one of {sorted(ALLOWED_VALUES[dimension])} or unknown"
                )

    def fact(self, dimension: StateDimension | str) -> StateValue:
        key = dimension.value if isinstance(dimension, StateDimension) else str(dimension)
        return self.dimensions[key]

    def update(
        self,
        dimension: StateDimension | str,
        value: Optional[str],
        *,
        source: str,
        observed_at: Optional[float] = None,
        valid_for_s: Optional[float] = None,
        evidence: Optional[Mapping[str, Any]] = None,
    ) -> "RobotWorldState":
        resolved = dimension if isinstance(dimension, StateDimension) else StateDimension(str(dimension))
        if value is not None and value not in ALLOWED_VALUES[resolved]:
            raise ValueError(f"invalid {resolved.value} state: {value}")
        observed = observed_at if observed_at is not None else time.time()
        valid_until = observed + valid_for_s if valid_for_s is not None else None
        dimensions = dict(self.dimensions)
        dimensions[resolved.value] = StateValue(
            value=value,
            source=source,
            observed_at=observed if value is not None else None,
            valid_until=valid_until if value is not None else None,
            evidence=dict(evidence or {}),
        )
        return replace(
            self,
            world_version=self.world_version + 1,
            dimensions=dimensions,
            updated_at=observed,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "robot_id": self.robot_id,
            "world_version": self.world_version,
            "updated_at": self.updated_at,
            "dimensions": {
                name: value.to_dict() for name, value in self.dimensions.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RobotWorldState":
        raw_dimensions = dict(data.get("dimensions") or {})
        dimensions = _unknown_dimensions()
        dimensions.update(
            {name: StateValue.from_dict(value) for name, value in raw_dimensions.items()}
        )
        return cls(
            schema_version=str(data.get("schema_version", WORLD_STATE_SCHEMA_VERSION)),
            robot_id=str(data["robot_id"]),
            world_version=int(data.get("world_version", 0)),
            updated_at=float(data.get("updated_at", time.time())),
            dimensions=dimensions,
        )
