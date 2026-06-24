"""
从物体位姿计算机器人接近位姿（approach point）。

机器人停在物体前方 offset 米处，朝向物体，
为 SONIC 搬起/放下动作留出操作距离。
"""

import math

# Nav2 地图边界（由四个角点标定确定）
MAP_X_MIN = -0.5
MAP_X_MAX = 5.0
MAP_Y_MIN = -2.5
MAP_Y_MAX = 1.0


def compute_approach(
    obj_nav2: tuple[float, float, float],
    offset: float = 0.25,
) -> tuple[float, float, float]:
    """
    从物体在 Nav2 map 系下的位姿，计算机器人接近位姿。

    Args:
        obj_nav2: 物体在 Nav2 map 系下的 (x, y, yaw)，yaw 为弧度
        offset: 机器人停在物体前方的距离（米），默认 0.25

    Returns:
        (app_x, app_y, app_yaw) — Nav2 map 系下的接近位姿

    Raises:
        ValueError: 如果接近点超出地图边界
    """
    ox, oy, oyaw = obj_nav2

    app_x = ox - offset * math.cos(oyaw)
    app_y = oy - offset * math.sin(oyaw)
    app_yaw = oyaw

    if not (MAP_X_MIN <= app_x <= MAP_X_MAX):
        raise ValueError(
            f"approach x={app_x:.2f} out of map bounds [{MAP_X_MIN}, {MAP_X_MAX}]"
        )
    if not (MAP_Y_MIN <= app_y <= MAP_Y_MAX):
        raise ValueError(
            f"approach y={app_y:.2f} out of map bounds [{MAP_Y_MIN}, {MAP_Y_MAX}]"
        )

    return (app_x, app_y, app_yaw)
