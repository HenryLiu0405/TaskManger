from __future__ import annotations

import threading
import time
import unittest

from phi_robot.skills import (
    CancellationToken,
    SkillContext,
    SkillOutcome,
    SkillRequest,
)
from phi_robot.skills.catalog import build_skill_dispatcher

from tests.offline.fakes import ScriptedRobotStub


def request_for(
    skill: str,
    args: dict,
    key: str,
    *,
    source: str = "test",
    robot_id: str = "robot-1",
    token: CancellationToken | None = None,
    deadline_monotonic: float | None = None,
) -> SkillRequest:
    return SkillRequest(
        skill_name=skill,
        version="1.0",
        args=args,
        context=SkillContext(
            robot_id=robot_id,
            source=source,
            request_id=f"request-{key}",
            mission_id="mission-test",
            goal_id="goal-test",
            step_id=f"step-{key}",
            idempotency_key=key,
            cancel_token=token or CancellationToken(),
            deadline_monotonic=deadline_monotonic,
        ),
    )


MOVE_A = {
    "target": {"x": 1.0, "y": 2.0, "z": 0.0, "theta": 0.0},
    "timeout_s": 2.0,
}
MOVE_B = {
    "target": {"x": 2.0, "y": 3.0, "z": 0.0, "theta": 0.0},
    "timeout_s": 2.0,
}


class SkillDispatcherTests(unittest.TestCase):
    def test_same_idempotency_key_dispatches_backend_once(self) -> None:
        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        first = dispatcher.execute(request_for("move_to", MOVE_A, "same"))
        second = dispatcher.execute(request_for("move_to", MOVE_A, "same"))
        self.assertEqual(first.outcome, SkillOutcome.SUCCEEDED)
        self.assertEqual(second.invocation_id, first.invocation_id)
        self.assertEqual(adapter.calls["move_to"], 1)

    def test_same_key_different_parameters_is_conflict(self) -> None:
        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        first = dispatcher.execute(request_for("move_to", MOVE_A, "conflict"))
        conflict = dispatcher.execute(request_for("move_to", MOVE_B, "conflict"))
        self.assertEqual(first.outcome, SkillOutcome.SUCCEEDED)
        self.assertEqual(conflict.outcome, SkillOutcome.REJECTED)
        self.assertEqual(conflict.error.code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(adapter.calls["move_to"], 1)

    def test_precondition_rejection_does_not_poison_idempotency_key(self) -> None:
        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        args = {
            "target": {"x": 1.0, "y": 2.0, "z": 0.0, "theta": 0.0},
            "timeout_s": 1.0,
        }
        first = dispatcher.execute(request_for("place", args, "precondition-key"))
        self.assertEqual(first.outcome, SkillOutcome.REJECTED)
        self.assertEqual(first.error.code, "PRECONDITION_FAILED")
        self.assertEqual(adapter.calls["place"], 0)

        adapter.holding = "box-00"
        second = dispatcher.execute(request_for("place", args, "precondition-key"))
        self.assertEqual(second.outcome, SkillOutcome.SUCCEEDED)
        self.assertNotEqual(second.invocation_id, first.invocation_id)
        self.assertEqual(adapter.calls["place"], 1)

    def test_writer_conflict_is_rejected_and_query_can_run(self) -> None:
        adapter = ScriptedRobotStub(block_tool="move_to")
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        first_id = dispatcher.dispatch(request_for("move_to", MOVE_A, "writer-a", source="mission"))
        self.assertTrue(adapter.block_started.wait(timeout=1.0))

        busy = dispatcher.execute(request_for("move_to", MOVE_B, "writer-b", source="step_debug"))
        self.assertEqual(busy.outcome, SkillOutcome.REJECTED)
        self.assertEqual(busy.error.code, "RESOURCE_BUSY")

        query = dispatcher.execute(request_for("get_pose", {}, "query", source="dev_console"))
        self.assertEqual(query.outcome, SkillOutcome.SUCCEEDED)
        self.assertEqual(adapter.max_active_mutating_calls, 1)
        self.assertEqual(adapter.calls["move_to"], 1)

        adapter.block_release.set()
        self.assertEqual(dispatcher.wait(first_id, timeout=1.0).outcome, SkillOutcome.SUCCEEDED)

    def test_different_robots_have_independent_writer_locks_and_dedupe_scopes(self) -> None:
        adapter = ScriptedRobotStub(block_tool="move_to")
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        first_id = dispatcher.dispatch(request_for(
            "move_to", MOVE_A, "shared-key", robot_id="robot-a"
        ))
        second_id = dispatcher.dispatch(request_for(
            "move_to", MOVE_A, "shared-key", robot_id="robot-b"
        ))
        self.assertNotEqual(first_id, second_id)
        self.assertTrue(adapter.two_mutations_started.wait(timeout=1.0))
        self.assertEqual(adapter.calls["move_to"], 2)
        self.assertEqual(adapter.max_active_mutating_calls, 2)
        adapter.block_release.set()
        self.assertEqual(dispatcher.wait(first_id, timeout=1.0).outcome, SkillOutcome.SUCCEEDED)
        self.assertEqual(dispatcher.wait(second_id, timeout=1.0).outcome, SkillOutcome.SUCCEEDED)

    def test_concurrent_duplicate_attaches_to_running_invocation(self) -> None:
        adapter = ScriptedRobotStub(block_tool="move_to")
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        request = request_for("move_to", MOVE_A, "duplicate-running")
        first_id = dispatcher.dispatch(request)
        self.assertTrue(adapter.block_started.wait(timeout=1.0))
        second_id = dispatcher.dispatch(request_for("move_to", MOVE_A, "duplicate-running"))
        self.assertEqual(second_id, first_id)
        self.assertEqual(adapter.calls["move_to"], 1)
        adapter.block_release.set()
        self.assertEqual(dispatcher.wait(first_id, timeout=1.0).outcome, SkillOutcome.SUCCEEDED)

    def test_timeout_is_unknown_and_lock_stays_draining(self) -> None:
        adapter = ScriptedRobotStub(block_tool="move_to")
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        request = request_for("move_to", MOVE_A, "timeout")
        result = dispatcher.execute(request, timeout=0.02)
        self.assertTrue(adapter.block_started.is_set())
        self.assertEqual(result.outcome, SkillOutcome.UNKNOWN)
        self.assertEqual(result.error.code, "UNKNOWN_OUTCOME")
        invocation_id = result.invocation_id
        self.assertEqual(dispatcher.status(invocation_id)["state"], "draining")

        duplicate = dispatcher.execute(
            request_for("move_to", MOVE_A, "timeout"), timeout=0.01
        )
        self.assertEqual(duplicate.outcome, SkillOutcome.UNKNOWN)
        busy = dispatcher.execute(request_for("move_to", MOVE_B, "after-timeout"))
        self.assertEqual(busy.error.code, "RESOURCE_BUSY")
        self.assertEqual(adapter.calls["move_to"], 1)

        adapter.block_release.set()
        terminal = dispatcher.wait(invocation_id, timeout=1.0)
        self.assertEqual(terminal.outcome, SkillOutcome.SUCCEEDED)
        attached = dispatcher.execute(request_for("move_to", MOVE_A, "timeout"))
        self.assertEqual(attached.invocation_id, invocation_id)
        self.assertEqual(adapter.calls["move_to"], 1)

    def test_cancel_before_dispatch_never_calls_backend(self) -> None:
        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        token = CancellationToken()
        token.cancel()
        result = dispatcher.execute(
            request_for("move_to", MOVE_A, "pre-cancel", token=token)
        )
        self.assertEqual(result.outcome, SkillOutcome.CANCELLED)
        self.assertEqual(result.error.code, "CANCELLED_BEFORE_DISPATCH")
        self.assertEqual(adapter.trace, [])

    def test_expired_deadline_is_rejected_before_backend(self) -> None:
        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        result = dispatcher.execute(request_for(
            "move_to", MOVE_A, "expired-deadline",
            deadline_monotonic=time.monotonic() - 1.0,
        ))
        self.assertEqual(result.outcome, SkillOutcome.REJECTED)
        self.assertEqual(result.error.code, "DEADLINE_EXCEEDED_BEFORE_DISPATCH")
        self.assertEqual(adapter.trace, [])

        wall_clock_request = request_for("move_to", MOVE_A, "expired-wall-clock")
        wall_clock_request = SkillRequest(
            skill_name=wall_clock_request.skill_name,
            version=wall_clock_request.version,
            args=wall_clock_request.args,
            context=SkillContext(
                robot_id="robot-1", source="test", request_id="wall-clock",
                mission_id="mission-test", goal_id="goal-test", step_id="wall-clock",
                idempotency_key="expired-wall-clock", deadline=time.time() - 1.0,
            ),
        )
        wall_result = dispatcher.execute(wall_clock_request)
        self.assertEqual(wall_result.error.code, "DEADLINE_EXCEEDED_BEFORE_DISPATCH")
        self.assertEqual(adapter.trace, [])

    def test_cancel_during_non_interruptible_call_is_not_false_confirmation(self) -> None:
        adapter = ScriptedRobotStub(block_tool="move_to")
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        invocation_id = dispatcher.dispatch(request_for("move_to", MOVE_A, "runtime-cancel"))
        self.assertTrue(adapter.block_started.wait(timeout=1.0))
        receipt = dispatcher.cancel(invocation_id)
        self.assertTrue(receipt.accepted)
        self.assertFalse(receipt.confirmed)
        self.assertEqual(receipt.state.value, "cancel_requested")

        busy = dispatcher.execute(request_for("pick", {
            "object_id": "box-00", "timeout_s": 1.0,
        }, "cancel-successor"))
        self.assertEqual(busy.error.code, "RESOURCE_BUSY")
        adapter.block_release.set()
        terminal = dispatcher.wait(invocation_id, timeout=1.0)
        self.assertEqual(terminal.outcome, SkillOutcome.SUCCEEDED)
        after = dispatcher.cancel(invocation_id)
        self.assertFalse(after.accepted)
        self.assertFalse(after.confirmed)

    def test_transport_failure_is_unknown(self) -> None:
        adapter = ScriptedRobotStub(failures={
            "move_to": [{"error_code": "SERVICE_UNAVAILABLE", "message": "lost"}],
        })
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        result = dispatcher.execute(request_for("move_to", MOVE_A, "transport"))
        self.assertEqual(result.outcome, SkillOutcome.UNKNOWN)
        self.assertEqual(result.error.category.value, "transport")

    def test_adapter_exception_is_unknown_for_mutation_and_releases_the_writer(self) -> None:
        class ExplodingAdapter(ScriptedRobotStub):
            def execute(self, tool, args, *, request_id, goal_id, step_id):
                self._record(tool, args)
                raise RuntimeError("adapter exploded")

        adapter = ExplodingAdapter()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        result = dispatcher.execute(request_for("move_to", MOVE_A, "explode"))
        self.assertEqual(result.outcome, SkillOutcome.UNKNOWN)
        self.assertEqual(result.error.code, "ADAPTER_EXCEPTION_UNKNOWN")
        self.assertEqual(result.error.category.value, "unknown")

        # The worker returned, so a new writer may dispatch instead of leaving
        # a leaked lock. It fails for the same deliberate adapter exception.
        second = dispatcher.execute(request_for("move_to", MOVE_B, "explode-2"))
        self.assertEqual(second.error.code, "ADAPTER_EXCEPTION_UNKNOWN")
        self.assertEqual(adapter.names, ["move_to", "move_to"])

        query = dispatcher.execute(request_for("get_pose", {}, "explode-query"))
        self.assertEqual(query.outcome, SkillOutcome.FAILED)
        self.assertEqual(query.error.code, "INTERNAL_ERROR")

    def test_verification_failure_overrides_backend_ok(self) -> None:
        class WrongPoseAdapter(ScriptedRobotStub):
            def execute(self, tool, args, *, request_id, goal_id, step_id):
                if tool == "move_to":
                    self._record("move_to", args)
                    return {
                        "status": "ok",
                        "message": "claimed arrival",
                        "state": {
                            "robot_pose": {"x": 99, "y": 99, "z": 0, "theta": 0},
                            "holding": None,
                        },
                        "metrics": {},
                    }
                return super().execute(
                    tool, args, request_id=request_id, goal_id=goal_id, step_id=step_id
                )

        adapter = WrongPoseAdapter()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        result = dispatcher.execute(request_for("move_to", MOVE_A, "verify-fail"))
        self.assertEqual(result.outcome, SkillOutcome.FAILED)
        self.assertEqual(result.error.code, "VERIFICATION_FAILED")
        self.assertEqual(result.error.category.value, "verification")

    def test_ros_unreliable_queries_are_explicitly_unsupported(self) -> None:
        class RosAcceptanceAdapter(ScriptedRobotStub):
            pass

        adapter = RosAcceptanceAdapter()
        dispatcher = build_skill_dispatcher(
            adapter, mode="skills", audit_enabled=False
        )
        result = dispatcher.execute(request_for("get_pose", {}, "ros-query"))
        self.assertEqual(result.outcome, SkillOutcome.REJECTED)
        self.assertEqual(result.error.code, "UNSUPPORTED_CAPABILITY")
        self.assertEqual(adapter.trace, [])


if __name__ == "__main__":
    unittest.main()
