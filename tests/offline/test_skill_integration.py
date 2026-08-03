from __future__ import annotations

import asyncio
import copy
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from phi_robot.adapters.unitree_sim import UnitreeSimBackend
from phi_robot.dev_console import DevConsoleController
from phi_robot.fake_robot_service import DEFAULT_FAKE_SPEC, FakeRobotService
from phi_robot.mission_runner import MissionExecutionHook, MissionRunner
from phi_robot.mission_service import MissionService
from phi_robot.step_debug import ST_STEP_READY, StepDebugController
from phi_robot.store import MissionStore
from phi_robot.skills.catalog import build_skill_dispatcher

from tests.offline.fakes import ScriptedRobotStub


class CompositeSkillIntegrationTests(unittest.TestCase):
    def test_scripted_stub_golden_trace(self) -> None:
        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        calls = [
            ("move_to", {
                "target": {"x": -2.0, "y": 1.5, "z": 0.0, "theta": 0.0},
                "timeout_s": 2,
            }, {}),
            ("pick", {"object_id": "box-00", "timeout_s": 2}, {
                "slot_nav2_x": -2.0, "slot_nav2_y": 1.5,
            }),
            ("move_to", {
                "target": {"x": 1.0, "y": 0.5, "z": 0.0, "theta": 0.0},
                "timeout_s": 2,
            }, {}),
            ("place", {
                "target": {"x": 1.0, "y": 0.5, "z": 0.0, "theta": 0.0},
                "timeout_s": 2,
            }, {}),
        ]
        for index, (tool, args, annotations) in enumerate(calls):
            result = dispatcher.execute_legacy(
                tool,
                args,
                request_id="golden",
                goal_id="goal",
                step_id=f"step-{index}",
                annotations=annotations,
                idempotency_key=f"golden-{index}",
            )
            self.assertEqual(result["status"], "ok", result)

        self.assertEqual(adapter.names, [
            "move_to",
            "select_target",
            "fresh_pose",
            "pick",
            "clear_target",
            "move_to",
            "place",
        ])
        self.assertFalse(adapter.select_target_active)

    def test_composite_pick_clears_target_when_selection_has_no_match(self) -> None:
        class NoMatchStub(ScriptedRobotStub):
            def select_target_public(self, *args, **kwargs):
                result = super().select_target_public(*args, **kwargs)
                if kwargs.get("select", args[0] if args else False):
                    result["matched_object_id"] = -1
                return result

        adapter = NoMatchStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        result = dispatcher.execute_legacy(
            "pick", {"object_id": "box-00", "timeout_s": 1},
            request_id="no-match", goal_id="goal", step_id="pick",
            annotations={"slot_nav2_x": 0.0, "slot_nav2_y": 0.0},
            idempotency_key="no-match-pick",
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "FP_LOCK_FAILED")
        self.assertEqual(adapter.names, ["select_target", "clear_target"])
        self.assertFalse(adapter.select_target_active)
        self.assertEqual(adapter.calls["pick"], 0)

    def test_missing_target_selection_capability_is_explicitly_unsupported(self) -> None:
        class MissingSelectionStub(ScriptedRobotStub):
            def select_target_public(self, *args, **kwargs):
                if kwargs.get("select", args[0] if args else False):
                    self._record("select_target")
                    return None
                self._record("clear_target")
                return None

        adapter = MissingSelectionStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        result = dispatcher.execute_legacy(
            "pick", {"object_id": "box-00", "timeout_s": 1},
            request_id="missing-fp", goal_id="goal", step_id="pick",
            annotations={"slot_nav2_x": 0.0, "slot_nav2_y": 0.0},
            idempotency_key="missing-fp",
        )
        self.assertEqual(result["outcome"], "rejected")
        self.assertEqual(result["error_code"], "UNSUPPORTED_CAPABILITY")
        self.assertEqual(adapter.names, ["select_target", "clear_target"])
        self.assertEqual(adapter.calls["pick"], 0)

    def test_composite_pick_requires_fresh_pose_and_cleans_up(self) -> None:
        class StalePoseStub(ScriptedRobotStub):
            def wait_fp_pose_ready(self, min_wait_s=0.0, timeout_s=1.0):
                self._record("fresh_pose", {"min_wait_s": min_wait_s, "timeout_s": timeout_s})
                return False

        adapter = StalePoseStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        result = dispatcher.execute_legacy(
            "pick", {"object_id": "box-00", "timeout_s": 1},
            request_id="stale", goal_id="goal", step_id="pick",
            annotations={"slot_nav2_x": 0.0, "slot_nav2_y": 0.0},
            idempotency_key="stale-pick",
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "POSE_NOT_READY")
        self.assertEqual(adapter.names, ["select_target", "fresh_pose", "clear_target"])
        self.assertEqual(adapter.calls["pick"], 0)
        self.assertFalse(adapter.select_target_active)

    def test_pick_success_does_not_hide_unconfirmed_target_cleanup(self) -> None:
        class CleanupFailureStub(ScriptedRobotStub):
            def select_target_public(
                self, select, pick_x=0.0, pick_y=0.0,
                material_points_xy=None, step_id="",
            ):
                if select:
                    return super().select_target_public(
                        select, pick_x, pick_y, material_points_xy, step_id
                    )
                self._record("clear_target", step_id=step_id)
                return {"success": False, "message": "cleanup unavailable"}

        adapter = CleanupFailureStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        result = dispatcher.execute_legacy(
            "pick", {"object_id": "box-00", "timeout_s": 1},
            request_id="cleanup", goal_id="goal", step_id="pick",
            annotations={"slot_nav2_x": 0.0, "slot_nav2_y": 0.0},
            idempotency_key="cleanup",
        )
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["error_code"], "TARGET_CLEANUP_FAILED")
        self.assertEqual(adapter.holding, "box-00")
        self.assertTrue(adapter.select_target_active)
        self.assertEqual(adapter.names, [
            "select_target", "fresh_pose", "pick", "clear_target",
        ])

    def test_pick_backend_receives_stable_idempotency_key(self) -> None:
        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        for _ in range(2):
            result = dispatcher.execute_legacy(
                "pick", {"object_id": "box-00", "timeout_s": 1},
                request_id="logical-request", goal_id="goal", step_id="pick",
                annotations={"slot_nav2_x": 0.0, "slot_nav2_y": 0.0},
                idempotency_key="stable-carry-request-id",
            )
            self.assertEqual(result["status"], "ok")
        pick_calls = [entry for entry in adapter.trace if entry["name"] == "pick"]
        self.assertEqual(len(pick_calls), 1)
        self.assertEqual(pick_calls[0]["request_id"], "stable-carry-request-id")

    def test_navigation_terminal_and_unknown_failures_are_distinguished(self) -> None:
        cases = (
            ("PATH_PLAN_FAILED", "failed", "execution"),
            ("SERVICE_UNAVAILABLE", "unknown", "transport"),
            ("NAV_TIMEOUT", "unknown", "timeout"),
        )
        for index, (code, outcome, category) in enumerate(cases):
            with self.subTest(code=code):
                adapter = ScriptedRobotStub(failures={
                    "move_to": [{"error_code": code, "message": code}],
                })
                dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
                result = dispatcher.execute_legacy(
                    "move_to",
                    {"target": {"x": 1, "y": 0, "z": 0, "theta": 0}, "timeout_s": 1},
                    request_id=f"nav-{index}", goal_id="goal", step_id="move",
                    idempotency_key=f"nav-{index}",
                )
                self.assertEqual(result["outcome"], outcome)
                self.assertEqual(result["error_category"], category)
                self.assertEqual(adapter.calls["move_to"], 1)

    def test_carry_rejection_and_lost_response_always_clear_target(self) -> None:
        cases = (
            ("CARRY_REJECTED", "failed"),
            ("SERVICE_UNAVAILABLE", "unknown"),
        )
        for index, (code, outcome) in enumerate(cases):
            with self.subTest(code=code):
                adapter = ScriptedRobotStub(failures={
                    "pick": [{"error_code": code, "message": code}],
                })
                dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
                result = dispatcher.execute_legacy(
                    "pick", {"object_id": "box-00", "timeout_s": 1},
                    request_id=f"carry-{index}", goal_id="goal", step_id="pick",
                    annotations={"slot_nav2_x": 0.0, "slot_nav2_y": 0.0},
                    idempotency_key=f"carry-{index}",
                )
                self.assertEqual(result["outcome"], outcome)
                self.assertEqual(adapter.names, [
                    "select_target", "fresh_pose", "pick", "clear_target",
                ])
                self.assertFalse(adapter.select_target_active)

    def test_real_fake_service_runs_same_composite_contract(self) -> None:
        service = FakeRobotService.from_spec(copy.deepcopy(DEFAULT_FAKE_SPEC))
        dispatcher = build_skill_dispatcher(service, audit_enabled=False)
        sequence = [
            ("move_to", {
                "target": {"x": 0.5, "y": 3.5, "z": 0.0, "theta": 0.0},
                "timeout_s": 2,
            }),
            ("pick", {"object_id": "box-00", "timeout_s": 2}),
            ("move_to", {
                "target": {"x": 2.0, "y": 0.5, "z": 0.0, "theta": 0.0},
                "timeout_s": 2,
            }),
            ("place", {
                "target": {"x": 2.0, "y": 0.5, "z": 0.0, "theta": 0.0},
                "timeout_s": 2,
            }),
        ]
        for index, (tool, args) in enumerate(sequence):
            result = dispatcher.execute_legacy(
                tool,
                args,
                request_id="fake-flow",
                goal_id="goal",
                step_id=f"step-{index}",
                idempotency_key=f"fake-flow-{index}",
            )
            self.assertEqual(result["status"], "ok", result)
            self.assertNotEqual(result["verification"]["status"], "failed")
        self.assertEqual([item["tool"] for item in service.history], [
            "move_to", "pick", "move_to", "place",
        ])
        self.assertIsNone(service.snapshot()["holding"])

    def test_unitree_sim_runs_identical_contract(self) -> None:
        adapter = UnitreeSimBackend(spec=copy.deepcopy(DEFAULT_FAKE_SPEC))
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        result = dispatcher.execute_legacy(
            "move_to",
            {"target": {"x": 0.5, "y": 3.5, "z": 0, "theta": 0}, "timeout_s": 2},
            request_id="sim",
            goal_id="goal",
            step_id="move",
            idempotency_key="sim-move",
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["verification"]["status"], "passed")


class ControllerCharacterizationTests(unittest.TestCase):
    def make_controller(self, adapter: ScriptedRobotStub, *, mode: str = "skills") -> StepDebugController:
        dispatcher = build_skill_dispatcher(
            adapter, mode=mode, audit_enabled=False
        )
        return StepDebugController(adapter, skill_dispatcher=dispatcher)

    def test_home_run_loads_plan_without_adapter_call(self) -> None:
        adapter = ScriptedRobotStub()
        controller = self.make_controller(adapter)
        result = controller.load_plan(["se"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["total_steps"], 4)
        self.assertEqual(adapter.trace, [])
        self.assertEqual(result["raw_steps"][0]["skill_version"], "1.0")
        self.assertIn("annotations", result["raw_steps"][0])

    def test_phase1_autonomous_drop_recovery_is_disabled(self) -> None:
        adapter = ScriptedRobotStub()
        controller = self.make_controller(adapter)
        result = controller.auto_drop_recovery()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "UNSUPPORTED_CAPABILITY")
        self.assertEqual(adapter.trace, [])

    def test_each_single_step_click_dispatches_one_core_action(self) -> None:
        adapter = ScriptedRobotStub()
        controller = self.make_controller(adapter)
        controller.load_plan(["se"])
        result = controller.execute_current_step()
        self.assertTrue(result["ok"])
        controller._exec_thread.join(timeout=1.0)
        self.assertFalse(controller._exec_thread.is_alive())
        self.assertEqual(adapter.calls["move_to"], 1)
        self.assertEqual(adapter.calls["pick"], 0)

    def test_plan_reload_during_block_is_rejected_without_state_corruption(self) -> None:
        adapter = ScriptedRobotStub(block_tool="move_to")
        controller = self.make_controller(adapter)
        controller.load_plan(["se"])
        controller.execute_current_step()
        self.assertTrue(adapter.block_started.wait(timeout=1.0))
        rejected = controller.load_plan(["nw"])
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["error_code"], "RESOURCE_BUSY")
        adapter.block_release.set()
        controller._exec_thread.join(timeout=1.0)
        state = controller.get_state()
        self.assertEqual(state["current_index"], 1)
        self.assertEqual(adapter.calls["move_to"], 1)

    def test_abort_during_block_does_not_claim_stop_or_start_reset(self) -> None:
        adapter = ScriptedRobotStub(block_tool="move_to")
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        controller = StepDebugController(
            adapter,
            skill_dispatcher=dispatcher,
            abort_join_timeout_s=0.01,
        )
        controller.load_plan(["se"])
        controller.execute_current_step()
        self.assertTrue(adapter.block_started.wait(timeout=1.0))
        old_worker = controller._exec_thread

        aborted = controller.abort()
        self.assertTrue(aborted["backend_draining"])
        self.assertFalse(aborted["physical_stop_confirmed"])
        self.assertEqual(adapter.calls["move_to"], 1)
        self.assertEqual(adapter.reset_count, 0)

        adapter.block_release.set()
        old_worker.join(timeout=1.0)
        self.assertFalse(old_worker.is_alive())
        self.assertEqual(controller.get_state()["state"], "idle")

    def test_reset_to_stock_during_block_cannot_start_a_second_action(self) -> None:
        adapter = ScriptedRobotStub(block_tool="move_to")
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        controller = StepDebugController(adapter, skill_dispatcher=dispatcher)
        controller.load_plan(["se"])
        controller.execute_current_step()
        self.assertTrue(adapter.block_started.wait(timeout=1.0))
        old_worker = controller._exec_thread

        rewound = controller.reset_to_stock_point()
        self.assertTrue(rewound["ok"])
        self.assertEqual(controller.get_state()["state"], "step_ready")
        self.assertTrue(controller.execute_current_step()["ok"])
        controller._exec_thread.join(timeout=1.0)
        self.assertEqual(controller.get_state()["plan"][0]["error_code"], "RESOURCE_BUSY")
        self.assertEqual(adapter.calls["move_to"], 1)
        self.assertEqual(adapter.max_active_mutating_calls, 1)

        adapter.block_release.set()
        old_worker.join(timeout=1.0)
        self.assertFalse(old_worker.is_alive())

    def test_legacy_manual_pick_is_rejected_without_target_lock(self) -> None:
        adapter = ScriptedRobotStub()
        controller = self.make_controller(adapter, mode="legacy-direct")
        controller.load_plan(["se"])
        controller._current_index = 1
        controller._state = ST_STEP_READY
        before = len(adapter.trace)
        result = controller.execute_current_step()
        self.assertFalse(result["ok"])
        self.assertIn("锁定单目标", result["message"])
        self.assertEqual(len(adapter.trace), before)

    def test_failed_manual_target_selection_can_be_retried_without_stale_dedupe(self) -> None:
        class FirstNoMatchStub(ScriptedRobotStub):
            def __init__(self):
                super().__init__()
                self.selection_attempts = 0

            def select_target_public(self, *args, **kwargs):
                result = super().select_target_public(*args, **kwargs)
                selecting = kwargs.get("select", args[0] if args else False)
                if selecting:
                    self.selection_attempts += 1
                    if self.selection_attempts == 1:
                        result["matched_object_id"] = -1
                return result

        adapter = FirstNoMatchStub()
        controller = self.make_controller(adapter, mode="legacy-direct")
        controller.load_plan(["se"])
        first = controller.toggle_select_target()
        second = controller.toggle_select_target()
        self.assertFalse(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(adapter.selection_attempts, 2)
        self.assertEqual(adapter.names, ["select_target", "clear_target", "select_target"])

    def test_step_debug_auto_uses_golden_order(self) -> None:
        adapter = ScriptedRobotStub()
        controller = self.make_controller(adapter)
        controller.load_plan(["se"])
        started = controller.auto_run()
        self.assertTrue(started["ok"])
        controller._auto_thread.join(timeout=2.0)
        self.assertFalse(controller._auto_thread.is_alive())
        self.assertEqual(adapter.names, [
            "move_to", "select_target", "fresh_pose", "pick", "clear_target",
            "move_to", "place",
        ])
        self.assertEqual(controller.get_state()["state"], "idle")

    def test_step_debug_auto_stops_after_first_failure(self) -> None:
        adapter = ScriptedRobotStub(failures={
            "move_to": [{"error_code": "PATH_PLAN_FAILED", "message": "rejected"}],
        })
        controller = self.make_controller(adapter)
        controller.load_plan(["se"])
        controller.auto_run()
        controller._auto_thread.join(timeout=2.0)
        self.assertEqual(adapter.names, ["move_to"])
        self.assertEqual(controller.get_state()["state"], "step_failed")

    def test_step_debug_unknown_outcome_requires_reconciliation_before_retry(self) -> None:
        adapter = ScriptedRobotStub(failures={
            "move_to": [{"error_code": "SERVICE_UNAVAILABLE", "message": "response lost"}],
        })
        controller = self.make_controller(adapter)
        controller.load_plan(["se"])
        controller.execute_current_step()
        controller._exec_thread.join(timeout=1.0)
        state = controller.get_state()
        self.assertEqual(state["state"], "step_failed")
        self.assertEqual(state["plan"][0]["outcome"], "unknown")
        retry = controller.retry_step()
        self.assertFalse(retry["ok"])
        self.assertEqual(retry["error_code"], "RECONCILIATION_REQUIRED")
        skipped = controller.skip_step()
        forced = controller.force_skip()
        self.assertEqual(skipped["error_code"], "RECONCILIATION_REQUIRED")
        self.assertEqual(forced["error_code"], "RECONCILIATION_REQUIRED")
        self.assertEqual(adapter.calls["move_to"], 1)

    def test_step_debug_confirmed_failure_retry_reuses_the_logical_step_key(self) -> None:
        adapter = ScriptedRobotStub(failures={
            "move_to": [{"error_code": "PATH_PLAN_FAILED", "message": "rejected"}],
        })
        controller = self.make_controller(adapter)
        controller.load_plan(["se"])
        self.assertTrue(controller.execute_current_step()["ok"])
        controller._exec_thread.join(timeout=1.0)
        self.assertFalse(controller._exec_thread.is_alive())
        self.assertEqual(controller.get_state()["state"], "step_failed")
        first_invocation = controller.get_state()["plan"][0]["invocation_id"]

        self.assertTrue(controller.retry_step()["ok"])
        controller._exec_thread.join(timeout=1.0)
        self.assertFalse(controller._exec_thread.is_alive())
        state = controller.get_state()
        self.assertEqual(state["state"], "step_failed")
        self.assertEqual(state["plan"][0]["invocation_id"], first_invocation)
        self.assertEqual(adapter.calls["move_to"], 1)

    def test_mission_runner_is_fail_stop_and_persists_result(self) -> None:
        adapter = ScriptedRobotStub(failures={
            "move_to": [{"error_code": "PATH_PLAN_FAILED", "message": "rejected"}],
        })
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        with tempfile.TemporaryDirectory() as directory:
            service = MissionService(MissionStore(data_dir=directory))
            mission_id = service.submit(
                request_id="runner-fail",
                scene_id="scene",
                goal_id="goal",
                scene_version="scene-v1",
                stock_layout_version="stock-v1",
                destination_order=["se"],
            )
            record = asyncio.run(MissionRunner(
                service, adapter, skill_dispatcher=dispatcher
            ).run(mission_id))
        self.assertEqual(record.status, "failed")
        self.assertEqual(adapter.names, ["move_to"])
        self.assertEqual(record.plan[0].status, "failed")
        self.assertIsNotNone(record.plan[0].result)
        self.assertEqual(record.plan[0].result.error_code, "PATH_PLAN_FAILED")
        self.assertTrue(record.plan[0].invocation_id)
        self.assertEqual(record.plan[0].attempt, 1)

    def test_mission_runner_ignores_legacy_retry_policy_for_physical_action(self) -> None:
        class RetryHook(MissionExecutionHook):
            def apply_replan_policy(self, record, result, events=None):
                return ("retry", None)

        adapter = ScriptedRobotStub(failures={
            "move_to": [{"error_code": "PATH_PLAN_FAILED", "message": "rejected"}],
        })
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        with tempfile.TemporaryDirectory() as directory:
            service = MissionService(MissionStore(data_dir=directory))
            mission_id = service.submit(
                request_id="runner-no-retry", scene_id="scene", goal_id="goal",
                scene_version="scene-v1", stock_layout_version="stock-v1",
                destination_order=["se"], options={"max_retries": 9},
            )
            record = asyncio.run(MissionRunner(
                service, adapter, hook=RetryHook(), skill_dispatcher=dispatcher
            ).run(mission_id))
        self.assertEqual(record.status, "failed")
        self.assertEqual(adapter.calls["move_to"], 1)

    def test_mission_runner_persists_unknown_outcome_and_stops(self) -> None:
        adapter = ScriptedRobotStub(failures={
            "move_to": [{"error_code": "SERVICE_UNAVAILABLE", "message": "response lost"}],
        })
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        with tempfile.TemporaryDirectory() as directory:
            service = MissionService(MissionStore(data_dir=directory))
            mission_id = service.submit(
                request_id="runner-unknown", scene_id="scene", goal_id="goal",
                scene_version="scene-v1", stock_layout_version="stock-v1",
                destination_order=["se"],
            )
            record = asyncio.run(MissionRunner(
                service, adapter, skill_dispatcher=dispatcher
            ).run(mission_id))
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.plan[0].status, "failed")
        self.assertEqual(record.plan[0].result.outcome, "unknown")
        self.assertEqual(adapter.names, ["move_to"])

    def test_mission_reset_creates_new_epoch_without_reusing_old_side_effects(self) -> None:
        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        with tempfile.TemporaryDirectory() as directory:
            service = MissionService(MissionStore(data_dir=directory))
            mission_id = service.submit(
                request_id="runner-reset", scene_id="scene", goal_id="goal",
                scene_version="scene-v1", stock_layout_version="stock-v1",
                destination_order=["se"],
            )
            first = asyncio.run(MissionRunner(
                service, adapter, skill_dispatcher=dispatcher
            ).run(mission_id))
            self.assertEqual(first.status, "completed")
            self.assertEqual(first.execution_epoch, 1)
            first_trace_count = len(adapter.trace)

            service.reset(mission_id)
            reset_record = service.get_mission(mission_id)
            self.assertEqual(reset_record.execution_epoch, 2)
            self.assertTrue(all(step.status == "pending" for step in reset_record.plan))
            self.assertTrue(all(step.invocation_id is None for step in reset_record.plan))

            second = asyncio.run(MissionRunner(
                service, adapter, skill_dispatcher=dispatcher
            ).run(mission_id))
        self.assertEqual(second.status, "completed")
        self.assertEqual(second.execution_epoch, 2)
        self.assertEqual(len(adapter.trace), first_trace_count * 2)

    def test_step_debug_and_manual_console_share_writer_lock(self) -> None:
        adapter = ScriptedRobotStub(block_tool="move_to")
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        step_debug = StepDebugController(adapter, skill_dispatcher=dispatcher)
        console = DevConsoleController(adapter, skill_dispatcher=dispatcher)
        step_debug.load_plan(["se"])
        step_debug.execute_current_step()
        self.assertTrue(adapter.block_started.wait(timeout=1.0))

        with patch("phi_robot.dev_console.audit_logger.log_action_start"), \
             patch("phi_robot.dev_console.audit_logger.log_action_end"):
            submitted = console.manual_next({"x": 1.0, "y": 1.0})
            self.assertTrue(submitted["ok"])
            console._exec_thread.join(timeout=1.0)
        self.assertEqual(adapter.calls["move_to"], 1)
        self.assertEqual(adapter.max_active_mutating_calls, 1)
        self.assertTrue(any("RESOURCE_BUSY" in item["detail"] for item in console.get_logs()))

        adapter.block_release.set()
        step_debug._exec_thread.join(timeout=1.0)

    def test_mission_runner_and_step_debug_share_writer_lock(self) -> None:
        adapter = ScriptedRobotStub(block_tool="move_to")
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        with tempfile.TemporaryDirectory() as directory:
            service = MissionService(MissionStore(data_dir=directory))
            mission_id = service.submit(
                request_id="mission-lock", scene_id="scene", goal_id="goal",
                scene_version="scene-v1", stock_layout_version="stock-v1",
                destination_order=["se"],
            )
            runner = MissionRunner(service, adapter, skill_dispatcher=dispatcher)
            runner_thread = threading.Thread(
                target=lambda: asyncio.run(runner.run(mission_id)), daemon=True
            )
            runner_thread.start()
            self.assertTrue(adapter.block_started.wait(timeout=1.0))

            step_debug = StepDebugController(adapter, skill_dispatcher=dispatcher)
            step_debug.load_plan(["nw"])
            step_debug.execute_current_step()
            step_debug._exec_thread.join(timeout=1.0)
            self.assertEqual(step_debug.get_state()["state"], "step_failed")
            self.assertEqual(step_debug.get_state()["plan"][0]["error_code"], "RESOURCE_BUSY")
            self.assertEqual(adapter.calls["move_to"], 1)

            adapter.block_release.set()
            runner_thread.join(timeout=2.0)
            self.assertFalse(runner_thread.is_alive())

    def test_two_missions_share_one_writer_and_busy_mission_fails_stop(self) -> None:
        adapter = ScriptedRobotStub(block_tool="move_to")
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        with tempfile.TemporaryDirectory() as directory:
            service = MissionService(MissionStore(data_dir=directory))
            first_id = service.submit(
                request_id="mission-one", scene_id="scene", goal_id="goal-one",
                scene_version="scene-v1", stock_layout_version="stock-v1",
                destination_order=["se"],
            )
            second_id = service.submit(
                request_id="mission-two", scene_id="scene", goal_id="goal-two",
                scene_version="scene-v1", stock_layout_version="stock-v1",
                destination_order=["e"],
            )

            first_thread = threading.Thread(target=lambda: asyncio.run(
                MissionRunner(service, adapter, skill_dispatcher=dispatcher).run(first_id)
            ))
            first_thread.start()
            self.assertTrue(adapter.block_started.wait(timeout=1.0))

            second_record = asyncio.run(
                MissionRunner(service, adapter, skill_dispatcher=dispatcher).run(second_id)
            )
            self.assertEqual(second_record.status, "failed")
            self.assertEqual(second_record.plan[0].result.error_code, "RESOURCE_BUSY")
            self.assertEqual(adapter.calls["move_to"], 1)
            self.assertEqual(adapter.max_active_mutating_calls, 1)

            adapter.block_release.set()
            first_thread.join(timeout=3.0)
            self.assertFalse(first_thread.is_alive())
            self.assertEqual(service.get_mission(first_id).status, "completed")

    def test_dev_console_manual_and_auto_modes_share_one_writer(self) -> None:
        adapter = ScriptedRobotStub(block_tool="move_to")
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        with tempfile.TemporaryDirectory() as directory:
            service = MissionService(MissionStore(data_dir=directory))
            console = DevConsoleController(
                adapter,
                mission_service=service,
                skill_dispatcher=dispatcher,
            )
            self.assertTrue(console.switch_mode("auto")["ok"])
            self.assertTrue(console.auto_start(["se"])["ok"])
            self.assertTrue(adapter.block_started.wait(timeout=1.0))

            self.assertTrue(console.switch_mode("manual")["ok"])
            with patch("phi_robot.dev_console.audit_logger.log_action_start"), \
                 patch("phi_robot.dev_console.audit_logger.log_action_end"):
                self.assertTrue(console.manual_next({"x": 1.0, "y": 1.0})["ok"])
                console._exec_thread.join(timeout=1.0)
            self.assertEqual(adapter.calls["move_to"], 1)
            self.assertEqual(adapter.max_active_mutating_calls, 1)
            self.assertTrue(any(
                "RESOURCE_BUSY" in item["detail"] for item in console.get_logs()
            ))

            adapter.block_release.set()
            console._auto_thread.join(timeout=3.0)
            self.assertFalse(console._auto_thread.is_alive())
            self.assertEqual(
                service.get_mission(console._auto_mission_id).status,
                "completed",
            )

    def test_manual_pause_is_local_gate_not_false_physical_stop_confirmation(self) -> None:
        adapter = ScriptedRobotStub(block_tool="move_to")
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        console = DevConsoleController(adapter, skill_dispatcher=dispatcher)
        with patch("phi_robot.dev_console.audit_logger.log_action_start"), \
             patch("phi_robot.dev_console.audit_logger.log_action_end"):
            console.manual_next({"x": 1.0, "y": 1.0})
            self.assertTrue(adapter.block_started.wait(timeout=1.0))
            paused = console.manual_pause()
            self.assertTrue(paused["ok"])
            self.assertTrue(paused["pause_requested"])
            self.assertFalse(paused["physical_stop_confirmed"])
            self.assertEqual(paused["control_result"]["error_code"], "RESOURCE_BUSY")

            adapter.block_release.set()
            time.sleep(0.03)
            self.assertTrue(console._exec_thread.is_alive())
            resumed = console.manual_pause()
            self.assertTrue(resumed["ok"])
            self.assertFalse(resumed["paused"])
            console._exec_thread.join(timeout=1.0)
        self.assertFalse(console._exec_thread.is_alive())
        self.assertEqual(console.manual_state, "arrived")

    def test_manual_unknown_outcome_blocks_successor_without_redispatch(self) -> None:
        adapter = ScriptedRobotStub(failures={
            "move_to": [{"error_code": "SERVICE_UNAVAILABLE", "message": "lost"}],
        })
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        console = DevConsoleController(adapter, skill_dispatcher=dispatcher)
        with patch("phi_robot.dev_console.audit_logger.log_action_start"), \
             patch("phi_robot.dev_console.audit_logger.log_action_end"):
            self.assertTrue(console.manual_next({"x": 1.0, "y": 1.0})["ok"])
            console._exec_thread.join(timeout=1.0)
            self.assertEqual(console.manual_state, "stopped")
            successor = console.manual_next({"x": 2.0, "y": 2.0})
        self.assertFalse(successor["ok"])
        self.assertEqual(adapter.calls["move_to"], 1)


if __name__ == "__main__":
    unittest.main()
