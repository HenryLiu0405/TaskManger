"""
phi_robot 事件推送中心
支持异步 WebSocket 订阅和同步 SSE 推送
"""

from __future__ import annotations
from typing import Dict, List, Optional, AsyncIterator, Set, Tuple
import asyncio
import queue
from datetime import datetime

from .models import MissionEvent
from .store import MissionStore


class MissionEventHub:
    """事件推送中心 - 管理 WebSocket 连接和事件广播"""

    def __init__(self, store: MissionStore):
        """
        初始化事件中心
        
        Args:
            store: MissionStore 实例
        """
        self._store = store
        # mission_id -> set of (queue, client_id) 客户端映射
        self._subscribers: Dict[str, Set[Tuple[asyncio.Queue, str]]] = {}
        self._client_counter = 0

    async def subscribe(self, mission_id: str) -> AsyncIterator[MissionEvent]:
        """
        订阅任务事件流
        
        Args:
            mission_id: 任务 ID
            
        Yields:
            MissionEvent — 增量事件
        """
        if mission_id not in self._subscribers:
            self._subscribers[mission_id] = set()
        
        client_id = f"client-{self._client_counter}"
        self._client_counter += 1
        
        queue: asyncio.Queue[Optional[MissionEvent]] = asyncio.Queue()
        self._subscribers[mission_id].add((queue, client_id))
        
        try:
            # 先推送已有事件
            last_event_id = -1
            for event in self._store.get_all_events(mission_id):
                yield event
                last_event_id = event.event_id
            
            # 然后推送新事件
            while True:
                event = await queue.get()
                if event is None:  # 停止信号
                    break
                yield event
        finally:
            # 清理订阅
            self._subscribers[mission_id].discard((queue, client_id))
            if not self._subscribers[mission_id]:
                del self._subscribers[mission_id]

    async def publish_event(self, mission_id: str, event: MissionEvent) -> None:
        """
        发布事件
        
        Args:
            mission_id: 任务 ID
            event: MissionEvent 实例
        """
        # 存储到数据库
        self._store.put_event(mission_id, event)
        
        # 广播给所有订阅者
        if mission_id in self._subscribers:
            for queue, _ in list(self._subscribers[mission_id]):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass  # 客户端断线，跳过

    def unsubscribe_all(self, mission_id: str) -> None:
        """停止所有该任务的订阅"""
        if mission_id in self._subscribers:
            for queue, _ in list(self._subscribers[mission_id]):
                try:
                    queue.put_nowait(None)  # 停止信号
                except asyncio.QueueFull:
                    pass
            self._subscribers[mission_id].clear()

    def get_subscriber_count(self, mission_id: str) -> int:
        """获取订阅者数量"""
        return len(self._subscribers.get(mission_id, set()))

    async def close(self) -> None:
        """关闭事件中心"""
        for mission_id in list(self._subscribers.keys()):
            self.unsubscribe_all(mission_id)


class SyncEventHub:
    """同步版事件中心，用于 Flask 线程环境（SSE 推送）"""

    def __init__(self, store: MissionStore):
        self._store = store
        self._queues: Dict[str, List[queue.Queue]] = {}

    def publish(self, mission_id: str, event: MissionEvent) -> None:
        """先落库，再推送给所有订阅者"""
        self._store.put_event(mission_id, event)
        for q in self._queues.get(mission_id, []):
            try:
                q.put_nowait(event.to_dict())
            except queue.Full:
                pass

    def subscribe_sync(self, mission_id: str) -> queue.Queue:
        """返回一个 queue.Queue，调用方阻塞 get()"""
        q: queue.Queue = queue.Queue()
        self._queues.setdefault(mission_id, []).append(q)
        return q

    def unsubscribe(self, mission_id: str, q: queue.Queue) -> None:
        """移除订阅"""
        queues = self._queues.get(mission_id, [])
        if q in queues:
            queues.remove(q)
        if not queues:
            self._queues.pop(mission_id, None)

    def close(self, mission_id: str) -> None:
        """关闭某个任务的所有订阅"""
        for q in self._queues.pop(mission_id, []):
            try:
                q.put_nowait(None)
            except queue.Full:
                pass
