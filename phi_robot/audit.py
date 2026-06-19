"""
AuditLogger — 操作审计日志（JSONL 按天分文件）.

线程安全的单例, 所有写操作均为追加模式 (append-only).
按天自动轮转, 文件名格式: audit_YYYY-MM-DD.jsonl

用法:
    from phi_robot.audit import audit_logger

    audit_logger.log_action_start(tool="move_to", request_id="...", ...)
    audit_logger.log_action_end(tool="move_to", elapsed_ms=1234, ...)
    audit_logger.log_state_change("standing", "moving")
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


# ── 事件类型 ────────────────────────────────────────────────

class AuditEvent:
    ACTION_START  = "action_start"
    ACTION_END    = "action_end"
    ROS2_CALL     = "ros2_call"
    STATE_CHANGE  = "state_change"
    SYSTEM        = "system"


# ── NoOp 回退（防御：即使 audit 模块加载失败也不影响主系统）───

class _NoOpAuditLogger:
    """空操作审计日志器, 与 AuditLogger 接口完全一致."""

    def log_action_start(self, **kwargs: Any) -> None:
        pass

    def log_action_end(self, **kwargs: Any) -> None:
        pass

    def log_ros2_call(self, *args: Any, **kwargs: Any) -> None:
        pass

    def log_state_change(self, old: str, new: str, **extra: Any) -> None:
        pass

    def log_system(self, detail: str, **extra: Any) -> None:
        pass

    def today_info(self) -> dict:
        return {}

    def close(self) -> None:
        pass


# ── 真正的审计日志器 ───────────────────────────────────────

class AuditLogger:
    """线程安全审计日志器 (JSONL, 按天轮转)."""

    MAX_RETRIES = 3

    def __init__(self, log_dir: str = "logs") -> None:
        # 以模块文件所在目录为基准, 解析绝对路径
        try:
            _module_dir = os.path.dirname(os.path.abspath(__file__))
        except Exception:
            _module_dir = os.getcwd()
        if not os.path.isabs(log_dir):
            log_dir = os.path.normpath(os.path.join(_module_dir, "..", log_dir))
        self._log_dir = log_dir
        self._enabled = True
        self._session_id = uuid.uuid4().hex[:12]
        self._lock = threading.Lock()
        self._current_date: Optional[str] = None
        self._current_path: Optional[str] = None
        self._total_writes = 0
        self._total_bytes = 0

    # ── 公开接口 ───────────────────────────────────────────

    def log_action_start(
        self,
        tool: str,
        request_id: str = "",
        goal_id: str = "",
        step_id: str = "",
        args: Optional[dict] = None,
        **extra: Any,
    ) -> None:
        self._write(AuditEvent.ACTION_START, {
            "tool": tool,
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
            "args": args or {},
            **extra,
        })

    def log_action_end(
        self,
        tool: str,
        request_id: str = "",
        goal_id: str = "",
        step_id: str = "",
        status: str = "",
        error_code: Optional[str] = None,
        elapsed_ms: float = 0.0,
        **extra: Any,
    ) -> None:
        self._write(AuditEvent.ACTION_END, {
            "tool": tool,
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
            "status": status,
            "error_code": error_code,
            "elapsed_ms": round(elapsed_ms, 1),
            **extra,
        })

    def log_ros2_call(
        self,
        service: str,
        duration_ms: float = 0.0,
        success: Optional[bool] = None,
        message: str = "",
        **extra: Any,
    ) -> None:
        self._write(AuditEvent.ROS2_CALL, {
            "service": service,
            "duration_ms": round(duration_ms, 1),
            "success": success,
            "message": message[:200] if message else "",
            **extra,
        })

    def log_state_change(self, old: str, new: str, **extra: Any) -> None:
        self._write(AuditEvent.STATE_CHANGE, {
            "old_state": old,
            "new_state": new,
            **extra,
        })

    def log_system(self, detail: str, **extra: Any) -> None:
        self._write(AuditEvent.SYSTEM, {
            "detail": detail,
            **extra,
        })

    def today_info(self) -> dict:
        """返回今天日志文件的基本信息 (供前端展示)."""
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self._file_path(date)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return {
            "date": date,
            "path": path,
            "size_bytes": size,
            "size_human": _human_size(size),
            "session_id": self._session_id,
            "total_writes": self._total_writes,
        }

    def close(self) -> None:
        """显式关闭（停止写入）."""
        self._enabled = False

    # ── 内部方法 ───────────────────────────────────────────

    def _write(self, event_type: str, payload: dict) -> None:
        if not self._enabled:
            return

        line = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": self._session_id,
            "type": event_type,
            **payload,
        }

        for attempt in range(self.MAX_RETRIES):
            try:
                self._rotate_if_needed()
                with self._lock:
                    with open(self._current_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(line, ensure_ascii=False) + "\n")
                    self._total_writes += 1
                    self._total_bytes += len(json.dumps(line, ensure_ascii=False)) + 1
                return
            except Exception:
                if attempt == self.MAX_RETRIES - 1:
                    self._enabled = False  # 静默降级
                else:
                    time.sleep(0.01)

    def _rotate_if_needed(self) -> None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if date != self._current_date:
            self._current_date = date
            self._current_path = self._file_path(date)
            os.makedirs(self._log_dir, exist_ok=True)

    def _file_path(self, date: str) -> str:
        return os.path.join(self._log_dir, f"audit_{date}.jsonl")


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


# ── 全局单例（防御式创建）─────────────────────────────────

try:
    audit_logger = AuditLogger()
except Exception:
    audit_logger = _NoOpAuditLogger()
