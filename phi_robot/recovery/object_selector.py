"""
多物体加权打分选择器。

FP 最多同时跟踪 5 个物体。场景中物料区可能有多个箱子，
需要选出"最适合搬的那个"——综合考虑跟踪质量、空间位置和任务目标。
"""

import math
from typing import Optional

# FP 状态常量（与 FoundationPose /fp_state JSON 一致）
STATE_LOST = 0
STATE_PAUSED = 1
STATE_TRACKING = 2

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
        objects: FP /fp_state 中的 trackers 列表，每项含:
            object_id, state, tracking_frames, mask_area_pixels,
            pose: {position: {x, y, z}, orientation: {w, x, y, z}}
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

        mask_area = obj.get("mask_area_pixels", 0)
        if mask_area < MIN_MASK_AREA:
            continue

        pose = obj.get("pose", {})
        pos = pose.get("position", {})
        orient = pose.get("orientation", {})

        obj_x = pos.get("x", 0.0)
        obj_y = pos.get("y", 0.0)

        # ── 加权打分 ──
        score = 0.0

        # 因子 1：空间接近度（距预期物料点的距离，权重 40%）
        d_expected = math.hypot(obj_x - expected_slot[0], obj_y - expected_slot[1])
        score += max(0.0, 1.0 - d_expected / 0.50) * 0.40

        # 因子 2：跟踪稳定性（权重 25%）
        score += min(tracking_frames / 100.0, 1.0) * 0.25

        # 因子 3：可见性 — 掩码面积（权重 15%）
        score += min(mask_area / 1000.0, 1.0) * 0.15

        # 因子 4：姿态质量 — 物体是否直立（权重 20%）
        # w = cos(θ/2)，对直立物体 θ≈0 → w≈1 → upright≈1
        w = max(-1.0, min(1.0, orient.get("w", 1.0)))
        upright = abs(2.0 * w * w - 1.0)
        score += upright * 0.20

        candidates.append({
            "object_id": obj.get("object_id", -1),
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
        "pose_map": (best["obj_x"], best["obj_y"], 0.0),
        "tracking_frames": best["tracking_frames"],
        "mask_area_pixels": best["mask_area_pixels"],
    }
