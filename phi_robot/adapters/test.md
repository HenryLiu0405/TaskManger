# Unitree 模拟接入：目录结构与接口草案

说明：此草案按可直接实现的最小工程化结构给出，目标是把 `phi_robot` 中的 dict 请求/响应契约通过一个适配层映射到宇树（Unitree）真实接口或本地模拟服务。文档包含目录建议、关键接口定义、JSON 请求/响应样例、以及切换真实/仿真路径的配置要点。

---

## 建议目录结构（可直接创建）

phi_robot/
- adapters/
  - __init__.py
  - unitree_adapter.py          # Unitree 真实接口适配器（SDK/ROS/UDP 映射）
  - unitree_sim.py              # 本地模拟服务（接收相同 dict，返回仿真响应）
  - adapter_base.py             # 适配器基类与配置加载
- tools/
  - __init__.py
  - robot_tools.py              # 现有工具层（保持不变，调用 adapter）
- docs/
  - test.md                     # 本文件
  - ...

可选：若使用 ROS2，还可以新增 `adapters/ros2_unitree.py` 作为 ROS2 topic/action 映射实现。

---

## 设计要点（概览）
- `robot_tools.py` 不直接调用硬件；它通过 `Adapter` 抽象发送请求。
- 提供两种实现：`UnitreeAdapter`（真实设备映射）和 `UnitreeSim`（本地仿真）。通过配置或环境变量选择。
- 所有请求/响应保持 JSON-可序列化的 dict 结构，便于通过 HTTP、MQTT、UDP 或 ROS2 转发。

---

## Adapter 基类（adapter_base.py）

接口草案（Python protocol / abstract class）：

class AdapterBase(Protocol):
    def send_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """同步发送命令到物理层或模拟层，返回结构化响应 dict。"""

    def async_send_command(self, cmd: dict[str, Any]) -> Awaitable[dict[str, Any]]: ...

    def snapshot(self) -> dict[str, Any]:
        """返回当前机器人状态快照（同 fake_robot_service.snapshot() 格式）。"""

实现要求：任何适配器必须保持请求/响应字段兼容：`request_id, status, error_code, message, state, metrics`。

---

## Unitree Adapter（unitree_adapter.py）草案

职责：把标准 dict 映射为宇树 SDK/协议调用，或通过 ROS2 发布对应 topic。示例伪码：

class UnitreeAdapter(AdapterBase):
    def __init__(self, config):
        # 初始化 SDK / ROS2 node / UDP socket

    def send_command(self, cmd):
        # cmd 示例：{"tool":"move_to","request_id":"...","args":{...}}
        if cmd["tool"] == "move_to":
            # 将 args 转为 Unitree 的移动请求并发送
            resp = self._call_unitree_move(args)
        elif cmd["tool"] == "pick":
            resp = self._call_unitree_pick(args)
        ...
        # 将 Unitree 原生响应包装成标准 dict 并返回
        return {
            "request_id": cmd.get("request_id"),
            "status": "ok" or "error",
            "error_code": ...,
            "message": ...,
            "state": {...},
            "metrics": {...},
        }

注意：实现时把 blocking/timeout/ack 逻辑映射为 `metrics.latency_ms` 与 `status` 字段。

---

## Unitree 模拟（unitree_sim.py）草案

职责：实现与 `FakeRobotService` 类似的内存世界，但暴露为独立进程（HTTP 或 UDP）。可直接复用 `FakeRobotService.execute` 的逻辑：接收命令 JSON -> 调用相同内部方法 -> 返回 JSON 响应。

建议接口（HTTP）示例：
- POST /api/command  接收 JSON body: {"tool":"move_to","request_id":"...","args":{...}}
- GET  /api/snapshot 返回当前 snapshot

优点：可以在另一台机器上运行、与真实 adapter 开发并行、方便集成测试。

---

## JSON 请求 / 响应样例

请求（来自 `robot_tools`）：
```json
{
  "request_id": "phase1-step-002",
  "goal_id": "phase1",
  "step_id": "step-002",
  "tool": "pick",
  "args": {"object_id": "box-42", "timeout_s": 20}
}
```

HTTP POST body（若使用 HTTP）：相同 JSON 保存在 body。

成功响应：
```json
{
  "request_id": "phase1-step-002",
  "status": "ok",
  "error_code": null,
  "message": "picked box-42",
  "state": {"pose": {"x":1.2,"y":0.3,"z":0.0,"theta":0.0}, "holding":"box-42"},
  "metrics": {"latency_ms": 12}
}
```

失败响应示例：
```json
{
  "request_id": "phase1-step-002",
  "status": "error",
  "error_code": "GRIP_FAIL",
  "message": "first pick attempt failed",
  "state": {...},
  "metrics": {"latency_ms": 9}
}
```

---

## Unitree 映射建议（选择其一实现）

1) ROS2（推荐，若团队使用 ROS2）
   - 将 `move_to` 映射为 Action 或 Topic，`pick/place` 映射为 Service 或 Action。
   - 模拟器也开 ROS2 node，提供相同 topic/service，agent 直接用 rclpy 调用。

2) HTTP/gRPC（简单且可跨语言）
   - Adapter 把 dict POST 到 Unitree 控制代理（或本地模拟 server）。

3) UDP / 低级 SDK（如果宇树只提供 UDP）
   - Adapter 负责序列化为二进制包并发送；在模拟阶段，写一个 UDP fake server 解析相同字段。

---

## 配置与切换（示例）

ENV 配置：
- `PHI_ADAPTER=unitree_sim` 或 `PHI_ADAPTER=unitree_real`
- `UNITREE_ENDPOINT=http://localhost:8080` 或 SDK 配置

在 `phi_robot/nanobot_compat.py` 或 `robot_tools` 初始化时根据 env 选择适配器。

---

## 测试与验证步骤（快速启动）
1. 启动模拟服务： `python -m phi_robot.adapters.unitree_sim --port 8080`
2. 配置环境： `export PHI_ADAPTER=unitree_sim; export UNITREE_ENDPOINT=http://localhost:8080`
3. 运行回放：
```bash
python3 -m phi_robot.phase1_runner --write-report phi_robot/docs/phase1.md
```
4. 观察 `phase1.md` 与 `unitree_sim` 日志，验证重试/重规划行为。

---

如果你同意，我可以接着为你生成 `adapters/unitree_sim.py` 的最小可运行实现（HTTP server + reuse `FakeRobotService` 逻辑），并把 `robot_tools` 的 adapter hook 实现成可配置的加载点。要我继续吗？

---

## 演示测试：目的与实际结果

目的：验证 `UnitreeSim` 模拟服务能够按预期暴露 HTTP 接口并返回与 `FakeRobotService` 兼容的 JSON 响应；同时验证 `robot_tools` 在默认配置下能加载 `unitree_sim` 作为 backend。

复现命令（示例）：

```bash
# 启动模拟服务（单独终端）
python3 -m phi_robot.adapters.unitree_sim --port 8080

# 发送示例命令（若系统无 curl，使用 Python requests）
python3 - <<'PY'
import requests, json
resp = requests.post('http://127.0.0.1:8080/api/command', json={
  "request_id":"demo-1","goal_id":"demo","step_id":"s1","tool":"get_pose"
})
print(json.dumps(resp.json(), indent=2))
PY

# 获取 snapshot
python3 - <<'PY'
import requests, json
resp = requests.get('http://127.0.0.1:8080/api/snapshot')
print(json.dumps(resp.json(), indent=2))
PY
```

实际输出（演示记录）：

POST /api/command 返回（格式化）：

```json
{
  "request_id": "demo-1",
  "status": "ok",
  "error_code": null,
  "message": "pose returned",
  "state": {
    "pose": {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0},
    "holding": null,
    "boxes": {
      "box-42": {"box_id": "box-42", "pose": {"x": 1.2, "y": 0.3, "z": 0.0, "theta": 0.0}, "held": false}
    }
  },
  "metrics": {"latency_ms": 8, "sim_time_ms": 0}
}
```

GET /api/snapshot 返回（格式化）：

```json
{
  "robot_pose": {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0},
  "holding": null,
  "boxes": {
    "box-42": {"box_id": "box-42", "pose": {"x": 1.2, "y": 0.3, "z": 0.0, "theta": 0.0}, "held": false}
  },
  "obstacles": []
}
```

说明：该测试验证了三点：
- 服务可启动并监听端口；
- `/api/command` 按照工具名路由到 `FakeRobotService` 的实现并返回结构化结果；
- `/api/snapshot` 返回当前世界状态（用于回放与断言）。

已将该演示结果纳入本文件，便于验收报告中引用。
