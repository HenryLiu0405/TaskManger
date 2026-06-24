"""
phi_robot 任务规划生成器
将九宫格路径展开为可执行的 PlanStep 列表
"""

from __future__ import annotations
from typing import List, Dict, Any, Tuple
from .models import PlanStep, GridCell, StockSlot
from .recovery.coordinate_transform import real_to_nav2


# 九宫格编号到坐标的映射（单位：米）
GRID_CELLS: Dict[str, GridCell] = {
    "nw": GridCell("nw", 2.7, 2.5, 0.0, 0.0),
    "n": GridCell("n", 3.5, 2.5, 0.0, 0.0),
    "ne": GridCell("ne", 4.3, 2.5, 0.0, 0.0),
    "w": GridCell("w", 2.7, 1.5, 0.0, 0.0),
    "c": GridCell("c", 3.5, 1.5, 0.0, 0.0),
    "e": GridCell("e", 4.3, 1.5, 0.0, 0.0),
    "sw": GridCell("sw", 2.7, 0.5, 0.0, 0.0),
    "s": GridCell("s", 3.5, 0.5, 0.0, 0.0),
    "se": GridCell("se", 4.3, 0.5, 0.0, 0.0),
}

# 备货槽位坐标表（后端固定维护）
STOCK_SLOTS: List[StockSlot] = [
    StockSlot(0, 1.6, 2.6, 0.0, order_index=0, consumed=False),   # 顶排左
    StockSlot(1, 0.9, 2.6, 0.0, order_index=1, consumed=False),   # 顶排中
    StockSlot(2, 0.2, 2.6, 0.0, order_index=2, consumed=False),   # 顶排右
    StockSlot(3, 1.6, 2.1, 0.0, order_index=3, consumed=False),   # 中排左
    StockSlot(4, 0.9, 2.1, 0.0, order_index=4, consumed=False),   # 中排中
    StockSlot(5, 0.2, 2.1, 0.0, order_index=5, consumed=False),   # 中排右
    StockSlot(6, 1.6, 1.6, 0.0, order_index=6, consumed=False),   # 底排左
    StockSlot(7, 0.9, 1.6, 0.0, order_index=7, consumed=False),   # 底排中
    StockSlot(8, 0.2, 1.6, 0.0, order_index=8, consumed=False),   # 底排右
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

            # 第 1 步: 移动到备货槽位  (current 由 runner 执行时注入)
            nav2_x, nav2_y = real_to_nav2(stock_slot.x, stock_slot.y)
            plan.append(
                PlanStep(
                    step_id=self._next_step_id(),
                    task_index=task_index,
                    tool="move_to",
                    args={
                        "target": {"x": nav2_x, "y": nav2_y, "z": stock_slot.z, "theta": 0.0},
                        "action": "start",
                        "timeout_s": 30,
                        "request_id": request_id,
                        "goal_id": goal_id,
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

            # 第 3 步: 移动到目标放置位  (current 由 runner 执行时注入)
            goal_cell = grid_cells[destination_position]
            nav2_gx, nav2_gy = real_to_nav2(goal_cell.x, goal_cell.y)
            plan.append(
                PlanStep(
                    step_id=self._next_step_id(),
                    task_index=task_index,
                    tool="move_to",
                    args={
                        "target": {"x": nav2_gx, "y": nav2_gy, "z": goal_cell.z, "theta": 0.0},
                        "action": "start",
                        "timeout_s": 30,
                        "request_id": request_id,
                        "goal_id": goal_id,
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
