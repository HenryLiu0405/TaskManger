# WAIC TaskManger

基于 ROS2 Humble 的 G1 人形机器人搬运控制系统。双界面架构：**主页**（九宫格选点 + 实时进度） + **调试控制台**（分步调试 + 自动执行），通过 ROS2 Service/Topic 与 Gateway（C++ 运动网关）、Navigation（路径规划）、FoundationPose（6DoF 位姿估计）对接，实现**行走、搬起箱子、携带行走、放下箱子**全流程闭环。

> 自主化开发状态（2026-08-03）：仓库已加入 Phase 2–8 的离线实现切片。
> 默认主页现为自然语言任务提交与监控台，旧九宫格/分步工具保留在
> `?developer=1`。Gemini Robotics-ER 2 标准预览端点已配置，但真实云调用、
> 真实语义适配器、仿真和 HIL 尚未批准或验证；现有真实 ROS 路径不会因这些
> 离线代码自动切换。详见 `docs/autonomy/phase*/implementation.md`。

## 系统架构

```
┌─ 浏览器 ──────────────────────────────────────────────────────┐
│                                                                │
│  主页 (index.html :4173)          debug 控制台 (debug.html)     │
│  ├─ 九宫格画路径                    ├─ 步骤列表                   │
│  ├─ 点 RUN → POST /load 加载计划   ├─ 自动执行 (FP select_target)│
│  └─ SSE /stream 实时进度           └─ 手动模式                   │
│                         │                                      │
└─────────────────────────│──────────────────────────────────────┘
                          │ HTTP + SSE
                          ▼
┌─ TaskManger (Flask :5000) ────────────────────────────────────┐
│  phi_robot/                                                    │
│  ├─ api_server.py          REST API + SSE 端点                 │
│  ├─ step_debug.py          StepDebugController (分步调试引擎)   │
│  ├─ mission_planner.py     任务规划 (九宫格 → 执行步骤)         │
│  ├─ robot_state.py         SafetyStateMachine (9 状态安全机)    │
│  ├─ service_manager.py     远程服务管理 (systemd over SSH)      │
│  ├─ robot_profile.py       多机器人档案管理                      │
│  └─ adapters/ros_acceptance.py   ROS2 适配器                    │
└──────────────┬─────────────────────────────────────────────────┘
               │ ROS2 Service / Topic / Action
               ▼
┌─ ROS2 中间层 ─────────────────────────────────────────────────┐
│  goal_bridge       /start_navigation → /goal_pose              │
│  ZMQ Bridge        CycloneDDS(robot) ↔ Fast-DDS(workstation)   │
│  FoundationPose    /foundationpose/select_target                │
└──────────────┬─────────────────────────────────────────────────┘
               │
               ▼
┌─ G1 机器人 (Orin) ────────────────────────────────────────────┐
│  Gateway (C++)      运动控制 (lift/lay_down/stand/replay)       │
│  SONIC              全身控制策略                                │
│  FAST-LIO            激光惯性里程计                              │
│  Nav2                路径规划                                    │
└────────────────────────────────────────────────────────────────┘
```

**关键数据流**：

| 方向 | 路径 | 说明 |
|---|---|---|
| 导航目标 | TaskManger → `/start_navigation` → goal_bridge → `/goal_pose` → Nav2 | |
| 导航到达 | Nav2 → `/nav_reached` → ZMQ Bridge → TaskManger | |
| 运动控制 | TaskManger → Gateway Services (`/set_lift`, `/set_lay_down`, 等) | |
| 位姿估计 | FoundationPose `/foundationpose/pose_results` → TaskManger | pelvis 帧 |
| 模式切换 | TaskManger → `/locomotion_mode` (Topic) → Gateway | 1=行走, 2=搬运 |
| 暂停导航 | TaskManger → `/nav_pause` (Topic) → ZMQ Bridge → Nav2 | |
| 掉箱检测 | FoundationPose `/vision/drop_detector_enable` → Gateway | |

## 目录结构

```
TaskManger/
├── phi_robot/                          # 后端 Python 包
│   ├── adapters/
│   │   └── ros_acceptance.py           # ROS2 适配器 (Gateway/Nav/FP 联调核心)
│   ├── api_server.py                   # Flask API 服务 (REST + SSE)
│   ├── step_debug.py                   # 分步调试控制器 (StepDebugController)
│   ├── dev_console.py                  # 调试控制台控制器
│   ├── mission_planner.py              # 任务规划器 (九宫格坐标 → 执行步骤)
│   ├── mission_runner.py               # 任务执行引擎
│   ├── mission_service.py              # 任务持久化
│   ├── robot_state.py                  # SafetyStateMachine (9 状态 + 迁移白名单)
│   ├── robot_profile.py                # 多机器人档案管理
│   ├── service_manager.py              # 远程 systemd 服务管理
│   ├── service_registry.json           # 服务注册表 (工作站 + Orin)
│   ├── robots.json                     # 机器人 IP/Domain 配置
│   ├── scene_coords.json               # 场地 Nav2 map 坐标配置
│   ├── audit.py                        # 操作审计日志
│   ├── recovery_diag.py                # 恢复诊断事件工厂
│   └── models.py                       # 数据模型
├── phi_robot_fronted/                  # 前端 React 18 + Vite 5
│   ├── index.html                      # 主页入口
│   ├── debug.html                      # 调试控制台入口
│   └── src/
│       ├── App.jsx                     # 主页: 九宫格 + SSE 进度
│       ├── debug-main.jsx              # 调试页入口
│       ├── DeveloperConsole.jsx        # 调试控制台
│       ├── StepDebugPanel.jsx          # 分步调试面板
│       ├── LockBoard2D.jsx             # 九宫格画布
│       ├── UnifiedConsole.jsx          # 统一控制台
│       ├── StepDetailPanel.jsx         # 步骤详情 + 终端输出
│       └── index.css                   # lunar 主题样式
├── bridge/                             # 桥接层代码
│   ├── workstation/
│   │   └── goal_bridge.py              # /start_navigation → /goal_pose (等 /navigation_complete)
│   └── orin/
│       ├── goal_bridge.py              # Orin 侧 goal 桥接
│       ├── action_bridge.py            # Action 桥接
│       └── zmq_bridge.py               # ZMQ 跨 DDS 桥接
├── systemd/                            # systemd 服务单元文件
│   ├── workstation/                    #   工作站侧 (部署到 ~/.config/systemd/user/)
│   └── orin/                           #   Orin 侧 (部署到机器人 ~/.config/systemd/user/)
├── scripts/                            # 一键启动脚本
│   ├── start_taskmanger.sh
│   └── start_foundationpose.sh
├── robot_interfaces/                   # ExecuteTrajectory 服务定义
├── phi_robot_interfaces/               # phi_robot 自定义接口
├── run_phi_robot_api.py                # 后端启动入口
├── run_ros_acceptance.sh               # ROS2 联调启动脚本
└── requirements.txt                    # Python 依赖
```

## 环境要求

| 组件 | 版本 | 备注 |
|------|------|------|
| OS | Ubuntu 22.04 | 工作站 + Orin |
| ROS2 | Humble | `/opt/ros/humble` |
| Python | 3.10 | 系统或 conda |
| Node.js | ≥ 18 | 前端 |
| DDS | Fast-DDS | workstation 侧 |
| DDS | CycloneDDS | Orin 机器人侧 |
| conda | miniconda3 | FoundationPose 环境 |

### Python 依赖

```
flask>=3.0,<4.0
flask-cors>=4.0,<5.0
jsonschema>=4.23,<5.0
```

`rclpy`、`std_msgs`、`sensor_msgs` 为 ROS2 Humble 自带。

建议用仓库内的 `environment.yml` 创建隔离的开发环境：

```bash
conda env create -f environment.yml
conda activate AgenticRobot
python --version  # 必须为 3.10.x
```

`AgenticRobot` 包含后端与离线测试依赖；ROS Python 包仍由 Ubuntu 22.04
上的 ROS2 Humble 提供。正式离线 Gate 需在目标 Ubuntu 主机上 source
Humble 后执行 `TASKMANAGER_PYTHON="$CONDA_PREFIX/bin/python" ./scripts/check_offline.sh`。

### 前端依赖

React 18 + Vite 5 + Ant Design 5

## 配置

### Gemini Robotics-ER 2（自主运行时，显式启用）

复制 `config/autonomy.env.example` 中的变量名到部署密钥管理器或进程环境：

```bash
export GEMINI_API_KEY='使用轮换后的新密钥'
export GEMINI_ROBOTICS_MODEL='gemini-robotics-er-2-preview'
export GEMINI_API_MODE='interactions'
```

普通 `gemini-flash-latest` 是通用 Gemini alias，不能当作 Robotics-ER 2
身份；`gemini-robotics-er-2-streaming-preview` 属于 Live API 流式端点，
不由当前非流式 Planner 适配器调用。API key 不得提交到 Git、普通日志或模型审计记录。创建
`GeminiAutonomyRuntime` 本身不会发起网络/机器人调用；实际提交任务前仍需
通过 replay、simulation 和相应 HIL gate。

### 1. 机器人配置 (`phi_robot/robots.json`)

多机器人档案的**唯一事实来源**。`switch_robot.sh` 读取此文件生成 `active_robot.env`。

```json
{
  "active": "A",
  "robots": {
    "A": {
      "label": "机器人A",
      "ip": "192.168.3.168",
      "domain_id": 66,
      "camera_host": "192.168.3.168",
      "orin_host": "unitree@192.168.3.168"
    },
    "B": {
      "label": "机器人B",
      "ip": "192.168.3.215",
      "domain_id": 5,
      "camera_host": "192.168.3.215",
      "orin_host": "unitree@192.168.3.215"
    }
  }
}
```

**字段说明**：

| 字段 | 说明 |
|------|------|
| `ip` | 机器人 IP |
| `domain_id` | ROS2 DDS domain（每台机器人独立 domain 防止干扰） |
| `camera_host` | ZMQ 相机服务器 IP |
| `orin_host` | SSH 连接串（用于 service_manager 远程管理） |

### 2. 场景坐标 (`phi_robot/scene_coords.json`)

Nav2 map 坐标系下的 9 目标点 + 1 物料点配置。

```json
{
  "frame": "nav2_map",
  "stock_point": { "x": -2.5, "y": 1.9, "theta": 2.2689 },
  "grid_cells": {
    "nw": { "x": -0.4, "y": 1.4, "theta": -1.5708 },
    "n":  { "x": 0.11, "y": 2.0, "theta": -1.5708 },
    "ne": { "x": 0.9,  "y": 2.8, "theta": -1.5708 },
    "w":  { "x": -1.1, "y": 2.0, "theta": -1.5708 },
    "c":  { "x": -0.5, "y": 2.7, "theta": -1.5708 },
    "e":  { "x": 0.2,  "y": 3.5, "theta": -1.5708 },
    "sw": { "x": -1.8, "y": 2.7, "theta": 0.0 },
    "s":  { "x": -1.1, "y": 3.5, "theta": 1.5708 },
    "se": { "x": -0.5, "y": 4.1, "theta": 1.5708 }
  },
  "map_bounds": { "x_min": -3.0, "x_max": 1.5, "y_min": 1.0, "y_max": 4.6 },
  "approach_offset": 0.6
}
```

改坐标只需编辑此文件 + 重启 TaskManger，前端从 `/api/scene_coords` 自动拉取。

### 3. `active_robot.env`（自动生成）

由 `switch_robot.sh` 从 `robots.json` 生成，**不要手动编辑**：

```
ROS_DOMAIN_ID=66
APP_DOMAIN=66
WAIC_ROBOT_ID=A
WAIC_ROBOT_IP=192.168.3.168
WAIC_CAMERA_HOST=192.168.3.168
FP_CAMERA_HOST=192.168.3.168
WAIC_ORIN_HOST=unitree@192.168.3.168
```

所有 systemd 服务通过 `EnvironmentFile` 读取此文件来获取 domain 和目标机器人。

## 快速开始

### 1. 克隆

```bash
git clone https://github.com/PHI-WAIC/TaskManger.git
cd TaskManger
```

### 2. 构建 ROS2 接口包

```bash
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select robot_interfaces phi_robot_interfaces
source install/setup.bash
```

### 3. 安装依赖

```bash
# Python 虚拟环境
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 前端
cd phi_robot_fronted
npm install
npm run build        # 生产构建到 dist/
```

### 4. 配置机器人

```bash
# 编辑 robots.json 填入你的机器人 IP 和 domain_id
vim phi_robot/robots.json

# 生成 active_robot.env
python3 -c "
import sys; sys.path.insert(0, '.')
from phi_robot import robot_profile
robot_profile.write_env_file(robot_profile.get_active())
"
```

### 5. 启动

**方式 A：systemd（生产推荐）**

```bash
cp systemd/workstation/*.service ~/.config/systemd/user/
# 按实际环境修改 .service 文件中的路径
systemctl --user daemon-reload
systemctl --user start waic-taskmanger
```

**方式 B：手动启动**

```bash
# 终端 1: 后端
source /opt/ros/humble/setup.bash
source install/setup.bash
source /home/hairo/waic/active_robot.env
source .venv/bin/activate
python3 run_phi_robot_api.py --port 5000 --acceptance ros \
  --path-plan-service /start_navigation

# 终端 2: 前端
cd phi_robot_fronted
npm run dev
```

### 6. 访问

| 页面 | 地址 | 用途 |
|------|------|------|
| 主页 | `http://localhost:4173/` | 九宫格画路径 → 加载计划 |
| 调试控制台 | `http://localhost:4173/debug.html` | 分步调试 / 自动执行 (FP select_target) |

## 双界面工作流

```
┌─ 主页 (index.html) ───────────┐   ┌─ debug.html ────────────────┐
│                                │   │                              │
│  用户画九宫格 → 点 RUN          │   │  操作员看到已加载的计划        │
│       ↓                        │   │       ↓                      │
│  POST /api/dev/step_debug/load │   │  点击"自动执行"               │
│  计划已加载（只显示，不执行）    │   │       ↓                      │
│       ↓                        │   │  StepDebugController._run_auto│
│  ← SSE /stream 实时推送进度 ───│──│  (select_target + fail-stop)   │
│  UnifiedConsole 显示进度点      │   │                              │
│  StepDetailPanel 步骤状态      │   │                              │
└────────────────────────────────┘   └──────────────────────────────┘
```

**关键设计**：主页 RUN 和 debug 自动执行走同一引擎（`StepDebugController._run_auto()`），包含 FP `select_target` 自动单目标锁定。主页通过 SSE 实时同步步骤状态，不需要轮询。

## 机器人切换

```bash
# 切换到机器人 B
/home/hairo/waic/switch_robot.sh B

# 查看当前受控机器人
curl http://localhost:5000/api/robot/active
```

`switch_robot.sh` 会：
1. 从 `robots.json` 读取目标机器人 profile
2. 重新生成 `active_robot.env`
3. 重启 `waic-navigation`、`waic-goal-bridge`、`waic-foundationpose`
4. 最后重启 `waic-taskmanger`（新 domain 生效）

> **硬约束**：ROS_DOMAIN_ID 在进程启动时固定，切换机器人 = 切换 domain = 必须重启整套服务。

## ROS2 接口

### Service（TaskManger 调用）

| 服务 | 类型 | 说明 |
|------|------|------|
| `/start_navigation` | `ExecuteTrajectory` | 路径规划导航 (→ goal_bridge → /goal_pose) |
| `/submit_carry_task` | `SubmitCarryTask` | 提交搬起任务 (单物体位姿 → Gateway) |
| `/set_lay_down` | `SetBool` | 放下箱子 |
| `/set_stand` | `Trigger` | 机器人站立 |
| `/notify_goal_reached` | `SetBool` | 通知 Gateway 已到达 (carry_walking 后进入 goal_reached_locked) |
| `/foundationpose/select_target` | `SelectTarget` | FP 单目标锁定 (MODE_TARGET/MODE_DROPPED) |
| `/foundationpose/activate` | `Activate` | 激活/停用 FP 跟踪 |
| `/foundationpose/reset` | `Reset` | 重置 FP tracker |

### Topic（TaskManger 发布/订阅）

| 话题 | 类型 | 方向 | 说明 |
|------|------|------|------|
| `/locomotion_mode` | `Int32` | 发布 | 运动模式: `1`=正常行走, `2`=携带行走。0.4s 间隔持续发布 |
| `/nav_pause` | `Bool` | 发布 | 暂停/恢复导航 |
| `/nav_reached` | `Bool` | 订阅 | 导航到达确认 (Nav2 → ZMQ Bridge → TaskManger) |
| `/foundationpose/pose_results` | `PoseEstimateArray` | 订阅 | FP 位姿估计结果 (pelvis 帧) |
| `/fp_state` | `String` | 订阅 | FP 跟踪状态 JSON |
| `/odom` | `Odometry` | 订阅 | 里程计 (TF 已废弃，只用 odom) |
| `/G1Env/env_state_act` | `ByteMultiArray` | 订阅 | SONIC 环境状态 (含 waist joint angles，仅 FP 使用) |

### SelectTarget 服务详解

```
/foundationpose/select_target
  Request:
    mode: 0 (MODE_TARGET: 取箱) | 1 (MODE_DROPPED: 掉箱检测)
    select: true=进入模式, false=退出
    material_points_xy: [x1, y1, x2, y2, ...]  # 所有物料点
    target_points_xy: [x1, y1, x2, y2, ...]     # 所有目标点
    pick_x, pick_y: 当前要拿取的物料点坐标
    point_tolerance: 容差 (默认 0.3m)

  Response:
    success: bool
    matched_object_id: int32   # 匹配到的物体 ID, -1=未匹配
    box_world_xy: [x1, y1, ...]  # 视野中所有箱子世界坐标
    box_object_ids: [id1, ...]  # 对应箱子 ID
```

**自动执行流程**：pick 步前自动 `select_target(select=True, MODE_TARGET)` 锁定距离取料点最近的箱子；pick 完成后自动 `select_target(select=False)` 退出并重新启用掉箱检测。

## DDS & ZMQ Bridge 拓扑

```
机器人 Orin                        工作站
CycloneDDS domain 0               Fast-DDS domain 66/5
                                  
  /goal_pose    ──ZMQ:5561──▶     /goal_pose
  /cmd_vel      ──ZMQ:5562──▶     /cmd_vel
  /tf           ──ZMQ:5564──▶     /tf
  /tf_static    ──ZMQ:5565──▶     /tf_static
  /nav_pause    ◀──ZMQ:5566──     /nav_pause
  /nav_reached  ──ZMQ:5567──▶     /nav_reached
```

12 个 worker（6 对 sub/pub）在 Orin `/home/unitree/waic_bridge/zmq_bridge.py` 上运行。

## API 端点

### 任务 & 状态

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/snapshot` | 系统快照 |
| GET | `/api/robot/state` | SafetyStateMachine 当前状态 |

### 分步调试 (StepDebugController)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/dev/step_debug/load` | 加载计划 (主页 RUN 按钮调用) |
| POST | `/api/dev/step_debug/auto_run` | 自动执行 (debug 自动执行按钮) |
| POST | `/api/dev/step_debug/pause` | 暂停 |
| POST | `/api/dev/step_debug/resume` | 继续 |
| POST | `/api/dev/step_debug/stop` | 终止 |
| POST | `/api/dev/step_debug/select_target_toggle` | 手动切换 FP 单目标模式 |
| POST | `/api/dev/step_debug/drop_check` | 手动掉箱检测 |
| GET | `/api/dev/step_debug/stream` | SSE 事件流 (主页实时进度) |
| GET | `/api/dev/step_debug/state` | 当前分步调试状态 |

### 机器人管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/robot/active` | 当前受控机器人 + domain 同步状态 |
| GET | `/api/robot/list` | 所有可选机器人 |
| GET | `/api/scene_coords` | 场地坐标配置 (前端动态渲染用) |

### 服务管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/services` | 列出所有服务状态 |
| POST | `/api/services/<id>/start` | 启动服务 |
| POST | `/api/services/<id>/stop` | 停止服务 |

## SafetyStateMachine

9 状态 + 迁移白名单 + 黑名单双重校验，防止非法操作。

| 状态 | 说明 |
|------|------|
| `IDLE` | 空闲，未就绪 |
| `STANDING` | 已站立，可移动 |
| `MOVING` | 行走中 |
| `ARRIVED` | 已到达目标位置 |
| `PICKING` | 搬起执行中 |
| `HOLDING` | 持有箱子 |
| `PLACING` | 放下执行中 |
| `EMERGENCY` | 急停 |
| `ERROR` | 故障 |

**黑名单**：

| 操作 | 禁用状态 |
|------|------|
| walk | PICKING, PLACING, EMERGENCY, ERROR |
| pick | IDLE, MOVING, PICKING, HOLDING, PLACING, EMERGENCY, ERROR |
| place | IDLE, STANDING, MOVING, PICKING, EMERGENCY, ERROR |

## 部署

### systemd 服务一览

**工作站**：

| 服务 | systemd 单元 |
|------|-------------|
| TaskManger | `waic-taskmanger` |
| Navigation（仿真） | `waic-navigation` |
| goal_bridge | `waic-goal-bridge` |
| FoundationPose | `waic-foundationpose` |

**Orin 真机**：

| 服务 | systemd 单元 |
|------|-------------|
| Gateway | `waic-gateway@<domain>` |
| SONIC | `waic-sonic-real@<domain>` |
| ZMQ Bridge | `waic-zmq-bridge@<domain>` |
| Action Bridge | `waic-action-bridge` |
| FAST-LIO | `waic-fastlio` |

### 部署 systemd

```bash
# 工作站
cp systemd/workstation/*.service ~/.config/systemd/user/
# 按需修改路径后:
systemctl --user daemon-reload
systemctl --user enable --now waic-taskmanger

# Orin (从工作站推)
scp systemd/orin/*.service unitree@<orin_ip>:~/.config/systemd/user/
ssh unitree@<orin_ip> "systemctl --user daemon-reload"
```

### 启动顺序

```
1. Orin: waic-gateway, waic-fastlio, waic-zmq-bridge, waic-action-bridge
2. 工作站: waic-goal-bridge
3. 工作站: waic-navigation
4. 工作站: waic-foundationpose
5. 工作站: waic-taskmanger
6. 手动: npm run dev (前端)
```

## 新工作站部署 Checklist

在另一台工作站上拉取后，按以下步骤操作：

- [ ] `git clone` + `git checkout replanning`
- [ ] 安装 ROS2 Humble + conda + Node.js ≥ 18
- [ ] `colcon build --symlink-install`
- [ ] 创建 `.venv` + `pip install -r requirements.txt`
- [ ] 创建 conda env `foundationpose`（含 torch, FoundationPose 依赖）
- [ ] 编辑 `robots.json`（填入实际机器人 IP/domain）
- [ ] 生成 `active_robot.env`
- [ ] 编辑 `scene_coords.json`（填入实际场地坐标）
- [ ] `cd phi_robot_fronted && npm install && npm run build`
- [ ] 部署 systemd 文件到 `~/.config/systemd/user/` 并按需改路径
- [ ] 生成 SSH key `id_ed25519_waic`（用于 ssh 到 Orin）
- [ ] 启动服务

## 常见问题

**Q: 启动报 `ModuleNotFoundError: No module named 'rclpy'`**

需要先 source ROS2 环境：`source /opt/ros/humble/setup.bash`

**Q: FP 节点闪退，报 `AssertionError: matched_object_id must be in [-128, 127]`**

tracker ID 溢出。需将 `SelectTarget.srv` 中 `matched_object_id` 和 `box_object_ids` 从 `int8` 改为 `int32`。

**Q: FP 收不到 `/G1Env/env_state_act` 数据**

QoS 不兼容。FP 订阅需用 `best_effort` 匹配 SONIC publisher 的 QoS。

**Q: `/foundationpose/pose_results` frame_id 在 pelvis 和 torso_link 间交替**

有多个 FP 节点实例在运行。`pkill -f foundationpose_node` 杀掉旧实例后重启。

**Q: 主页 RUN 后不自动执行**

这是设计行为——主页只加载计划到 `StepDebugController`，执行由操作员在 debug.html 控制。

**Q: 机器人无响应**

1. 检查 DDS domain 一致：`echo $ROS_DOMAIN_ID`
2. 检查双方在同一局域网：`ros2 node list`
3. 检查 ZMQ bridge 是否在线：`systemctl --user status waic-zmq-bridge@<domain>`

**Q: 搬起箱子后无法走路**

确认 `/locomotion_mode` 被正确设置为 `2`（携带行走模式）。Gateway 在 `carry_wait_walk` 状态下需要 mode=2 才能触发行走。

## Git 工作流

```
replanning (当前开发分支)
  └── <feature-branch> → 开发 → 真机验证 → 合并
```

| 规则 | 说明 |
|------|------|
| 提交格式 | `feat:` / `fix:` / `chore:` / `docs:` |
| 禁止 force push 到 protected 分支 | |
| `robot_state.py` 改动须双人确认 | SafetyStateMachine 影响真机安全 |
