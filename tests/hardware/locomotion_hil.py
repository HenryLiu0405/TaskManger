#!/usr/bin/env python3
"""Manually call locomotion services after explicit authorization."""

from __future__ import annotations

import argparse
import json
import os


def _authorize(robot_id: str) -> None:
    if os.getenv("PHI_ALLOW_HARDWARE_TESTS") != "1":
        raise SystemExit("hardware tests disabled: set PHI_ALLOW_HARDWARE_TESTS=1")
    expected = os.getenv("PHI_HARDWARE_ROBOT_ID", "").strip()
    if not expected or robot_id != expected:
        raise SystemExit("robot identity confirmation failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("action", choices=("get", "lift", "lay_down", "stand"))
    args = parser.parse_args()
    _authorize(args.robot_id)

    import rclpy
    from std_srvs.srv import SetBool, Trigger

    rclpy.init()
    node = rclpy.create_node("locomotion_hil_client")
    try:
        service_type, service_name = {
            "get": (Trigger, "/get_locomotion_mode"),
            "lift": (SetBool, "/set_lift"),
            "lay_down": (SetBool, "/set_lay_down"),
            "stand": (Trigger, "/set_stand"),
        }[args.action]
        client = node.create_client(service_type, service_name)
        if not client.wait_for_service(timeout_sec=5.0):
            print(f"ERROR: {service_name} not available")
            return 1
        request = service_type.Request()
        if args.action in {"lift", "lay_down"}:
            request.data = True
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)
        if not future.done() or future.result() is None:
            print("ERROR: timeout or missing response")
            return 1
        response = future.result()
        try:
            detail = json.loads(response.message)
        except Exception:
            detail = response.message
        print(f"{service_name}: success={response.success} detail={detail}")
        return 0 if response.success else 1
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
