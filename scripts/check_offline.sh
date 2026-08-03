#!/usr/bin/env bash
set -euo pipefail

TASKMANAGER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASKMANAGER_PYTHON="${TASKMANAGER_PYTHON:-python3}"

if [[ "$(uname -s)" != "Linux" || ! -r /etc/os-release ]]; then
  echo "Ubuntu 22.04 required" >&2
  exit 1
fi
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "22.04" ]]; then
  echo "Ubuntu 22.04 required, got ${ID:-unknown} ${VERSION_ID:-unknown}" >&2
  exit 1
fi
if [[ "${ROS_DISTRO:-}" != "humble" ]]; then
  echo "ROS2 Humble environment required; source /opt/ros/humble/setup.bash" >&2
  exit 1
fi
"${TASKMANAGER_PYTHON}" -c 'import sys; expected=(3,10); actual=sys.version_info[:2]; raise SystemExit(0 if actual == expected else f"Python 3.10 required, got {actual[0]}.{actual[1]}")'
"${TASKMANAGER_PYTHON}" -c 'import flask, flask_cors, jsonschema'

export PHI_ALLOW_HARDWARE_TESTS=0
export PHI_ADAPTER=unitree_sim
export PHI_ACTION_EXECUTOR=skills
unset PHI_MOVE_TO_URL PHI_SIM_BASE_URL PHI_HARDWARE_ROBOT_ID

BEFORE_STATUS="$(mktemp)"
AFTER_STATUS="$(mktemp)"
COMPILE_CACHE="$(mktemp -d)"
trap 'rm -f "${BEFORE_STATUS}" "${AFTER_STATUS}"; rm -r "${COMPILE_CACHE}"' EXIT

git -C "${TASKMANAGER_ROOT}" status --porcelain=v1 >"${BEFORE_STATUS}"
PYTHONPYCACHEPREFIX="${COMPILE_CACHE}" "${TASKMANAGER_PYTHON}" -m compileall -q \
  "${TASKMANAGER_ROOT}/phi_robot" \
  "${TASKMANAGER_ROOT}/tests" \
  "${TASKMANAGER_ROOT}/bridge" \
  "${TASKMANAGER_ROOT}/run_phi_robot_api.py"
PYTHONDONTWRITEBYTECODE=1 "${TASKMANAGER_PYTHON}" \
  "${TASKMANAGER_ROOT}/tests/offline/run_suite.py"
npm --prefix "${TASKMANAGER_ROOT}/phi_robot_fronted" test
git -C "${TASKMANAGER_ROOT}" status --porcelain=v1 >"${AFTER_STATUS}"

if ! cmp -s "${BEFORE_STATUS}" "${AFTER_STATUS}"; then
  echo "offline checks changed the working tree" >&2
  diff -u "${BEFORE_STATUS}" "${AFTER_STATUS}" || true
  exit 1
fi

echo "offline gate passed without ROS/hardware access or working-tree changes"
