from __future__ import annotations

import os
from typing import Any

from phi_robot.fake_robot_service import DEFAULT_FAKE_SPEC


def load_backend(spec: dict[str, Any] | None = None):
    """Load a backend according to `PHI_ADAPTER` env var.

    Returns an object implementing `execute(tool,args,*,request_id,goal_id,step_id)`
    and `snapshot()`.
    """
    # 支持将 move_to 单独路由到真实同事服务器的临时配置：
    # - 设置 PHI_MOVE_TO_URL（例如 http://10.0.0.5:5000）时，返回 RemoteUnitreeAdapter，
    #   将普通请求发到 PHI_SIM_BASE_URL（默认为 http://127.0.0.1:8080），
    #   并把 move_to 请求路由到 PHI_MOVE_TO_URL。这样可以保留其他模拟函数，仅把 move_to 委托给真机同事。
    adapter = os.environ.get("PHI_ADAPTER", "unitree_sim")
    spec = spec or DEFAULT_FAKE_SPEC

    move_to_url = os.environ.get("PHI_MOVE_TO_URL")
    if move_to_url:
        from .remote_unitree import RemoteUnitreeAdapter

        sim_base = os.environ.get("PHI_SIM_BASE_URL", "http://127.0.0.1:8080")
        return RemoteUnitreeAdapter(base_url=sim_base, move_to_base_url=move_to_url)

    if adapter == "unitree_sim":
        from .unitree_sim import UnitreeSimBackend

        return UnitreeSimBackend.from_spec(spec)
    if adapter == "unitree_real":
        raise NotImplementedError("unitree_real adapter not implemented yet")
    raise ValueError(f"unknown PHI_ADAPTER: {adapter}")
