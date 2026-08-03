"""Planner-facing semantic skill vocabulary and execution adapter boundary."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from phi_robot.skills.dispatcher import SkillDispatcher
from phi_robot.skills.models import CancellationMode, SkillDefinition, SkillKind, SkillRequest
from phi_robot.skills.registry import SkillRegistry


SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
AGENT_SKILL_VERSION = "1.0"
OBSERVATION_CHANNELS = ("rgb", "overlay", "mask", "depth", "drop")
SUPERVISOR_CONTROL_TOOLS = frozenset({"stop_motion", "resume_motion"})


def _object(properties: Mapping[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "$schema": SCHEMA_URI,
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


LOCATION_REF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string", "enum": ["1.0"]},
        "kind": {
            "type": "string",
            "enum": ["named", "pose_ref", "region", "object_relative"],
        },
        "namespace": {"type": "string", "minLength": 1},
        "location_id": {"type": "string", "minLength": 1},
        "scene_id": {"type": "string", "minLength": 1},
        "scene_version": {"type": "string", "minLength": 1},
        "attributes": {"type": "object"},
    },
    "required": [
        "schema_version", "kind", "namespace", "location_id", "scene_id", "scene_version"
    ],
    "additionalProperties": False,
}

PERCEPTION_REF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string", "enum": ["1.0"]},
        "provider": {"type": "string", "minLength": 1},
        # Provider IDs can be integers or strings; the local ObjectRef parser
        # performs the strict union check after model output validation.
        "object_id": {},
        "tracker_session_id": {"type": "string", "minLength": 1},
        "observation_id": {"type": "string", "minLength": 1},
    },
    "required": [
        "schema_version", "provider", "object_id", "tracker_session_id", "observation_id"
    ],
    "additionalProperties": False,
}

OBJECT_REF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string", "enum": ["1.0"]},
        "logical_object_id": {"type": "string", "minLength": 1},
        "category": {"type": "string", "minLength": 1},
        "attributes": {"type": "object"},
        "perception_ref": PERCEPTION_REF_SCHEMA,
    },
    "required": [
        "schema_version", "logical_object_id", "category", "attributes", "perception_ref"
    ],
    "additionalProperties": False,
}


class SemanticSkillBackend(Protocol):
    """Trusted local implementation of the semantic action vocabulary."""

    def execute_semantic(
        self,
        skill_name: str,
        args: Mapping[str, Any],
        *,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...


class SemanticSkillHandler:
    def __init__(self, backend: SemanticSkillBackend) -> None:
        self.backend = backend

    def __call__(self, request: SkillRequest) -> Mapping[str, Any]:
        return self.backend.execute_semantic(
            request.skill_name,
            dict(request.args),
            context={
                "robot_id": request.context.robot_id,
                "request_id": request.context.request_id,
                "mission_id": request.context.mission_id,
                "goal_id": request.context.goal_id,
                "node_id": request.context.step_id,
                "invocation_id": request.context.invocation_id,
                "idempotency_key": request.context.idempotency_key,
                "annotations": dict(request.annotations),
            },
        )


class SupervisorToolBridge:
    """Execute out-of-band model tool calls without exposing ROS authority.

    In particular, ``stop_motion`` always uses the Supervisor interrupt lane;
    it is never queued behind the normal semantic dispatcher writer lock.
    Physical task actions belong in a validated PlanGraph.
    """

    def __init__(self, *, supervisor: Any, observation_source: Any) -> None:
        self.supervisor = supervisor
        self.observation_source = observation_source

    def execute(self, decision: Any, *, mission_id: str) -> Mapping[str, Any]:
        if getattr(decision, "decision_type", None) != "tool_call":
            raise ValueError("SupervisorToolBridge requires a tool_call decision")
        payload = dict(getattr(decision, "payload", {}) or {})
        name = str(payload.get("skill_name") or "")
        args = dict(payload.get("args") or {})
        if name == "stop_motion":
            receipt = self.supervisor.request_stop(
                mission_id, reason=str(args.get("reason") or "model requested stop")
            )
            return {"tool": name, "receipt": receipt.to_dict()}
        if name == "resume_motion":
            return {
                "tool": name,
                "snapshot": self.supervisor.start(mission_id, background=True),
            }
        if name in {
            "observe_scene", "request_reobservation", "inspect_anomaly", "list_objects"
        }:
            requested = args.get("channels")
            channels = (
                tuple(str(value) for value in requested)
                if isinstance(requested, list) and requested
                else ("rgb", "overlay", "mask", "depth")
            )
            observation = self.observation_source.capture(
                channels=channels,
                reason=str(args.get("reason") or f"model tool call {name}"),
            )
            return {"tool": name, "observation": observation.to_dict()}
        raise ValueError(
            f"physical semantic tool {name!r} must be part of a validated PlanGraph"
        )


def agent_skill_definitions() -> tuple[SkillDefinition, ...]:
    timeout = {"type": "number", "minimum": 0.001, "maximum": 600.0}
    logical_id = {"type": "string", "minLength": 1, "maxLength": 256}
    channels = {
        "type": "array",
        "items": {"type": "string", "enum": list(OBSERVATION_CHANNELS)},
        "minItems": 1,
        "maxItems": len(OBSERVATION_CHANNELS),
    }
    values = (
        SkillDefinition(
            name="observe_scene", version=AGENT_SKILL_VERSION, kind=SkillKind.QUERY,
            description="Capture a fresh synchronized visual observation bundle.",
            input_schema=_object({"channels": channels, "reason": {"type": "string"}}, ("channels", "reason")),
            planner_visible=True, side_effecting=False, concurrency_safe=True, risk_level="low",
        ),
        SkillDefinition(
            name="list_objects", version=AGENT_SKILL_VERSION, kind=SkillKind.QUERY,
            description="Return every fresh perception candidate, including provider object IDs.",
            input_schema=_object({}), planner_visible=True, side_effecting=False,
            concurrency_safe=True, risk_level="low",
        ),
        SkillDefinition(
            name="move_to_location", version=AGENT_SKILL_VERSION, kind=SkillKind.COMPOSITE,
            description="Resolve a semantic LocationRef locally, navigate, and verify arrival.",
            input_schema=_object({"location_ref": LOCATION_REF_SCHEMA, "timeout_s": timeout}, ("location_ref", "timeout_s")),
            planner_visible=True, resources=("robot.motion",), risk_level="high",
            cancellation_mode=CancellationMode.COOPERATIVE, verifier="arrived_at_location",
        ),
        SkillDefinition(
            name="select_object", version=AGENT_SKILL_VERSION, kind=SkillKind.COMPOSITE,
            description="Select an observed provider object_id and issue a fresh local selection epoch.",
            input_schema=_object({"object_ref": OBJECT_REF_SCHEMA}, ("object_ref",)),
            planner_visible=True, resources=("robot.perception",), risk_level="medium",
            verifier="selection_epoch_bound",
        ),
        SkillDefinition(
            name="pick_object", version=AGENT_SKILL_VERSION, kind=SkillKind.COMPOSITE,
            description="Pick the currently selected logical object using a post-selection fresh pose.",
            input_schema=_object({"logical_object_id": logical_id, "timeout_s": timeout}, ("logical_object_id", "timeout_s")),
            planner_visible=True, resources=("robot.motion", "robot.gripper"), risk_level="high",
            verifier="holding_object",
        ),
        SkillDefinition(
            name="place_at_location", version=AGENT_SKILL_VERSION, kind=SkillKind.COMPOSITE,
            description="Place the held logical object at a resolved LocationRef and verify release.",
            input_schema=_object({
                "logical_object_id": logical_id,
                "location_ref": LOCATION_REF_SCHEMA,
                "timeout_s": timeout,
            }, ("logical_object_id", "location_ref", "timeout_s")),
            planner_visible=True, resources=("robot.motion", "robot.gripper"), risk_level="high",
            verifier="placed_at_location",
        ),
        SkillDefinition(
            name="verify_holding", version=AGENT_SKILL_VERSION, kind=SkillKind.QUERY,
            description="Combine deterministic payload state and visual evidence for holding.",
            input_schema=_object({"logical_object_id": logical_id}, ("logical_object_id",)),
            planner_visible=True, side_effecting=False, concurrency_safe=True, risk_level="low",
        ),
        SkillDefinition(
            name="verify_placement", version=AGENT_SKILL_VERSION, kind=SkillKind.QUERY,
            description="Combine deterministic and visual evidence for final placement.",
            input_schema=_object({"logical_object_id": logical_id, "location_ref": LOCATION_REF_SCHEMA}, ("logical_object_id", "location_ref")),
            planner_visible=True, side_effecting=False, concurrency_safe=True, risk_level="low",
        ),
        SkillDefinition(
            name="stop_motion", version=AGENT_SKILL_VERSION, kind=SkillKind.COMPOSITE,
            description="Request the Supervisor high-priority local interrupt lane and confirm stop.",
            input_schema=_object({"reason": {"type": "string", "minLength": 1}}, ("reason",)),
            planner_visible=True, resources=("robot.motion",), risk_level="critical",
            cancellation_mode=CancellationMode.CONFIRMED, verifier="motion_stopped",
        ),
        SkillDefinition(
            name="resume_motion", version=AGENT_SKILL_VERSION, kind=SkillKind.COMPOSITE,
            description="Resume an explicitly paused mission through the Supervisor.",
            input_schema=_object({"reason": {"type": "string", "minLength": 1}}, ("reason",)),
            planner_visible=True, resources=("robot.motion",), risk_level="high",
            verifier="execution_resumed",
        ),
        SkillDefinition(
            name="inspect_anomaly", version=AGENT_SKILL_VERSION, kind=SkillKind.QUERY,
            description="Inspect synchronized anomaly evidence without moving the robot.",
            input_schema=_object({"event_id": {"type": "string", "minLength": 1}, "channels": channels}, ("event_id", "channels")),
            planner_visible=True, side_effecting=False, concurrency_safe=True, risk_level="low",
        ),
        SkillDefinition(
            name="request_reobservation", version=AGENT_SKILL_VERSION, kind=SkillKind.QUERY,
            description="Request fresh evidence or another configured camera view.",
            input_schema=_object({"channels": channels, "reason": {"type": "string", "minLength": 1}}, ("channels", "reason")),
            planner_visible=True, side_effecting=False, concurrency_safe=True, risk_level="low",
        ),
    )
    return values


def build_agent_skill_registry(backend: SemanticSkillBackend) -> SkillRegistry:
    registry = SkillRegistry()
    handler = SemanticSkillHandler(backend)
    for definition in agent_skill_definitions():
        registry.register(definition, handler)
    return registry


def build_agent_skill_dispatcher(
    backend: SemanticSkillBackend,
    *,
    audit_enabled: bool = True,
) -> SkillDispatcher:
    return SkillDispatcher(
        build_agent_skill_registry(backend),
        mode="skills",
        audit_enabled=audit_enabled,
    )
