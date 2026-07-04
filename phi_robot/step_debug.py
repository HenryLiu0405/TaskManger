"""
分步调试控制器。

在手动模式和自动模式之间提供第三种操作模式：
加载 mission plan → 每步等待人工确认 → 执行 → 诊断事件采集 → 停在下一步前。

纯 Python 层，不依赖 ROS。通过 adapter 协议与机器人交互。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .models import PlanStep
from .mission_planner import MissionPlanner
from .recovery_diag import DiagEvent, DiagCollector

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
        """跳过当前步骤（仅 STEP_READY / STEP_DONE 状态允许）。"""
        with self._lock:
            if self._state == ST_STEP_FAILED:
                return {
                    "ok": False,
                    "message": "步骤已失败，请先重试或终止。如需强制跳过，调 force_skip。",
                }
            if self._state not in (ST_STEP_READY, ST_STEP_DONE):
                return {"ok": False,
                        "message": f"当前状态 {self._state} 不可跳过"}

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

        if result and result.get("success"):
            self._select_target_active = True
            return {
                "ok": True,
                "active": True,
                "object_id": result.get("matched_object_id"),
                "message": result.get("message", "已锁定"),
            }
        else:
            msg = result.get("message", "锁定失败") if result else "SelectTarget 服务不可用"
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

    def stand_robot(self) -> dict[str, Any]:
        """切换机器人为站立模式"""
        with self._lock:
            if hasattr(self._adapter, "stand_robot"):
                return self._adapter.stand_robot()
            return {"ok": False, "message": "adapter 不支持站立"}

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
    # 查询
    # ═══════════════════════════════════════════════════════════

    def get_state(self) -> dict[str, Any]:
        """同步快照，返回完整状态供前端渲染。"""
        with self._lock:
            nav_reached = None
            if hasattr(self._adapter, "nav_reached"):
                nav_reached = self._adapter.nav_reached
            return {
                "mode": "step_debug",
                "state": self._state,
                "plan": self._plan_steps,
                "current_index": self._current_index,
                "select_target_active": self._select_target_active,
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

            log = StepLog(
                step_id=step.step_id,
                tool=step.tool,
                status="ok" if is_ok else "error",
                error_code=result.get("error_code", "") or "",
                message=result.get("message", ""),
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
