from __future__ import annotations

import os
from typing import Any

from phi_robot.fake_robot_service import DEFAULT_FAKE_SPEC


def load_backend(spec: dict[str, Any] | None = None):
    """Load a backend according to `PHI_ADAPTER` env var.

    Returns an object implementing `execute(tool,args,*,request_id,goal_id,step_id)`
    and `snapshot()`.
    """
    adapter = os.environ.get("PHI_ADAPTER", "unitree_sim")
    spec = spec or DEFAULT_FAKE_SPEC
    if adapter == "unitree_sim":
        from .unitree_sim import UnitreeSimBackend

        return UnitreeSimBackend.from_spec(spec)
    if adapter == "unitree_real":
        raise NotImplementedError("unitree_real adapter not implemented yet")
    raise ValueError(f"unknown PHI_ADAPTER: {adapter}")
