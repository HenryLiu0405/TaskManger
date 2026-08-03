"""
Move-to passthrough adapter for acceptance testing.

Only move_to targeting the 9-grid positions is forwarded to the real device.
Every other capability is rejected explicitly so an acceptance run cannot
report success for an action that never happened.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger("phi_robot.move_to_passthrough")

# Only 9-grid positions are valid move_to targets for the real device.
# Stock slots (y=3.5) are out of range and should be skipped.
REAL_MOVE_TO_TARGETS = {
    (0.5, 0.5),
    (1.5, 0.5),
    (2.5, 0.5),
    (0.5, 1.5),
    (1.5, 1.5),
    (2.5, 1.5),
    (0.5, 2.5),
    (1.5, 2.5),
    (2.5, 2.5),
}


class MoveToPassthroughAdapter:
    """Acceptance adapter: forward one scoped capability and reject the rest."""

    def __init__(self, move_to_url: str, timeout_s: float = 60.0):
        self.move_to_url = move_to_url.rstrip("/")
        self.timeout_s = timeout_s

    # ── RobotAdapter protocol ──────────────────────────────────

    def execute(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        request_id: str,
        goal_id: str,
        step_id: str,
    ) -> dict[str, Any]:
        if tool == "move_to" and self._is_grid_target(args):
            return self._forward_move_to(args)
        # pick / place / queries / stock-slot navigation are outside the
        # explicitly authorized acceptance surface.
        return {
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
            "status": "error",
            "error_code": "UNSUPPORTED_CAPABILITY",
            "message": f"acceptance adapter does not implement {tool} for this target",
            "state": self._minimal_state(),
            "metrics": {},
        }

    def snapshot(self) -> dict[str, Any]:
        return self._minimal_state()

    def reset(self) -> None:
        pass

    # Tells MissionRunner not to inject `current` only for 9-grid move_to.
    def should_route_real_move_to(self, tool: str, args: dict[str, Any]) -> bool:
        return tool == "move_to" and self._is_grid_target(args)

    # ── internal ───────────────────────────────────────────────

    @staticmethod
    def _is_grid_target(args: dict[str, Any]) -> bool:
        target = args.get("target")
        if not isinstance(target, dict):
            return False
        try:
            x = round(float(target.get("x")), 1)
            y = round(float(target.get("y")), 1)
        except (TypeError, ValueError):
            return False
        return (x, y) in REAL_MOVE_TO_TARGETS

    def _forward_move_to(self, args: dict[str, Any]) -> dict[str, Any]:
        target = args.get("target", {})
        payload = {
            "tool": "move_to",
            "args": {"target": target},
        }

        url = f"{self.move_to_url}/api/command"
        body = json.dumps(payload).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")

        print(f"\n  [acceptance] >>> POST {url}", flush=True)
        print(f"  [acceptance]     payload: {json.dumps(payload)}", flush=True)

        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                print(f"  [acceptance] <<< status={data.get('status')}  {data.get('message', '')}", flush=True)
                return data
        except URLError as e:
            logger.error("move_to endpoint unreachable at %s: %s", url, e)
            return {
                "status": "error",
                "error_code": "MOVE_TO_UNREACHABLE",
                "message": f"move_to endpoint unreachable: {e}",
                "metrics": {},
            }

    @staticmethod
    def _minimal_state() -> dict[str, Any]:
        return {
            "robot_pose": {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0},
            "holding": None,
            "boxes": {},
        }
