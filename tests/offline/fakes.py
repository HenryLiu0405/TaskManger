"""Standard-library-only fakes for the Phase 0/1 offline gate."""

from __future__ import annotations

import threading
from collections import defaultdict
from copy import deepcopy
from typing import Any, Mapping


class ScriptedRobotStub:
    """ROS-shaped synchronous adapter with recording and blocking controls."""

    def __init__(
        self,
        *,
        failures: Mapping[str, list[dict[str, Any]]] | None = None,
        block_tool: str | None = None,
    ) -> None:
        self.trace: list[dict[str, Any]] = []
        self.failures = {name: list(values) for name, values in (failures or {}).items()}
        self.block_tool = block_tool
        self.block_started = threading.Event()
        self.two_mutations_started = threading.Event()
        self.block_release = threading.Event()
        if block_tool is None:
            self.block_release.set()
        self._guard = threading.Lock()
        self._active = 0
        self.max_active_mutating_calls = 0
        self.calls = defaultdict(int)
        self.pose = {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0}
        self.holding: str | None = None
        self.selected_object_id = 7
        self.select_target_active = False
        self.reset_count = 0
        self.sonic_input_source = "ROS2"

    @property
    def names(self) -> list[str]:
        return [entry["name"] for entry in self.trace]

    def _record(self, name: str, args: Mapping[str, Any] | None = None, **extra: Any) -> None:
        with self._guard:
            self.trace.append({"name": name, "args": deepcopy(dict(args or {})), **extra})

    def execute(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        request_id: str,
        goal_id: str,
        step_id: str,
    ) -> dict[str, Any]:
        mutating = tool in {"move_to", "pick", "place"}
        with self._guard:
            if mutating:
                self._active += 1
                self.max_active_mutating_calls = max(self.max_active_mutating_calls, self._active)
                if self._active >= 2:
                    self.two_mutations_started.set()
            self.calls[tool] += 1
            call_index = self.calls[tool]
            self.trace.append({
                "name": tool,
                "args": deepcopy(args),
                "request_id": request_id,
                "goal_id": goal_id,
                "step_id": step_id,
            })
        try:
            if self.block_tool == tool:
                self.block_started.set()
                if not self.block_release.wait(timeout=5.0):
                    raise RuntimeError("test stub block was not released")

            scripted = self.failures.get(tool, [])
            if call_index <= len(scripted):
                return {
                    "status": "error",
                    "error_code": "SCRIPTED_FAILURE",
                    "message": "scripted failure",
                    "state": self.snapshot(),
                    "metrics": {},
                    **scripted[call_index - 1],
                }

            if tool == "move_to":
                self.pose = deepcopy(args["target"])
                return self._ok("arrived", request_id, pose=deepcopy(self.pose))
            if tool == "pick":
                self.holding = str(args["object_id"])
                return self._ok(f"picked {self.holding}", request_id)
            if tool == "place":
                self.holding = None
                self.pose = {
                    axis: args.get(axis, default)
                    for axis, default in (("x", 0.0), ("y", 0.0), ("z", 0.0), ("theta", 0.0))
                }
                return self._ok("placed", request_id)
            if tool in {"get_pose", "get_gripper_state"}:
                return self._ok("query", request_id)
            return {
                "status": "error",
                "error_code": "UNSUPPORTED_CAPABILITY",
                "message": f"unsupported {tool}",
                "state": self.snapshot(),
                "metrics": {},
            }
        finally:
            if mutating:
                with self._guard:
                    self._active -= 1

    def _ok(self, message: str, request_id: str, **extra: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "error_code": None,
            "message": message,
            "state": self.snapshot(),
            "metrics": {"stub": True},
            "request_id": request_id,
            **extra,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "robot_pose": deepcopy(self.pose),
            "pose": deepcopy(self.pose),
            "holding": self.holding,
            "boxes": {},
        }

    def reset(self) -> None:
        self.reset_count += 1
        self.pose = {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0}
        self.holding = None

    def reset_pick_observation(self) -> None:
        pass

    def select_target_public(
        self,
        select: bool,
        pick_x: float = 0.0,
        pick_y: float = 0.0,
        material_points_xy: list[float] | None = None,
        step_id: str = "",
    ) -> dict[str, Any]:
        name = "select_target" if select else "clear_target"
        self._record(name, {
            "pick_x": pick_x,
            "pick_y": pick_y,
            "material_points_xy": list(material_points_xy or []),
        }, step_id=step_id)
        self.select_target_active = select
        return {
            "success": True,
            "message": "selected" if select else "cleared",
            "matched_object_id": self.selected_object_id if select else -1,
        }

    def wait_fp_pose_ready(self, min_wait_s: float = 0.0, timeout_s: float = 1.0) -> bool:
        self._record("fresh_pose", {"min_wait_s": min_wait_s, "timeout_s": timeout_s})
        return self.select_target_active

    def pause_navigation(self) -> None:
        self._record("pause_navigation")

    def resume_navigation(self) -> None:
        self._record("resume_navigation")

    def stand_robot(self) -> dict[str, Any]:
        self._record("stand")
        return {"ok": True, "message": "standing"}

    def step_back_robot(self) -> dict[str, Any]:
        self._record("step_back")
        return {"ok": True, "message": "stepped back"}

    def get_robot_state(self) -> dict[str, Any]:
        return self.snapshot()

    def get_sonic_input_source(self) -> str:
        return self.sonic_input_source

    def set_sonic_input_source(self, gamepad: bool) -> dict[str, Any]:
        self.sonic_input_source = "GAMEPAD" if gamepad else "ROS2"
        self._record("set_input_source", {"gamepad": gamepad})
        return {
            "ok": True,
            "message": "input source updated",
            "active_source": self.sonic_input_source,
        }
