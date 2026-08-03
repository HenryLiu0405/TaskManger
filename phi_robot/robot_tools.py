"""Tool layer for the phi_robot Phase 2 integration.

This module mirrors the `nanobot` tool pattern:
- each tool is a `Tool` subclass with a JSON Schema contract;
- `ToolRegistry` is used for registration and invocation;
- the backend returns structured responses with request/goal/step IDs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from phi_robot.nanobot_compat import Tool, ToolRegistry, tool_parameters

from phi_robot.fake_robot_service import DEFAULT_FAKE_SPEC, FakeRobotService
from phi_robot.skills import build_skill_dispatcher
from phi_robot.skills.catalog import skill_definitions
from phi_robot.skills.models import SkillDefinition


def _default_backend():
    try:
        from phi_robot.adapters import load_backend

        return load_backend(spec=DEFAULT_FAKE_SPEC)
    except Exception:
        # Fallback to in-process fake service if adapters are unavailable
        return FakeRobotService.from_spec(DEFAULT_FAKE_SPEC)


_METADATA_KEYS = frozenset({
    "request_id", "mission_id", "goal_id", "step_id", "robot_id", "idempotency_key",
})


def _definition_for(tool_name: str) -> SkillDefinition:
    definition = next(
        (item for item in skill_definitions() if item.name == tool_name and item.version == "1.0"),
        None,
    )
    if definition is None:
        raise KeyError(tool_name)
    return definition


def robot_request_schema(tool_name: str) -> dict[str, Any]:
    """Generate the tool request schema from the production skill definition."""

    definition = _definition_for(tool_name)
    schema = dict(definition.input_schema)
    properties = dict(schema.get("properties", {}))
    properties.update(
        {
            "request_id": {"type": "string"},
            "goal_id": {"type": "string"},
            "step_id": {"type": "string"},
            "mission_id": {"type": "string"},
        }
    )
    required = ["request_id", "goal_id", "step_id", *schema.get("required", [])]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


class RobotBackend(Protocol):
    """Minimal backend protocol used by the Phase 2 tool layer."""

    def execute(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        request_id: str,
        goal_id: str,
        step_id: str,
    ) -> dict[str, Any]: ...


@dataclass
class RobotToolCallRecord:
    request_id: str
    goal_id: str
    step_id: str
    tool: str
    args: dict[str, Any]
    status: str
    error_code: str | None
    message: str
    latency_ms: float
    attempt: int


@dataclass
class RobotToolClient:
    """Agent-facing adapter generated from the production skill catalog.

    ``max_retries`` remains as a compatibility field but is intentionally not
    applied.  Physical retries must be an explicit mission-level decision.
    """

    backend: RobotBackend = field(default_factory=_default_backend)
    max_retries: int = 0
    call_log: list[RobotToolCallRecord] = field(default_factory=list)
    _dispatcher: Any = field(default=None, init=False, repr=False)
    _dispatcher_backend_id: int = field(default=0, init=False, repr=False)

    def dispatcher(self):
        """Return the backend-bound dispatcher used by every generated tool."""

        if self._dispatcher is None or self._dispatcher_backend_id != id(self.backend):
            self._dispatcher = build_skill_dispatcher(
                self.backend, audit_enabled=False
            )
            self._dispatcher_backend_id = id(self.backend)
        return self._dispatcher

    async def execute(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = str(params.get("request_id", "req-local"))
        mission_id = str(params.get("mission_id", ""))
        goal_id = str(params.get("goal_id", "goal-local"))
        step_id = str(params.get("step_id", "step-local"))
        robot_id = str(params.get("robot_id", "active"))
        idempotency_key = str(params.get("idempotency_key", ""))
        args = {key: value for key, value in params.items() if key not in _METADATA_KEYS}
        dispatcher = self.dispatcher()

        start = time.perf_counter()
        loop = __import__("asyncio").get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: dispatcher.execute_legacy(
                tool,
                args,
                request_id=request_id,
                mission_id=mission_id,
                goal_id=goal_id,
                step_id=step_id,
                robot_id=robot_id,
                source="robot_tool_suite",
                idempotency_key=idempotency_key,
            ),
        )
        self.call_log.append(
            RobotToolCallRecord(
                request_id=request_id,
                goal_id=goal_id,
                step_id=step_id,
                tool=tool,
                args=dict(args),
                status=response["status"],
                error_code=response.get("error_code"),
                message=str(response.get("message", "")),
                latency_ms=(time.perf_counter() - start) * 1000.0,
                attempt=1,
            )
        )
        return response


class _RobotTool(Tool):
    tool_name: str = ""

    def __init__(self, client: RobotToolClient):
        self.client = client

    @property
    def name(self) -> str:
        return self.tool_name

    async def execute(self, **kwargs: Any) -> Any:
        return await self.client.execute(self.tool_name, dict(kwargs))


class CatalogRobotTool(Tool):
    """Nanobot-compatible tool projected from one SkillRegistry entry."""

    def __init__(self, client: RobotToolClient, definition: SkillDefinition):
        self.client = client
        self.definition = definition

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def description(self) -> str:
        return self.definition.description

    @property
    def parameters(self) -> dict[str, Any]:
        return robot_request_schema(self.definition.name)

    @property
    def read_only(self) -> bool:
        return not self.definition.side_effecting

    @property
    def concurrency_safe(self) -> bool:
        return self.definition.concurrency_safe

    async def execute(self, **kwargs: Any) -> Any:
        return await self.client.execute(self.definition.name, dict(kwargs))


@tool_parameters(robot_request_schema("move_to"))
class MoveToTool(_RobotTool):
    tool_name = "move_to"

    @property
    def description(self) -> str:
        return "Move the robot end-effector or base to a target pose."


@tool_parameters(robot_request_schema("pick"))
class PickTool(_RobotTool):
    tool_name = "pick"

    @property
    def description(self) -> str:
        return "Pick an object at the current pose with structured error reporting."


@tool_parameters(robot_request_schema("place"))
class PlaceTool(_RobotTool):
    tool_name = "place"

    @property
    def description(self) -> str:
        return "Place the held object at the target pose with structured error reporting."


@tool_parameters(robot_request_schema("get_pose"))
class GetPoseTool(_RobotTool):
    tool_name = "get_pose"

    @property
    def description(self) -> str:
        return "Return the current robot pose from the backend snapshot."

    @property
    def read_only(self) -> bool:
        return True


@tool_parameters(robot_request_schema("get_gripper_state"))
class GetGripperStateTool(_RobotTool):
    tool_name = "get_gripper_state"

    @property
    def description(self) -> str:
        return "Return the gripper state from the backend snapshot."

    @property
    def read_only(self) -> bool:
        return True


@dataclass
class RobotToolSuite:
    """Convenience bundle used by the Phase 2 runner and future agent wiring."""

    client: RobotToolClient = field(default_factory=RobotToolClient)

    def build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        skill_registry = self.client.dispatcher().registry
        exposed = {"move_to", "pick", "place", "get_pose", "get_gripper_state"}
        if self.client.backend.__class__.__name__ == "RosAcceptanceAdapter":
            # Queries remain catalogued but are not advertised until a real
            # ROS state source can satisfy their contract.
            exposed -= {"get_pose", "get_gripper_state"}
        for definition in skill_registry.definitions():
            if definition.name in exposed:
                registry.register(CatalogRobotTool(self.client, definition))
        return registry
