"""
分步调试控制器。

在手动模式和自动模式之间提供第三种操作模式：
加载 mission plan → 每步等待人工确认 → 执行 → 诊断事件采集 → 停在下一步前。

纯 Python 层，不依赖 ROS。通过 adapter 协议与机器人交互。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .models import PlanStep
from .mission_planner import MissionPlanner, load_scene_coords
from .recovery_diag import DiagEvent, DiagCollector
from .robot_state import safety_fsm, RobotState

logger = logging.getLogger("phi_robot.step_debug")


# ═══════════════════════════════════════════════════════════════
# 状态常量
# ═══════════════════════════════════════════════════════════════

ST_IDLE = "idle"
ST_PLAN_LOADED = "plan_loaded"
ST_STEP_READY = "step_ready"
ST_EXECUTING = "executing"
ST_PAUSED = "paused"
ST_STEP_DONE = "step_done"
ST_STEP_FAILED = "step_failed"
ST_ABORTED = "aborted"


# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════


@dataclass
class StepLog:
    """单步执行摘要日志"""
    step_id: str
    tool: str
    status: str         # "ok" | "error" | "skipped" | "force_skipped"
    error_code: str = ""
    message: str = ""
    elapsed_s: float = 0.0
    timestamp: str = ""

    @staticmethod
    def now() -> str:
        return datetime.now().strftime("%H:%M:%S")


# ═══════════════════════════════════════════════════════════════
# StepDebugController
# ═══════════════════════════════════════════════════════════════

# 格口优先级：load_plan 时按此顺序重排 destinations（数字越小越先执行）
_DEST_PRIORITY = {
    "ne": 0, "e": 1, "se": 2,
    "n":  3, "c": 4, "s":  5,
    "nw": 6, "w": 7, "sw": 8,
}


class StepDebugController:
    """分步调试模式控制器。

    状态机：
        IDLE → PLAN_LOADED → STEP_READY ⇄ EXECUTING → STEP_DONE/FAILED
                                ↑              ↓
                                └── PAUSED ←──┘
                                ABORTED（任意状态可到达）

    线程模型：
        - 所有公开方法持有 _lock，保证状态读写的线程安全
        - execute_current_step / retry_step 在后台线程执行 adapter 调用
        - 诊断事件通过 adapter.set_diag_callback → collector.push 收集
    """

    def __init__(self, adapter: Any, mission_service: Any = None):
        """
        Args:
            adapter: RobotAdapter 协议实现（如 RosAcceptanceAdapter）。
            mission_service: 可选，MissionService 实例（供 plan 生成使用）。
        """
        self._adapter = adapter
        self._mission_service = mission_service

        # ── 状态机 ──
        self._state: str = ST_IDLE
        self._plan: list[PlanStep] = []
        self._plan_steps: list[dict[str, Any]] = []  # 前端渲染用的步骤状态
        self._current_index: int = 0

        # ── 诊断 ──
        self._collector = DiagCollector(max_events=500)

        # ── 线程控制 ──
        self._lock = threading.RLock()
        self._stop_flag = threading.Event()
        self._exec_thread: Optional[threading.Thread] = None
        self._execution_id: int = 0  # 递增以标记旧线程无效

        # ── FP SelectTarget 单目标模式 ──
        self._select_target_active: bool = False

        # ── 日志 ──
        self._step_logs: list[StepLog] = []

        # ── 掉箱恢复：路径 B 跳过 nav goal 重发 ──
        self._skip_nav_resend: bool = False

        # ── 注册诊断回调到 adapter ──
        if hasattr(adapter, "set_diag_callback"):
            adapter.set_diag_callback(self._on_diag_event)
        else:
            logger.warning("adapter 不支持 set_diag_callback，诊断事件将不可用")

        logger.info("StepDebugController 已就绪，状态=%s", self._state)

    # ── 诊断回调 ──────────────────────────────────────────────

    def _on_diag_event(self, event: DiagEvent) -> None:
        """adapter 推送诊断事件 → 写入 collector"""
        self._collector.push(event)

    # ═══════════════════════════════════════════════════════════
    # 公开 API
    # ═══════════════════════════════════════════════════════════

    def load_plan(self, destinations: list[str]) -> dict[str, Any]:
        """加载 mission plan，返回步骤列表供前端渲染。

        Returns:
            {"ok": True, "plan": [...], "total_steps": N}
        """
        with self._lock:
            # 按固定优先级排序，不依赖用户输入顺序
            destinations = sorted(destinations, key=lambda d: _DEST_PRIORITY.get(d, 99))
            try:
                planner = MissionPlanner()
                plan = planner.plan(
                    goal_id="step_debug",
                    destination_order=destinations,
                    request_id="step_debug",
                )
            except Exception as e:
                logger.exception("load_plan: MissionPlanner 失败")
                return {"ok": False, "message": f"生成 plan 失败: {e}"}

            self._plan = plan
            self._current_index = 0
            self._plan_steps = [
                {
                    "step_id": s.step_id,
                    "task_index": s.task_index,
                    "tool": s.tool,
                    "args_summary": _summarize_step_args(s.tool, s.args),
                    "status": "pending",
                }
                for s in plan
            ]
            self._step_logs.clear()
            self._collector.clear()
            self._state = ST_PLAN_LOADED
            # 第一个步进入就绪
            if self._plan_steps:
                self._plan_steps[0]["status"] = "current"
                self._state = ST_STEP_READY

            logger.info("load_plan: %d steps loaded, destinations=%s",
                        len(plan), destinations)
            return {
                "ok": True,
                "plan": self._plan_steps,
                "raw_steps": [
                    {
                        "step_id": s.step_id,
                        "task_index": s.task_index,
                        "tool": s.tool,
                        "args": s.args,
                        "status": "pending",
                    }
                    for s in plan
                ],
                "total_steps": len(plan),
            }

    def execute_current_step(self) -> dict[str, Any]:
        """异步执行当前步骤。

        立即返回 {"ok": True, "status": "started"}。
        后台线程完成后通过 SSE 推送 state_change。
        """
        with self._lock:
            if self._state not in (ST_PLAN_LOADED, ST_STEP_READY, ST_STEP_DONE):
                return {"ok": False, "message": f"当前状态 {self._state} 不可执行"}

            if self._current_index >= len(self._plan):
                return {"ok": False, "message": "所有步骤已执行完毕"}

            # 旧线程未退出时 detach（不影响本次执行；旧线程返回后
            # 会因 execution_id 不匹配而静默退出）
            if self._exec_thread and self._exec_thread.is_alive():
                logger.warning(
                    "execute: 上一个执行线程仍在运行（卡在 adapter），"
                    "已 detach，旧线程结果将被忽略"
                )
                self._exec_thread = None

            step = self._plan[self._current_index]

            # ★ pick 步必须处于单目标模式（SelectTarget 已激活）
            if step.tool == "pick" and not self._select_target_active:
                return {
                    "ok": False,
                    "message": (
                        "搬箱子 (pick) 要求先进入单物体识别模式，"
                        "请点击「锁定单目标」按钮"
                    ),
                }

            self._state = ST_EXECUTING
            self._plan_steps[self._current_index]["status"] = "running"
            self._collector.clear()
            self._stop_flag.clear()
            self._execution_id += 1  # 递增 epoch，旧线程返回时将静默退出

        # 在锁外启动后台线程
        self._exec_thread = threading.Thread(
            target=self._run_step,
            args=(step,),
            daemon=True,
        )
        self._exec_thread.start()

        return {
            "ok": True,
            "status": "started",
            "step_id": step.step_id,
            "message": f"执行 {step.step_id}: {step.tool}",
        }

    def auto_run(self) -> dict[str, Any]:
        """自动顺序执行所有剩余步骤（后台线程）。

        与手动单步不同：线程内顺序调 adapter.execute()（同步阻塞），
        天然保证「上一步真正完成才执行下一步」。
        pick 步前自动进入 FP 单目标模式。
        失败即停，用户可随时通过 abort 终止。
        """
        with self._lock:
            if not self._plan_steps:
                return {"ok": False, "message": "未加载 plan"}
            if self._state not in (ST_STEP_READY, ST_STEP_DONE):
                return {
                    "ok": False,
                    "message": (
                        f"当前状态 {self._state} 不可启动自动执行，"
                        "请先加载 plan 或完成当前步"
                    ),
                }
            if self._current_index >= len(self._plan):
                return {"ok": False, "message": "所有步骤已执行完毕"}

            # pick 步前置条件检查（与手动执行一致）
            step = self._plan[self._current_index]
            if step.tool == "pick" and not self._select_target_active:
                return {
                    "ok": False,
                    "message": (
                        "当前步为搬箱子 (pick)，需先进入单物体识别模式。"
                        "请点击「锁定单目标」按钮后重试自动执行。"
                    ),
                }

            self._state = ST_EXECUTING
            self._collector.clear()
            self._stop_flag.clear()
            self._execution_id += 1

        self._auto_thread = threading.Thread(
            target=self._run_auto,
            args=(self._execution_id,),
            daemon=True,
        )
        self._auto_thread.start()

        return {
            "ok": True,
            "status": "auto_started",
            "total_steps": len(self._plan),
            "current_index": self._current_index,
        }

    def _run_auto(self, exec_id: int) -> None:
        """自动循环体（后台线程）：顺序执行剩余步骤，失败即停。

        不新建子线程 —— 直接在 _run_auto 线程内顺序调
        adapter.execute()（同步阻塞），一步完成才执行下一步。
        """
        try:
            while self._current_index < len(self._plan):
                # 每步前检查中止 / epoch 失效
                if self._stop_flag.is_set() or exec_id != self._execution_id:
                    logger.info(
                        "_run_auto: 中止 (stop=%s, epoch=%d/%d)",
                        self._stop_flag.is_set(), exec_id, self._execution_id,
                    )
                    return

                # 暂停等待（手动暂停 / 恢复）
                while self._state == ST_PAUSED and not self._stop_flag.is_set():
                    time.sleep(0.5)
                if self._stop_flag.is_set():
                    return

                step = self._plan[self._current_index]

                # ── pick 步自动进入 FP 单目标模式 ─────────
                if step.tool == "pick" and not self._select_target_active:
                    pick_x, pick_y = self._extract_slot_nav2_xy()
                    logger.info(
                        "_run_auto: 自动进入 FP 单目标模式 "
                        "(pick_x=%.2f, pick_y=%.2f)", pick_x, pick_y,
                    )
                    # 等待机器人稳定 + FP 收敛到新位置
                    time.sleep(5.0)
                    # SSE 反馈：正在锁定
                    with self._lock:
                        if exec_id == self._execution_id:
                            self._step_logs.append(StepLog(
                                step_id=step.step_id,
                                tool="select_target",
                                status="running",
                                message=f"正在锁定单目标 ({pick_x:.2f}, {pick_y:.2f})...",
                                timestamp=StepLog.now(),
                            ))

                    locked = self._auto_toggle_select_target()
                    if not locked:
                        # ★ 第一次锁定失败 → 等 1 秒重试
                        with self._lock:
                            if exec_id == self._execution_id:
                                self._step_logs.append(StepLog(
                                    step_id=step.step_id,
                                    tool="select_target",
                                    status="running",
                                    message=f"首次锁定失败，等待 1 秒后重试...",
                                    timestamp=StepLog.now(),
                                ))
                        if self._stop_flag.is_set() or exec_id != self._execution_id:
                            return
                        time.sleep(1.0)
                        if self._stop_flag.is_set() or exec_id != self._execution_id:
                            return
                        locked = self._auto_toggle_select_target()

                    if not locked:
                        with self._lock:
                            if exec_id == self._execution_id:
                                self._step_logs.append(StepLog(
                                    step_id=step.step_id,
                                    tool="select_target",
                                    status="error",
                                    error_code="FP_LOCK_FAILED",
                                    message=f"重试锁定仍失败: 未在 ({pick_x:.2f}, {pick_y:.2f}) 匹配到物体",
                                    timestamp=StepLog.now(),
                                ))
                                self._plan_steps[self._current_index][
                                    "status"
                                ] = "failed"
                                self._plan_steps[self._current_index][
                                    "error_code"
                                ] = "FP_LOCK_FAILED"
                                self._state = ST_STEP_FAILED
                        return
                    # SSE 反馈：锁定成功
                    with self._lock:
                        if exec_id == self._execution_id:
                            obj_id = self._adapter._fp_target_object_id
                            self._step_logs.append(StepLog(
                                step_id=step.step_id,
                                tool="select_target",
                                status="ok",
                                message=f"已锁定单目标 object_id={obj_id}",
                                timestamp=StepLog.now(),
                            ))

                    # ★ 等待 FP 下一帧发布过滤后的位姿，确保 adapter 收到锁定后的 pose
                    time.sleep(0.1)

                # ── 标记 running ──────────────────────────
                with self._lock:
                    if exec_id != self._execution_id:
                        return
                    self._plan_steps[self._current_index]["status"] = "running"

                # ── 执行（同步阻塞，真正等机器人完成） ────
                t_start = time.monotonic()
                try:
                    result = self._adapter.execute(
                        tool=step.tool,
                        args=step.args,
                        request_id="step_debug_auto",
                        goal_id="step_debug_auto",
                        step_id=step.step_id,
                    )
                except Exception as e:
                    logger.exception("_run_auto: adapter.execute 异常: %s", e)
                    result = {
                        "status": "error",
                        "error_code": "ADAPTER_EXCEPTION",
                        "message": str(e),
                    }

                elapsed = time.monotonic() - t_start
                is_ok = result.get("status") == "ok"

                # ── 更新状态机 ────────────────────────────
                with self._lock:
                    if self._stop_flag.is_set() or exec_id != self._execution_id:
                        return

                    # ── pick 成功时附加位姿和动作信息 ──
                    if step.tool == "pick" and is_ok:
                        msg_parts = [result.get("message", "")]
                        px = result.get("pose_x")
                        py = result.get("pose_y")
                        pz = result.get("pose_z")
                        if px is not None:
                            msg_parts.insert(0, f"位姿=({px:.3f},{py:.3f},{pz:.3f})")
                        action = result.get("selected_action")
                        if action:
                            msg_parts.append(f"action={action}")
                        dist = result.get("distance")
                        if dist is not None:
                            msg_parts.append(f"dist={dist:.3f}m")
                        rich_message = " | ".join(msg_parts)
                    else:
                        rich_message = result.get("message", "")

                    log = StepLog(
                        step_id=step.step_id,
                        tool=step.tool,
                        status="ok" if is_ok else "error",
                        error_code=result.get("error_code", "") or "",
                        message=rich_message,
                        elapsed_s=round(elapsed, 2),
                        timestamp=StepLog.now(),
                    )
                    self._step_logs.append(log)

                    if is_ok:
                        self._plan_steps[self._current_index][
                            "status"
                        ] = "completed"
                        self._current_index += 1

                        # ── pick 完成后立即退出 FP 单目标模式 ──
                        # 搬起结束 → 恢复多目标识别，后续 move_to 和 place 不需要
                        if step.tool == "pick" and self._select_target_active:
                            try:
                                if hasattr(self._adapter, "select_target_public"):
                                    self._adapter.select_target_public(
                                        select=False, step_id="auto_after_pick"
                                    )
                            except Exception as e:
                                logger.error(
                                    "_run_auto pick 后退出 SelectTarget 失败: %s", e
                                )
                            self._select_target_active = False
                            logger.info("_run_auto: pick 完成，已退出单目标模式")
                            # SSE 反馈：已退出
                            self._step_logs.append(StepLog(
                                step_id=step.step_id,
                                tool="select_target_exit",
                                status="ok",
                                message="已退出单目标模式（恢复多物体识别）",
                                timestamp=StepLog.now(),
                            ))

                        if self._current_index >= len(self._plan):
                            self._state = ST_IDLE
                            logger.info("_run_auto: 全部步骤完成 (%d 步)", len(self._plan))
                        else:
                            self._plan_steps[self._current_index][
                                "status"
                            ] = "current"
                            self._state = ST_STEP_DONE
                            logger.info(
                                "_run_auto: %s 完成 → 前进到 %s (%d/%d)",
                                step.step_id,
                                self._plan[self._current_index].step_id,
                                self._current_index + 1,
                                len(self._plan),
                            )
                    else:
                        self._plan_steps[self._current_index]["status"] = "failed"
                        self._plan_steps[self._current_index]["error_code"] = (
                            result.get("error_code", "")
                        )
                        self._plan_steps[self._current_index]["replan_target"] = (
                            result.get("replan_target")
                        )
                        self._state = ST_STEP_FAILED
                        logger.warning(
                            "_run_auto: %s 失败 (%s): %s",
                            step.step_id, log.error_code, log.message,
                        )
                        return  # 失败即停

            # ── 全部完成：自动退出 FP 单目标模式 ─────────
            if self._select_target_active:
                try:
                    if hasattr(self._adapter, "select_target_public"):
                        self._adapter.select_target_public(
                            select=False, step_id="auto_done"
                        )
                except Exception as e:
                    logger.error(
                        "_run_auto 退出 SelectTarget 失败: %s", e
                    )
                self._select_target_active = False

        except Exception as e:
            logger.exception("_run_auto: 未预期异常")
            with self._lock:
                if exec_id == self._execution_id:
                    self._state = ST_STEP_FAILED

    def _auto_toggle_select_target(self) -> bool:
        """自动进入 FP 单目标模式（线程内调用，不经过 HTTP）。

        Returns:
            True 表示锁定成功，False 表示失败。
        """
        pick_x, pick_y = self._extract_slot_nav2_xy()
        if pick_x is None:
            logger.error("_auto_toggle_select_target: 无法提取 slot 坐标")
            return False

        if not hasattr(self._adapter, "select_target_public"):
            logger.error(
                "_auto_toggle_select_target: adapter 不支持 SelectTarget"
            )
            return False

        result = self._adapter.select_target_public(
            select=True,
            pick_x=pick_x,
            pick_y=pick_y,
            material_points_xy=[pick_x, pick_y],
            step_id="auto",
        )

        matched_id = result.get("matched_object_id", -1) if result else -1
        if result and result.get("success") and matched_id >= 0:
            self._select_target_active = True
            logger.info(
                "_auto_toggle_select_target: 自动锁定成功, object_id=%s",
                matched_id,
            )
            return True
        else:
            msg = (
                result.get("message", "未知错误")
                if result
                else "SelectTarget 服务不可用"
            )
            if result and result.get("success") and matched_id < 0:
                msg = f"FP 服务返回成功但未匹配到物体 (matched_object_id={matched_id})"
            logger.error(
                "_auto_toggle_select_target: 自动锁定失败: %s", msg
            )
            return False

    def retry_step(self) -> dict[str, Any]:
        """重试当前步骤（状态机表现同 execute_current_step）。"""
        with self._lock:
            if self._state not in (ST_STEP_DONE, ST_STEP_FAILED):
                return {"ok": False,
                        "message": f"当前状态 {self._state} 不可重试，请先执行步骤"}

            # 旧线程未退出时 detach（不影响本次重试）
            if self._exec_thread and self._exec_thread.is_alive():
                logger.warning(
                    "retry: 上一个执行线程仍在运行，已 detach，旧线程结果将被忽略"
                )
                self._exec_thread = None

            # STEP_DONE 时 _current_index 已推进到下一步，
            # 需回退以重试刚完成的步骤
            if self._state == ST_STEP_DONE:
                self._current_index -= 1

            # 重试不推进 index，回到同一 step
            step = self._plan[self._current_index]

            # ★ pick 步必须处于单目标模式
            if step.tool == "pick" and not self._select_target_active:
                return {
                    "ok": False,
                    "message": (
                        "搬箱子 (pick) 要求先进入单物体识别模式，"
                        "请点击「锁定单目标」按钮"
                    ),
                }

            self._state = ST_EXECUTING
            self._plan_steps[self._current_index]["status"] = "running"
            self._collector.clear()
            self._stop_flag.clear()
            self._execution_id += 1  # 递增 epoch，旧线程返回时将静默退出

        self._exec_thread = threading.Thread(
            target=self._run_step,
            args=(step,),
            daemon=True,
        )
        self._exec_thread.start()

        return {
            "ok": True,
            "status": "started",
            "step_id": step.step_id,
            "message": f"重试 {step.step_id}: {step.tool}",
        }

    def skip_step(self) -> dict[str, Any]:
        """跳过当前步骤，任意状态均可触发。

        执行中/暂停时：置 stop_flag + 递增 execution_id 终止旧线程，
        然后跳过当前步。
        """
        with self._lock:
            if self._state in (ST_EXECUTING, ST_PAUSED):
                # 终止正在执行的旧线程
                self._stop_flag.set()
                self._execution_id += 1
                logger.warning(
                    "skip_step: 强制终止执行中步骤 (state=%s), 新 execution_id=%d",
                    self._state, self._execution_id,
                )
                return self._do_skip("force_skipped")

            return self._do_skip("skipped")

    def force_skip(self) -> dict[str, Any]:
        """强制跳过（含失败步），记录日志供事后审计。

        仅允许在就绪/完成/失败状态下调用。
        执行中（EXECUTING/PAUSED）拒绝，避免与 _run_step 竞争 _current_index。
        """
        with self._lock:
            if self._state in (ST_EXECUTING, ST_PAUSED):
                return {
                    "ok": False,
                    "message": f"当前状态 {self._state}，步骤正在执行中，请先 pause 或 abort 后再跳过",
                }
            return self._do_skip("force_skipped")

    def _do_skip(self, skip_type: str) -> dict[str, Any]:
        """内部：标记当前步为 skip，前进 index。调用方需持有 _lock。"""
        step = self._plan[self._current_index]
        self._plan_steps[self._current_index]["status"] = skip_type
        self._step_logs.append(StepLog(
            step_id=step.step_id, tool=step.tool,
            status=skip_type, timestamp=StepLog.now(),
        ))
        logger.warning("skip_step: %s %s (%s)", step.step_id, step.tool, skip_type)

        self._current_index += 1
        if self._current_index >= len(self._plan):
            self._state = ST_IDLE
            return {"ok": True, "message": "全部步骤已完成（最后一步被跳过）", "finished": True}

        self._plan_steps[self._current_index]["status"] = "current"
        self._state = ST_STEP_READY
        return {"ok": True, "message": f"已跳过，当前步骤: {self._plan[self._current_index].step_id}"}

    def pause_execution(self) -> dict[str, Any]:
        """暂停正在执行的步骤（调 /pause_navigation）。"""
        with self._lock:
            if self._state != ST_EXECUTING:
                return {"ok": False, "message": f"当前状态 {self._state}，无可暂停的步骤"}
            self._state = ST_PAUSED

        # 锁外调 adapter（可能阻塞）
        try:
            if hasattr(self._adapter, "pause_navigation"):
                self._adapter.pause_navigation()
        except Exception as e:
            logger.error("pause_navigation 失败: %s", e)

        return {"ok": True, "message": "已暂停导航"}

    def resume_execution(self) -> dict[str, Any]:
        """恢复暂停的步骤。"""
        with self._lock:
            if self._state != ST_PAUSED:
                return {"ok": False, "message": f"当前状态 {self._state}，非暂停状态"}

        try:
            if hasattr(self._adapter, "resume_navigation"):
                self._adapter.resume_navigation()
        except Exception as e:
            logger.error("resume_navigation 失败: %s", e)

        with self._lock:
            # 重新检查：resume_navigation 解除阻塞后，
            # _run_step 可能已经完成并将状态设为 STEP_DONE/STEP_FAILED，
            # 此时不可覆盖为 EXECUTING
            if self._state == ST_PAUSED:
                self._state = ST_EXECUTING
            else:
                logger.warning(
                    "resume_execution: 恢复期间 _run_step 已完成，"
                    "当前状态=%s，不再覆盖为 EXECUTING", self._state
                )
        return {"ok": True, "message": "已恢复执行"}

    # ── FP SelectTarget 手动切换 ────────────────────────────

    def _extract_slot_nav2_xy(self) -> tuple[float | None, float | None]:
        """从已加载 plan 中提取当前 task 的物料槽位 Nav2 坐标。

        遍历所有 step，找到当前 task_index 中 tool="move_to" 的第一步，
        取其 slot_nav2_x/slot_nav2_y 作为 pick 坐标。
        """
        if self._current_index >= len(self._plan):
            return (None, None)
        task_idx = self._plan[self._current_index].task_index
        for step in self._plan:
            if step.task_index == task_idx and step.tool == "move_to":
                sx = step.args.get("slot_nav2_x")
                sy = step.args.get("slot_nav2_y")
                if sx is not None and sy is not None:
                    return (float(sx), float(sy))
        return (None, None)

    def toggle_select_target(self) -> dict[str, Any]:
        """切换 FoundationPose 单目标模式。

        从已加载 plan 中提取物料点 Nav2 坐标作为 pick 点，
        调用 /foundationpose/select_target 进入/退出单目标模式。
        不依赖 _enable_replanning —— 手动模式独立可用。
        """
        # ── 退出单目标 ──
        if self._select_target_active:
            if hasattr(self._adapter, "select_target_public"):
                self._adapter.select_target_public(select=False, step_id="manual")
            self._select_target_active = False
            logger.info("select_target_toggle: 手动退出单目标模式")
            return {"ok": True, "active": False, "message": "已退出单目标模式"}

        # ── 进入单目标 ──
        pick_x, pick_y = self._extract_slot_nav2_xy()
        if pick_x is None:
            return {
                "ok": False,
                "message": "未加载 plan 或 plan 中无 slot 坐标，无法确定 pick 点",
            }

        if not hasattr(self._adapter, "select_target_public"):
            return {"ok": False, "message": "adapter 不支持 SelectTarget"}

        result = self._adapter.select_target_public(
            select=True,
            pick_x=pick_x,
            pick_y=pick_y,
            material_points_xy=[pick_x, pick_y],
            step_id="manual",
        )

        matched_id = result.get("matched_object_id", -1) if result else -1
        if result and result.get("success") and matched_id >= 0:
            self._select_target_active = True
            return {
                "ok": True,
                "active": True,
                "object_id": matched_id,
                "message": result.get("message", "已锁定"),
            }
        else:
            msg = result.get("message", "锁定失败") if result else "SelectTarget 服务不可用"
            if result and result.get("success") and matched_id < 0:
                msg = f"FP 服务返回成功但未匹配到物体 (matched_object_id={matched_id})"
            return {"ok": False, "active": False, "message": msg}

    def abort(self) -> dict[str, Any]:
        """终止调试会话，重置所有状态。

        终止后状态回到 idle，可立即加载新 plan 重新开始实验，
        无需重启 TaskManger。

        核心策略：
        1. 置 _stop_flag + 递增 _execution_id 使旧线程失效
        2. 试等线程退出（5s），超时则 detach（孤儿线程返回后自行检测
           execution_id 不匹配而静默退出，不会污染新执行的状态）
        3. 清空 plan / 状态 / adapter，回到 idle
        """
        with self._lock:
            self._stop_flag.set()
            self._execution_id += 1
            self._state = ST_IDLE
            self._plan = []
            self._plan_steps = []
            self._current_index = 0
            self._step_logs.clear()
            self._collector.clear()

        # 等待后台线程退出
        if self._exec_thread and self._exec_thread.is_alive():
            self._exec_thread.join(timeout=5.0)
            if self._exec_thread.is_alive():
                logger.warning(
                    "abort: 线程 %s 5s 内未退出（卡在 adapter 调用中），"
                    "已 detach。旧线程返回后会因 execution_id 不匹配而静默退出。",
                    self._exec_thread.name,
                )
                self._exec_thread = None  # detach，不阻塞后续 execute

        # 退出 FP 单目标模式（如果已激活）
        if self._select_target_active:
            try:
                if hasattr(self._adapter, "select_target_public"):
                    self._adapter.select_target_public(select=False, step_id="abort")
            except Exception as e:
                logger.error("abort 退出 SelectTarget 失败: %s", e)
            self._select_target_active = False

        # 清理 adapter 状态
        try:
            if hasattr(self._adapter, "reset"):
                self._adapter.reset()
        except Exception as e:
            logger.error("abort 清理 adapter 失败: %s", e)

        logger.info("abort: 调试会话已终止，状态已重置为 idle")
        return {"ok": True, "message": "调试会话已终止，可重新加载任务"}

    # ═══════════════════════════════════════════════════════════
    # 掉箱处理（手动重规划）
    # ═══════════════════════════════════════════════════════════

    def enable_drop_detector(self) -> dict[str, Any]:
        """开启掉箱检测节点并返回当前状态"""
        with self._lock:
            if hasattr(self._adapter, "enable_drop_detector"):
                return self._adapter.enable_drop_detector()
            return {"ok": False, "message": "adapter 不支持掉箱检测"}

    def disable_drop_detector(self) -> dict[str, Any]:
        """关闭掉箱检测节点"""
        with self._lock:
            if hasattr(self._adapter, "disable_drop_detector"):
                return self._adapter.disable_drop_detector()
            return {"ok": False, "message": "adapter 不支持掉箱检测"}

    def pause_robot(self) -> dict[str, Any]:
        """暂停 Nav2 导航。

        🆕 改用 /nav_reached 状态模型：发送暂停信号后等待 Nav2 确认到达。
        上游订阅 nav_reached 属性即可判断机器人是否已停。
        """
        with self._lock:
            if hasattr(self._adapter, "pause_navigation"):
                self._adapter.pause_navigation()
                # 🆕 检查 /nav_reached 状态确认暂停生效
                nav_reached = None
                if hasattr(self._adapter, "nav_reached"):
                    nav_reached = self._adapter.nav_reached
                return {
                    "ok": True,
                    "message": "已发送暂停信号",
                    "nav_reached": nav_reached,  # 供前端确认
                }
            return {"ok": False, "message": "adapter 不支持暂停"}

    def resume_robot(self) -> dict[str, Any]:
        """恢复 Nav2 导航（发送 /nav_pause=false）。"""
        with self._lock:
            if hasattr(self._adapter, "resume_navigation"):
                self._adapter.resume_navigation()
                return {"ok": True, "message": "已发送恢复信号"}
            return {"ok": False, "message": "adapter 不支持恢复"}

    def stand_robot(self) -> dict[str, Any]:
        """切换机器人为站立模式"""
        with self._lock:
            if hasattr(self._adapter, "stand_robot"):
                return self._adapter.stand_robot()
            return {"ok": False, "message": "adapter 不支持站立"}

    def step_back_robot(self) -> dict[str, Any]:
        """让机器人后退两步（调用 /Step_back 服务）。"""
        with self._lock:
            if hasattr(self._adapter, "step_back_robot"):
                return self._adapter.step_back_robot()
            return {"ok": False, "message": "adapter 不支持后退"}

    # ═══════════════════════════════════════════════════════════
    # 通信预案（计划卡住 → 回物料点重试）
    # ═══════════════════════════════════════════════════════════

    def reset_to_stock_point(self) -> dict[str, Any]:
        """将计划回退到最近一次走到物料点的步骤。

        纯计划状态回退，不发送 Nav2 指令。适用于所有突发情况。

        处理流程：
            1. abort 当前执行（设 stop_flag + 递增 execution_id）
            2. 找到最近的一个 move_to 物料点步骤
            3. 将该步骤及之后所有步骤重置为 "pending"
            4. 将该物料点步骤设为 "current"，状态回到 step_ready
        """
        with self._lock:
            # ── 1. 校验：必须有已加载的计划 ──
            if not self._plan_steps:
                return {"ok": False, "message": "没有加载计划"}

            # ── 2. 中止当前执行 ──
            self._stop_flag.set()
            self._execution_id += 1

            if self._exec_thread and self._exec_thread.is_alive():
                logger.warning(
                    "reset_to_stock: 上一个执行线程仍在运行，已 detach"
                )
                self._exec_thread = None

            # ── 3. 找到最近的一个 move_to 物料点步骤 ──
            stock_index = self._find_last_stock_step(self._current_index)
            if stock_index < 0:
                return {"ok": False, "message": "计划中没有找到物料点步骤"}

            # ── 4. 将 stock_index 及之后所有步骤重置 ──
            for i in range(stock_index, len(self._plan_steps)):
                self._plan_steps[i]["status"] = "pending"
                self._plan_steps[i].pop("error_code", None)
                self._plan_steps[i].pop("replan_target", None)
            self._plan_steps[stock_index]["status"] = "current"

            self._current_index = stock_index
            self._state = ST_STEP_READY

            # ── 5. 可选：取消当前导航 ──
            if hasattr(self._adapter, 'pause_navigation'):
                try:
                    self._adapter.pause_navigation()
                except Exception:
                    pass

            # ── 6. 退出 FP 单目标模式 ──
            self._select_target_active = False

            logger.info(
                "reset_to_stock: 已回退到物料点步骤 step %d, 状态 step_ready",
                stock_index,
            )

            return {
                "ok": True,
                "message": f"已回到物料点步骤 (step {stock_index})",
                "stock_step_index": stock_index,
            }

    def _find_last_stock_step(self, current_index: int) -> int:
        """从 current_index 往前找最近的一个 move_to 物料点步骤。

        物料点步骤特征：tool == "move_to" 且 args 中含 slot_nav2_x。
        """
        for i in range(current_index, -1, -1):
            if i >= len(self._plan):
                continue
            step = self._plan[i]
            if step.tool == "move_to" and "slot_nav2_x" in step.args:
                return i
        # fallback: 从计划开头找第一个物料点步骤
        for i, step in enumerate(self._plan):
            if step.tool == "move_to" and "slot_nav2_x" in step.args:
                return i
        return -1

    def go_to_robot_origin(self) -> dict[str, Any]:
        """导航机器人到预定义的机器人原点。

        发送 Nav2 move_to 指令，后台线程执行。
        任何状态均可触发，有已加载计划即可。
        """
        coords = load_scene_coords()
        origin = coords.get("robot_origin")
        if not origin:
            return {"ok": False, "message": "scene_coords.json 中未配置 robot_origin"}

        with self._lock:
            if not self._plan_steps:
                return {"ok": False, "message": "没有加载计划"}

            # 中止当前执行
            self._stop_flag.set()
            self._execution_id += 1
            if self._exec_thread and self._exec_thread.is_alive():
                logger.warning(
                    "go_origin: 上一个执行线程仍在运行，已 detach"
                )
                self._exec_thread = None

            origin_step = PlanStep(
                step_id="go_robot_origin",
                task_index=-1,
                tool="move_to",
                args={
                    "target": {
                        "x": origin["x"],
                        "y": origin["y"],
                        "z": 0.0,
                        "theta": origin.get("theta", 0.0),
                    },
                },
            )

            self._state = ST_EXECUTING
            origin_exec_id = self._execution_id

        # 在锁外启动后台线程
        self._exec_thread = threading.Thread(
            target=self._run_go_origin,
            args=(origin_step, origin_exec_id),
            daemon=True,
        )
        self._exec_thread.start()

        return {
            "ok": True,
            "message": f"正在导航到机器人原点 ({origin['x']}, {origin['y']})",
        }

    def _run_go_origin(self, move_step: PlanStep, my_exec_id: int) -> None:
        """后台线程：导航到机器人原点。"""
        t_start = time.monotonic()
        try:
            result = self._adapter.execute(
                tool=move_step.tool,
                args=move_step.args,
                request_id="go_origin",
                goal_id="go_origin",
                step_id=move_step.step_id,
            )
        except Exception as e:
            logger.exception("go_origin adapter.execute 异常: %s", e)
            result = {
                "status": "error",
                "error_code": "ADAPTER_EXCEPTION",
                "message": str(e),
            }

        elapsed = time.monotonic() - t_start

        with self._lock:
            if self._stop_flag.is_set() or my_exec_id != self._execution_id:
                return  # 被新操作覆盖

            log = StepLog(
                step_id=move_step.step_id,
                tool=move_step.tool,
                status="ok" if result.get("status") == "ok" else "error",
                error_code=result.get("error_code", "") or "",
                message=result.get("message", ""),
                elapsed_s=round(elapsed, 2),
                timestamp=StepLog.now(),
            )
            self._step_logs.append(log)

            self._state = ST_IDLE

            logger.info(
                "go_origin: 到达机器人原点 (%.1fs), 状态 idle", elapsed
            )

    def identify_dropped_box(self) -> dict[str, Any]:
        """调用 FP 识别掉落箱子（MODE_DROPPED）"""
        with self._lock:
            if not hasattr(self._adapter, "identify_dropped_box"):
                return {"ok": False, "message": "adapter 不支持掉箱识别"}
            # 从当前 plan 中提取所有物料点和目标点坐标
            material_xy = []
            target_xy = []
            if self._plan:
                for s in self._plan:
                    if s.tool == "move_to":
                        sx = float(s.args.get("slot_nav2_x", 0))
                        sy = float(s.args.get("slot_nav2_y", 0))
                        if sx or sy:
                            material_xy.extend([sx, sy])
                        gx = float(s.args.get("grid_nav2_x", 0))
                        gy = float(s.args.get("grid_nav2_y", 0))
                        if gx or gy:
                            target_xy.extend([gx, gy])
            # 去重：取每对的唯一值
            seen = set()
            material_xy_dedup = []
            for i in range(0, len(material_xy), 2):
                key = (round(material_xy[i], 2), round(material_xy[i+1], 2))
                if key not in seen:
                    seen.add(key)
                    material_xy_dedup.extend([material_xy[i], material_xy[i+1]])
            return self._adapter.identify_dropped_box(
                material_points_xy=material_xy_dedup,
                target_points_xy=target_xy,
            )

    def replan_pick(self) -> dict[str, Any]:
        """用掉落箱子位姿执行重规划搬起"""
        with self._lock:
            if hasattr(self._adapter, "replan_pick"):
                result = self._adapter.replan_pick()
                return {"ok": result.get("status") == "ok",
                        "message": result.get("message", ""),
                        "result": result}
            return {"ok": False, "message": "adapter 不支持重规划搬起"}

    # ═══════════════════════════════════════════════════════════
    # 自动化掉箱处理（auto mode 专用）
    # ═══════════════════════════════════════════════════════════

    def auto_drop_recovery(self) -> dict[str, Any]:
        """自动化掉箱恢复入口：暂停→站立→reset→识别→重规划→恢复自动。

        仅自动执行期间可用，当前步骤不可为 pick/place。
        spawn 后台线程执行恢复序列，立即返回。
        """
        with self._lock:
            # 校验：自动执行必须正在运行
            if not self._auto_thread or not self._auto_thread.is_alive():
                return {"ok": False,
                        "message": "自动执行未在运行，无需掉箱恢复"}

            # 校验：当前步骤不可为 pick / place（搬起/放下动作不可打断）
            if self._current_index < len(self._plan):
                cur_tool = self._plan[self._current_index].tool
                if cur_tool in ("pick", "place"):
                    return {"ok": False,
                            "message": f"当前步骤为 {cur_tool}，不可在搬起/放下期间触发掉箱恢复"}

            saved_index = self._current_index

            # 中止当前自动执行线程
            self._stop_flag.set()
            self._execution_id += 1
            recovery_epoch = self._execution_id

            # 清空旧诊断事件，避免残留的 DiagEvent 污染恢复周期
            self._collector.clear()

            # 暂停机器人
            try:
                if hasattr(self._adapter, "pause_navigation"):
                    self._adapter.pause_navigation()
            except Exception as e:
                logger.error("auto_drop_recovery: pause_navigation 失败: %s", e)

            # 启动恢复线程
            t = threading.Thread(
                target=self._run_drop_recovery,
                args=(recovery_epoch, saved_index),
                daemon=True,
            )
            self._auto_thread = t
            t.start()

        return {"ok": True, "status": "recovery_started",
                "saved_index": saved_index}

    def _run_drop_recovery(self, exec_id: int, saved_index: int) -> None:
        """自动化掉箱恢复序列（后台线程）。

        序列：暂停(已做) → 等1s → 站立 → 等2s → 等3s
              → 识别掉落箱(最多2次) → 等2s → 重规划搬起 → 退出单目标 → 恢复自动
        """
        log = self._append_recovery_log

        def _aborted() -> bool:
            return self._stop_flag.is_set() or exec_id != self._execution_id

        try:
            # ── 步骤 1: 等待稳定 ──
            if _aborted():
                return
            time.sleep(1.0)

            # ── 步骤 2: 站立模式 ──
            if _aborted():
                return
            log("running", "正在切换站立模式...")
            try:
                if hasattr(self._adapter, "stand_robot"):
                    self._adapter.stand_robot()
                # ★ 同步 Python safety_fsm，否则 can_walk() 仍返回 False
                safety_fsm.force_state(RobotState.STANDING)
                log("ok", "站立模式完成")
            except Exception as e:
                log("error", f"站立模式失败: {e}")
                self._fail_recovery(exec_id)
                return

            # ── 步骤 3: 等待站稳 + FP 稳定 ──
            if _aborted():
                return
            time.sleep(3.0)

            # ── 步骤 4: 后退一步（给重规划留空间）──
            if _aborted():
                return
            log("running", "正在后退一步 (/Step_back)...")
            try:
                if hasattr(self._adapter, "step_back_robot"):
                    sb_result = self._adapter.step_back_robot()
                    if sb_result.get("ok"):
                        log("ok", "后退完成")
                    else:
                        log("error", f"后退失败（非致命，继续流程）: {sb_result.get('message', '')}")
                else:
                    log("error", "adapter 不支持后退，跳过")
            except Exception as e:
                log("error", f"后退异常（非致命，继续流程）: {e}")

            # ── 步骤 5: 等待 FP 从新位置重新收敛 ──
            if _aborted():
                return
            time.sleep(3.0)

            # ── 步骤 6: 识别掉落箱（最多重试 2 次）──
            if _aborted():
                return
            log("running", "正在识别掉落箱 (FP MODE_DROPPED)...")
            ident_result = None
            for attempt in range(2):
                if _aborted():
                    return
                try:
                    if hasattr(self, "identify_dropped_box"):
                        ident_result = self.identify_dropped_box()
                except Exception as e:
                    log("error", f"识别掉落箱异常: {e}")
                    self._fail_recovery(exec_id)
                    return

                if ident_result and ident_result.get("ok"):
                    break  # 成功，跳出重试循环

                if attempt == 0:
                    log("running", "首次识别失败，等待 1 秒后重试...")
                    time.sleep(1.0)

            if not ident_result or not ident_result.get("ok"):
                msg = ident_result.get("message", "未知错误") if ident_result else "识别服务不可用"
                log("error", f"❌ 识别失败（重试后仍失败）: {msg}")
                self._fail_recovery(exec_id)
                return

            matched_id = ident_result.get("matched_object_id", -1)
            pose = ident_result.get("pose")
            pose_str = ""
            if pose:
                pose_str = f" ({pose.x:.2f}, {pose.y:.2f})" if hasattr(pose, 'x') else ""
            log("ok", f"✅ 识别成功: 箱子 #{matched_id}{pose_str}")

            # ── 步骤 7: 等待 ──
            if _aborted():
                return
            time.sleep(2.0)

            # ── 步骤 8: 重规划搬起 ──
            if _aborted():
                return
            log("running", "正在执行重规划搬起...")
            try:
                replan_result = self.replan_pick()
            except Exception as e:
                log("error", f"重规划搬起异常: {e}")
                self._fail_recovery(exec_id)
                return

            if not replan_result or not replan_result.get("ok"):
                msg = replan_result.get("message", "未知错误") if replan_result else "搬起服务不可用"
                log("error", f"❌ 搬起失败: {msg}")
                self._fail_recovery(exec_id)
                return

            log("ok", "✅ 重规划搬起完成")

            # ── 步骤 9: 退出单目标模式 ──
            if _aborted():
                return
            try:
                if (hasattr(self._adapter, "select_target_public")
                        and self._select_target_active):
                    self._adapter.select_target_public(
                        select=False, step_id="recovery_exit"
                    )
            except Exception as e:
                logger.error("recovery: 退出 SelectTarget 失败: %s", e)
            self._select_target_active = False

            # ── 步骤 10: 判断恢复路径 ──
            if _aborted():
                return

            saved_step = self._plan[saved_index] if saved_index < len(self._plan) else None
            saved_tool = saved_step.tool if saved_step else ""

            if saved_tool == "move_to":
                # 路径 B：已在 move_to 中 → 恢复行走，不重发导航目标
                resume_index = saved_index
                self._skip_nav_resend = True
                log("ok", "路径B: 恢复行走 → 继续等待 nav_reached")
            else:
                # 路径 A（saved 为 pick 或其它）：发新导航目标
                resume_index = self._find_resume_index(saved_index)
                self._skip_nav_resend = False
                log("ok", f"路径A: 发送导航目标 → resume_index={resume_index}")

            # ── 恢复导航 ──
            try:
                if hasattr(self._adapter, "resume_navigation"):
                    self._adapter.resume_navigation()
            except Exception as e:
                logger.error("recovery: resume_navigation 失败: %s", e)

            # ── 在 adapter 上设置 skip_nav_resend 标记 ──
            if hasattr(self._adapter, "skip_nav_resend"):
                self._adapter.skip_nav_resend = self._skip_nav_resend

            # ── 重启自动执行 ──
            with self._lock:
                if exec_id != self._execution_id:
                    return
                self._current_index = resume_index
                self._plan_steps[resume_index]["status"] = "current"
                self._state = ST_STEP_READY
                self._stop_flag.clear()

            # 内联重启 _run_auto（同一线程，不额外 spawn）
            self._run_auto(exec_id)

        except Exception as e:
            logger.exception("_run_drop_recovery: 未预期异常")
            self._fail_recovery(exec_id)

    def _find_resume_index(self, saved_index: int) -> int:
        """从 saved_index 往前找最近 pick 步，返回其下一步（move_to to place）。"""
        for i in range(saved_index, -1, -1):
            if i < len(self._plan) and self._plan[i].tool == "pick":
                return i + 1
        # fallback：找不到 pick → 返回原 index
        return saved_index

    def _append_recovery_log(self, status: str, message: str) -> None:
        """推送掉箱恢复诊断日志到 _step_logs（SSE 可见）。"""
        with self._lock:
            self._step_logs.append(StepLog(
                step_id="drop_recovery",
                tool="drop_recovery",
                status=status,
                message=message,
                timestamp=StepLog.now(),
            ))

    def _fail_recovery(self, exec_id: int) -> None:
        """恢复失败：设置状态为 STEP_FAILED，退出自动模式。"""
        with self._lock:
            if exec_id == self._execution_id:
                self._state = ST_STEP_FAILED
        # ★ 恢复失败后同步 FSM，确保手动操作不受阻
        safety_fsm.force_state(RobotState.STANDING)

    # ═══════════════════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════════════════

    def get_state(self) -> dict[str, Any]:
        """同步快照，返回完整状态供前端渲染。"""
        with self._lock:
            nav_reached = None
            if hasattr(self._adapter, "nav_reached"):
                nav_reached = self._adapter.nav_reached
            locked_object_id = None
            if self._select_target_active and hasattr(self._adapter, "_fp_target_object_id"):
                locked_object_id = self._adapter._fp_target_object_id
            return {
                "mode": "step_debug",
                "state": self._state,
                "plan": self._plan_steps,
                "current_index": self._current_index,
                "select_target_active": self._select_target_active,
                "locked_object_id": locked_object_id,
                "nav_reached": nav_reached,  # 🆕 Nav2 导航到达状态
                "diag_events": self._collector.snapshot(),
                "step_logs": [
                    {
                        "step_id": l.step_id, "tool": l.tool,
                        "status": l.status, "error_code": l.error_code,
                        "message": l.message, "elapsed_s": l.elapsed_s,
                        "timestamp": l.timestamp,
                    }
                    for l in self._step_logs
                ],
            }

    def drain_diag_events(self) -> list[dict[str, Any]]:
        """取出增量诊断事件（SSE 推送用，调用方累积）。"""
        return self._collector.drain()

    # ═══════════════════════════════════════════════════════════
    # 内部
    # ═══════════════════════════════════════════════════════════

    def _run_step(self, step: PlanStep) -> None:
        """后台线程：执行单个 PlanStep，完成后更新状态机。

        不持有 _lock 执行 adapter.execute()（可能耗时 30s+）。
        通过 execution_id 防止旧线程污染新执行的状态。
        """
        my_exec_id = self._execution_id  # 拍快照 — 线程启动时的 epoch

        t_start = time.monotonic()
        try:
            result = self._adapter.execute(
                tool=step.tool,
                args=step.args,
                request_id="step_debug",
                goal_id="step_debug",
                step_id=step.step_id,
            )
        except Exception as e:
            logger.exception("adapter.execute 异常: %s", e)
            result = {
                "status": "error",
                "error_code": "ADAPTER_EXCEPTION",
                "message": str(e),
            }

        elapsed = time.monotonic() - t_start
        is_ok = result.get("status") == "ok"

        with self._lock:
            # 双重防护：stop_flag 或 execution_id 不匹配 → 静默退出
            if self._stop_flag.is_set() or my_exec_id != self._execution_id:
                return  # 已被 abort 或新执行已启动，不更新状态

            # ── pick 成功时附加位姿和动作信息 ──
            if step.tool == "pick" and is_ok:
                msg_parts = [result.get("message", "")]
                px = result.get("pose_x")
                py = result.get("pose_y")
                pz = result.get("pose_z")
                if px is not None:
                    msg_parts.insert(0, f"位姿=({px:.3f},{py:.3f},{pz:.3f})")
                action = result.get("selected_action")
                if action:
                    msg_parts.append(f"action={action}")
                dist = result.get("distance")
                if dist is not None:
                    msg_parts.append(f"dist={dist:.3f}m")
                rich_message = " | ".join(msg_parts)
            else:
                rich_message = result.get("message", "")

            log = StepLog(
                step_id=step.step_id,
                tool=step.tool,
                status="ok" if is_ok else "error",
                error_code=result.get("error_code", "") or "",
                message=rich_message,
                elapsed_s=round(elapsed, 2),
                timestamp=StepLog.now(),
            )
            self._step_logs.append(log)

            if is_ok:
                self._plan_steps[self._current_index]["status"] = "completed"
                self._current_index += 1
                if self._current_index >= len(self._plan):
                    self._state = ST_IDLE
                    logger.info("_run_step: 全部步骤完成")
                else:
                    self._plan_steps[self._current_index]["status"] = "current"
                    self._state = ST_STEP_DONE
                    logger.info("_run_step: %s 完成 → 前进到 %s",
                                step.step_id, self._plan[self._current_index].step_id)
            else:
                self._plan_steps[self._current_index]["status"] = "failed"
                self._plan_steps[self._current_index]["error_code"] = (
                    result.get("error_code", "")
                )
                self._plan_steps[self._current_index]["replan_target"] = (
                    result.get("replan_target")
                )
                self._state = ST_STEP_FAILED
                logger.warning("_run_step: %s 失败 (%s): %s",
                               step.step_id, log.error_code, log.message)


# ═══════════════════════════════════════════════════════════════
# 模块级工具
# ═══════════════════════════════════════════════════════════════


def _summarize_step_args(tool: str, args: dict[str, Any]) -> str:
    """提取步骤参数摘要"""
    if tool == "move_to":
        t = args.get("target", {})
        return f"({t.get('x', '?')}, {t.get('y', '?')})"
    if tool == "pick":
        return f"object={args.get('object_id', '?')}"
    if tool == "place":
        return f"({args.get('x', '?')}, {args.get('y', '?')})"
    return tool
