"""
恢复编排器。

将前 5 个 recovery 模块串联为完整的重规划决策流程。
纯决策层——所有函数接受 Callable 回调，不直接依赖 ROS。

四个入口函数对应搬箱流程的关键检查点：

   到达 approach 点  →  run_arrival_check()    → pick / scan / replan / escalate
   搬起前             →  pre_pick_confirm()     → (ok, detail) / (fail, reason)
   搬起后             →  post_pick_verify()     → ok / PICK_FAILED + 物体新位姿
   放置后             →  post_place_verify()    → ok / PLACE_FAILED

依赖：
  - replan_gate.wait_fp_stable()        → 到达后等 FP 稳定
  - replan_gate.scan_to_find()          → FP 看不见时旋转扫描
  - replan_gate.should_replan()         → 6 道门控决策
  - object_selector.STATE_TRACKING      → 状态常量
  - coordinate_transform.torso_to_map() → 坐标变换
"""

import math
import time
from typing import Callable, Optional

from .coordinate_transform import torso_to_map
from .object_selector import STATE_TRACKING
from .replan_gate import (
    wait_fp_stable,
    scan_to_find,
    should_replan,
    MIN_STABLE_FRAMES,
    MAX_REPLANS,
)

# 事后验证参数
POST_VERIFY_RADIUS = 0.30    # 在预期位置周围搜索物体的半径（米）
POST_VERIFY_SETTLE_S = 1.0   # 机器人恢复站立后等待 FP 稳定的时间（秒）
POST_VERIFY_MIN_FRAMES = 15  # 事后验证的最低跟踪帧数（低于 should_replan 的 30，
                             # 因为搬起/放置后箱子位姿可能变了，给 FP 重新注册的时间）


def pre_pick_confirm(
    get_fp_state: Callable[[], Optional[dict]],
    target_object_id: int,
) -> tuple[bool, str]:
    """
    搬起前末次 FP 确认——目标物体还在吗？

    在调用 /set_lift 之前执行。如果物体在 Phase 1 和搬起之间
    消失了（被人移动、被碰倒等），这个检查能防止 SONIC 空抓。

    Args:
        get_fp_state: 无参回调，返回解析后的 /fp_state JSON dict 或 None。
        target_object_id: select_target_object() 选出的物体 ID。

    Returns:
        (True, "ok")       — 物体还在，可以搬
        (False, reason)    — 物体消失或不稳定，不应搬
    """
    fp = get_fp_state()
    if fp is None:
        return (False, "FP not available")

    trackers = fp.get("trackers", [])

    # 按 ID 查找目标物体
    target = None
    for obj in trackers:
        if obj.get("id") == target_object_id:
            target = obj
            break

    if target is None:
        return (False, f"target object {target_object_id} disappeared")

    if target.get("state") != STATE_TRACKING:
        return (False, f"target object {target_object_id} not tracking (state={target.get('state')})")

    if target.get("tracking_frames", 0) < MIN_STABLE_FRAMES:
        return (False, f"target object {target_object_id} not stable (frames={target.get('tracking_frames', 0)})")

    return (True, "ok")


def post_pick_verify(
    get_fp_state: Callable[[], Optional[dict]],
    get_odom: Callable[[], tuple[float, float, float]],
    expected_slot_xy: tuple[float, float],
    settle_time_s: float = POST_VERIFY_SETTLE_S,
) -> dict:
    """
    搬起后 FP 事后验证——原来那个位子上箱子还在不在？

    搬起成功 = 地上没箱子了（箱子在机器人手里，离开地面）。
    搬起失败 = 地上还有箱子（可能从手中滑落，或根本没抓起来）。

    验证逻辑：
        1. 等待 settle_time_s 让机器人从弯腰恢复站立，FP 视角恢复
        2. 在 expected_slot_xy 周围 POST_VERIFY_RADIUS (0.30m) 内搜索物体
        3. 找到了 → 搬起失败，返回物体当前位姿供重规划使用
        4. 没找到 → 搬起成功

    Args:
        get_fp_state: 无参回调，返回解析后的 /fp_state JSON dict 或 None。
        get_odom: 无参回调，返回当前里程计 (x, y, yaw)，map 系。
        expected_slot_xy: 预期物料点在 map 系下的 (x, y)。
                          来自 mission_planner 的 slot 坐标经 real_to_nav2 后。
        settle_time_s: 等待机器人恢复站立的时间，默认 1.0 秒。

    Returns:
        {"status": "ok"}
        — 搬起成功，地上没箱子了。

        {"status": "PICK_FAILED",
         "detail": str,
         "current_obj_pose": (x, y, yaw) in map 系,
         "deviation": float}
        — 搬起失败，地上还有箱子。current_obj_pose 可用于重规划。
    """
    time.sleep(settle_time_s)

    odom = get_odom()
    fp = get_fp_state()

    if fp is None:
        return {"status": "PICK_FAILED",
                "detail": "FP not available after pick — cannot verify",
                "current_obj_pose": None,
                "deviation": None}

    # 在预期物料点周围搜索
    nearby = []
    for obj in fp.get("trackers", []):
        if obj.get("state") != STATE_TRACKING:
            continue
        if obj.get("tracking_frames", 0) < POST_VERIFY_MIN_FRAMES:
            continue

        obj_x = obj.get("x", 0.0)
        obj_y = obj.get("y", 0.0)
        mx, my = torso_to_map(obj_x, obj_y, odom)

        d = math.hypot(mx - expected_slot_xy[0], my - expected_slot_xy[1])
        if d < POST_VERIFY_RADIUS:
            nearby.append((d, obj, (mx, my, 0.0)))

    if nearby:
        # 箱子还在原地 → 搬起失败
        nearby.sort(key=lambda x: x[0])
        d, obj, pose_map = nearby[0]
        return {
            "status": "PICK_FAILED",
            "detail": f"物体仍在原位 (偏差 {d:.3f}m)，object_id={obj.get('id')}，搬起失败",
            "current_obj_pose": pose_map,
            "deviation": d,
        }

    # 箱子不在原位 → 搬起成功
    return {"status": "ok"}


def post_place_verify(
    get_fp_state: Callable[[], Optional[dict]],
    get_odom: Callable[[], tuple[float, float, float]],
    expected_grid_xy: tuple[float, float],
    settle_time_s: float = POST_VERIFY_SETTLE_S,
) -> dict:
    """
    放置后 FP 事后验证——目标格子位置上是不是出现了新箱子？

    放置成功 = 目标位置出现物体。
    放置失败 = 目标位置没有物体（可能在携带行走中掉落）。

    验证逻辑：
        1. 等待 settle_time_s 让机器人恢复站立
        2. 在 expected_grid_xy 周围 POST_VERIFY_RADIUS (0.30m) 内搜索物体
        3. 找到了 → 放置成功
        4. 没找到 → 放置失败（箱子可能掉了）

    Args:
        get_fp_state: 无参回调，返回解析后的 /fp_state JSON dict 或 None。
        get_odom: 无参回调，返回当前里程计 (x, y, yaw)，map 系。
        expected_grid_xy: 预期放置点在 map 系下的 (x, y)。
                          来自 mission_planner 的 grid 坐标经 real_to_nav2 后。
        settle_time_s: 等待机器人恢复站立的时间，默认 1.0 秒。

    Returns:
        {"status": "ok",
         "detail": str,
         "placed_pose": (x, y, yaw) in map 系}
        — 放置成功。

        {"status": "PLACE_FAILED",
         "detail": str}
        — 放置失败，目标位置无物体。调用方应启动 scan_to_find 全场搜索。
    """
    time.sleep(settle_time_s)

    odom = get_odom()
    fp = get_fp_state()

    if fp is None:
        return {"status": "PLACE_FAILED",
                "detail": "FP not available after place — cannot verify"}

    for obj in fp.get("trackers", []):
        if obj.get("state") != STATE_TRACKING:
            continue
        if obj.get("tracking_frames", 0) < POST_VERIFY_MIN_FRAMES:
            continue

        obj_x = obj.get("x", 0.0)
        obj_y = obj.get("y", 0.0)
        mx, my = torso_to_map(obj_x, obj_y, odom)

        d = math.hypot(mx - expected_grid_xy[0], my - expected_grid_xy[1])
        if d < POST_VERIFY_RADIUS:
            return {
                "status": "ok",
                "detail": f"箱子已放置在 ({mx:.2f}, {my:.2f})，偏差 {d:.3f}m",
                "placed_pose": (mx, my, 0.0),
            }

    return {"status": "PLACE_FAILED",
            "detail": "放置后目标位置无物体——箱子可能在携带过程中掉落"}


def run_arrival_check(
    get_fp_state: Callable[[], Optional[dict]],
    get_odom: Callable[[], tuple[float, float, float]],
    rotate_to_relative: Callable[[float], None],
    expected_obj_pose: tuple[float, float, float],
    replan_count: int = 0,
    wait_timeout_s: float = 3.0,
) -> dict:
    """
    到达 approach 点后的完整检测流程。

    编排 wait_fp_stable → scan_to_find → should_replan 三个步骤，
    返回下一步动作。

    流程：
        1. wait_fp_stable(timeout_s) — 等 FP 稳定输出
           ├─ 超时 → {"action": "scan", "reason": "FP 超时无稳定物体"}
           └─ 拿到物体 → 继续

        2. should_replan(fp_state, expected_obj_pose, current_odom, replan_count)
           ├─ should=False → {"action": "pre_pick", "target": obj}
           │   （偏差在容差内 或 可从当前位置搬起）
           ├─ should=True  → {"action": "replan", "new_approach": P, "reason": ...}
           └─ Gate 全部不通过（但非 should）→ 检查原因：
               - "FP no tracking" → {"action": "scan", ...}
               - "budget exhausted" → {"action": "escalate", "level": "L2", ...}
               - "out of map bounds" → {"action": "escalate", "level": "L3", ...}
               - 其他 → {"action": "wait", "reason": ...}

    Args:
        get_fp_state: 无参回调，返回解析后的 /fp_state JSON dict 或 None。
        get_odom: 无参回调，返回当前里程计 (x, y, yaw)，map 系。
        rotate_to_relative: 回调，从扫描起始朝向算起的绝对偏移角度（度）。
                           传给 scan_to_find()。
        expected_obj_pose: 预期物体在 map 系下的 (x, y, yaw)。
        replan_count: 当前已重规划次数（0-based）。
        wait_timeout_s: wait_fp_stable 的超时秒数，默认 3.0。

    Returns:
        {"action": "pre_pick", "target": obj_dict}
        — 偏差在容差内 或 可从当前位置搬起，可以进入搬起流程。

        {"action": "replan", "new_approach": (x, y, yaw), "reason": str}
        — 需要重规划。调用方用 new_approach 调 Nav2 重导航。

        {"action": "scan", "reason": str}
        — FP 看不见物体或超时，需要旋转扫描。调用方调 scan_to_find。

        {"action": "escalate", "level": "L2"|"L3", "reason": str}
        — 升级：L2=重规划预算耗尽，L3=物体不可达。

        {"action": "wait", "reason": str}
        — FP 数据暂时不可信，稍后重试。
    """
    # Step 1: 等 FP 稳定
    current_odom = get_odom()
    stable_obj = wait_fp_stable(get_fp_state, timeout_s=wait_timeout_s)

    if stable_obj is None:
        # 超时 → 检查是否存在 TRACKING 物体（即使不稳定）
        fp = get_fp_state()
        if fp:
            tracking_objs = [o for o in fp.get("trackers", [])
                           if o.get("state") == STATE_TRACKING]
            if tracking_objs:
                # 有物体但跟踪不稳定 → 稍后重试（给 FP 更多时间）
                best_frames = max(o.get("tracking_frames", 0) for o in tracking_objs)
                return {"action": "wait",
                        "reason": f"FP 有 {len(tracking_objs)} 个物体但未稳定 "
                                  f"(max frames={best_frames}/{MIN_STABLE_FRAMES})"}
        # 完全没物体 → 旋转扫描
        return {"action": "scan",
                "reason": f"FP 在 {wait_timeout_s:.0f}s 内无稳定 TRACKING 物体"}

    # 拿到稳定物体 → 读最新 fp_state 传给 should_replan
    fp = get_fp_state()
    if fp is None:
        return {"action": "scan", "reason": "FP state unavailable after wait_fp_stable"}

    # Step 2: 门控决策
    should, reason, new_approach = should_replan(
        fp, expected_obj_pose, current_odom, replan_count,
    )

    if should:
        assert new_approach is not None
        return {
            "action": "replan",
            "new_approach": new_approach,
            "reason": reason,
        }

    # should=False → 分析原因，决定下一步
    if "no tracking" in reason:
        return {"action": "scan", "reason": reason}

    if "budget exhausted" in reason:
        return {"action": "escalate", "level": "L2", "reason": reason}

    if "out of map bounds" in reason:
        return {"action": "escalate", "level": "L3", "reason": reason}

    if "not stable" in reason or "occluded" in reason or "no qualified" in reason:
        return {"action": "wait", "reason": reason}

    # 偏差在容差内 或 可从当前位置搬起 → 可以搬
    return {"action": "pre_pick", "target": stable_obj}
