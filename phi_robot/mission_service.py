"""
phi_robot 任务服务层
负责任务的生命周期管理：提交、运行、暂停、恢复、重置、查询
"""

from __future__ import annotations
from typing import Optional
from datetime import datetime
from dataclasses import replace
import uuid

from .models import MissionRecord, PlanStep
from .store import MissionStore
from .mission_planner import MissionPlanner


class MissionService:
    """任务服务 - 业务逻辑层"""

    def __init__(self, store: Optional[MissionStore] = None):
        """
        初始化任务服务
        
        Args:
            store: MissionStore 实例，如果为 None 则创建新实例
        """
        self._store = store or MissionStore()
        self._planner = MissionPlanner()

    def submit(
        self,
        request_id: str,
        scene_id: str,
        goal_id: str,
        scene_version: str,
        stock_layout_version: str,
        destination_order: list,
        options: Optional[dict] = None,
    ) -> str:
        """
        提交任务
        
        Args:
            request_id: 请求 ID（用于幂等性）
            scene_id: 场景 ID
            goal_id: 目标 ID
            scene_version: 场景版本
            stock_layout_version: 备货槽位版本
            destination_order: 目标顺序列表，例如 ["nw", "n", "ne"]
            options: 执行选项，例如 {"max_replans": 2, "timeout_s": 20}
            
        Returns:
            mission_id — 任务 ID
            
        Raises:
            ValueError: 如果输入验证失败
        """
        # 校验输入
        if not request_id:
            raise ValueError("request_id 不能为空")
        if not destination_order:
            raise ValueError("destination_order 不能为空")
        
        # 幂等性 — 相同 request_id 复用已有 mission
        existing = self._store.get_by_request_id(request_id)
        if existing:
            if existing.status in ("running", "paused", "pause_requested"):
                return existing.mission_id
            # 终态或 ready → 重置并复用
            plan = self._planner.plan(
                goal_id=goal_id,
                destination_order=destination_order,
                request_id=request_id,
                scene_version=scene_version,
                stock_layout_version=stock_layout_version,
            )
            record = replace(
                existing,
                goal_id=goal_id,
                scene_version=scene_version,
                stock_layout_version=stock_layout_version,
                status="ready",
                plan=plan,
                current_step_index=-1,
                current_task_index=0,
                current_stock_slot_index=0,
                state={},
                last_error=None,
                pause_requested=False,
                abort_requested=False,
                metrics={
                    "max_replans": options.get("max_replans", 2) if options else 2,
                    "timeout_s": options.get("timeout_s", 20) if options else 20,
                },
            )
            self._store.update(existing.mission_id, record)
            return existing.mission_id
        
        # 生成任务计划（传入版本参数用于查表）
        plan = self._planner.plan(
            goal_id=goal_id,
            destination_order=destination_order,
            request_id=request_id,
            scene_version=scene_version,
            stock_layout_version=stock_layout_version,
        )
        
        # 创建任务记录
        mission_id = f"mission-{uuid.uuid4().hex[:12]}"
        record = MissionRecord(
            mission_id=mission_id,
            request_id=request_id,
            scene_id=scene_id,
            goal_id=goal_id,
            scene_version=scene_version,
            stock_layout_version=stock_layout_version,
            status="ready",
            plan=plan,
            current_step_index=-1,
            current_task_index=0,
            current_stock_slot_index=0,
            state={},
            metrics={
                "max_replans": options.get("max_replans", 2) if options else 2,
                "timeout_s": options.get("timeout_s", 20) if options else 20,
            },
            last_error=None,
        )
        
        # 存储任务
        mission_id = self._store.create(record)
        return mission_id

    def get_mission(self, mission_id: str) -> Optional[MissionRecord]:
        """获取任务记录"""
        return self._store.get(mission_id)

    def run(self, mission_id: str) -> None:
        """
        启动任务执行
        
        Args:
            mission_id: 任务 ID
            
        Raises:
            KeyError: 如果任务不存在
            ValueError: 如果任务状态非法
        """
        record = self._store.get(mission_id)
        if not record:
            raise KeyError(f"mission_id {mission_id} 不存在")
        
        if record.status not in ("ready", "paused"):
            raise ValueError(
                f"任务状态为 {record.status}，无法运行"
            )
        
        record = replace(record, status="running", current_step_index=0)
        self._store.update(mission_id, record)

    def pause(self, mission_id: str) -> None:
        """
        请求暂停任务 — 设置标志位，由 runner 在步骤边界执行实际暂停

        Args:
            mission_id: 任务 ID
        """
        record = self._store.get(mission_id)
        if not record:
            raise KeyError(f"mission_id {mission_id} 不存在")

        if record.status == "running" and not record.pause_requested:
            record = replace(record, pause_requested=True)
            self._store.update(mission_id, record)

    def request_abort(self, mission_id: str) -> None:
        """
        请求中止任务 — 设置标志位，由 runner 在步骤边界执行实际中止

        Args:
            mission_id: 任务 ID
        """
        record = self._store.get(mission_id)
        if not record:
            raise KeyError(f"mission_id {mission_id} 不存在")

        if record.status == "running" and not record.abort_requested:
            record = replace(record, abort_requested=True)
            self._store.update(mission_id, record)

    def resume(self, mission_id: str) -> None:
        """
        恢复任务 — 清除暂停标志，恢复为 running

        Args:
            mission_id: 任务 ID
        """
        record = self._store.get(mission_id)
        if not record:
            raise KeyError(f"mission_id {mission_id} 不存在")

        if record.status == "paused":
            record = replace(record, status="running", pause_requested=False)
            self._store.update(mission_id, record)

    def reset(self, mission_id: str) -> None:
        """
        重置任务为初始状态
        
        Args:
            mission_id: 任务 ID
            
        Raises:
            ValueError: 如果任务状态不允许重置
        """
        record = self._store.get(mission_id)
        if not record:
            raise KeyError(f"mission_id {mission_id} 不存在")
        
        if record.status not in ("paused", "completed", "failed", "aborted"):
            raise ValueError(
                f"任务状态为 {record.status}，无法重置"
            )
        
        record = replace(
            record,
            status="ready",
            current_step_index=-1,
            current_task_index=0,
            current_stock_slot_index=0,
            state={},
            last_error=None
        )
        self._store.update(mission_id, record)

    def mark_step_succeeded(
        self, mission_id: str, result_dict: dict
    ) -> None:
        """
        标记一个步骤成功执行
        
        Args:
            mission_id: 任务 ID
            result_dict: 工具执行结果字典
        """
        record = self._store.get(mission_id)
        if not record:
            raise KeyError(f"mission_id {mission_id} 不存在")
        
        # 更新计划中的步骤状态（创建新的 PlanStep 以支持 frozen）
        if record.current_step_index >= 0 and record.current_step_index < len(record.plan):
            old_step = record.plan[record.current_step_index]
            new_step = replace(old_step, status="completed")
            new_plan = list(record.plan)
            new_plan[record.current_step_index] = new_step
            record = replace(record, plan=new_plan)
        
        # 更新机器人状态
        if result_dict.get("state"):
            record = replace(record, state=result_dict["state"])
        elif result_dict.get("pose"):
            new_state = {**record.state, "pose": result_dict["pose"]}
            record = replace(record, state=new_state)
        
        self._store.update(mission_id, record)

    def mark_step_running(self, mission_id: str) -> None:
        """标记当前步骤为执行中"""
        record = self._store.get(mission_id)
        if not record:
            raise KeyError(f"mission_id {mission_id} 不存在")
        if record.current_step_index >= 0 and record.current_step_index < len(record.plan):
            old_step = record.plan[record.current_step_index]
            new_step = replace(old_step, status="running")
            new_plan = list(record.plan)
            new_plan[record.current_step_index] = new_step
            record = replace(record, plan=new_plan)
            self._store.update(mission_id, record)

    def mark_step_failed(
        self, mission_id: str, error_code: str, error_message: str
    ) -> None:
        """
        标记一个步骤失败
        
        Args:
            mission_id: 任务 ID
            error_code: 错误代码
            error_message: 错误消息
        """
        record = self._store.get(mission_id)
        if not record:
            raise KeyError(f"mission_id {mission_id} 不存在")
        
        # 更新计划中的步骤状态（创建新的 PlanStep 以支持 frozen）
        if record.current_step_index >= 0 and record.current_step_index < len(record.plan):
            old_step = record.plan[record.current_step_index]
            new_step = replace(old_step, status="failed")
            new_plan = list(record.plan)
            new_plan[record.current_step_index] = new_step
            record = replace(record, plan=new_plan)
        
        error_msg = f"{error_code}: {error_message}"
        record = replace(record, last_error=error_msg)
        self._store.update(mission_id, record)

    def mark_completed(self, mission_id: str) -> None:
        """标记任务完成"""
        record = self._store.get(mission_id)
        if not record:
            raise KeyError(f"mission_id {mission_id} 不存在")
        
        record = replace(record, status="completed")
        self._store.update(mission_id, record)

    def mark_failed(self, mission_id: str, reason: str) -> None:
        """标记任务失败"""
        record = self._store.get(mission_id)
        if not record:
            raise KeyError(f"mission_id {mission_id} 不存在")
        
        record = replace(record, status="failed", last_error=reason)
        self._store.update(mission_id, record)

    def mark_aborted(self, mission_id: str, reason: str) -> None:
        """标记任务中止（安全事件等）"""
        record = self._store.get(mission_id)
        if not record:
            raise KeyError(f"mission_id {mission_id} 不存在")
        
        record = replace(record, status="aborted", last_error=reason)
        self._store.update(mission_id, record)

    def advance_step(self, mission_id: str) -> None:
        """推进到下一个步骤"""
        record = self._store.get(mission_id)
        if not record:
            raise KeyError(f"mission_id {mission_id} 不存在")
        
        record = replace(record, current_step_index=record.current_step_index + 1)
        self._store.update(mission_id, record)

    @property
    def store(self) -> MissionStore:
        """获取存储实例"""
        return self._store
