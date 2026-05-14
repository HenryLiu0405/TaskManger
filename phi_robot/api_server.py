"""
phi_robot HTTP API 服务
支持前端和后端通信的 REST API 和 WebSocket 事件流
"""

from __future__ import annotations
import json
import uuid
import asyncio
import threading
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
        """重规划策略 — 根据错误代码分类处理"""
        error_code = result.get("error_code", "")

        if error_code == "safety_alert":
            self.events.append({
                "type": "safety_alert",
                "timestamp": datetime.now().isoformat(),
                "message": "Safety alert triggered, aborting mission"
            })
            return ("abort", None)
        elif error_code in ("GRIP_FAIL", "TIMEOUT"):
            return ("retry", None)
        elif error_code in ("NOT_REACHABLE", "OBSTRUCTED"):
            return ("replan", None)
        elif error_code == "INVALID_ARGS":
            return ("abort", None)       # 配置错误，不可恢复
        elif error_code == "PRECONDITION_FAILED":
            return ("retry", None)       # 仿真状态不一致，重试

        return ("continue", None)


class PhiRobotAPIServer:
    """phi_robot API 服务器"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        adapter=None,
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

        self._setup_routes()
    
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

                # 重置仿真环境到初始状态
                self.adapter.reset()

                # 启动任务
                self.service.run(mission_id)

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
    server = PhiRobotAPIServer(host="127.0.0.1", port=5000)
    server.run()
