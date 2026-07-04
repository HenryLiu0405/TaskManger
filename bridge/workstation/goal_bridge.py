#!/usr/bin/env python3
"""监听 /start_navigation → 提取坐标 → 发布 /goal_pose → 等 /navigation_complete 才返回"""
import json, math, threading, rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from geometry_msgs.msg import PoseStamped
from std_msgs.msg import Bool
from robot_interfaces.srv import ExecuteTrajectory


class GoalBridge(Node):
    def __init__(self):
        super().__init__('goal_bridge')
        self.srv = self.create_service(
            ExecuteTrajectory, '/start_navigation', self.callback)
        self.pub = self.create_publisher(PoseStamped, '/goal_pose', 10)
        self._complete = threading.Event()
        self.create_subscription(
            Bool, '/navigation_complete', self._on_navigation_complete, 10)
        self.get_logger().info('GoalBridge: /start_navigation → /goal_pose, waits for /navigation_complete')

    def _on_navigation_complete(self, msg: Bool) -> None:
        if msg.data:
            self._complete.set()
            self.get_logger().info('navigation_complete received, releasing service response')

    def callback(self, request, response):
        try:
            data = json.loads(request.trajectory_json)
            x = float(data.get('target_x', 0.0))
            y = float(data.get('target_y', 0.0))
            yaw = float(data.get('yaw', math.pi / 2))
            self._complete.clear()
            msg = PoseStamped()
            msg.header.frame_id = 'map'
            msg.pose.position.x = x
            msg.pose.position.y = y
            msg.pose.orientation.z = math.sin(yaw * 0.5)
            msg.pose.orientation.w = math.cos(yaw * 0.5)
            self.pub.publish(msg)
            self.get_logger().info(f'Goal to fastlio_ws: ({x}, {y}, yaw={yaw:.3f}), waiting for arrive...')

            if self._complete.wait(timeout=180.0):
                response.success = True
                self.get_logger().info(f'Arrived: ({x}, {y})')
            else:
                response.success = False
                self.get_logger().error(f'Navigation timeout: ({x}, {y})')
        except Exception as e:
            self.get_logger().error(str(e))
            response.success = False
        return response


def main():
    rclpy.init()
    node = GoalBridge()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        executor.remove_node(node)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
