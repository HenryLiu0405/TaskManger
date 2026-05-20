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
    ):
        """
        初始化执行引擎

        Args:
            service: MissionService 实例
            adapter: RobotAdapter 实例（仿真或真机）
            hook: 执行钩子（可选）
            cancel_event: 取消事件，set 时中断执行
        """
        self._service = service
        self._adapter = adapter
        self._hook = hook
        self._cancel_event = cancel_event
        self._replan_counts: dict[int, int] = {}
        self._max_replans_per_task = 2

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

                # 执行工具
                try:
                    args = dict(step.args)  # shallow copy — don't mutate frozen PlanStep
                    # 条件性注入 `current`：仅当适配器不是将 move_to 转发到外部服务时注入。
                    # 只有当当前 move_to 会走真机时才不注入 current；否则本地模拟仍需 current。
                    should_inject_current = True
                    try:
                        route_real_move_to = getattr(self._adapter, "should_route_real_move_to", None)
                        if callable(route_real_move_to) and route_real_move_to(step.tool, args):
                            should_inject_current = False
                    except Exception:
                        should_inject_current = True

                    if step.tool == "move_to" and should_inject_current:
                        # 执行前获取机器人当前位姿，注入 current 字段
                        pose_result = await self._execute_tool(
                            tool="get_pose",
                            args={},
                            request_id=record.request_id,
                            goal_id=record.goal_id,
                            step_id=step.step_id + "-pre",
                        )
                        if pose_result.get("status") == "ok":
                            args["current"] = pose_result.get("state", {}).get("pose", {})
                        else:
                            args["current"] = {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0}

                    result_dict = await self._execute_tool(
                        tool=step.tool,
                        args=args,
                        request_id=record.request_id,
                        goal_id=record.goal_id,
                        step_id=step.step_id,
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
                    )

                    context.tool_results.append(tool_result)

                    if tool_result.status == "ok":
                        self._service.mark_step_succeeded(mission_id, result_dict)
                    else:
                        error_code = tool_result.error_code or "UNKNOWN_ERROR"
                        task_idx = step.task_index

                        # 通过钩子获取重规划策略
                        action, new_steps = (
                            self._hook.apply_replan_policy(record, result_dict)
                            if self._hook
                            else ("continue", None)
                        )

                        if action == "abort":
                            record = replace(
                                record,
                                status="aborted",
                                last_error=f"{error_code}: {tool_result.message}",
                            )
                            self._service.store.update(mission_id, record)
                            break
                        elif action == "retry":
                            # 同步骤重试，不推进 step_index，不消耗 replan 预算
                            continue
                        elif action == "replan":
                            current_replans = self._replan_counts.get(task_idx, 0)
                            if current_replans < self._max_replans_per_task:
                                self._replan_counts[task_idx] = current_replans + 1
                                # 重规划：不推进步骤，下次迭代重新执行当前步骤
                                continue
                            else:
                                # 预算耗尽，标记失败并继续
                                self._service.mark_step_failed(
                                    mission_id, error_code, tool_result.message
                                )
                        else:
                            # "continue"：标记失败，继续下一步
                            self._service.mark_step_failed(
                                mission_id, error_code, tool_result.message
                            )

                except Exception as e:
                    self._service.mark_step_failed(
                        mission_id, "INTERNAL_ERROR", str(e)
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
    ) -> Dict[str, Any]:
        """执行单个工具，执行前检查取消信号"""
        if self._cancel_event and self._cancel_event.is_set():
            return {
                "status": "error",
                "error_code": "CANCELLED",
                "message": "Execution cancelled before tool call",
            }
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._adapter.execute(
                tool,
                args,
                request_id=request_id,
                goal_id=goal_id,
                step_id=step_id,
            ),
        )
