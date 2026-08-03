"""Versioned, grounded natural-language goal contract."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .locations import LocationRef
from .objects import ObjectRef
from .observations import ObservationBundle


GOAL_SCHEMA_VERSION = "1.0"


class GoalGroundingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GoalSpec:
    goal_id: str
    original_instruction: str
    object_ref: ObjectRef
    destination: LocationRef
    constraints: Mapping[str, Any]
    success_criteria: tuple[str, ...]
    grounding_observation_id: str
    grounding_world_version: int
    created_at: float = field(default_factory=time.time)
    schema_version: str = GOAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != GOAL_SCHEMA_VERSION:
            raise ValueError(f"unsupported GoalSpec schema_version: {self.schema_version}")
        if not self.goal_id or not self.original_instruction.strip():
            raise ValueError("goal_id and original_instruction must be non-empty")
        if not self.grounding_observation_id or self.grounding_world_version < 0:
            raise ValueError("goal grounding identity is invalid")
        if not self.success_criteria or any(not item.strip() for item in self.success_criteria):
            raise ValueError("GoalSpec requires explicit non-empty success criteria")
        perception = self.object_ref.perception_ref
        if perception is None:
            raise ValueError("grounded GoalSpec requires a perception-bound ObjectRef")
        if perception.observation_id != self.grounding_observation_id:
            raise ValueError("ObjectRef is not bound to the grounding observation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "goal_id": self.goal_id,
            "original_instruction": self.original_instruction,
            "object_ref": self.object_ref.to_dict(),
            "destination": self.destination.to_dict(),
            "constraints": dict(self.constraints),
            "success_criteria": list(self.success_criteria),
            "grounding_observation_id": self.grounding_observation_id,
            "grounding_world_version": self.grounding_world_version,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GoalSpec":
        return cls(
            schema_version=str(data.get("schema_version", GOAL_SCHEMA_VERSION)),
            goal_id=str(data["goal_id"]),
            original_instruction=str(data["original_instruction"]),
            object_ref=ObjectRef.from_dict(data["object_ref"]),
            destination=LocationRef.from_dict(data["destination"]),
            constraints=dict(data.get("constraints") or {}),
            success_criteria=tuple(str(item) for item in data.get("success_criteria") or ()),
            grounding_observation_id=str(data["grounding_observation_id"]),
            grounding_world_version=int(data["grounding_world_version"]),
            created_at=float(data.get("created_at", time.time())),
        )

    @classmethod
    def from_model_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        instruction: str,
        observation: ObservationBundle,
        goal_id: Optional[str] = None,
    ) -> "GoalSpec":
        """Create a trusted goal from a model grounding payload.

        The original instruction, evidence identity, and world version come
        from local state, not from model-authored copies.
        """

        body = payload.get("goal_spec") if isinstance(payload.get("goal_spec"), Mapping) else payload
        if not isinstance(body, Mapping):
            raise GoalGroundingError("GOAL_PAYLOAD_INVALID", "goal payload must be an object")
        try:
            object_ref = ObjectRef.from_dict(body["object_ref"])
            destination = LocationRef.from_dict(body["destination"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GoalGroundingError("GOAL_PAYLOAD_INVALID", str(exc)) from exc
        perception = object_ref.perception_ref
        if perception is None:
            raise GoalGroundingError(
                "OBJECT_NOT_GROUNDED", "model must select one observed perception object_id"
            )
        matches = [
            candidate
            for candidate in observation.objects
            if candidate.perception_ref.identity == perception.identity
            and candidate.perception_ref.observation_id == perception.observation_id
        ]
        if len(matches) != 1:
            raise GoalGroundingError(
                "OBJECT_EVIDENCE_MISMATCH",
                "grounded object does not identify exactly one candidate in the observation",
            )
        candidate = matches[0]
        if candidate.category != object_ref.category:
            raise GoalGroundingError(
                "OBJECT_CATEGORY_MISMATCH", "ObjectRef category conflicts with perception evidence"
            )
        success = tuple(str(item) for item in body.get("success_criteria") or ())
        try:
            return cls(
                goal_id=goal_id or str(body.get("goal_id") or f"goal-{uuid.uuid4().hex}"),
                original_instruction=instruction,
                object_ref=object_ref,
                destination=destination,
                constraints=dict(body.get("constraints") or {}),
                success_criteria=success,
                grounding_observation_id=observation.observation_id,
                grounding_world_version=observation.world_version,
            )
        except ValueError as exc:
            raise GoalGroundingError("GOAL_PAYLOAD_INVALID", str(exc)) from exc

