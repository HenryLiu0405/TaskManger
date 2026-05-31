#!/usr/bin/env python3
"""Test script: call /start_navigation with a full 100-entry cmd_vel trajectory."""
import json, math, sys
import rclpy
from robot_interfaces.srv import ExecuteTrajectory

rclpy.init()
node = rclpy.create_node("test_client")
client = node.create_client(ExecuteTrajectory, "/start_navigation")

if not client.wait_for_service(timeout_sec=5.0):
    print("ERROR: /start_navigation not available")
    sys.exit(1)

# Build 100-entry cmd_vel trajectory toward target
tx, ty = float(sys.argv[1]) if len(sys.argv) > 1 else 1.5, float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
dist = math.hypot(tx, ty)
speed = 0.3
vx = (tx / dist) * speed if dist > 0.01 else speed
vy = (ty / dist) * speed if dist > 0.01 else 0.0

cmd_vel = [{"index": i, "linear": {"x": vx, "y": vy}, "angular": {"z": 0.0}, "locomotion_mode": 1} for i in range(100)]
payload = {"source": "phi_robot_test", "rate_hz_recommended": 20.0, "cmd_vel": cmd_vel}

req = ExecuteTrajectory.Request()
req.trajectory_json = json.dumps(payload)

print(f"Calling /start_navigation with target ({tx}, {ty}), {len(cmd_vel)} cmd_vel entries...")
future = client.call_async(req)
rclpy.spin_until_future_complete(node, future, timeout_sec=30.0)

if future.done():
    result = future.result()
    print(f"Response: success={result.success}")
else:
    print("ERROR: timeout")

node.destroy_node()
rclpy.shutdown()
