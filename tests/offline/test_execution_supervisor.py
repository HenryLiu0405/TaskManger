from __future__ import annotations

import tempfile
import time
import unittest

from phi_robot.autonomy.contracts import (
    ActionLifecycle,
    ActionTimeouts,
    MissionLifecycle,
    PlanGraph,
    PlanNode,
    RevisionKind,
)
from phi_robot.autonomy.interrupts import AdapterInterruptLane
from phi_robot.autonomy.ledger import SupervisorLedger
from phi_robot.autonomy.runtime import RobotRuntime, RobotRuntimeRegistry
from phi_robot.autonomy.supervisor import ExecutionSupervisor
from phi_robot.autonomy.world_state import RobotWorldState, StateDimension
from phi_robot.skills.catalog import build_skill_dispatcher

from tests.offline.fakes import ScriptedRobotStub


def _move_node(node_id: str, x: float = 1.0, *, depends_on: tuple[str, ...] = ()) -> PlanNode:
    return PlanNode(
        node_id=node_id,
        skill_name="move_to",
        args={
            "target": {"x": x, "y": 0.0, "z": 0.0, "theta": 0.0},
            "timeout_s": 2.0,
        },
        depends_on=depends_on,
        timeouts=ActionTimeouts(dispatch_s=1.0, operation_s=2.0, caller_wait_s=2.5),
    )


def _one_move_graph(*, plan_id: str = "plan-one") -> PlanGraph:
    return PlanGraph(plan_id=plan_id, goal_id="goal", nodes=(_move_node("move"),))


class ExecutionContractTests(unittest.TestCase):
    def test_plan_graph_rejects_cycles_and_serializes_three_timeouts(self) -> None:
        with self.assertRaisesRegex(ValueError, "cycle"):
            PlanGraph(
                plan_id="cycle",
                goal_id="goal",
                nodes=(
                    _move_node("a", depends_on=("b",)),
                    _move_node("b", depends_on=("a",)),
                ),
            )
        round_trip = PlanGraph.from_dict(_one_move_graph().to_dict())
        self.assertEqual(round_trip, _one_move_graph())
        timeouts = round_trip.nodes[0].to_dict()["timeouts"]
        self.assertEqual(set(timeouts), {"dispatch_s", "operation_s", "caller_wait_s"})

    def test_world_state_uses_explicit_unknown_with_provenance(self) -> None:
        state = RobotWorldState(robot_id="robot")
        motion = state.fact(StateDimension.MOTION)
        self.assertIsNone(motion.value)
        self.assertFalse(motion.known)
        self.assertFalse(motion.is_fresh())
        updated = state.update(
            StateDimension.MOTION,
            "stopped",
            source="stop_ack",
            evidence={"velocity_mps": 0.0},
        )
        self.assertEqual(updated.world_version, 1)
        self.assertEqual(updated.fact("motion").source, "stop_ack")
        self.assertEqual(updated.fact("payload").to_dict()["value"], None)


class ExecutionSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.adapter = ScriptedRobotStub()
        self.dispatcher = build_skill_dispatcher(self.adapter, audit_enabled=False)
        self.ledger = SupervisorLedger(f"{self._tmp.name}/supervisor.sqlite3")

    def tearDown(self) -> None:
        self.ledger.close()
        self._tmp.cleanup()

    def make_supervisor(self, *, adapter=None, dispatcher=None) -> ExecutionSupervisor:
        selected_adapter = adapter or self.adapter
        return ExecutionSupervisor(
            robot_id="robot-01",
            dispatcher=dispatcher or self.dispatcher,
            ledger=self.ledger,
            interrupt_lane=AdapterInterruptLane(selected_adapter),
        )

    def test_one_submission_runs_complete_plan_and_persists_evidence(self) -> None:
        graph = PlanGraph.sequential(
            plan_id="fixed-plan",
            goal_id="goal",
            nodes=(
                _move_node("move-stock", 0.0),
                PlanNode(
                    node_id="pick",
                    skill_name="pick",
                    args={"object_id": "box-00", "timeout_s": 2.0},
                    annotations={"slot_nav2_x": 0.0, "slot_nav2_y": 0.0},
                    timeouts=ActionTimeouts(operation_s=2.0, caller_wait_s=2.5),
                ),
                _move_node("move-goal", 1.0),
                PlanNode(
                    node_id="place",
                    skill_name="place",
                    args={
                        "target": {"x": 1.0, "y": 0.0, "z": 0.0, "theta": 0.0},
                        "timeout_s": 2.0,
                    },
                    timeouts=ActionTimeouts(operation_s=2.0, caller_wait_s=2.5),
                ),
            ),
        )
        supervisor = self.make_supervisor()
        mission_id = supervisor.submit_plan(graph, request_id="fixed-request")
        snapshot = supervisor.start(mission_id, background=False)

        self.assertEqual(snapshot["mission"]["status"], MissionLifecycle.COMPLETED.value)
        self.assertEqual(
            self.adapter.names,
            [
                "move_to", "select_target", "fresh_pose", "pick",
                "clear_target", "move_to", "place",
            ],
        )
        self.assertEqual(
            [item["status"] for item in snapshot["invocations"]],
            [ActionLifecycle.SUCCEEDED.value] * 4,
        )
        self.assertTrue(all(
            item["result"]["verification"]["status"] == "passed"
            for item in snapshot["invocations"]
        ))
        self.assertEqual(
            snapshot["world_state"]["dimensions"]["payload"]["value"], "empty"
        )

    def test_restart_never_redispatches_a_durable_incomplete_action(self) -> None:
        mission_id = self.ledger.create_mission(
            robot_id="robot-01",
            request_id="crash-request",
            graph=_one_move_graph(),
        )
        revision = self.ledger.active_revision(mission_id)
        invocation, _ = self.ledger.prepare_invocation(
            mission_id=mission_id,
            plan_revision_id=revision.plan_revision_id,
            node_id="move",
            skill_name="move_to",
            skill_version="1.0",
            idempotency_key=f"mission:{mission_id}:node:move",
            args=revision.graph.node("move").args,
            annotations={},
        )
        self.ledger.mark_dispatched(invocation["invocation_id"])
        self.ledger.set_mission_status(
            mission_id, MissionLifecycle.RUNNING, current_node_id="move"
        )

        supervisor = self.make_supervisor()
        snapshot = supervisor.snapshot(mission_id)
        self.assertIn(mission_id, supervisor.recovered_mission_ids)
        self.assertEqual(
            snapshot["mission"]["status"], MissionLifecycle.RECONCILIATION_REQUIRED.value
        )
        self.assertEqual(
            snapshot["invocations"][0]["status"],
            ActionLifecycle.RECONCILIATION_REQUIRED.value,
        )
        with self.assertRaisesRegex(ValueError, "cannot start"):
            supervisor.start(mission_id, background=False)
        self.assertEqual(self.adapter.calls["move_to"], 0)

    def test_unknown_result_requires_trusted_reconciliation_without_retry(self) -> None:
        adapter = ScriptedRobotStub(failures={
            "move_to": [{"error_code": "SERVICE_UNAVAILABLE", "message": "response lost"}],
        })
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        supervisor = self.make_supervisor(adapter=adapter, dispatcher=dispatcher)
        mission_id = supervisor.submit_plan(_one_move_graph(), request_id="unknown")
        snapshot = supervisor.start(mission_id, background=False)
        self.assertEqual(
            snapshot["mission"]["status"], MissionLifecycle.RECONCILIATION_REQUIRED.value
        )
        self.assertEqual(adapter.calls["move_to"], 1)
        invocation_id = snapshot["invocations"][0]["invocation_id"]

        with self.assertRaises(PermissionError):
            supervisor.reconcile_invocation(
                mission_id,
                invocation_id,
                resolution=ActionLifecycle.SUCCEEDED,
                evidence={"pose_sensor": "arrived"},
                principal="operator",
            )
        reconciled = supervisor.reconcile_invocation(
            mission_id,
            invocation_id,
            resolution=ActionLifecycle.SUCCEEDED,
            evidence={"pose_sensor": "arrived", "observed_at": time.time()},
            principal="local-operator",
            trusted_local_principal=True,
        )
        self.assertEqual(reconciled["mission"]["status"], MissionLifecycle.READY.value)
        completed = supervisor.start(mission_id, background=False)
        self.assertEqual(completed["mission"]["status"], MissionLifecycle.COMPLETED.value)
        self.assertEqual(adapter.calls["move_to"], 1)

    def test_interrupt_lane_stops_before_blocked_normal_action_drains(self) -> None:
        class ConfirmingStopAdapter(ScriptedRobotStub):
            def stop_motion(self, **context):
                self._record("stop_motion", context)
                return {
                    "accepted": True,
                    "confirmed": True,
                    "motion_state": "stopped",
                    "message": "zero velocity confirmed",
                }

        adapter = ConfirmingStopAdapter(block_tool="move_to")
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        supervisor = self.make_supervisor(adapter=adapter, dispatcher=dispatcher)
        graph = PlanGraph.sequential(
            plan_id="interrupt-plan",
            goal_id="goal",
            nodes=(_move_node("first", 1.0), _move_node("must-not-run", 2.0)),
        )
        mission_id = supervisor.submit_plan(graph, request_id="interrupt")
        supervisor.start(mission_id, background=True)
        self.assertTrue(adapter.block_started.wait(1.0))

        started = time.monotonic()
        receipt = supervisor.record_drop_suspected(
            mission_id,
            event={"detector_sequence": 7, "detected_at": time.time()},
        )
        elapsed = time.monotonic() - started
        self.assertTrue(receipt.confirmed)
        self.assertLess(elapsed, 0.5)
        self.assertIn("stop_motion", adapter.names)
        self.assertEqual(adapter.calls["move_to"], 1)
        self.assertFalse(adapter.block_release.is_set())

        adapter.block_release.set()
        snapshot = supervisor.wait(mission_id, timeout=2.0)
        self.assertEqual(adapter.calls["move_to"], 1)
        self.assertEqual(snapshot["mission"]["status"], MissionLifecycle.RECOVERING.value)
        self.assertEqual(
            snapshot["world_state"]["dimensions"]["motion"]["value"], "stopped"
        )
        self.assertEqual(snapshot["interrupts"][0]["status"], "stop_confirmed")

    def test_unconfirmed_stop_enters_intervention_state(self) -> None:
        supervisor = self.make_supervisor()
        mission_id = supervisor.submit_plan(_one_move_graph(), request_id="unconfirmed")
        # ScriptedRobotStub.pause_navigation returns no acknowledgement payload.
        receipt = supervisor.request_stop(mission_id, reason="operator abort")
        self.assertTrue(receipt.accepted)
        self.assertFalse(receipt.confirmed)
        snapshot = supervisor.snapshot(mission_id)
        self.assertEqual(
            snapshot["mission"]["status"], MissionLifecycle.INTERVENTION_REQUIRED.value
        )
        self.assertIsNone(snapshot["world_state"]["dimensions"]["motion"]["value"])

    def test_high_risk_success_without_verification_is_failed(self) -> None:
        class UnverifiedAdapter:
            def execute(self, tool, args, *, request_id, goal_id, step_id):
                return {
                    "status": "ok",
                    "message": "backend claimed success",
                    "state": {},
                    "metrics": {},
                }

            def snapshot(self):
                return {}

        adapter = UnverifiedAdapter()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        supervisor = self.make_supervisor(adapter=adapter, dispatcher=dispatcher)
        mission_id = supervisor.submit_plan(_one_move_graph(), request_id="unverified")
        snapshot = supervisor.start(mission_id, background=False)
        self.assertEqual(snapshot["mission"]["status"], MissionLifecycle.FAILED.value)
        self.assertEqual(snapshot["invocations"][0]["status"], ActionLifecycle.FAILED.value)
        self.assertEqual(
            snapshot["invocations"][0]["result"]["supervisor_error"]["code"],
            "REQUIRED_VERIFICATION_MISSING",
        )

    def test_dispatched_nodes_are_immutable_across_plan_revisions(self) -> None:
        mission_id = self.ledger.create_mission(
            robot_id="robot-01", request_id="revision", graph=_one_move_graph()
        )
        revision = self.ledger.active_revision(mission_id)
        invocation, _ = self.ledger.prepare_invocation(
            mission_id=mission_id,
            plan_revision_id=revision.plan_revision_id,
            node_id="move",
            skill_name="move_to",
            skill_version="1.0",
            idempotency_key="stable-key",
            args=revision.graph.node("move").args,
            annotations={},
        )
        self.ledger.mark_dispatched(invocation["invocation_id"])
        changed = PlanGraph(
            plan_id="changed",
            goal_id="goal",
            nodes=(_move_node("move", 99.0),),
        )
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.ledger.append_revision(
                mission_id,
                changed,
                kind=RevisionKind.REPLAN,
                reason="unsafe mutation",
            )

    def test_runtime_registry_rejects_two_roots_for_one_robot(self) -> None:
        first_ledger = SupervisorLedger(f"{self._tmp.name}/runtime-one.sqlite3")
        second_ledger = SupervisorLedger(f"{self._tmp.name}/runtime-two.sqlite3")
        try:
            first = RobotRuntime(
                robot_id="same",
                adapter=self.adapter,
                ledger=first_ledger,
                dispatcher=self.dispatcher,
            )
            second = RobotRuntime(
                robot_id="same",
                adapter=self.adapter,
                ledger=second_ledger,
                dispatcher=self.dispatcher,
            )
            registry = RobotRuntimeRegistry()
            registry.register(first)
            with self.assertRaisesRegex(ValueError, "already has"):
                registry.register(second)
        finally:
            first_ledger.close()
            second_ledger.close()


if __name__ == "__main__":
    unittest.main()
