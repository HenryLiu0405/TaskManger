from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest

from phi_robot.mission_service import MissionService
from phi_robot.skills.catalog import build_skill_dispatcher
from phi_robot.store import MissionStore

from tests.offline.fakes import ScriptedRobotStub

try:
    from phi_robot.api_server import PhiRobotAPIServer
except ModuleNotFoundError as exc:  # Local bootstrap may not have dev dependencies yet.
    if exc.name not in {"flask", "flask_cors"}:
        raise
    PhiRobotAPIServer = None  # type: ignore[assignment,misc]


@unittest.skipIf(PhiRobotAPIServer is None, "Flask dev dependency is not installed")
class HttpCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.adapter = ScriptedRobotStub()
        self.service = MissionService(MissionStore(data_dir=self._tmp.name))
        dispatcher = build_skill_dispatcher(self.adapter, audit_enabled=False)
        self.server = PhiRobotAPIServer(
            adapter=self.adapter,
            mission_service=self.service,
            skill_dispatcher=dispatcher,
        )
        self.server.app.config.update(TESTING=True)
        self.client = self.server.app.test_client()

    def tearDown(self) -> None:
        from phi_robot.robot_state import safety_fsm

        safety_fsm.set_bypass(False)
        self._tmp.cleanup()

    def test_home_run_endpoint_only_loads_plan(self) -> None:
        response = self.client.post(
            "/api/dev/step_debug/load", json={"destinations": ["se"]}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["total_steps"], 4)
        self.assertEqual(self.adapter.trace, [])

        state = self.client.get("/api/dev/step_debug/state").get_json()
        self.assertEqual(state["state"], "step_ready")
        self.assertEqual(state["current_index"], 0)
        self.assertEqual(len(state["plan"]), 4)

    def test_step_debug_sse_keeps_existing_state_change_envelope(self) -> None:
        self.client.post("/api/dev/step_debug/load", json={"destinations": ["se"]})
        response = self.client.get("/api/dev/step_debug/stream", buffered=False)
        try:
            chunk = next(iter(response.response)).decode("utf-8")
        finally:
            response.close()
        self.assertTrue(chunk.startswith("event: state\n"))
        data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line.removeprefix("data: "))
        self.assertEqual(payload["type"], "state_change")
        self.assertEqual(payload["state"], "step_ready")
        self.assertIn("plan", payload)

    def test_rest_mission_uses_same_dispatcher_and_legacy_http_shape(self) -> None:
        submitted = self.client.post("/api/missions", json={
            "request_id": "http-contract",
            "scene_id": "scene",
            "goal_id": "goal",
            "scene_version": "scene-v1",
            "stock_layout_version": "stock-v1",
            "destination_order": ["se"],
        })
        self.assertEqual(submitted.status_code, 201)
        body = submitted.get_json()
        self.assertEqual(body["status"], "pending")
        mission_id = body["mission_id"]

        started = self.client.post(f"/api/missions/{mission_id}/run")
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.get_json()["status"], "running")

        for _ in range(200):
            record = self.service.get_mission(mission_id)
            if record.status != "running":
                break
            time.sleep(0.005)
        self.assertEqual(record.status, "completed")
        self.assertEqual(self.adapter.names, [
            "move_to", "select_target", "fresh_pose", "pick", "clear_target",
            "move_to", "place",
        ])

        queried = self.client.get(f"/api/missions/{mission_id}")
        self.assertEqual(queried.status_code, 200)
        query_body = queried.get_json()
        self.assertEqual(query_body["status"], "completed")
        self.assertEqual(query_body["total_steps"], 4)

    def test_operator_control_mutations_share_the_robot_writer_lock(self) -> None:
        blocking = ScriptedRobotStub(block_tool="move_to")
        dispatcher = build_skill_dispatcher(blocking, audit_enabled=False)
        server = PhiRobotAPIServer(
            adapter=blocking,
            mission_service=self.service,
            skill_dispatcher=dispatcher,
        )
        server.app.config.update(TESTING=True)
        client = server.app.test_client()

        move_result: list[dict] = []
        worker = threading.Thread(target=lambda: move_result.append(
            dispatcher.execute_legacy(
                "move_to",
                {"target": {"x": 1, "y": 0, "z": 0, "theta": 0}, "timeout_s": 2},
                request_id="blocked-move", goal_id="goal", step_id="move",
                source="test", idempotency_key="blocked-move",
            )
        ))
        worker.start()
        self.assertTrue(blocking.block_started.wait(1.0))
        try:
            input_response = client.post(
                "/api/dev/sonic/input_source", json={"gamepad": True}
            ).get_json()
            bypass_response = client.post(
                "/api/robot/safety/bypass", json={"bypass": True}
            ).get_json()
            self.assertFalse(input_response["ok"])
            self.assertEqual(input_response["error_code"], "RESOURCE_BUSY")
            self.assertFalse(bypass_response["ok"])
            self.assertEqual(bypass_response["error_code"], "RESOURCE_BUSY")
            self.assertNotIn("set_input_source", blocking.names)
        finally:
            blocking.block_release.set()
            worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(move_result[0]["status"], "ok")

    def test_operator_control_http_shapes_remain_compatible(self) -> None:
        initial = self.client.get("/api/dev/sonic/input_source").get_json()
        self.assertEqual(initial, {"active_source": "ROS2"})

        changed = self.client.post(
            "/api/dev/sonic/input_source", json={"gamepad": True}
        ).get_json()
        self.assertTrue(changed["ok"])
        self.assertEqual(changed["active_source"], "GAMEPAD")
        self.assertIsNone(changed["error_code"])
        queried = self.client.get("/api/dev/sonic/input_source").get_json()
        self.assertEqual(queried, {"active_source": "GAMEPAD"})

        bypass = self.client.post(
            "/api/robot/safety/bypass", json={"bypass": True}
        ).get_json()
        self.assertTrue(bypass["ok"])
        self.assertTrue(bypass["bypass"])
        restored = self.client.post(
            "/api/robot/safety/bypass", json={"bypass": False}
        ).get_json()
        self.assertTrue(restored["ok"])
        self.assertFalse(restored["bypass"])


if __name__ == "__main__":
    unittest.main()
