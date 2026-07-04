"""
phi_robot HTTP API 服务
支持前端和后端通信的 REST API 和 WebSocket 事件流
"""

from __future__ import annotations
import json
import os
import uuid
import asyncio
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import logging

from .models import MissionEvent
from .mission_service import MissionService
from .mission_runner import MissionRunner, MissionExecutionHook
from .mission_event_hub import SyncEventHub
from .adapters.unitree_sim import UnitreeSimBackend
from .dev_console import DevConsoleController
from .step_debug import StepDebugController
from .robot_state import safety_fsm, RobotState
from .audit import audit_logger

logger = logging.getLogger("phi_robot.api")


class APIHook(MissionExecutionHook):
    """API 专用钩子，用于事件推送"""

    def __init__(self, mission_id: str, event_hub=None):
        self.mission_id = mission_id
        self.events: List[Dict[str, Any]] = []
        self._event_hub = event_hub
        self._step_count = 0

    async def after_iteration(self, context: "ExecutionContext") -> None:
        """每步执行后，通过 SyncEventHub 推送进度事件"""
        if not self._event_hub:
            return
        record = context.mission_record
        event = MissionEvent(
            event_id=0,
            type="step_progress",
            timestamp=datetime.now(),
            mission_id=self.mission_id,
            step_id=context.current_step.step_id if context.current_step else None,
            payload={
                "status": record.status,
                "step_index": record.current_step_index,
                "total_steps": len(record.plan),
                "step_count": self._step_count,
            },
        )
        self._event_hub.publish(self.mission_id, event)
        self._step_count += 1

    async def before_iteration(self, context: "ExecutionContext") -> None:
        """迭代前推送 heartbeat"""
        if not self._event_hub:
            return
        event = MissionEvent(
            event_id=0,
            type="iteration_start",
            timestamp=datetime.now(),
            mission_id=self.mission_id,
            payload={"status": context.mission_record.status},
        )
        self._event_hub.publish(self.mission_id, event)

    def apply_replan_policy(self, record: Any, result: dict, events: Any = None) -> tuple:
        """错误处理策略 — 区分 abort / retry / continue"""
        error_code = result.get("error_code", "")

        if error_code == "safety_alert":
            self.events.append({
                "type": "safety_alert",
                "timestamp": datetime.now().isoformat(),
                "message": "Safety alert triggered, aborting mission"
            })
            return ("abort", None)

        # 致命错误 → 中止
        if error_code in ("NOT_REACHABLE", "OBSTRUCTED", "INVALID_ARGS"):
            return ("abort", None)
        # 可重试的瞬态错误
        if error_code in ("GRIP_FAIL", "TIMEOUT", "PATH_PLAN_FAILED",
                          "LIFT_FAILED", "LAY_DOWN_FAILED",
                          "NOTIFY_GOAL_FAILED", "REPLAY_FAILED",
                          "FP_UNAVAILABLE", "FP_NOT_VISIBLE",
                          "PICK_NOT_READY", "PLACE_NOT_READY",
                          "REPLAY_NOT_DONE", "PATH_PLAN_NOT_READY"):
            return ("retry", None)
        # 其他错误 → 继续下一步
        return ("continue", None)


class PhiRobotAPIServer:
    """phi_robot API 服务器"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        adapter=None,
        service_manager=None,
    ):
        self.app = Flask(__name__)
        CORS(self.app)  # 启用跨域请求

        self.host = host
        self.port = port
        self.service = MissionService()
        self.adapter = adapter if adapter is not None else UnitreeSimBackend()
        self.active_runners: Dict[str, MissionRunner] = {}
        self.mission_hooks: Dict[str, APIHook] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self.event_hub = SyncEventHub(self.service.store)
        self.dev_console = DevConsoleController(self.adapter, mission_service=self.service)
        self.step_debug = StepDebugController(self.adapter, mission_service=self.service)
        self.service_manager = service_manager

        self._setup_routes()
        if self.service_manager is not None:
            self._setup_service_routes()

    def _setup_routes(self) -> None:
        """注册 API 路由"""

        @self.app.route("/api/health", methods=["GET"])
        def health():
            """健康检查"""
            return jsonify({
                "status": "ok",
                "timestamp": datetime.now().isoformat()
            })

        @self.app.route("/api/missions", methods=["POST"])
        def submit_mission():
            """提交任务"""
            try:
                data = request.json or {}

                # 提取参数
                request_id = data.get("request_id")
                if not request_id:
                    return jsonify({"error": "request_id is required"}), 400
                scene_id = data.get("scene_id") or "scene-001"
                goal_id = data.get("goal_id") or f"goal-{uuid.uuid4().hex[:8]}"
                scene_version = data.get("scene_version", "scene-v1")
                stock_layout_version = data.get("stock_layout_version", "stock-v1")
                destination_order = data.get("destination_order", [])

                if not destination_order:
                    return jsonify({"error": "destination_order is required"}), 400

                # 验证目标是否为九宫格位置
                valid_positions = ["nw", "n", "ne", "w", "c", "e", "sw", "s", "se"]
                for pos in destination_order:
                    if pos not in valid_positions:
                        return jsonify({"error": f"Invalid position: {pos}"}), 400

                # 提交任务
                mission_id = self.service.submit(
                    request_id=request_id,
                    scene_id=scene_id,
                    goal_id=goal_id,
                    scene_version=scene_version,
                    stock_layout_version=stock_layout_version,
                    destination_order=destination_order,
                    options=data.get("options", {})
                )

                return jsonify({
                    "mission_id": mission_id,
                    "request_id": request_id,
                    "goal_id": goal_id,
                    "status": "pending",
                    "timestamp": datetime.now().isoformat()
                }), 201

            except Exception as e:
                logger.exception("Error submitting mission")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/missions/<mission_id>", methods=["GET"])
        def get_mission(mission_id: str):
            """查询任务状态"""
            try:
                record = self.service.get_mission(mission_id)
                if not record:
                    return jsonify({"error": "Mission not found"}), 404

                # 序列化返回
                return jsonify({
                    "mission_id": mission_id,
                    "status": record.status,
                    "current_step_index": record.current_step_index,
                    "total_steps": len(record.plan),
                    "last_error": record.last_error,
                    "created_at": record.created_at.isoformat() if record.created_at else None,
                    "updated_at": record.updated_at.isoformat() if record.updated_at else None,
                    "steps": [
                        {
                            "step_id": step.step_id,
                            "tool": step.tool,
                            "status": step.status,
                            "args": step.args
                        }
                        for step in record.plan
                    ]
                })

            except Exception as e:
                logger.exception("Error querying mission")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/missions/<mission_id>/run", methods=["POST"])
        def run_mission(mission_id: str):
            """启动任务执行"""
            try:
                record = self.service.get_mission(mission_id)
                if not record:
                    return jsonify({"error": "Mission not found"}), 404

                # 如果任务已在运行，返回 409 而不是抛出异常
                if record.status == "running":
                    return jsonify({"error": f"Mission is {record.status}, cannot run"}), 409

                # 重置仿真环境到初始状态
                self.adapter.reset()

                # 启动任务（service.run 可能会抛 ValueError，当状态非法时）
                try:
                    self.service.run(mission_id)
                except ValueError as ve:
                    return jsonify({"error": str(ve)}), 409

                # 创建 cancel_event、钩子和执行器
                cancel_event = threading.Event()
                self._cancel_events[mission_id] = cancel_event
                hook = APIHook(mission_id, event_hub=self.event_hub)
                runner = MissionRunner(self.service, self.adapter, hook=hook, cancel_event=cancel_event)

                self.mission_hooks[mission_id] = hook
                self.active_runners[mission_id] = runner

                # 在后台线程中异步执行
                thread = threading.Thread(
                    target=self._run_mission_sync,
                    args=(mission_id, runner),
                    daemon=True,
                )
                thread.start()

                return jsonify({
                    "mission_id": mission_id,
                    "status": "running",
                    "timestamp": datetime.now().isoformat()
                })

            except Exception as e:
                logger.exception("Error running mission")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/missions/<mission_id>/pause", methods=["POST"])
        def pause_mission(mission_id: str):
            """暂停任务 — 设置 pause_requested 标志，runner 在步骤边界执行暂停"""
            try:
                record = self.service.get_mission(mission_id)
                if not record:
                    return jsonify({"error": "Mission not found"}), 404

                self.service.pause(mission_id)
                return jsonify({
                    "mission_id": mission_id,
                    "status": "pause_requested",
                    "timestamp": datetime.now().isoformat()
                })

            except Exception as e:
                logger.exception("Error pausing mission")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/missions/<mission_id>/abort", methods=["POST"])
        def abort_mission(mission_id: str):
            """中止任务 — 设置 abort_requested 标志 + 触发 cancel_event"""
            try:
                record = self.service.get_mission(mission_id)
                if not record:
                    return jsonify({"error": "Mission not found"}), 404

                # 1. 设置 abort 标志（runner 在循环顶部检查）
                self.service.request_abort(mission_id)
                # 2. 触发取消事件（中断可能正在执行中的工具调用等待）
                cancel_event = self._cancel_events.get(mission_id)
                if cancel_event:
                    cancel_event.set()

                return jsonify({
                    "mission_id": mission_id,
                    "status": "abort_requested",
                    "timestamp": datetime.now().isoformat()
                })

            except Exception as e:
                logger.exception("Error aborting mission")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/missions/<mission_id>/resume", methods=["POST"])
        def resume_mission(mission_id: str):
            """恢复任务 — 清除暂停标志，重新创建 runner 继续执行"""
            try:
                record = self.service.get_mission(mission_id)
                if not record:
                    return jsonify({"error": "Mission not found"}), 404

                if record.status != "paused":
                    return jsonify({"error": f"Mission is {record.status}, not paused"}), 409

                self.service.resume(mission_id)

                # 重新创建 runner 和 cancel_event
                cancel_event = threading.Event()
                self._cancel_events[mission_id] = cancel_event
                hook = APIHook(mission_id, event_hub=self.event_hub)
                runner = MissionRunner(self.service, self.adapter, hook=hook, cancel_event=cancel_event)
                self.mission_hooks[mission_id] = hook
                self.active_runners[mission_id] = runner

                thread = threading.Thread(
                    target=self._run_mission_sync,
                    args=(mission_id, runner),
                    daemon=True,
                )
                thread.start()

                return jsonify({
                    "mission_id": mission_id,
                    "status": "running",
                    "timestamp": datetime.now().isoformat()
                })

            except Exception as e:
                logger.exception("Error resuming mission")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/missions/<mission_id>/reset", methods=["POST"])
        def reset_mission(mission_id: str):
            """重置任务 — 停止执行并重置为 ready 状态"""
            try:
                record = self.service.get_mission(mission_id)
                if not record:
                    return jsonify({"error": "Mission not found"}), 404

                # 取消正在执行的 runner
                cancel_event = self._cancel_events.pop(mission_id, None)
                if cancel_event:
                    cancel_event.set()
                self.active_runners.pop(mission_id, None)
                self.mission_hooks.pop(mission_id, None)

                self.service.reset(mission_id)
                return jsonify({
                    "mission_id": mission_id,
                    "status": "ready",
                    "timestamp": datetime.now().isoformat()
                })

            except Exception as e:
                logger.exception("Error resetting mission")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/missions/<mission_id>/events", methods=["GET"])
        def get_mission_events(mission_id: str):
            """获取任务事件"""
            try:
                hook = self.mission_hooks.get(mission_id)
                if not hook:
                    return jsonify({"events": []})

                return jsonify({"events": hook.events})

            except Exception as e:
                logger.exception("Error querying mission events")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/missions/<mission_id>/stream", methods=["GET"])
        def stream_mission(mission_id: str):
            """SSE 事件流 — 替代轮询"""
            def event_stream():
                q = self.event_hub.subscribe_sync(mission_id)
                try:
                    while True:
                        try:
                            event = q.get(timeout=30)
                            if event is None:
                                break
                            yield f"data: {json.dumps(event, default=str)}\n\n"
                        except Exception:
                            yield ": heartbeat\n\n"
                finally:
                    self.event_hub.unsubscribe(mission_id, q)

            return Response(event_stream(), mimetype="text/event-stream")

        @self.app.route("/api/snapshot", methods=["GET"])
        def get_snapshot():
            """获取仿真环境快照"""
            try:
                snapshot = self.adapter.snapshot()
                return jsonify(snapshot)

            except Exception as e:
                logger.exception("Error getting snapshot")
                return jsonify({"error": str(e)}), 500

        # ── 机器人安全状态查询 ────────────────────────────

        @self.app.route("/api/robot/state", methods=["GET"])
        def get_robot_state():
            """查询安全状态机 + 机器人实时状态"""
            return jsonify({
                "state": safety_fsm.state_value,
                "can_walk": safety_fsm.can_walk(),
                "can_pick": safety_fsm.can_pick(),
                "can_place": safety_fsm.can_place(),
                "bypass": safety_fsm.bypass,
                "sonic_input_source": (
                    self.adapter.get_sonic_input_source()
                    if hasattr(self.adapter, "get_sonic_input_source") else "unknown"
                ),
            })

        @self.app.route("/api/robot/safety/bypass", methods=["POST"])
        def set_safety_bypass():
            """运行时开关：启用/禁用安全状态机旁路。

            请求体: {"bypass": true} → 跳过所有安全检查，自由调用 move_to/pick/place
                    {"bypass": false} → 恢复正常安全校验
            """
            data = request.json or {}
            enabled = bool(data.get("bypass", False))
            safety_fsm.set_bypass(enabled)
            logger.warning("API: 安全旁路 %s", "启用" if enabled else "关闭")
            return jsonify({
                "ok": True,
                "bypass": enabled,
                "message": "安全旁路已{} — 所有 can_walk/can_pick/can_place 强制返回 true".format(
                    "启用" if enabled else "关闭"
                ),
            })

        # ── SONIC 输入源切换 ──────────────────────────────

        @self.app.route("/api/dev/sonic/input_source", methods=["GET"])
        def get_sonic_input_source():
            """查询当前 SONIC 输入源: "ROS2" | "GAMEPAD" | "unknown" """
            source = "unknown"
            if hasattr(self.adapter, "get_sonic_input_source"):
                source = self.adapter.get_sonic_input_source()
            return jsonify({"active_source": source})

        @self.app.route("/api/dev/sonic/input_source", methods=["POST"])
        def set_sonic_input_source():
            """切换 SONIC 输入源。

            请求体: {"gamepad": true}  → 手柄接管 (GAMEPAD)
                    {"gamepad": false} → ROS2 自动控制
            """
            data = request.json or {}
            gamepad = bool(data.get("gamepad", False))
            if hasattr(self.adapter, "set_sonic_input_source"):
                result = self.adapter.set_sonic_input_source(gamepad)
                logger.warning("API: SONIC 输入源 → %s (ok=%s)",
                              "GAMEPAD" if gamepad else "ROS2", result.get("ok"))
                return jsonify(result)
            return jsonify({"ok": False, "message": "适配器不支持 SONIC 输入源切换",
                           "active_source": "unknown"})

        # ── 调试控制台 API ────────────────────────────────

        @self.app.route("/api/dev/mode", methods=["POST"])
        def dev_switch_mode():
            """切换自动/手动模式"""
            data = request.json or {}
            new_mode = data.get("mode", "manual")
            result = self.dev_console.switch_mode(new_mode)
            return jsonify(result)

        @self.app.route("/api/dev/state", methods=["GET"])
        def dev_get_state():
            """获取完整状态（含日志）"""
            state = self.dev_console.get_state()
            state["logs"] = self.dev_console.get_logs()
            return jsonify(state)

        @self.app.route("/api/dev/stream", methods=["GET"])
        def dev_stream():
            """SSE 实时状态推送"""
            import time as _time
            def generate():
                while True:
                    state = self.dev_console.get_state()
                    state["logs"] = self.dev_console.get_logs()
                    yield f"data: {json.dumps(state, default=str)}\n\n"
                    _time.sleep(1)
            return Response(generate(), mimetype="text/event-stream")

        @self.app.route("/api/dev/logs", methods=["GET"])
        def dev_get_logs():
            """获取日志"""
            return jsonify({"logs": self.dev_console.get_logs()})

        @self.app.route("/api/dev/logs/export", methods=["GET"])
        def dev_export_logs():
            """导出日志 JSON 文件"""
            from flask import make_response
            content = self.dev_console.export_logs()
            resp = make_response(content)
            resp.headers["Content-Type"] = "application/json"
            resp.headers["Content-Disposition"] = (
                f"attachment; filename=robot_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            return resp

        # 手动模式 — 含安全状态检查

        @self.app.route("/api/dev/manual/next", methods=["POST"])
        def dev_manual_next():
            if not safety_fsm.can_walk():
                return jsonify({"ok": False, "message": f"当前 {safety_fsm.state_value}, 不可导航"}), 409
            data = request.json or {}
            target = data.get("target")
            result = self.dev_console.manual_next(target)
            return jsonify(result)

        @self.app.route("/api/dev/manual/prev", methods=["POST"])
        def dev_manual_prev():
            result = self.dev_console.manual_prev()
            return jsonify(result)

        @self.app.route("/api/dev/manual/pick", methods=["POST"])
        def dev_manual_pick():
            if not safety_fsm.can_pick():
                return jsonify({"ok": False, "message": f"当前 {safety_fsm.state_value}, 不可搬起"}), 409
            result = self.dev_console.manual_pick()
            return jsonify(result)

        @self.app.route("/api/dev/manual/place", methods=["POST"])
        def dev_manual_place():
            if not safety_fsm.can_place():
                return jsonify({"ok": False, "message": f"当前 {safety_fsm.state_value}, 不可放下"}), 409
            result = self.dev_console.manual_place()
            return jsonify(result)

        @self.app.route("/api/dev/manual/pause", methods=["POST"])
        def dev_manual_pause():
            result = self.dev_console.manual_pause()
            return jsonify(result)

        @self.app.route("/api/dev/manual/stop", methods=["POST"])
        def dev_manual_stop():
            safety_fsm.reset()
            result = self.dev_console.manual_stop()
            return jsonify(result)

        # 自动模式

        @self.app.route("/api/dev/auto/start", methods=["POST"])
        def dev_auto_start():
            data = request.json or {}
            destinations = data.get("destinations", [])
            if not destinations:
                return jsonify({"ok": False, "message": "destinations 不能为空"})
            result = self.dev_console.auto_start(destinations)
            return jsonify(result)

        @self.app.route("/api/dev/auto/pause", methods=["POST"])
        def dev_auto_pause():
            result = self.dev_console.auto_pause()
            return jsonify(result)

        @self.app.route("/api/dev/auto/resume", methods=["POST"])
        def dev_auto_resume():
            result = self.dev_console.auto_resume()
            return jsonify(result)

        @self.app.route("/api/dev/auto/stop", methods=["POST"])
        def dev_auto_stop():
            result = self.dev_console.auto_stop()
            return jsonify(result)

        # ── 分步调试 API ────────────────────────────────

        @self.app.route("/api/dev/step_debug/load", methods=["POST"])
        def step_debug_load():
            """加载 mission plan → 返回步骤列表"""
            data = request.json or {}
            destinations = data.get("destinations", [])
            if not destinations:
                return jsonify({"ok": False, "message": "destinations 不能为空"}), 400
            result = self.step_debug.load_plan(destinations)
            return jsonify(result)

        @self.app.route("/api/dev/step_debug/execute", methods=["POST"])
        def step_debug_execute():
            """异步执行当前步骤，立即返回 started"""
            result = self.step_debug.execute_current_step()
            return jsonify(result)

        @self.app.route("/api/dev/step_debug/retry", methods=["POST"])
        def step_debug_retry():
            """重试当前步骤"""
            result = self.step_debug.retry_step()
            return jsonify(result)

        @self.app.route("/api/dev/step_debug/skip", methods=["POST"])
        def step_debug_skip():
            """跳过当前步骤（仅 STEP_READY / STEP_DONE）"""
            result = self.step_debug.skip_step()
            return jsonify(result)

        @self.app.route("/api/dev/step_debug/force_skip", methods=["POST"])
        def step_debug_force_skip():
            """强制跳过（含失败步）"""
            result = self.step_debug.force_skip()
            return jsonify(result)

        @self.app.route("/api/dev/step_debug/pause", methods=["POST"])
        def step_debug_pause():
            """暂停正在执行的步骤"""
            result = self.step_debug.pause_execution()
            return jsonify(result)

        @self.app.route("/api/dev/step_debug/resume", methods=["POST"])
        def step_debug_resume():
            """恢复暂停的步骤"""
            result = self.step_debug.resume_execution()
            return jsonify(result)

        @self.app.route("/api/dev/step_debug/abort", methods=["POST"])
        def step_debug_abort():
            """终止调试会话"""
            result = self.step_debug.abort()
            return jsonify(result)

        @self.app.route("/api/dev/step_debug/select_target_toggle", methods=["POST"])
        def step_debug_select_target_toggle():
            """切换 FoundationPose 单目标模式"""
            result = self.step_debug.toggle_select_target()
            return jsonify(result)

        @self.app.route("/api/dev/step_debug/reset_fp", methods=["POST"])
        def step_debug_reset_fp():
            """FoundationPose 重新标定物体"""
            try:
                if hasattr(self.adapter, "call_reset_fp"):
                    result = self.adapter.call_reset_fp()
                    if result is None:
                        return jsonify({"ok": False, "message": "Reset 服务不可用或超时"})
                    return jsonify({"ok": result.get("success", False),
                                    "message": result.get("message", "")})
                return jsonify({"ok": False, "message": "适配器不支持"})
            except Exception as e:
                logger.exception("reset_fp 失败")
                return jsonify({"ok": False, "message": str(e)})

        # ── 掉箱处理（手动重规划） ──

        @self.app.route("/api/dev/step_debug/drop_detector/enable", methods=["POST"])
        def step_debug_drop_detector_enable():
            """开启掉箱检测"""
            try:
                result = self.step_debug.enable_drop_detector()
                return jsonify(result)
            except Exception as e:
                logger.exception("drop_detector enable 失败")
                return jsonify({"ok": False, "message": str(e)})

        @self.app.route("/api/dev/step_debug/drop_detector/disable", methods=["POST"])
        def step_debug_drop_detector_disable():
            """关闭掉箱检测"""
            try:
                result = self.step_debug.disable_drop_detector()
                return jsonify(result)
            except Exception as e:
                logger.exception("drop_detector disable 失败")
                return jsonify({"ok": False, "message": str(e)})

        @self.app.route("/api/dev/step_debug/robot/pause", methods=["POST"])
        def step_debug_pause_robot():
            """暂停机器人导航"""
            try:
                result = self.step_debug.pause_robot()
                return jsonify(result)
            except Exception as e:
                logger.exception("pause_robot 失败")
                return jsonify({"ok": False, "message": str(e)})

        @self.app.route("/api/dev/step_debug/robot/stand", methods=["POST"])
        def step_debug_stand_robot():
            """切换机器人站立模式"""
            try:
                result = self.step_debug.stand_robot()
                return jsonify(result)
            except Exception as e:
                logger.exception("stand_robot 失败")
                return jsonify({"ok": False, "message": str(e)})

        @self.app.route("/api/dev/step_debug/dropped/identify", methods=["POST"])
        def step_debug_identify_dropped():
            """识别掉落箱子（FP MODE_DROPPED）"""
            try:
                result = self.step_debug.identify_dropped_box()
                return jsonify(result)
            except Exception as e:
                logger.exception("identify_dropped 失败")
                return jsonify({"ok": False, "message": str(e)})

        @self.app.route("/api/dev/step_debug/dropped/replan_pick", methods=["POST"])
        def step_debug_replan_pick():
            """执行重规划搬起"""
            try:
                result = self.step_debug.replan_pick()
                return jsonify(result)
            except Exception as e:
                logger.exception("replan_pick 失败")
                return jsonify({"ok": False, "message": str(e)})

        @self.app.route("/api/dev/step_debug/pose_snapshot", methods=["GET"])
        def step_debug_pose_snapshot():
            """返回缓存的单物体位姿 (torso_link 帧)，供前端确认数据正确后再执行 pick"""
            try:
                if not hasattr(self.adapter, '_latest_pose_result'):
                    return jsonify({"ok": False, "message": "适配器不支持"})
                pose = self.adapter._latest_pose_result
                if pose is None or getattr(pose, 'state', 0) != 2:
                    return jsonify({"ok": True, "has_pose": False})
                import math
                x = pose.pose.position.x
                y = pose.pose.position.y
                z = pose.pose.position.z
                yaw_deg = round(math.degrees(math.atan2(y, x)), 1)
                dist = round(math.sqrt(x * x + y * y), 3)
                return jsonify({
                    "ok": True,
                    "has_pose": True,
                    "x": x, "y": y, "z": z,
                    "yaw_deg": yaw_deg,
                    "distance": dist,
                    "frame_id": pose.header.frame_id,
                    "tracking_frames": getattr(pose, 'tracking_frames', 0),
                })
            except Exception as e:
                logger.exception("pose_snapshot 失败")
                return jsonify({"ok": False, "message": str(e)})

        @self.app.route("/api/dev/step_debug/state", methods=["GET"])
        def step_debug_state():
            """同步快照：plan + current_index + diag_events + step_logs + odom"""
            state = self.step_debug.get_state()
            # 附加机器人安全状态 + 里程计
            state["robot_state"] = {
                "state": safety_fsm.state_value,
                "can_walk": safety_fsm.can_walk(),
                "can_pick": safety_fsm.can_pick(),
                "can_place": safety_fsm.can_place(),
            }
            try:
                if hasattr(self.adapter, "get_odom"):
                    odom = self.adapter.get_odom()
                    state["odom"] = {
                        "x": round(float(odom.get("x", 0)), 3),
                        "y": round(float(odom.get("y", 0)), 3),
                        "yaw_deg": round(float(odom.get("yaw_deg", 0)), 1),
                    }
            except Exception:
                state["odom"] = None
            return jsonify(state)

        @self.app.route("/api/dev/step_debug/stream", methods=["GET"])
        def step_debug_stream():
            """SSE 增量推送：diag 事件 + state_change"""
            import time as _time
            # None 强制首次循环推送初始状态，避免依赖前端恰好也初始化为 'idle'
            _last_state = None

            def _json_default(obj):
                """自定义 JSON encoder：遇到非标准类型时 log warning 再转字符串，
                避免 silent data corruption（如 tuple 被序列化为 "(1.5, 2.3)"）。"""
                logger.warning(
                    "SSE 序列化: 非标准类型 %s，值=%r，已转为字符串。"
                    "请检查上游代码是否漏了 list() 转换。",
                    type(obj).__name__, obj,
                )
                return str(obj)

            def generate():
                nonlocal _last_state
                try:
                    while True:
                        # 增量诊断事件
                        diag_events = self.step_debug.drain_diag_events()
                        if diag_events:
                            yield (
                                "event: diag\n"
                                "data: " + json.dumps(
                                    {"type": "diag", "events": diag_events},
                                    default=_json_default,
                                ) + "\n\n"
                            )

                        # 状态变更检测
                        current = self.step_debug.get_state()
                        current_state = current.get("state", "")
                        if current_state != _last_state:
                            _last_state = current_state
                            yield (
                                "event: state\n"
                                "data: " + json.dumps(
                                    {
                                        "type": "state_change",
                                        "state": current_state,
                                        "current_index": current.get("current_index"),
                                        "plan": current.get("plan"),
                                        "step_logs": current.get("step_logs"),
                                        "select_target_active": current.get("select_target_active"),
                                        "nav_reached": current.get("nav_reached"),  # 🆕 Nav2 导航到达状态
                                    },
                                    default=_json_default,
                                ) + "\n\n"
                            )

                        _time.sleep(0.1)  # 100ms 轮询，保证诊断事件延迟低
                except GeneratorExit:
                    logger.debug("step_debug SSE 客户端断开")
                except Exception:
                    logger.exception("step_debug SSE 生成器异常")

            return Response(generate(), mimetype="text/event-stream")

        # ── 审计日志 API ──────────────────────────────────

        @self.app.route("/api/dev/audit/today", methods=["GET"])
        def dev_audit_today():
            """返回当天审计日志文件信息"""
            info = audit_logger.today_info()
            lines = []
            if info.get("path") and os.path.exists(info["path"]):
                try:
                    with open(info["path"], "r", encoding="utf-8") as f:
                        lines = [line.rstrip("\n") for line in f.readlines()[-200:]]
                except Exception:
                    pass
            info["lines"] = lines
            info["line_count"] = len(lines)
            return jsonify(info)

        @self.app.route("/api/dev/audit/download", methods=["GET"])
        def dev_audit_download():
            """下载当天审计日志文件"""
            from flask import send_file
            info = audit_logger.today_info()
            path = info.get("path", "")
            if not path or not os.path.exists(path):
                return jsonify({"error": "no audit log for today"}), 404
            return send_file(
                path,
                mimetype="application/x-ndjson",
                as_attachment=True,
                download_name=f"audit_{info.get('date', 'unknown')}.jsonl",
            )

        # ── FoundationPose MJPEG 视频流 ───────────────────

        # 占位帧（懒加载，内存绘制，FP 无数据时保活）
        _placeholder_jpeg: Optional[bytes] = None
        _placeholder_disabled: bool = False

        def _get_placeholder_jpeg() -> Optional[bytes]:
            nonlocal _placeholder_jpeg, _placeholder_disabled
            if _placeholder_disabled:
                return None
            if _placeholder_jpeg is not None:
                return _placeholder_jpeg
            try:
                from PIL import Image, ImageDraw
                import io as _io
                img = Image.new('RGB', (640, 480), (20, 20, 20))
                _draw = ImageDraw.Draw(img)
                _draw.text((180, 225), "Waiting for FP data...", fill=(160, 160, 160))
                _draw.text((200, 250), "Check FP node is activated", fill=(120, 120, 120))
                _buf = _io.BytesIO()
                img.save(_buf, format='JPEG', quality=80)
                _placeholder_jpeg = _buf.getvalue()
            except ImportError:
                _placeholder_disabled = True
                return None
            return _placeholder_jpeg

        @self.app.route("/api/fp/video/<channel>/stream", methods=["GET"])
        def fp_video_stream(channel: str):
            """MJPEG stream for FP video channels (rgb / depth / mask)."""
            if channel not in ('rgb', 'depth', 'mask', 'drop'):
                return jsonify({'error': 'unknown channel, use rgb/depth/mask/drop'}), 404

            adapter = self.adapter
            get_frame = getattr(adapter, 'get_fp_video_frame', None)

            def generate():
                last_data_time = time.time()
                while True:
                    frame = get_frame(channel) if callable(get_frame) else None
                    if frame is not None:
                        last_data_time = time.time()
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n'
                               + frame + b'\r\n')
                    else:
                        # 超过 2 秒无数据 → 发送占位帧保活
                        if time.time() - last_data_time > 2.0:
                            placeholder = _get_placeholder_jpeg()
                            if placeholder is not None:
                                yield (b'--frame\r\n'
                                       b'Content-Type: image/jpeg\r\n\r\n'
                                       + placeholder + b'\r\n')
                            last_data_time = time.time()
                    time.sleep(0.033)  # ~30 fps cap

            return Response(
                generate(),
                mimetype='multipart/x-mixed-replace; boundary=frame',
                headers={
                    'Cache-Control': 'no-cache, no-store, must-revalidate',
                    'X-Accel-Buffering': 'no',
                },
            )

    def _setup_service_routes(self) -> None:
        """注册 ServiceManager 相关路由"""

        @self.app.route("/api/services", methods=["GET"])
        def get_services():
            """获取所有服务状态"""
            if not self.service_manager:
                return jsonify({"error": "ServiceManager not configured"}), 503
            return jsonify(self.service_manager.get_frontend_data())

        @self.app.route("/api/services/registry", methods=["GET"])
        def get_registry():
            """获取服务注册表"""
            if not self.service_manager:
                return jsonify({"error": "ServiceManager not configured"}), 503
            return jsonify({
                "compose_file": self.service_manager.registry.compose_file,
                "compose_profile": self.service_manager.registry.compose_profile,
                "services": [
                    {
                        "id": s.id,
                        "group": s.group,
                        "label": s.label,
                        "description": s.description,
                        "compose_service": s.compose_service,
                        "check": s.check,
                        "manage": s.manage,
                        "depends_on": s.depends_on,
                    }
                    for s in self.service_manager.registry.services
                ],
            })

        @self.app.route("/api/services/<svc_id>/start", methods=["POST"])
        def start_service(svc_id: str):
            if not self.service_manager:
                return jsonify({"error": "ServiceManager not configured"}), 503
            result = self.service_manager.start(svc_id)
            return jsonify(result)

        @self.app.route("/api/services/<svc_id>/stop", methods=["POST"])
        def stop_service(svc_id: str):
            if not self.service_manager:
                return jsonify({"error": "ServiceManager not configured"}), 503
            result = self.service_manager.stop(svc_id)
            return jsonify(result)

        @self.app.route("/api/services/<svc_id>/restart", methods=["POST"])
        def restart_service(svc_id: str):
            if not self.service_manager:
                return jsonify({"error": "ServiceManager not configured"}), 503
            result = self.service_manager.restart(svc_id)
            return jsonify(result)

    async def _run_mission_async(self, mission_id: str, runner: MissionRunner) -> None:
        """异步执行任务"""
        try:
            final_record = await runner.run(mission_id)
            logger.info(f"Mission {mission_id} completed with status: {final_record.status}")
        except Exception as e:
            logger.exception(f"Error executing mission {mission_id}")
        finally:
            self.active_runners.pop(mission_id, None)

    def _run_mission_sync(self, mission_id: str, runner: MissionRunner) -> None:
        """同步执行任务（在线程中）"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            final_record = loop.run_until_complete(runner.run(mission_id))
            logger.info(f"Mission {mission_id} completed with status: {final_record.status}")
        except Exception as e:
            logger.exception(f"Error executing mission {mission_id}")
        finally:
            self.active_runners.pop(mission_id, None)
            self._cancel_events.pop(mission_id, None)
            loop.close()

    def run(self) -> None:
        """启动服务器"""
        logger.info(f"Starting phi_robot API server on {self.host}:{self.port}")
        self.app.run(host=self.host, port=self.port, debug=False, use_reloader=False)


def create_app() -> Flask:
    """工厂函数，用于测试"""
    server = PhiRobotAPIServer()
    return server.app


if __name__ == "__main__":
    server = PhiRobotAPIServer(host="0.0.0.0", port=5000)
    server.run()
