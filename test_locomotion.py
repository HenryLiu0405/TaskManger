#!/usr/bin/env python3
"""Test script: call locomotion mode services (lift/lay_down/stand/get)."""
import json, sys
import rclpy
from rclpy.node import Node
from std_srvs.srv import SetBool, Trigger


def call_trigger(node: Node, client, name: str) -> dict | None:
    future = client.call_async(Trigger.Request())
    rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)
    if not future.done():
        print(f"  [{name}] TIMEOUT")
        return None
    resp = future.result()
    payload = None
    try:
        payload = json.loads(resp.message)
    except Exception:
        pass
    print(f"  [{name}] success={resp.success}")
    if payload:
        print(f"  [{name}] posture={payload.get('posture_state')}, mode={payload.get('mode')}, has_mode={payload.get('has_mode')}")
    else:
        print(f"  [{name}] message={resp.message}")
    return payload


def call_set_bool(node: Node, client, name: str, value: bool) -> dict | None:
    req = SetBool.Request()
    req.data = value
    future = client.call_async(req)
    rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)
    if not future.done():
        print(f"  [{name}] TIMEOUT")
        return None
    resp = future.result()
    payload = None
    try:
        payload = json.loads(resp.message)
    except Exception:
        pass
    print(f"  [{name}] success={resp.success}")
    if payload:
        print(f"  [{name}] posture={payload.get('posture_state')}, error={payload.get('error')}")
    else:
        print(f"  [{name}] message={resp.message}")
    return payload


def main():
    rclpy.init()
    node = rclpy.create_node("loco_test")

    get_client = node.create_client(Trigger, "/get_locomotion_mode")
    lift_client = node.create_client(SetBool, "/set_lift")
    lay_down_client = node.create_client(SetBool, "/set_lay_down")
    stand_client = node.create_client(Trigger, "/set_stand")

    # Wait for services
    for cli, name in [(get_client, "/get"), (lift_client, "/set_lift"),
                       (lay_down_client, "/set_lay_down"), (stand_client, "/set_stand")]:
        if not cli.wait_for_service(timeout_sec=5.0):
            print(f"WARNING: {name} not available")

    action = sys.argv[1] if len(sys.argv) > 1 else "all"

    if action in ("get", "all"):
        print("\n--- GET ---")
        call_trigger(node, get_client, "get")

    if action in ("lift", "all"):
        print("\n--- SET LIFT ---")
        call_set_bool(node, lift_client, "set_lift", True)

    if action in ("lay_down", "all"):
        print("\n--- SET LAY_DOWN ---")
        call_set_bool(node, lay_down_client, "set_lay_down", True)

    if action in ("stand", "all"):
        print("\n--- SET STAND ---")
        call_trigger(node, stand_client, "set_stand")

    if action == "all":
        print("\n--- GET (final) ---")
        call_trigger(node, get_client, "get")

    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
