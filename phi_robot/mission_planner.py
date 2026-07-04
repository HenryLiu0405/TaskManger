"""
phi_robot 任务规划生成器
将九宫格路径展开为可执行的 PlanStep 列表
"""

from __future__ import annotations
import math
from typing import List, Dict, Any, Tuple
from .models import PlanStep, GridCell, StockSlot


# ── 坐标变换 ──────────────────────────────────────────────────────────
# 真实场地坐标系 → Nav2 map 坐标系（仿射变换，由四个角点标定确定）
#   真实            →   Nav2 map
#   (0, 3)   左上   →   (-0.46, -2.30)
#   (0, 0)   左下   →   (-0.63,  0.67)
#   (5, 3)   右上   →   ( 4.54, -2.30)
#   (5, 0)   右下   →   ( 4.37,  0.67)


def real_to_nav2(x: float, y: float) -> tuple[float, float]:
    """真实场地坐标 (米) → Nav2 map 坐标 (米)"""
    nav2_x = 1.0 * x + 0.0567 * y - 0.63
    nav2_y = 0.0 * x - 0.99 * y + 0.67
    return (nav2_x, nav2_y)


# ── approach 点计算 ────────────────────────────────────────────────────
# Nav2 地图边界（由四个角点标定确定）
_MAP_X_MIN = -0.7
_MAP_X_MAX = 4.6
_MAP_Y_MIN = -2.4
_MAP_Y_MAX = 0.7


def compute_approach(
    obj_nav2: tuple[float, float, float],
    offset: float = 0.1,
) -> tuple[float, float, float]:
    """
    从物体在 Nav2 map 系下的位姿，计算机器人接近位姿。

    机器人停在物体前方 offset 米处，朝向 +Y（π/2），
    为搬起/放下动作留出操作距离。

    Args:
        obj_nav2: 物体在 Nav2 map 系下的 (x, y, yaw)，yaw 为弧度
        offset: 机器人停在物体前方的距离（米），默认 0.1

    Returns:
        (app_x, app_y, app_yaw) — Nav2 map 系下的接近位姿

    Raises:
        ValueError: 如果接近点超出地图边界
    """
    ox, oy, oyaw = obj_nav2
    _ = oyaw  # 保留参数兼容性；朝向统一用 π/2

    app_yaw = math.pi / 2   # 机器人最终朝向 y 轴正方向
    app_x = ox - offset * math.cos(app_yaw)
    app_y = oy - offset * math.sin(app_yaw)

    if not (_MAP_X_MIN <= app_x <= _MAP_X_MAX):
        raise ValueError(
            f"approach x={app_x:.2f} out of map bounds [{_MAP_X_MIN}, {_MAP_X_MAX}]"
        )
    if not (_MAP_Y_MIN <= app_y <= _MAP_Y_MAX):
        raise ValueError(
            f"approach y={app_y:.2f} out of map bounds [{_MAP_Y_MIN}, {_MAP_Y_MAX}]"
        )

    return (app_x, app_y, app_yaw)


# 九宫格编号到坐标的映射（单位：米，真实场地坐标系）
#   Y=2.5 (北)
#   ┌────┬────┬────┐
#   │ nw │ n  │ ne │
#   ├────┼────┼────┤
#   │ w  │ c  │ e  │
#   ├────┼────┼────┤
#   │ sw │ s  │ se │
#   └────┴────┴────┘  Y=0.5 (南)
GRID_CELLS: Dict[str, GridCell] = {
    "nw": GridCell("nw", 2.5, 2.5, 0.0, 0.0),
    "n":  GridCell("n",  3.5, 2.5, 0.0, 0.0),
    "ne": GridCell("ne", 4.5, 2.5, 0.0, 0.0),
    "w":  GridCell("w",  2.5, 1.5, 0.0, 0.0),
    "c":  GridCell("c",  3.5, 1.5, 0.0, 0.0),
    "e":  GridCell("e",  4.5, 1.5, 0.0, 0.0),
    "sw": GridCell("sw", 2.5, 0.5, 0.0, 0.0),
    "s":  GridCell("s",  3.5, 0.5, 0.0, 0.0),
    "se": GridCell("se", 4.5, 0.5, 0.0, 0.0),
}

# 备货槽位坐标表（真实场地坐标系）
#   3列 × 3行 物料区，slot 按 order_index 0→8 消耗
#   ┌─────┬──────┬─────┐  Y=1.5 (北)
#   │ A1  │  A2  │ A3  │
#   │ 2.0 │ 1.25 │ 0.5 │
#   ├─────┼──────┼─────┤
#   │ A4  │  A5  │ A6  │  Y=1.0
#   ├─────┼──────┼─────┤
#   │ A7  │  A8  │ A9  │  Y=0.5 (南)
#   └─────┴──────┴─────┘
STOCK_SLOTS: List[StockSlot] = [
    StockSlot(0, 2.0, 1.5, 0.0, order_index=0, consumed=False),   # A1
    StockSlot(1, 1.25, 1.5, 0.0, order_index=1, consumed=False),   # A2
    StockSlot(2, 0.5, 1.5, 0.0, order_index=2, consumed=False),    # A3
    StockSlot(3, 2.0, 1.0, 0.0, order_index=3, consumed=False),    # A4
    StockSlot(4, 1.25, 1.0, 0.0, order_index=4, consumed=False),   # A5
    StockSlot(5, 0.5, 1.0, 0.0, order_index=5, consumed=False),    # A6
    StockSlot(6, 2.0, 0.5, 0.0, order_index=6, consumed=False),    # A7
    StockSlot(7, 1.25, 0.5, 0.0, order_index=7, consumed=False),   # A8
    StockSlot(8, 0.5, 0.5, 0.0, order_index=8, consumed=False),    # A9
]


def get_grid_cells(version: str = "scene-v1") -> Dict[str, GridCell]:
    """按场景版本返回九宫格坐标表"""
    # 当前只有 v1，后续版本在此扩展
    return GRID_CELLS


def get_stock_slots(version: str = "stock-v1") -> List[StockSlot]:
    """按库存布局版本返回备货槽位表"""
    # 当前只有 v1，后续版本在此扩展
    return STOCK_SLOTS


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
        stock_slots = get_stock_slots(stock_layout_version)

        for position in destination_order:
            if position not in grid_cells:
                raise ValueError(f"无效的目标方位: {position}")

        plan: List[PlanStep] = []
        self._step_counter = 0

        for task_index, destination_position in enumerate(destination_order):
            if task_index >= len(stock_slots):
                raise ValueError(f"任务 {task_index} 超出槽位数量 {len(stock_slots)}")

            stock_slot = stock_slots[task_index]

            # 第 1 步: 移动到备货槽位 approach 点
            nav2_x, nav2_y = real_to_nav2(stock_slot.x, stock_slot.y)
            try:
                app_x, app_y, app_yaw = compute_approach(
                    (nav2_x, nav2_y, 0.0), offset=0.6
                )
            except ValueError as e:
                raise ValueError(
                    f"slot {stock_slot.slot_id} approach 点越界: {e}"
                ) from e
            plan.append(
                PlanStep(
                    step_id=self._next_step_id(),
                    task_index=task_index,
                    tool="move_to",
                    args={
                        "target": {"x": app_x, "y": app_y, "z": stock_slot.z, "theta": app_yaw},
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

            # 第 3 步: 移动到目标放置位（直接到格子，不需要 approach offset）
            goal_cell = grid_cells[destination_position]
            nav2_gx, nav2_gy = real_to_nav2(goal_cell.x, goal_cell.y)
            plan.append(
                PlanStep(
                    step_id=self._next_step_id(),
                    task_index=task_index,
                    tool="move_to",
                    args={
                        "target": {"x": nav2_gx, "y": nav2_gy, "z": goal_cell.z, "theta": math.pi / 2},
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
