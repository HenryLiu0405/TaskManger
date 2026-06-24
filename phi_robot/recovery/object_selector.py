"""
多物体加权打分选择器。

FP 最多同时跟踪 5 个物体。场景中物料区可能有多个箱子，
需要选出"最适合搬的那个"——综合考虑跟踪质量、空间位置和任务目标。

依赖：/fp_state JSON（扁平结构，foundationpose_node.py:297-306）
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
            注意: 不含 orientation — yaw 不可用，返回 0.0。
        expected_slot: 任务目标物料点在 map 系下的 (x, y)
        current_odom: 机器人当前 (x, y, yaw)，保留参数，当前未使用

    Returns:
        {"object_id", "score", "pose_map": (x, y, yaw),
         "tracking_frames", "mask_area_pixels"} 或 None
    """
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

        obj_x = obj.get("x", 0.0)
        obj_y = obj.get("y", 0.0)

        # ── 加权打分 ──
        score = 0.0

        # 因子 1：空间接近度（距预期物料点的距离，权重 50%）
        d_expected = math.hypot(obj_x - expected_slot[0], obj_y - expected_slot[1])
        score += max(0.0, 1.0 - d_expected / 0.50) * 0.50

        # 因子 2：跟踪稳定性（权重 30%）
        score += min(tracking_frames / 100.0, 1.0) * 0.30

        # 因子 3：可见性 — 掩码面积（权重 20%）
        score += min(mask_area / 1000.0, 1.0) * 0.20

        # 因子 4（预留）：姿态质量 — 物体是否直立
        # /fp_state JSON 当前不含 orientation 字段，无法计算。
        # 未来扩展后恢复此因子（建议权重 20%，重新平衡其他权重）。

        candidates.append({
            "object_id": obj.get("id", -1),
            "score": score,
            "obj_x": obj_x,
            "obj_y": obj_y,
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
        # yaw: /fp_state JSON 不含物体朝向，硬编码 0.0。
        # 当前所有 slot yaw=0 不触发问题；post_pick_verify 场景需要完整 6DoF。
        "pose_map": (best["obj_x"], best["obj_y"], 0.0),
        "tracking_frames": best["tracking_frames"],
        "mask_area_pixels": best["mask_area_pixels"],
    }
