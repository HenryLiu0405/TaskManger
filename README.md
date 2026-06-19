# WAIC TaskManger

基于 ROS2 Humble 的 G1 人形机器人搬运控制系统。提供九宫格任务编排 + 调试控制台双界面，通过 ROS2 Service/Topic 与 Gateway（C++ 运动网关）和 Navigation（路径规划）对接，实现**行走、搬起箱子、携带行走、放下箱子**全流程闭环操作。

## 系统架构

```
浏览器 (localhost:5173)
    │ HTTP
    ▼
TaskManger (Flask :5000)           ← 任务编排 + 状态机 + 安全校验
    │ ROS2 Service / Topic
    ├──▶ Navigation (/start_navigation)    ← 路径规划 → /cmd_vel
    ├──▶ Gateway (/set_lift, /set_lay_down) ← 运动控制 → SONIC
    ├──▶ /locomotion_mode (Topic)          ← 告诉 Gateway 走路模式
    └──▶ /notify_goal_reached              ← 通知 Gateway 已到达
             │
             ▼
         G1 机器人 / MuJoCo 仿真
```

## 目录结构

```
TaskManger/
├── phi_robot/                     # 后端 Python 包
│   ├── adapters/
│   │   └── ros_acceptance.py      # ROS2 适配器（C++ gateway 联调核心）
│   ├── robot_state.py             # SafetyStateMachine（9 状态 + 白/黑名单）
│   ├── api_server.py              # Flask API 服务（REST + SSE）
│   ├── dev_console.py             # 调试控制台控制器
│   ├── service_manager.py         # ServicePanel 远程服务管理
│   ├── audit.py                   # 操作审计日志
│   ├── mission_planner.py         # 任务规划器（九宫格坐标 → 执行步骤）
│   ├── mission_runner.py          # 任务执行引擎
│   ├── mission_service.py         # 任务持久化
│   ├── models.py                  # 数据模型
│   └── ...
├── phi_robot_fronted/             # 前端 React 18 + Vite 5
│   └── src/
│       ├── App.jsx                # 九宫格主界面
│       ├── DeveloperConsole.jsx   # 调试控制台（含 SafetyStateMachine 状态显示）
│       ├── ControlButtons.jsx     # 控制按钮（含安全状态自动禁用）
│       └── ...
├── phi_robot_interfaces/          # ROS2 自定义接口（colcon 包）
├── robot_interfaces/              # ExecuteTrajectory 服务定义
├── systemd/                       # systemd 服务单元文件
│   ├── workstation/               #   → 部署到工作站 ~/.config/systemd/user/
│   └── orin/                      #   → 部署到 Orin ~/.config/systemd/user/
├── scripts/                       # 一键启动脚本
│   ├── start_taskmanger.sh
│   └── start_foundationpose.sh
├── bridge/                        # 桥接层代码
│   ├── workstation/               #   → goal_bridge（工作站）
│   └── orin/                      #   → goal/zmq/action bridge（Orin 真机）
├── run_phi_robot_api.py           # 后端启动入口
├── run_ros_acceptance.sh          # ROS2 联调启动脚本
└── requirements.txt               # Python 依赖
```

## 环境要求

| 组件 | 版本 | 备注 |
|------|------|------|
| OS | Ubuntu 22.04 | 工作站 + Orin 均此版本 |
| ROS2 | Humble | `/opt/ros/humble` |
| Python | 3.10 | 系统自带或 conda |
| Node.js | ≥ 18 | 前端构建 |
| DDS | Fast-DDS | DOMAIN 66 |
| RMW | `rmw_fastrtps_cpp` | |

### Python 依赖

```
flask>=3.0,<4.0
flask-cors>=4.0,<5.0
```

`rclpy`、`std_msgs`、`std_srvs`、`sensor_msgs` 为 ROS2 Humble 自带，无需额外安装。

### 前端依赖

React 18 + Vite 5 + Ant Design 5 + Tailwind CSS 3

## 快速开始

### 1. 构建 ROS2 接口包

```bash
cd /home/hairo/waic/TaskManger
source /opt/ros/humble/setup.bash

# 只在首次或接口改动后需要
colcon build --packages-select robot_interfaces phi_robot_interfaces
```

### 2. 安装依赖

```bash
# Python 虚拟环境（可选但推荐）
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```bash
# 前端
cd phi_robot_fronted
npm install
npm run build
```

### 3. 启动后端

```bash
# 方式 A：systemd（生产推荐）
systemctl --user start waic-taskmanger

# 方式 B：一键脚本
./run_ros_acceptance.sh

# 方式 C：手动
source /opt/ros/humble/setup.bash
source /home/hairo/waic/tm_ws/install/setup.bash
export ROS_DOMAIN_ID=66
export FLASK_PORT=5000
python3 run_phi_robot_api.py --port 5000 --acceptance ros \
  --path-plan-service /start_navigation
```

### 4. 启动前端

```bash
cd phi_robot_fronted
npm run dev
```

### 5. 访问界面

| 页面 | 地址 | 用途 |
|------|------|------|
| 九宫格 | `http://localhost:5173/` | 可视化任务编排与提交 |
| 调试控制台 | `http://localhost:5173/debug.html` | 手动/自动模式、机器人状态、操作日志 |

## ROS2 接口

### Service（TaskManger 调用）

| 服务 | 类型 | 说明 |
|------|------|------|
| `/start_navigation` | `ExecuteTrajectory` | 路径规划导航。请求：`{target_x, target_y, yaw, walk}` |
| `/set_lift` | `std_srvs/SetBool` | 搬起箱子 |
| `/set_lay_down` | `std_srvs/SetBool` | 放下箱子 |
| `/set_stand` | `std_srvs/Trigger` | 机器人站立 |
| `/request_replay` | `std_srvs/Trigger` | 执行回放轨迹 |
| `/notify_goal_reached` | `std_srvs/SetBool` | 通知目标已到达（C++ gateway place 前置步骤） |
| `/get_locomotion_mode` | `std_srvs/Trigger` | 查询当前运动模式 |

### Topic（TaskManger 发布/订阅）

| 话题 | 类型 | 方向 | 说明 |
|------|------|------|------|
| `/locomotion_mode` | `std_msgs/Int32` | 发布 | 运动模式。`1`=正常行走，`2`=携带行走（搬箱时用） |
| `/pause_navigation` | `std_msgs/Bool` | 发布 | 暂停/恢复导航 |
| `/navigation_status` | `std_msgs/String` | 订阅 | 导航状态反馈 |

> **关键设计**：`/locomotion_mode` 是 Topic（非 Service），0.4s 间隔持续发布，C++ gateway 有 0.8s 超时机制。搬箱子后必须发布 `mode=2` 才能触发 `TryStartCarryWalking`。

### DDS 配置

```bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_DOMAIN_ID=66
export ROS_LOCALHOST_ONLY=0
```

## SafetyStateMachine（安全状态机）

9 状态 + 迁移白名单 + 黑名单双重校验，防止非法操作组合。

| 状态 | 说明 | 允许的操作 |
|------|------|:--:|
| `IDLE` | 空闲 | walk |
| `STANDING` | 已站立（出发前） | walk |
| `MOVING` | 移动中 | — |
| `ARRIVED` | 已到达目标 | pick, place, walk |
| `PICKING` | 正在搬起 | — |
| `HOLDING` | 持有箱子 | walk（carry mode=2） |
| `PLACING` | 正在放下 | — |
| `EMERGENCY` | 紧急停止 | 仅可 reset → IDLE |
| `ERROR` | 错误 | 仅可 reset → IDLE |

**黑名单**：

| 操作 | 禁用状态 |
|------|------|
| walk | PICKING, PLACING, EMERGENCY, ERROR |
| pick | IDLE, MOVING, PICKING, HOLDING, PLACING, EMERGENCY, ERROR |
| place | IDLE, STANDING, MOVING, PICKING, EMERGENCY, ERROR |

前端按钮根据当前状态自动灰掉不可用操作。详见 [SAFETY.md §6](../docs/SAFETY.md)。

## 全流程操作链

### 搬箱子完整流程

```
用户点击"走去 B5(3.5, 2.5)"
  → SafetyStateMachine.can_walk() ✅
  → /locomotion_mode = 1（正常行走）
  → /start_navigation {target_x:3.5, target_y:2.5, walk:true}
  → Gateway → SONIC → 机器人行走
  → 到达目标 → IDLE→STANDING→MOVING→ARRIVED

用户点击"搬起"
  → SafetyStateMachine.can_pick() ✅
  → /set_lift(data:true) → /request_replay → Gateway motion_1
  → ARRIVED→PICKING→HOLDING（持有箱子）

用户点击"走去 A5(1.25, 1.0)"
  → SafetyStateMachine.can_walk() ✅
  → /locomotion_mode = 2（携带行走 🔑）
  → /start_navigation {target_x:1.25, target_y:1.0, walk:true}
  → Gateway TryStartCarryWalking → carry_walking
  → 到达目标 → /notify_goal_reached → goal_reached_locked
  → HOLDING→MOVING→ARRIVED

用户点击"放下"
  → SafetyStateMachine.can_place() ✅
  → /set_lay_down(data:true) → /request_replay → Gateway motion_3
  → Gateway 内部自动站立 → /set_stand（已站立则跳过）
  → ARRIVED→PLACING→STANDING
```

## API 端点

### 任务

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/missions` | 提交新任务 |
| GET | `/api/missions/<id>` | 查询任务状态 |
| POST | `/api/missions/<id>/run` | 启动任务 |
| POST | `/api/missions/<id>/pause` | 暂停 |
| POST | `/api/missions/<id>/resume` | 继续 |
| POST | `/api/missions/<id>/abort` | 中止 |
| GET | `/api/missions/<id>/stream` | SSE 事件流 |
| GET | `/api/snapshot` | 系统快照 |
| GET | `/api/health` | 健康检查 |
| GET | `/api/robot/state` | 当前 SafetyStateMachine 状态 |

### 调试控制台

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/dev/mode` | 切换手动/自动模式 |
| GET | `/api/dev/state` | 获取完整状态 |
| GET | `/api/dev/stream` | SSE 实时状态推送 |
| GET | `/api/dev/logs` | 获取操作日志 |
| GET | `/api/dev/logs/export` | 导出操作日志（JSON） |
| POST | `/api/dev/manual/next` | 手动 — 导航 |
| POST | `/api/dev/manual/pick` | 手动 — 搬箱子 |
| POST | `/api/dev/manual/place` | 手动 — 放箱子 |
| POST | `/api/dev/manual/pause` | 手动 — 暂停 |
| POST | `/api/dev/manual/stop` | 手动 — 终止 |
| POST | `/api/dev/auto/start` | 自动 — 启动 |
| POST | `/api/dev/auto/pause` | 自动 — 暂停 |
| POST | `/api/dev/auto/resume` | 自动 — 继续 |
| POST | `/api/dev/auto/stop` | 自动 — 终止 |

### 服务管理（ServicePanel）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/services` | 列出所有服务及状态 |
| POST | `/api/services/<id>/start` | 启动服务 |
| POST | `/api/services/<id>/stop` | 停止服务 |

## 部署

### systemd 服务一览

**工作站**：

| 服务 | systemd 单元 | 文件 |
|------|------|------|
| TaskManger | `waic-taskmanger` | [systemd/workstation/waic-taskmanger.service](systemd/workstation/waic-taskmanger.service) |
| Navigation（本地仿真） | `waic-navigation` | [systemd/workstation/waic-navigation.service](systemd/workstation/waic-navigation.service) |
| goal_bridge | `waic-goal-bridge` | [systemd/workstation/waic-goal-bridge.service](systemd/workstation/waic-goal-bridge.service) |
| FoundationPose | `waic-foundationpose` | [systemd/workstation/waic-foundationpose.service](systemd/workstation/waic-foundationpose.service) |

**Orin 真机**：

| 服务 | systemd 单元 | 文件 |
|------|------|------|
| FAST-LIO | `waic-fastlio` | [systemd/orin/waic-fastlio.service](systemd/orin/waic-fastlio.service) |
| SONIC（仿真） | `waic-sonic` | [systemd/orin/waic-sonic.service](systemd/orin/waic-sonic.service) |
| SONIC（真机） | `waic-sonic-real` | [systemd/orin/waic-sonic-real.service](systemd/orin/waic-sonic-real.service) |
| goal_bridge | `waic-goal-bridge` | [systemd/orin/waic-goal-bridge.service](systemd/orin/waic-goal-bridge.service) |
| ZMQ Bridge | `waic-zmq-bridge` | [systemd/orin/waic-zmq-bridge.service](systemd/orin/waic-zmq-bridge.service) |
| Action Bridge | `waic-action-bridge` | [systemd/orin/waic-action-bridge.service](systemd/orin/waic-action-bridge.service) |

### 部署 systemd 文件

```bash
# 工作站
cp systemd/workstation/*.service ~/.config/systemd/user/
systemctl --user daemon-reload

# Orin 真机
scp systemd/orin/*.service unitree@192.168.50.79:~/.config/systemd/user/
ssh unitree@192.168.50.79 "systemctl --user daemon-reload"
```

## Git 工作流

详见 [SAFETY.md §9](../docs/SAFETY.md#9-git-代码管理规范)。

```
master (生产就绪)
  └── <feature-branch> → 开发 → 真机验证 → 合并回 master → 删除分支
```

**规则**：

| 规则 | 说明 |
|------|------|
| master 每个 commit 都经过真机验证 | 确保随时可部署 |
| 不直接在 master 上改代码 | 所有修改在 feature 分支 |
| 提交格式：`<type>: <描述>` | `feat:` / `fix:` / `chore:` / `docs:` |
| 禁止 force push 到 master | 历史不可改写 |
| `robot_state.py` 改动须双人确认 | SafetyStateMachine 影响真机安全 |

## 真机文件同步

仓库是 systemd 和 bridge 文件的权威来源。部署流程：

```
TaskManger/systemd/workstation/  ──cp──▶  ~/.config/systemd/user/
TaskManger/systemd/orin/         ──scp─▶  unitree@orin:~/.config/systemd/user/
TaskManger/bridge/workstation/   ──cp──▶  ~/waic/waic_bridge/
TaskManger/bridge/orin/          ──scp─▶  unitree@orin:~/waic_bridge/
TaskManger/scripts/              ──cp──▶  ~/waic/
```

## 常见问题

**Q: 启动报 `ModuleNotFoundError: No module named 'rclpy'`**

需要先 source ROS2 环境：`source /opt/ros/humble/setup.bash`

**Q: 启动报 `ImportError: cannot import name 'ExecuteTrajectory'`**

需要 source 本地 workspace：`source install/setup.bash`

**Q: 前端跨域报错**

后端已启用 flask-cors。确认 Vite 代理配置中的后端端口（默认 5000）与后端监听端口一致。

**Q: 机器人无响应**

1. 确认 DDS 配置一致：`RMW_IMPLEMENTATION=rmw_fastrtps_cpp`、`ROS_DOMAIN_ID=66`
2. 确认双方在同一局域网且 ROS2 节点可互相发现：`ros2 node list`
3. 检查 Gateway 是否在线：`ros2 service list | grep get_locomotion_mode`

**Q: 搬起箱子后无法走路**

确认 `/locomotion_mode` 被正确设置为 `2`（携带行走模式）。C++ gateway 在 carry_wait_walk 状态下需要 mode=2 才会触发 `TryStartCarryWalking`。

**Q: 到达目标后不返回 ok**

C++ gateway 没有隐式目标检测（与旧 Python gateway 不同）。TaskManger 在 navigation 返回后会自动调用 `/notify_goal_reached true` 通知 gateway 进入 `goal_reached_locked` 状态。

## 相关文档

| 文档 | 说明 |
|------|------|
| [OVERVIEW.md](../docs/OVERVIEW.md) | 项目总览、物理拓扑、数据流 |
| [ARCHITECTURE.md](../docs/ARCHITECTURE.md) | 系统架构、源码仓库 |
| [ENVIRONMENTS.md](../docs/ENVIRONMENTS.md) | 工作站 + Orin 环境配置 |
| [INTERFACES.md](../docs/INTERFACES.md) | ROS2 接口完整文档 |
| [OPERATIONS.md](../docs/OPERATIONS.md) | 运维手册、启动/停止步骤 |
| [SAFETY.md](../docs/SAFETY.md) | 安全体系设计 + Git 管理规范 |
| [ISSUES.md](../docs/ISSUES.md) | 已知问题与修复记录 |
| [CHANGELOG.md](../docs/CHANGELOG.md) | 变更记录 |
