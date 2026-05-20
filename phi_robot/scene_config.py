"""
phi_robot 场景配置和版本管理
定义九宫格坐标、备货槽位和机器人初始状态
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Any

from .models import GridCell, StockSlot


@dataclass
class SceneConfig:
    """场景配置"""
    scene_id: str
    version: str
    description: str
    grid_cells: Dict[str, GridCell]
    stock_slots: List[StockSlot]
    robot_initial_pose: Dict[str, float]  # {x, y, z, theta}
    robot_initial_holding: str = None
    obstacles: List[Dict[str, float]] = None  # {x, y, w, h}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "version": self.version,
            "description": self.description,
            "grid_cells": {k: v.to_dict() for k, v in self.grid_cells.items()},
            "stock_slots": [s.to_dict() for s in self.stock_slots],
            "robot_initial_pose": self.robot_initial_pose,
            "robot_initial_holding": self.robot_initial_holding,
            "obstacles": self.obstacles or [],
        }


# 场景版本库
SCENE_CONFIGS: Dict[str, SceneConfig] = {
    "scene-v1": SceneConfig(
        scene_id="scene-001",
        version="scene-v1",
        description="标准 3x3m 放置区 + 1.5x1.5m 备货区",
        grid_cells={
            "nw": GridCell("nw", 0.5, 2.5, 0.0, 0.0),
            "n": GridCell("n", 1.5, 2.5, 0.0, 0.0),
            "ne": GridCell("ne", 2.5, 2.5, 0.0, 0.0),
            "w": GridCell("w", 0.5, 1.5, 0.0, 0.0),
            "c": GridCell("c", 1.5, 1.5, 0.0, 0.0),
            "e": GridCell("e", 2.5, 1.5, 0.0, 0.0),
            "sw": GridCell("sw", 0.5, 0.5, 0.0, 0.0),
            "s": GridCell("s", 1.5, 0.5, 0.0, 0.0),
            "se": GridCell("se", 2.5, 0.5, 0.0, 0.0),
        },
        stock_slots=[
            StockSlot(0, 0.5, 3.5, 0.0, order_index=0, consumed=False),
            StockSlot(1, 1.0, 3.5, 0.0, order_index=1, consumed=False),
            StockSlot(2, 1.5, 3.5, 0.0, order_index=2, consumed=False),
            StockSlot(3, 2.0, 3.5, 0.0, order_index=3, consumed=False),
            StockSlot(4, 2.5, 3.5, 0.0, order_index=4, consumed=False),
            StockSlot(5, 3.0, 3.5, 0.0, order_index=5, consumed=False),
            StockSlot(6, 3.5, 3.5, 0.0, order_index=6, consumed=False),
            StockSlot(7, 4.0, 3.5, 0.0, order_index=7, consumed=False),
            StockSlot(8, 4.5, 3.5, 0.0, order_index=8, consumed=False),
        ],
        robot_initial_pose={"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0},
        robot_initial_holding=None,
        obstacles=[],
    ),
}


@dataclass
class StockLayoutVersion:
    """备货槽位版本"""
    layout_id: str
    version: str
    description: str
    stock_slots: List[StockSlot]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layout_id": self.layout_id,
            "version": self.version,
            "description": self.description,
            "stock_slots": [s.to_dict() for s in self.stock_slots],
        }


# 备货槽位版本库
STOCK_LAYOUT_CONFIGS: Dict[str, StockLayoutVersion] = {
    "stock-v1": StockLayoutVersion(
        layout_id="stock-001",
        version="stock-v1",
        description="标准 1.5x1.5m 备货区，9 个槽位排成一排",
        stock_slots=[
            StockSlot(0, 0.5, 3.5, 0.0, order_index=0, consumed=False),
            StockSlot(1, 1.0, 3.5, 0.0, order_index=1, consumed=False),
            StockSlot(2, 1.5, 3.5, 0.0, order_index=2, consumed=False),
            StockSlot(3, 2.0, 3.5, 0.0, order_index=3, consumed=False),
            StockSlot(4, 2.5, 3.5, 0.0, order_index=4, consumed=False),
            StockSlot(5, 3.0, 3.5, 0.0, order_index=5, consumed=False),
            StockSlot(6, 3.5, 3.5, 0.0, order_index=6, consumed=False),
            StockSlot(7, 4.0, 3.5, 0.0, order_index=7, consumed=False),
            StockSlot(8, 4.5, 3.5, 0.0, order_index=8, consumed=False),
        ],
    ),
}


def get_scene_config(version: str) -> SceneConfig:
    """获取场景配置"""
    if version not in SCENE_CONFIGS:
        raise ValueError(f"未知的场景版本: {version}")
    return SCENE_CONFIGS[version]


def get_stock_layout(version: str) -> StockLayoutVersion:
    """获取备货槽位版本"""
    if version not in STOCK_LAYOUT_CONFIGS:
        raise ValueError(f"未知的备货槽位版本: {version}")
    return STOCK_LAYOUT_CONFIGS[version]
