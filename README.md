# phi_robot_demo

方块搬运机器人控制系统 - 包含前端、后端、模拟环境。

## 启动方式

### 方式一：内嵌模式（模拟环境与 API 同进程）

```bash
# 安装依赖
uv pip install --python .venv/bin/python -r requirements.txt

# 启动（模拟环境内嵌）
.venv/bin/python run_phi_robot_api.py --host 0.0.0.0 --port 5000
```

访问 http://127.0.0.1:5000/api/health 检查后端健康状态。

### 方式二：分离模式（模拟环境独立进程）

```bash
# 终端 1: 启动模拟环境
.venv/bin/python -m phi_robot.adapters.unitree_sim --port 8080 &

# 终端 2: 启动 API（连接远程模拟环境）
.venv/bin/python run_phi_robot_api.py --host 0.0.0.0 --sim-url http://127.0.0.1:8080
```

### 启动前端

```bash
cd phi_robot_fronted
npm install
npm run dev
```

访问 http://127.0.0.1:4173 使用九宫格锁屏界面。

## 目录结构

```
phi_robot/          # 后端 Python 包
  adapters/         # 模拟/真机适配器
  api_server.py     # Flask REST API 服务
  mission_planner.py # 任务规划器
  mission_runner.py  # 任务执行引擎
  mission_service.py # 任务生命周期管理
  fake_robot_service.py # 内置模拟环境
  models.py         # 数据模型
phi_robot_fronted/  # 前端 React/Vite 项目
  src/
    App.jsx         # 主界面（九宫格锁屏）
    api.js          # API 客户端
run_phi_robot_api.py # 启动脚本

## Node.js (user-local) 安装说明

如果你没有系统权限安装 Node.js，可以使用 `nvm` 在用户目录安装 Node.js（无 sudo）：

```bash
# 安装 nvm（用户级）
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.5/install.sh | bash

# 使 nvm 可用（或关闭并重新打开终端）
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

# 安装最新 LTS
nvm install --lts
nvm alias default lts/*

# 验证
node -v
npm -v
```

在安装并激活后，进入 `phi_robot_fronted` 安装前端依赖并启动：

```bash
cd phi_robot_fronted
npm install
npm run dev
```

```
