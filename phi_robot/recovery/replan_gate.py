"""
智能门控与旋转扫描。

决定"要不要重规划"——不是每次 FP 更新都触发，先过 6 道门控。
旋转扫描解决"FP 只有 ~55.6° HFOV，到达后可能看不见箱子"的问题。

依赖：
  - object_selector.select_target_object() → Gate 2 选最优物体 + 偏差计算
  - grasp_posture.match_grasp_posture()     → Gate 3 判断 SONIC 能否搬起
  - approach_calculator.compute_approach()  → Gate 3 内部调用（边界校验）
"""

import math
import time
from typing import Callable, Optional

from .coordinate_transform import torso_to_map
from .object_selector import (
    select_target_object,
    STATE_TRACKING,
)
from .grasp_posture import match_grasp_posture

# ── 阈值常量 ──

# FP 稳定检测
MIN_STABLE_FRAMES = 30        # 最少连续跟踪帧数（1 秒 @ 30fps）
MIN_MASK_AREA = 200           # 最小掩码面积（640×480 下 ≈ 直径 0.3m 圆柱在 2m 外）

# 重规划触发
MIN_DEVIATION = 0.10          # 物体偏差超过此值才考虑重规划（米）
MIN_APPROACH_DELTA = 0.05     # 新 approach 和当前位置差小于此值则跳过（米）
MAX_REPLANS = 2               # 单任务最大重规划次数

# 旋转扫描
SCAN_ANGLES = [-30, -20, -10, 0, 10, 20, 30]  # 相对角度（度）
SCAN_WAIT_FRAMES = 5                            # 每步等待帧数
SCAN_FRAME_PERIOD = 1.0 / 30.0                  # 30fps 帧周期

# ── should_replan 返回 reason_code 常量 ──
# run_arrival_check 用这些常量做路由，不依赖 reason 字符串内容
REASON_NO_TRACKING = "NO_TRACKING"            # Gate 0: 无 TRACKING 物体
REASON_NOT_STABLE = "NOT_STABLE"              # Gate 1: tracking_frames 不足
REASON_OCCLUDED = "OCCLUDED"                  # Gate 1: 所有物体 mask_area 不足
REASON_NO_QUALIFIED = "NO_QUALIFIED"          # Gate 2: select_target_object 无合格候选
REASON_WITHIN_TOLERANCE = "WITHIN_TOLERANCE"  # Gate 2: 偏差在容差内
REASON_GRASPABLE = "GRASPABLE"                # Gate 3: 可从当前位置搬起
REASON_OUT_OF_BOUNDS = "OUT_OF_BOUNDS"        # Gate 3: approach 点越界
REASON_SAME_POSITION = "SAME_POSITION"        # Gate 4: 新 approach 与当前位置几乎相同
REASON_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"  # Gate 5: 重规划预算耗尽
REASON_NEEDS_REPLAN = "NEEDS_REPLAN"          # 全部通过，需要重规划


def wait_fp_stable(
    get_fp_state: Callable[[], Optional[dict]],
    timeout_s: float = 3.0,
) -> Optional[dict]:
    """
    等待 FP 给出稳定输出，用于到达 approach 点后的初始检测。

    Args:
        get_fp_state: 无参回调，返回解析后的 /fp_state JSON dict，
                      或 None（topic 尚无数据）。
        timeout_s: 超时秒数，默认 3.0。

    Returns:
        第一个满足硬过滤条件的物体 dict（FP 原始扁平结构：
        {"id", "state", "x", "y", "z", "mask_area", "tracking_frames"}），
        超时返回 None。

        硬过滤条件：
        - state == STATE_TRACKING (1)
        - tracking_frames >= 30
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        fp = get_fp_state()
        if fp is None:
            time.sleep(0.1)
            continue

        trackers = fp.get("trackers", [])
        for obj in trackers:
            if (
                obj.get("state") == STATE_TRACKING
                and obj.get("tracking_frames", 0) >= MIN_STABLE_FRAMES
            ):
                return obj

        time.sleep(0.1)

    return None


def scan_to_find(
    get_fp_state: Callable[[], Optional[dict]],
    get_odom: Callable[[], tuple[float, float, float]],
    rotate_to_relative: Callable[[float], None],
    target_object_id: int | None = None,
) -> tuple[str, Optional[tuple[float, float, float]]]:
    """
    旋转扫描找回 FP 视野外的物体。

    相机 HFOV ≈ 55.6°。扫描 ±30°（7 个角度，步长 10°），
    相邻角度有重叠，总覆盖 ≈ 115° 前向半球。

    扫描序列：[0°, -30°, -20°, -10°, +10°, +20°, +30°]
    每个角度是**从扫描起始朝向算起的绝对偏移量**，不是增量。
    即 rotate_to_relative(-30°) 表示"转到起始朝向的 -30° 位置"，
    而非"从当前位置再转 -30°"。

    每步流程：
        1. rotate_to_relative(angle_deg) — 转到从起始朝向算起的绝对偏移角度
        2. 等待 5 帧让 FP 稳定
        3. 读 FP state + 最新 odometry
        4. 检查是否有 TRACKING 物体

    Args:
        get_fp_state: 无参回调，返回解析后的 /fp_state JSON dict 或 None。
        get_odom: 无参回调，返回当前里程计 (x, y, yaw)，yaw 为弧度，map 系。
                  每步重新读取以保证 torso→map 变换使用最新的 yaw。
        rotate_to_relative: 回调，参数为从扫描起始朝向算起的绝对偏移角度（度）。
                           正值 = 左转，负值 = 右转。阻塞直到旋转完成。
                           实现方需记录扫描起始 yaw，每次传入偏移量后计算目标 yaw
                           = start_yaw + radians(angle_deg)，再通过 /cmd_vel 旋转到位。
        target_object_id: 可选，指定要查找的物体 ID。None 时返回第一个找到的物体。

    Returns:
        ("found", (mx, my, 0.0))  — 找到物体，返回 map 系位姿（yaw 固定 0.0）
        ("not_found", None)        — 全部 7 个角度扫描完仍未找到

    注意：找到的物体只做基础校验（state==TRACKING, frames>=10），
          不做 select_target_object 加权评分。调用方拿到位姿后自行评分。
    """
    scan_angles = [0] + [a for a in SCAN_ANGLES if a != 0]

    for angle_deg in scan_angles:
        if angle_deg != 0:
            rotate_to_relative(angle_deg)

        # 等待 FP 稳定（5 帧 @ 30fps）
        time.sleep(SCAN_WAIT_FRAMES * SCAN_FRAME_PERIOD)

        fp = get_fp_state()
        if fp is None:
            continue

        # 读取最新 odometry（yaw 在旋转后已改变，torso→map 变换依赖 ryaw）
        current_odom = get_odom()

        for obj in fp.get("trackers", []):
            # 基础校验：TRACKING 且至少 10 帧（不要闪烁检测）
            if obj.get("state") != STATE_TRACKING:
                continue
            if obj.get("tracking_frames", 0) < 10:
                continue
            if target_object_id is not None and obj.get("id") != target_object_id:
                continue

            obj_x = obj.get("x", 0.0)
            obj_y = obj.get("y", 0.0)
            mx, my = torso_to_map(obj_x, obj_y, current_odom)
            return ("found", (mx, my, 0.0))

    return ("not_found", None)


def should_replan(
    fp_state: dict,
    expected_obj_pose: tuple[float, float, float],
    current_odom: tuple[float, float, float],
    replan_count: int = 0,
) -> dict:
    """
    智能门控：判断是否需要触发重规划。

    不是每次 FP 更新都触发重规划——先过 6 道门控，
    只有全部通过才返回 should=True。

    Gate 0: FP 有没有物体在 TRACKING？
    Gate 1: FP 数据可信？（tracking_frames >= 30, mask_area >= 200）
    Gate 2: 选出最优物体后，偏差 > 0.10m？（调用 select_target_object）
    Gate 3: SONIC 能从当前位置搬吗？（调用 match_grasp_posture）
    Gate 4: 新 approach 和当前位置有区别？（避免原地踏步，差值 < 0.05m 跳过）
    Gate 5: 重规划预算还有？（replan_count < 2）

    Args:
        fp_state: 解析后的 /fp_state JSON dict（含 "trackers" 列表）。
        expected_obj_pose: 预期物体在 map 系下的 (x, y, yaw)。
                           来自 mission_planner 的 slot/grid 坐标经 real_to_nav2 后。
        current_odom: 机器人当前里程计 (x, y, yaw)，map 系，来自 Nav2 /Odometry。
        replan_count: 当前已重规划次数（0-based）。首次调用传 0。

    Returns:
        {
            "should": bool,
            "reason_code": str,        # REASON_* 常量，调用方用于路由
            "reason": str,             # 人类可读的详细原因
            "new_approach": (x,y,yaw) | None,
            "selected": dict | None,   # select_target_object 的返回值（含
                                       #   object_id, score, pose_map 等）
        }
    """
    trackers = fp_state.get("trackers", [])

    # ═══════════════════════════════════════════════════════════════
    # Gate 0: 有没有物体在跟踪？
    # ═══════════════════════════════════════════════════════════════
    tracking_objs = [o for o in trackers if o.get("state") == STATE_TRACKING]
    if not tracking_objs:
        return {
            "should": False,
            "reason_code": REASON_NO_TRACKING,
            "reason": "FP no tracking objects",
            "new_approach": None,
            "selected": None,
        }

    # ═══════════════════════════════════════════════════════════════
    # Gate 1: FP 数据可信？
    # ═══════════════════════════════════════════════════════════════
    # frames 检查：取跟踪帧数最高的物体，代表 FP 整体稳定程度
    best_by_frames = max(tracking_objs, key=lambda o: o.get("tracking_frames", 0))

    if best_by_frames.get("tracking_frames", 0) < MIN_STABLE_FRAMES:
        return {
            "should": False,
            "reason_code": REASON_NOT_STABLE,
            "reason": f"FP not stable yet "
                      f"(frames={best_by_frames.get('tracking_frames', 0)})",
            "new_approach": None,
            "selected": None,
        }

    # mask 检查：个体属性——只要存在任一 TRACKING 物体 mask 达标就放行
    if not any(o.get("mask_area", 0) >= MIN_MASK_AREA for o in tracking_objs):
        return {
            "should": False,
            "reason_code": REASON_OCCLUDED,
            "reason": "all objects too far or occluded",
            "new_approach": None,
            "selected": None,
        }

    # ═══════════════════════════════════════════════════════════════
    # Gate 2: 选出最优物体，检查位置偏差
    # ═══════════════════════════════════════════════════════════════
    expected_slot = (expected_obj_pose[0], expected_obj_pose[1])
    selected = select_target_object(trackers, expected_slot, current_odom)

    if selected is None:
        return {
            "should": False,
            "reason_code": REASON_NO_QUALIFIED,
            "reason": "no qualified target after scoring",
            "new_approach": None,
            "selected": None,
        }

    dev = math.hypot(
        selected["pose_map"][0] - expected_slot[0],
        selected["pose_map"][1] - expected_slot[1],
    )

    if dev < MIN_DEVIATION:
        return {
            "should": False,
            "reason_code": REASON_WITHIN_TOLERANCE,
            "reason": f"within tolerance (dev={dev:.3f}m)",
            "new_approach": None,
            "selected": selected,
        }

    # ═══════════════════════════════════════════════════════════════
    # Gate 3: SONIC 能从当前位置搬吗？
    # ═══════════════════════════════════════════════════════════════
    obj_pose_map = selected["pose_map"]
    can_grasp, detail = match_grasp_posture(current_odom, obj_pose_map)

    if can_grasp == "ok":
        return {
            "should": False,
            "reason_code": REASON_GRASPABLE,
            "reason": f"graspable from current position (posture={detail})",
            "new_approach": None,
            "selected": selected,
        }

    if can_grasp == "not_available":
        return {
            "should": False,
            "reason_code": REASON_OUT_OF_BOUNDS,
            "reason": "approach point out of map bounds",
            "new_approach": None,
            "selected": selected,
        }

    # can_grasp == "replan" — detail 是修正后的 approach 点
    assert isinstance(detail, tuple), f"expected tuple, got {type(detail)}"
    P_new: tuple[float, float, float] = detail

    # ═══════════════════════════════════════════════════════════════
    # Gate 4: 新 approach 和当前位置有区别？（避免原地踏步）
    # ═══════════════════════════════════════════════════════════════
    delta = math.hypot(
        P_new[0] - current_odom[0],
        P_new[1] - current_odom[1],
    )

    if delta < MIN_APPROACH_DELTA:
        return {
            "should": False,
            "reason_code": REASON_SAME_POSITION,
            "reason": f"approach same as current (delta={delta:.3f}m)",
            "new_approach": P_new,
            "selected": selected,
        }

    # ═══════════════════════════════════════════════════════════════
    # Gate 5: 重规划预算还有？
    # ═══════════════════════════════════════════════════════════════
    if replan_count >= MAX_REPLANS:
        return {
            "should": False,
            "reason_code": REASON_BUDGET_EXHAUSTED,
            "reason": "replan budget exhausted",
            "new_approach": None,
            "selected": selected,
        }

    # 全部通过 → 触发重规划
    return {
        "should": True,
        "reason_code": REASON_NEEDS_REPLAN,
        "reason": f"deviation {dev:.3f}m > {MIN_DEVIATION}m, "
                  f"replan #{replan_count + 1}",
        "new_approach": P_new,
        "selected": selected,
    }
