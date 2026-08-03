from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from phi_robot.fake_robot_service import contract_for
from phi_robot.mission_planner import MissionPlanner
from phi_robot.models import PlanStep
from phi_robot.adapters.move_to_passthrough import MoveToPassthroughAdapter
from phi_robot.robot_tools import RobotToolClient, RobotToolSuite, robot_request_schema
from phi_robot.skills import SkillContext, SkillRequest
from phi_robot.skills.catalog import (
    build_skill_dispatcher,
    build_skill_registry,
    default_dispatcher_mode,
    skill_definitions,
)
from phi_robot.skills.compat import normalize_legacy_call

from tests.offline.fakes import ScriptedRobotStub


class SkillCatalogTests(unittest.TestCase):
    def test_catalog_is_versioned_unique_and_two_layered(self) -> None:
        definitions = skill_definitions()
        keys = [(item.name, item.version) for item in definitions]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(
            [item.name for item in definitions if item.planner_visible],
            ["move_to", "pick", "place"],
        )
        for item in definitions:
            self.assertEqual(item.input_schema.get("$schema"), "https://json-schema.org/draft/2020-12/schema")
            self.assertEqual(item.output_schema.get("$schema"), "https://json-schema.org/draft/2020-12/schema")
            self.assertFalse(item.input_schema.get("additionalProperties", True))
            if item.planner_visible:
                self.assertEqual(item.kind.value, "composite")

    def test_rollout_mode_is_exclusive_and_ros_defaults_to_legacy(self) -> None:
        class RosAcceptanceAdapter(ScriptedRobotStub):
            pass

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PHI_ACTION_EXECUTOR", None)
            self.assertEqual(default_dispatcher_mode(ScriptedRobotStub()), "skills")
            self.assertEqual(default_dispatcher_mode(RosAcceptanceAdapter()), "legacy-direct")
        with patch.dict(os.environ, {"PHI_ACTION_EXECUTOR": "skills"}):
            self.assertEqual(default_dispatcher_mode(RosAcceptanceAdapter()), "skills")
        with patch.dict(os.environ, {"PHI_ACTION_EXECUTOR": "shadow"}):
            with self.assertRaises(ValueError):
                default_dispatcher_mode(ScriptedRobotStub())

    def test_planner_steps_pass_strict_registry_validation(self) -> None:
        registry = build_skill_registry(ScriptedRobotStub(), mode="skills")
        plan = MissionPlanner().plan("goal", ["se", "c"], request_id="req")
        self.assertTrue(plan)
        for step in plan:
            errors = registry.validate(SkillRequest(
                skill_name=step.tool,
                version=step.skill_version,
                args=step.args,
                context=SkillContext(
                    robot_id="robot-test",
                    source="planner-contract",
                    request_id="req",
                    mission_id="mission",
                    goal_id="goal",
                    step_id=step.step_id,
                ),
                annotations=step.annotations,
            ))
            self.assertEqual(errors, [], (step, errors))

    def test_schema_rejects_missing_wrong_and_unknown_fields_before_adapter(self) -> None:
        cases = [
            ("move_to", {"target": {"x": 1, "y": 2, "z": 0, "theta": 0}}),
            ("move_to", {"target": {"x": 1, "y": 2, "z": 0}, "timeout_s": 1}),
            ("move_to", {"target": {"x": "bad", "y": 2, "z": 0}, "timeout_s": 1}),
            ("move_to", {"target": {"x": 1, "y": 2, "z": 0, "frame": "map"}, "timeout_s": 1}),
            ("pick", {"object_id": "box", "timeout_s": 1, "grip_force": 9}),
            ("place", {"target": {"x": 1, "y": 2, "z": 0}, "timeout_s": -1}),
            ("get_pose", {"unexpected": True}),
        ]
        for index, (tool, args) in enumerate(cases):
            with self.subTest(tool=tool, index=index):
                adapter = ScriptedRobotStub()
                dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
                result = dispatcher.execute(SkillRequest(
                    skill_name=tool,
                    version="1.0",
                    args=args,
                    context=SkillContext(
                        robot_id="robot-test",
                        source="strict-schema-test",
                        request_id=f"req-{index}",
                        goal_id="goal",
                        step_id=f"step-{index}",
                        idempotency_key=f"invalid-{index}",
                    ),
                )).to_legacy()
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["error_code"], "INVALID_ARGS")
                self.assertEqual(adapter.trace, [])

        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        unknown = dispatcher.execute_legacy(
            "fly", {}, request_id="unknown", goal_id="goal", step_id="unknown",
            idempotency_key="unknown-skill",
        )
        self.assertEqual(unknown["error_code"], "UNSUPPORTED_SKILL")
        self.assertEqual(adapter.trace, [])

        unknown_version = dispatcher.execute_legacy(
            "move_to",
            {"target": {"x": 1, "y": 2, "z": 0, "theta": 0}, "timeout_s": 1},
            version="9.9", request_id="unknown-version", goal_id="goal",
            step_id="unknown-version", idempotency_key="unknown-version",
        )
        self.assertEqual(unknown_version["error_code"], "UNSUPPORTED_SKILL")
        self.assertEqual(adapter.trace, [])

    def test_legacy_converter_separates_args_annotations_and_context(self) -> None:
        args, annotations = normalize_legacy_call("move_to", {
            "x": 1,
            "y": 2,
            "z": 0,
            "theta": 0.5,
            "timeout_s": 4,
            "action": "start",
            "current": {"x": 0, "y": 0, "z": 0},
            "slot_nav2_x": 9,
            "request_id": "must-not-leak",
        })
        self.assertEqual(args, {
            "target": {"x": 1, "y": 2, "z": 0, "theta": 0.5},
            "timeout_s": 4.0,
        })
        self.assertEqual(annotations["slot_nav2_x"], 9)
        self.assertIn("legacy_current", annotations)
        self.assertNotIn("request_id", args)

        place_args, place_annotations = normalize_legacy_call("place", {
            "x": 3,
            "y": 4,
            "z": 0,
            "object_id": "legacy-correlation-only",
            "timeout_s": 2,
        })
        self.assertEqual(place_args["target"]["x"], 3)
        self.assertEqual(place_annotations["object_id"], "legacy-correlation-only")

    def test_legacy_converter_does_not_sanitize_invalid_action_values(self) -> None:
        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        cases = (
            ("move_to", {
                "target": {"x": 1, "y": 2, "z": 0, "theta": 0},
                "timeout_s": -5,
            }),
            ("pick", {"object_id": 42, "timeout_s": 1}),
        )
        for index, (tool, args) in enumerate(cases):
            with self.subTest(tool=tool):
                result = dispatcher.execute_legacy(
                    tool, args, request_id=f"legacy-invalid-{index}",
                    goal_id="goal", step_id=f"step-{index}",
                    idempotency_key=f"legacy-invalid-{index}",
                )
                self.assertEqual(result["error_code"], "INVALID_ARGS")
        self.assertEqual(adapter.trace, [])

    def test_annotations_are_not_forwarded_to_adapter(self) -> None:
        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        result = dispatcher.execute_legacy(
            "move_to",
            {"target": {"x": 1, "y": 2, "z": 0, "theta": 0}, "timeout_s": 2},
            request_id="req",
            goal_id="goal",
            step_id="step",
            annotations={"slot_nav2_x": 99, "grid_nav2_y": 88},
            idempotency_key="annotations-not-forwarded",
        )
        self.assertEqual(result["status"], "ok")
        forwarded = adapter.trace[-1]["args"]
        self.assertNotIn("slot_nav2_x", forwarded)
        self.assertNotIn("grid_nav2_y", forwarded)

    def test_reliable_state_preconditions_reject_before_backend(self) -> None:
        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        place = dispatcher.execute_legacy(
            "place",
            {"target": {"x": 1, "y": 2, "z": 0, "theta": 0}, "timeout_s": 1},
            request_id="pre-place", goal_id="goal", step_id="place",
            idempotency_key="pre-place",
        )
        self.assertEqual(place["error_code"], "PRECONDITION_FAILED")
        self.assertEqual(adapter.trace, [])

        adapter.holding = "existing-box"
        pick = dispatcher.execute_legacy(
            "pick", {"object_id": "box-00", "timeout_s": 1},
            request_id="pre-pick", goal_id="goal", step_id="pick",
            annotations={"slot_nav2_x": 0, "slot_nav2_y": 0},
            idempotency_key="pre-pick",
        )
        self.assertEqual(pick["error_code"], "PRECONDITION_FAILED")
        self.assertEqual(adapter.trace, [])

    def test_unimplemented_acceptance_capability_is_not_reported_as_success(self) -> None:
        adapter = MoveToPassthroughAdapter("http://127.0.0.1:1")
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        result = dispatcher.execute_legacy(
            "place",
            {"target": {"x": 1, "y": 2, "z": 0, "theta": 0}, "timeout_s": 1},
            request_id="unsupported", goal_id="goal", step_id="place",
            idempotency_key="unsupported-place",
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "UNSUPPORTED_CAPABILITY")
        self.assertEqual(result["outcome"], "rejected")

    def test_operator_capabilities_are_hidden_and_use_the_dispatcher_contract(self) -> None:
        from phi_robot.robot_state import safety_fsm

        definitions = {item.name: item for item in skill_definitions()}
        for name in ("get_input_source", "set_input_source", "set_safety_bypass"):
            self.assertTrue(definitions[name].operator_only)
            self.assertFalse(definitions[name].planner_visible)

        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        queried = dispatcher.execute_legacy(
            "get_input_source", {}, request_id="input-get", goal_id="operator",
            step_id="get-input", source="operator_api",
        )
        self.assertEqual(queried["status"], "ok")
        self.assertEqual(queried["active_source"], "ROS2")

        changed = dispatcher.execute_legacy(
            "set_input_source", {"gamepad": True},
            request_id="input-set", goal_id="operator", step_id="set-input",
            source="operator_api", idempotency_key="input-set",
        )
        self.assertEqual(changed["status"], "ok")
        self.assertEqual(changed["active_source"], "GAMEPAD")
        self.assertEqual(adapter.names, ["set_input_source"])

        try:
            bypass = dispatcher.execute_legacy(
                "set_safety_bypass", {"enabled": True},
                request_id="bypass", goal_id="operator", step_id="set-bypass",
                source="operator_api", idempotency_key="bypass-on",
            )
            self.assertEqual(bypass["status"], "ok")
            self.assertTrue(bypass["bypass"])
            self.assertTrue(safety_fsm.bypass)
        finally:
            safety_fsm.set_bypass(False)

    def test_internal_robot_state_query_returns_structured_state(self) -> None:
        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        result = dispatcher.execute_legacy(
            "get_robot_state", {}, request_id="state", goal_id="operator",
            step_id="get-state", source="dev_console_query",
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["robot_state"], adapter.snapshot())
        self.assertEqual(result["state"], adapter.snapshot())

    def test_runtime_cache_reads_use_an_internal_query(self) -> None:
        adapter = ScriptedRobotStub()
        adapter.nav_reached = True
        adapter._fp_target_object_id = 12
        adapter.get_odom = lambda: {"x": 1.0, "y": 2.0, "yaw_deg": 3.0}
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        result = dispatcher.execute_legacy(
            "get_runtime_snapshot", {}, request_id="runtime-query",
            goal_id="goal", step_id="runtime-query",
            idempotency_key="runtime-query",
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["snapshot"], adapter.snapshot())
        self.assertTrue(result["nav_reached"])
        self.assertEqual(result["locked_object_id"], 12)
        self.assertEqual(
            result["odom"], {"x": 1.0, "y": 2.0, "yaw_deg": 3.0}
        )
        self.assertEqual(
            result["target_pose"], {"supported": False, "has_pose": False}
        )
        definition = dispatcher.registry.get("get_runtime_snapshot", "1.0")
        self.assertIsNotNone(definition)
        self.assertFalse(definition.planner_visible)
        self.assertFalse(definition.side_effecting)
        self.assertTrue(definition.concurrency_safe)

    def test_pick_rejects_untrusted_arrival_distance_before_target_selection(self) -> None:
        adapter = ScriptedRobotStub()
        dispatcher = build_skill_dispatcher(adapter, audit_enabled=False)
        result = dispatcher.execute_legacy(
            "pick", {"object_id": "box-00", "timeout_s": 1},
            request_id="far-pick", goal_id="goal", step_id="pick",
            annotations={"slot_nav2_x": 10, "slot_nav2_y": 10},
            idempotency_key="far-pick",
        )
        self.assertEqual(result["error_code"], "PRECONDITION_FAILED")
        self.assertEqual(adapter.trace, [])

    def test_planstep_old_json_remains_readable(self) -> None:
        step = PlanStep.from_dict({
            "step_id": "old-step",
            "task_index": 0,
            "tool": "place",
            "args": {"x": 1, "y": 2, "z": 0, "timeout_s": 3},
            "unknown_old_field": "ignored",
        })
        self.assertEqual(step.skill_version, "1.0")
        self.assertEqual(step.annotations, {})
        self.assertEqual(step.attempt, 0)

    def test_robot_tool_suite_is_generated_from_skill_contract(self) -> None:
        adapter = ScriptedRobotStub()
        suite = RobotToolSuite(client=RobotToolClient(backend=adapter))
        registry = suite.build_registry()
        names = [item["function"]["name"] for item in registry.get_definitions()]
        self.assertEqual(names, ["get_gripper_state", "get_pose", "move_to", "pick", "place"])
        for name in names:
            tool = registry.get(name)
            self.assertIsNotNone(tool)
            self.assertEqual(tool.parameters, robot_request_schema(name))
            self.assertNotIn("robot_id", tool.parameters["properties"])
            self.assertNotIn("idempotency_key", tool.parameters["properties"])

        invalid = asyncio.run(registry.execute("move_to", {
            "request_id": "tool-req",
            "goal_id": "goal",
            "step_id": "step",
            "target": {"x": 1, "y": 2, "z": 0},
            "timeout_s": 1,
            "unknown": "rejected",
        }))
        self.assertIsInstance(invalid, str)
        self.assertIn("Invalid parameters", invalid)
        self.assertEqual(adapter.trace, [])

        skill_schemas = {
            item.name: dict(item.input_schema)
            for item in skill_definitions()
        }
        for name in names:
            self.assertEqual(contract_for(name), skill_schemas[name])

    def test_unreliable_ros_queries_are_catalogued_but_not_advertised(self) -> None:
        class RosAcceptanceAdapter(ScriptedRobotStub):
            pass

        suite = RobotToolSuite(client=RobotToolClient(backend=RosAcceptanceAdapter()))
        names = [item["function"]["name"] for item in suite.build_registry().get_definitions()]
        self.assertEqual(names, ["move_to", "pick", "place"])
        definitions = suite.client.dispatcher().registry.definitions()
        self.assertIn("get_pose", {item.name for item in definitions})


if __name__ == "__main__":
    unittest.main()
