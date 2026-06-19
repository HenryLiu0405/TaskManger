#!/bin/bash
# TaskManger 一键启动脚本
set -e

source /home/hairo/miniconda3/etc/profile.d/conda.sh
conda activate foundationpose
source /opt/ros/humble/setup.bash
source /home/hairo/waic/tm_ws/install/setup.bash 2>/dev/null || true
export ROS_DOMAIN_ID=66
export FLASK_PORT=5000

cd /home/hairo/waic/TaskManger
exec python3 run_phi_robot_api.py \
  --port 5000 \
  --acceptance ros \
  --path-plan-service /start_navigation \
  --compose-file /home/hairo/waic/docker-compose.yml \
  --service-registry /home/hairo/waic/TaskManger/phi_robot/service_registry.json
