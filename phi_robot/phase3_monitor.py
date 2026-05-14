"""Phase 3 monitor, replanning, and heartbeat primitives.

This module follows the lifecycle ideas from nanobot's AgentHook and
HeartbeatService, but keeps the implementation local and deterministic so the
phase can be validated without importing the full nanobot runtime.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


EventType = Literal["new_instruction", "safety_alert", "priority_change", "heartbeat_tick"]
Priority = Literal["low", "normal", "high"]
TaskStatus = Literal["PENDING", "RUNNING", "SUCCESS", "FAILED", "ABORTED"]
PlanAction = Literal["continue", "replan", "abort"]
HeartbeatAction = Literal["skip", "run"]


@dataclass(slots=True)
class Phase3Event:
    event_id: str
    type: EventType
    priority: Priority
    content: str
    ts: int


@dataclass(slots=True)
class Phase3HookContext:
    """Mutable per-iteration state mirrored from nanobot.AgentHookContext."""

    iteration: int
    messages: list[dict[str, Any]]
    response: dict[str, Any] | None = None
    usage: dict[str, int] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[Any] = field(default_factory=list)
    tool_events: list[dict[str, str]] = field(default_factory=list)
    streamed_content: bool = False
    final_content: str | None = None
    stop_reason: str | None = None
    error: str | None = None
    injections: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class PlanStep:
    step_id: str
    tool: str
    args: dict[str, Any]


@dataclass(slots=True)
class PlanState:
    goal_id: str
    steps: list[PlanStep]
    status: TaskStatus = "PENDING"
    replanning_count: int = 0
    max_replans: int = 2
    stop_reason: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    tool_events: list[dict[str, str]] = field(default_factory=list)


class Phase3EventQueue:
    """Priority-ordered event queue with event_id de-duplication."""

    def __init__(self, events: list[Phase3Event] | None = None) -> None:
        self._events = list(events or [])
        self._seen_event_ids: set[str] = {event.event_id for event in self._events}

    def push(self, event: Phase3Event) -> bool:
        if event.event_id in self._seen_event_ids:
            return False
        self._seen_event_ids.add(event.event_id)
        self._events.append(event)
        return True

    def drain(self, *, limit: int | None = None) -> list[Phase3Event]:
        ordered = sorted(self._events, key=lambda event: (self._priority_rank(event.priority), event.ts, event.event_id), reverse=True)
        if limit is not None:
            ordered = ordered[:limit]
        drained_ids = {event.event_id for event in ordered}
        self._events = [event for event in self._events if event.event_id not in drained_ids]
        return ordered

    @staticmethod
    def _priority_rank(priority: Priority) -> int:
        return {"low": 0, "normal": 1, "high": 2}[priority]


class Phase3InjectionBuffer:
    """Simple injection buffer mirroring runner injection_callback semantics."""

    def __init__(self) -> None:
        self._items: deque[dict[str, Any]] = deque()

    def push(self, message: dict[str, Any]) -> None:
        self._items.append(message)

    async def drain(self, limit: int = 10) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        while self._items and len(items) < limit:
            items.append(self._items.popleft())
        return items

    def __len__(self) -> int:
        return len(self._items)


class Phase3HeartbeatController:
    """Deterministic heartbeat reader inspired by nanobot.HeartbeatService."""

    def __init__(self, heartbeat_file: Path) -> None:
        self.heartbeat_file = heartbeat_file

    def read(self) -> str | None:
        if not self.heartbeat_file.exists():
            return None
        try:
            return self.heartbeat_file.read_text(encoding="utf-8")
        except Exception:
            return None

    def decide(self, content: str) -> tuple[HeartbeatAction, str]:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        action = "skip"
        tasks: list[str] = []
        for line in lines:
            lower = line.lower()
            if lower.startswith("action:"):
                value = lower.split(":", 1)[1].strip()
                if value in {"run", "skip"}:
                    action = value  # type: ignore[assignment]
            elif lower.startswith("tasks:"):
                tasks.append(line.split(":", 1)[1].strip())
        if action == "run" and not tasks:
            tasks = [content.strip()]
        return action, "\n".join(tasks)

    def trigger_now(self) -> tuple[HeartbeatAction, str] | None:
        content = self.read()
        if not content:
            return None
        return self.decide(content)


class Phase3MonitorHook:
    """Monitor hook that consumes events and mutates the current plan."""

    def __init__(
        self,
        event_queue: Phase3EventQueue,
        injection_buffer: Phase3InjectionBuffer,
        *,
        max_replans: int = 2,
        alternate_drop: dict[str, Any] | None = None,
    ) -> None:
        self.event_queue = event_queue
        self.injection_buffer = injection_buffer
        self.max_replans = max_replans
        self.alternate_drop = alternate_drop or {"x": 2.4, "y": 0.9, "z": 0.0, "timeout_s": 20}
        self.tool_events: list[dict[str, str]] = []
        self.replanning_count = 0
        self._deduped_event_ids: set[str] = set()
        self.pending_drop_override: dict[str, Any] | None = None

    async def before_iteration(self, context: Phase3HookContext) -> None:
        for event in self.event_queue.drain():
            if event.event_id in self._deduped_event_ids:
                continue
            self._deduped_event_ids.add(event.event_id)
            await self._handle_event(context, event)

    async def before_execute_tools(self, context: Phase3HookContext) -> None:
        for tool_call in context.tool_calls:
            self.tool_events.append(
                {
                    "name": tool_call.get("name", "unknown"),
                    "status": "start",
                    "detail": context.messages[-1].get("content", "") if context.messages else "",
                }
            )

    async def after_iteration(self, context: Phase3HookContext) -> None:
        if context.error:
            self.tool_events.append(
                {
                    "name": "iteration",
                    "status": "error",
                    "detail": context.error,
                }
            )
        if context.stop_reason:
            self.tool_events.append(
                {
                    "name": "iteration",
                    "status": context.stop_reason,
                    "detail": context.final_content or context.error or "",
                }
            )

    async def _handle_event(self, context: Phase3HookContext, event: Phase3Event) -> None:
        if event.type == "safety_alert":
            context.stop_reason = "ABORTED"
            context.final_content = event.content
            context.error = event.content
            context.injections.append(
                {
                    "role": "system",
                    "content": f"Safety alert received: {event.content}",
                    "meta": {"event_id": event.event_id, "priority": event.priority},
                }
            )
            return

        injection = {
            "role": "user",
            "content": event.content,
            "meta": {"event_id": event.event_id, "priority": event.priority, "type": event.type},
        }
        context.injections.append(injection)
        self.injection_buffer.push(injection)
        if event.type in {"new_instruction", "priority_change"}:
            self.pending_drop_override = parse_instruction_target(event.content) or dict(self.alternate_drop)
            context.tool_events.append(
                {
                    "name": "monitor",
                    "status": "injected",
                    "detail": event.content,
                }
            )

    def apply_replan_policy(
        self,
        state: PlanState,
        step: PlanStep,
        response: dict[str, Any],
        events: list[Phase3Event],
    ) -> tuple[str, list[PlanStep]]:
        if state.replanning_count >= state.max_replans:
            state.status = "FAILED"
            state.stop_reason = "max_replan_attempts_exceeded"
            return "abort", []

        error_code = response.get("error_code")
        if error_code == "OBSTRUCTED" and step.tool == "place":
            state.replanning_count += 1
            updated_step = PlanStep(step_id=f"{step.step_id}-replan", tool="place", args=dict(self.alternate_drop))
            state.history.append({"action": "replan_place", "step_id": step.step_id, "alternate": dict(self.alternate_drop)})
            return "replan", [updated_step]

        if error_code in {"GRIP_FAIL", "TIMEOUT"} and step.tool == "pick":
            state.replanning_count += 1
            state.history.append({"action": "retry_pick", "step_id": step.step_id, "error_code": error_code})
            return "replan", [step]

        if error_code == "NOT_REACHABLE" and step.tool == "move_to":
            state.replanning_count += 1
            safe_waypoint = PlanStep(
                step_id=f"{step.step_id}-waypoint",
                tool="move_to",
                args={"x": 1.0, "y": 0.0, "z": 0.0, "speed": 0.15, "timeout_s": 20},
            )
            state.history.append({"action": "insert_waypoint", "step_id": step.step_id})
            return "replan", [safe_waypoint, step]

        if any(event.type == "priority_change" for event in events):
            state.replanning_count += 1
            state.history.append({"action": "priority_replan", "step_id": step.step_id, "events": [event.event_id for event in events]})
            return "replan", [PlanStep(step_id=f"{step.step_id}-priority", tool="place", args=dict(self.alternate_drop))]

        if any(event.type == "new_instruction" for event in events):
            state.replanning_count += 1
            state.history.append({"action": "instruction_replan", "step_id": step.step_id, "events": [event.event_id for event in events]})
            return "replan", [PlanStep(step_id=f"{step.step_id}-instruction", tool="place", args=dict(self.alternate_drop))]

        state.status = "FAILED"
        state.stop_reason = error_code or "unrecoverable_error"
        return "abort", []


def parse_instruction_target(content: str) -> dict[str, Any] | None:
    text = content.lower()
    if "b2" in text or "备用" in content or "alternate" in text:
        return {"x": 2.4, "y": 0.9, "z": 0.0, "timeout_s": 20}
    if "b1" in text:
        return {"x": 2.0, "y": 0.5, "z": 0.0, "timeout_s": 20}
    return None
