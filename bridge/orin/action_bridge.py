#!/usr/bin/env python3
"""Bridge: /goal_pose topic → /navigate_to_pose Action"""
import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose


class ActionBridge(Node):
    def __init__(self):
        super().__init__("action_bridge")
        self._action_client = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self._sub = self.create_subscription(
            PoseStamped, "/goal_pose", self._on_goal, 10)
        self.get_logger().info("ActionBridge: /goal_pose → /navigate_to_pose")

    def _on_goal(self, msg: PoseStamped):
        if not self._action_client.wait_for_server(timeout_sec=2.0):
            self.get_logger().error("Action server /navigate_to_pose not available")
            return
        goal = NavigateToPose.Goal()
        goal.pose = msg
        self._action_client.send_goal_async(goal)
        self.get_logger().info(
            f"Sent goal: ({msg.pose.position.x:.2f}, {msg.pose.position.y:.2f})"
        )


def main():
    rclpy.init()
    rclpy.spin(ActionBridge())


if __name__ == "__main__":
    main()
