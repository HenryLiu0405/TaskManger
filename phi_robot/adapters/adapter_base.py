"""
phi_robot 适配器基类和协议定义
支持与仿真、真机等不同后端的统一接口
"""

from __future__ import annotations
from typing import Any, Protocol, Dict
from abc import abstractmethod


class RobotAdapter(Protocol):
    """机器人适配器统一协议
    
    所有适配器（仿真、真机）都应实现这个协议。
    """

    @abstractmethod
    def execute(
        self,
        tool: str,
        args: Dict[str, Any],
        *,
        request_id: str,
        goal_id: str,
        step_id: str,
    ) -> Dict[str, Any]:
        """
        执行工具命令
        
        Args:
            tool: 工具名称 (move_to, pick, place, get_pose, get_gripper_state)
            args: 工具参数字典
            request_id: 请求 ID
            goal_id: 目标 ID
            step_id: 步骤 ID
            
        Returns:
            move_to 响应:
            {
                "status": "ok" | "error",
                "error_code": None | str,
                "message": str,
                "pose": {"x": float, "y": float, "z": float, "theta": float} | None,
                "metrics": {"latency_ms": int},
                "request_id": str,
                "goal_id": str,
                "step_id": str,
            }
            pick / place / get_pose 响应:
            {
                "status": "ok" | "error",
                "error_code": None | str,
                "message": str,
                "state": {"pose": {...}, "holding": ..., "boxes": {...}},
                "metrics": {"latency_ms": int, "sim_time_ms": int},
                "request_id": str,
                "goal_id": str,
                "step_id": str,
            }
        """
        ...

    @abstractmethod
    def snapshot(self) -> Dict[str, Any]:
        """
        获取当前世界状态快照

        Returns:
            {
                "robot_pose": {"x": float, "y": float, "z": float, "theta": float},
                "holding": None | str,
                "boxes": {box_id: {"pose": {...}, "held": bool}},
                "obstacles": [{x, y, w, h}, ...],
            }
        """
        ...

    def reset(self) -> None:
        """重置仿真环境到初始状态（可选实现）"""
        pass


# 向后兼容别名
AdapterBase = RobotAdapter

