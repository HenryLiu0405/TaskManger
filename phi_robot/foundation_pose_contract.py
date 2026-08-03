"""Pure FoundationPose observation checks shared by ROS and offline tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FoundationPoseSelectionEpoch:
    """Identity and clock boundary created for one target selection."""

    object_id: Optional[int]
    required_frame: str
    started_ros_ns: int
    started_monotonic: float
    stale_after_s: float


@dataclass(frozen=True)
class FoundationPoseObservation:
    """ROS-independent metadata needed to accept or reject one pose."""

    object_id: Optional[int]
    frame_id: str
    stamp_ns: int
    received_monotonic: float


def reject_foundation_pose_observation(
    observation: FoundationPoseObservation,
    epoch: FoundationPoseSelectionEpoch,
    *,
    now_ros_ns: int,
    now_monotonic: float,
) -> Optional[str]:
    """Return a stable rejection reason, or ``None`` when the pose is usable."""

    if epoch.object_id is not None and observation.object_id != epoch.object_id:
        return "OBJECT_ID_MISMATCH"
    if observation.frame_id != epoch.required_frame:
        return "POSE_FRAME_MISMATCH"
    if observation.stamp_ns <= 0:
        return "POSE_TIMESTAMP_INVALID"
    if (
        observation.stamp_ns < epoch.started_ros_ns
        or observation.received_monotonic < epoch.started_monotonic
    ):
        return "POSE_BEFORE_SELECTION"

    monotonic_age = now_monotonic - observation.received_monotonic
    ros_age_ns = now_ros_ns - observation.stamp_ns
    if monotonic_age < 0 or ros_age_ns < 0:
        return "POSE_TIMESTAMP_IN_FUTURE"
    if (
        monotonic_age > epoch.stale_after_s
        or ros_age_ns > int(epoch.stale_after_s * 1_000_000_000)
    ):
        return "POSE_STALE"
    return None
