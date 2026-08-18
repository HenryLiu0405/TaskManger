"""
ROS2 acceptance adapter.

Bridges the RobotAdapter protocol to ROS2 services provided by colleagues:
  - move_to  → /start_navigation (trajectory_json → success)
  - pick     → /submit_carry_task (single-object pose → gateway state machine)
  - place    → /notify_goal_reached → /set_lay_down → /set_stand
  - get_pose/get_gripper_state → unsupported until a reliable source is wired
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import threading
import time
import uuid
from typing import Any, Callable

import rclpy
from rclpy.executors import MultiThreadedExecutor

from nav_msgs.msg import Odometry
from robot_interfaces.srv import ExecuteTrajectory
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool, Int32, String
from std_srvs.srv import SetBool, Trigger

from ..robot_state import safety_fsm, RobotState
from ..audit import audit_logger
from ..foundation_pose_contract import (
    FoundationPoseObservation,
    FoundationPoseSelectionEpoch,
    reject_foundation_pose_observation,
)
from ..recovery_diag import (
    DiagEvent,
    EVT_STEP_START,
    make_fp_snapshot, make_odom_snapshot, make_step_result,
    make_select_target, make_drop_status, make_drop_detector_state,
)

logger = logging.getLogger("phi_robot.ros_acceptance")

try:
    from foundationpose_ros2.srv import SelectTarget, Activate
    _HAS_SELECT_TARGET = True
except ImportError:
    SelectTarget = None
    Activate = None
    _HAS_SELECT_TARGET = False

try:
    from foundationpose_ros2.srv import Reset
    _HAS_RESET = True
except ImportError:
    Reset = None
    _HAS_RESET = False
    logger.info("foundationpose_ros2.srv.SelectTarget/Activate not available; calls disabled")

try:
    from foundationpose_ros2.msg import PoseEstimate, PoseEstimateArray
    _HAS_POSE_ESTIMATE = True
except ImportError:
    PoseEstimate = None
    PoseEstimateArray = None
    _HAS_POSE_ESTIMATE = False
    logger.info("foundationpose_ros2.msg.PoseEstimate not available; pose subscription disabled")

try:
    from gear_sonic_interfaces.srv import SubmitCarryTask
    _HAS_SUBMIT_CARRY = True
except ImportError:
    SubmitCarryTask = None
    _HAS_SUBMIT_CARRY = False
    logger.info("gear_sonic_interfaces.srv.SubmitCarryTask not available; carry task submission disabled")

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
        camera_host: str = "192.168.50.79",
    ):
        self._timeout_s = timeout_s
        self._path_plan_service = path_plan_service
        self._camera_host = camera_host
        self._carry_target_frame = os.getenv("WAIC_CARRY_TARGET_FRAME", "pelvis").strip() or "pelvis"
        try:
            stale_after_s = float(os.getenv("WAIC_FP_POSE_STALE_AFTER_S", "1.5"))
        except ValueError:
            logger.warning("invalid WAIC_FP_POSE_STALE_AFTER_S; using 1.5 seconds")
            stale_after_s = 1.5
        self._fp_pose_stale_after_s = max(stale_after_s, 0.1)

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
        self._step_back_client = self._node.create_client(Trigger, "/Step_back")

        # 🆕 SONIC 输入源切换（ROS2 自动 ↔ Gamepad 人工接管）
        self._set_input_source_client = self._node.create_client(
            SetBool, '/control/set_input_source')
        self._sonic_input_source: str = "ROS2"  # 默认自动控制

        def _on_input_source_state(msg):
            try:
                data = json.loads(msg.data)
                self._sonic_input_source = data.get("active_source", "ROS2")
            except Exception:
                pass
        self._node.create_subscription(
            String, '/control/input_source_state', _on_input_source_state, 10)

        # 🆕 FoundationPose SelectTarget 服务（v3: 多物体选择移交 FP）
        if _HAS_SELECT_TARGET:
            self._select_target_client = self._node.create_client(
                SelectTarget, '/foundationpose/select_target')
            self._activate_client = self._node.create_client(
                Activate, '/foundationpose/activate')
        else:
            self._select_target_client = None
            self._activate_client = None

        # FP Reset（独立，不影响 SelectTarget/Activate）
        if _HAS_RESET:
            self._reset_client = self._node.create_client(
                Reset, '/foundationpose/reset')
        else:
            self._reset_client = None
            logger.info("foundationpose_ros2.srv.Reset not available; reset calls disabled")

        # 🆕 掉箱检测订阅（v3: drop_detector_node → /vision/box_drop_status）
        self._box_drop_detected: bool = False
        self._drop_event_sequence: int = 0
        self._drop_callback: Callable[[dict[str, Any]], None] | None = None
        self._diag_callback: Callable[[DiagEvent], None] | None = None
        self._current_step_id: str = ""
        self._drop_odom_x: float = 0.0   # 掉落瞬间里程计 x（用于 replan_target）
        self._drop_odom_y: float = 0.0   # 掉落瞬间里程计 y
        self._drop_enable_pub = self._node.create_publisher(
            Bool, '/vision/drop_detector_enable', 10)
        self._drop_detector_process: subprocess.Popen | None = None  # 按需拉起的节点进程

        def _on_box_drop_status(msg: Bool):
            prev = self._box_drop_detected
            self._box_drop_detected = not msg.data  # data:true=在, data:false=掉落
            if not msg.data and not prev:
                self._drop_event_sequence += 1
                detected_at = time.time()
                self._drop_odom_x = getattr(self, "_odom_x", 0.0)
                self._drop_odom_y = getattr(self, "_odom_y", 0.0)
                logger.error("⚠ 掉箱检测：箱子掉落！/vision/box_drop_status → false "
                             "odom=(%.2f, %.2f)，交由 Supervisor 请求停止",
                             self._drop_odom_x, self._drop_odom_y)
                self._emit_diag(make_drop_status(
                    getattr(self, "_current_step_id", ""), False,
                    self._drop_odom_x, self._drop_odom_y))
                if self._drop_callback is not None:
                    try:
                        self._drop_callback({
                            "event_id": f"drop-{uuid.uuid4().hex}",
                            "sequence": self._drop_event_sequence,
                            "detected_at": detected_at,
                            "detector_health": "healthy",
                            "box_present": False,
                            "odometry": (
                                {
                                    "x": self._drop_odom_x,
                                    "y": self._drop_odom_y,
                                    "yaw": getattr(self, "_odom_yaw", 0.0),
                                    "received_at": getattr(self, "_odom_received_at", 0.0),
                                }
                                if getattr(self, "_odom_received_at", 0.0) > 0.0
                                else None
                            ),
                        })
                    except Exception:
                        logger.exception("drop callback failed")
            elif msg.data and prev:
                logger.info("掉箱检测恢复: 箱子重新出现在画面中")
                self._emit_diag(make_drop_status(
                    getattr(self, "_current_step_id", ""), True,
                    getattr(self, "_odom_x", 0.0), getattr(self, "_odom_y", 0.0)))

        self._node.create_subscription(
            Bool, '/vision/box_drop_status', _on_box_drop_status, 10)

        self._executor = MultiThreadedExecutor()
        self._executor.add_node(self._node)
        self._spin_thread = threading.Thread(target=self._spin_executor, daemon=True)
        self._spin_thread.start()

        # ── 导航控制 ──
        self._navigation_stop_event = threading.Event()
        # /nav_pause: 暂停/恢复命令（上游 → Nav2）
        #   True  = 暂停（速度归零，状态保持，恢复后断点继续）
        #   False = 恢复（从断点继续导航）
        self._pause_nav_pub = self._node.create_publisher(Bool, "/nav_pause", 10)

        # /nav_reached: 到达通知（Nav2 → 上游）
        #   False = 收到新目标，导航中
        #   True  = 到达目标（位置<0.35m + 朝向<±10° + 持续1.5s），锁死零速
        #   上游收到 True 后可做后续动作（下发新目标 / pick / place）
        self._nav_reached: bool = False
        def _on_nav_reached(msg: Bool):
            prev = self._nav_reached
            self._nav_reached = msg.data
            if prev != self._nav_reached:
                logger.info("nav_reached: %s → %s (%s)",
                            prev, self._nav_reached,
                            "到达" if self._nav_reached else "导航中")
        self._node.create_subscription(Bool, "/nav_reached", _on_nav_reached, 10)

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
        self._fp_state_received_at: float = 0.0
        self._fp_state_sequence: int = 0
        self._fp_tracker_session_id = f"fp-session-{uuid.uuid4().hex}"
        try:
            self._fp_session_gap_s = max(
                float(os.getenv("WAIC_FP_SESSION_GAP_S", "3.0")), 0.5
            )
        except ValueError:
            self._fp_session_gap_s = 3.0
        self._fp_rgb_jpeg: Optional[bytes] = None
        self._fp_depth_jpeg: Optional[bytes] = None
        self._fp_mask_jpeg: Optional[bytes] = None
        self._drop_vis_jpeg: Optional[bytes] = None  # drop_detector 可视化画面
        self._fp_frame_samples: dict[str, dict[str, Any]] = {}
        self._fp_frame_sequences: dict[str, int] = {}
        # GIL 下 bytes 引用赋值是原子的，无需锁

        # 里程计 — 订阅 /odom (nav_msgs/Odometry)，缓存最新位姿
        self._odom_x: float = 0.0
        self._odom_y: float = 0.0
        self._odom_yaw: float = 0.0
        self._odom_linear_speed: float = 0.0
        self._odom_angular_speed: float = 0.0
        self._odom_received_at: float = 0.0
        self._odom_sequence: int = 0

        def _on_odom(msg: Odometry) -> None:
            self._odom_x = msg.pose.pose.position.x
            self._odom_y = msg.pose.pose.position.y
            q = msg.pose.pose.orientation
            # quaternion → yaw
            siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
            cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
            self._odom_yaw = math.atan2(siny_cosp, cosy_cosp)
            twist = msg.twist.twist
            self._odom_linear_speed = math.hypot(
                float(twist.linear.x), float(twist.linear.y)
            )
            self._odom_angular_speed = abs(float(twist.angular.z))
            self._odom_received_at = time.time()
            self._odom_sequence += 1
        self._node.create_subscription(Odometry, "/odom", _on_odom, 10)


        from rclpy.qos import QoSProfile, ReliabilityPolicy
        _fp_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)

        def _on_fp_state(msg):
            try:
                received_at = time.time()
                if (
                    self._fp_state_received_at > 0.0
                    and received_at - self._fp_state_received_at > self._fp_session_gap_s
                ):
                    # A provider outage/restart may reuse numeric tracker IDs.
                    # Treat any material state-stream gap as a new ID namespace.
                    self._fp_tracker_session_id = f"fp-session-{uuid.uuid4().hex}"
                    if hasattr(self, "_fp_target_object_id"):
                        self._fp_target_object_id = None
                        self._latest_pose_result = None
                        self._target_selection_pending = False
                self._fp_state = json.loads(msg.data)
                self._fp_state_received_at = received_at
                self._fp_state_sequence += 1
            except Exception:
                pass
        self._node.create_subscription(String, "/fp_state", _on_fp_state, 10)

        # /fp_frame/compressed (旧单帧 topic) 已废弃，改为三个独立 CompressedImage topic
        def _store_frame(channel: str, msg: CompressedImage) -> bytes:
            data = bytes(msg.data)
            sequence = self._fp_frame_sequences.get(channel, 0) + 1
            self._fp_frame_sequences[channel] = sequence
            stamp = getattr(getattr(msg, "header", None), "stamp", None)
            stamp_ns = (
                int(getattr(stamp, "sec", 0)) * 1_000_000_000
                + int(getattr(stamp, "nanosec", 0))
            )
            self._fp_frame_samples[channel] = {
                "data": data,
                # Receive time is used for freshness because deployments may
                # use ROS simulated time or clocks that are not synchronized.
                "captured_at": time.time(),
                "sequence": sequence,
                "content_type": "image/jpeg",
                "source_stamp_ns": stamp_ns,
            }
            return data

        def _on_fp_rgb(msg):
            self._fp_rgb_jpeg = _store_frame("rgb", msg)

        def _on_fp_depth(msg):
            self._fp_depth_jpeg = _store_frame("depth", msg)

        def _on_fp_mask(msg):
            self._fp_mask_jpeg = _store_frame("mask", msg)

        self._node.create_subscription(
            CompressedImage, '/fp/rgb_overlay/compressed', _on_fp_rgb, _fp_qos)
        self._node.create_subscription(
            CompressedImage, '/fp/depth_colormap/compressed', _on_fp_depth, _fp_qos)
        self._node.create_subscription(
            CompressedImage, '/fp/mask/compressed', _on_fp_mask, _fp_qos)

        def _on_drop_vis(msg):
            self._drop_vis_jpeg = _store_frame("drop", msg)
        self._node.create_subscription(
            CompressedImage, '/vision/drop_detector_vis/compressed', _on_drop_vis, _fp_qos)

        # place 操作容错标志
        self._box_released: bool = False

        # ── FP 验证用状态 ──
        # 最近一次 move_to 的目标坐标（Nav2 map 系），用作 odometry 的近似值
        self._last_move_target: tuple[float, float] = (0.0, 0.0)
        # 当前任务预期的物料点（Nav2 map 系），调用方通过 args 传入
        self._expected_slot_xy: tuple[float, float] | None = None
        # FP 选出的最优物体 ID（由 SelectTarget 服务 或 run_arrival_check 设置）
        self._fp_target_object_id: int | None = None
        # SelectTarget(select=true) 是否已激活单目标模式（需在搬起后/重规划前清理）
        self._select_target_active: bool = False
        self._local_id_selection_active: bool = False

        # ── 单物体位姿缓存（/foundationpose/pose_result → /submit_carry_task）──
        # ── 物体位姿缓存（→ /submit_carry_task）──
        # 多物体模式：遍历数组取第一个 TRACKING 物体
        # 单物体模式（SelectTarget 激活）：数组仅含锁定物体
        self._latest_pose_result = None  # PoseEstimate | None
        self._latest_pose_received_monotonic: float = 0.0
        self._target_selection_started_monotonic: float = 0.0
        self._target_selection_started_ros_ns: int = 0
        self._target_selection_pending: bool = False
        if _HAS_POSE_ESTIMATE:
            def _on_pose_results(msg: PoseEstimateArray):
                if self._target_selection_pending:
                    return
                candidates = [obj for obj in msg.objects if obj.state >= 1]
                now_monotonic = time.monotonic()
                now_ros_ns = self._node.get_clock().now().nanoseconds
                epoch = self._foundation_pose_epoch()
                for obj in candidates:
                    stamp = getattr(getattr(obj, "header", None), "stamp", None)
                    stamp_ns = (
                        int(getattr(stamp, "sec", 0)) * 1_000_000_000
                        + int(getattr(stamp, "nanosec", 0))
                    )
                    observation = FoundationPoseObservation(
                        object_id=getattr(obj, "object_id", None),
                        frame_id=str(getattr(getattr(obj, "header", None), "frame_id", "") or ""),
                        stamp_ns=stamp_ns,
                        received_monotonic=now_monotonic,
                    )
                    if reject_foundation_pose_observation(
                        observation,
                        epoch,
                        now_ros_ns=now_ros_ns,
                        now_monotonic=now_monotonic,
                    ) is not None:
                        continue
                    self._latest_pose_result = obj
                    self._latest_pose_received_monotonic = now_monotonic
                    break
            self._node.create_subscription(
                PoseEstimateArray, '/foundationpose/pose_results', _on_pose_results, 10)

        # SubmitCarryTask 客户端（懒初始化，首次调用 /submit_carry_task 时创建）
        self._submit_carry_client = None

        # ── 分步调试诊断 ──
        self._current_step_id = ""

        logger.info(
            "ROS2 node ready — path_plan=%s, lift=%s, lay_down=%s, stand=%s, replay=%s, notify_goal=%s",
            path_plan_service,
            lift_service,
            lay_down_service,
            stand_service,
            request_replay_service,
            notify_goal_reached_service,
        )

    # ── 诊断回调（分步调试用） ──────────────────────────────

    def set_safety_bypass(self, enabled: bool) -> None:
        """运行时开关：启用/禁用安全状态机旁路。

        True  → 跳过所有 can_walk/can_pick/can_place 检查，transition 不校验。
        False → 恢复正常安全检查。
        """
        safety_fsm.set_bypass(enabled)

    def set_sonic_input_source(self, gamepad: bool) -> dict[str, Any]:
        """切换 SONIC 输入源。

        True  → 手柄接管 (GAMEPAD)，人工遥控。
        False → ROS2 自动控制，TaskManger 下发指令。

        返回: {"ok": True/False, "message": str, "active_source": str}
        """
        if not self._set_input_source_client.wait_for_service(timeout_sec=2.0):
            return {"ok": False, "message": "/control/set_input_source 服务不可用",
                    "active_source": self._sonic_input_source}

        req = SetBool.Request()
        req.data = gamepad

        try:
            future = self._set_input_source_client.call_async(req)
            if not self._wait_future(future, timeout_sec=3.0):
                return {"ok": False, "message": "set_input_source 调用超时",
                        "active_source": self._sonic_input_source}
            resp = future.result()
            source = "GAMEPAD" if gamepad else "ROS2"
            logger.warning("SONIC 输入源切换 → %s (success=%s)", source, resp.success)
            if not resp.success:
                return {
                    "ok": False,
                    "error_code": "INPUT_SOURCE_REJECTED",
                    "message": resp.message,
                    "active_source": self._sonic_input_source,
                }

            confirm_deadline = time.monotonic() + 2.0
            while (
                self._sonic_input_source != source
                and time.monotonic() < confirm_deadline
            ):
                time.sleep(0.05)
            confirmed = self._sonic_input_source == source
            return {
                "ok": confirmed,
                "error_code": None if confirmed else "VERIFICATION_FAILED",
                "message": (
                    resp.message
                    if confirmed
                    else f"input source service accepted but state did not confirm {source}"
                ),
                "active_source": self._sonic_input_source,
                "verification": {
                    "status": "passed" if confirmed else "failed",
                    "evidence": {
                        "requested_source": source,
                        "active_source": self._sonic_input_source,
                    },
                    "message": "input source state topic confirmation",
                },
            }
        except Exception as e:
            logger.exception("set_input_source 调用失败: %s", e)
            return {"ok": False, "message": str(e),
                    "active_source": self._sonic_input_source}

    def get_sonic_input_source(self) -> str:
        """查询当前 SONIC 输入源: "ROS2" | "GAMEPAD" | "unknown" """
        return self._sonic_input_source or "unknown"

    def set_diag_callback(self, cb: Callable[[DiagEvent], None] | None) -> None:
        """注册诊断事件回调。分步调试模式下由 StepDebugController 调用。

        传入 None 可取消回调（恢复正常模式，不产生诊断开销）。
        """
        self._diag_callback = cb

    def set_drop_callback(
        self, cb: Callable[[dict[str, Any]], None] | None
    ) -> None:
        """Register the autonomous local drop-event consumer."""
        self._drop_callback = cb

    def _emit_diag(self, event: DiagEvent) -> None:
        """向已注册的诊断回调推送事件（无回调时是空操作）"""
        if self._diag_callback is not None:
            try:
                self._diag_callback(event)
            except Exception:
                pass  # 诊断回调异常不应阻断正常流程

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
        # ── 诊断：记录步骤开始 ──
        self._current_step_id = step_id
        t_start = time.monotonic()
        self._emit_diag(DiagEvent(
            DiagEvent.now(), step_id, "", EVT_STEP_START,
            {"tool": tool, "args_summary": _summarize_args(tool, args)},
        ))

        def _finish(result: dict[str, Any]) -> dict[str, Any]:
            elapsed = time.monotonic() - t_start
            self._emit_diag(make_step_result(
                step_id,
                status=result.get("status", "ok"),
                error_code=result.get("error_code", "") or "",
                message=result.get("message", ""),
                elapsed_s=elapsed,
            ))
            return result

        # 安全检查 + 状态迁移
        if tool == "move_to":
            if not safety_fsm.can_walk():
                return _finish(self._error_result("PRECONDITION_FAILED",
                                         f"当前 {safety_fsm.state_value}, 不可走路",
                                         request_id, goal_id, step_id))
            # 记录目标坐标（用作 odometry 近似值）
            target = args.get("target", {})
            self._last_move_target = (
                float(target.get("x", 0.0)), float(target.get("y", 0.0)),
            )

            safety_fsm.transition(RobotState.MOVING)
            result = self._call_path_plan(args, request_id, goal_id, step_id)
            if result.get("status") == "ok":
                safety_fsm.transition(RobotState.ARRIVED)
            else:
                safety_fsm.transition(RobotState.STANDING)
            return _finish(result)

        if tool == "pick":
            if not safety_fsm.can_pick():
                return _finish(self._error_result("PRECONDITION_FAILED",
                                         f"当前 {safety_fsm.state_value}, 不可搬起",
                                         request_id, goal_id, step_id))
            safety_fsm.transition(RobotState.PICKING)
            result = self._call_locomotion("pick", request_id, goal_id, step_id)
            if result.get("status") == "ok":
                safety_fsm.transition(RobotState.HOLDING)
            else:
                safety_fsm.to_error()
            return _finish(result)

        if tool == "place":
            if not safety_fsm.can_place():
                return _finish(self._error_result("PRECONDITION_FAILED",
                                         f"当前 {safety_fsm.state_value}, 不可放下",
                                         request_id, goal_id, step_id))
            safety_fsm.transition(RobotState.PLACING)
            result = self._call_locomotion("place", request_id, goal_id, step_id)
            if result.get("status") == "ok":
                safety_fsm.transition(RobotState.STANDING)
            else:
                safety_fsm.to_error()
            return _finish(result)

        return _finish(self._error_result(
            "UNSUPPORTED_CAPABILITY",
            f"ROS acceptance adapter does not implement a reliable {tool} capability",
            request_id,
            goal_id,
            step_id,
        ))

    def snapshot(self) -> dict[str, Any]:
        return self._minimal_state()

    def reset(self) -> None:
        self._fp_target_object_id = None
        self._select_target_active = False
        self._local_id_selection_active = False
        self._latest_pose_result = None
        self._latest_pose_received_monotonic = 0.0
        self._target_selection_started_monotonic = 0.0
        self._target_selection_started_ros_ns = 0
        self._target_selection_pending = False
        self._expected_slot_xy = None
        self._box_drop_detected = False
        self._drop_odom_x = 0.0
        self._drop_odom_y = 0.0
        self._current_carrying_object_id = None
        self._last_mission_slot_id = None
        self._nav_status = ""

    def should_route_real_move_to(self, tool: str, args: dict[str, Any]) -> bool:
        """All move_to calls go through ROS — never inject local 'current' pose."""
        return True

    def get_services_status(self) -> dict[str, bool]:
        """非阻塞读取 ROS2 服务发现状态，供控制台轮询。"""
        checks = [
            ("/start_navigation", self._path_plan_client),
            ("/set_lift", self._lift_client),
            ("/set_lay_down", self._lay_down_client),
            ("/set_stand", self._stand_client),
            ("/request_replay", self._request_replay_client),
            ("/notify_goal_reached", self._notify_goal_reached_client),
            ("/get_locomotion_mode", self._get_mode_client),
            ("/control/set_input_source", self._set_input_source_client),
        ]
        status = {}
        for name, client in checks:
            try:
                is_ready = getattr(client, "service_is_ready", None)
                status[name] = bool(
                    is_ready()
                    if callable(is_ready)
                    else client.wait_for_service(timeout_sec=0.0)
                )
            except Exception:
                status[name] = False
        # /pause_navigation is a Topic publisher, always available
        status["/pause_navigation"] = True
        return status

    def pause_navigation(self):
        """Publish Bool(data=True) to /nav_pause — 速度归零，状态保持，恢复后断点继续。

        与 /nav_reached 是两套独立机制：
          /nav_pause   = 暂停/恢复命令（上游 → Nav2）
          /nav_reached = 到达通知（Nav2 → 上游），属 nav_reached 属性
        """
        msg = Bool()
        msg.data = True
        self._pause_nav_pub.publish(msg)
        logger.info("pause_navigation: published data=true → /nav_pause")

    def stop_motion(
        self,
        *,
        robot_id: str = "",
        mission_id: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Request Nav pause and confirm a stable stop from fresh odometry.

        Publishing ``/nav_pause`` alone is only request acknowledgement.  This
        method waits for multiple new odometry samples whose pose delta and
        reported twist both remain below conservative thresholds.  If that
        evidence is unavailable, the caller receives ``confirmed=false``.
        """
        requested_at = time.time()
        self._navigation_stop_event.set()
        self.pause_navigation()
        try:
            timeout_s = max(float(os.getenv("WAIC_STOP_CONFIRM_TIMEOUT_S", "4.0")), 0.2)
            stable_for_s = max(float(os.getenv("WAIC_STOP_STABLE_FOR_S", "0.6")), 0.2)
            linear_limit = max(float(os.getenv("WAIC_STOP_LINEAR_MPS", "0.03")), 0.0)
            angular_limit = max(float(os.getenv("WAIC_STOP_ANGULAR_RPS", "0.05")), 0.0)
        except ValueError:
            timeout_s, stable_for_s, linear_limit, angular_limit = 4.0, 0.6, 0.03, 0.05

        deadline = time.monotonic() + timeout_s
        last_sequence = -1
        previous: tuple[float, float, float, float] | None = None
        stable_since: float | None = None
        last_evidence: dict[str, Any] = {}
        while time.monotonic() < deadline:
            sequence = getattr(self, "_odom_sequence", 0)
            if sequence == last_sequence:
                time.sleep(0.02)
                continue
            last_sequence = sequence
            received_at = getattr(self, "_odom_received_at", 0.0)
            if sequence <= 0 or received_at < requested_at:
                time.sleep(0.02)
                continue
            current = (
                received_at,
                getattr(self, "_odom_x", 0.0),
                getattr(self, "_odom_y", 0.0),
                getattr(self, "_odom_yaw", 0.0),
            )
            if previous is None:
                previous = current
                continue
            dt = max(current[0] - previous[0], 1e-6)
            pose_linear_speed = math.hypot(
                current[1] - previous[1], current[2] - previous[2]
            ) / dt
            yaw_delta = math.atan2(
                math.sin(current[3] - previous[3]),
                math.cos(current[3] - previous[3]),
            )
            pose_angular_speed = abs(yaw_delta) / dt
            reported_linear = getattr(self, "_odom_linear_speed", float("inf"))
            reported_angular = getattr(self, "_odom_angular_speed", float("inf"))
            stopped = (
                pose_linear_speed <= linear_limit
                and pose_angular_speed <= angular_limit
                and reported_linear <= linear_limit
                and reported_angular <= angular_limit
            )
            last_evidence = {
                "odom_sequence": sequence,
                "odom_received_at": received_at,
                "pose_linear_speed_mps": pose_linear_speed,
                "pose_angular_speed_rps": pose_angular_speed,
                "reported_linear_speed_mps": reported_linear,
                "reported_angular_speed_rps": reported_angular,
                "stable_for_required_s": stable_for_s,
            }
            now_monotonic = time.monotonic()
            if stopped:
                stable_since = stable_since or now_monotonic
                if now_monotonic - stable_since >= stable_for_s:
                    return {
                        "accepted": True,
                        "confirmed": True,
                        "motion_state": "stopped",
                        "message": "navigation pause and stable odometry stop confirmed",
                        "evidence": {
                            **last_evidence,
                            "robot_id": robot_id,
                            "mission_id": mission_id,
                            "reason": reason,
                        },
                    }
            else:
                stable_since = None
            previous = current
        return {
            "accepted": True,
            "confirmed": False,
            "motion_state": "unknown",
            "message": "navigation pause requested but stable stop was not confirmed",
            "evidence": {
                **last_evidence,
                "robot_id": robot_id,
                "mission_id": mission_id,
                "reason": reason,
            },
        }

    def resume_navigation(self):
        """Publish Bool(data=False) to /nav_pause — 从断点恢复导航。

        见 pause_navigation() 说明。
        """
        msg = Bool()
        msg.data = False
        self._pause_nav_pub.publish(msg)
        logger.info("resume_navigation: published data=false → /nav_pause")

    @property
    def nav_reached(self) -> bool:
        """Nav2 /nav_reached 到达通知: False=导航中, True=已到达且锁死零速。

        到达条件: 位置<0.35m + 朝向<±10° + 持续1.5s。
        上游收到 True 后可做后续动作（下发新目标/pick/place）。
        注意：这是状态通知，不是暂停命令。暂停用 pause_navigation() → /nav_pause。
        """
        return self._nav_reached

    def get_navigation_status(self) -> str:
        """Return cached /navigation_status value."""
        return self._nav_status

    # ── 掉箱处理（手动重规划） ─────────────────────────────────

    def enable_drop_detector(self) -> dict:
        """启动 drop_detector_node 子进程，启用掉箱检测。"""
        # 如果已有进程在跑，先杀掉
        if self._drop_detector_process is not None:
            self._kill_drop_detector_process()

        # 启动 drop_detector_node 子进程（继承当前 ROS2 环境）
        cmd = [
            'ros2', 'run', 'foundationpose_ros2', 'drop_detector_node',
            '--ros-args',
            '-p', f'camera_host:={self._camera_host}',
            '-p', 'enable_visualization:=true',
        ]
        try:
            self._drop_detector_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ},  # 继承当前环境（ROS_DOMAIN_ID 等）
            )
            logger.info("drop_detector_node 子进程已启动 (pid=%d)", self._drop_detector_process.pid)
        except Exception as e:
            logger.exception("drop_detector_node 启动失败: %s", e)
            return {"ok": False, "message": f"启动 drop_detector_node 失败: {e}"}

        # 等待节点初始化（发布者就绪）
        time.sleep(1.0)

        # 发布启用信号
        msg = Bool(data=True)
        self._drop_enable_pub.publish(msg)
        self._emit_diag(make_drop_detector_state(self._current_step_id, True))
        logger.info("drop_detector enabled (camera=%s)", self._camera_host)
        return {"ok": True, "message": f"drop detector started and enabled (camera={self._camera_host})"}

    def disable_drop_detector(self) -> dict:
        """禁用掉箱检测并终止 drop_detector_node 子进程。"""
        # 先发禁用信号
        msg = Bool(data=False)
        self._drop_enable_pub.publish(msg)
        self._emit_diag(make_drop_detector_state(self._current_step_id, False))

        # 杀掉子进程
        self._kill_drop_detector_process()
        logger.info("drop_detector disabled and process killed")
        return {"ok": True, "message": "drop detector disabled and process killed"}

    def _kill_drop_detector_process(self):
        """终止 drop_detector_node 子进程（先 SIGTERM，超时则 SIGKILL）。"""
        if self._drop_detector_process is None:
            return
        p = self._drop_detector_process
        self._drop_detector_process = None
        # 进程已自行退出
        if p.poll() is not None:
            logger.info("drop_detector_node 进程已自行退出 (pid=%d, rc=%d)", p.pid, p.returncode)
            return
        try:
            p.terminate()
            try:
                p.wait(timeout=3)
                logger.info("drop_detector_node 进程已终止 (pid=%d)", p.pid)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
                logger.warning("drop_detector_node 进程被强制杀死 (pid=%d)", p.pid)
        except Exception as e:
            logger.warning("终止 drop_detector_node 时出错: %s", e)

    def stand_robot(self) -> dict:
        """调用 /set_stand 服务让机器人站立（放下手臂）。"""
        return self._call_set_stand("replan", "replan", "replan-stand")

    def step_back_robot(self) -> dict:
        """调用 /Step_back 服务让机器人后退两步。"""
        t0 = time.time()
        req = Trigger.Request()
        print(f"\n  [ros] >>> /Step_back", flush=True)
        future = self._step_back_client.call_async(req)
        ok = self._wait_future(future, self._timeout_s)
        elapsed = (time.time() - t0) * 1000

        if not ok:
            logger.error("Step_back call timed out")
            audit_logger.log_ros2_call(service="/Step_back", duration_ms=elapsed,
                                       success=False, message="timed out")
            return {"ok": False, "message": "Step_back 超时"}

        result = future.result()
        print(f"  [ros] <<< Step_back success={result.success}", flush=True)
        audit_logger.log_ros2_call(service="/Step_back", duration_ms=elapsed,
                                   success=result.success,
                                   message=result.message or str(result.success))

        return {
            "ok": result.success,
            "message": result.message or ("后退完成" if result.success else "后退失败"),
        }

    def identify_dropped_box(
        self, material_points_xy: list[float] | None = None,
        target_points_xy: list[float] | None = None,
    ) -> dict:
        """
        调用 FP SelectTarget MODE_DROPPED=1 识别掉落箱子。

        Args:
            material_points_xy: 所有物料点 XY 扁平列表 [x1,y1,x2,y2,...]
            target_points_xy: 所有目标点 XY 扁平列表

        Returns:
            {"ok": True/False, "matched_object_id": int, "box_world_xy": [...],
             "message": str, "pose": {...} | None}
        """
        if material_points_xy is None:
            material_points_xy = [s.x for s in self._stock_slot_cache] if hasattr(self, '_stock_slot_cache') else []
        if target_points_xy is None:
            target_points_xy = []

        result = self._call_select_target(
            select=True, mode=1,  # MODE_DROPPED
            pick_x=0.0, pick_y=0.0,
            material_points_xy=material_points_xy,
            target_points_xy=target_points_xy,
            point_tolerance=1.0,
            step_id="replan-identify",
        )
        if result and result.get("success"):
            obj_id = result.get("matched_object_id", -1)
            box_xy = result.get("box_world_xy", [])
            logger.info("identify_dropped_box: found object_id=%s at %s", obj_id, box_xy)
            pose = None
            if obj_id >= 0 and self._latest_pose_result is not None:
                p = self._latest_pose_result
                pose = {"x": float(p.pose.position.x), "y": float(p.pose.position.y),
                        "z": float(p.pose.position.z),
                        "qx": float(p.pose.orientation.x), "qy": float(p.pose.orientation.y),
                        "qz": float(p.pose.orientation.z), "qw": float(p.pose.orientation.w)}
            return {"ok": True, "matched_object_id": obj_id, "box_world_xy": box_xy,
                    "pose": pose, "message": result.get("message", "")}
        else:
            return {"ok": False, "matched_object_id": -1, "box_world_xy": [],
                    "pose": None, "message": result.get("message", "") if result else "no result"}

    def replan_pick(self, request_id: str = "replan", goal_id: str = "replan",
                    step_id: str = "replan-pick") -> dict:
        """
        用缓存的掉落箱子位姿执行搬起操作。

        依赖 identify_dropped_box 先被调用以缓存位姿到 self._latest_pose_result。

        Returns:
            {"ok": True/False, "message": str, ...}
        """
        if self._latest_pose_result is None:
            return {"ok": False, "message": "no cached pose; call identify_dropped_box first"}
        self._select_target_active = True  # 标记为单物体模式
        result = self._call_submit_carry_task(request_id, goal_id, step_id)
        if result["status"] == "ok":
            ready = self._wait_until_ready("carry")
            if not ready:
                result["status"] = "error"
                result["error_code"] = "PICK_NOT_READY"
                result["message"] = "robot did not reach ready state after replan pick"
        self._cleanup_select_target(step_id)
        return result

    # ── 基础查询 ──────────────────────────────────────────────

    def get_fp_state(self) -> dict[str, Any]:
        """获取 FoundationPose 最新状态."""
        return dict(self._fp_state)

    def get_fp_state_sample(self) -> dict[str, Any]:
        """Return the cached tracker set with receive-time freshness metadata."""
        return {
            "state": dict(self._fp_state),
            "received_at": self._fp_state_received_at,
            "sequence": self._fp_state_sequence,
            "tracker_session_id": self._fp_tracker_session_id,
        }

    def get_tracker_session_id(self) -> str:
        """Process/reset scoped FoundationPose identity used by ObjectRef."""
        return self._fp_tracker_session_id

    def get_odom(self) -> dict[str, Any]:
        """获取最新里程计 (x, y, yaw_deg)."""
        return {
            "x": getattr(self, "_odom_x", 0.0),
            "y": getattr(self, "_odom_y", 0.0),
            "yaw_deg": round(
                math.degrees(getattr(self, "_odom_yaw", 0.0)), 1
            ),
        }

    def get_odom_sample(self) -> dict[str, Any] | None:
        """Return odometry only after a real message has been observed."""
        if self._odom_received_at <= 0.0:
            return None
        return {
            "x": self._odom_x,
            "y": self._odom_y,
            "yaw": self._odom_yaw,
            "linear_speed_mps": self._odom_linear_speed,
            "angular_speed_rps": self._odom_angular_speed,
            "received_at": self._odom_received_at,
            "sequence": self._odom_sequence,
            "frame_id": "odom",
        }

    def get_fp_video_frame(self, channel: str) -> Optional[bytes]:
        """获取 FoundationPose / drop_detector 最新视频帧 (JPEG bytes).

        channel: 'rgb' | 'depth' | 'mask' | 'drop'
        """
        if channel == 'rgb':
            return self._fp_rgb_jpeg
        elif channel == 'depth':
            return self._fp_depth_jpeg
        elif channel == 'mask':
            return self._fp_mask_jpeg
        elif channel == 'drop':
            return self._drop_vis_jpeg
        return None

    def get_fp_video_sample(self, channel: str) -> dict[str, Any] | None:
        """Return frame bytes plus receive timestamp and monotonic sequence.

        ``rgb`` is the existing FoundationPose RGB-overlay topic.  The
        autonomy observation source may expose the same sample as ``overlay``
        so deployments do not need another camera node merely for naming.
        """
        source_channel = "rgb" if channel == "overlay" else channel
        sample = self._fp_frame_samples.get(source_channel)
        if sample is None:
            return None
        return dict(sample)

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

    # ── FP verification helpers ────────────────────────────────

    def _estimated_odom(self) -> tuple[float, float, float]:
        """当前里程计位姿，来自 /odom 订阅。

        返回 (x, y, yaw) 在 Nav2 map 系下。
        yaw 由 orientation quaternion 转换，实时反映机器人朝向。
        """
        x, y, yaw = self._odom_x, self._odom_y, self._odom_yaw
        self._emit_diag(make_odom_snapshot(self._current_step_id, x, y, yaw))
        return (x, y, yaw)

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

    def _call_path_plan(self, args, request_id, goal_id, step_id):
        t0 = time.time()

        # A prior confirmed stop leaves Nav paused.  A new semantic navigation
        # invocation is the explicit authority to resume with a fresh goal.
        self._navigation_stop_event.clear()
        self.resume_navigation()

        target = args.get("target", {})
        tx = float(target.get("x", 0.0))
        ty = float(target.get("y", 0.0))
        theta = float(target.get("theta", math.pi / 2))

        trajectory = self._build_trajectory(tx, ty, yaw=theta)
        req = ExecuteTrajectory.Request()
        req.trajectory_json = trajectory

        # 先重置到达标志，再发起导航 — 防止短距离导航在 reset 之前就完成，
        # ROS2 spin 线程已把 _nav_reached 设为 True 然后被 reset 覆盖为 False
        self._nav_reached = False

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

        # 等待 nav_monitor 发布 /nav_reached=true（ZMQ bridge 回传达到通知）
        nav_timeout = 600.0  # 最长等 10 分钟
        arrived = self._wait_nav_reached(timeout=nav_timeout)
        if not arrived:
            if self._navigation_stop_event.is_set():
                return self._error_result(
                    "CANCELLED",
                    f"navigation stopped before reaching ({tx:.1f}, {ty:.1f})",
                    request_id,
                    goal_id,
                    step_id,
                )
            return self._error_result("NAV_TIMEOUT",
                                     f"nav_reached not received within {nav_timeout:.0f}s for ({tx:.1f}, {ty:.1f})",
                                     request_id, goal_id, step_id)

        # 通知 gateway 到达 — 搬箱走路(carry_walking)需要此调用切到 goal_reached_locked
        # 正常走路(normal_planner)时 gateway 会忽略，无副作用
        notify = self._call_notify_goal_reached(request_id, goal_id, step_id)
        if notify["status"] != "ok":
            return notify

        ready = self._wait_until_ready("move_to")
        if not ready:
            return self._error_result("PATH_PLAN_NOT_READY",
                                     f"robot did not reach ready state after move_to ({tx:.1f}, {ty:.1f})",
                                     request_id, goal_id, step_id)

        return {
            "status": "ok",
            "error_code": None,
            "message": "arrived",
            "pose": {"x": tx, "y": ty, "z": 0.0, "theta": theta},
            "verification": {
                "status": "passed",
                "evidence": {"nav_reached": True, "gateway_ready": True},
                "message": "fresh nav_reached and gateway ready observed",
            },
            "metrics": {},
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
        }

    def _wait_nav_reached(self, timeout: float = 600.0) -> bool:
        """Wait for /nav_reached topic to become True (Nav2 → bridge → workstation).

        Returns True when arrived, False on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._navigation_stop_event.is_set():
                logger.info("navigation wait interrupted by local stop request")
                return False
            if self._nav_reached:
                logger.info("nav_reached=True received, navigation complete")
                return True
            time.sleep(0.2)
        logger.warning("_wait_nav_reached timed out after %.0fs", timeout)
        return False

    @staticmethod
    def _build_trajectory(tx: float, ty: float, yaw: float | None = None) -> str:
        """Build a coordinate-format trajectory JSON for /start_navigation."""
        if yaw is None:
            yaw = math.pi / 2   # 默认朝向 y 轴正方向
        return json.dumps({
            "target_x": tx,
            "target_y": ty,
            "yaw": yaw,
            "walk": True,
        })

    # 🆕 Activate service wrapper (v3)
    def _call_activate(self, activate: bool, step_id: str = "") -> bool:
        """调 FP /foundationpose/activate 开启/关闭位姿估计。

        Returns:
            True — 调用成功
            False — 调用失败或 service 不可用
        """
        if self._activate_client is None:
            logger.warning("Activate client not available; skipping")
            return False

        req = Activate.Request()
        req.activate = activate

        if not self._activate_client.wait_for_service(timeout_sec=2.0):
            logger.error("Activate service not available")
            return False

        try:
            future = self._activate_client.call_async(req)
            if not self._wait_future(future, timeout_sec=3.0):
                logger.error("Activate call timed out")
                return False
            resp = future.result()
            logger.info("Activate(activate=%s) → success=%s", activate, resp.success)
            return resp.success
        except Exception as e:
            logger.exception("Activate call failed: %s", e)
            return False

    def _cleanup_select_target(self, step_id: str) -> None:
        """若 SelectTarget 单目标模式已激活，退出并恢复多物体模式。"""
        if self._select_target_active:
            result = self._call_select_target(select=False, step_id=step_id)
            if result and result.get("success"):
                self._select_target_active = False
            else:
                logger.error("SelectTarget cleanup was not confirmed; mode remains active")

    # 🆕 SelectTarget service wrapper (v3)
    def _call_select_target(self, select: bool, pick_x: float = 0.0,
                            pick_y: float = 0.0,
                            material_points_xy: list[float] | None = None,
                            target_points_xy: list[float] | None = None,
                            mode: int = 0, point_tolerance: float = 0.5,
                            step_id: str = "") -> dict[str, Any] | None:
        """调 FP /foundationpose/select_target 进入/退出单目标模式。

        Args:
            select: True=进入单目标模式, False=退出
            mode: 0=MODE_TARGET (取箱), 1=MODE_DROPPED (掉箱检测)
            point_tolerance: 判断"在点位上"的容差（米）

        Returns:
            dict with success/object_id 或 None（service 不可用时）
        """
        if self._select_target_client is None:
            logger.warning("SelectTarget client not available; skipping")
            return None

        req = SelectTarget.Request()
        req.mode = mode
        req.select = select
        req.pick_x = pick_x
        req.pick_y = pick_y
        req.material_points_xy = material_points_xy or []
        req.target_points_xy = target_points_xy or []
        req.point_tolerance = point_tolerance

        if not self._select_target_client.wait_for_service(timeout_sec=2.0):
            logger.error("SelectTarget service not available")
            return None

        try:
            future = self._select_target_client.call_async(req)
            if not self._wait_future(future, timeout_sec=3.0):
                logger.error("SelectTarget call timed out")
                return None
            resp = future.result()
            result = {
                "success": resp.success,
                "message": resp.message,
                "matched_object_id": resp.matched_object_id,
                "box_world_xy": list(resp.box_world_xy) if resp.box_world_xy else [],
                "box_object_ids": list(resp.box_object_ids) if resp.box_object_ids else [],
            }
            # ── 诊断 ──
            self._emit_diag(make_select_target(
                step_id, select, pick_x, pick_y,
                resp.matched_object_id, resp.success,
            ))
            if select and resp.success:
                self._fp_target_object_id = resp.matched_object_id
                self._target_selection_pending = False
                logger.info("SelectTarget(select=true) → object_id=%s", resp.matched_object_id)
            elif not select and resp.success:
                self._fp_target_object_id = None
                self._target_selection_pending = False
                self._emit_diag(make_drop_detector_state(
                    step_id, True, "SelectTarget(select=false)"))
                logger.info("SelectTarget(select=false) → 掉箱检测已恢复")
            return result
        except Exception as e:
            logger.exception("SelectTarget call failed: %s", e)
            return None

    # ── 公开入口（供 step_debug 手动切换用） ──

    def select_target_public(
        self,
        select: bool,
        pick_x: float = 0.0,
        pick_y: float = 0.0,
        material_points_xy: list[float] | None = None,
        step_id: str = "",
    ) -> dict[str, Any] | None:
        """手动切换 FoundationPose 单目标模式的公开入口。

        与 _call_select_target 完全相同的逻辑，仅暴露为公开方法。
        """
        if select:
            self.reset_pick_observation()
        result = self._call_select_target(
            select=select,
            pick_x=pick_x,
            pick_y=pick_y,
            material_points_xy=material_points_xy or [],
            point_tolerance=2.0,
            step_id=step_id,
        )
        if select and result and result.get("success"):
            self._select_target_active = True
        elif not select and result and result.get("success"):
            self._select_target_active = False
        return result

    def select_object_id_public(
        self,
        object_id: int,
        *,
        step_id: str = "",
    ) -> dict[str, Any]:
        """Bind the next pick pose to one explicit PoseEstimate object ID.

        The deployed SelectTarget service chooses by map coordinate and does
        not accept an object ID.  FoundationPose already publishes the full
        ``PoseEstimateArray``, so the TaskManger adapter can enforce the ID
        locally without changing or restarting the perception node.
        """
        if isinstance(object_id, bool):
            return {
                "success": False,
                "message": "FoundationPose object_id must be an integer",
                "matched_object_id": -1,
            }
        try:
            selected_id = int(object_id)
        except (TypeError, ValueError):
            return {
                "success": False,
                "message": "FoundationPose object_id must be an integer",
                "matched_object_id": -1,
            }
        if selected_id < 0:
            return {
                "success": False,
                "message": "FoundationPose object_id must be non-negative",
                "matched_object_id": -1,
            }
        self.reset_pick_observation()
        self._fp_target_object_id = selected_id
        self._target_selection_pending = False
        self._local_id_selection_active = True
        logger.info(
            "local FoundationPose object-ID selection → object_id=%s step=%s",
            selected_id,
            step_id,
        )
        return {
            "success": True,
            "message": "local object-ID selection epoch started",
            "matched_object_id": selected_id,
            "tracker_session_id": self._fp_tracker_session_id,
        }

    def clear_object_id_selection(self) -> dict[str, Any]:
        """Clear only the local multi-object PoseEstimate ID filter."""
        self._fp_target_object_id = None
        self._latest_pose_result = None
        self._latest_pose_received_monotonic = 0.0
        self._target_selection_pending = False
        self._local_id_selection_active = False
        return {"success": True, "message": "local object-ID selection cleared"}

    def get_selected_pose_identity(self) -> dict[str, Any] | None:
        """Describe the fresh pose that would be submitted to the gateway."""
        pose = self._latest_pose_result
        if pose is None or self._pose_rejection_reason(pose) is not None:
            return None
        stamp = getattr(getattr(pose, "header", None), "stamp", None)
        stamp_ns = (
            int(getattr(stamp, "sec", 0)) * 1_000_000_000
            + int(getattr(stamp, "nanosec", 0))
        )
        return {
            "provider": "foundationpose",
            "object_id": int(getattr(pose, "object_id")),
            "tracker_session_id": self._fp_tracker_session_id,
            "observation_id": (
                f"fp-pose-{stamp_ns}"
                if stamp_ns > 0
                else f"fp-pose-received-{self._latest_pose_received_monotonic:.9f}"
            ),
            "frame_id": str(
                getattr(getattr(pose, "header", None), "frame_id", "") or ""
            ),
            "received_monotonic": self._latest_pose_received_monotonic,
        }

    def reset_pick_observation(self) -> None:
        """Start a new target-observation epoch and discard stale cached poses."""
        self._latest_pose_result = None
        self._latest_pose_received_monotonic = 0.0
        self._target_selection_started_monotonic = time.monotonic()
        self._target_selection_started_ros_ns = self._node.get_clock().now().nanoseconds
        self._target_selection_pending = True

    def _foundation_pose_epoch(self) -> FoundationPoseSelectionEpoch:
        return FoundationPoseSelectionEpoch(
            object_id=self._fp_target_object_id,
            required_frame=self._carry_target_frame,
            started_ros_ns=self._target_selection_started_ros_ns,
            started_monotonic=self._target_selection_started_monotonic,
            stale_after_s=self._fp_pose_stale_after_s,
        )

    def _pose_rejection_reason(self, pose: Any) -> str | None:
        stamp = getattr(getattr(pose, "header", None), "stamp", None)
        observation = FoundationPoseObservation(
            object_id=getattr(pose, "object_id", None),
            frame_id=str(getattr(getattr(pose, "header", None), "frame_id", "") or ""),
            stamp_ns=(
                int(getattr(stamp, "sec", 0)) * 1_000_000_000
                + int(getattr(stamp, "nanosec", 0))
            ),
            received_monotonic=self._latest_pose_received_monotonic,
        )
        return reject_foundation_pose_observation(
            observation,
            self._foundation_pose_epoch(),
            now_ros_ns=self._node.get_clock().now().nanoseconds,
            now_monotonic=time.monotonic(),
        )

    def wait_fp_pose_ready(self, min_wait_s: float = 3.0,
                           timeout_s: float = 8.0) -> bool:
        """等待 FP 发布有效物体位姿（state >= IDLE）。

        move_to 后 FP 需要时间重新检测新位置的圆柱体，
        此方法先等待 min_wait_s 秒，再轮询 _latest_pose_result。

        Returns:
            True 表示已有有效位姿，False 表示超时。
        """
        deadline = time.time() + timeout_s
        # 最小等待缓冲：给 FP 视觉管道处理帧的时间
        time.sleep(min_wait_s)
        while time.time() < deadline:
            pose = self._latest_pose_result
            if (
                pose is not None
                and pose.state >= 1
                and not self._target_selection_pending
                and self._pose_rejection_reason(pose) is None
            ):
                logger.info(
                    "wait_fp_pose_ready: 就绪 (state=%d, object_id=%d, "
                    "elapsed=%.1fs)",
                    pose.state, pose.object_id,
                    timeout_s - (deadline - time.time()),
                )
                return True
            time.sleep(0.5)
        logger.warning(
            "wait_fp_pose_ready: 超时 (%.1fs), "
            "latest_pose=%s",
            timeout_s,
            "None" if self._latest_pose_result is None
            else f"state={self._latest_pose_result.state}",
        )
        return False

    def call_reset_fp(self) -> dict | None:
        """调 FP /foundationpose/reset 重新标定物体（清空所有 tracker 重新检测）"""
        if self._reset_client is None:
            logger.warning("Reset client not available; skipping")
            return None

        req = Reset.Request()
        if not self._reset_client.wait_for_service(timeout_sec=2.0):
            logger.error("Reset service not available")
            return None

        try:
            future = self._reset_client.call_async(req)
            if not self._wait_future(future, timeout_sec=3.0):
                logger.error("Reset call timed out")
                return None
            resp = future.result()
            logger.info("Reset → success=%s message=%s", resp.success, resp.message)
            if resp.success:
                # Provider IDs may be reused after reset; start a new identity
                # namespace so an old ObjectRef cannot silently select one.
                self._fp_tracker_session_id = f"fp-session-{uuid.uuid4().hex}"
                self.clear_object_id_selection()
            return {"success": resp.success, "message": resp.message}
        except Exception as e:
            logger.exception("Reset call failed: %s", e)
            return None

    def _call_locomotion(self, tool, request_id, goal_id, step_id):
        if tool == "pick":
            return self._pick_sequence(request_id, goal_id, step_id)
        # place: notify → lay_down → replay → stand
        result = self._call_notify_goal_reached(request_id, goal_id, step_id)
        if result["status"] != "ok":
            return result
        return self._place_sequence(request_id, goal_id, step_id)

    def _pick_sequence(self, request_id, goal_id, step_id):
        try:
            # ★ 新 C++ gateway: 一步完成 motion1（替代 set_lift + request_replay）
            # /submit_carry_task 接收单物体位姿（权威帧默认 pelvis），
            # gateway 自动选择 center/right/left/front 并执行搬起动捕
            result = self._call_submit_carry_task(request_id, goal_id, step_id)
            if result["status"] != "ok":
                return result
            ready = self._wait_until_ready("pick")
            if not ready:
                result["status"] = "error"
                result["error_code"] = "PICK_NOT_READY"
                result["message"] = "robot did not reach ready state after pick"
                return result

            result["verification"] = {
                "status": "passed",
                "evidence": {"gateway_ready": True, "hold_pose_active": True},
                "message": "gateway holding posture and ready state observed",
            }
            return result
        finally:
            self._cleanup_select_target(step_id)

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
            "verification": {
                "status": "passed",
                "evidence": {
                    "box_released": self._box_released,
                    "posture": "stand",
                    "gateway_ready": True,
                },
                "message": "release, standing posture, and gateway ready observed",
            },
            "metrics": {},
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
        }

    def _call_submit_carry_task(self, request_id, goal_id, step_id):
        """将缓存的单物体位姿提交给 C++ gateway 的 /submit_carry_task 服务。

        替代旧的两步调用 (_call_set_lift + _call_request_replay)。
        前提: 用户已通过 SelectTarget 进入单目标模式，
              FP 正在发布与 WAIC_CARRY_TARGET_FRAME 一致的位姿。

        返回: {"status": "ok"/"error", ...}
        """
        if not _HAS_SUBMIT_CARRY:
            return self._error_result(
                "UNSUPPORTED_CAPABILITY",
                "gear_sonic_interfaces 未安装，无法调用 /submit_carry_task",
                request_id, goal_id, step_id)

        pose_est = self._latest_pose_result
        if pose_est is None:
            return self._error_result(
                "NO_POSE", "没有可用的单物体位姿 (pose_result 为空)",
                request_id, goal_id, step_id)
        if pose_est.state < 1:  # STATE_PAUSED(0) 不可用，IDLE(1) 及以上都接受
            return self._error_result(
                "POSE_NOT_READY",
                f"pose_result 状态不可用 (当前 state={pose_est.state})",
                request_id, goal_id, step_id)
        rejection_reason = self._pose_rejection_reason(pose_est)
        if self._target_selection_pending or rejection_reason is not None:
            code = (
                "POSE_FRAME_MISMATCH"
                if rejection_reason == "POSE_FRAME_MISMATCH"
                else "POSE_NOT_READY"
            )
            return self._error_result(
                code,
                f"FoundationPose observation rejected: "
                f"{rejection_reason or 'TARGET_SELECTION_PENDING'}",
                request_id,
                goal_id,
                step_id,
            )

        # 懒初始化客户端
        if self._submit_carry_client is None:
            self._submit_carry_client = self._node.create_client(
                SubmitCarryTask, '/submit_carry_task')
        if not self._submit_carry_client.wait_for_service(timeout_sec=3.0):
            return self._error_result(
                "SERVICE_UNAVAILABLE",
                "/submit_carry_task 不可用 (gateway 是否已启动?)",
                request_id, goal_id, step_id)

        req = SubmitCarryTask.Request()
        # SubmitCarryTask guarantees exactly-once behavior for an accepted
        # request ID.  The dispatcher supplies a stable idempotency key here;
        # never append wall-clock time or a retry would become a new action.
        req.request_id = request_id
        req.command = "carry"
        req.object_pose.header = pose_est.header
        req.object_pose.pose = pose_est.pose       # position + orientation

        print(f"\n  [ros] >>> /submit_carry_task  "
              f"x={req.object_pose.pose.position.x:.3f}  "
              f"y={req.object_pose.pose.position.y:.3f}  "
              f"z={req.object_pose.pose.position.z:.3f}  "
              f"frame_id={req.object_pose.header.frame_id}", flush=True)

        future = self._submit_carry_client.call_async(req)
        ok = self._wait_future(future, timeout_sec=10.0)
        if not ok:
            print(f"  [ros] <<< /submit_carry_task TIMEOUT", flush=True)
            return self._error_result(
                "TIMEOUT", "/submit_carry_task 超时 (10s)",
                request_id, goal_id, step_id)
        resp = future.result()

        if resp is None:
            return self._error_result(
                "TIMEOUT", "/submit_carry_task 超时 (10s)",
                request_id, goal_id, step_id)
        if not resp.accepted:
            print(f"  [ros] <<< /submit_carry_task accepted=False  "
                  f"message={resp.message[:80] if resp.message else ''}", flush=True)
            return self._error_result(
                "CARRY_REJECTED",
                f"gateway 拒绝: {resp.message}",
                request_id, goal_id, step_id)

        print(f"  [ros] <<< /submit_carry_task accepted=True  "
              f"action={resp.selected_action}  "
              f"dist={resp.distance:.3f}m  yaw={resp.yaw_deg:.1f}°", flush=True)
        logger.info(
            "submit_carry_task accepted: request_id=%s action=%s "
            "dist=%.3fm yaw=%.1f° → %s",
            req.request_id, resp.selected_action,
            resp.distance, resp.yaw_deg, resp.message)
        return {
            "status": "ok",
            "message": resp.message,
            "selected_action": resp.selected_action,
            "distance": resp.distance,
            "yaw_deg": resp.yaw_deg,
            "target_x": resp.target_x,
            "target_y": resp.target_y,
            "pose_x": req.object_pose.pose.position.x,
            "pose_y": req.object_pose.pose.position.y,
            "pose_z": req.object_pose.pose.position.z,
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
        notify_timeout_s = min(self._timeout_s, 5.0)
        ok = self._wait_future(future, notify_timeout_s)
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


# ═══════════════════════════════════════════════════════════════
# 模块级工具
# ═══════════════════════════════════════════════════════════════


def _summarize_args(tool: str, args: dict[str, Any]) -> str:
    """提取步骤参数摘要，供诊断事件的 step_start 使用。"""
    if tool == "move_to":
        t = args.get("target", {})
        return f"move_to({t.get('x', '?')}, {t.get('y', '?')})"
    if tool == "pick":
        return f"pick({args.get('object_id', '?')})"
    if tool == "place":
        return f"place({args.get('x', '?')}, {args.get('y', '?')})"
    return tool
