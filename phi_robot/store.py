"""
phi_robot 任务存储层
内存 + JSON 文件持久化，支持后续 SQLite/PostgreSQL 扩展
"""

from __future__ import annotations
import json
import os
import threading
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path
from dataclasses import replace
import uuid
from .models import MissionRecord, MissionEvent


class MissionStore:
    """任务存储 — 内存索引 + JSON 文件持久化"""

    def __init__(self, data_dir: str = None):
        self._missions: Dict[str, MissionRecord] = {}
        self._events: Dict[str, List[MissionEvent]] = {}
        self._request_to_mission: Dict[str, str] = {}
        self._next_event_id: Dict[str, int] = {}

        self._data_dir = Path(
            data_dir or os.path.expanduser("~/.phi_robot/data")
        )
        self._data_dir.mkdir(parents=True, exist_ok=True)
        # Readers must not observe a new in-memory state before the matching
        # durable snapshot has finished.  The re-entrant lock lets update()
        # hold that visibility boundary while _persist() performs its atomic
        # temporary-file rename.
        self._write_lock = threading.RLock()

        # 启动时从磁盘恢复已有任务
        self._load_all()

    # ── 持久化 ──────────────────────────────────────────────

    def _persist(self, mission_id: str) -> None:
        """原子写入：先写 .tmp 再 rename（线程安全）"""
        record = self._missions.get(mission_id)
        if not record:
            return
        events = self._events.get(mission_id, [])
        filepath = self._data_dir / f"{mission_id}.json"
        tmp = filepath.with_suffix(f".tmp-{os.getpid()}-{threading.get_ident()}")
        with self._write_lock:
            with open(tmp, "w") as f:
                json.dump(
                    {
                        "record": record.to_dict(),
                        "events": [e.to_dict() for e in events],
                    },
                    f,
                    indent=2,
                    default=str,
                )
            tmp.rename(filepath)

    def _load_all(self) -> None:
        """从磁盘恢复所有已持久化的任务"""
        for filepath in sorted(self._data_dir.glob("*.json")):
            mission_id = filepath.stem
            try:
                with open(filepath) as f:
                    data = json.load(f)
                record = MissionRecord.from_dict(data["record"])
                events = [MissionEvent.from_dict(e) for e in data.get("events", [])]
                self._missions[mission_id] = record
                self._request_to_mission[record.request_id] = mission_id
                self._events[mission_id] = events
                self._next_event_id[mission_id] = len(events)
            except Exception:
                # 损坏的文件跳过不阻塞启动
                pass

    # ── CRUD ─────────────────────────────────────────────────

    def create(self, record: MissionRecord) -> str:
        if record.request_id in self._request_to_mission:
            existing_mission_id = self._request_to_mission[record.request_id]
            existing = self._missions.get(existing_mission_id)
            if existing and existing.goal_id == record.goal_id:
                return existing_mission_id
            raise ValueError(
                f"request_id {record.request_id} 已存在但参数不匹配"
            )

        if not record.mission_id:
            mission_id = f"mission-{uuid.uuid4().hex[:12]}"
        else:
            mission_id = record.mission_id
            record = MissionRecord(
                mission_id=mission_id,
                request_id=record.request_id,
                scene_id=record.scene_id,
                goal_id=record.goal_id,
                scene_version=record.scene_version,
                stock_layout_version=record.stock_layout_version,
                status=record.status,
                execution_epoch=record.execution_epoch,
                plan=record.plan,
                current_step_index=record.current_step_index,
                current_task_index=record.current_task_index,
                current_stock_slot_index=record.current_stock_slot_index,
                state=record.state,
                metrics=record.metrics,
                last_error=record.last_error,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )

        self._request_to_mission[record.request_id] = mission_id
        self._missions[mission_id] = record
        self._events[mission_id] = []
        self._next_event_id[mission_id] = 0
        self._persist(mission_id)
        return mission_id

    def get(self, mission_id: str) -> Optional[MissionRecord]:
        with self._write_lock:
            return self._missions.get(mission_id)

    def update(self, mission_id: str, record: MissionRecord) -> None:
        with self._write_lock:
            if mission_id not in self._missions:
                raise KeyError(f"mission_id {mission_id} 不存在")
            record = replace(record, updated_at=datetime.now())
            self._missions[mission_id] = record
            self._persist(mission_id)

    def put_event(self, mission_id: str, event: MissionEvent) -> int:
        if mission_id not in self._missions:
            raise KeyError(f"mission_id {mission_id} 不存在")

        event_id = self._next_event_id[mission_id]
        event.event_id = event_id
        self._next_event_id[mission_id] += 1

        if not event.timestamp:
            event.timestamp = datetime.now()

        self._events[mission_id].append(event)
        self._persist(mission_id)
        return event_id

    def get_events(
        self, mission_id: str, since_event_id: int = 0
    ) -> List[MissionEvent]:
        with self._write_lock:
            if mission_id not in self._missions:
                raise KeyError(f"mission_id {mission_id} 不存在")
            return [e for e in self._events[mission_id] if e.event_id > since_event_id]

    def get_all_events(self, mission_id: str) -> List[MissionEvent]:
        with self._write_lock:
            if mission_id not in self._missions:
                raise KeyError(f"mission_id {mission_id} 不存在")
            return list(self._events[mission_id])

    def get_all_missions(self) -> List[MissionRecord]:
        with self._write_lock:
            return list(self._missions.values())

    def get_by_request_id(self, request_id: str) -> Optional[MissionRecord]:
        with self._write_lock:
            mission_id = self._request_to_mission.get(request_id)
            return self._missions.get(mission_id) if mission_id else None

    def clear(self) -> None:
        """清空所有数据（用于测试）"""
        # 删除磁盘上的文件
        for mission_id in self._missions:
            filepath = self._data_dir / f"{mission_id}.json"
            try:
                filepath.unlink(missing_ok=True)
            except OSError:
                pass
        self._missions.clear()
        self._events.clear()
        self._request_to_mission.clear()
        self._next_event_id.clear()

    def __repr__(self) -> str:
        return (
            f"MissionStore(missions={len(self._missions)}, "
            f"events={sum(len(e) for e in self._events.values())}, "
            f"dir={self._data_dir})"
        )
