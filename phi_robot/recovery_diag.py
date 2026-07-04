"""
真机联调诊断事件模型。

Recovery 模块的内部决策链通过 DiagEvent 暴露给前端，
让操作员在分步调试时能看到 gate 结果、FP 快照、scan 进度等。

纯数据层——不依赖 ROS，不 import recovery 模块。
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger("phi_robot.recovery_diag")


# ═══════════════════════════════════════════════════════════════
# 阶段常量
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 事件类型常量
# ═══════════════════════════════════════════════════════════════

EVT_FP_SNAPSHOT = "fp_snapshot"
EVT_ODOM = "odom_snapshot"
EVT_STEP_START = "step_start"
EVT_STEP_RESULT = "step_result"
EVT_SELECT_TARGET = "select_target"
EVT_DROP_STATUS = "drop_status"
EVT_DROP_DETECTOR_STATE = "drop_detector_state"

# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════


@dataclass
class DiagEvent:
    """一次 recovery 诊断事件"""

    timestamp: str          # "HH:MM:SS.mmm"
    step_id: str            # PlanStep.step_id
    phase: str              # PHASE_* 常量
    event_type: str         # EVT_* 常量
    data: dict = field(default_factory=dict)

    @staticmethod
    def now() -> str:
        return datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.timestamp,
            "step": self.step_id,
            "phase": self.phase,
            "type": self.event_type,
            "data": self.data,
        }


class DiagCollector:
    """诊断事件收集器，线程安全。

    分步调试模式下 StepDebugController 持有一个 collector，
    adapter 通过诊断回调向 collector 推送事件。

    提供两种读取模式：
      - drain():   取出并清空 — SSE 增量推送用（读+清空在同一锁内原子完成）
      - snapshot(): 只读副本   — state API 用

    线程安全保证：push() / drain() / snapshot() / clear() 均持有同一把锁，
    drain() 的读+清空是原子操作，不会丢失并发 push 的事件。
    """

    def __init__(self, max_events: int = 500):
        self._events: list[DiagEvent] = []
        self._max = max_events
        self._lock = threading.Lock()
        self._truncation_warned: bool = False

    def push(self, event: DiagEvent) -> None:
        with self._lock:
            self._events.append(event)
            if len(self._events) > self._max:
                if not self._truncation_warned:
                    logger.warning(
                        "DiagCollector: 事件数超过上限 %d，开始截断旧事件。"
                        "可能原因：SSE 连接断开或前端卡顿导致 drain() 未被及时调用。",
                        self._max,
                    )
                    self._truncation_warned = True
                self._events = self._events[-self._max:]

    def drain(self) -> list[dict[str, Any]]:
        """取出并清空当前缓冲区（SSE 增量推送）。

        读+清空在同一锁内完成，保证并发 push 的事件不丢失。"""
        with self._lock:
            events = self._events[:]
            self._events.clear()
            self._truncation_warned = False  # drain 后重置告警标志
        return [e.to_dict() for e in events]

    def snapshot(self) -> list[dict[str, Any]]:
        """返回当前缓冲区副本（不清空）"""
        with self._lock:
            return [e.to_dict() for e in self._events]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._truncation_warned = False

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


# ═══════════════════════════════════════════════════════════════
# 辅助构建函数
# ═══════════════════════════════════════════════════════════════


def make_fp_snapshot(step_id: str, phase: str, fp_state: dict | None) -> DiagEvent:
    """从 /fp_state 解析结果构建快照事件"""
    if fp_state is None:
        return DiagEvent(
            timestamp=DiagEvent.now(), step_id=step_id,
            phase=phase, event_type=EVT_FP_SNAPSHOT,
            data={"available": False, "trackers": []},
        )
    trackers = []
    for obj in fp_state.get("trackers", []):
        trackers.append({
            "id": obj.get("id"),
            "state": obj.get("state"),
            "frames": obj.get("tracking_frames", 0),
            "mask_area": obj.get("mask_area", 0),
            "x": round(float(obj.get("x", 0.0)), 3),
            "y": round(float(obj.get("y", 0.0)), 3),
            "z": round(float(obj.get("z", 0.0)), 3),
        })
    return DiagEvent(
        timestamp=DiagEvent.now(), step_id=step_id,
        phase=phase, event_type=EVT_FP_SNAPSHOT,
        data={"available": True, "tracker_count": len(trackers), "trackers": trackers},
    )


def make_odom_snapshot(
    step_id: str, x: float, y: float, yaw: float,
) -> DiagEvent:
    """里程计快照"""
    return DiagEvent(
        timestamp=DiagEvent.now(), step_id=step_id,
        phase="", event_type=EVT_ODOM,
        data={
            "x": round(x, 3),
            "y": round(y, 3),
            "yaw_deg": round(math.degrees(yaw), 1),
        },
    )


def make_step_result(
    step_id: str, status: str, error_code: str, message: str, elapsed_s: float,
) -> DiagEvent:
    """步骤执行结果"""
    return DiagEvent(
        timestamp=DiagEvent.now(), step_id=step_id,
        phase="", event_type=EVT_STEP_RESULT,
        data={
            "status": status,
            "error_code": error_code,
            "message": message,
            "elapsed_s": round(elapsed_s, 2),
        },
    )


# 🆕 v3: SelectTarget + drop_status 诊断事件工厂

def make_select_target(
    step_id: str, select: bool, pick_x: float, pick_y: float,
    matched_object_id: int, success: bool,
) -> DiagEvent:
    """SelectTarget service 调用结果"""
    return DiagEvent(
        timestamp=DiagEvent.now(), step_id=step_id,
        phase="", event_type=EVT_SELECT_TARGET,
        data={
            "select": select,
            "pick_x": pick_x, "pick_y": pick_y,
            "matched_object_id": matched_object_id,
            "success": success,
        },
    )


def make_drop_status(
    step_id: str, box_present: bool,
    odom_x: float = 0.0, odom_y: float = 0.0,
) -> DiagEvent:
    """/vision/box_drop_status 状态变化"""
    return DiagEvent(
        timestamp=DiagEvent.now(), step_id=step_id,
        phase="carry_walk", event_type=EVT_DROP_STATUS,
        data={
            "box_present": box_present,
            "odom_x": round(odom_x, 3),
            "odom_y": round(odom_y, 3),
        },
    )


def make_drop_detector_state(
    step_id: str, enabled: bool, trigger_source: str = "",
) -> DiagEvent:
    """掉箱检测启用/禁用事件"""
    return DiagEvent(
        timestamp=DiagEvent.now(), step_id=step_id,
        phase="carry_walk", event_type=EVT_DROP_DETECTOR_STATE,
        data={
            "enabled": enabled,
            "trigger_source": trigger_source,
        },
    )


# ═══════════════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════════════


def _fmt_approach(approach: tuple | None) -> list[float] | None:
    if approach is None:
        return None
    return [round(float(v), 3) for v in approach]
