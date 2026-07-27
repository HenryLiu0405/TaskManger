#!/bin/bash
# switch_robot.sh <robot_id>
#
# 多机器人切换：把 robots.json 里选中的 profile 落地为 active_robot.env，
# 然后重启 4 个工作站 systemd 单元（含本进程的父服务 waic-taskmanger）。
#
# 必须由 TaskManger 以 setsid/start_new_session 方式 spawn（脱离进程），
# 因为最后一步会重启 waic-taskmanger，把发起该切换的 HTTP 进程杀掉。
#
# 硬约束：ROS_DOMAIN_ID 在进程启动时固定，切换 domain 只能靠重启整套服务栈。
set -u

WAIC_ROOT="${WAIC_ROOT:-/home/hairo/waic}"
ROBOTS_JSON="${WAIC_ROOT}/TaskManger/phi_robot/robots.json"
ENV_FILE="${WAIC_ROOT}/active_robot.env"
LOCK_FILE="${WAIC_ROOT}/active_robot.lock"

ROBOT_ID="${1:-}"
if [ -z "$ROBOT_ID" ]; then
  echo "usage: switch_robot.sh <robot_id>" >&2
  exit 2
fi

log() { echo "[switch_robot] $*"; }

fail() {
  local msg="$1"
  log "ERROR: $msg"
  # 保留 lock 并标记错误，供前端 /api/robot/switch/status 显示
  printf '{"target":"%s","error":true,"message":"%s"}\n' "$ROBOT_ID" "$msg" >"$LOCK_FILE"
  exit 1
}

# ── 1. 用 python 读取目标 profile 并生成 active_robot.env ──────────────
#     （复用 robot_profile.py，保证键名与后端一致）
python3 - "$ROBOT_ID" <<'PYEOF' || fail "生成 active_robot.env 失败"
import sys, os
sys.path.insert(0, os.path.join(os.environ.get("WAIC_ROOT", "/home/hairo/waic"), "TaskManger"))
from phi_robot import robot_profile
rid = sys.argv[1]
profile = robot_profile.get_robot(rid)
if not profile:
    print(f"unknown robot_id: {rid}", file=sys.stderr)
    sys.exit(1)
robot_profile.write_env_file(profile)
print(f"active_robot.env written for {rid} (domain={profile['domain_id']})")
PYEOF

log "env file ready: $ENV_FILE"

# ── 2. 重启工作站服务（先重启依赖，再重启 TaskManger 自己） ───────────
#     TaskManger 放最后：一旦它重启，本脚本仍在独立 session 中继续。
for unit in waic-navigation waic-goal-bridge waic-foundationpose; do
  log "restarting $unit ..."
  if ! systemctl --user restart "$unit"; then
    fail "restart $unit failed"
  fi
done

# 切换成功：清 lock（在重启 taskmanger 之前，避免新进程看到残留 lock）
rm -f "$LOCK_FILE"
log "lock cleared, restarting waic-taskmanger (self parent) ..."

# 最后重启 TaskManger —— 新进程以新 ROS_DOMAIN_ID 启动
systemctl --user restart waic-taskmanger

log "switch to $ROBOT_ID complete"
exit 0
