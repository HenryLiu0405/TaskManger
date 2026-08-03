from __future__ import annotations

import tempfile
import unittest

from phi_robot.autonomy.agent_tools import build_agent_skill_dispatcher
from phi_robot.autonomy.closed_loop import ClosedLoopAgentController
from phi_robot.autonomy.ledger import SupervisorLedger
from phi_robot.autonomy.model_gateway import ModelCallLedger, ModelGateway
from phi_robot.autonomy.planner_agent import PlannerAgent
from phi_robot.autonomy.supervisor import ExecutionSupervisor

from tests.offline.test_phase5_agent import (
    _decision,
    _destination,
    _object_ref,
    _observation,
    _resolver,
)


class SequenceObservationSource:
    def __init__(self, observations):
        self.observations = list(observations)
        self.index = 0
        self.last = self.observations[0]

    def capture(self, *, channels, reason):
        if self.index < len(self.observations):
            self.last = self.observations[self.index]
            self.index += 1
        return self.last

    def current_world_version(self):
        return self.last.world_version


class TaskQueueProvider:
    name = "queue"
    model = "offline-model"

    def __init__(self, responses):
        self.responses = {key: list(value) for key, value in responses.items()}
        self.calls = []

    def invoke(self, request, media, *, timeout_s):
        task = request["task"]
        self.calls.append((task, request["observation"]["observation_id"]))
        if task not in self.responses or not self.responses[task]:
            raise AssertionError(f"unexpected provider task: {task}")
        return {"response_id": f"response-{len(self.calls)}", "decision": self.responses[task].pop(0)}


class ScriptedBackend:
    def __init__(self, fail_first_pick=False):
        self.calls = []
        self.fail_first_pick = fail_first_pick
        self.pick_count = 0

    def execute_semantic(self, skill_name, args, *, context):
        self.calls.append((skill_name, context["node_id"], context["idempotency_key"]))
        if skill_name == "pick_object":
            self.pick_count += 1
            if self.fail_first_pick and self.pick_count == 1:
                return {
                    "status": "error",
                    "error_code": "GRIP_FAIL",
                    "message": "visual evidence shows no grasp",
                }
        return {
            "status": "ok",
            "message": "done",
            "verification": {"status": "passed", "evidence": {"scripted": True}},
        }


def _goal_payload():
    return {
        "goal_spec": {
            "goal_id": "goal-closed-loop",
            "object_ref": _object_ref().to_dict(),
            "destination": _destination().to_dict(),
            "constraints": {},
            "success_criteria": ["evidence-backed completion"],
        }
    }


def _move_node(node_id, location, depends_on=()):
    return {
        "node_id": node_id,
        "skill_name": "move_to_location",
        "skill_version": "1.0",
        "args": {"location_ref": location, "timeout_s": 30.0},
        "depends_on": list(depends_on),
        "success_evidence": [f"arrival evidence for {node_id}"],
    }


def _plan(nodes, *, plan_id, superseded=()):
    return {
        "schema_version": "1.0",
        "plan_id": plan_id,
        "goal_id": "goal-closed-loop",
        "nodes": nodes,
        "metadata": {"superseded_node_ids": list(superseded)},
    }


def _model_decision(decision_type, observation_id, payload, summary="decision"):
    return {
        "schema_version": "1.0",
        "decision_id": f"{decision_type}-{observation_id}",
        "decision_type": decision_type,
        "observation_id": observation_id,
        "world_version": 7,
        "summary": summary,
        "payload": payload,
    }


class Phase6ClosedLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.supervisor_ledger = SupervisorLedger(f"{self.tmp.name}/supervisor.sqlite3")
        self.model_ledger = ModelCallLedger(f"{self.tmp.name}/models.sqlite3")

    def tearDown(self):
        self.model_ledger.close()
        self.supervisor_ledger.close()
        self.tmp.cleanup()

    def build(self, backend, provider, observations):
        dispatcher = build_agent_skill_dispatcher(backend, audit_enabled=False)
        supervisor = ExecutionSupervisor(
            robot_id="robot-01",
            dispatcher=dispatcher,
            ledger=self.supervisor_ledger,
        )
        source = SequenceObservationSource(observations)
        gateway = ModelGateway(
            providers={"queue": provider},
            ledger=self.model_ledger,
            skill_registry=dispatcher.registry,
        )
        planner = PlannerAgent(
            gateway=gateway,
            provider_name="queue",
            observation_source=source,
            location_resolver=_resolver(),
            supervisor=supervisor,
            skill_registry=dispatcher.registry,
        )
        supervisor.boundary_gate = ClosedLoopAgentController(
            gateway=gateway,
            provider_name="queue",
            observation_source=source,
            plan_validator=planner,
            max_reobservations=1,
        )
        return planner, supervisor

    def test_scene_change_replaces_only_unexecuted_tail(self):
        center = {**_destination().to_dict(), "location_id": "c"}
        initial_graph = _plan([
            _move_node("move-center", center),
            _move_node("old-tail", _destination().to_dict(), ("move-center",)),
        ], plan_id="initial")
        revised_graph = _plan([
            _move_node("move-center", center),
            _move_node("new-tail", _destination().to_dict(), ("move-center",)),
        ], plan_id="revision-2")
        provider = TaskQueueProvider({
            "ground_goal": [_model_decision("goal", "obs-1", _goal_payload())],
            "create_plan": [_model_decision("plan", "obs-1", {"plan_graph": initial_graph})],
            "verify_action": [
                _model_decision(
                    "replan",
                    "obs-2",
                    {
                        "plan_graph": revised_graph,
                        "evidence_conflict": {
                            "deterministic": "arrived",
                            "visual": "destination became occupied",
                        },
                    },
                    "occupied destination requires new tail",
                ),
                _model_decision("verify", "obs-3", {"verdict": "continue"}),
            ],
        })
        backend = ScriptedBackend()
        planner, supervisor = self.build(
            backend,
            provider,
            [_observation("obs-1"), _observation("obs-2"), _observation("obs-3")],
        )
        task = planner.submit_instruction(
            "把左边红箱放到东北格", request_id="closed-loop-1", background=False
        )
        snapshot = supervisor.snapshot(task.mission_id)
        self.assertEqual(snapshot["mission"]["status"], "completed")
        self.assertEqual([item[1] for item in backend.calls], ["move-center", "new-tail"])
        self.assertNotIn("old-tail", [item[1] for item in backend.calls])
        self.assertEqual(len(self.supervisor_ledger.list_revisions(task.mission_id)), 2)
        conflicts = [
            event for event in snapshot["events"] if event["event_type"] == "evidence_conflict"
        ]
        self.assertEqual(len(conflicts), 1)

    def test_failed_pick_gets_new_node_and_never_retries_old_invocation(self):
        first = {
            "node_id": "pick-attempt-1",
            "skill_name": "pick_object",
            "skill_version": "1.0",
            "args": {"logical_object_id": "task-box-00", "timeout_s": 30.0},
            "depends_on": [],
            "success_evidence": ["holding evidence"],
        }
        second = {
            **first,
            "node_id": "pick-attempt-2",
            "depends_on": ["pick-attempt-1"],
        }
        provider = TaskQueueProvider({
            "ground_goal": [_model_decision("goal", "obs-1", _goal_payload())],
            "create_plan": [_model_decision(
                "plan", "obs-1", {"plan_graph": _plan([first], plan_id="pick-plan")}
            )],
            "verify_action": [
                _model_decision(
                    "replan",
                    "obs-2",
                    {"plan_graph": _plan(
                        [first, second], plan_id="pick-revision", superseded=("pick-attempt-1",)
                    )},
                    "fresh evidence supports a different grasp strategy",
                ),
                _model_decision("verify", "obs-3", {"verdict": "continue"}),
            ],
        })
        backend = ScriptedBackend(fail_first_pick=True)
        planner, supervisor = self.build(
            backend,
            provider,
            [_observation("obs-1"), _observation("obs-2"), _observation("obs-3")],
        )
        task = planner.submit_instruction("pick the red box", background=False)
        snapshot = supervisor.snapshot(task.mission_id)
        self.assertEqual(snapshot["mission"]["status"], "completed")
        self.assertEqual(
            [item[1] for item in backend.calls], ["pick-attempt-1", "pick-attempt-2"]
        )
        self.assertEqual(len({item[2] for item in backend.calls}), 2)
        self.assertEqual(
            [item["status"] for item in snapshot["invocations"]],
            ["failed", "succeeded"],
        )


if __name__ == "__main__":
    unittest.main()
