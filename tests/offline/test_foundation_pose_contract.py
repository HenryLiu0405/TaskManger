from __future__ import annotations

import unittest

from phi_robot.foundation_pose_contract import (
    FoundationPoseObservation,
    FoundationPoseSelectionEpoch,
    reject_foundation_pose_observation,
)


class FoundationPoseContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.epoch = FoundationPoseSelectionEpoch(
            object_id=7,
            required_frame="pelvis",
            started_ros_ns=10_000_000_000,
            started_monotonic=20.0,
            stale_after_s=1.5,
        )

    def reject(self, **changes) -> str | None:
        values = {
            "object_id": 7,
            "frame_id": "pelvis",
            "stamp_ns": 10_500_000_000,
            "received_monotonic": 20.5,
        }
        values.update(changes)
        return reject_foundation_pose_observation(
            FoundationPoseObservation(**values),
            self.epoch,
            now_ros_ns=11_000_000_000,
            now_monotonic=21.0,
        )

    def test_fresh_matching_observation_is_accepted(self) -> None:
        self.assertIsNone(self.reject())

    def test_wrong_or_switched_object_id_is_rejected(self) -> None:
        self.assertEqual(self.reject(object_id=8), "OBJECT_ID_MISMATCH")
        switched = FoundationPoseSelectionEpoch(
            object_id=8,
            required_frame=self.epoch.required_frame,
            started_ros_ns=self.epoch.started_ros_ns,
            started_monotonic=self.epoch.started_monotonic,
            stale_after_s=self.epoch.stale_after_s,
        )
        old_observation = FoundationPoseObservation(
            object_id=7,
            frame_id="pelvis",
            stamp_ns=10_500_000_000,
            received_monotonic=20.5,
        )
        self.assertEqual(
            reject_foundation_pose_observation(
                old_observation, switched,
                now_ros_ns=11_000_000_000, now_monotonic=21.0,
            ),
            "OBJECT_ID_MISMATCH",
        )

    def test_wrong_frame_is_rejected_without_relabeling(self) -> None:
        self.assertEqual(self.reject(frame_id="camera_link"), "POSE_FRAME_MISMATCH")

    def test_preselection_and_invalid_timestamps_are_rejected(self) -> None:
        self.assertEqual(self.reject(stamp_ns=0), "POSE_TIMESTAMP_INVALID")
        self.assertEqual(self.reject(stamp_ns=9_999_999_999), "POSE_BEFORE_SELECTION")
        self.assertEqual(self.reject(received_monotonic=19.9), "POSE_BEFORE_SELECTION")

    def test_stale_or_future_observations_are_rejected(self) -> None:
        self.assertEqual(
            self.reject(stamp_ns=8_000_000_000, received_monotonic=18.0),
            "POSE_BEFORE_SELECTION",
        )
        stale_epoch = FoundationPoseSelectionEpoch(
            object_id=7,
            required_frame="pelvis",
            started_ros_ns=1,
            started_monotonic=1.0,
            stale_after_s=1.5,
        )
        stale = FoundationPoseObservation(7, "pelvis", 8_000_000_000, 18.0)
        self.assertEqual(
            reject_foundation_pose_observation(
                stale, stale_epoch,
                now_ros_ns=11_000_000_000, now_monotonic=21.0,
            ),
            "POSE_STALE",
        )
        self.assertEqual(
            self.reject(stamp_ns=12_000_000_000),
            "POSE_TIMESTAMP_IN_FUTURE",
        )
