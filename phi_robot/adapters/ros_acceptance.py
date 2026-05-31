"""
ROS2 acceptance adapter.

Bridges the RobotAdapter protocol to ROS2 services provided by colleagues:
  - move_to  → /start_navigation (trajectory_json → success)
  - pick     → /set_lift → /request_replay
  - place    → /notify_goal_reached → /set_lay_down → /request_replay → /set_stand
  - get_pose → returns minimal ok (no pose feedback needed)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import rclpy
from robot_interfaces.srv import ExecuteTrajectory
from std_srvs.srv import SetBool, Trigger

logger = logging.getLogger("phi_robot.ros_acceptance")

_READY_POLL_INTERVAL_S = 0.5
_READY_TIMEOUT_S = 120.0


class RosAcceptanceAdapter:
    """Acceptance-test adapter that calls ROS2 services for path-planning and locomotion actions."""

    def __init__(
        self,
        path_plan_service: str = "/start_navigation",
        lift_service: str = "/set_lift",
        lay_down_service: str = "/set_lay_down",
        stand_service: str = "/set_stand",
        request_replay_service: str = "/request_replay",
        notify_goal_reached_service: str = "/notify_goal_reached",
        timeout_s: float = 60.0,
    ):
        self._timeout_s = timeout_s
        self._path_plan_service = path_plan_service

        if not rclpy.ok():
            rclpy.init()
        self._node = rclpy.create_node("phi_robot_acceptance")

        self._path_plan_client = self._node.create_client(ExecuteTrajectory, path_plan_service)
        self._lift_client = self._node.create_client(SetBool, lift_service)
        self._lay_down_client = self._node.create_client(SetBool, lay_down_service)
        self._stand_client = self._node.create_client(Trigger, stand_service)
        self._request_replay_client = self._node.create_client(Trigger, request_replay_service)
        self._notify_goal_reached_client = self._node.create_client(SetBool, notify_goal_reached_service)
        self._get_mode_client = self._node.create_client(Trigger, "/get_locomotion_mode")

        logger.info("ROS2 node ready — path_plan=%s, lift=%s, lay_down=%s, stand=%s, replay=%s, notify_goal=%s",
                    "phi_robot_acceptance", path_plan_service, lift_service, lay_down_service, stand_service,
                    request_replay_service, notify_goal_reached_service)

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
        if tool == "move_to":
            return self._call_path_plan(args, request_id, goal_id, step_id)
        if tool in ("pick", "place"):
            return self._call_locomotion(tool, request_id, goal_id, step_id)
        # get_pose / get_gripper_state — not needed for acceptance test
        return {
            "status": "ok",
            "error_code": None,
            "message": "acceptance: skipped",
            "state": self._minimal_state(),
            "metrics": {},
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
        }

    def snapshot(self) -> dict[str, Any]:
        return self._minimal_state()

    def reset(self) -> None:
        pass

    def should_route_real_move_to(self, tool: str, args: dict[str, Any]) -> bool:
        """All move_to calls go through ROS — never inject local 'current' pose."""
        return True

    # ── internal ───────────────────────────────────────────────

    _READY_STATES = {"carry_wait_walk", "normal_planner", "goal_reached_locked"}
    _BLOCKED_STATES = {"carry_walking", "carry_standing_up", "motion_transition"}

    def _wait_until_ready(self, label: str):
        """Poll /get_locomotion_mode until robot in a ready state or timeout."""
        deadline = time.time() + _READY_TIMEOUT_S
        while time.time() < deadline:
            req = Trigger.Request()
            future = self._get_mode_client.call_async(req)
            rclpy.spin_until_future_complete(self._node, future, timeout_sec=5.0)
            if future.done():
                resp = future.result()
                try:
                    payload = json.loads(resp.message)
                    carry = payload.get("carry_state", "")
                    mode = int(payload.get("mode", -1))
                    posture = payload.get("posture_state", "")
                    hold = payload.get("hold_pose_active", False)
                    replay_active = payload.get("replay_active", False)
                    print(f"  [ros] poll  carry={carry}  mode={mode}  posture={posture}  "
                          f"hold={hold}  replay={replay_active}", flush=True)
                    if carry in self._READY_STATES:
                        # After pick, wait for hold_pose_active to ensure action settled
                        if label == "pick" and not hold:
                            print(f"  [ros] pick settling  hold_pose_active={hold}  waiting...", flush=True)
                        else:
                            print(f"  [ros] ready (after {label})", flush=True)
                            return True
                    if carry in self._BLOCKED_STATES:
                        print(f"  [ros] blocked  carry={carry}  waiting...", flush=True)
                except Exception:
                    pass
            time.sleep(_READY_POLL_INTERVAL_S)
        logger.warning("_wait_until_ready timed out after %.0fs for %s", _READY_TIMEOUT_S, label)
        return False

    def _call_path_plan(self, args, request_id, goal_id, step_id):
        target = args.get("target", {})
        tx = float(target.get("x", 0.0))
        ty = float(target.get("y", 0.0))

        trajectory = self._build_trajectory(tx, ty)
        req = ExecuteTrajectory.Request()
        req.trajectory_json = trajectory

        print(f"\n  [ros] >>> {self._path_plan_service}  "
              f"target=({tx:.1f}, {ty:.1f})", flush=True)
        future = self._path_plan_client.call_async(req)
        rclpy.spin_until_future_complete(self._node, future, timeout_sec=self._timeout_s)

        if not future.done():
            logger.error("path-planning call timed out for (%.1f, %.1f)", tx, ty)
            return self._error_result("PATH_PLAN_TIMEOUT",
                                     f"timed out: ({tx}, {ty})",
                                     request_id, goal_id, step_id)

        result = future.result()
        print(f"  [ros] <<< success={result.success}", flush=True)

        if result.success:
            self._wait_until_ready("move_to")

        return {
            "status": "ok" if result.success else "error",
            "error_code": None if result.success else "PATH_PLAN_FAILED",
            "message": "arrived" if result.success else "failed",
            "pose": {"x": tx, "y": ty, "z": 0.0, "theta": 0.0},
            "metrics": {},
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
        }

    @staticmethod
    def _build_trajectory(tx: float, ty: float) -> str:
        """Build a coordinate-format trajectory JSON for /start_navigation."""
        return json.dumps({
            "target_x": tx,
            "target_y": ty,
            "yaw": 0.0,
            "walk": True,
        })

    def _call_locomotion(self, tool, request_id, goal_id, step_id):
        if tool == "pick":
            return self._pick_sequence(request_id, goal_id, step_id)
        # place: notify → lay_down → replay → stand
        result = self._call_notify_goal_reached(request_id, goal_id, step_id)
        if result["status"] != "ok":
            return result
        return self._place_sequence(request_id, goal_id, step_id)

    def _pick_sequence(self, request_id, goal_id, step_id):
        result = self._call_set_lift(request_id, goal_id, step_id)
        if result["status"] != "ok":
            return result
        result = self._call_request_replay(request_id, goal_id, step_id)
        if result["status"] == "ok":
            self._wait_until_ready("pick")
        return result

    def _place_sequence(self, request_id, goal_id, step_id):
        result = self._call_set_lay_down(request_id, goal_id, step_id)
        if result["status"] != "ok":
            return result
        result = self._call_request_replay(request_id, goal_id, step_id)
        if result["status"] != "ok":
            return result
        result = self._call_set_stand(request_id, goal_id, step_id)
        if result["status"] == "ok":
            self._wait_until_ready("place")
        return result

    def _call_set_lift(self, request_id, goal_id, step_id):
        req = SetBool.Request()
        req.data = True
        print(f"\n  [ros] >>> /set_lift  data=true", flush=True)
        future = self._lift_client.call_async(req)
        rclpy.spin_until_future_complete(self._node, future, timeout_sec=self._timeout_s)

        if not future.done():
            logger.error("set_lift call timed out")
            return self._error_result("LIFT_TIMEOUT", "timed out: set_lift",
                                     request_id, goal_id, step_id)

        result = future.result()
        print(f"  [ros] <<< set_lift success={result.success}", flush=True)

        return {
            "status": "ok" if result.success else "error",
            "error_code": None if result.success else "LIFT_FAILED",
            "message": "lifted",
            "state": self._minimal_state(),
            "metrics": {},
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
        }

    def _call_set_lay_down(self, request_id, goal_id, step_id):
        req = SetBool.Request()
        req.data = True
        print(f"\n  [ros] >>> /set_lay_down  data=true", flush=True)
        future = self._lay_down_client.call_async(req)
        rclpy.spin_until_future_complete(self._node, future, timeout_sec=self._timeout_s)

        if not future.done():
            logger.error("set_lay_down call timed out")
            return self._error_result("LAY_DOWN_TIMEOUT", "timed out: set_lay_down",
                                     request_id, goal_id, step_id)

        result = future.result()
        print(f"  [ros] <<< set_lay_down success={result.success}", flush=True)

        return {
            "status": "ok" if result.success else "error",
            "error_code": None if result.success else "LAY_DOWN_FAILED",
            "message": "lay_down",
            "state": self._minimal_state(),
            "metrics": {},
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
        }

    def _call_set_stand(self, request_id, goal_id, step_id):
        req = Trigger.Request()
        print(f"\n  [ros] >>> /set_stand", flush=True)
        future = self._stand_client.call_async(req)
        rclpy.spin_until_future_complete(self._node, future, timeout_sec=self._timeout_s)

        if not future.done():
            logger.error("set_stand call timed out")
            return self._error_result("STAND_TIMEOUT", "timed out: set_stand",
                                     request_id, goal_id, step_id)

        result = future.result()
        print(f"  [ros] <<< set_stand success={result.success}", flush=True)

        return {
            "status": "ok" if result.success else "error",
            "error_code": None if result.success else "STAND_FAILED",
            "message": "stand",
            "state": self._minimal_state(),
            "metrics": {},
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
        }

    def _call_request_replay(self, request_id, goal_id, step_id):
        req = Trigger.Request()
        print(f"\n  [ros] >>> /request_replay", flush=True)
        future = self._request_replay_client.call_async(req)
        rclpy.spin_until_future_complete(self._node, future, timeout_sec=self._timeout_s)

        if not future.done():
            logger.error("request_replay call timed out")
            return self._error_result("REPLAY_TIMEOUT", "timed out: request_replay",
                                     request_id, goal_id, step_id)

        result = future.result()
        ok = result.success or "already finished" in (result.message or "") or "streamed transition is active" in (result.message or "")
        print(f"  [ros] <<< request_replay success={result.success}  ok={ok}  message={result.message[:80] if result.message else ''}", flush=True)

        return {
            "status": "ok" if ok else "error",
            "error_code": None if ok else "REPLAY_FAILED",
            "message": "replayed",
            "state": self._minimal_state(),
            "metrics": {},
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
        }

    def _call_notify_goal_reached(self, request_id, goal_id, step_id):
        req = SetBool.Request()
        req.data = True
        print(f"\n  [ros] >>> /notify_goal_reached  data=true", flush=True)
        future = self._notify_goal_reached_client.call_async(req)
        rclpy.spin_until_future_complete(self._node, future, timeout_sec=self._timeout_s)

        if not future.done():
            logger.error("notify_goal_reached call timed out")
            return self._error_result("NOTIFY_GOAL_TIMEOUT", "timed out: notify_goal_reached",
                                     request_id, goal_id, step_id)

        result = future.result()
        print(f"  [ros] <<< notify_goal_reached success={result.success}", flush=True)

        return {
            "status": "ok" if result.success else "error",
            "error_code": None if result.success else "NOTIFY_GOAL_FAILED",
            "message": "goal notified",
            "state": self._minimal_state(),
            "metrics": {},
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
        }

    @staticmethod
    def _error_result(error_code, message, request_id, goal_id, step_id):
        return {
            "status": "error",
            "error_code": error_code,
            "message": message,
            "state": {},
            "metrics": {},
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
        }

    @staticmethod
    def _minimal_state():
        return {
            "robot_pose": {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0},
            "holding": None,
            "boxes": {},
        }

    def shutdown(self):
        """Destroy node and shutdown rclpy."""
        self._node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass
