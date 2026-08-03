"""Versioned v1 skill catalog."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Mapping

from .adapters import AdapterSkillHandler
from .dispatcher import SkillDispatcher
from .models import CancellationMode, SkillDefinition, SkillKind
from .registry import SkillRegistry


SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"


def _object_schema(
    properties: Mapping[str, Any],
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "$schema": SCHEMA_URI,
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


POSE_SCHEMA = {
    "type": "object",
    "properties": {
        "x": {"type": "number"},
        "y": {"type": "number"},
        "z": {"type": "number"},
        "theta": {"type": "number", "default": 0.0},
    },
    "required": ["x", "y", "z", "theta"],
    "additionalProperties": False,
}

RESULT_SCHEMA = {
    "$schema": SCHEMA_URI,
    "type": "object",
    "properties": {
        "invocation_id": {"type": "string"},
        "outcome": {
            "type": "string",
            "enum": ["succeeded", "rejected", "failed", "cancelled", "unknown"],
        },
        "output": {"type": "object"},
        "state": {"type": "object"},
        "error": {},
        "metrics": {"type": "object"},
        "verification": {"type": "object"},
        "request_id": {"type": "string"},
        "mission_id": {"type": "string"},
        "goal_id": {"type": "string"},
        "step_id": {"type": "string"},
        "skill_name": {"type": "string"},
        "started_at": {"type": "number"},
        "finished_at": {"type": "number"},
    },
    "required": [
        "invocation_id", "outcome", "output", "state", "error", "metrics",
        "verification", "request_id", "mission_id", "goal_id", "step_id",
        "skill_name", "started_at", "finished_at",
    ],
    "additionalProperties": False,
}


def skill_definitions() -> list[SkillDefinition]:
    timeout = {"type": "number", "minimum": 0.001, "maximum": 600.0}
    empty = _object_schema({})
    definitions = [
        SkillDefinition(
            name="move_to", version="1.0", kind=SkillKind.COMPOSITE,
            description="Navigate to a validated target pose and verify arrival.",
            input_schema=_object_schema({"target": POSE_SCHEMA, "timeout_s": timeout}, ("target", "timeout_s")),
            planner_visible=True, resources=("robot.motion",), risk_level="high",
            timeout_s=30.0, cancellation_mode=CancellationMode.COOPERATIVE,
            preconditions=("robot is ready to walk",), verifier="arrived_at_target",
        ),
        SkillDefinition(
            name="pick", version="1.0", kind=SkillKind.COMPOSITE,
            description="Select, acquire, and verify one object at the trusted stock point.",
            input_schema=_object_schema(
                {"object_id": {"type": "string", "minLength": 1, "maxLength": 128}, "timeout_s": timeout},
                ("object_id", "timeout_s"),
            ),
            planner_visible=True, resources=("robot.motion", "robot.gripper"), risk_level="high",
            timeout_s=30.0, cancellation_mode=CancellationMode.NONE,
            preconditions=("robot arrived at trusted stock point", "fresh matching target pose"),
            verifier="holding_object",
        ),
        SkillDefinition(
            name="place", version="1.0", kind=SkillKind.COMPOSITE,
            description="Release the held object at a validated target pose and verify release.",
            input_schema=_object_schema({"target": POSE_SCHEMA, "timeout_s": timeout}, ("target", "timeout_s")),
            planner_visible=True, resources=("robot.motion", "robot.gripper"), risk_level="high",
            timeout_s=30.0, cancellation_mode=CancellationMode.NONE,
            preconditions=("robot is holding an object",), verifier="released_and_standing",
        ),
        SkillDefinition(
            name="get_pose", version="1.0", kind=SkillKind.QUERY,
            description="Read the current robot pose when the backend provides a reliable source.",
            input_schema=empty, side_effecting=False, concurrency_safe=True, risk_level="low",
        ),
        SkillDefinition(
            name="get_gripper_state", version="1.0", kind=SkillKind.QUERY,
            description="Read the current holding state when the backend provides a reliable source.",
            input_schema=empty, side_effecting=False, concurrency_safe=True, risk_level="low",
        ),
        SkillDefinition(
            name="get_input_source", version="1.0", kind=SkillKind.QUERY,
            description="Operator-only query for the active SONIC input source.",
            input_schema=empty, operator_only=True,
            side_effecting=False, concurrency_safe=True, risk_level="low",
        ),
        SkillDefinition(
            name="set_input_source", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Operator-only switch between ROS2 automation and gamepad control.",
            input_schema=_object_schema({"gamepad": {"type": "boolean"}}, ("gamepad",)),
            operator_only=True, resources=("robot.control",), risk_level="high",
            cancellation_mode=CancellationMode.NONE,
        ),
        SkillDefinition(
            name="set_safety_bypass", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Operator-only mutation of the local safety-policy bypass.",
            input_schema=_object_schema({"enabled": {"type": "boolean"}}, ("enabled",)),
            operator_only=True, resources=("robot.safety",), risk_level="critical",
            cancellation_mode=CancellationMode.NONE,
        ),
        SkillDefinition(
            name="select_target", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Operator/internal FoundationPose target selection primitive.",
            input_schema=_object_schema({
                "pick_x": {"type": "number"},
                "pick_y": {"type": "number"},
                "material_points_xy": {"type": "array", "items": {"type": "number"}, "minItems": 2},
            }, ("pick_x", "pick_y")),
            resources=("robot.perception",), risk_level="medium",
        ),
        SkillDefinition(
            name="clear_target", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Exit FoundationPose single-target mode.", input_schema=empty,
            resources=("robot.perception",), risk_level="low",
        ),
        *[
            SkillDefinition(
                name=name, version="1.0", kind=SkillKind.PRIMITIVE,
                description=description, input_schema=empty,
                operator_only=True, resources=resources, risk_level=risk,
                cancellation_mode=cancel_mode,
            )
            for name, description, resources, risk, cancel_mode in (
                ("pause_navigation", "Request navigation pause.", ("robot.motion",), "high", CancellationMode.COOPERATIVE),
                ("resume_navigation", "Request navigation resume.", ("robot.motion",), "high", CancellationMode.COOPERATIVE),
                ("stand", "Return the robot to standing posture.", ("robot.motion",), "high", CancellationMode.NONE),
                ("step_back", "Move the robot backward using the gateway primitive.", ("robot.motion",), "high", CancellationMode.NONE),
                ("drop_detector_enable", "Enable drop detection.", ("robot.perception",), "medium", CancellationMode.NONE),
                ("drop_detector_disable", "Disable drop detection.", ("robot.perception",), "medium", CancellationMode.NONE),
                ("adapter_reset", "Reset adapter-local state only.", ("robot.control",), "high", CancellationMode.NONE),
            )
        ],
        SkillDefinition(
            name="identify_dropped", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Identify a dropped object using trusted configured points.",
            input_schema=_object_schema({
                "material_points_xy": {"type": "array", "items": {"type": "number"}},
                "target_points_xy": {"type": "array", "items": {"type": "number"}},
            }),
            operator_only=True, resources=("robot.perception",), risk_level="medium",
        ),
        # ROS/Gateway primitives are registered for internal composition and
        # contract tests only.  They are deliberately invisible to planners;
        # a backend without an explicit implementation returns UNSUPPORTED.
        SkillDefinition(
            name="navigation_start", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Start navigation to a target without granting planner access.",
            input_schema=_object_schema({"target": POSE_SCHEMA, "timeout_s": timeout}, ("target", "timeout_s")),
            resources=("robot.motion",), risk_level="high",
            cancellation_mode=CancellationMode.COOPERATIVE,
        ),
        SkillDefinition(
            name="navigation_wait", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Wait for a fresh nav_reached terminal signal.",
            input_schema=_object_schema({"timeout_s": timeout}, ("timeout_s",)),
            side_effecting=False, concurrency_safe=True, resources=("robot.motion.state",),
            risk_level="low",
        ),
        SkillDefinition(
            name="gateway_notify_goal_reached", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Notify the locomotion gateway that navigation reached its goal.",
            input_schema=empty, resources=("robot.motion",), risk_level="high",
        ),
        SkillDefinition(
            name="wait_ready", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Wait for a declared gateway ready state.",
            input_schema=_object_schema({
                "operation": {"type": "string", "enum": ["move_to", "carry", "place"]},
                "timeout_s": timeout,
            }, ("operation", "timeout_s")),
            side_effecting=False, concurrency_safe=True, resources=("robot.motion.state",),
            risk_level="low",
        ),
        SkillDefinition(
            name="wait_fresh_pose", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Wait for a fresh FoundationPose sample matching the selected object.",
            input_schema=_object_schema({
                "object_id": {"type": "string", "minLength": 1},
                "timeout_s": timeout,
            }, ("object_id", "timeout_s")),
            side_effecting=False, concurrency_safe=False, resources=("robot.perception.state",),
            risk_level="medium",
        ),
        SkillDefinition(
            name="submit_carry", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Submit the cached fresh pose using the invocation idempotency key.",
            input_schema=_object_schema({"object_id": {"type": "string", "minLength": 1}}, ("object_id",)),
            resources=("robot.motion", "robot.gripper"), risk_level="high",
        ),
        SkillDefinition(
            name="lay_down", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Start the gateway lay-down sequence.", input_schema=empty,
            resources=("robot.motion", "robot.gripper"), risk_level="high",
        ),
        SkillDefinition(
            name="replay", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Request or observe the gateway replay transition.", input_schema=empty,
            resources=("robot.motion",), risk_level="high",
        ),
        SkillDefinition(
            name="reset_fp", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Operator-only FoundationPose tracker reset.", input_schema=empty,
            operator_only=True, resources=("robot.perception",), risk_level="medium",
        ),
        SkillDefinition(
            name="replan_pick", version="1.0", kind=SkillKind.PRIMITIVE,
            description="Operator-only pick using a previously reconciled dropped-object pose.",
            input_schema=empty, operator_only=True,
            resources=("robot.motion", "robot.gripper"), risk_level="high",
        ),
        SkillDefinition(
            name="get_robot_state", version="1.0", kind=SkillKind.QUERY,
            description="Read the adapter's structured robot state.", input_schema=empty,
            side_effecting=False, concurrency_safe=True, risk_level="low",
        ),
        SkillDefinition(
            name="get_runtime_snapshot", version="1.0", kind=SkillKind.QUERY,
            description=(
                "Read cached adapter, navigation, odometry, and selected-target "
                "state for internal UI compatibility."
            ),
            input_schema=empty, side_effecting=False, concurrency_safe=True,
            risk_level="low",
        ),
    ]
    return [
        replace(definition, output_schema=RESULT_SCHEMA)
        for definition in definitions
    ]


def build_skill_registry(adapter: Any, *, mode: str = "skills") -> SkillRegistry:
    composite_pick = mode == "skills"
    handler = AdapterSkillHandler(adapter, composite_pick=composite_pick)
    registry = SkillRegistry()
    for definition in skill_definitions():
        registry.register(definition, handler)
    return registry


def default_dispatcher_mode(adapter: Any) -> str:
    explicit = os.getenv("PHI_ACTION_EXECUTOR", "").strip().lower()
    if explicit:
        if explicit not in {"skills", "legacy-direct"}:
            raise ValueError("PHI_ACTION_EXECUTOR must be 'skills' or 'legacy-direct'")
        return explicit
    return "legacy-direct" if adapter.__class__.__name__ == "RosAcceptanceAdapter" else "skills"


def build_skill_dispatcher(
    adapter: Any,
    *,
    mode: str | None = None,
    audit_enabled: bool = True,
) -> SkillDispatcher:
    resolved_mode = mode or default_dispatcher_mode(adapter)
    registry = build_skill_registry(adapter, mode=resolved_mode)
    return SkillDispatcher(
        registry,
        mode=resolved_mode,
        audit_enabled=audit_enabled,
        composite_pick=resolved_mode == "skills",
    )
