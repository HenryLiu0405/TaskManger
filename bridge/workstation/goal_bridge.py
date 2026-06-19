#!/usr/bin/env python3
"""监听 /start_navigation → 提取坐标 → 发布 /goal_pose"""
import json, rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseStamped
from robot_interfaces.srv import ExecuteTrajectory

class GoalBridge(Node):
    def __init__(self):
        super().__init__('goal_bridge')
        self.srv = self.create_service(ExecuteTrajectory, '/start_navigation', self.callback)
        self.pub = self.create_publisher(PoseStamped, '/goal_pose', 10)
        self.get_logger().info('GoalBridge: /start_navigation → /goal_pose')

    def callback(self, request, response):
        try:
            data = json.loads(request.trajectory_json)
            x = float(data.get('target_x', 0.0))
            y = float(data.get('target_y', 0.0))
            msg = PoseStamped()
            msg.header.frame_id = 'map'
            msg.pose.position.x = x
            msg.pose.position.y = y
            msg.pose.orientation.w = 1.0
            self.pub.publish(msg)
            self.get_logger().info(f'Goal to fastlio_ws: ({x}, {y})')
            response.success = True
        except Exception as e:
            self.get_logger().error(str(e))
            response.success = False
        return response

def main():
    rclpy.init()
    rclpy.spin(GoalBridge())

if __name__ == '__main__':
    main()
