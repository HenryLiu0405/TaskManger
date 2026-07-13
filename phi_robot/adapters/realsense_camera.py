"""
RS 画面数据源 — 从 view_realsense.py 写的共享文件读取。

view_realsense.py 每帧原子写入 /tmp/rs_latest.jpg，
本模块只负责读取，不打开相机（避免设备独占冲突）。
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger("phi_robot.realsense_camera")

_SHARED_FRAME_PATH = "/tmp/rs_latest.jpg"


class RealsenseGrabber:
    """从共享文件读取最新 RealSense 帧（view_realsense.py 写入）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._buffer: bytes | None = None
        self._last_mtime: float = 0.0

    # ── public ────────────────────────────────────────────────

    def get_frame(self) -> bytes | None:
        """返回最新共享帧 bytes，文件未更新时返回上次缓存。"""
        try:
            mtime = os.path.getmtime(_SHARED_FRAME_PATH)
        except OSError:
            return self._buffer  # 文件尚不存在，返回旧缓存

        with self._lock:
            if mtime <= self._last_mtime:
                return self._buffer
            self._last_mtime = mtime

        try:
            with open(_SHARED_FRAME_PATH, 'rb') as f:
                data = f.read()
            if data:
                with self._lock:
                    self._buffer = data
                return data
        except (IOError, OSError) as e:
            logger.warning("RealsenseGrabber: 读取共享帧失败 — %s", e)

        return self._buffer

    @property
    def active(self) -> bool:
        return os.path.exists(_SHARED_FRAME_PATH)

    def close(self) -> None:
        pass  # 不持有资源
