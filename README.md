# phi_robot

基于 ROS2 的机器人搬运控制系统。支持九宫格任务编排 + 调试控制台双界面，通过 ROS2 服务与机器人底层运动控制对接，实现导航行走、搬起箱子、放置箱子等全流程闭环操作。

## 项目结构

```
phi_robot_demo/
├── phi_robot/                    # 后端 Python 包
│   ├── api_server.py             # Flask API 服务（REST + SSE）
│   ├── dev_console.py            # 调试控制台控制器（双模式状态机）
│   ├── mission_planner.py        # 任务规划器（九宫格坐标 → 执行步骤）
│   ├── mission_runner.py         # 任务执行引擎（后台循环）
│   ├── mission_service.py        # 任务持久化与状态管理
│   ├── models.py                 # 数据模型
│   └── adapters/
│       └── ros_acceptance.py     # ROS2 适配器（核心联调层）
├── phi_robot_fronted/            # 前端 React 应用
│   ├── index.html                # 九宫格界面入口
│   ├── debug.html                # 调试控制台入口
│   └── src/
│       ├── App.jsx               # 九宫格主界面
│       ├── LockBoard2D.jsx       # 可视化面板
│       ├── UnifiedConsole.jsx    # 任务提交/监控面板
│       ├── DeveloperConsole.jsx  # HGPT CONSOLE 控制台
│       ├── ControlButtons.jsx    # 控制按钮（含确认弹窗）
│       ├── RobotPanel.jsx        # 机器人状态面板
│       ├── BoxPanel.jsx          # 箱子状态面板
│       └── LogPanel.jsx          # 操作日志面板
├── robot_interfaces/             # ROS2 自定义服务定义
│   └── srv/
│       └── ExecuteTrajectory.srv # 路径规划服务接口
├── run_phi_robot_api.py          # 后端启动入口
├── run_ros_acceptance.sh         # ROS2 联调一键启动脚本
└── requirements.txt              # Python 依赖
```

## 环境要求

| 组件 | 版本 |
|------|------|
| ROS2 | Jazzy |
| Python | >= 3.10 |
| Node.js | >= 18 |
| DDS | Fast-RTPS（与机器人端一致） |

### Python 依赖

```
flask>=3.0,<4.0
flask-cors>=4.0,<5.0
```
rclpy 为 ROS2 Jazzy 自带，无需额外安装。

### 前端依赖

React 18 + Vite 5 + Ant Design 5 + Tailwind CSS 3

## 快速开始

### 1. 构建 ROS2 接口包

```bash
cd phi_robot_demo
source /opt/ros/jazzy/setup.bash
colcon build --packages-select robot_interfaces
```

### 2. 安装 Python 依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 安装前端依赖并构建

```bash
cd phi_robot_fronted
npm install
npm run build
```

### 4. 启动后端（ROS2 联调模式）

```bash
# 一键启动（推荐）
./run_ros_acceptance.sh

# 或手动执行
source /opt/ros/jazzy/setup.bash
source install/setup.bash
source .venv/bin/activate
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_DOMAIN_ID=66
export ROS_LOCALHOST_ONLY=0
python3 run_phi_robot_api.py --port 5000 --acceptance ros \
  --path-plan-service /start_navigation
```

### 5. 启动前端

```bash
cd phi_robot_fronted
npm run dev
```

### 6. 访问界面

| 页面 | 地址 | 用途 |
|------|------|------|
| 九宫格界面 | `http://localhost:4173/` | 可视化任务编排与提交 |
| 调试控制台 | `http://localhost:4173/debug.html` | 手动/自动模式控制、实时状态监控 |

## 调试控制台使用指南

### 手动模式

适用场景：单步调试，逐步验证机器人动作。

1. 在预设位置中点击目标坐标，或手动输入 X/Y 数值
2. 点击「导航」按钮，机器人移动到目标位置
3. 点击「搬箱子」执行 lift + replay 序列
4. 点击「放箱子」执行 lay_down + replay + stand 序列
5. 随时可按「暂停」停止机器人运动，「继续」从中断点恢复
6. 「终止」将重置所有状态（不可恢复）

状态流转：`idle → moving → arrived → picking → holding → placing → idle`

### 自动模式

适用场景：批量搬运任务，按规划好的步骤序列自动执行。

1. 在「目标位置」区域点击九宫格方位（如 nw、c、se），加入任务序列
2. 系统自动按顺序分配物料区槽位（顶排 → 中排 → 底排）
3. 点击「开始」启动自动任务
4. 每个步骤执行完成后自动进入下一步
5. 支持暂停/继续/终止，操作日志实时输出

## ROS2 接口说明

### 自定义服务（需 colcon build）

| 服务 | 类型 | 方向 | 说明 |
|------|------|------|------|
| `/start_navigation` | `ExecuteTrajectory` | 调用 | 路径规划导航，入参为 JSON 字符串，包含 target_x、target_y、yaw、walk |

### 标准服务（ROS2 内置类型）

| 服务 | 类型 | 方向 | 说明 |
|------|------|------|------|
| `/set_lift` | `std_srvs/SetBool` | 调用 | 搬起箱子 |
| `/set_lay_down` | `std_srvs/SetBool` | 调用 | 放下箱子 |
| `/set_stand` | `std_srvs/Trigger` | 调用 | 机器人站立 |
| `/request_replay` | `std_srvs/Trigger` | 调用 | 执行回放轨迹 |
| `/notify_goal_reached` | `std_srvs/SetBool` | 调用 | 通知目标已到达（place 前置步骤） |
| `/get_locomotion_mode` | `std_srvs/Trigger` | 调用 | 查询当前运动状态 |

### 话题

| 话题 | 类型 | 方向 | 说明 |
|------|------|------|------|
| `/pause_navigation` | `std_msgs/Bool` | 发布 | 暂停/恢复导航（True=暂停，False=继续） |
| `/navigation_status` | `std_msgs/String` | 订阅 | 导航状态反馈（IDLE / PAUSED 等） |

### DDS 配置

需与机器人端保持一致：

```bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_DOMAIN_ID=66
export ROS_LOCALHOST_ONLY=0
```

## API 端点

### 任务相关

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/missions` | 提交新任务 |
| GET | `/api/missions/<id>` | 查询任务状态 |
| POST | `/api/missions/<id>/run` | 启动任务执行 |
| POST | `/api/missions/<id>/pause` | 暂停任务 |
| POST | `/api/missions/<id>/resume` | 继续任务 |
| POST | `/api/missions/<id>/abort` | 中止任务 |
| GET | `/api/missions/<id>/stream` | SSE 事件流 |
| GET | `/api/snapshot` | 获取系统快照 |
| GET | `/api/health` | 健康检查 |

### 调试控制台

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/dev/mode` | 切换手动/自动模式 |
| GET | `/api/dev/state` | 获取完整状态 |
| GET | `/api/dev/stream` | SSE 实时状态推送 |
| GET | `/api/dev/logs` | 获取操作日志 |
| GET | `/api/dev/logs/export` | 导出操作日志（JSON） |
| POST | `/api/dev/manual/next` | 手动模式 — 导航 |
| POST | `/api/dev/manual/pick` | 手动模式 — 搬箱子 |
| POST | `/api/dev/manual/place` | 手动模式 — 放箱子 |
| POST | `/api/dev/manual/pause` | 手动模式 — 暂停 |
| POST | `/api/dev/manual/stop` | 手动模式 — 终止 |
| POST | `/api/dev/auto/start` | 自动模式 — 启动 |
| POST | `/api/dev/auto/pause` | 自动模式 — 暂停 |
| POST | `/api/dev/auto/resume` | 自动模式 — 继续 |
| POST | `/api/dev/auto/stop` | 自动模式 — 终止 |

## 坐标参考

### 目标区（九宫格落箱点）

| 方位 | x | y |
|------|---|---|
| nw | 2.7 | 2.5 |
| n | 3.5 | 2.5 |
| ne | 4.3 | 2.5 |
| w | 2.7 | 1.5 |
| c | 3.5 | 1.5 |
| e | 4.3 | 1.5 |
| sw | 2.7 | 0.5 |
| s | 3.5 | 0.5 |
| se | 4.3 | 0.5 |

### 物料区（备货槽位，按顺序分配）

| 位置 | x | y |
|------|---|---|
| 顶排左 | 1.6 | 2.6 |
| 顶排中 | 0.9 | 2.6 |
| 顶排右 | 0.2 | 2.6 |
| 中排左 | 1.6 | 2.1 |
| 中排中 | 0.9 | 2.1 |
| 中排右 | 0.2 | 2.1 |
| 底排左 | 1.6 | 1.6 |
| 底排中 | 0.9 | 1.6 |
| 底排右 | 0.2 | 1.6 |

## 常见问题

**Q: 启动报 `ModuleNotFoundError: No module named 'rclpy'`**

需要先 source ROS2 环境：`source /opt/ros/jazzy/setup.bash`

**Q: 启动报 `ImportError: cannot import name 'ExecuteTrajectory' from 'robot_interfaces.srv'`**

需要 source 本地 workspace：`source install/setup.bash`

**Q: 前端访问后端 API 跨域报错**

后端已启用 flask-cors，确认 Vite 代理配置中的后端端口（默认 5000）与后端监听端口一致。

**Q: 机器人无响应**

检查 DDS 配置是否与机器人端一致（`RMW_IMPLEMENTATION`、`ROS_DOMAIN_ID`），确认双方在同一局域网且 ROS2 节点可互相发现。
