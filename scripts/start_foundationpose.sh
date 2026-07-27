#!/bin/bash
# FoundationPose 一键启动脚本
# 用法: bash start_foundationpose.sh [额外ros2 launch参数]
set -e

# 多机器人配置：优先读 active_robot.env（systemd 已通过 EnvironmentFile 注入，
# 此处 source 支持命令行直接运行）。FP 按 camera_host 自动选 domain（见 launch）。
if [ -f /home/hairo/waic/active_robot.env ]; then
  set -a
  source /home/hairo/waic/active_robot.env
  set +a
fi
CAMERA_HOST="${FP_CAMERA_HOST:-192.168.50.223}"

source /home/hairo/miniconda3/etc/profile.d/conda.sh
conda activate foundationpose
source /opt/ros/humble/setup.bash
source /home/hairo/waic/fp_ws/install/setup.bash
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-66}"

exec ros2 launch foundationpose_ros2 foundationpose.launch.py \
  enable_visualization:=true \
  publish_in_pelvis_frame:=true \
  camera_host:="${CAMERA_HOST}" \
  "$@"
