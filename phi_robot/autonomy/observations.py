"""Timestamped visual ring buffer, atomic observation bundle, and drop event."""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from .objects import ObjectCandidate, ObjectCandidateSet, PerceptionRef


OBSERVATION_SCHEMA_VERSION = "1.0"


class ObservationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ObservationFrame:
    frame_id: str
    channel: str
    captured_at: float
    sequence: int
    content_type: str
    sha256: str
    byte_length: int
    synchronization: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    data: bytes = field(default=b"", repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.frame_id or not self.channel or not self.content_type:
            raise ValueError("frame identity fields must be non-empty")
        if self.sequence < 0 or self.byte_length <= 0:
            raise ValueError("frame sequence/length are invalid")
        if len(self.sha256) != 64:
            raise ValueError("frame requires a SHA-256 digest")
        if self.data and len(self.data) != self.byte_length:
            raise ValueError("frame byte_length does not match data")
        if self.data and hashlib.sha256(self.data).hexdigest() != self.sha256:
            raise ValueError("frame hash does not match data")

    def to_dict(self, *, include_data: bool = False) -> dict[str, Any]:
        result = {
            "frame_id": self.frame_id,
            "channel": self.channel,
            "captured_at": self.captured_at,
            "sequence": self.sequence,
            "content_type": self.content_type,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
            "synchronization": dict(self.synchronization),
            "metadata": dict(self.metadata),
        }
        if include_data:
            result["data"] = self.data
        return result


@dataclass(frozen=True)
class ObservationBundle:
    observation_id: str
    world_version: int
    captured_at: float
    valid_until: float
    robot_id: str
    frames: tuple[ObservationFrame, ...]
    objects: tuple[ObjectCandidate, ...]
    robot_state: Mapping[str, Any]
    active_action: Mapping[str, Any]
    recent_events: tuple[Mapping[str, Any], ...]
    synchronization: Mapping[str, Any]
    schema_version: str = OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != OBSERVATION_SCHEMA_VERSION:
            raise ValueError(f"unsupported ObservationBundle schema_version: {self.schema_version}")
        if not self.observation_id or not self.robot_id:
            raise ValueError("observation_id and robot_id must be non-empty")
        if self.world_version < 0 or self.valid_until < self.captured_at:
            raise ValueError("observation version/time bounds are invalid")
        frame_ids = [frame.frame_id for frame in self.frames]
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("observation contains duplicate frame IDs")
        for candidate in self.objects:
            if candidate.perception_ref.observation_id != self.observation_id:
                raise ValueError("object candidate is not bound to this observation")

    def is_fresh(
        self,
        *,
        now: Optional[float] = None,
        current_world_version: Optional[int] = None,
    ) -> bool:
        if self.valid_until < (now if now is not None else time.time()):
            return False
        return current_world_version is None or current_world_version == self.world_version

    def to_dict(self) -> dict[str, Any]:
        """Audit-safe descriptor; image bytes are deliberately excluded."""

        return {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
            "world_version": self.world_version,
            "captured_at": self.captured_at,
            "valid_until": self.valid_until,
            "robot_id": self.robot_id,
            "frames": [frame.to_dict(include_data=False) for frame in self.frames],
            "objects": [candidate.to_dict() for candidate in self.objects],
            "robot_state": dict(self.robot_state),
            "active_action": dict(self.active_action),
            "recent_events": [dict(event) for event in self.recent_events],
            "synchronization": dict(self.synchronization),
        }

    def media(self) -> dict[str, bytes]:
        return {frame.frame_id: frame.data for frame in self.frames}


class VisualRingBuffer:
    """Bounded per-channel frame history with freshness and sync checks."""

    def __init__(
        self,
        *,
        capacity_per_channel: int = 30,
        max_frame_age_s: float = 2.0,
        sync_tolerance_s: float = 0.15,
        future_tolerance_s: float = 0.05,
        clock: Any = time.time,
    ) -> None:
        if capacity_per_channel < 1 or max_frame_age_s <= 0 or sync_tolerance_s < 0:
            raise ValueError("invalid visual ring-buffer configuration")
        self.capacity_per_channel = capacity_per_channel
        self.max_frame_age_s = max_frame_age_s
        self.sync_tolerance_s = sync_tolerance_s
        self.future_tolerance_s = future_tolerance_s
        self.clock = clock
        self._guard = threading.RLock()
        self._frames: dict[str, deque[ObservationFrame]] = defaultdict(
            lambda: deque(maxlen=capacity_per_channel)
        )

    def add_frame(
        self,
        *,
        channel: str,
        data: bytes,
        content_type: str,
        captured_at: float,
        sequence: int,
        synchronization: Optional[Mapping[str, Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> ObservationFrame:
        if not isinstance(data, bytes) or not data:
            raise ObservationError("FRAME_DATA_INVALID", "frame data must be non-empty bytes")
        if not channel:
            raise ObservationError("FRAME_CHANNEL_INVALID", "frame channel must be non-empty")
        now = float(self.clock())
        if captured_at > now + self.future_tolerance_s:
            raise ObservationError("FRAME_TIMESTAMP_FUTURE", "frame timestamp is in the future")
        frame = ObservationFrame(
            frame_id=f"frame-{uuid.uuid4().hex}",
            channel=channel,
            captured_at=float(captured_at),
            sequence=int(sequence),
            content_type=content_type,
            sha256=hashlib.sha256(data).hexdigest(),
            byte_length=len(data),
            synchronization=dict(synchronization or {}),
            metadata=dict(metadata or {}),
            data=data,
        )
        with self._guard:
            history = self._frames[channel]
            if history and sequence <= history[-1].sequence:
                raise ObservationError(
                    "FRAME_SEQUENCE_OLD", "frame sequence must increase for each channel"
                )
            if history and captured_at < history[-1].captured_at:
                raise ObservationError(
                    "FRAME_TIMESTAMP_OLD", "frame timestamp moved backwards for channel"
                )
            history.append(frame)
        return frame

    def build_bundle(
        self,
        *,
        robot_id: str,
        world_version: int,
        channels: Iterable[str],
        robot_state: Mapping[str, Any],
        active_action: Mapping[str, Any],
        recent_events: Iterable[Mapping[str, Any]] = (),
        candidates: Optional[ObjectCandidateSet] = None,
        observation_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> ObservationBundle:
        bundle_time = float(now if now is not None else self.clock())
        requested = tuple(dict.fromkeys(str(channel) for channel in channels))
        if not requested:
            raise ObservationError("CHANNELS_EMPTY", "at least one frame channel is required")
        with self._guard:
            newest: list[ObservationFrame] = []
            for channel in requested:
                history = self._frames.get(channel)
                if not history:
                    raise ObservationError(
                        "FRAME_CHANNEL_UNAVAILABLE", f"no frames for channel {channel}"
                    )
                newest.append(history[-1])
            anchor = min(frame.captured_at for frame in newest)
            selected: list[ObservationFrame] = []
            for channel in requested:
                history = self._frames[channel]
                selected.append(min(history, key=lambda frame: abs(frame.captured_at - anchor)))

        oldest = min(frame.captured_at for frame in selected)
        newest_at = max(frame.captured_at for frame in selected)
        if newest_at - oldest > self.sync_tolerance_s:
            raise ObservationError(
                "FRAMES_UNSYNCHRONIZED",
                f"frame spread {newest_at - oldest:.6f}s exceeds tolerance",
            )
        valid_until = min(frame.captured_at + self.max_frame_age_s for frame in selected)
        if valid_until < bundle_time:
            raise ObservationError("FRAME_STALE", "latest synchronized frames are stale")
        observation_id = observation_id or f"obs-{uuid.uuid4().hex}"
        if candidates is not None:
            if candidates.observation_id != observation_id:
                raise ObservationError(
                    "OBJECT_OBSERVATION_MISMATCH",
                    "candidate set observation_id differs from bundle",
                )
            if not candidates.is_fresh(now=bundle_time):
                raise ObservationError("OBJECTS_STALE", "object candidate set is stale")
            objects = candidates.candidates
            valid_until = min(valid_until, candidates.valid_until)
        else:
            objects = ()
        synchronization = {
            "method": "nearest_timestamp",
            "anchor_at": anchor,
            "spread_s": newest_at - oldest,
            "tolerance_s": self.sync_tolerance_s,
            "channels": list(requested),
        }
        return ObservationBundle(
            observation_id=observation_id,
            world_version=world_version,
            captured_at=newest_at,
            valid_until=valid_until,
            robot_id=robot_id,
            frames=tuple(selected),
            objects=tuple(objects),
            robot_state=dict(robot_state),
            active_action=dict(active_action),
            recent_events=tuple(dict(event) for event in recent_events),
            synchronization=synchronization,
        )

    def channels(self) -> tuple[str, ...]:
        with self._guard:
            return tuple(sorted(self._frames))


class DetectorHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class DropEvent:
    event_id: str
    sequence: int
    detected_at: float
    detector_health: DetectorHealth
    robot_id: str
    mission_id: Optional[str]
    logical_object_id: Optional[str]
    perception_ref: Optional[PerceptionRef]
    evidence_frame_ids: tuple[str, ...]
    odometry: Optional[Mapping[str, Any]]
    detector_metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.sequence < 0 or not self.event_id or not self.robot_id:
            raise ValueError("drop event identity is invalid")
        if self.detector_health == DetectorHealth.HEALTHY and not self.evidence_frame_ids:
            raise ValueError("healthy drop event requires evidence frames")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "detected_at": self.detected_at,
            "detector_health": self.detector_health.value,
            "robot_id": self.robot_id,
            "mission_id": self.mission_id,
            "logical_object_id": self.logical_object_id,
            "perception_ref": self.perception_ref.to_dict() if self.perception_ref else None,
            "evidence_frame_ids": list(self.evidence_frame_ids),
            "odometry": dict(self.odometry) if self.odometry is not None else None,
            "detector_metadata": dict(self.detector_metadata),
        }
