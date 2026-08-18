from __future__ import annotations

import json
import tempfile
import time
import unittest

from phi_robot.autonomy.agent_tools import build_agent_skill_dispatcher
from phi_robot.autonomy.evaluation import export_replay_bundle, mission_metrics
from phi_robot.autonomy.ledger import SupervisorLedger
from phi_robot.autonomy.model_gateway import ModelCallLedger, ModelGateway, ReplayProvider
from phi_robot.autonomy.planner_agent import PlannerAgent
from phi_robot.autonomy.supervisor import ExecutionSupervisor
from phi_robot.mission_service import MissionService
from phi_robot.skills.catalog import build_skill_dispatcher
from phi_robot.store import MissionStore

from tests.offline.fakes import ScriptedRobotStub
from tests.offline.test_phase5_agent import (
    FixedObservationSource,
    RecordingSemanticBackend,
    _decision,
    _destination,
    _object_ref,
    _observation,
    _plan_graph,
    _resolver,
)

try:
    from phi_robot.api_server import PhiRobotAPIServer
except ModuleNotFoundError as exc:
    if exc.name not in {"flask", "flask_cors"}:
        raise
    PhiRobotAPIServer = None


class Phase8ReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.backend = RecordingSemanticBackend()
        self.dispatcher = build_agent_skill_dispatcher(self.backend, audit_enabled=False)
        self.supervisor_ledger = SupervisorLedger(f"{self.tmp.name}/supervisor.sqlite3")
        self.model_ledger = ModelCallLedger(f"{self.tmp.name}/models.sqlite3")
        self.supervisor = ExecutionSupervisor(
            robot_id="robot-01",
            dispatcher=self.dispatcher,
            ledger=self.supervisor_ledger,
        )

    def tearDown(self):
        self.model_ledger.close()
        self.supervisor_ledger.close()
        self.tmp.cleanup()

    def test_replay_links_decisions_actions_and_hashes_without_sensitive_payloads(self):
        records = {
            "ground": {"decision": _decision("goal", {"goal_spec": {
                "goal_id": "goal-agent-1",
                "object_ref": _object_ref().to_dict(),
                "destination": _destination().to_dict(),
                "constraints": {},
                "success_criteria": ["verified placement"],
            }})},
            "plan": {"decision": _decision("plan", {"plan_graph": _plan_graph()})},
        }
        gateway = ModelGateway(
            providers={"replay": ReplayProvider(records)},
            ledger=self.model_ledger,
            skill_registry=self.dispatcher.registry,
        )
        agent = PlannerAgent(
            gateway=gateway,
            provider_name="replay",
            observation_source=FixedObservationSource(_observation()),
            location_resolver=_resolver(),
            supervisor=self.supervisor,
            skill_registry=self.dispatcher.registry,
        )
        task = agent.submit_instruction(
            "把左边的红色箱子放到东北格",
            background=False,
            replay_keys={"ground_goal": "ground", "create_plan": "plan"},
        )
        replay = export_replay_bundle(
            self.supervisor_ledger,
            task.mission_id,
            model_ledger=self.model_ledger,
        ).to_dict()
        encoded = json.dumps(replay, ensure_ascii=False)
        self.assertEqual(len(replay["model_calls"]), 2)
        self.assertEqual(len(replay["invocations"]), 5)
        self.assertEqual(replay["privacy"]["raw_images_included"], False)
        self.assertNotIn("offline-rgb", encoded)
        self.assertNotIn("sensitive runtime prompt", encoded)
        self.assertNotIn("test-only-secret", encoded)
        self.assertTrue(all(call["frame_hashes"] for call in replay["model_calls"]))

        metrics = mission_metrics(
            self.supervisor_ledger,
            task.mission_id,
            model_ledger=self.model_ledger,
        )
        self.assertTrue(metrics["goal_success"])
        self.assertEqual(metrics["vlm"]["call_count"], 2)
        self.assertEqual(metrics["physical_invocation_count"], 5)
        self.assertEqual(metrics["duplicate_physical_actions_after_restart"], 0)
        self.assertEqual(metrics["invalid_model_output_physical_action_count"], 0)


class FakeAutonomyService:
    def __init__(self):
        self.calls = []

    def submit_instruction(self, instruction, *, request_id=None, background=True):
        self.calls.append(("submit", instruction, request_id, background))
        return {"task": {"mission_id": "autonomy-1"}, "snapshot": {"mission": {"status": "running"}}}

    def snapshot(self, mission_id):
        self.calls.append(("snapshot", mission_id))
        return {"mission": {"mission_id": mission_id, "status": "running"}}

    def replay_bundle(self, mission_id):
        self.calls.append(("replay", mission_id))
        return {"schema_version": "1.0", "mission": {"mission_id": mission_id}}

    def metrics(self, mission_id):
        self.calls.append(("metrics", mission_id))
        return {"mission_id": mission_id, "vlm": {"call_count": 2}}

    def pause(self, mission_id, *, principal):
        self.calls.append(("pause", mission_id, principal))
        return {"mission": {"status": "paused"}}

    def stop(self, mission_id, *, principal, reason):
        self.calls.append(("stop", mission_id, principal, reason))
        return {"receipt": {"confirmed": True}}


@unittest.skipIf(PhiRobotAPIServer is None, "Flask dev dependency is not installed")
class Phase8HttpConsoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.adapter = ScriptedRobotStub()
        service = MissionService(MissionStore(data_dir=self.tmp.name))
        self.autonomy = FakeAutonomyService()
        self.server = PhiRobotAPIServer(
            adapter=self.adapter,
            mission_service=service,
            skill_dispatcher=build_skill_dispatcher(self.adapter, audit_enabled=False),
            autonomy_service=self.autonomy,
        )
        self.server.app.config.update(TESTING=True)
        self.client = self.server.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_instruction_monitor_replay_metrics_and_operator_auth_boundary(self):
        missing = self.client.post("/api/autonomy/tasks", json={})
        self.assertEqual(missing.status_code, 400)
        submitted = self.client.post(
            "/api/autonomy/tasks",
            json={"instruction": "move the red box", "request_id": "r-1"},
        )
        self.assertEqual(submitted.status_code, 202)
        self.assertEqual(submitted.get_json()["task"]["mission_id"], "autonomy-1")
        self.assertEqual(
            self.client.get("/api/autonomy/tasks/autonomy-1").status_code, 200
        )
        self.assertEqual(
            self.client.get("/api/autonomy/tasks/autonomy-1/replay").status_code, 200
        )
        self.assertEqual(
            self.client.get("/api/autonomy/tasks/autonomy-1/metrics").get_json()["vlm"]["call_count"],
            2,
        )
        denied = self.client.post("/api/autonomy/tasks/autonomy-1/stop", json={})
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn("stop", [call[0] for call in self.autonomy.calls])

        self.server.autonomy_operator_authorizer = lambda request: "local-test-operator"
        paused = self.client.post("/api/autonomy/tasks/autonomy-1/pause", json={})
        stopped = self.client.post(
            "/api/autonomy/tasks/autonomy-1/stop", json={"reason": "test stop"}
        )
        self.assertEqual(paused.status_code, 200)
        self.assertTrue(stopped.get_json()["receipt"]["confirmed"])
        self.assertIn(("pause", "autonomy-1", "local-test-operator"), self.autonomy.calls)

    def test_readiness_uses_live_adapter_evidence_without_cloud_probe(self):
        now = time.time()
        self.adapter.get_services_status = lambda: {
            "/start_navigation": True,
            "/get_locomotion_mode": True,
        }
        self.adapter.get_fp_video_sample = lambda channel: {
            "captured_at": now,
            "sequence": 12,
            "data": b"not-returned-to-browser",
        }
        self.adapter.get_fp_state_sample = lambda: {
            "received_at": now,
            "sequence": 8,
            "state": {"trackers": [{"object_id": 3}]},
        }
        self.adapter.get_odom_sample = lambda: {
            "received_at": now,
            "sequence": 21,
        }

        response = self.client.get("/api/autonomy/readiness")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["ready_for_task"])
        self.assertEqual(
            set(payload["components"]),
            {"vlm", "camera", "foundationpose", "ros", "robot", "supervisor"},
        )
        self.assertEqual(payload["components"]["camera"]["status"], "ready")
        self.assertEqual(payload["components"]["foundationpose"]["status"], "ready")
        self.assertEqual(payload["components"]["ros"]["status"], "ready")
        self.assertEqual(payload["components"]["robot"]["status"], "ready")
        self.assertEqual(payload["components"]["supervisor"]["status"], "ready")
        self.assertEqual(payload["components"]["vlm"]["status"], "configured")
        self.assertNotIn("not-returned-to-browser", json.dumps(payload))

        self.adapter.get_fp_video_sample = lambda channel: {
            "captured_at": now - 10,
            "sequence": 12,
            "data": b"stale-frame",
        }
        stale = self.client.get("/api/autonomy/readiness").get_json()
        self.assertEqual(stale["components"]["camera"]["status"], "stale")
        self.assertFalse(stale["ready_for_task"])


if __name__ == "__main__":
    unittest.main()
