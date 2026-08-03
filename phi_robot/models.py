"""
phi_robot 数据模型定义
对标 think.md § 5.5.2
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List
from datetime import datetime
import uuid


@dataclass(frozen=True)
class GridCell:
    """九宫格单元"""
    position: str  # nw, n, ne, w, c, e, sw, s, se
    x: float
    y: float
    z: float = 0.0
    theta: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> GridCell:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class StockSlot:
    """备货槽位"""
    slot_id: int  # 0-8
    x: float
    y: float
    z: float = 0.0
    order_index: int = 0
    consumed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> StockSlot:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class ToolResult:
    """工具执行结果"""
    status: str  # ok, error
    error_code: Optional[str] = None
    message: str = ""
    state: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    request_id: Optional[str] = None
    goal_id: Optional[str] = None
    step_id: Optional[str] = None
    invocation_id: Optional[str] = None
    outcome: str = ""
    error_category: Optional[str] = None
    verification: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ToolResult:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def __repr__(self) -> str:
        return (
            f"ToolResult(status={self.status}, error_code={self.error_code}, "
            f"message={self.message!r}, step_id={self.step_id})"
        )


@dataclass(frozen=True)
class ToolCommand:
    """工具调用命令"""
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    goal_id: str = ""
    step_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ToolCommand:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def __repr__(self) -> str:
        return f"ToolCommand(tool={self.tool}, step_id={self.step_id})"


@dataclass(frozen=True)
class PlanStep:
    """计划步骤"""
    step_id: str
    task_index: int
    tool: str
    args: Dict[str, Any]
    status: str = "pending"
    result: Optional[ToolResult] = None
    skill_version: str = "1.0"
    annotations: Dict[str, Any] = field(default_factory=dict)
    invocation_id: Optional[str] = None
    attempt: int = 0

    def to_dict(self) -> dict:
        data = asdict(self)
        if self.result:
            data["result"] = self.result.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> PlanStep:
        data = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        if data.get("result"):
            data["result"] = ToolResult.from_dict(data["result"])
        return cls(**data)

    def __repr__(self) -> str:
        return (
            f"PlanStep(step_id={self.step_id}, tool={self.tool}, "
            f"status={self.status})"
        )


@dataclass
class MissionEvent:
    """事件记录"""
    event_id: int
    type: str
    timestamp: datetime
    mission_id: str
    step_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "timestamp": self.timestamp.isoformat(),
            "mission_id": self.mission_id,
            "step_id": self.step_id,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> MissionEvent:
        data = dict(data)
        if isinstance(data.get("timestamp"), str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def __repr__(self) -> str:
        return (
            f"MissionEvent(event_id={self.event_id}, type={self.type}, "
            f"step_id={self.step_id})"
        )


@dataclass
class MissionRecord:
    """任务主记录"""
    mission_id: str
    request_id: str
    scene_id: str
    goal_id: str
    scene_version: str
    stock_layout_version: str
    status: str
    execution_epoch: int = 1
    plan: List[PlanStep] = field(default_factory=list)
    current_step_index: int = -1
    current_task_index: int = 0
    current_stock_slot_index: int = 0
    state: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    last_error: Optional[str] = None
    pause_requested: bool = False
    abort_requested: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "mission_id": self.mission_id,
            "request_id": self.request_id,
            "scene_id": self.scene_id,
            "goal_id": self.goal_id,
            "scene_version": self.scene_version,
            "stock_layout_version": self.stock_layout_version,
            "status": self.status,
            "execution_epoch": self.execution_epoch,
            "plan": [step.to_dict() for step in self.plan],
            "current_step_index": self.current_step_index,
            "current_task_index": self.current_task_index,
            "current_stock_slot_index": self.current_stock_slot_index,
            "state": self.state,
            "metrics": self.metrics,
            "last_error": self.last_error,
            "pause_requested": self.pause_requested,
            "abort_requested": self.abort_requested,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> MissionRecord:
        data = dict(data)
        if data.get("plan"):
            data["plan"] = [PlanStep.from_dict(s) for s in data["plan"]]
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def __repr__(self) -> str:
        return (
            f"MissionRecord(mission_id={self.mission_id}, status={self.status}, "
            f"step={self.current_step_index}/{len(self.plan)})"
        )
