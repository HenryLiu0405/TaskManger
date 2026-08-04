#!/bin/bash
# TaskManger 一键启动脚本
set -e

source /home/hairo/miniconda3/etc/profile.d/conda.sh
conda activate foundationpose
source /opt/ros/humble/setup.bash
source /home/hairo/waic/fp_ws/install/setup.bash 2>/dev/null || true
source /home/hairo/waic/tm_ws/install/setup.bash 2>/dev/null || true
source /home/hairo/waic/gear_sonic_client_ws/install/setup.bash 2>/dev/null || true  # SubmitCarryTask 接口

cd /home/hairo/waic/TaskManger
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

# 多机器人配置：优先读 active_robot.env（由 switch_robot.sh 生成）。
# systemd 已通过 EnvironmentFile 注入；此处 source 是为了支持直接命令行运行。
if [ -f /home/hairo/waic/active_robot.env ]; then
  set -a
  source /home/hairo/waic/active_robot.env
  set +a
fi
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-66}"
export WAIC_CAMERA_HOST="${WAIC_CAMERA_HOST:-192.168.3.168}"
export WAIC_ORIN_HOST="${WAIC_ORIN_HOST:-unitree@192.168.3.168}"
export APP_DOMAIN="${APP_DOMAIN:-$ROS_DOMAIN_ID}"
export FLASK_PORT=5000
TASKMANAGER_PYTHON="${TASKMANAGER_PYTHON:-python3}"

if [ "${PHI_AUTONOMY_ENABLED:-0}" = "1" ]; then
  exec "${TASKMANAGER_PYTHON}" run_autonomy_api.py
fi

exec "${TASKMANAGER_PYTHON}" run_phi_robot_api.py \
  --port 5000 \
  --acceptance ros \
  --path-plan-service /start_navigation \
  --camera-host "${WAIC_CAMERA_HOST}" \
  --compose-file /home/hairo/waic/docker-compose.yml \
  --service-registry /home/hairo/waic/TaskManger/phi_robot/service_registry.json
