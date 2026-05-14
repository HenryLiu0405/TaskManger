"""Tool layer for the phi_robot Phase 2 integration.

This module mirrors the `nanobot` tool pattern:
- each tool is a `Tool` subclass with a JSON Schema contract;
- `ToolRegistry` is used for registration and invocation;
- the backend returns structured responses with request/goal/step IDs.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from phi_robot.nanobot_compat import Tool, ToolRegistry, tool_parameters

from phi_robot.fake_robot_service import DEFAULT_FAKE_SPEC, FakeRobotService, contract_for


def _default_backend():
    try:
        from phi_robot.adapters import load_backend

        return load_backend(spec=DEFAULT_FAKE_SPEC)
    except Exception:
        # Fallback to in-process fake service if adapters are unavailable
        return FakeRobotService.from_spec(DEFAULT_FAKE_SPEC)


_METADATA_KEYS = frozenset({"request_id", "goal_id", "step_id"})
_RETRYABLE_ERROR_CODES = frozenset({"GRIP_FAIL", "TIMEOUT", "INTERNAL_ERROR"})


def robot_request_schema(tool_name: str) -> dict[str, Any]:
    """Build the Phase 2 tool schema with the contract metadata fields included."""

    schema = contract_for(tool_name)
    properties = dict(schema.get("properties", {}))
    properties.update(
        {
            "request_id": {"type": "string"},
            "goal_id": {"type": "string"},
            "step_id": {"type": "string"},
        }
    )
    required = ["request_id", "goal_id", "step_id", *schema.get("required", [])]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
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
    """Structured tool executor with timeout and retry handling."""

    backend: RobotBackend = field(default_factory=_default_backend)
    max_retries: int = 1
    retryable_error_codes: frozenset[str] = _RETRYABLE_ERROR_CODES
    call_log: list[RobotToolCallRecord] = field(default_factory=list)

    async def execute(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = str(params.get("request_id", "req-local"))
        goal_id = str(params.get("goal_id", "goal-local"))
        step_id = str(params.get("step_id", "step-local"))
        args = {key: value for key, value in params.items() if key not in _METADATA_KEYS}

        attempts = 0
        last_response: dict[str, Any] | None = None
        timeout_s = self._timeout_seconds(args)

        while attempts <= self.max_retries:
            attempts += 1
            start = time.perf_counter()
            response = await self._call_backend(
                tool,
                args,
                request_id=request_id,
                goal_id=goal_id,
                step_id=step_id,
                timeout_s=timeout_s,
            )
            latency_ms = (time.perf_counter() - start) * 1000.0
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
                    latency_ms=latency_ms,
                    attempt=attempts,
                )
            )

            if response["status"] == "ok":
                return response

            last_response = response
            if response.get("error_code") not in self.retryable_error_codes:
                return response
            if attempts > self.max_retries:
                return response

        assert last_response is not None
        return last_response

    async def _call_backend(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        request_id: str,
        goal_id: str,
        step_id: str,
        timeout_s: float,
    ) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self.backend.execute,
                    tool,
                    args,
                    request_id=request_id,
                    goal_id=goal_id,
                    step_id=step_id,
                ),
                timeout=timeout_s,
            )
        except TimeoutError:
            snapshot = getattr(self.backend, "snapshot", None)
            return {
                "request_id": request_id,
                "status": "error",
                "error_code": "TIMEOUT",
                "message": f"tool execution exceeded timeout_s={timeout_s}",
                "state": snapshot() if callable(snapshot) else {},
                "metrics": {"latency_ms": timeout_s * 1000.0},
            }
        except Exception as exc:  # pragma: no cover - defensive fallback
            snapshot = getattr(self.backend, "snapshot", None)
            return {
                "request_id": request_id,
                "status": "error",
                "error_code": "INTERNAL_ERROR",
                "message": str(exc),
                "state": snapshot() if callable(snapshot) else {},
                "metrics": {},
            }

    @staticmethod
    def _timeout_seconds(args: dict[str, Any]) -> float:
        timeout = args.get("timeout_s", 30)
        try:
            return max(float(timeout), 0.001)
        except (TypeError, ValueError):
            return 30.0


class _RobotTool(Tool):
    tool_name: str = ""

    def __init__(self, client: RobotToolClient):
        self.client = client

    @property
    def name(self) -> str:
        return self.tool_name

    async def execute(self, **kwargs: Any) -> Any:
        return await self.client.execute(self.tool_name, dict(kwargs))


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
        registry.register(MoveToTool(self.client))
        registry.register(PickTool(self.client))
        registry.register(PlaceTool(self.client))
        registry.register(GetPoseTool(self.client))
        registry.register(GetGripperStateTool(self.client))
        return registry
