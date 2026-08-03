"""
phi_robot 任务执行引擎
后台执行循环，支持钩子、重规划和事件推送
"""

from __future__ import annotations
import threading
from typing import Optional, Callable, Any, Dict, List
from datetime import datetime
from dataclasses import replace
import asyncio

from .models import MissionRecord, MissionEvent, ToolResult
from .mission_service import MissionService
from .adapters.adapter_base import RobotAdapter
from .skills import build_skill_dispatcher


class ExecutionContext:
    """执行上下文 - 传递给钩子的上下文对象"""

    def __init__(self, mission_record: MissionRecord):
        self.mission_record = mission_record
        # 安全地获取当前步骤（检查范围）
        if 0 <= mission_record.current_step_index < len(mission_record.plan):
            self.current_step = mission_record.plan[mission_record.current_step_index]
        else:
            self.current_step = None
        self.tool_results: List[ToolResult] = []
        self.stop_reason: Optional[str] = None
        self.error: Optional[str] = None


class MissionExecutionHook:
    """执行钩子 - 可选，用于监控和干预执行过程"""

    async def before_iteration(self, context: ExecutionContext) -> None:
        """每次迭代前调用"""
        pass

    async def before_execute_tools(self, context: ExecutionContext) -> None:
        """执行工具前调用"""
        pass

    async def after_iteration(self, context: ExecutionContext) -> None:
        """每次迭代后调用"""
        pass

    def apply_replan_policy(
        self,
        record: MissionRecord,
        result: Dict[str, Any],
        events: Optional[List[MissionEvent]] = None,
    ) -> tuple[str, Optional[List]]:
        """
        重规划策略
        
        Returns:
            (action, new_steps)
            action: "continue", "replan", "abort"
            new_steps: 如果重规划，返回新步骤列表，否则为 None
        """
        return ("continue", None)


class MissionRunner:
    """任务执行引擎 - 后台执行循环"""

    def __init__(
        self,
        service: MissionService,
        adapter: RobotAdapter,
        hook: Optional[MissionExecutionHook] = None,
        cancel_event: Optional[threading.Event] = None,
        step_callback: Optional[Callable] = None,
        skill_dispatcher: Any = None,
    ):
        """
        初始化执行引擎

        Args:
            service: MissionService 实例
            adapter: RobotAdapter 实例（仿真或真机）
            hook: 执行钩子（可选）
            cancel_event: 取消事件，set 时中断执行
            step_callback: 每步完成回调，签名为 (PlanStep, ToolResult) -> None
        """
        self._service = service
        self._adapter = adapter
        self._skill_dispatcher = skill_dispatcher or build_skill_dispatcher(adapter)
        self._hook = hook
        self._cancel_event = cancel_event
        self._step_callback = step_callback
        self._attempts_by_step: Dict[str, int] = {}

    async def run(self, mission_id: str) -> MissionRecord:
        """
        执行任务
        
        Args:
            mission_id: 任务 ID
            
        Returns:
            最终的 MissionRecord
        """
        record = self._service.get_mission(mission_id)
        if not record:
            raise KeyError(f"mission_id {mission_id} 不存在")

        # 确保任务已启动
        if record.status != "running":
            self._service.run(mission_id)
            record = self._service.get_mission(mission_id)

        try:
            while record.status == "running":
                # === 检查取消/暂停/中止标志 ===
                if self._cancel_event and self._cancel_event.is_set():
                    record = self._service.get_mission(mission_id)
                    if record.pause_requested:
                        record = replace(record, status="paused")
                        self._service.store.update(mission_id, record)
                        break
                    self._service.mark_aborted(mission_id, "cancelled via cancel_event")
                    break

                # 每次迭代重新读取 record（可能被外部修改）
                record = self._service.get_mission(mission_id)
                if record.abort_requested:
                    self._service.mark_aborted(mission_id, "abort requested")
                    break
                if record.pause_requested:
                    record = replace(record, status="paused")
                    self._service.store.update(mission_id, record)
                    break

                context = ExecutionContext(record)

                # 钩子: 迭代前
                if self._hook:
                    await self._hook.before_iteration(context)

                if context.stop_reason:
                    record = replace(
                        record,
                        status="aborted",
                        last_error=context.error or context.stop_reason,
                    )
                    self._service.store.update(mission_id, record)
                    break

                # 检查是否完成
                if record.current_step_index >= len(record.plan):
                    record = replace(record, status="completed")
                    self._service.store.update(mission_id, record)
                    break

                # 获取当前步骤
                step = record.plan[record.current_step_index]

                # 钩子: 工具执行前
                if self._hook:
                    await self._hook.before_execute_tools(context)

                # 标记步骤为执行中
                self._service.mark_step_running(mission_id)

                # 执行工具
                attempt = self._attempts_by_step.get(step.step_id, 0) + 1
                self._attempts_by_step[step.step_id] = attempt
                try:
                    args = dict(step.args)  # shallow copy — don't mutate frozen PlanStep

                    result_dict = await self._execute_tool(
                        tool=step.tool,
                        args=args,
                        request_id=record.request_id,
                        goal_id=record.goal_id,
                        step_id=step.step_id,
                        mission_id=mission_id,
                        execution_epoch=record.execution_epoch,
                        annotations=step.annotations,
                        skill_version=step.skill_version,
                        attempt=attempt,
                    )

                    tool_result = ToolResult(
                        status=result_dict.get("status", "error"),
                        error_code=result_dict.get("error_code"),
                        message=result_dict.get("message", ""),
                        state=result_dict.get("state", {}),
                        metrics=result_dict.get("metrics", {}),
                        request_id=result_dict.get("request_id"),
                        goal_id=result_dict.get("goal_id"),
                        step_id=step.step_id,
                        invocation_id=result_dict.get("invocation_id"),
                        outcome=result_dict.get("outcome", ""),
                        error_category=result_dict.get("error_category"),
                        verification=result_dict.get("verification", {}),
                    )

                    context.tool_results.append(tool_result)
                    if self._step_callback:
                        self._step_callback(step, tool_result)

                    if tool_result.status == "ok":
                        self._service.mark_step_succeeded(
                            mission_id, result_dict, attempt=attempt
                        )
                    else:
                        error_code = tool_result.error_code or "UNKNOWN_ERROR"

                        # 通过钩子获取错误处理策略
                        action, _ = (
                            self._hook.apply_replan_policy(record, result_dict)
                            if self._hook
                            else ("continue", None)
                        )

                        unknown_outcome = result_dict.get("outcome") == "unknown"

                        if action == "abort" or unknown_outcome:
                            self._service.mark_step_failed(
                                mission_id,
                                error_code,
                                tool_result.message,
                                result_dict=result_dict,
                                attempt=attempt,
                            )
                            record = self._service.get_mission(mission_id)
                            record = replace(
                                record,
                                status="failed" if unknown_outcome else "aborted",
                            )
                            self._service.store.update(mission_id, record)
                            break
                        else:
                            # Phase 1 is fail-stop and does not honor legacy
                            # retry requests. Re-dispatching a physical step
                            # requires a later, explicit recovery design.
                            self._service.mark_step_failed(
                                mission_id, error_code, tool_result.message,
                                result_dict=result_dict,
                                attempt=attempt,
                            )
                            record = self._service.get_mission(mission_id)
                            record = replace(record, status="failed")
                            self._service.store.update(mission_id, record)
                            break

                except Exception as e:
                    if self._step_callback:
                        self._step_callback(step, ToolResult(
                            status="error", error_code="INTERNAL_ERROR",
                            message=str(e), step_id=step.step_id,
                        ))
                    self._service.mark_step_failed(
                        mission_id, "INTERNAL_ERROR", str(e),
                        result_dict={
                            "status": "error",
                            "error_code": "INTERNAL_ERROR",
                            "message": str(e),
                            "outcome": "failed",
                            "error_category": "execution",
                            "step_id": step.step_id,
                        },
                        attempt=attempt,
                    )
                    record = replace(record, status="failed", last_error=str(e))
                    self._service.store.update(mission_id, record)
                    break

                # 推进到下一步
                self._service.advance_step(mission_id)
                record = self._service.get_mission(mission_id)

                # 钩子: 迭代后
                if self._hook:
                    await self._hook.after_iteration(context)

        except Exception as e:
            record = replace(record, status="failed", last_error=str(e))
            self._service.store.update(mission_id, record)

        return self._service.get_mission(mission_id)

    async def _execute_tool(
        self,
        tool: str,
        args: Dict[str, Any],
        request_id: str,
        goal_id: str,
        step_id: str,
        mission_id: str = "",
        execution_epoch: int = 1,
        annotations: Optional[Dict[str, Any]] = None,
        skill_version: str = "1.0",
        attempt: int = 1,
    ) -> Dict[str, Any]:
        """执行单个工具，执行前检查取消信号"""
        if self._cancel_event and self._cancel_event.is_set():
            return {
                "status": "error",
                "error_code": "CANCELLED",
                "message": "Execution cancelled before tool call",
                "outcome": "cancelled",
                "error_category": "cancelled",
            }
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._skill_dispatcher.execute_legacy(
                tool,
                args,
                version=skill_version,
                request_id=request_id,
                goal_id=goal_id,
                step_id=step_id,
                mission_id=mission_id,
                source="mission_runner",
                annotations=annotations or {},
                attempt=attempt,
                idempotency_key=(
                    f"mission:{mission_id or request_id}:"
                    f"epoch-{execution_epoch}:{step_id}"
                ),
            ),
        )
