"""
SONIC 搬起姿势匹配器。

判断机器人从当前位置能否用 SONIC 搬起物体——
给定机器人位姿和物体在 map 系下的位姿，遍历可用姿势配置，
检查距离和朝向是否在某个姿势的允许范围内。

当前仅配置 `default` 单姿势（距离 [0.25m, 0.35m]）。
同事完成 SONIC 多姿势后，在 POSTURE_CONFIG 中追加条目即可，
match_grasp_posture() 会返回第一个匹配的姿势名。
"""

import math

from .approach_calculator import compute_approach

# SONIC 可用搬起姿势配置
# 每条：姿势名 → 最小距离(m) / 最大距离(m) / 朝向容差(°)
# 匹配时按列表顺序遍历，返回第一个满足条件的姿势
POSTURE_CONFIG: list[dict] = [
    {
        "name": "default",
        "d_min": 0.25,
        "d_max": 0.35,
        "yaw_tolerance": 15.0,   # 度，匹配时转为弧度
    },
    # 后续多姿势扩展示例（同事完成后取消注释并调参）：
    # {"name": "far_reach",  "d_min": 0.35, "d_max": 0.55, "yaw_tolerance": 15.0},
    # {"name": "close_grip", "d_min": 0.15, "d_max": 0.25, "yaw_tolerance": 10.0},
]


def _normalize_angle(angle: float) -> float:
    """将角度归一化到 [-π, π]"""
    return math.atan2(math.sin(angle), math.cos(angle))


def match_grasp_posture(
    robot_pose: tuple[float, float, float],
    obj_pose_map: tuple[float, float, float],
    postures: list[dict] | None = None,
) -> tuple[str, str | tuple[float, float, float] | None]:
    """
    判断机器人从当前位置能否用 SONIC 搬起物体。

    Args:
        robot_pose: 机器人当前在 map 系下的 (x, y, yaw)，yaw 为弧度。
                    来自 Nav2 /Odometry。
        obj_pose_map: 物体在 map 系下的 (x, y, yaw)，yaw 为弧度。
                      来自 FP + _torso_to_map() 变换。
                      注意：当前 /fp_state 不含物体朝向，yaw 通常为 0.0。
        postures: 可用姿势列表，默认使用 POSTURE_CONFIG。

    Returns:
        ("ok", posture_name)      — 当前距离在某个姿势的可搬起范围内
        ("replan", P_new)         — 距离/朝向不合适，返回修正后的新 approach 点
                                    P_new 为 Nav2 map 系 (x, y, yaw)
        ("not_available", None)   — 物体位姿导致 approach 点越界，无法自动恢复

    Raises:
        ValueError: 如果 postures 为空
    """
    if postures is None:
        postures = POSTURE_CONFIG

    if not postures:
        raise ValueError("postures 配置为空，无法匹配")

    rx, ry, ryaw = robot_pose
    ox, oy, oyaw = obj_pose_map

    d = math.hypot(ox - rx, oy - ry)
    dyaw = abs(_normalize_angle(ryaw - oyaw))

    # 遍历姿势配置，返回第一个匹配的
    for posture in postures:
        d_min = posture["d_min"]
        d_max = posture["d_max"]
        yaw_tol_rad = math.radians(posture["yaw_tolerance"])

        # 浮点容差：避免 d=0.3500000000000001 之类被判为越界
        if d_min - 1e-9 <= d <= d_max + 1e-9 and dyaw <= yaw_tol_rad + 1e-9:
            return ("ok", posture["name"])

    # 无匹配姿势 → 用默认姿势的中点距离重算 approach
    # 默认姿势 = 列表第一个
    default = postures[0]
    ideal_offset = (default["d_min"] + default["d_max"]) / 2.0

    try:
        P_new = compute_approach(obj_pose_map, offset=ideal_offset)
    except ValueError:
        # approach 点越界 → 此物体无法从任何安全位置接近
        return ("not_available", None)

    return ("replan", P_new)
