from __future__ import annotations

import time
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, jsonify

from phi_robot.autonomy.deployment import (
    DeploymentConfig,
    _operator_authorizer,
    _protect_mutating_routes,
)
from phi_robot.autonomy.bootstrap import build_gemini_autonomy_runtime
from phi_robot.autonomy.gemini_provider import GeminiRoboticsER2Config
from phi_robot.autonomy.interrupts import AdapterInterruptLane
from phi_robot.autonomy.locations import NineGridLocationResolver
from phi_robot.autonomy.objects import ObjectRef
from phi_robot.autonomy.observations import ObservationError
from phi_robot.autonomy.ros_bridge import (
    AdapterObservationSource,
    RosSemanticBackend,
    build_shared_dispatcher,
)
from phi_robot.autonomy.runtime import RobotRuntime


def _resolver() -> NineGridLocationResolver:
    names = ("nw", "n", "ne", "w", "c", "e", "sw", "s", "se")
    return NineGridLocationResolver(
        {
            "frame": "map",
            "grid_cells": {
                name: {"x": float(index), "y": 1.0, "theta": 0.0}
                for index, name in enumerate(names)
            },
        },
        scene_id="scene-001",
        scene_version="scene-v1",
        map_version="map-v1",
    )


class FakeRosSurface:
    def __init__(self) -> None:
        self.now = time.time()
        self.selected_id = None
        self.calls = []
        self.robot_state = {
            "carry_state": "normal_planner",
            "posture_state": "stand",
            "hold_pose_active": False,
            "box_released": False,
        }

    def get_fp_video_sample(self, channel):
        source = "rgb" if channel == "overlay" else channel
        return {
            "data": f"jpeg-{source}".encode(),
            "captured_at": self.now,
            "sequence": 1,
            "content_type": "image/jpeg",
            "source_stamp_ns": 1,
        }

    def get_fp_state_sample(self):
        return {
            "state": {
                "trackers": [
                    {
                        "id": 12,
                        "state": 1,
                        "x": 0.2,
                        "y": 0.3,
                        "z": 0.4,
                        "color": "red",
                    }
                ]
            },
            "received_at": self.now,
            "sequence": 1,
            "tracker_session_id": "fp-session-test",
        }

    def get_robot_state(self):
        return dict(self.robot_state)

    def get_odom_sample(self):
        return {
            "x": 1.0,
            "y": 2.0,
            "yaw": 0.0,
            "received_at": self.now,
            "sequence": 1,
        }

    def select_object_id_public(self, object_id, *, step_id=""):
        self.selected_id = object_id
        return {"success": True, "matched_object_id": object_id}

    def clear_object_id_selection(self):
        self.selected_id = None
        return {"success": True}

    def wait_fp_pose_ready(self, min_wait_s=0.0, timeout_s=1.0):
        return self.selected_id is not None

    def get_selected_pose_identity(self):
        return {
            "provider": "foundationpose",
            "object_id": self.selected_id,
            "tracker_session_id": "fp-session-test",
            "observation_id": "fp-pose-after-selection",
        }

    def execute(self, tool, args, *, request_id, goal_id, step_id):
        self.calls.append((tool, dict(args), request_id))
        if tool == "pick":
            self.robot_state.update(
                carry_state="carry_wait_walk", hold_pose_active=True
            )
        if tool == "place":
            self.robot_state.update(
                carry_state="normal_planner",
                hold_pose_active=False,
                box_released=True,
                posture_state="stand",
            )
        return {
            "status": "ok",
            "message": f"{tool} done",
            "verification": {
                "status": "passed",
                "evidence": {"fake": True, "tool": tool},
            },
        }


class RosAutonomyBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = FakeRosSurface()
        self.source = AdapterObservationSource(
            self.adapter,
            robot_id="A",
            frame_max_age_s=2.0,
            decision_ttl_s=60.0,
        )
        self.backend = RosSemanticBackend(
            self.adapter,
            observation_source=self.source,
            location_resolver=_resolver(),
        )
        self.context = {
            "idempotency_key": "mission:1:node:1",
            "goal_id": "goal-1",
            "node_id": "node-1",
        }

    def test_observation_and_exact_object_id_flow_use_existing_adapter(self):
        observation = self.source.capture(
            channels=("rgb", "overlay", "mask", "depth"), reason="test"
        )
        self.assertEqual([item.perception_ref.object_id for item in observation.objects], [12])
        logical = ObjectRef(
            logical_object_id="task-box-00",
            category="box",
            attributes={"color": "red"},
            perception_ref=observation.objects[0].perception_ref,
        )
        selected = self.backend.execute_semantic(
            "select_object", {"object_ref": logical.to_dict()}, context=self.context
        )
        self.assertEqual(selected["status"], "ok")
        self.assertEqual(
            selected["verification"]["evidence"]["matched_object_id"], 12
        )

        picked = self.backend.execute_semantic(
            "pick_object",
            {"logical_object_id": "task-box-00", "timeout_s": 10.0},
            context=self.context,
        )
        self.assertEqual(picked["status"], "ok")
        self.assertIsNone(self.adapter.selected_id)

        destination = {
            "schema_version": "1.0",
            "kind": "named",
            "namespace": "grid",
            "location_id": "ne",
            "scene_id": "scene-001",
            "scene_version": "scene-v1",
            "attributes": {},
        }
        moved = self.backend.execute_semantic(
            "move_to_location",
            {"location_ref": destination, "timeout_s": 20.0},
            context=self.context,
        )
        self.assertEqual(moved["resolved_location"]["frame_id"], "map")
        placed = self.backend.execute_semantic(
            "place_at_location",
            {
                "logical_object_id": "task-box-00",
                "location_ref": destination,
                "timeout_s": 10.0,
            },
            context=self.context,
        )
        self.assertEqual(placed["status"], "ok")
        self.assertEqual([item[0] for item in self.adapter.calls], ["pick", "move_to", "place"])

    def test_stopped_camera_frame_is_rejected(self):
        self.adapter.now = time.time() - 10.0
        with self.assertRaises(ObservationError) as caught:
            self.source.capture(channels=("overlay",), reason="stale")
        self.assertEqual(caught.exception.code, "FRAME_STALE")

    def test_shared_dispatcher_hides_legacy_primitives_from_cloud_planner(self):
        dispatcher = build_shared_dispatcher(
            self.adapter, self.backend, robot_id="A"
        )
        visible = {
            definition.name
            for definition in dispatcher.registry.definitions(planner_visible_only=True)
        }
        self.assertIn("move_to_location", visible)
        self.assertIn("select_object", visible)
        self.assertNotIn("move_to", visible)
        self.assertIsNotNone(dispatcher.registry.get("move_to"))
        self.assertEqual(dispatcher.default_robot_id, "A")

    def test_deployment_check_is_read_only_and_never_reports_secrets(self):
        root = Path(__file__).resolve().parents[2]
        config = DeploymentConfig(
            robot_id="A",
            camera_host="192.168.50.79",
            scene_config_path=root / "phi_robot" / "scene_coords.json",
            scene_id="scene-001",
            scene_version="scene-v1",
            map_version="map-v1",
            data_dir=root / ".runtime-test-not-created",
            frontend_dist=root / "phi_robot_fronted" / "dist",
            operator_token="operator-secret",
        )
        report = config.check(
            {
                "GEMINI_API_KEY": "gemini-secret",
                "GEMINI_ROBOTICS_MODEL": "gemini-robotics-er-1.6-preview",
                "GEMINI_API_MODE": "generate_content",
            }
        )
        self.assertTrue(report["ok"])
        serialized = repr(report) + repr(config)
        self.assertNotIn("gemini-secret", serialized)
        self.assertNotIn("operator-secret", serialized)
        self.assertFalse((root / ".runtime-test-not-created").exists())

    def test_gemini_runtime_attaches_to_the_one_existing_supervisor(self):
        dispatcher = build_shared_dispatcher(self.adapter, self.backend)
        with tempfile.TemporaryDirectory() as directory:
            robot_runtime = RobotRuntime(
                robot_id="A",
                adapter=self.adapter,
                ledger_path=Path(directory) / "supervisor.sqlite3",
                dispatcher=dispatcher,
                interrupt_lane=AdapterInterruptLane(self.adapter),
            )
            autonomy = build_gemini_autonomy_runtime(
                robot_id="A",
                semantic_backend=self.backend,
                observation_source=self.source,
                location_resolver=_resolver(),
                interrupt_lane=robot_runtime.interrupt_lane,
                supervisor_ledger_path=None,
                model_ledger_path=Path(directory) / "models.sqlite3",
                config=GeminiRoboticsER2Config(
                    api_key="offline-secret",
                    model="gemini-robotics-er-1.6-preview",
                    api_mode="generate_content",
                ),
                dispatcher=dispatcher,
                supervisor=robot_runtime.supervisor,
            )
            self.assertIs(autonomy.supervisor, robot_runtime.supervisor)
            self.assertIs(autonomy.dispatcher, dispatcher)
            self.assertFalse(autonomy.owns_supervisor_ledger)
            autonomy.close()
            self.assertEqual(robot_runtime.ledger.load_world_state("A").robot_id, "A")
            robot_runtime.close()

    def test_production_guard_denies_untrusted_mutations(self):
        root = Path(__file__).resolve().parents[2]
        config = DeploymentConfig(
            robot_id="A",
            camera_host="camera",
            scene_config_path=root / "phi_robot" / "scene_coords.json",
            scene_id="scene-001",
            scene_version="scene-v1",
            map_version="map-v1",
            data_dir=root / ".runtime-test-not-created",
            frontend_dist=root / "phi_robot_fronted" / "dist",
        )
        app = Flask(__name__)

        @app.post("/api/change")
        def change():
            return jsonify({"ok": True})

        authorizer = _operator_authorizer(config)
        _protect_mutating_routes(SimpleNamespace(app=app), authorizer)
        client = app.test_client()
        denied = client.post(
            "/api/change", environ_base={"REMOTE_ADDR": "10.0.0.8"}
        )
        self.assertEqual(denied.status_code, 403)
        allowed = client.post(
            "/api/change", environ_base={"REMOTE_ADDR": "127.0.0.1"}
        )
        self.assertEqual(allowed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
