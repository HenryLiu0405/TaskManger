from __future__ import annotations

import tempfile
import time
import unittest
from types import SimpleNamespace

from phi_robot.autonomy.agent_tools import SupervisorToolBridge, build_agent_skill_dispatcher
from phi_robot.autonomy.ledger import SupervisorLedger
from phi_robot.autonomy.locations import LocationKind, LocationRef, NineGridLocationResolver
from phi_robot.autonomy.model_gateway import ModelCallLedger, ModelGateway, ReplayProvider
from phi_robot.autonomy.objects import ObjectCandidate, ObjectRef, PerceptionRef
from phi_robot.autonomy.observations import ObservationBundle, ObservationFrame
from phi_robot.autonomy.planner_agent import AgentPlanningError, PlannerAgent
from phi_robot.autonomy.supervisor import ExecutionSupervisor


def _resolver():
    cells = {
        name: {"x": float(index), "y": 0.0, "z": 0.0, "theta": 0.0}
        for index, name in enumerate(("nw", "n", "ne", "w", "c", "e", "sw", "s", "se"))
    }
    return NineGridLocationResolver(
        {"frame": "map", "grid_cells": cells},
        scene_id="scene-001",
        scene_version="v1",
        map_version="map-v1",
    )


def _destination():
    return LocationRef(
        kind=LocationKind.NAMED,
        namespace="grid",
        location_id="ne",
        scene_id="scene-001",
        scene_version="v1",
    )


def _object_ref(observation_id="obs-1", object_id=12):
    return ObjectRef(
        logical_object_id="task-box-00",
        category="box",
        attributes={"color": "red", "relative_position": "left"},
        perception_ref=PerceptionRef(
            provider="foundationpose",
            object_id=object_id,
            tracker_session_id="fp-session-1",
            observation_id=observation_id,
        ),
    )


def _observation(observation_id="obs-1", world_version=7, object_id=12):
    now = time.time()
    frame_data = b"offline-rgb"
    import hashlib
    frame = ObservationFrame(
        frame_id=f"frame-{observation_id}",
        channel="rgb",
        captured_at=now,
        sequence=1,
        content_type="image/jpeg",
        sha256=hashlib.sha256(frame_data).hexdigest(),
        byte_length=len(frame_data),
        data=frame_data,
    )
    ref = PerceptionRef(
        provider="foundationpose",
        object_id=object_id,
        tracker_session_id="fp-session-1",
        observation_id=observation_id,
    )
    return ObservationBundle(
        observation_id=observation_id,
        world_version=world_version,
        captured_at=now,
        valid_until=now + 60.0,
        robot_id="robot-01",
        frames=(frame,),
        objects=(ObjectCandidate(
            perception_ref=ref,
            category="box",
            attributes={"color": "red", "relative_position": "left"},
            confidence=0.98,
        ),),
        robot_state={"control": "autonomous"},
        active_action={},
        recent_events=(),
        synchronization={"method": "offline"},
    )


class FixedObservationSource:
    def __init__(self, observation):
        self.observation = observation
        self.capture_reasons = []

    def capture(self, *, channels, reason):
        self.capture_reasons.append((tuple(channels), reason))
        return self.observation

    def current_world_version(self):
        return self.observation.world_version


class RecordingSemanticBackend:
    def __init__(self):
        self.calls = []

    def execute_semantic(self, skill_name, args, *, context):
        self.calls.append((skill_name, dict(args), dict(context)))
        return {
            "status": "ok",
            "message": f"{skill_name} completed",
            "verification": {
                "status": "passed",
                "evidence": {"offline_fake": True, "skill_name": skill_name},
            },
        }


def _decision(decision_type, payload):
    return {
        "schema_version": "1.0",
        "decision_id": f"decision-{decision_type}",
        "decision_type": decision_type,
        "observation_id": "obs-1",
        "world_version": 7,
        "summary": f"{decision_type} from replay",
        "payload": payload,
    }


def _plan_graph():
    obj = _object_ref().to_dict()
    destination = _destination().to_dict()
    return {
        "schema_version": "1.0",
        "plan_id": "semantic-plan-1",
        "goal_id": "goal-agent-1",
        "nodes": [
            {
                "node_id": "select-red-box",
                "skill_name": "select_object",
                "skill_version": "1.0",
                "args": {"object_ref": obj},
                "depends_on": [],
                "success_evidence": ["selection epoch bound to object 12"],
            },
            {
                "node_id": "pick-red-box",
                "skill_name": "pick_object",
                "skill_version": "1.0",
                "args": {"logical_object_id": "task-box-00", "timeout_s": 30.0},
                "depends_on": ["select-red-box"],
                "success_evidence": ["deterministic and visual holding evidence"],
            },
            {
                "node_id": "move-ne",
                "skill_name": "move_to_location",
                "skill_version": "1.0",
                "args": {"location_ref": destination, "timeout_s": 60.0},
                "depends_on": ["pick-red-box"],
                "success_evidence": ["arrival at resolved grid/ne"],
            },
            {
                "node_id": "place-ne",
                "skill_name": "place_at_location",
                "skill_version": "1.0",
                "args": {
                    "logical_object_id": "task-box-00",
                    "location_ref": destination,
                    "timeout_s": 30.0,
                },
                "depends_on": ["move-ne"],
                "success_evidence": ["release confirmed"],
            },
            {
                "node_id": "verify-ne",
                "skill_name": "verify_placement",
                "skill_version": "1.0",
                "args": {
                    "logical_object_id": "task-box-00",
                    "location_ref": destination,
                },
                "depends_on": ["place-ne"],
                "success_evidence": ["red box is visually and deterministically at grid/ne"],
            },
        ],
        "metadata": {"model_strategy": "grounded arbitrary semantic graph"},
    }


class Phase5AgentTests(unittest.TestCase):
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
        self.observation_source = FixedObservationSource(_observation())

    def tearDown(self):
        self.model_ledger.close()
        self.supervisor_ledger.close()
        self.tmp.cleanup()

    def make_agent(self, records):
        provider = ReplayProvider(records)
        gateway = ModelGateway(
            providers={"replay": provider},
            ledger=self.model_ledger,
            skill_registry=self.dispatcher.registry,
        )
        return PlannerAgent(
            gateway=gateway,
            provider_name="replay",
            observation_source=self.observation_source,
            location_resolver=_resolver(),
            supervisor=self.supervisor,
            skill_registry=self.dispatcher.registry,
        )

    def test_one_instruction_grounds_id_and_executes_complete_semantic_plan(self):
        records = {
            "ground": {"response_id": "ground-response", "decision": _decision(
                "goal",
                {"goal_spec": {
                    "goal_id": "goal-agent-1",
                    "original_instruction": "MODEL MUST NOT OWN THIS FIELD",
                    "object_ref": _object_ref().to_dict(),
                    "destination": _destination().to_dict(),
                    "constraints": {"avoid": "people"},
                    "success_criteria": ["box at grid/ne", "robot no longer holding"],
                }},
            )},
            "plan": {"response_id": "plan-response", "decision": _decision(
                "plan", {"plan_graph": _plan_graph()}
            )},
        }
        agent = self.make_agent(records)
        instruction = "把左边的红色箱子放到东北格"
        task = agent.submit_instruction(
            instruction,
            request_id="request-agent-1",
            background=False,
            replay_keys={"ground_goal": "ground", "create_plan": "plan"},
        )
        snapshot = self.supervisor.snapshot(task.mission_id)
        self.assertEqual(snapshot["mission"]["status"], "completed")
        self.assertEqual(task.goal.original_instruction, instruction)
        self.assertEqual(task.goal.object_ref.perception_ref.object_id, 12)
        self.assertEqual(task.goal.destination.qualified_id, "grid/ne")
        self.assertEqual(
            [item[0] for item in self.backend.calls],
            [
                "select_object", "pick_object", "move_to_location",
                "place_at_location", "verify_placement",
            ],
        )
        self.assertNotIn("move_to", [item[0] for item in self.backend.calls])
        self.assertEqual(len(snapshot["invocations"]), 5)
        self.assertTrue(all(
            item["result"]["verification"]["status"] == "passed"
            for item in snapshot["invocations"]
        ))

    def test_wrong_perception_id_is_rejected_before_physical_submission(self):
        wrong = _object_ref(object_id=999).to_dict()
        records = {
            "ground": {"decision": _decision("goal", {"goal_spec": {
                "goal_id": "goal-agent-1",
                "object_ref": wrong,
                "destination": _destination().to_dict(),
                "constraints": {},
                "success_criteria": ["verified placement"],
            }})},
        }
        agent = self.make_agent(records)
        with self.assertRaises(AgentPlanningError) as context:
            agent.submit_instruction(
                "move box",
                background=False,
                replay_keys={"ground_goal": "ground"},
            )
        self.assertEqual(context.exception.code, "OBJECT_EVIDENCE_MISMATCH")
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.supervisor_ledger.list_missions(), [])

    def test_model_stop_tool_uses_supervisor_interrupt_bridge(self):
        class FakeSupervisor:
            def __init__(self):
                self.calls = []

            def request_stop(self, mission_id, *, reason):
                self.calls.append((mission_id, reason))
                return SimpleNamespace(to_dict=lambda: {"confirmed": True})

        supervisor = FakeSupervisor()
        bridge = SupervisorToolBridge(
            supervisor=supervisor,
            observation_source=self.observation_source,
        )
        result = bridge.execute(
            SimpleNamespace(
                decision_type="tool_call",
                payload={"skill_name": "stop_motion", "args": {"reason": "visual hazard"}},
            ),
            mission_id="mission-1",
        )
        self.assertTrue(result["receipt"]["confirmed"])
        self.assertEqual(supervisor.calls, [("mission-1", "visual hazard")])
        with self.assertRaisesRegex(ValueError, "validated PlanGraph"):
            bridge.execute(
                SimpleNamespace(
                    decision_type="tool_call",
                    payload={"skill_name": "pick_object", "args": {}},
                ),
                mission_id="mission-1",
            )


if __name__ == "__main__":
    unittest.main()
