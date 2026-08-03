"""Logical/perception object identity, candidates, lineage, and selection epochs."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


OBJECT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class PerceptionRef:
    provider: str
    object_id: int | str
    tracker_session_id: str
    observation_id: str
    schema_version: str = OBJECT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != OBJECT_SCHEMA_VERSION:
            raise ValueError(f"unsupported PerceptionRef schema_version: {self.schema_version}")
        if not self.provider or not self.tracker_session_id or not self.observation_id:
            raise ValueError("perception identity fields must be non-empty")
        if isinstance(self.object_id, bool) or not isinstance(self.object_id, (int, str)):
            raise ValueError("object_id must be an integer or string")
        if isinstance(self.object_id, str) and not self.object_id:
            raise ValueError("object_id string must be non-empty")

    @property
    def identity(self) -> tuple[str, str, int | str]:
        return (self.provider, self.tracker_session_id, self.object_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "provider": self.provider,
            "object_id": self.object_id,
            "tracker_session_id": self.tracker_session_id,
            "observation_id": self.observation_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PerceptionRef":
        return cls(
            schema_version=str(data.get("schema_version", OBJECT_SCHEMA_VERSION)),
            provider=str(data["provider"]),
            object_id=data["object_id"],
            tracker_session_id=str(data["tracker_session_id"]),
            observation_id=str(data["observation_id"]),
        )


@dataclass(frozen=True)
class ObjectRef:
    logical_object_id: str
    category: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    perception_ref: Optional[PerceptionRef] = None
    schema_version: str = OBJECT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != OBJECT_SCHEMA_VERSION:
            raise ValueError(f"unsupported ObjectRef schema_version: {self.schema_version}")
        if not self.logical_object_id or not self.category:
            raise ValueError("logical_object_id and category must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "logical_object_id": self.logical_object_id,
            "category": self.category,
            "attributes": dict(self.attributes),
            "perception_ref": self.perception_ref.to_dict() if self.perception_ref else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ObjectRef":
        perception = data.get("perception_ref")
        return cls(
            schema_version=str(data.get("schema_version", OBJECT_SCHEMA_VERSION)),
            logical_object_id=str(data["logical_object_id"]),
            category=str(data["category"]),
            attributes=dict(data.get("attributes") or {}),
            perception_ref=PerceptionRef.from_dict(perception) if perception else None,
        )


@dataclass(frozen=True)
class ObjectCandidate:
    perception_ref: PerceptionRef
    category: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    pose: Optional[Mapping[str, Any]] = None
    confidence: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "perception_ref": self.perception_ref.to_dict(),
            "category": self.category,
            "attributes": dict(self.attributes),
            "pose": dict(self.pose) if self.pose is not None else None,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ObjectCandidate":
        return cls(
            perception_ref=PerceptionRef.from_dict(data["perception_ref"]),
            category=str(data["category"]),
            attributes=dict(data.get("attributes") or {}),
            pose=dict(data["pose"]) if data.get("pose") is not None else None,
            confidence=(float(data["confidence"]) if data.get("confidence") is not None else None),
        )


@dataclass(frozen=True)
class ObjectCandidateSet:
    observation_id: str
    provider: str
    tracker_session_id: str
    captured_at: float
    valid_until: float
    candidates: tuple[ObjectCandidate, ...]

    def __post_init__(self) -> None:
        if not self.observation_id or not self.provider or not self.tracker_session_id:
            raise ValueError("candidate-set identity fields must be non-empty")
        identities: set[tuple[str, str, int | str]] = set()
        for candidate in self.candidates:
            ref = candidate.perception_ref
            if ref.observation_id != self.observation_id:
                raise ValueError("candidate observation_id does not match candidate set")
            if ref.provider != self.provider or ref.tracker_session_id != self.tracker_session_id:
                raise ValueError("candidate provider/session does not match candidate set")
            if ref.identity in identities:
                raise ValueError("candidate set contains duplicate perception identity")
            identities.add(ref.identity)

    def is_fresh(self, *, now: Optional[float] = None) -> bool:
        return self.valid_until >= (now if now is not None else time.time())

    def candidate(self, object_id: int | str) -> ObjectCandidate:
        matches = [item for item in self.candidates if item.perception_ref.object_id == object_id]
        if len(matches) != 1:
            raise ObjectSelectionError(
                "OBJECT_ID_NOT_UNIQUE",
                f"expected one candidate for object_id {object_id!r}, found {len(matches)}",
            )
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "provider": self.provider,
            "tracker_session_id": self.tracker_session_id,
            "captured_at": self.captured_at,
            "valid_until": self.valid_until,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ObjectCandidateSet":
        return cls(
            observation_id=str(data["observation_id"]),
            provider=str(data["provider"]),
            tracker_session_id=str(data["tracker_session_id"]),
            captured_at=float(data["captured_at"]),
            valid_until=float(data["valid_until"]),
            candidates=tuple(ObjectCandidate.from_dict(item) for item in data.get("candidates") or ()),
        )


@dataclass(frozen=True)
class SelectionToken:
    token_id: str
    logical_object_id: str
    perception_ref: PerceptionRef
    selection_epoch: int
    selected_at: float
    valid_until: float

    def is_fresh(self, *, now: Optional[float] = None) -> bool:
        return self.valid_until >= (now if now is not None else time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "logical_object_id": self.logical_object_id,
            "perception_ref": self.perception_ref.to_dict(),
            "selection_epoch": self.selection_epoch,
            "selected_at": self.selected_at,
            "valid_until": self.valid_until,
        }


@dataclass(frozen=True)
class ObjectLineageRecord:
    logical_object_id: str
    previous_ref: PerceptionRef
    new_ref: PerceptionRef
    reason: str
    evidence_observation_ids: tuple[str, ...]
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.previous_ref.identity == self.new_ref.identity:
            raise ValueError("lineage must link different perception identities")
        if not self.reason or not self.evidence_observation_ids:
            raise ValueError("lineage requires a reason and evidence observations")

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_object_id": self.logical_object_id,
            "previous_ref": self.previous_ref.to_dict(),
            "new_ref": self.new_ref.to_dict(),
            "reason": self.reason,
            "evidence_observation_ids": list(self.evidence_observation_ids),
            "created_at": self.created_at,
        }


class ObjectSelectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ObjectSelectionStore:
    """Process-local selection authority; tokens are persisted by callers."""

    def __init__(self) -> None:
        self._epoch_by_provider: dict[str, int] = {}
        self._active_by_logical: dict[str, SelectionToken] = {}
        self._lineage: list[ObjectLineageRecord] = []

    def select(
        self,
        logical: ObjectRef,
        candidates: ObjectCandidateSet,
        *,
        object_id: int | str,
        now: Optional[float] = None,
        ttl_s: float = 1.5,
    ) -> SelectionToken:
        selected_at = now if now is not None else time.time()
        if not candidates.is_fresh(now=selected_at):
            raise ObjectSelectionError("OBSERVATION_STALE", "candidate observation is stale")
        candidate = candidates.candidate(object_id)
        if logical.category != candidate.category:
            raise ObjectSelectionError("CATEGORY_MISMATCH", "selected candidate category does not match goal")
        epoch_key = f"{candidates.provider}:{candidates.tracker_session_id}"
        epoch = self._epoch_by_provider.get(epoch_key, 0) + 1
        self._epoch_by_provider[epoch_key] = epoch
        token = SelectionToken(
            token_id=f"selection-{uuid.uuid4().hex}",
            logical_object_id=logical.logical_object_id,
            perception_ref=candidate.perception_ref,
            selection_epoch=epoch,
            selected_at=selected_at,
            valid_until=min(candidates.valid_until, selected_at + ttl_s),
        )
        self._active_by_logical[logical.logical_object_id] = token
        return token

    def validate_pick_binding(
        self,
        token: SelectionToken,
        pose_ref: PerceptionRef,
        *,
        now: Optional[float] = None,
    ) -> None:
        current = self._active_by_logical.get(token.logical_object_id)
        if current is None or current.token_id != token.token_id:
            raise ObjectSelectionError("SELECTION_EPOCH_OLD", "selection token is no longer active")
        if not token.is_fresh(now=now):
            raise ObjectSelectionError("SELECTION_STALE", "selection token expired")
        if pose_ref.identity != token.perception_ref.identity:
            if pose_ref.tracker_session_id != token.perception_ref.tracker_session_id:
                raise ObjectSelectionError("TRACKER_SESSION_MISMATCH", "tracker session changed after selection")
            raise ObjectSelectionError("OBJECT_ID_MISMATCH", "pose object_id differs from selected object")
        if pose_ref.observation_id == token.perception_ref.observation_id:
            raise ObjectSelectionError(
                "POSE_NOT_AFTER_SELECTION",
                "pick pose must come from a fresh observation after target selection",
            )

    def add_lineage(self, record: ObjectLineageRecord) -> None:
        self._lineage.append(record)

    def lineage(self, logical_object_id: str) -> tuple[ObjectLineageRecord, ...]:
        return tuple(
            item for item in self._lineage if item.logical_object_id == logical_object_id
        )
