"""
phi_robot 任务规划生成器
将九宫格路径展开为可执行的 PlanStep 列表

坐标来源：TaskManger/phi_robot/scene_coords.json（rviz2 量取的 Nav2 map 世界坐标）。
坐标已是 Nav2 map 系 → 不再做 real_to_nav2 仿射；plan 直接使用配置坐标。
改坐标只需编辑 scene_coords.json 后重启 TaskManger。
"""

from __future__ import annotations
import json
import math
import os
from typing import List, Dict, Any, Tuple
from .models import PlanStep, GridCell, StockSlot


# ── 坐标配置加载（scene_coords.json，Nav2 map 系）────────────────────────
_SCENE_COORDS_PATH = os.path.join(os.path.dirname(__file__), "scene_coords.json")
_scene_cache: Dict[str, Any] | None = None


def load_scene_coords(reload: bool = False) -> Dict[str, Any]:
    """读取并缓存 scene_coords.json（Nav2 map 世界坐标）。

    Args:
        reload: True 时强制重新读盘（改坐标后无需重启进程即可生效）。
    """
    global _scene_cache
    if _scene_cache is None or reload:
        with open(_SCENE_COORDS_PATH, "r", encoding="utf-8") as f:
            _scene_cache = json.load(f)
    return _scene_cache


def get_grid_cells(version: str = "scene-v1") -> Dict[str, GridCell]:
    """从配置返回九宫格目标点表（Nav2 map 坐标）。"""
    coords = load_scene_coords()
    return {
        pos: GridCell(pos, float(c["x"]), float(c["y"]), 0.0, float(c.get("theta", 0.0)))
        for pos, c in coords["grid_cells"].items()
    }


def get_stock_point(version: str = "stock-v1") -> StockSlot:
    """从配置返回唯一物料点（Nav2 map 坐标）。

    物料点已从九宫格改为单个固定点：人工每次把圆柱放到该点供机器人取料。
    theta 存进 StockSlot.z 之外无字段，故通过 load_scene_coords 单独取朝向。
    """
    coords = load_scene_coords()
    sp = coords["stock_point"]
    return StockSlot(0, float(sp["x"]), float(sp["y"]), 0.0, order_index=0, consumed=False)


def get_stock_theta(version: str = "stock-v1") -> float:
    """物料点朝向（弧度）。"""
    return float(load_scene_coords()["stock_point"].get("theta", math.pi / 2))


def _map_bounds() -> tuple[float, float, float, float]:
    """从配置返回 Nav2 地图边界 (x_min, x_max, y_min, y_max)。"""
    b = load_scene_coords()["map_bounds"]
    return (float(b["x_min"]), float(b["x_max"]), float(b["y_min"]), float(b["y_max"]))


def compute_approach(
    obj_nav2: tuple[float, float, float],
    offset: float = 0.1,
) -> tuple[float, float, float]:
    """
    从物体在 Nav2 map 系下的位姿，计算机器人接近位姿。

    机器人停在物体前方 offset 米处，朝向由 obj_nav2 的 yaw 决定
    （沿该朝向反向后退 offset），为搬起/放下动作留出操作距离。

    Args:
        obj_nav2: 物体在 Nav2 map 系下的 (x, y, yaw)，yaw 为弧度（机器人最终朝向）
        offset: 机器人停在物体前方的距离（米）

    Returns:
        (app_x, app_y, app_yaw) — Nav2 map 系下的接近位姿

    Raises:
        ValueError: 如果接近点超出地图边界
    """
    ox, oy, app_yaw = obj_nav2

    # 沿最终朝向反向后退 offset：机器人停在点前方、面向该点
    app_x = ox - offset * math.cos(app_yaw)
    app_y = oy - offset * math.sin(app_yaw)

    x_min, x_max, y_min, y_max = _map_bounds()
    if not (x_min <= app_x <= x_max):
        raise ValueError(
            f"approach x={app_x:.2f} out of map bounds [{x_min}, {x_max}]"
        )
    if not (y_min <= app_y <= y_max):
        raise ValueError(
            f"approach y={app_y:.2f} out of map bounds [{y_min}, {y_max}]"
        )

    return (app_x, app_y, app_yaw)


class MissionPlanner:
    """任务规划生成器"""

    def __init__(self):
        """初始化规划器"""
        self._step_counter = 0

    def plan(
        self,
        goal_id: str,
        destination_order: List[str],
        request_id: str = "",
        scene_version: str = "scene-v1",
        stock_layout_version: str = "stock-v1",
        max_tasks: int = 9,
    ) -> List[PlanStep]:
        """
        生成任务执行计划

        Args:
            goal_id: 目标 ID
            destination_order: 目标方位列表，例如 ["nw", "n", "ne"]
            request_id: 请求 ID（用于元数据）
            scene_version: 场景版本，用于查表获取坐标
            stock_layout_version: 库存布局版本，用于查表获取槽位坐标
            max_tasks: 最多执行的任务数（默认 9 个）

        Returns:
            list[PlanStep] — 按顺序执行的步骤列表

        Raises:
            ValueError: 如果目标方位无效或超过限制
        """
        if not destination_order:
            raise ValueError("destination_order 不能为空")

        if len(destination_order) > max_tasks:
            raise ValueError(f"任务数不能超过 {max_tasks}（当前 {len(destination_order)}）")

        grid_cells = get_grid_cells(scene_version)
        stock_point = get_stock_point(stock_layout_version)   # 单一固定物料点
        stock_theta = get_stock_theta(stock_layout_version)   # 物料点朝向（弧度）

        for position in destination_order:
            if position not in grid_cells:
                raise ValueError(f"无效的目标方位: {position}")

        plan: List[PlanStep] = []
        self._step_counter = 0

        for task_index, destination_position in enumerate(destination_order):
            # 物料点已改为单一固定点：所有 task 共用同一取料点（人工补料）

            # 第 1 步: 移动到物料点 approach 点
            # 坐标已是 Nav2 map 系（scene_coords.json），不再做 real_to_nav2
            nav2_x, nav2_y = stock_point.x, stock_point.y
            try:
                app_x, app_y, app_yaw = compute_approach(
                    (nav2_x, nav2_y, stock_theta), offset=0.6
                )
            except ValueError as e:
                raise ValueError(
                    f"物料点 approach 点越界: {e}"
                ) from e
            plan.append(
                PlanStep(
                    step_id=self._next_step_id(),
                    task_index=task_index,
                    tool="move_to",
                    args={
                        "target": {"x": app_x, "y": app_y, "z": stock_point.z, "theta": app_yaw},
                        "action": "start",
                        "timeout_s": 30,
                        "request_id": request_id,
                        "goal_id": goal_id,
                        "slot_nav2_x": nav2_x,    # 物料点在 Nav2 map 系的坐标
                        "slot_nav2_y": nav2_y,    # 供 FP 验证钩子使用
                    },
                    status="pending",
                )
            )

            # 第 2 步: 抓取方块
            plan.append(
                PlanStep(
                    step_id=self._next_step_id(),
                    task_index=task_index,
                    tool="pick",
                    args={
                        "object_id": f"box-{task_index:02d}",
                        "timeout_s": 30,
                        "request_id": request_id,
                        "goal_id": goal_id,
                    },
                    status="pending",
                )
            )

            # 第 3 步: 移动到目标放置位（直接到格子，坐标已是 Nav2 map 系）
            goal_cell = grid_cells[destination_position]
            nav2_gx, nav2_gy = goal_cell.x, goal_cell.y
            plan.append(
                PlanStep(
                    step_id=self._next_step_id(),
                    task_index=task_index,
                    tool="move_to",
                    args={
                        "target": {"x": nav2_gx, "y": nav2_gy, "z": goal_cell.z, "theta": goal_cell.theta},
                        "action": "start",
                        "timeout_s": 30,
                        "request_id": request_id,
                        "goal_id": goal_id,
                        "grid_nav2_x": nav2_gx,
                        "grid_nav2_y": nav2_gy,
                    },
                    status="pending",
                )
            )

            # 第 4 步: 放置方块
            plan.append(
                PlanStep(
                    step_id=self._next_step_id(),
                    task_index=task_index,
                    tool="place",
                    args={
                        "x": goal_cell.x,
                        "y": goal_cell.y,
                        "z": goal_cell.z,
                        "timeout_s": 30,
                        "request_id": request_id,
                        "goal_id": goal_id,
                    },
                    status="pending",
                )
            )

        return plan

    def _next_step_id(self) -> str:
        """生成下一个 step_id"""
        step_id = f"step-{self._step_counter:04d}"
        self._step_counter += 1
        return step_id
