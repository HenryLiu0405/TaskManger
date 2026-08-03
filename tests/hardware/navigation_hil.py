#!/usr/bin/env python3
"""Manually call /start_navigation after explicit two-factor authorization."""

from __future__ import annotations

import argparse
import json
import math
import os
import time


def _authorize(robot_id: str) -> None:
    if os.getenv("PHI_ALLOW_HARDWARE_TESTS") != "1":
        raise SystemExit("hardware tests disabled: set PHI_ALLOW_HARDWARE_TESTS=1")
    expected = os.getenv("PHI_HARDWARE_ROBOT_ID", "").strip()
    if not expected or robot_id != expected:
        raise SystemExit("robot identity confirmation failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("x", type=float)
    parser.add_argument("y", type=float)
    parser.add_argument("--theta", type=float, default=math.pi / 2)
    args = parser.parse_args()
    _authorize(args.robot_id)

    import rclpy
    from robot_interfaces.srv import ExecuteTrajectory
    from std_msgs.msg import Bool

    rclpy.init()
    node = rclpy.create_node("navigation_hil_client")
    try:
        nav_state = {
            "request_sent_at": 0.0,
            "saw_false": False,
            "fresh_reached": False,
        }

        def _on_nav_reached(message: Bool) -> None:
            if not message.data and nav_state["request_sent_at"] > 0.0:
                nav_state["saw_false"] = True
            elif nav_state["saw_false"]:
                nav_state["fresh_reached"] = True

        node.create_subscription(Bool, "/nav_reached", _on_nav_reached, 10)
        client = node.create_client(ExecuteTrajectory, "/start_navigation")
        if not client.wait_for_service(timeout_sec=5.0):
            print("ERROR: /start_navigation not available")
            return 1

        request = ExecuteTrajectory.Request()
        request.trajectory_json = json.dumps({
            "target_x": args.x,
            "target_y": args.y,
            "yaw": args.theta,
            "walk": True,
        })
        print(f"Calling /start_navigation for approved robot {args.robot_id}...")
        nav_state["request_sent_at"] = time.monotonic()
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=30.0)
        if not future.done() or future.result() is None:
            print("ERROR: timeout or missing response")
            return 1
        response = future.result()
        print(f"Response: success={response.success} message={response.message}")
        if not response.success:
            return 1

        deadline = time.monotonic() + 600.0
        while not nav_state["fresh_reached"] and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.2)
        if not nav_state["fresh_reached"]:
            print("ERROR: no fresh false→true /nav_reached sequence within 600s")
            return 1
        print("Navigation completion confirmed by fresh /nav_reached=true")
        return 0
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
