"""
phi_robot HTTP API 服务
REST 接口实现，对标 think.md § 5.5.3 的接口契约
"""

from __future__ import annotations
from typing import Optional, Dict, Any, List
from datetime import datetime
import uuid

from .mission_service import MissionService
from .mission_runner import MissionRunner, MissionExecutionHook
from .mission_event_hub import MissionEventHub
from .models import MissionRecord, MissionEvent
from .adapters.adapter_base import RobotAdapter


class MissionApiService:
    """
    任务级 API 服务
    负责参数校验、幂等性保证、返回包装
    """

    def __init__(
        self,
        service: MissionService,
        adapter: RobotAdapter,
        event_hub: Optional[MissionEventHub] = None,
        hook: Optional[MissionExecutionHook] = None,
    ):
        """
        初始化 API 服务
        
        Args:
            service: MissionService 实例
            adapter: RobotAdapter 实例
            event_hub: MissionEventHub 实例（可选）
            hook: 执行钩子（可选）
        """
        self._service = service
        self._adapter = adapter
        self._event_hub = event_hub or MissionEventHub(service.store)
        self._hook = hook
        self._runners: Dict[str, MissionRunner] = {}

    # ========== POST /api/mission/submit ==========
    def submit_mission(
        self,
        request_id: str,
        scene_id: str,
        goal_id: str,
        scene_version: str,
        stock_layout_version: str,
        destination_order: List[str],
        board_mapping: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        提交任务
        
        Returns:
            {
                "request_id": str,
                "mission_id": str,
                "status": "ready",
                "message": "任务已创建",
            }
        """
        try:
            mission_id = self._service.submit(
                request_id=request_id,
                scene_id=scene_id,
                goal_id=goal_id,
                scene_version=scene_version,
                stock_layout_version=stock_layout_version,
                destination_order=destination_order,
                board_mapping=board_mapping,
                options=options,
            )
            return {
                "request_id": request_id,
                "mission_id": mission_id,
                "status": "ready",
                "message": "任务已创建",
            }
        except ValueError as e:
            return {
                "request_id": request_id,
                "error_code": "INVALID_INPUT",
                "message": str(e),
            }

    # ========== GET /api/mission/{id} ==========
    def get_mission_status(self, mission_id: str) -> Dict[str, Any]:
        """
        查询任务状态
        
        Returns:
            {
                "request_id": str,
                "mission_id": str,
                "status": str,
                "current_step": int,
                "completed": int,
                "total": int,
                "state": {...},
                "metrics": {...},
                "error_code": None | str,
                "message": str,
                "updated_at": str,
            }
        """
        record = self._service.get_mission(mission_id)
        if not record:
            return {
                "error_code": "NOT_FOUND",
                "message": f"mission_id {mission_id} 不存在",
            }

        completed = sum(
            1 for step in record.plan if step.status == "succeeded"
        )
        total = len(record.plan)

        return {
            "request_id": record.request_id,
            "mission_id": record.mission_id,
            "status": record.status,
            "current_step": record.current_step_index,
            "completed": completed,
            "total": total,
            "state": record.state,
            "metrics": record.metrics,
            "error_code": None if not record.last_error else "EXECUTION_ERROR",
            "message": record.last_error or "running",
            "updated_at": record.updated_at.isoformat(),
        }

    # ========== POST /api/mission/{id}/run ==========
    async def run_mission(self, mission_id: str) -> Dict[str, Any]:
        """
        启动任务执行
        
        Returns:
            {
                "mission_id": str,
                "status": "running",
                "message": "任务已启动",
                "current_step": int,
            }
        """
        record = self._service.get_mission(mission_id)
        if not record:
            return {
                "error_code": "NOT_FOUND",
                "message": f"mission_id {mission_id} 不存在",
            }

        if record.status == "running":
            return {
                "mission_id": mission_id,
                "status": "running",
                "message": "任务已在运行中",
                "current_step": record.current_step_index,
            }

        try:
            self._service.run(mission_id)
            
            # 创建后台执行器（不阻塞）
            runner = MissionRunner(
                service=self._service,
                adapter=self._adapter,
                hook=self._hook,
            )
            self._runners[mission_id] = runner
            
            # 异步启动执行（不等待完成）
            asyncio.create_task(runner.run(mission_id))
            
            record = self._service.get_mission(mission_id)
            return {
                "mission_id": mission_id,
                "status": "running",
                "message": "任务已启动",
                "current_step": record.current_step_index,
            }
        except Exception as e:
            return {
                "error_code": "EXECUTION_ERROR",
                "message": str(e),
            }

    # ========== POST /api/mission/{id}/pause ==========
    def pause_mission(self, mission_id: str) -> Dict[str, Any]:
        """
        暂停任务
        
        Returns:
            {
                "mission_id": str,
                "status": "paused",
                "message": "任务已暂停",
            }
        """
        try:
            self._service.pause(mission_id)
            return {
                "mission_id": mission_id,
                "status": "paused",
                "message": "任务已暂停",
            }
        except Exception as e:
            return {
                "error_code": "EXECUTION_ERROR",
                "message": str(e),
            }

    # ========== POST /api/mission/{id}/resume ==========
    async def resume_mission(self, mission_id: str) -> Dict[str, Any]:
        """
        恢复任务
        
        Returns:
            {
                "mission_id": str,
                "status": "running",
                "message": "任务已恢复",
            }
        """
        try:
            self._service.resume(mission_id)
            
            # 重新启动执行器
            runner = MissionRunner(
                service=self._service,
                adapter=self._adapter,
                hook=self._hook,
            )
            self._runners[mission_id] = runner
            asyncio.create_task(runner.run(mission_id))
            
            return {
                "mission_id": mission_id,
                "status": "running",
                "message": "任务已恢复",
            }
        except Exception as e:
            return {
                "error_code": "EXECUTION_ERROR",
                "message": str(e),
            }

    # ========== POST /api/mission/{id}/reset ==========
    def reset_mission(self, mission_id: str) -> Dict[str, Any]:
        """
        重置任务
        
        Returns:
            {
                "mission_id": str,
                "status": "ready",
                "message": "任务已重置",
            }
        """
        try:
            self._service.reset(mission_id)
            return {
                "mission_id": mission_id,
                "status": "ready",
                "message": "任务已重置",
            }
        except Exception as e:
            return {
                "error_code": "EXECUTION_ERROR",
                "message": str(e),
            }

    # ========== GET /api/mission/{id}/events ==========
    async def get_events_stream(
        self, mission_id: str, since_event_id: int = -1
    ):
        """
        获取事件流（WebSocket 或 SSE 适配）
        
        Yields:
            MissionEvent 对象
        """
        record = self._service.get_mission(mission_id)
        if not record:
            raise KeyError(f"mission_id {mission_id} 不存在")

        # 订阅事件流
        async for event in self._event_hub.subscribe(mission_id):
            if event.event_id > since_event_id:
                yield event

    @property
    def event_hub(self) -> MissionEventHub:
        """获取事件中心"""
        return self._event_hub


# 为了支持异步，添加导入
import asyncio
