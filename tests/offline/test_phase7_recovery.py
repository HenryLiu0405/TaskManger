from __future__ import annotations

import dataclasses
import tempfile
import threading
import time
import unittest

from phi_robot.autonomy.agent_tools import build_agent_skill_dispatcher
from phi_robot.autonomy.contracts import ActionTimeouts, PlanGraph, PlanNode
from phi_robot.autonomy.goals import GoalSpec
from phi_robot.autonomy.interrupts import StopReceipt
from phi_robot.autonomy.ledger import SupervisorLedger
from phi_robot.autonomy.model_gateway import ModelCallLedger, ModelGateway
from phi_robot.autonomy.objects import ObjectCandidate, ObjectRef, ObjectSelectionStore, PerceptionRef
from phi_robot.autonomy.planner_agent import PlannerAgent
from phi_robot.autonomy.recovery import DropRecoveryCoordinator, RecoveryState
from phi_robot.autonomy.supervisor import ExecutionSupervisor

from tests.offline.test_phase5_agent import (
    FixedObservationSource,
    _destination,
    _object_ref,
    _observation,
    _resolver,
)
from tests.offline.test_phase6_closed_loop import TaskQueueProvider


class BlockingTransportBackend:
    def __init__(self):
        self.transport_started = threading.Event()
        self.release_transport = threading.Event()
        self.calls = []

    def execute_semantic(self, skill_name, args, *, context):
        self.calls.append((skill_name, context["node_id"], dict(args)))
        if context["node_id"] == "transport-old":
            self.transport_started.set()
            self.release_transport.wait(2.0)
            return {
                "status": "error",
                "error_code": "CANCELLED",
                "message": "old navigation cancelled by confirmed interrupt",
            }
        return {
            "status": "ok",
            "message": f"{skill_name} complete",
            "verification": {
                "status": "passed",
                "evidence": {"node_id": context["node_id"]},
            },
        }


class RecordingInterruptLane:
    def __init__(self, backend, *, confirmed=True):
        self.backend = backend
        self.confirmed = confirmed
        self.calls = []

    def stop_motion(self, *, robot_id, mission_id, reason):
        self.calls.append((robot_id, mission_id, reason, time.time()))
        self.backend.release_transport.set()
        now = time.time()
        return StopReceipt(
            accepted=True,
            confirmed=self.confirmed,
            message="zero velocity confirmed" if self.confirmed else "ack missing",
            motion_state="stopped" if self.confirmed else None,
            evidence={"velocity_mps": 0.0} if self.confirmed else {},
            requested_at=now,
            confirmed_at=now if self.confirmed else None,
        )


def _goal():
    return GoalSpec(
        goal_id="goal-recovery",
        original_instruction="把左边的红色箱子放到东北格",
        object_ref=_object_ref("obs-1", 12),
        destination=_destination(),
        constraints={"avoid_people": True},
        success_criteria=("box verified at grid/ne",),
        grounding_observation_id="obs-1",
        grounding_world_version=7,
    )


def _initial_graph(goal):
    destination = goal.destination.to_dict()
    return PlanGraph.sequential(
        plan_id="initial-transport-plan",
        goal_id=goal.goal_id,
        nodes=(
            PlanNode(
                node_id="pick-initial",
                skill_name="pick_object",
                args={"logical_object_id": "task-box-00", "timeout_s": 30.0},
                success_evidence=("holding",),
                timeouts=ActionTimeouts(operation_s=5.0, caller_wait_s=5.0),
            ),
            PlanNode(
                node_id="transport-old",
                skill_name="move_to_location",
                args={"location_ref": destination, "timeout_s": 60.0},
                success_evidence=("arrival",),
                timeouts=ActionTimeouts(operation_s=5.0, caller_wait_s=5.0),
            ),
            PlanNode(
                node_id="place-old",
                skill_name="place_at_location",
                args={
                    "logical_object_id": "task-box-00",
                    "location_ref": destination,
                    "timeout_s": 30.0,
                },
                success_evidence=("placement",),
            ),
        ),
        metadata={"goal_spec": goal.to_dict(), "semantic": True},
    )


def _recovery_observation():
    original = _observation("obs-recovery", world_version=20, object_id=77)
    fresh_ref = PerceptionRef(
        provider="foundationpose",
        object_id=77,
        tracker_session_id="fp-session-after-drop",
        observation_id="obs-recovery",
    )
    return dataclasses.replace(
        original,
        objects=(ObjectCandidate(
            perception_ref=fresh_ref,
            category="box",
            attributes={"color": "red", "relative_position": "near_robot"},
            confidence=0.93,
        ),),
    )


def _recovery_plan(goal):
    destination = goal.destination.to_dict()
    fresh = ObjectRef(
        logical_object_id="task-box-00",
        category="box",
        attributes={"color": "red"},
        perception_ref=PerceptionRef(
            provider="foundationpose",
            object_id=77,
            tracker_session_id="fp-session-after-drop",
            observation_id="obs-recovery",
        ),
    ).to_dict()
    pick_initial = _initial_graph(goal).node("pick-initial").to_dict()
    transport_old = _initial_graph(goal).node("transport-old").to_dict()
    nodes = [
        pick_initial,
        transport_old,
        {
            "node_id": "select-reacquired-77",
            "skill_name": "select_object",
            "skill_version": "1.0",
            "args": {"object_ref": fresh},
            "depends_on": ["transport-old"],
            "success_evidence": ["fresh selection epoch for object 77"],
        },
        {
            "node_id": "repick-1",
            "skill_name": "pick_object",
            "skill_version": "1.0",
            "args": {"logical_object_id": "task-box-00", "timeout_s": 30.0},
            "depends_on": ["select-reacquired-77"],
            "success_evidence": ["fresh post-selection pose and grasp"],
        },
        {
            "node_id": "verify-holding-after-repick",
            "skill_name": "verify_holding",
            "skill_version": "1.0",
            "args": {"logical_object_id": "task-box-00"},
            "depends_on": ["repick-1"],
            "success_evidence": ["holding verified"],
        },
        {
            "node_id": "transport-new",
            "skill_name": "move_to_location",
            "skill_version": "1.0",
            "args": {"location_ref": destination, "timeout_s": 60.0},
            "depends_on": ["verify-holding-after-repick"],
            "success_evidence": ["new arrival invocation"],
        },
        {
            "node_id": "place-recovered",
            "skill_name": "place_at_location",
            "skill_version": "1.0",
            "args": {
                "logical_object_id": "task-box-00",
                "location_ref": destination,
                "timeout_s": 30.0,
            },
            "depends_on": ["transport-new"],
            "success_evidence": ["release verified"],
        },
        {
            "node_id": "verify-final-placement",
            "skill_name": "verify_placement",
            "skill_version": "1.0",
            "args": {
                "logical_object_id": "task-box-00",
                "location_ref": destination,
            },
            "depends_on": ["place-recovered"],
            "success_evidence": ["original destination complete"],
        },
    ]
    return {
        "schema_version": "1.0",
        "plan_id": "recovery-plan-1",
        "goal_id": goal.goal_id,
        "nodes": nodes,
        "metadata": {"superseded_node_ids": ["transport-old"]},
    }


def _recovery_decision(goal):
    return {
        "schema_version": "1.0",
        "decision_id": "recover-decision-1",
        "decision_type": "recover",
        "observation_id": "obs-recovery",
        "world_version": 20,
        "summary": "drop confirmed; object 77 is the same logical red box",
        "payload": {
            "classification": "confirmed_drop",
            "plan_graph": _recovery_plan(goal),
        },
    }


class Phase7RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.backend = BlockingTransportBackend()
        self.dispatcher = build_agent_skill_dispatcher(self.backend, audit_enabled=False)
        self.supervisor_ledger = SupervisorLedger(f"{self.tmp.name}/supervisor.sqlite3")
        self.model_ledger = ModelCallLedger(f"{self.tmp.name}/models.sqlite3")

    def tearDown(self):
        self.model_ledger.close()
        self.supervisor_ledger.close()
        self.tmp.cleanup()

    def build(self, *, confirmed=True):
        lane = RecordingInterruptLane(self.backend, confirmed=confirmed)
        supervisor = ExecutionSupervisor(
            robot_id="robot-01",
            dispatcher=self.dispatcher,
            ledger=self.supervisor_ledger,
            interrupt_lane=lane,
        )
        provider = TaskQueueProvider({"diagnose_drop": [_recovery_decision(_goal())]})
        gateway = ModelGateway(
            providers={"queue": provider},
            ledger=self.model_ledger,
            skill_registry=self.dispatcher.registry,
        )
        source = FixedObservationSource(_recovery_observation())
        planner = PlannerAgent(
            gateway=gateway,
            provider_name="queue",
            observation_source=source,
            location_resolver=_resolver(),
            supervisor=supervisor,
            skill_registry=self.dispatcher.registry,
        )
        selection_store = ObjectSelectionStore()
        recovery = DropRecoveryCoordinator(
            supervisor=supervisor,
            gateway=gateway,
            provider_name="queue",
            observation_source=source,
            plan_validator=planner,
            selection_store=selection_store,
            action_drain_timeout_s=2.0,
            max_recovery_attempts=1,
        )
        return supervisor, provider, lane, recovery, selection_store

    def start_transport(self, supervisor):
        mission_id = supervisor.submit_plan(
            _initial_graph(_goal()),
            request_id="drop-mission",
            metadata={"goal_spec": _goal().to_dict()},
        )
        supervisor.start(mission_id, background=True)
        self.assertTrue(self.backend.transport_started.wait(1.0))
        return mission_id

    def test_confirmed_drop_stops_reacquires_repicks_and_uses_new_navigation(self):
        supervisor, provider, lane, recovery, selection_store = self.build(confirmed=True)
        mission_id = self.start_transport(supervisor)
        outcome = recovery.handle_drop(
            mission_id,
            {
                "event_id": "drop-1",
                "sequence": 9,
                "detected_at": time.time(),
                "evidence_frame_ids": ["drop-frame"],
            },
            background=False,
        )
        self.assertEqual(outcome.state, RecoveryState.RESUMED)
        self.assertEqual(outcome.classification, "confirmed_drop")
        self.assertEqual(len(lane.calls), 1)
        self.assertEqual(provider.calls[0][0], "diagnose_drop")
        snapshot = supervisor.snapshot(mission_id)
        self.assertEqual(snapshot["mission"]["status"], "completed")
        node_ids = [item[1] for item in self.backend.calls]
        self.assertEqual(node_ids.count("transport-old"), 1)
        self.assertEqual(node_ids.count("transport-new"), 1)
        old_index = node_ids.index("transport-old")
        new_index = node_ids.index("transport-new")
        self.assertLess(old_index, new_index)
        self.assertIn("select-reacquired-77", node_ids)
        self.assertIn("repick-1", node_ids)
        revisions = self.supervisor_ledger.list_revisions(mission_id)
        self.assertEqual(revisions[-1].kind.value, "recovery")
        self.assertIn("transport-old", revisions[-1].graph.superseded_node_ids)
        self.assertEqual(
            len(selection_store.lineage("task-box-00")), 1
        )
        states = [
            event["payload"]["state"]
            for event in snapshot["events"]
            if event["event_type"] == "recovery_state_changed"
        ]
        self.assertLess(states.index("drop_suspected"), states.index("stop_confirmed"))
        self.assertLess(states.index("stop_confirmed"), states.index("diagnosing"))
        self.assertEqual(states[-1], "resumed")

    def test_unconfirmed_stop_never_calls_cloud_or_dispatches_recovery(self):
        supervisor, provider, lane, recovery, _ = self.build(confirmed=False)
        mission_id = self.start_transport(supervisor)
        outcome = recovery.handle_drop(
            mission_id,
            {"event_id": "drop-unconfirmed", "detected_at": time.time()},
            background=False,
        )
        self.assertEqual(outcome.state, RecoveryState.INTERVENTION_REQUIRED)
        self.assertEqual(provider.calls, [])
        supervisor.wait(mission_id, timeout=2.0)
        node_ids = [item[1] for item in self.backend.calls]
        self.assertNotIn("select-reacquired-77", node_ids)
        self.assertEqual(
            supervisor.snapshot(mission_id)["mission"]["status"],
            "intervention_required",
        )


if __name__ == "__main__":
    unittest.main()
