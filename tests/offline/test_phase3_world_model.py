from __future__ import annotations

import time
import unittest
from pathlib import Path

from phi_robot.autonomy.locations import (
    LocationKind,
    LocationRef,
    LocationResolutionError,
    NineGridLocationResolver,
    legacy_grid_location,
)
from phi_robot.autonomy.objects import (
    ObjectCandidate,
    ObjectCandidateSet,
    ObjectLineageRecord,
    ObjectRef,
    ObjectSelectionError,
    ObjectSelectionStore,
    PerceptionRef,
)
from phi_robot.autonomy.observations import (
    DetectorHealth,
    DropEvent,
    ObservationError,
    VisualRingBuffer,
)


ROOT = Path(__file__).resolve().parents[2]


class LocationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = NineGridLocationResolver.from_json_file(
            ROOT / "phi_robot" / "scene_coords.json",
            scene_id="scene-001",
            scene_version="scene-v1",
            map_version="nav-map-v1",
        )

    def test_legacy_grid_translates_to_symbolic_ref_then_resolves_with_provenance(self) -> None:
        location = legacy_grid_location(
            "grid/ne", scene_id="scene-001", scene_version="scene-v1"
        )
        self.assertEqual(location.qualified_id, "grid/ne")
        self.assertNotIn("x", location.to_dict())
        resolved = self.resolver.resolve(location, now=100.0)
        self.assertEqual(resolved.location_ref, location)
        self.assertEqual(resolved.frame_id, "nav2_map")
        self.assertEqual(set(resolved.pose), {"x", "y", "z", "theta"})
        self.assertEqual(resolved.provenance["qualified_location_id"], "grid/ne")
        self.assertEqual(type(resolved).from_dict(resolved.to_dict()), resolved)

    def test_only_current_nine_grid_is_executable_but_schema_is_extensible(self) -> None:
        future = LocationRef(
            kind=LocationKind.REGION,
            namespace="warehouse",
            location_id="safe-zone",
            scene_id="scene-001",
            scene_version="scene-v1",
        )
        with self.assertRaises(LocationResolutionError) as context:
            self.resolver.resolve(future)
        self.assertEqual(context.exception.code, "UNSUPPORTED_LOCATION_KIND")

        wrong_scene = legacy_grid_location(
            "ne", scene_id="scene-other", scene_version="scene-v1"
        )
        with self.assertRaises(LocationResolutionError) as context:
            self.resolver.resolve(wrong_scene)
        self.assertEqual(context.exception.code, "SCENE_ID_MISMATCH")


def _candidate_set(
    *,
    observation_id: str,
    session: str = "fp-session-1",
    captured_at: float = 10.0,
    valid_until: float = 12.0,
) -> ObjectCandidateSet:
    return ObjectCandidateSet(
        observation_id=observation_id,
        provider="foundationpose",
        tracker_session_id=session,
        captured_at=captured_at,
        valid_until=valid_until,
        candidates=(
            ObjectCandidate(
                perception_ref=PerceptionRef(
                    provider="foundationpose",
                    object_id=12,
                    tracker_session_id=session,
                    observation_id=observation_id,
                ),
                category="box",
                attributes={"color": "red", "side": "left"},
                confidence=0.97,
            ),
            ObjectCandidate(
                perception_ref=PerceptionRef(
                    provider="foundationpose",
                    object_id=13,
                    tracker_session_id=session,
                    observation_id=observation_id,
                ),
                category="box",
                attributes={"color": "blue", "side": "right"},
                confidence=0.95,
            ),
        ),
    )


class ObjectIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logical = ObjectRef(
            logical_object_id="task-box-00",
            category="box",
            attributes={"color": "red"},
        )
        self.store = ObjectSelectionStore()

    def test_complete_candidate_set_and_fresh_selection_bind_pick_pose(self) -> None:
        candidates = _candidate_set(observation_id="obs-1")
        self.assertEqual(len(candidates.candidates), 2)
        token = self.store.select(
            self.logical, candidates, object_id=12, now=10.5, ttl_s=1.0
        )
        self.assertEqual(token.perception_ref.object_id, 12)
        self.assertEqual(token.selection_epoch, 1)
        fresh_pose = PerceptionRef(
            provider="foundationpose",
            object_id=12,
            tracker_session_id="fp-session-1",
            observation_id="obs-pose-after-selection",
        )
        self.store.validate_pick_binding(token, fresh_pose, now=10.8)

    def test_wrong_id_old_epoch_and_reused_tracker_session_fail_deterministically(self) -> None:
        first = self.store.select(
            self.logical, _candidate_set(observation_id="obs-1"),
            object_id=12, now=10.2, ttl_s=1.0,
        )
        wrong_id = PerceptionRef(
            provider="foundationpose", object_id=13,
            tracker_session_id="fp-session-1", observation_id="obs-2",
        )
        with self.assertRaises(ObjectSelectionError) as context:
            self.store.validate_pick_binding(first, wrong_id, now=10.3)
        self.assertEqual(context.exception.code, "OBJECT_ID_MISMATCH")

        reused_after_restart = PerceptionRef(
            provider="foundationpose", object_id=12,
            tracker_session_id="fp-session-2", observation_id="obs-2",
        )
        with self.assertRaises(ObjectSelectionError) as context:
            self.store.validate_pick_binding(first, reused_after_restart, now=10.3)
        self.assertEqual(context.exception.code, "TRACKER_SESSION_MISMATCH")

        second = self.store.select(
            self.logical,
            _candidate_set(observation_id="obs-3", captured_at=10.4, valid_until=12.0),
            object_id=12,
            now=10.5,
        )
        self.assertEqual(second.selection_epoch, 2)
        with self.assertRaises(ObjectSelectionError) as context:
            self.store.validate_pick_binding(
                first,
                PerceptionRef(
                    provider="foundationpose", object_id=12,
                    tracker_session_id="fp-session-1", observation_id="obs-4",
                ),
                now=10.6,
            )
        self.assertEqual(context.exception.code, "SELECTION_EPOCH_OLD")

    def test_reidentification_requires_explicit_lineage(self) -> None:
        old = _candidate_set(observation_id="before-drop").candidate(12).perception_ref
        new = _candidate_set(
            observation_id="after-drop", session="fp-session-2"
        ).candidate(13).perception_ref
        record = ObjectLineageRecord(
            logical_object_id=self.logical.logical_object_id,
            previous_ref=old,
            new_ref=new,
            reason="VLM and color/shape evidence linked object after drop",
            evidence_observation_ids=("before-drop", "after-drop"),
        )
        self.store.add_lineage(record)
        self.assertEqual(self.store.lineage("task-box-00")[0], record)


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class ObservationHubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock(100.1)
        self.ring = VisualRingBuffer(
            capacity_per_channel=3,
            max_frame_age_s=1.0,
            sync_tolerance_s=0.1,
            clock=self.clock,
        )
        self.rgb = self.ring.add_frame(
            channel="rgb", data=b"rgb-image", content_type="image/jpeg",
            captured_at=100.0, sequence=1,
        )
        self.overlay = self.ring.add_frame(
            channel="foundationpose_overlay", data=b"overlay-image",
            content_type="image/jpeg", captured_at=100.05, sequence=4,
        )

    def test_atomic_bundle_has_hashes_media_and_world_version_freshness(self) -> None:
        observation_id = "obs-bundle"
        candidates = _candidate_set(
            observation_id=observation_id,
            captured_at=100.0,
            valid_until=100.8,
        )
        bundle = self.ring.build_bundle(
            robot_id="robot-01",
            world_version=42,
            channels=("rgb", "foundationpose_overlay"),
            robot_state={"motion": "stopped"},
            active_action={},
            candidates=candidates,
            observation_id=observation_id,
            now=100.1,
        )
        descriptor = bundle.to_dict()
        self.assertEqual(bundle.observation_id, observation_id)
        self.assertEqual(len(bundle.objects), 2)
        self.assertTrue(bundle.is_fresh(now=100.2, current_world_version=42))
        self.assertFalse(bundle.is_fresh(now=100.2, current_world_version=43))
        self.assertNotIn("data", descriptor["frames"][0])
        self.assertEqual(bundle.media()[self.rgb.frame_id], b"rgb-image")
        self.assertEqual(len(descriptor["frames"][0]["sha256"]), 64)

    def test_stopped_camera_cannot_supply_indefinitely_valid_old_frame(self) -> None:
        self.clock.now = 102.0
        with self.assertRaises(ObservationError) as context:
            self.ring.build_bundle(
                robot_id="robot-01",
                world_version=1,
                channels=("rgb", "foundationpose_overlay"),
                robot_state={},
                active_action={},
            )
        self.assertEqual(context.exception.code, "FRAME_STALE")

    def test_unsynchronized_channels_and_old_sequences_are_rejected(self) -> None:
        with self.assertRaises(ObservationError) as context:
            self.ring.add_frame(
                channel="rgb", data=b"old", content_type="image/jpeg",
                captured_at=100.01, sequence=1,
            )
        self.assertEqual(context.exception.code, "FRAME_SEQUENCE_OLD")

        self.clock.now = 100.5
        self.ring.add_frame(
            channel="depth", data=b"depth", content_type="image/png",
            captured_at=100.4, sequence=1,
        )
        with self.assertRaises(ObservationError) as context:
            self.ring.build_bundle(
                robot_id="robot-01", world_version=1,
                channels=("rgb", "depth"), robot_state={}, active_action={},
            )
        self.assertEqual(context.exception.code, "FRAMES_UNSYNCHRONIZED")

    def test_structured_drop_event_keeps_health_context_and_unknown_odometry(self) -> None:
        event = DropEvent(
            event_id="drop-1",
            sequence=9,
            detected_at=time.time(),
            detector_health=DetectorHealth.HEALTHY,
            robot_id="robot-01",
            mission_id="mission-1",
            logical_object_id="task-box-00",
            perception_ref=None,
            evidence_frame_ids=(self.rgb.frame_id, self.overlay.frame_id),
            odometry=None,
            detector_metadata={"edge": "rising"},
        )
        payload = event.to_dict()
        self.assertEqual(payload["detector_health"], "healthy")
        self.assertIsNone(payload["odometry"])
        self.assertEqual(payload["mission_id"], "mission-1")


if __name__ == "__main__":
    unittest.main()
