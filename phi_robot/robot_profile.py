"""
robot_profile — 多机器人配置档案管理

单一事实来源: robots.json（每台机器人的 ip / domain_id / camera_host / orin_host）。
切换机器人时把选中 profile 落地成 active_robot.env，供工作站 systemd 单元
（waic-taskmanger / waic-navigation / waic-goal-bridge / waic-foundationpose）
通过 EnvironmentFile 读取。

关键约束: ROS_DOMAIN_ID 在 rclpy.init() 时读取一次，进程运行期不可变。
所以 runtime_domain() 返回的是**本进程启动时的真实 domain**，用于验证切换是否真正生效。
"""

from __future__ import annotations
import json
import os
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("phi_robot.robot_profile")

# robots.json 与本模块同目录
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
ROBOTS_JSON = os.path.join(_THIS_DIR, "robots.json")

# 工作站根目录（active_robot.env / active_robot.lock 落在此）
WAIC_ROOT = os.environ.get("WAIC_ROOT", "/home/hairo/waic")
ENV_FILE = os.path.join(WAIC_ROOT, "active_robot.env")
LOCK_FILE = os.path.join(WAIC_ROOT, "active_robot.lock")


def load_profiles(path: str = ROBOTS_JSON) -> Dict[str, Any]:
    """读取 robots.json，返回 {"active": str, "robots": {...}}"""
    with open(path) as f:
        return json.load(f)


def get_robot(robot_id: str, path: str = ROBOTS_JSON) -> Optional[Dict[str, Any]]:
    """返回指定机器人的 profile（含 id 字段），不存在返回 None"""
    data = load_profiles(path)
    robot = data.get("robots", {}).get(robot_id)
    if robot is None:
        return None
    return {"id": robot_id, **robot}


def get_active(path: str = ROBOTS_JSON) -> Optional[Dict[str, Any]]:
    """返回 robots.json 里 active 指向的 profile（含 id 字段）"""
    data = load_profiles(path)
    active_id = data.get("active")
    if not active_id:
        return None
    return get_robot(active_id, path)


def set_active(robot_id: str, path: str = ROBOTS_JSON) -> Dict[str, Any]:
    """把 robots.json 的 active 更新为 robot_id 并写回。返回目标 profile。"""
    data = load_profiles(path)
    if robot_id not in data.get("robots", {}):
        raise ValueError(f"unknown robot_id: {robot_id}")
    data["active"] = robot_id
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    logger.info("robots.json active → %s", robot_id)
    return {"id": robot_id, **data["robots"][robot_id]}


def write_env_file(profile: Dict[str, Any], path: str = ENV_FILE) -> None:
    """把 profile 落地成 systemd EnvironmentFile。

    生成的键与工作站单元 / start_taskmanger.sh / start_foundationpose.sh 约定一致。
    """
    domain = int(profile["domain_id"])
    lines = [
        f"ROS_DOMAIN_ID={domain}",
        f"APP_DOMAIN={domain}",
        f"WAIC_ROBOT_ID={profile['id']}",
        f"WAIC_ROBOT_IP={profile['ip']}",
        f"WAIC_CAMERA_HOST={profile['camera_host']}",
        f"FP_CAMERA_HOST={profile['camera_host']}",
        f"WAIC_ORIN_HOST={profile['orin_host']}",
    ]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    logger.info("wrote %s (robot=%s domain=%s)", path, profile["id"], domain)


def runtime_domain() -> Optional[int]:
    """本进程启动时真实的 ROS_DOMAIN_ID（用于验证切换生效）。"""
    val = os.environ.get("ROS_DOMAIN_ID")
    if val is None or val == "":
        return None
    try:
        return int(val)
    except ValueError:
        return None


# ── 切换锁 ────────────────────────────────────────────

def is_switching(path: str = LOCK_FILE) -> bool:
    """是否有切换正在进行（lock 文件存在）"""
    return os.path.exists(path)


def read_lock(path: str = LOCK_FILE) -> Dict[str, Any]:
    """读取 lock 内容（切换目标 / 错误标记等），不存在返回 {}"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def write_lock(info: Dict[str, Any], path: str = LOCK_FILE) -> None:
    """写切换锁"""
    with open(path, "w") as f:
        json.dump(info, f, ensure_ascii=False)


def clear_lock(path: str = LOCK_FILE) -> None:
    """清除切换锁（幂等）"""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
