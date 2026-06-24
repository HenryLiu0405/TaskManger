"""
坐标变换模块。

提供两种坐标变换：
  1. real_to_nav2  — 真实场地坐标系 → Nav2 map 坐标系（仿射变换）
  2. torso_to_map  — torso_link 系 → map 系（2D 旋转 + 平移）

真实            →   Nav2 map
(0, 3)   左上   →   (-0.26, -2.50)
(0, 0)   左下   →   (-0.48, 0.416)
(5, 3)   右上   →   (4.73, -2.12)
(5, 0)   右下   →   (4.51, 0.79)
"""

import math


def real_to_nav2(x: float, y: float) -> tuple[float, float]:
    """真实场地坐标 (米) → Nav2 map 坐标 (米)"""
    nav2_x = 0.998 * x + 0.0733 * y - 0.48
    nav2_y = 0.0748 * x - 0.972 * y + 0.416
    return (nav2_x, nav2_y)


def torso_to_map(
    obj_x: float, obj_y: float, odom: tuple[float, float, float],
) -> tuple[float, float]:
    """
    torso_link 系物体坐标 → map 系。

    用里程计 yaw 做 2D 旋转 + 平移。忽略 torso→base_link 的小偏移（~0.42m），
    该偏移对搬起距离判断（阈值 0.05m 级）不产生实质影响。

    Args:
        obj_x: 物体在 torso_link 系下的 x 坐标（米），来自 FP /fp_state。
        obj_y: 物体在 torso_link 系下的 y 坐标（米），来自 FP /fp_state。
        odom: 机器人当前里程计 (x, y, yaw)，yaw 为弧度，map 系。

    Returns:
        (mx, my) — 物体在 map 系下的坐标。
    """
    rx, ry, ryaw = odom
    mx = rx + obj_x * math.cos(ryaw) - obj_y * math.sin(ryaw)
    my = ry + obj_x * math.sin(ryaw) + obj_y * math.cos(ryaw)
    return (mx, my)
