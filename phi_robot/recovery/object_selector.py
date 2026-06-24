"""
多物体加权打分选择器。

FP 最多同时跟踪 5 个物体。场景中物料区可能有多个箱子，
需要选出"最适合搬的那个"——综合考虑跟踪质量、空间位置和任务目标。

依赖：/fp_state JSON（扁平结构，foundationpose_node.py:297-306）
坐标系：FP 输出的 x, y 在 torso_link 系下，需用 current_odom 转到 map 系后
        才能和 expected_slot（map 系）比较距离。
注意：当前 /fp_state 不含物体朝向（无 quaternion），yaw 返回 0.0。
      未来需扩展 /fp_state 或订阅 /foundationpose/pose_result 获取完整 6DoF 位姿。
"""

import math
from typing import Optional

# FP 状态常量 — 与 state_machine.py:343 BlobTracker 一致
#   IDLE, TRACKING, LOST = 0, 1, 2
STATE_IDLE = 0
STATE_TRACKING = 1
STATE_LOST = 2

# 硬过滤阈值
MIN_TRACKING_FRAMES = 30  # 稳定跟踪最少 1 秒 @ 30fps
MIN_MASK_AREA = 200       # 640×480 下直径 0.3m 圆柱在 2m 外的投影面积


def _torso_to_map(
    obj_x: float, obj_y: float, odom: tuple[float, float, float],
) -> tuple[float, float]:
    """torso_link 系物体坐标 → map 系（2D 旋转 + 平移，忽略 torso→base 小偏移）"""
    rx, ry, ryaw = odom
    mx = rx + obj_x * math.cos(ryaw) - obj_y * math.sin(ryaw)
    my = ry + obj_x * math.sin(ryaw) + obj_y * math.cos(ryaw)
    return (mx, my)


def select_target_object(
    objects: list[dict],
    expected_slot: tuple[float, float],
    current_odom: tuple[float, float, float] | None = None,
) -> Optional[dict]:
    """
    从 FP 检测到的多个物体中选出最优搬起目标。

    Args:
        objects: FP /fp_state 中的 trackers 列表。每项为扁平结构:
            {"id": int, "state": int, "x": float, "y": float, "z": float,
             "mask_area": int, "tracking_frames": int}
            注意: x, y 在 torso_link 系下；不含 orientation。
        expected_slot: 任务目标物料点在 map 系下的 (x, y)
        current_odom: 机器人当前 (x, y, yaw) 在 map 系下（来自 Nav2 /Odometry）。
                      用于将物体坐标从 torso_link 转到 map 系。
                      None 时跳过空间接近度因子。

    Returns:
        {"object_id", "score", "pose_map": (x, y, yaw) in map 系,
         "tracking_frames", "mask_area_pixels"} 或 None
    """
    has_odom = current_odom is not None

    candidates = []

    for obj in objects:
        # ── 硬过滤 ──
        state = obj.get("state", -1)
        if state != STATE_TRACKING:
            continue

        tracking_frames = obj.get("tracking_frames", 0)
        if tracking_frames < MIN_TRACKING_FRAMES:
            continue

        mask_area = obj.get("mask_area", 0)
        if mask_area < MIN_MASK_AREA:
            continue

        obj_x_torso = obj.get("x", 0.0)
        obj_y_torso = obj.get("y", 0.0)

        # ── 坐标变换：torso_link → map ──
        if has_odom:
            obj_x_map, obj_y_map = _torso_to_map(obj_x_torso, obj_y_torso, current_odom)
        else:
            obj_x_map, obj_y_map = obj_x_torso, obj_y_torso

        # ── 加权打分 ──
        score = 0.0

        if has_odom:
            # 因子 1：空间接近度（距预期物料点的距离，权重 50%）
            d_expected = math.hypot(
                obj_x_map - expected_slot[0], obj_y_map - expected_slot[1]
            )
            score += max(0.0, 1.0 - d_expected / 0.50) * 0.50
            # 因子 2：跟踪稳定性（权重 30%）
            score += min(tracking_frames / 100.0, 1.0) * 0.30
            # 因子 3：可见性 — 掩码面积（权重 20%）
            score += min(mask_area / 1000.0, 1.0) * 0.20
        else:
            # 无 odometry → 跳过空间接近度，权重重新分配
            # 因子 2：跟踪稳定性（权重 60%）
            score += min(tracking_frames / 100.0, 1.0) * 0.60
            # 因子 3：可见性（权重 40%）
            score += min(mask_area / 1000.0, 1.0) * 0.40

        # 因子 4（预留）：姿态质量 — 物体是否直立
        # /fp_state JSON 当前不含 orientation 字段，无法计算。
        # 未来扩展后恢复此因子（建议权重 20%，重新平衡其他权重）。

        candidates.append({
            "object_id": obj.get("id", -1),
            "score": score,
            "obj_x_map": obj_x_map,
            "obj_y_map": obj_y_map,
            "tracking_frames": tracking_frames,
            "mask_area_pixels": mask_area,
        })

    if not candidates:
        return None

    # 取最高分
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    return {
        "object_id": best["object_id"],
        "score": round(best["score"], 4),
        # pose_map 返回 map 系坐标（如有 odom 则已转换）
        # yaw: /fp_state JSON 不含物体朝向，硬编码 0.0
        "pose_map": (best["obj_x_map"], best["obj_y_map"], 0.0),
        "tracking_frames": best["tracking_frames"],
        "mask_area_pixels": best["mask_area_pixels"],
    }
