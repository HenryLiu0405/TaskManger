"""
机器人开发调试控制台控制器
双模式（自动/手动）+ 撤销栈 + 日志缓冲
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from .adapters.adapter_base import RobotAdapter
from .audit import audit_logger
from .robot_state import safety_fsm
from .skills import build_skill_dispatcher

logger = logging.getLogger("phi_robot.dev_console")


# ── 数据模型 ──────────────────────────────────────────────

@dataclass
class ActionRecord:
    """撤销栈中的一条记录"""
    forward_name: str
    forward_args: dict
    reverse_name: str
    reverse_args: dict
    timestamp: str = ""

    @staticmethod
    def now() -> str:
        return datetime.now().strftime("%H:%M:%S")


@dataclass
class LogEntry:
    timestamp: str
    level: str       # "ok" | "error" | "warn"
    action: str
    detail: str


# ── 状态常量 ──────────────────────────────────────────────

IDLE = "idle"
MOVING = "moving"
ARRIVED = "arrived"
PICKING = "picking"
HOLDING = "holding"
PLACING = "placing"
PAUSED = "paused"
STOPPED = "stopped"


# ── 控制器 ────────────────────────────────────────────────

class DevConsoleController:
    """双模式机器人调试控制台"""

    MAX_LOG_ENTRIES = 500
    MAX_HISTORY = 20

    def __init__(self, adapter: RobotAdapter, mission_service=None, skill_dispatcher=None):
        self._adapter = adapter
        self._mission_service = mission_service
        self._skill_dispatcher = skill_dispatcher or build_skill_dispatcher(adapter)

        # 模式
        self._mode: str = "manual"

        # 服务状态缓存
        self._services_cache: dict = {}
        self._services_checked_at: float = 0.0

        # 手动模式状态
        self._manual_state: str = IDLE
        self._current_position: dict = {"x": 0.0, "y": 0.0}
        self._target_position: Optional[dict] = None
        self._holding_box: bool = False

        # 撤销栈
        self._action_history: list[ActionRecord] = []

        # 日志缓冲
        self._log_buffer: list[LogEntry] = []

        # 线程控制
        self._lock = threading.RLock()
        self._pause_flag = threading.Event()
        self._stop_flag = threading.Event()
        self._defer_pause: bool = False
        self._exec_thread: Optional[threading.Thread] = None

        # 自动模式
        self._auto_mission_id: Optional[str] = None
        self._auto_cancel_event: Optional[threading.Event] = None
        self._auto_runner = None
        self._auto_thread: Optional[threading.Thread] = None

        self._add_log("info", "console", "调试控制台已就绪")

    # ── 公共属性 ──────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def manual_state(self) -> str:
        return self._manual_state

    # ── 模式切换 ──────────────────────────────────────────

    def switch_mode(self, new_mode: str) -> dict:
        """切换自动/手动模式，返回操作提示"""
        if new_mode == self._mode:
            return {"ok": True, "message": f"已经是 {new_mode} 模式"}
        if new_mode == "manual":
            self._mode = "manual"
            self._reset_manual_state()
            self._add_log("info", "mode", "切换到手动模式")
            audit_logger.log_system("切换到手动模式", mode="manual")
            return {"ok": True, "message": "已切换到手动模式"}
        if new_mode == "auto":
            self._mode = "auto"
            self._reset_manual_state()
            self._add_log("info", "mode", "切换到自动模式")
            audit_logger.log_system("切换到自动模式", mode="auto")
            return {"ok": True, "message": "已切换到自动模式"}
        return {"ok": False, "message": f"未知模式: {new_mode}"}

    # ── 手动模式: 下一步 ─────────────────────────────────

    def manual_next(self, target: Optional[dict] = None) -> dict:
        """手动模式下一步 — 后台执行"""
        if self._mode != "manual":
            return {"ok": False, "message": "当前不在手动模式"}
        if self._manual_state == STOPPED:
            return {"ok": False, "message": "已终止，不可恢复"}
        if self._manual_state == PAUSED:
            return {"ok": False, "message": "已暂停，请先继续"}

        action = self._next_action(target)
        if action is None:
            return {"ok": False, "message": f"当前状态 {self._manual_state} 无法确定下一步"}

        self._start_background_action(action)
        return {"ok": True, "message": f"执行: {action.log_label}"}

    def _next_action(self, target: Optional[dict]) -> Optional["_ManualAction"]:
        """根据当前状态和输入决定下一步做什么"""
        if self._manual_state in (IDLE, HOLDING) and target:
            prev_pos = dict(self._current_position)
            return _ManualAction(
                tool="move_to",
                args={"target": target},
                log_label=f"move_to ({target.get('x')}, {target.get('y')})",
                transitional_state=MOVING,
                on_success_state=ARRIVED if not self._holding_box else ARRIVED,
                on_success_pos=target,
                reverse=ActionRecord(
                    forward_name="move_to",
                    forward_args={"target": target},
                    reverse_name="move_to",
                    reverse_args={"target": prev_pos},
                    timestamp=ActionRecord.now(),
                ),
                resume_state=ARRIVED if not self._holding_box else ARRIVED,
            )
        if self._manual_state == ARRIVED and target:
            # user gave new coordinates at ARRIVED — re-navigate, skip pick/place
            prev_pos = dict(self._current_position)
            return _ManualAction(
                tool="move_to",
                args={"target": target},
                log_label=f"move_to ({target.get('x')}, {target.get('y')})",
                transitional_state=MOVING,
                on_success_state=ARRIVED,
                on_success_pos=target,
                reverse=ActionRecord(
                    forward_name="move_to",
                    forward_args={"target": target},
                    reverse_name="move_to",
                    reverse_args={"target": prev_pos},
                    timestamp=ActionRecord.now(),
                ),
                resume_state=ARRIVED,
            )
        if self._manual_state == ARRIVED and not self._holding_box:
            return _ManualAction(
                tool="pick",
                args={},
                log_label="pick",
                transitional_state=PICKING,
                on_success_state=HOLDING,
                on_success_pos=None,
                on_holding=True,
                reverse=ActionRecord(
                    forward_name="pick",
                    forward_args={},
                    reverse_name="place",
                    reverse_args={},
                    timestamp=ActionRecord.now(),
                ),
                resume_state=HOLDING,
            )
        if self._manual_state == ARRIVED and self._holding_box:
            return _ManualAction(
                tool="place",
                args={},
                log_label="place",
                transitional_state=PLACING,
                on_success_state=IDLE,
                on_success_pos=None,
                on_not_holding=True,
                reverse=ActionRecord(
                    forward_name="place",
                    forward_args={},
                    reverse_name="pick",
                    reverse_args={},
                    timestamp=ActionRecord.now(),
                ),
                resume_state=IDLE,
            )
        return None

    # ── 手动模式: 上一步（撤销） ──────────────────────────

    def manual_prev(self) -> dict:
        """撤销上一步"""
        if self._mode != "manual":
            return {"ok": False, "message": "当前不在手动模式"}
        if self._manual_state == STOPPED:
            return {"ok": False, "message": "已终止，不可恢复"}
        if not self._action_history:
            return {"ok": False, "message": "没有可撤销的操作"}

        record = self._action_history.pop()
        self._start_background_action(
            _ManualAction(
                tool=record.reverse_name,
                args=record.reverse_args,
                log_label=f"undo: {record.forward_name} → {record.reverse_name}",
                transitional_state=self._reverse_transitional(record.reverse_name),
                on_success_state=self._reverse_result_state(record.reverse_name),
                on_success_pos=record.reverse_args.get("target"),
                on_holding=self._reverse_holding(record.reverse_name),
                on_not_holding=False,
                reverse=None,  # 撤销操作不再入栈
                resume_state=self._reverse_result_state(record.reverse_name),
            )
        )
        return {"ok": True, "message": f"撤销: {record.forward_name}"}

    @staticmethod
    def _reverse_transitional(reverse_tool: str) -> str:
        if reverse_tool == "move_to":
            return MOVING
        if reverse_tool == "pick":
            return PICKING
        if reverse_tool == "place":
            return PLACING
        return IDLE

    @staticmethod
    def _reverse_result_state(reverse_tool: str) -> str:
        if reverse_tool == "move_to":
            return ARRIVED
        if reverse_tool == "pick":
            return HOLDING
        if reverse_tool == "place":
            return IDLE
        return IDLE

    @staticmethod
    def _reverse_holding(reverse_tool: str) -> bool:
        return reverse_tool == "pick"

    # ── 手动模式: 独立搬箱子 / 放箱子 ─────────────────────

    def manual_pick(self) -> dict:
        """手动模式: 直接搬箱子（不依赖状态机）"""
        if self._mode != "manual":
            return {"ok": False, "message": "当前不在手动模式"}
        if self._manual_state == STOPPED:
            return {"ok": False, "message": "已终止，不可恢复"}

        self._start_background_action(
            _ManualAction(
                tool="pick",
                args={},
                log_label="manual pick",
                transitional_state=PICKING,
                on_success_state=HOLDING if not self._holding_box else self._manual_state,
                on_success_pos=None,
                on_holding=True,
                reverse=ActionRecord(
                    forward_name="pick",
                    forward_args={},
                    reverse_name="place",
                    reverse_args={},
                    timestamp=ActionRecord.now(),
                ),
                resume_state=HOLDING,
            )
        )
        return {"ok": True, "message": "pick 已提交，结果见操作日志", "async": True}

    def manual_place(self) -> dict:
        """手动模式: 直接放箱子"""
        if self._mode != "manual":
            return {"ok": False, "message": "当前不在手动模式"}
        if self._manual_state == STOPPED:
            return {"ok": False, "message": "已终止，不可恢复"}

        self._start_background_action(
            _ManualAction(
                tool="place",
                args={},
                log_label="manual place",
                transitional_state=PLACING,
                on_success_state=IDLE,
                on_success_pos=None,
                on_not_holding=True,
                reverse=ActionRecord(
                    forward_name="place",
                    forward_args={},
                    reverse_name="pick",
                    reverse_args={},
                    timestamp=ActionRecord.now(),
                ),
                resume_state=IDLE,
            )
        )
        return {"ok": True, "message": "place 已提交，结果见操作日志", "async": True}

    # ── 手动模式: 暂停 / 终止 ─────────────────────────────

    def manual_pause(self) -> dict:
        """暂停/继续 toggle  — 暂停时通过 /pause_navigation topic 立即停止导航"""
        if self._mode != "manual":
            return {"ok": False, "message": "当前不在手动模式"}

        if self._manual_state == STOPPED:
            return {"ok": False, "message": "已终止，不可恢复"}

        if self._manual_state == PAUSED:
            self._pause_flag.clear()
            self._defer_pause = False
            self._manual_state = self._resume_state
            control_result = None
            if self._resume_state == MOVING:
                control_result = self._skill_dispatcher.execute_legacy(
                    "resume_navigation", {},
                    request_id=f"dev-manual-resume-{uuid.uuid4().hex[:8]}",
                    goal_id="dev-manual",
                    step_id="resume-navigation",
                    source="dev_console_manual",
                )
            self._add_log("info", "resume", f"继续执行 → {self._manual_state}")
            return {
                "ok": True,
                "message": "已解除本地暂停门；导航恢复以后端回执为准",
                "paused": False,
                "physical_resume_confirmed": False,
                "control_result": control_result,
            }

        self._resume_state = self._manual_state

        control_result = None
        if self._manual_state == MOVING:
            control_result = self._skill_dispatcher.execute_legacy(
                "pause_navigation", {},
                request_id=f"dev-manual-pause-{uuid.uuid4().hex[:8]}",
                goal_id="dev-manual",
                step_id="pause-navigation",
                source="dev_console_manual",
            )
        elif self._manual_state in (PICKING, PLACING):
            self._defer_pause = True
            self._add_log("info", "pause", "动作完成时将暂停")

        self._pause_flag.set()
        self._manual_state = PAUSED
        self._add_log("info", "pause", "已设置本地暂停门，物理停止未确认")
        return {
            "ok": True,
            "message": "已请求暂停；当前物理动作未确认停止",
            "paused": True,
            "pause_requested": True,
            "physical_stop_confirmed": False,
            "control_result": control_result,
        }

    def manual_stop(self) -> dict:
        """终止 — 等当前动作完成后进入 stopped 状态（不可恢复）"""
        if self._mode != "manual":
            return {"ok": False, "message": "当前不在手动模式"}

        safety_fsm.reset()
        self._stop_flag.set()
        self._pause_flag.clear()
        self._manual_state = STOPPED
        self._action_history.clear()
        self._holding_box = False
        self._target_position = None
        self._add_log("warn", "stop", "已停止本地后继编排；在途物理动作未确认停止")
        audit_logger.log_system("manual_stop: 本地后继编排已停止，物理停止未确认")
        return {
            "ok": True,
            "message": "已停止后继编排；在途动作未确认停止",
            "stopped": True,
            "physical_stop_confirmed": False,
        }

    # ── 自动模式 ──────────────────────────────────────────

    def _make_step_logger(self, mission_id: str):
        short_id = mission_id[:8]
        def log_step(step, result):
            level = "ok" if result.status == "ok" else "error"
            detail = f"[{short_id}] {step.tool}"
            if result.message:
                detail += f" → {result.message}"
            if result.error_code:
                detail += f" ({result.error_code})"
            self._add_log(level, "auto", detail)
        return log_step

    # 合法的九宫格目标方位
    _VALID_DESTINATIONS = {"nw", "n", "ne", "w", "c", "e", "sw", "s", "se"}

    def auto_start(self, destinations: list) -> dict:
        """自动模式: 提交并执行任务"""
        if self._mode != "auto":
            return {"ok": False, "message": "当前不在自动模式"}
        if not self._mission_service:
            return {"ok": False, "message": "MissionService 未注入"}

        if self._auto_thread and self._auto_thread.is_alive():
            return {"ok": False, "message": "已有自动任务在运行"}

        # 预校验：只接受九宫格目标，拒绝备货区标签
        invalid = [d for d in destinations if d not in self._VALID_DESTINATIONS]
        if invalid:
            return {
                "ok": False,
                "message": f"无效目标: {', '.join(invalid)}（自动模式只接受九宫格位置: {', '.join(sorted(self._VALID_DESTINATIONS))}）",
            }

        try:
            mission_id = self._mission_service.submit(
                request_id=f"dev-auto-{uuid.uuid4().hex[:8]}",
                scene_id="dev-console",
                goal_id=f"dev-{uuid.uuid4().hex[:8]}",
                scene_version="dev-v1",
                stock_layout_version="dev-v1",
                destination_order=destinations,
            )
            self._auto_mission_id = mission_id
            self._auto_cancel_event = threading.Event()

            from .mission_runner import MissionRunner

            self._auto_runner = MissionRunner(
                self._mission_service,
                self._adapter,
                cancel_event=self._auto_cancel_event,
                step_callback=self._make_step_logger(mission_id),
                skill_dispatcher=self._skill_dispatcher,
            )

            def run_auto():
                loop = None
                try:
                    import asyncio
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    record = loop.run_until_complete(self._auto_runner.run(mission_id))
                    self._add_log(
                        "ok" if record.status == "completed" else "error",
                        "auto",
                        f"自动任务 {mission_id}: {record.status}",
                    )
                except Exception as e:
                    self._add_log("error", "auto", f"自动任务异常: {e}")
                finally:
                    if loop is not None:
                        loop.close()

            self._auto_thread = threading.Thread(target=run_auto, daemon=True)
            self._auto_thread.start()
            self._add_log("info", "auto", f"自动任务已启动: {mission_id} → {destinations}")
            return {"ok": True, "message": "自动任务已启动", "mission_id": mission_id}
        except Exception as e:
            self._add_log("error", "auto", f"启动失败: {e}")
            return {"ok": False, "message": str(e)}

    def auto_pause(self) -> dict:
        if self._auto_mission_id and self._mission_service:
            try:
                self._mission_service.pause(self._auto_mission_id)
                self._skill_dispatcher.execute_legacy(
                    "pause_navigation", {},
                    request_id=f"dev-auto-pause-{self._auto_mission_id}",
                    goal_id="dev-auto",
                    step_id="pause-navigation",
                    mission_id=self._auto_mission_id,
                    source="dev_console_auto",
                )
                if self._auto_cancel_event:
                    self._auto_cancel_event.set()
                self._add_log("info", "auto", "自动任务已请求暂停")
                return {"ok": True, "message": "已请求暂停"}
            except Exception as e:
                return {"ok": False, "message": str(e)}
        return {"ok": False, "message": "无运行中的自动任务"}

    def auto_resume(self) -> dict:
        """恢复自动任务 — 重新创建 runner 继续执行"""
        if not self._auto_mission_id or not self._mission_service:
            return {"ok": False, "message": "无暂停的自动任务"}

        try:
            record = self._mission_service.get_mission(self._auto_mission_id)
            if not record:
                return {"ok": False, "message": "任务不存在"}
            if record.status != "paused":
                return {"ok": False, "message": f"任务状态为 {record.status}，非 paused"}

            self._mission_service.resume(self._auto_mission_id)
            self._skill_dispatcher.execute_legacy(
                "resume_navigation", {},
                request_id=f"dev-auto-resume-{self._auto_mission_id}",
                goal_id="dev-auto",
                step_id="resume-navigation",
                mission_id=self._auto_mission_id,
                source="dev_console_auto",
            )
            self._auto_cancel_event = threading.Event()

            from .mission_runner import MissionRunner

            self._auto_runner = MissionRunner(
                self._mission_service,
                self._adapter,
                cancel_event=self._auto_cancel_event,
                step_callback=self._make_step_logger(self._auto_mission_id),
                skill_dispatcher=self._skill_dispatcher,
            )

            def run_auto():
                import asyncio
                loop = asyncio.new_event_loop()
                try:
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self._auto_runner.run(self._auto_mission_id))
                finally:
                    loop.close()

            self._auto_thread = threading.Thread(target=run_auto, daemon=True)
            self._auto_thread.start()
            self._add_log("info", "auto", "自动任务已恢复")
            return {"ok": True, "message": "自动任务已恢复"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def auto_stop(self) -> dict:
        if self._auto_cancel_event:
            self._auto_cancel_event.set()
        if self._auto_mission_id and self._mission_service:
            try:
                self._mission_service.request_abort(self._auto_mission_id)
            except Exception:
                pass
        self._auto_thread = None
        self._auto_runner = None
        self._auto_mission_id = None
        self._auto_cancel_event = None
        self._add_log("warn", "auto", "自动任务后继编排已终止，在途动作未确认停止")
        return {
            "ok": True,
            "message": "自动任务后继编排已终止；在途动作未确认停止",
            "physical_stop_confirmed": False,
        }

    # ── 状态查询 ──────────────────────────────────────────

    def get_state(self) -> dict:
        """返回完整状态 dict，供前端轮询"""
        robot_state = self._get_robot_state()
        services_status = self._get_services_status()
        fp_state = self._get_fp_state()

        # 解析 FP 跟踪状态
        trackers = fp_state.get("trackers", [])
        tracking_count = sum(1 for t in trackers if t.get("state") == 1)
        lost_count = sum(1 for t in trackers if t.get("state") == 0)

        if not trackers:
            camera_status = "无检测"
        elif tracking_count > 0:
            camera_status = f"跟踪中 ({tracking_count}个)"
        else:
            camera_status = f"丢失 ({lost_count}个)"

        tracked = [t for t in trackers if t.get("state") == 1]
        if tracked:
            t = tracked[0]
            box_position = f"({t['x']:.2f}, {t['y']:.2f}, {t['z']:.2f})"
        else:
            box_position = "--"

        return {
            "mode": self._mode,
            "manual": {
                "state": self._manual_state,
                "current_position": self._current_position,
                "target_position": self._target_position,
                "holding_box": self._holding_box,
                "can_undo": len(self._action_history) > 0,
                "undo_description": self._undo_description(),
                "undo_count": len(self._action_history),
                "is_paused": self._manual_state == PAUSED,
                "is_stopped": self._manual_state == STOPPED,
            },
            "auto": self._get_auto_state(),
            "robot": robot_state,
            "services": services_status,
            "box": {
                "camera_status": camera_status,
                "box_status": "持有箱子" if self._holding_box else "未持有",
                "box_position": box_position,
            },
        }

    def _get_robot_state(self) -> dict:
        """通过统一只读 skill 获取机器人实时状态。"""
        try:
            result = self._skill_dispatcher.execute_legacy(
                "get_robot_state", {},
                request_id=f"dev-state-{uuid.uuid4().hex[:12]}",
                goal_id="dev-console",
                step_id="get-robot-state",
                source="dev_console_query",
            )
            if result.get("status") == "ok":
                state = result.get("robot_state") or result.get("state")
                if isinstance(state, dict):
                    return state
        except Exception:
            pass
        return {
            "carry_state": "--",
            "mode": -1,
            "posture_state": "--",
            "hold_pose_active": False,
            "replay_active": False,
            "nav_status": "--",
        }

    def _get_fp_state(self) -> dict:
        """获取 FoundationPose 最新状态"""
        try:
            func = getattr(self._adapter, "get_fp_state", None)
            if callable(func):
                return func()
        except Exception:
            pass
        return {}

    def _get_services_status(self) -> dict:
        """通过适配器获取各 ROS2 服务在线状态（缓存 5 秒，避免拖慢轮询）"""
        now = time.time()
        if self._services_cache and (now - self._services_checked_at) < 5.0:
            return self._services_cache
        try:
            fn = getattr(self._adapter, "get_services_status", None)
            if callable(fn):
                self._services_cache = fn()
                self._services_checked_at = now
                return self._services_cache
        except Exception:
            pass
        return self._services_cache or {}

    def _get_auto_state(self) -> dict:
        if not self._auto_mission_id or not self._mission_service:
            return {
                "mission_id": None,
                "mission_status": None,
                "current_step": None,
                "total_steps": None,
            }
        try:
            record = self._mission_service.get_mission(self._auto_mission_id)
            if record:
                return {
                    "mission_id": self._auto_mission_id,
                    "mission_status": record.status,
                    "current_step": record.current_step_index,
                    "total_steps": len(record.plan),
                }
        except Exception:
            pass
        return {
            "mission_id": self._auto_mission_id,
            "mission_status": "unknown",
            "current_step": None,
            "total_steps": None,
        }

    # ── 日志 ──────────────────────────────────────────────

    def get_logs(self) -> list[dict]:
        return [
            {"timestamp": e.timestamp, "level": e.level, "action": e.action, "detail": e.detail}
            for e in self._log_buffer
        ]

    def export_logs(self) -> str:
        return json.dumps(self.get_logs(), ensure_ascii=False, indent=2)

    # ── 内部方法 ──────────────────────────────────────────

    def _start_background_action(self, action: "_ManualAction") -> None:
        """在后台线程中执行手动操作"""
        def run():
            if self._stop_flag.is_set():
                return

            request_id = f"dev-{uuid.uuid4().hex[:8]}"
            goal_id = "dev-manual"
            step_id = f"dev-{uuid.uuid4().hex[:8]}"

            with self._lock:
                self._manual_state = action.transitional_state
                self._target_position = action.args.get("target") if action.tool == "move_to" else self._target_position

            self._add_log("info", action.tool, f"开始: {action.log_label}")
            audit_logger.log_action_start(tool=action.tool, request_id=request_id,
                                          goal_id=goal_id, step_id=step_id,
                                          args=action.args)

            # 检查暂停标志（阻塞式等待继续）
            if self._pause_flag.is_set():
                self._add_log("info", "wait", "等待继续...")
                while self._pause_flag.is_set() and not self._stop_flag.is_set():
                    time.sleep(0.05)

            t0 = time.time()
            action_args = dict(action.args)
            if action.tool == "pick":
                action_args.setdefault("object_id", "manual-object")
                action_args.setdefault("timeout_s", 30.0)
            elif action.tool == "place":
                action_args.setdefault("target", {
                    "x": float(self._current_position.get("x", 0.0)),
                    "y": float(self._current_position.get("y", 0.0)),
                    "z": float(self._current_position.get("z", 0.0)),
                    "theta": float(self._current_position.get("theta", 0.0)),
                })
                action_args.setdefault("timeout_s", 30.0)
            action_annotations = {}
            if action.tool == "pick":
                # Manual coordinates are explicit operator input. Keep them
                # outside action args while allowing composite FP selection to
                # validate the material point used for this invocation.
                action_annotations = {
                    "slot_nav2_x": float(self._current_position.get("x", 0.0)),
                    "slot_nav2_y": float(self._current_position.get("y", 0.0)),
                }
            result = self._skill_dispatcher.execute_legacy(
                action.tool,
                action_args,
                request_id=request_id,
                goal_id=goal_id,
                step_id=step_id,
                source="dev_console_manual",
                annotations=action_annotations,
                idempotency_key=f"dev-console:{request_id}:{step_id}",
            )
            elapsed_ms = (time.time() - t0) * 1000

            with self._lock:
                if self._stop_flag.is_set():
                    self._add_log("warn", action.tool, "终止信号，丢弃结果")
                    audit_logger.log_action_end(tool=action.tool, request_id=request_id,
                                                goal_id=goal_id, step_id=step_id,
                                                status="cancelled", elapsed_ms=elapsed_ms)
                    return

                if self._pause_flag.is_set():
                    self._add_log("info", action.tool, "暂停中，等待继续...")
                    while self._pause_flag.is_set() and not self._stop_flag.is_set():
                        time.sleep(0.05)
                    if self._stop_flag.is_set():
                        audit_logger.log_action_end(tool=action.tool, request_id=request_id,
                                                    goal_id=goal_id, step_id=step_id,
                                                    status="cancelled", elapsed_ms=elapsed_ms)
                        return
                    self._add_log("info", action.tool, "继续执行")

                if result.get("status") == "ok":
                    if action.on_success_pos:
                        self._current_position = action.on_success_pos
                    if action.on_holding:
                        self._holding_box = True
                    if action.on_not_holding:
                        self._holding_box = False
                    if action.reverse:
                        self._action_history.append(action.reverse)
                        if len(self._action_history) > self.MAX_HISTORY:
                            self._action_history = self._action_history[-self.MAX_HISTORY:]
                    if self._defer_pause:
                        self._defer_pause = False
                        self._resume_state = action.on_success_state
                        self._pause_flag.set()
                        self._manual_state = PAUSED
                        self._add_log("ok", action.tool, f"完成: {action.log_label}，已暂停")
                    else:
                        self._manual_state = action.on_success_state
                        self._add_log("ok", action.tool, f"完成: {action.log_label}")
                    audit_logger.log_action_end(tool=action.tool, request_id=request_id,
                                                goal_id=goal_id, step_id=step_id,
                                                status="ok", elapsed_ms=elapsed_ms)
                else:
                    self._defer_pause = False
                    error_code = result.get("error_code", "UNKNOWN")

                    if result.get("outcome") == "unknown":
                        self._manual_state = STOPPED
                        self._add_log(
                            "error",
                            action.tool,
                            "结果未知，已阻止后继动作；请先人工核对机器人状态",
                        )
                        audit_logger.log_action_end(
                            tool=action.tool,
                            request_id=request_id,
                            goal_id=goal_id,
                            step_id=step_id,
                            status="unknown",
                            error_code=error_code,
                            elapsed_ms=elapsed_ms,
                        )
                        return

                    # place 失败时检查 _box_released: Gateway 内部可能已完成放置
                    if action.tool == "place":
                        try:
                            robot_state = self._get_robot_state()
                            if robot_state.get("box_released"):
                                self._holding_box = False
                                self._add_log("warn", action.tool,
                                              f"Gateway 显示箱子已释放, 清除 _holding_box "
                                              f"(error_code={error_code})")
                        except Exception:
                            pass

                    # pick 失败时检查 robot state 是否仍显示 holding
                    if action.tool == "pick":
                        try:
                            robot_state = self._get_robot_state()
                            if robot_state.get("carry_state") == "carry_wait_walk" or robot_state.get("hold_pose_active"):
                                self._holding_box = True
                                self._add_log("warn", action.tool,
                                              f"Gateway 显示已持有箱子, 设置 _holding_box=True "
                                              f"(error_code={error_code})")
                        except Exception:
                            pass

                    self._manual_state = action.resume_state or self._manual_state
                    self._add_log(
                        "error",
                        action.tool,
                        f"失败: {error_code} — {result.get('message', '')}",
                    )
                    audit_logger.log_action_end(tool=action.tool, request_id=request_id,
                                                goal_id=goal_id, step_id=step_id,
                                                status="error", error_code=error_code,
                                                elapsed_ms=elapsed_ms)

        self._exec_thread = threading.Thread(target=run, daemon=True)
        self._exec_thread.start()

    def _reset_manual_state(self) -> None:
        self._manual_state = IDLE
        self._action_history.clear()
        self._holding_box = False
        self._target_position = None
        self._defer_pause = False
        self._pause_flag.clear()
        self._stop_flag.clear()

    def _undo_description(self) -> str:
        if not self._action_history:
            return ""
        last = self._action_history[-1]
        if last.reverse_name == "move_to":
            t = last.reverse_args.get("target", {})
            return f"返回 ({t.get('x')}, {t.get('y')})"
        if last.reverse_name == "place":
            return "放下箱子"
        if last.reverse_name == "pick":
            return "搬起箱子"
        return last.reverse_name

    def _add_log(self, level: str, action: str, detail: str) -> None:
        entry = LogEntry(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            level=level,
            action=action,
            detail=detail,
        )
        with self._lock:
            self._log_buffer.append(entry)
            if len(self._log_buffer) > self.MAX_LOG_ENTRIES:
                self._log_buffer = self._log_buffer[-self.MAX_LOG_ENTRIES:]


# ── 内部辅助 ──────────────────────────────────────────────

@dataclass
class _ManualAction:
    """手动操作描述"""
    tool: str
    args: dict
    log_label: str
    transitional_state: str
    on_success_state: str
    on_success_pos: Optional[dict]
    reverse: Optional[ActionRecord]
    resume_state: str
    on_holding: bool = False
    on_not_holding: bool = False
