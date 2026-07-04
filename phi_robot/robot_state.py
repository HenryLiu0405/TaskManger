"""
SafetyStateMachine — 全局机器人安全状态机.

单例, 线程安全. 在所有操作前检查状态迁移是否合法.
用法:
    from phi_robot.robot_state import safety_fsm, RobotState

    if safety_fsm.can_transition(RobotState.MOVING):
        safety_fsm.transition(RobotState.MOVING)
    else:
        return {"ok": False, "error": f"当前 {safety_fsm.state}, 不可操作"}
"""

import threading
import logging
from enum import Enum

from .audit import audit_logger

logger = logging.getLogger(__name__)


class RobotState(Enum):
    """机器人全局状态."""
    IDLE = "idle"           # 初始, 未就绪
    STANDING = "standing"   # 站立就绪
    MOVING = "moving"       # 走路中
    ARRIVED = "arrived"     # 到达目标位置
    PICKING = "picking"     # 搬起执行中
    HOLDING = "holding"     # 搬着箱子
    PLACING = "placing"     # 放下执行中
    EMERGENCY = "emergency" # 急停
    ERROR = "error"         # 故障


# -- 允许的状态迁移白名单 ------------------------------------------
ALLOWED_TRANSITIONS: dict[RobotState, set[RobotState]] = {
    RobotState.IDLE:      {RobotState.STANDING},
    RobotState.STANDING:  {RobotState.MOVING},
    RobotState.MOVING:    {RobotState.ARRIVED, RobotState.STANDING},
    RobotState.ARRIVED:   {RobotState.PICKING, RobotState.STANDING, RobotState.MOVING, RobotState.PLACING},
    RobotState.PICKING:   {RobotState.HOLDING, RobotState.ERROR},
    RobotState.HOLDING:   {RobotState.MOVING, RobotState.PLACING},
    RobotState.PLACING:   {RobotState.STANDING, RobotState.ERROR},
    # 从紧急/错误状态只能复位到 IDLE:
    RobotState.EMERGENCY: {RobotState.IDLE},
    RobotState.ERROR:     {RobotState.IDLE},
}

# -- 哪些状态下禁止走路 -------------------------------------------
NO_WALK_STATES = {RobotState.PICKING, RobotState.PLACING, RobotState.EMERGENCY, RobotState.ERROR}

# -- 哪些状态下禁止搬起 -------------------------------------------
NO_PICK_STATES = {RobotState.IDLE, RobotState.MOVING, RobotState.PICKING,
                  RobotState.HOLDING, RobotState.PLACING, RobotState.EMERGENCY, RobotState.ERROR}

# -- 哪些状态下禁止放下 -------------------------------------------
NO_PLACE_STATES = {RobotState.IDLE, RobotState.STANDING, RobotState.MOVING,
                   RobotState.PICKING, RobotState.EMERGENCY, RobotState.ERROR}


class SafetyStateMachine:
    """线程安全的状态机单例."""

    def __init__(self) -> None:
        self._state: RobotState = RobotState.STANDING  # 默认站立
        self._lock = threading.Lock()
        self._bypass: bool = False  # 旁路模式：跳过所有安全检查

    # -- 旁路开关 -------------------------------------------------

    def set_bypass(self, enabled: bool) -> None:
        """启用/禁用旁路模式。

        True  → 所有 can_walk/can_pick/can_place 返回 True，
                transition() 直接迁移不校验白名单。
        False → 恢复正常安全检查（默认）。
        """
        with self._lock:
            self._bypass = enabled
            logger.warning("SafetyStateMachine: 旁路模式 %s — 所有安全检查%s",
                           "启用" if enabled else "关闭",
                           "已跳过" if enabled else "已恢复")

    @property
    def bypass(self) -> bool:
        return self._bypass

    # -- 只读 ----------------------------------------------------

    @property
    def state(self) -> RobotState:
        return self._state

    @property
    def state_value(self) -> str:
        return self._state.value

    def can_transition(self, target: RobotState) -> bool:
        """检查是否可以迁移到 target 状态."""
        if self._bypass:
            return True
        with self._lock:
            return target in ALLOWED_TRANSITIONS.get(self._state, set())

    def can_walk(self) -> bool:
        """当前是否允许走路 — 状态迁移表 + 黑名单双重校验."""
        if self._bypass:
            return True
        with self._lock:
            return (RobotState.MOVING in ALLOWED_TRANSITIONS.get(self._state, set())
                    and self._state not in NO_WALK_STATES)

    def can_pick(self) -> bool:
        """当前是否允许搬起 — 状态迁移表 + 黑名单双重校验."""
        if self._bypass:
            return True
        with self._lock:
            return (RobotState.PICKING in ALLOWED_TRANSITIONS.get(self._state, set())
                    and self._state not in NO_PICK_STATES)

    def can_place(self) -> bool:
        """当前是否允许放下 — 状态迁移表 + 黑名单双重校验."""
        if self._bypass:
            return True
        with self._lock:
            return (RobotState.PLACING in ALLOWED_TRANSITIONS.get(self._state, set())
                    and self._state not in NO_PLACE_STATES)

    # -- 写入 ----------------------------------------------------

    def transition(self, target: RobotState) -> bool:
        """尝试迁移到 target 状态, 返回是否成功."""
        with self._lock:
            if self._bypass:
                old = self._state
                self._state = target
                logger.info(f"StateMachine(bypass): {old.value} → {target.value}")
                audit_logger.log_state_change(old.value, target.value)
                return True
            allowed = ALLOWED_TRANSITIONS.get(self._state, set())
            if target in allowed:
                old = self._state
                self._state = target
                logger.info(f"StateMachine: {old.value} → {target.value}")
                audit_logger.log_state_change(old.value, target.value)
                return True
            logger.warning(
                f"StateMachine: 拒绝非法迁移 {self._state.value} → {target.value}"
            )
            return False

    def force_state(self, target: RobotState) -> None:
        """强制设置状态（仅用于 emergency/reset）."""
        with self._lock:
            old = self._state
            self._state = target
            logger.info(f"StateMachine(force): {old.value} → {target.value}")
            audit_logger.log_state_change(old.value, target.value)

    def reset(self) -> None:
        """复位到站立状态."""
        self.force_state(RobotState.STANDING)

    def emergency(self) -> None:
        """触发急停."""
        self.force_state(RobotState.EMERGENCY)

    def to_error(self) -> None:
        """进入错误状态."""
        self.force_state(RobotState.ERROR)


# -- 全局单例 ----------------------------------------------------
safety_fsm = SafetyStateMachine()
