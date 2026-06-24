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
import threading
import time
from typing import Any

import rclpy
from rclpy.executors import MultiThreadedExecutor
from robot_interfaces.srv import ExecuteTrajectory
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool, Int32, String
from std_srvs.srv import SetBool, Trigger

from ..robot_state import safety_fsm, RobotState
from ..audit import audit_logger

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

        self._executor = MultiThreadedExecutor()
        self._executor.add_node(self._node)
        self._spin_thread = threading.Thread(target=self._spin_executor, daemon=True)
        self._spin_thread.start()

        # Pause / resume navigation (Topic, not Service)
        self._pause_nav_pub = self._node.create_publisher(Bool, "/pause_navigation", 10)

        # Locomotion mode publisher — keeps gateway mode fresh during navigation
        self._mode_pub = self._node.create_publisher(Int32, "/locomotion_mode", 10)
        self._mode_stop_event = threading.Event()
        self._mode_thread: Optional[threading.Thread] = None
        self._nav_status = "IDLE"
        def _on_nav_status(msg):
            self._nav_status = msg.data
        self._node.create_subscription(String, "/navigation_status", _on_nav_status, 10)

        # FoundationPose 状态 + 视频帧
        self._fp_state: dict[str, Any] = {}
        self._fp_rgb_jpeg: Optional[bytes] = None
        self._fp_depth_jpeg: Optional[bytes] = None
        self._fp_mask_jpeg: Optional[bytes] = None
        # GIL 下 bytes 引用赋值是原子的，无需锁

        from rclpy.qos import QoSProfile, ReliabilityPolicy
        _fp_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)

        def _on_fp_state(msg):
            try:
                self._fp_state = json.loads(msg.data)
            except Exception:
                pass
        self._node.create_subscription(String, "/fp_state", _on_fp_state, 10)

        # /fp_frame/compressed (旧单帧 topic) 已废弃，改为三个独立 CompressedImage topic
        def _on_fp_rgb(msg):
            self._fp_rgb_jpeg = msg.data

        def _on_fp_depth(msg):
            self._fp_depth_jpeg = msg.data

        def _on_fp_mask(msg):
            self._fp_mask_jpeg = msg.data

        self._node.create_subscription(
            CompressedImage, '/fp/rgb_overlay/compressed', _on_fp_rgb, _fp_qos)
        self._node.create_subscription(
            CompressedImage, '/fp/depth_colormap/compressed', _on_fp_depth, _fp_qos)
        self._node.create_subscription(
            CompressedImage, '/fp/mask/compressed', _on_fp_mask, _fp_qos)

        # place 操作容错标志
        self._box_released: bool = False

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
        # 安全检查 + 状态迁移
        if tool == "move_to":
            if not safety_fsm.can_walk():
                return self._error_result("PRECONDITION_FAILED",
                                         f"当前 {safety_fsm.state_value}, 不可走路",
                                         request_id, goal_id, step_id)
            # mode=2 (carry walk) when holding box, else mode=1 (normal walk)
            is_carrying = safety_fsm.state == RobotState.HOLDING
            safety_fsm.transition(RobotState.MOVING)
            result = self._call_path_plan(args, request_id, goal_id, step_id,
                                          mode=2 if is_carrying else 1)
            if result.get("status") == "ok":
                safety_fsm.transition(RobotState.ARRIVED)
            else:
                safety_fsm.transition(RobotState.STANDING)
            return result

        if tool == "pick":
            if not safety_fsm.can_pick():
                return self._error_result("PRECONDITION_FAILED",
                                         f"当前 {safety_fsm.state_value}, 不可搬起",
                                         request_id, goal_id, step_id)
            safety_fsm.transition(RobotState.PICKING)
            result = self._call_locomotion("pick", request_id, goal_id, step_id)
            if result.get("status") == "ok":
                safety_fsm.transition(RobotState.HOLDING)
            else:
                safety_fsm.to_error()
            return result

        if tool == "place":
            if not safety_fsm.can_place():
                return self._error_result("PRECONDITION_FAILED",
                                         f"当前 {safety_fsm.state_value}, 不可放下",
                                         request_id, goal_id, step_id)
            safety_fsm.transition(RobotState.PLACING)
            result = self._call_locomotion("place", request_id, goal_id, step_id)
            if result.get("status") == "ok":
                safety_fsm.transition(RobotState.STANDING)
            else:
                safety_fsm.to_error()
            return result

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

    def get_services_status(self) -> dict[str, bool]:
        """检查各 ROS2 服务在线状态（短超时，供控制台轮询）"""
        checks = [
            ("/start_navigation", self._path_plan_client),
            ("/set_lift", self._lift_client),
            ("/set_lay_down", self._lay_down_client),
            ("/set_stand", self._stand_client),
            ("/request_replay", self._request_replay_client),
            ("/notify_goal_reached", self._notify_goal_reached_client),
            ("/get_locomotion_mode", self._get_mode_client),
        ]
        status = {}
        for name, client in checks:
            try:
                status[name] = client.wait_for_service(timeout_sec=0.1)
            except Exception:
                status[name] = False
        # /pause_navigation is a Topic publisher, always available
        status["/pause_navigation"] = True
        return status

    def pause_navigation(self):
        """Publish Bool(data=True) to /pause_navigation — robot stops immediately."""
        msg = Bool()
        msg.data = True
        self._pause_nav_pub.publish(msg)
        logger.info("pause_navigation: published data=true")

    def resume_navigation(self):
        """Publish Bool(data=False) to /pause_navigation — robot resumes from saved path."""
        msg = Bool()
        msg.data = False
        self._pause_nav_pub.publish(msg)
        logger.info("pause_navigation: published data=false")

    def get_navigation_status(self) -> str:
        """Return cached /navigation_status value."""
        return self._nav_status

    def get_fp_state(self) -> dict[str, Any]:
        """获取 FoundationPose 最新状态."""
        return dict(self._fp_state)

    def get_fp_video_frame(self, channel: str) -> Optional[bytes]:
        """获取 FoundationPose 最新视频帧 (JPEG bytes).

        channel: 'rgb' | 'depth' | 'mask'
        """
        if channel == 'rgb':
            return self._fp_rgb_jpeg
        elif channel == 'depth':
            return self._fp_depth_jpeg
        elif channel == 'mask':
            return self._fp_mask_jpeg
        return None

    def get_robot_state(self) -> dict[str, Any]:
        """单次查询机器人状态（不阻塞等待），供调试控制台轮询"""
        try:
            req = Trigger.Request()
            future = self._get_mode_client.call_async(req)
            self._wait_future(future, 1.0)
            if future.done():
                resp = future.result()
                payload = json.loads(resp.message)
                return {
                    "carry_state": payload.get("carry_state", "--"),
                    "mode": int(payload.get("mode", -1)),
                    "posture_state": payload.get("posture_state", "--"),
                    "hold_pose_active": payload.get("hold_pose_active", False),
                    "replay_active": payload.get("replay_active", False),
                    "nav_status": self._nav_status,
                    "box_released": self._box_released,
                }
        except Exception:
            pass
        return {
            "carry_state": "--",
            "mode": -1,
            "posture_state": "--",
            "hold_pose_active": False,
            "replay_active": False,
            "nav_status": self._nav_status,
            "box_released": self._box_released,
        }

    # ── internal ───────────────────────────────────────────────

    def _spin_executor(self):
        try:
            self._executor.spin()
        except Exception:
            pass

    @staticmethod
    def _wait_future(future, timeout_sec):
        deadline = time.time() + timeout_sec
        while not future.done() and time.time() < deadline:
            time.sleep(0.05)
        return future.done()

    _READY_STATES = {"carry_wait_walk", "normal_planner", "goal_reached_locked"}
    _BLOCKED_STATES = {"carry_walking", "carry_standing_up", "motion_transition"}

    def _wait_until_ready(self, label: str):
        """Poll /get_locomotion_mode until robot in a ready state or timeout."""
        deadline = time.time() + _READY_TIMEOUT_S
        while time.time() < deadline:
            if self._nav_status == "PAUSED":
                deadline = time.time() + _READY_TIMEOUT_S
                time.sleep(_READY_POLL_INTERVAL_S)
                continue
            req = Trigger.Request()
            future = self._get_mode_client.call_async(req)
            self._wait_future(future, 5.0)
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

    def _wait_replay_done(self):
        """Poll until replay_active becomes false (for set_stand prerequisite)."""
        deadline = time.time() + _READY_TIMEOUT_S
        while time.time() < deadline:
            if self._nav_status == "PAUSED":
                deadline = time.time() + _READY_TIMEOUT_S
                time.sleep(_READY_POLL_INTERVAL_S)
                continue
            req = Trigger.Request()
            future = self._get_mode_client.call_async(req)
            self._wait_future(future, 5.0)
            if future.done():
                try:
                    resp = future.result()
                    payload = json.loads(resp.message)
                    replay_active = payload.get("replay_active", False)
                    if not replay_active:
                        print(f"  [ros] replay done", flush=True)
                        return True
                    print(f"  [ros] replay still active  waiting...", flush=True)
                except Exception:
                    pass
            time.sleep(_READY_POLL_INTERVAL_S)
        logger.warning("_wait_replay_done timed out after %.0fs", _READY_TIMEOUT_S)
        return False

    def _carry_is_goal_reached_locked(self) -> bool:
        """Check whether gateway is already in goal_reached_locked state."""
        try:
            req = Trigger.Request()
            future = self._get_mode_client.call_async(req)
            self._wait_future(future, 3.0)
            if future.done():
                resp = future.result()
                payload = json.loads(resp.message)
                return payload.get("carry_state", "") == "goal_reached_locked"
        except Exception:
            pass
        return False

    def _posture_is_stand(self) -> bool:
        """检查当前姿态是否已经是站立状态（避免冗余 set_stand 调用）."""
        try:
            req = Trigger.Request()
            future = self._get_mode_client.call_async(req)
            self._wait_future(future, 3.0)
            if future.done():
                resp = future.result()
                payload = json.loads(resp.message)
                posture = payload.get("posture_state", "")
                return posture in ("stand", "standing")
        except Exception:
            pass
        return False

    def _start_mode_publishing(self, mode: int):
        """Start a background thread that publishes /locomotion_mode at ~0.4s interval.

        The 0.4s interval keeps the value fresh inside the gateway's 0.8s mode_timeout_sec
        window, so ModeForPlanner() always sees the external mode rather than falling back
        to auto_mode_from_cmd_mode.
        """
        self._mode_stop_event.clear()
        def _loop():
            msg = Int32(data=mode)
            while not self._mode_stop_event.is_set():
                self._mode_pub.publish(msg)
                self._mode_stop_event.wait(0.4)
        self._mode_thread = threading.Thread(target=_loop, daemon=True)
        self._mode_thread.start()
        print(f"  [ros] mode pub started  /locomotion_mode={mode}", flush=True)

    def _stop_mode_publishing(self):
        """Stop the background mode-publishing thread."""
        self._mode_stop_event.set()
        if self._mode_thread is not None:
            self._mode_thread.join(timeout=1.0)
            self._mode_thread = None
            print(f"  [ros] mode pub stopped", flush=True)

    def _notify_arrival(self):
        """Tell gateway the robot has arrived at the navigation goal.

        For carry walk (carry_walking), this transitions to goal_reached_locked
        so _wait_until_ready can return. For normal walk (normal_planner), the
        gateway rejects the call — we ignore that.
        """
        try:
            req = SetBool.Request()
            req.data = True
            future = self._notify_goal_reached_client.call_async(req)
            self._wait_future(future, 3.0)
            if future.done():
                resp = future.result()
                if resp.success:
                    print(f"  [ros] notify_goal_reached ok → goal_reached_locked", flush=True)
                else:
                    print(f"  [ros] notify_goal_reached skipped  "
                          f"(gateway says: {resp.message[:60] if resp.message else 'no message'})", flush=True)
        except Exception:
            pass

    def _call_path_plan(self, args, request_id, goal_id, step_id, mode=1):
        t0 = time.time()
        target = args.get("target", {})
        tx = float(target.get("x", 0.0))
        ty = float(target.get("y", 0.0))

        self._start_mode_publishing(mode)
        try:
            trajectory = self._build_trajectory(tx, ty)
            req = ExecuteTrajectory.Request()
            req.trajectory_json = trajectory

            print(f"\n  [ros] >>> {self._path_plan_service}  "
                  f"target=({tx:.1f}, {ty:.1f})", flush=True)

            future = self._path_plan_client.call_async(req)
            ok = self._wait_future(future, self._timeout_s)
            elapsed = (time.time() - t0) * 1000

            if not ok:
                logger.error("path-planning call timed out for (%.1f, %.1f)", tx, ty)
                audit_logger.log_ros2_call(service=self._path_plan_service, duration_ms=elapsed,
                                           success=False, message="timed out")
                return self._error_result("PATH_PLAN_TIMEOUT",
                                         f"timed out: ({tx}, {ty})",
                                         request_id, goal_id, step_id)

            result = future.result()
            print(f"  [ros] <<< success={result.success}", flush=True)
            audit_logger.log_ros2_call(service=self._path_plan_service, duration_ms=elapsed,
                                       success=result.success, message=str(result.success))

            if not result.success:
                return self._error_result("PATH_PLAN_FAILED",
                                         f"start_navigation returned success=false for ({tx:.1f}, {ty:.1f})",
                                         request_id, goal_id, step_id)
        finally:
            self._stop_mode_publishing()

        # Notify gateway that the robot has arrived.
        # For carry walk: gateway transitions carry_walking → goal_reached_locked.
        # For normal walk: gateway rejects (not in carry pipeline), harmless.
        self._notify_arrival()

        ready = self._wait_until_ready("move_to")
        if not ready:
            return self._error_result("PATH_PLAN_NOT_READY",
                                     f"robot did not reach ready state after move_to ({tx:.1f}, {ty:.1f})",
                                     request_id, goal_id, step_id)

        return {
            "status": "ok",
            "error_code": None,
            "message": "arrived",
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
        if result["status"] != "ok":
            return result
        ready = self._wait_until_ready("pick")
        if not ready:
            result["status"] = "error"
            result["error_code"] = "PICK_NOT_READY"
            result["message"] = "robot did not reach ready state after pick"
        return result

    def _place_sequence(self, request_id, goal_id, step_id):
        self._box_released = False
        result = self._call_set_lay_down(request_id, goal_id, step_id)
        if result["status"] != "ok":
            return result

        # 新 C++ gateway: /set_lay_down 内部已调用 TryStartRequestedReplay，
        # replay 在此处已经启动，无需再单独调 /request_replay。
        replay_done = self._wait_replay_done()
        if not replay_done:
            self._box_released = True
            return self._error_result("REPLAY_NOT_DONE",
                                      "replay did not finish after set_lay_down",
                                      request_id, goal_id, step_id)

        # 新 gateway FinishReplayOk 会将 posture_state 置为 "stand"
        if self._posture_is_stand():
            self._box_released = True
        else:
            result = self._call_set_stand(request_id, goal_id, step_id)
            if result["status"] != "ok":
                if result.get("error_code") in ("STAND_FAILED", "STAND_TIMEOUT"):
                    self._box_released = True
                return result
            self._box_released = True

        ready = self._wait_until_ready("place")
        if not ready:
            return self._error_result("PLACE_NOT_READY",
                                      "robot did not reach ready state after place",
                                      request_id, goal_id, step_id)
        return {
            "status": "ok",
            "error_code": None,
            "message": "placed",
            "state": self._minimal_state(),
            "metrics": {},
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
        }

    def _call_set_lift(self, request_id, goal_id, step_id):
        t0 = time.time()
        req = SetBool.Request()
        req.data = True
        print(f"\n  [ros] >>> /set_lift  data=true", flush=True)
        future = self._lift_client.call_async(req)
        ok = self._wait_future(future, self._timeout_s)
        elapsed = (time.time() - t0) * 1000

        if not ok:
            logger.error("set_lift call timed out")
            audit_logger.log_ros2_call(service="/set_lift", duration_ms=elapsed,
                                       success=False, message="timed out")
            return self._error_result("LIFT_TIMEOUT", "timed out: set_lift",
                                     request_id, goal_id, step_id)

        result = future.result()
        print(f"  [ros] <<< set_lift success={result.success}", flush=True)
        audit_logger.log_ros2_call(service="/set_lift", duration_ms=elapsed,
                                   success=result.success, message=str(result.success))

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
        t0 = time.time()
        req = SetBool.Request()
        req.data = True
        print(f"\n  [ros] >>> /set_lay_down  data=true", flush=True)
        future = self._lay_down_client.call_async(req)
        ok = self._wait_future(future, self._timeout_s)
        elapsed = (time.time() - t0) * 1000

        if not ok:
            logger.error("set_lay_down call timed out")
            audit_logger.log_ros2_call(service="/set_lay_down", duration_ms=elapsed,
                                       success=False, message="timed out")
            return self._error_result("LAY_DOWN_TIMEOUT", "timed out: set_lay_down",
                                     request_id, goal_id, step_id)

        result = future.result()
        print(f"  [ros] <<< set_lay_down success={result.success}", flush=True)
        audit_logger.log_ros2_call(service="/set_lay_down", duration_ms=elapsed,
                                   success=result.success, message=str(result.success))

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
        t0 = time.time()
        req = Trigger.Request()
        print(f"\n  [ros] >>> /set_stand", flush=True)
        future = self._stand_client.call_async(req)
        ok = self._wait_future(future, self._timeout_s)
        elapsed = (time.time() - t0) * 1000

        if not ok:
            logger.error("set_stand call timed out")
            audit_logger.log_ros2_call(service="/set_stand", duration_ms=elapsed,
                                       success=False, message="timed out")
            return self._error_result("STAND_TIMEOUT", "timed out: set_stand",
                                     request_id, goal_id, step_id)

        result = future.result()
        print(f"  [ros] <<< set_stand success={result.success}", flush=True)
        audit_logger.log_ros2_call(service="/set_stand", duration_ms=elapsed,
                                   success=result.success, message=result.message or str(result.success))

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
        t0 = time.time()
        req = Trigger.Request()
        print(f"\n  [ros] >>> /request_replay", flush=True)
        future = self._request_replay_client.call_async(req)
        ok = self._wait_future(future, self._timeout_s)
        elapsed = (time.time() - t0) * 1000

        if not ok:
            logger.error("request_replay call timed out")
            audit_logger.log_ros2_call(service="/request_replay", duration_ms=elapsed,
                                       success=False, message="timed out")
            return self._error_result("REPLAY_TIMEOUT", "timed out: request_replay",
                                     request_id, goal_id, step_id)

        result = future.result()
        success_flag = result.success or "already finished" in (result.message or "") or "streamed transition is active" in (result.message or "") or "replay already active" in (result.message or "")
        print(f"  [ros] <<< request_replay success={result.success}  ok={success_flag}  message={result.message[:80] if result.message else ''}", flush=True)
        audit_logger.log_ros2_call(service="/request_replay", duration_ms=elapsed,
                                   success=success_flag, message=(result.message or "")[:200])

        return {
            "status": "ok" if success_flag else "error",
            "error_code": None if success_flag else "REPLAY_FAILED",
            "message": "replayed",
            "state": self._minimal_state(),
            "metrics": {},
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
        }

    def _call_notify_goal_reached(self, request_id, goal_id, step_id):
        # If carry walk already notified (via _notify_arrival), gateway is already
        # in goal_reached_locked — skip the redundant call instead of getting rejected.
        if self._carry_is_goal_reached_locked():
            print(f"  [ros] notify_goal_reached skipped  carry already goal_reached_locked", flush=True)
            return {
                "status": "ok",
                "error_code": None,
                "message": "goal already notified",
                "state": self._minimal_state(),
                "metrics": {},
                "request_id": request_id,
                "goal_id": goal_id,
                "step_id": step_id,
            }

        t0 = time.time()
        req = SetBool.Request()
        req.data = True
        print(f"\n  [ros] >>> /notify_goal_reached  data=true", flush=True)
        future = self._notify_goal_reached_client.call_async(req)
        ok = self._wait_future(future, self._timeout_s)
        elapsed = (time.time() - t0) * 1000

        if not ok:
            logger.error("notify_goal_reached call timed out")
            audit_logger.log_ros2_call(service="/notify_goal_reached", duration_ms=elapsed,
                                       success=False, message="timed out")
            return self._error_result("NOTIFY_GOAL_TIMEOUT", "timed out: notify_goal_reached",
                                     request_id, goal_id, step_id)

        result = future.result()
        print(f"  [ros] <<< notify_goal_reached success={result.success}", flush=True)
        audit_logger.log_ros2_call(service="/notify_goal_reached", duration_ms=elapsed,
                                   success=result.success, message=str(result.success))

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
        self._executor.shutdown()
        self._node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass
