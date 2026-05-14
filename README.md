# phi_robot_demo

方块搬运机器人控制系统 - 包含前端、后端、模拟环境。

## 启动方式

### 方式一：内嵌模式（模拟环境与 API 同进程）

```bash
# 安装依赖
pip install flask flask-cors

# 启动（模拟环境内嵌）
python run_phi_robot_api.py
```

访问 http://127.0.0.1:5000/api/health 检查后端健康状态。

### 方式二：分离模式（模拟环境独立进程）

```bash
# 终端 1: 启动模拟环境
python -m phi_robot.adapters.unitree_sim --port 8080 &

# 终端 2: 启动 API（连接远程模拟环境）
python run_phi_robot_api.py --sim-url http://127.0.0.1:8080
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
```
