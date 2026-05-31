#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# phi_robot ROS2 验收模式启动脚本
#
# 使用方式:
#   chmod +x run_ros_acceptance.sh
#   ./run_ros_acceptance.sh
#
# 环境要求:
#   - 已安装 ros-jazzy-rmw-cyclonedds-cpp
#   - phi_robot_interfaces 已 colcon build
# ──────────────────────────────────────────────────────────
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# ── ROS2 环境 ────────────────────────────────────────────
source /opt/ros/jazzy/setup.bash
source "$PROJECT_DIR/install/setup.bash"

# ── DDS 配置（三方约定） ─────────────────────────────────
export ROS_DOMAIN_ID=66
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_DISCOVERY_SERVER=192.168.50.141:11811

# ── Python 虚拟环境 ──────────────────────────────────────
source "$PROJECT_DIR/.venv/bin/activate"

# ── 启动参数 ─────────────────────────────────────────────
# 路径规划服务名待同事确认后修改 --path-plan-service 的值
exec python3 run_phi_robot_api.py \
  --port 5000 \
  --acceptance ros \
  --path-plan-service /start_navigation \
  --gripper-service /robot/gripper_action
