"""
phi_robot WebAPI 集成层
将 MissionApiService 挂载到 nanobot FastAPI 服务
"""

from __future__ import annotations
from typing import Optional, Dict, Any, List
import logging

from .mission_service import MissionService
from .api_service import MissionApiService
from .mission_event_hub import MissionEventHub
from .mission_runner import MissionExecutionHook
from .adapters.adapter_base import RobotAdapter
from .adapters.unitree_sim import UnitreeSimBackend

logger = logging.getLogger("phi_robot.webapi")


class PhiRobotWebAPI:
    """phi_robot WebAPI 集成器"""

    def __init__(
        self,
        adapter: Optional[RobotAdapter] = None,
        hook: Optional[MissionExecutionHook] = None,
    ):
        """
        初始化 WebAPI
        
        Args:
            adapter: RobotAdapter 实例（默认使用仿真后端）
            hook: 执行钩子（可选）
        """
        self._adapter = adapter or UnitreeSimBackend()
        self._service = MissionService()
        self._event_hub = MissionEventHub(self._service.store)
        self._api_service = MissionApiService(
            service=self._service,
            adapter=self._adapter,
            event_hub=self._event_hub,
            hook=hook,
        )

    def register_routes(self, app) -> None:
        """
        注册路由到 FastAPI 应用
        
        Args:
            app: FastAPI 应用实例
        """
        api = self._api_service

        # POST /api/mission/submit
        @app.post("/api/mission/submit")
        async def submit_mission(payload: Dict[str, Any]):
            result = api.submit_mission(
                request_id=payload.get("request_id"),
                scene_id=payload.get("scene_id"),
                goal_id=payload.get("goal_id"),
                scene_version=payload.get("scene_version", "scene-v1"),
                stock_layout_version=payload.get("stock_layout_version", "stock-v1"),
                destination_order=payload.get("destination_order", []),
                board_mapping=payload.get("board_mapping"),
                options=payload.get("options"),
            )
            return result

        # GET /api/mission/{mission_id}
        @app.get("/api/mission/{mission_id}")
        async def get_mission_status(mission_id: str):
            return api.get_mission_status(mission_id)

        # POST /api/mission/{mission_id}/run
        @app.post("/api/mission/{mission_id}/run")
        async def run_mission(mission_id: str):
            return await api.run_mission(mission_id)

        # POST /api/mission/{mission_id}/pause
        @app.post("/api/mission/{mission_id}/pause")
        async def pause_mission(mission_id: str):
            return api.pause_mission(mission_id)

        # POST /api/mission/{mission_id}/resume
        @app.post("/api/mission/{mission_id}/resume")
        async def resume_mission(mission_id: str):
            return await api.resume_mission(mission_id)

        # POST /api/mission/{mission_id}/reset
        @app.post("/api/mission/{mission_id}/reset")
        async def reset_mission(mission_id: str):
            return api.reset_mission(mission_id)

        # GET /api/mission/{mission_id}/events (SSE 版本)
        @app.get("/api/mission/{mission_id}/events")
        async def get_events(mission_id: str, since_event_id: int = -1):
            """
            Server-Sent Events 事件流
            
            客户端订阅此端点获取实时事件
            """
            from starlette.responses import StreamingResponse

            async def event_stream():
                try:
                    async for event in api.get_events_stream(mission_id, since_event_id):
                        yield f"data: {event.to_dict()}\n\n"
                except Exception as e:
                    logger.exception("event stream error")
                    yield f"data: {{'error': '{str(e)}'}}\n\n"

            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )

        logger.info("phi_robot WebAPI routes registered")

    def get_api_service(self) -> MissionApiService:
        """获取 API 服务实例"""
        return self._api_service

    def get_event_hub(self) -> MissionEventHub:
        """获取事件中心"""
        return self._event_hub


def create_phi_robot_api(
    app=None,
    adapter: Optional[RobotAdapter] = None,
    hook: Optional[MissionExecutionHook] = None,
) -> PhiRobotWebAPI:
    """
    工厂函数 - 创建并注册 phi_robot WebAPI
    
    Args:
        app: FastAPI 应用实例
        adapter: RobotAdapter 实例
        hook: 执行钩子
        
    Returns:
        PhiRobotWebAPI 实例
    """
    webapi = PhiRobotWebAPI(adapter=adapter, hook=hook)
    if app:
        webapi.register_routes(app)
    return webapi
