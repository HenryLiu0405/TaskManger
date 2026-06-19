#!/bin/bash
# FoundationPose 一键启动脚本
# 用法: bash start_foundationpose.sh [额外ros2 launch参数]
set -e

CAMERA_HOST="${FP_CAMERA_HOST:-192.168.50.223}"

source /home/hairo/miniconda3/etc/profile.d/conda.sh
conda activate foundationpose
source /opt/ros/humble/setup.bash
source /home/hairo/fp_ws/install/setup.bash
export ROS_DOMAIN_ID=66

exec ros2 launch foundationpose_ros2 foundationpose.launch.py \
  enable_visualization:=false \
  camera_host:="${CAMERA_HOST}" \
  "$@"
