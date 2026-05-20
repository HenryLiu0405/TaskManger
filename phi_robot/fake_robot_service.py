"""Fake robot service used to validate agentic control flow without hardware.

The service exposes the same conceptual contract that a real robot backend would
provide, but keeps all state in memory and supports deterministic fault injection.
It is intentionally self-contained and depends only on the Python standard library.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Status = Literal["ok", "error"]


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    z: float = 0.0
    theta: float = 0.0

    def distance_to(self, other: "Pose") -> float:
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2 + (self.z - other.z) ** 2) ** 0.5


@dataclass(frozen=True)
class RectangleObstacle:
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def contains(self, pose: Pose) -> bool:
        return self.x_min <= pose.x <= self.x_max and self.y_min <= pose.y <= self.y_max


@dataclass
class BoxState:
    box_id: str
    pose: Pose
    held: bool = False


@dataclass(frozen=True)
class FaultRule:
    """Deterministic fault injection rule.

    A rule matches a tool on a specific call index. If ``repeat`` is True, the
    rule keeps firing for every later call index too.
    """

    tool: str
    call_index: int
    error_code: str
    message: str
    repeat: bool = False
    args_match: dict[str, Any] = field(default_factory=dict)

    def matches(self, tool: str, call_index: int, args: dict[str, Any]) -> bool:
        if self.tool != tool:
            return False
        if self.repeat:
            if call_index < self.call_index:
                return False
        elif call_index != self.call_index:
            return False
        for key, value in self.args_match.items():
            if args.get(key) != value:
                return False
        return True


@dataclass
class ActionRecord:
    request_id: str
    goal_id: str
    step_id: str
    tool: str
    args: dict[str, Any]
    response: dict[str, Any]


@dataclass
class WorldState:
    robot_pose: Pose = field(default_factory=lambda: Pose(0.0, 0.0, 0.0, 0.0))
    holding: str | None = None
    boxes: dict[str, BoxState] = field(default_factory=dict)
    obstacles: list[RectangleObstacle] = field(default_factory=list)


class FakeRobotService:
    """An in-memory robot backend that simulates move/pick/place style actions."""

    def __init__(
        self,
        *,
        world: WorldState,
        fault_rules: list[FaultRule] | None = None,
        pose_tolerance: float = 0.05,
    ) -> None:
        self.world = world
        self.fault_rules = list(fault_rules or [])
        self.pose_tolerance = pose_tolerance
        self._tool_calls = defaultdict(int)
        self._history: list[ActionRecord] = []

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> "FakeRobotService":
        boxes = {
            box_id: BoxState(box_id=box_id, pose=Pose(**box_spec["pose"]), held=box_spec.get("held", False))
            for box_id, box_spec in spec.get("boxes", {}).items()
        }
        obstacles = [RectangleObstacle(**item) for item in spec.get("obstacles", [])]
        world = WorldState(
            robot_pose=Pose(**spec.get("robot_pose", {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0})),
            holding=spec.get("holding"),
            boxes=boxes,
            obstacles=obstacles,
        )
        faults = [FaultRule(**item) for item in spec.get("fault_rules", [])]
        svc = cls(world=world, fault_rules=faults)
        svc._spec = spec
        return svc

    def reset(self) -> None:
        """重置模拟环境到初始状态"""
        spec = getattr(self, "_spec", None)
        if spec is None:
            return
        boxes = {
            box_id: BoxState(box_id=box_id, pose=Pose(**box_spec["pose"]), held=box_spec.get("held", False))
            for box_id, box_spec in spec.get("boxes", {}).items()
        }
        obstacles = [RectangleObstacle(**item) for item in spec.get("obstacles", [])]
        self.world = WorldState(
            robot_pose=Pose(**spec.get("robot_pose", {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0})),
            holding=spec.get("holding"),
            boxes=boxes,
            obstacles=obstacles,
        )
        self._tool_calls = defaultdict(int)
        self._history: list[ActionRecord] = []

    @property
    def history(self) -> list[dict[str, Any]]:
        return [asdict(record) for record in self._history]

    @property
    def call_counters(self) -> dict[str, int]:
        return dict(self._tool_calls)

    def snapshot(self) -> dict[str, Any]:
        return {
            "robot_pose": asdict(self.world.robot_pose),
            "holding": self.world.holding,
            "boxes": {
                box_id: {"box_id": box.box_id, "pose": asdict(box.pose), "held": box.held}
                for box_id, box in self.world.boxes.items()
            },
            "obstacles": [asdict(obstacle) for obstacle in self.world.obstacles],
        }

    def _next_call_index(self, tool: str) -> int:
        self._tool_calls[tool] += 1
        return self._tool_calls[tool]

    def _fault_response(self, request_id: str, tool: str, call_index: int, error_code: str, message: str) -> dict[str, Any]:
        response = {
            "request_id": request_id,
            "status": "error",
            "error_code": error_code,
            "message": message,
            "state": self._state_payload(),
            "metrics": {"latency_ms": 1 + call_index * 7},
        }
        self._history.append(
            ActionRecord(
                request_id=request_id,
                goal_id="",
                step_id="",
                tool=tool,
                args={},
                response=response,
            )
        )
        return response

    def _match_fault(self, tool: str, call_index: int, args: dict[str, Any]) -> FaultRule | None:
        for rule in self.fault_rules:
            if rule.matches(tool, call_index, args):
                return rule
        return None

    def _state_payload(self) -> dict[str, Any]:
        return {
            "pose": asdict(self.world.robot_pose),
            "holding": self.world.holding,
            "boxes": {
                box_id: {"box_id": box.box_id, "pose": asdict(box.pose), "held": box.held}
                for box_id, box in self.world.boxes.items()
            },
        }

    def _inside_obstacle(self, pose: Pose) -> bool:
        return any(obstacle.contains(pose) for obstacle in self.world.obstacles)

    def _find_box(self, box_id: str) -> BoxState | None:
        return self.world.boxes.get(box_id)

    def execute(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        request_id: str = "req-local",
        goal_id: str = "goal-local",
        step_id: str = "step-local",
    ) -> dict[str, Any]:
        call_index = self._next_call_index(tool)
        fault = self._match_fault(tool, call_index, args)
        if fault:
            response = self._fault_response(request_id, tool, call_index, fault.error_code, fault.message)
            self._history[-1] = ActionRecord(
                request_id=request_id,
                goal_id=goal_id,
                step_id=step_id,
                tool=tool,
                args=dict(args),
                response=response,
            )
            return response

        if tool == "get_pose":
            response = self._ok_response(request_id, tool, call_index, "pose returned")
        elif tool == "get_gripper_state":
            response = self._ok_response(request_id, tool, call_index, self.world.holding or "open")
        elif tool == "move_to":
            response = self._move_to(request_id, call_index, args)
        elif tool == "pick":
            response = self._pick(request_id, call_index, args)
        elif tool == "place":
            response = self._place(request_id, call_index, args)
        else:
            response = self._error_response(
                request_id,
                tool,
                call_index,
                "INVALID_ARGS",
                f"Unsupported tool: {tool}",
            )

        self._history.append(
            ActionRecord(
                request_id=request_id,
                goal_id=goal_id,
                step_id=step_id,
                tool=tool,
                args=dict(args),
                response=response,
            )
        )
        return response

    def _ok_response(self, request_id: str, tool: str, call_index: int, message: str) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "status": "ok",
            "error_code": None,
            "message": message,
            "state": self._state_payload(),
            "metrics": {"latency_ms": 1 + call_index * 7, "sim_time_ms": 0},
        }

    def _error_response(
        self,
        request_id: str,
        tool: str,
        call_index: int,
        error_code: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "status": "error",
            "error_code": error_code,
            "message": message,
            "state": self._state_payload(),
            "metrics": {"latency_ms": 1 + call_index * 7},
        }

    def _move_to(self, request_id: str, call_index: int, args: dict[str, Any]) -> dict[str, Any]:
        # validate top-level required fields
        for key in ("target", "current", "action", "timeout_s"):
            if key not in args:
                return self._error_response(request_id, "move_to", call_index, "INVALID_ARGS", f"missing {key}")

        if args["action"] != "start":
            return self._error_response(request_id, "move_to", call_index, "INVALID_ARGS", f"unknown action: {args['action']}")

        target_raw = args["target"]
        current_raw = args["current"]

        for label, raw in (("target", target_raw), ("current", current_raw)):
            if not isinstance(raw, dict):
                return self._error_response(request_id, "move_to", call_index, "INVALID_ARGS", f"{label} must be an object")
            for axis in ("x", "y", "z"):
                if axis not in raw:
                    return self._error_response(request_id, "move_to", call_index, "INVALID_ARGS", f"missing {label}.{axis}")

        try:
            target = Pose(
                float(target_raw["x"]), float(target_raw["y"]),
                float(target_raw.get("z", 0.0)), float(target_raw.get("theta", 0.0)),
            )
        except (TypeError, ValueError):
            return self._error_response(request_id, "move_to", call_index, "INVALID_ARGS", "target pose must be numeric")

        if self._inside_obstacle(target):
            return self._error_response(
                request_id, "move_to", call_index, "NOT_REACHABLE", "target blocked by obstacle",
            )

        self.world.robot_pose = target
        return {
            "request_id": request_id,
            "status": "ok",
            "error_code": None,
            "message": "arrived",
            "pose": {"x": target.x, "y": target.y, "z": target.z, "theta": target.theta},
            "metrics": {"latency_ms": 1 + call_index * 7},
        }

    def _pick(self, request_id: str, call_index: int, args: dict[str, Any]) -> dict[str, Any]:
        required = {"object_id", "timeout_s"}
        missing = sorted(required - set(args))
        if missing:
            return self._error_response(request_id, "pick", call_index, "INVALID_ARGS", f"missing {missing}")

        object_id = str(args["object_id"])
        box = self._find_box(object_id)
        if box is None:
            return self._error_response(request_id, "pick", call_index, "INVALID_ARGS", f"unknown object_id {object_id}")
        if self.world.holding is not None:
            return self._error_response(request_id, "pick", call_index, "PRECONDITION_FAILED", "gripper already holding object")
        if box.held:
            return self._error_response(request_id, "pick", call_index, "PRECONDITION_FAILED", "object already held")
        if self.world.robot_pose.distance_to(box.pose) > self.pose_tolerance:
            return self._error_response(request_id, "pick", call_index, "PRECONDITION_FAILED", "robot not at object pose")

        box.held = True
        self.world.holding = object_id
        return self._ok_response(request_id, "pick", call_index, f"picked {object_id}")

    def _place(self, request_id: str, call_index: int, args: dict[str, Any]) -> dict[str, Any]:
        required = {"x", "y", "z", "timeout_s"}
        missing = sorted(required - set(args))
        if missing:
            return self._error_response(request_id, "place", call_index, "INVALID_ARGS", f"missing {missing}")
        if self.world.holding is None:
            return self._error_response(request_id, "place", call_index, "PRECONDITION_FAILED", "nothing to place")

        try:
            target = Pose(float(args["x"]), float(args["y"]), float(args.get("z", 0.0)), float(args.get("theta", 0.0)))
        except (TypeError, ValueError):
            return self._error_response(request_id, "place", call_index, "INVALID_ARGS", "pose must be numeric")

        if self._inside_obstacle(target):
            return self._error_response(request_id, "place", call_index, "OBSTRUCTED", "target blocked by obstacle")

        box_id = self.world.holding
        box = self._find_box(box_id)
        if box is None:
            return self._error_response(request_id, "place", call_index, "INTERNAL_ERROR", f"missing held box {box_id}")

        box.held = False
        box.pose = target
        self.world.robot_pose = target
        self.world.holding = None
        return self._ok_response(request_id, "place", call_index, f"placed {box_id}")


def contract_for(tool: str) -> dict[str, Any]:
    """Return the tool contract for a given fake robot action."""
    contracts = {
        "move_to": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "z": {"type": "number"},
                        "theta": {"type": "number"},
                    },
                    "required": ["x", "y", "z"],
                },
                "current": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "z": {"type": "number"},
                        "theta": {"type": "number"},
                    },
                    "required": ["x", "y", "z"],
                },
                "action": {"type": "string", "enum": ["start"]},
                "timeout_s": {"type": "number", "minimum": 0.001},
            },
            "required": ["target", "current", "action", "timeout_s"],
        },
        "pick": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "grip_force": {"type": "number"},
                "timeout_s": {"type": "number", "minimum": 0.001},
            },
            "required": ["object_id", "timeout_s"],
        },
        "place": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "z": {"type": "number"},
                "object_id": {"type": "string"},
                "timeout_s": {"type": "number", "minimum": 0.001},
            },
            "required": ["x", "y", "z", "timeout_s"],
        },
        "get_pose": {"type": "object", "properties": {}, "required": []},
        "get_gripper_state": {"type": "object", "properties": {}, "required": []},
    }
    if tool not in contracts:
        raise KeyError(tool)
    return contracts[tool]


DEFAULT_FAKE_SPEC: dict[str, Any] = {
    "robot_pose": {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0},
    "holding": None,
    "boxes": {
        f"box-{i:02d}": {"pose": {"x": 0.5 + i * 0.5, "y": 3.5, "z": 0.0, "theta": 0.0}}
        for i in range(9)
    },
    "obstacles": [],
    "fault_rules": [],
}
