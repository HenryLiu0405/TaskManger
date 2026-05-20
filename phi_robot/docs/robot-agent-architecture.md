# 机器人 Agentic 架构（基于 nanobot 参考）

本文档面向开发者，说明如何基于 `nanobot` 的 agent 框架构建一个机器人相关的 agentic（包含规划、记忆、工具、监控、重规划）。文档包含：总体架构图、模块拆解、接口清单与实现建议。

## 概览架构图

```mermaid
flowchart TD
	User[用户指令]
	Sensors[传感器 / 状态]
	Ingest[输入层]
	Planner[规划器 (LLM)]
	Memory[记忆层 (可选)]
	TaskQueue[任务队列]
	Runner[执行器 / Runner]
	Tools[工具子系统]
	RobotCtrl[机器人控制服务]
	Monitor[监控 Hook]
	Replanner[重规划 / Heartbeat]
	Logger[日志 / Telemetry]

	User --> Ingest
	Sensors --> Ingest
	Ingest --> Planner
	Planner --> Memory
	Planner --> TaskQueue
	TaskQueue --> Runner
	Runner -->|调用| Tools
	Tools -->|高层命令| RobotCtrl
	RobotCtrl -->|反馈| Runner
	RobotCtrl -->|状态| Sensors
	Runner --> Monitor
	Monitor --> Planner
	Replanner --> Planner
	Runner --> Logger
	Monitor --> TaskQueue

	classDef infra fill:#f3f4f6,stroke:#aaa
	class Runner,Tools,RobotCtrl,Monitor,Replanner infra
```

## 模块说明（总体）
- 输入层（Ingest）
	- 功能：接收用户自然语言指令与传感器/状态流，将其标准化成统一的消息格式（`{role, content, meta}`）。
	- 对应 `nanobot`：消息总线(`MessageBus`)、InboundMessage。

- 规划层（Planner）
	- 功能：基于上下文与记忆调用 LLM 生成高层任务计划（分步动作/工具调用序列）。
	- 要点：使用 `ContextBuilder` 构造 prompt，调用 `AgentRunner` 发起多轮推理并接收 tool-call。

- 记忆层（Memory，可选）
	- 功能：保存任务摘要、环境简要、失败原因与用户偏好；提供短期/长期上下文注入。
	- 对应 `nanobot`：`MemoryStore` / `Dream` / `Consolidator`。

- 工具子系统（Tools）
	- 功能：将高层动作抽象为安全的原子工具（例如 `move_to`, `pick`, `place` 等）；每个工具负责参数校验、执行超时、错误返回和状态报告。
	- 对应 `nanobot`：`ToolRegistry`、`Tool` 抽象。建议把机器人动作做成异步工具实现，内部通过 RPC/GPIO/ROS 与 `RobotCtrl` 通信。

- 执行器 / Runner（Runner）
	- 功能：驱动规划-工具调用-注入的执行循环；负责 retries、空响应处理、工具结果注入、iteration 管理。
	- 对应 `nanobot.agent.runner.AgentRunner`。

- 监控 Hook（Monitor）
	- 功能：以 Hook 形式挂接到 Runner 的生命周期（before_iteration、before_execute_tools、after_iteration、on_stream 等），监听传感器告警与人工中断，将新指令注入到当前回合或中断并触发重规划。
	- 对应 `nanobot.agent.hook.AgentHook`。

- 重规划（Replanning / Heartbeat）
	- 功能：周期性检查任务进展（或当监控触发时）决定是否重新规划或人工接管；类似心跳服务会唤醒 Planner。
	- 对应 `nanobot.heartbeat.service.HeartbeatService`。

- 安全与控制服务（RobotCtrl）
	- 功能：真正与机器人底层对接的服务；实现运动学、碰撞检测、障碍回避、低级重试、急停。
	- 建议：以独立进程/服务暴露安全 RPC（HTTP/gRPC/ROS），Tools 与之通信。

## 详细接口清单（建议，JSON Schema / Type 风格）

1) 输入消息（统一格式）

```json
{
	"role": "user|system|sensor",
	"content": "...",
	"meta": {"timestamp": 0, "source": "voice|cli|vision", "pose": {"x":0,"y":0,"z":0}}
}
```

2) Planner 调用接口（伪 API）

- 方法：`Planner.plan(message_list, memory_summary=None) -> Plan`
- `Plan` 示例：

```json
{
	"id": "plan-uuid",
	"steps": [
		{"type":"tool_call","name":"move_to","args":{"x":1.2,"y":0.3}},
		{"type":"tool_call","name":"pick","args":{"object_id":"box-42"}},
		{"type":"tool_call","name":"move_to","args":{"x":2.0,"y":0.5}},
		{"type":"tool_call","name":"place","args":{"x":2.0,"y":0.5}}
	],
	"metadata": {"priority":"normal","timeout_s":600}
}
```

3) 工具接口（Tool signatures）

- `move_to({x, y, z, speed, timeout_s}) -> {status: ok|error, reason?, final_pose}`
- `pick({object_id, grip_force, timeout_s}) -> {status, success: bool, details}`
- `place({x, y, z, object_id, timeout_s}) -> {status, success, details}`
- `get_pose() -> {x,y,z,theta}`
- `get_gripper_state() -> {open|closed|force}`

工具应返回结构化结果并提供失败原因代码（例如 `NOT_REACHABLE`, `OBSTRUCTED`, `GRIP_FAIL`），Runner 根据失败类型选择重试、回滚或触发 replanning。

4) Monitor / Hook 接口

- Hook 方法（实现 `AgentHook`）：
	- `before_iteration(context)` — 每次迭代开始前，可检查外部事件；如果发现临时优先级指令，可以调用注入回调。
	- `before_execute_tools(context)` — 在即将执行工具前，进行安全校验 / 日志记录。
	- `after_iteration(context)` — 检查工具结果，触发报警或注入。

注入回调（Injection API）：Runner 在 `AgentRunSpec` 中支持 `injection_callback`，监控可通过该回调返回新的 `user` 消息注入到当前会话。

5) Replanner / Heartbeat 接口

- `Heartbeat.check()`：周期读取 `HEARTBEAT.md` 或任务队列，调用 Planner 判断是否需要执行（或继续），若需要调用 `on_execute(tasks_summary)` 执行。

## 实现建议（逐步落地）

1. 抽象并实现工具（核心工作，优先）：
	 - 在 `nanobot/agent/tools/` 下新增 `robot_tools.py`，定义 `MoveTool`, `PickTool`, `PlaceTool`，遵守 `Tool` 抽象（参数校验、cast 与 execute）。
	 - `execute()` 内部通过 RPC 调用独立的 `RobotCtrl` 服务（HTTP/gRPC/ROS）。

2. 实现 RobotCtrl（最小可运行守护进程）：
	 - 提供 `POST /move`, `POST /pick`, `POST /place`, `GET /pose`, `POST /stop`。
	 - 在开发初期，可用一个模拟器替代真实机器人（返回延迟与随机失败码以测试鲁棒性）。

3. 监控 Hook：
	 - 在 `nanobot/agent/hook.py` 基类上实现 `RobotMonitorHook`，在 `before_execute_tools` 检查安全条件、在 `after_iteration` 订阅传感器告警并通过 `injection_callback` 注入紧急指令。

4. 重规划策略：
	 - 紧急失败（如 `GRIP_FAIL`）由 Runner 触发即时重规划：把失败信息写回历史/内存并请求 Planner 生成替代步骤。
	 - 周期评估使用 Heartbeat：把任务队列摘要写进 `HEARTBEAT.md`，让心跳决定是否继续或重新规划。

5. 记忆策略（可选）：
	 - 只保存任务级别的摘要条目（例如“尝试抓取 box-42 失败，原因 OBSTRUCTED，在 x,y 附近有障碍”），并把原始视频/点云放外部存储。

## 示例：一次指令到执行的序列（伪流程）
1. 用户："把箱子从 A 点移到 B 点" → `Ingest` 标准化为 user 消息。
2. `Planner` 构造上下文（包含 map 摘要、机器人当前位置、任务偏好）并请求 LLM：返回 Plan（move_to A, pick box, move_to B, place）。
3. `Runner` 取出第一个步骤 `move_to A`，调用 `MoveTool.execute`。执行前 `RobotMonitorHook.before_execute_tools` 检查安全后放行。
4. `RobotCtrl` 返回状态 `ok final_pose`。Runner 继续下一步 `pick`。若 `pick` 返回 `GRIP_FAIL`，Runner 根据错误策略：
	 - 尝试 retry（限次数）→ 仍失败 → 将失败写入 memory/history 并触发 Planner 重新生成计划（注入失败信息）。
5. Planner 可能返回替代方案（例如先 reposition，再 pick），Runner 执行新的步骤，直到任务完成或人工接管。

## 开发注意事项与测试策略
- 模块化：将工具和 RobotCtrl 解耦，工具只做参数与结果封装。
- 仿真优先：先实现 RobotCtrl 的仿真后端（可用脚本模拟延迟与失败），再接真实硬件。
- 可观测性：在 `Runner` / Hook 中输出结构化事件（tool events），便于重放与调试。
- 安全优先：任何工具调用都必须有超时与软/硬停止策略。

## 推荐起步工程任务清单（最小可用 MVP）
1. 新建 `nanobot/agent/tools/robot_tools.py`，实现 `MoveTool`, `PickTool`, `PlaceTool`（stub -> 调用本地模拟 RobotCtrl）。
2. 新建 `tools/robot_ctrl_sim.py`（或单独服务）实现基本 HTTP 接口并在本地模拟成功/失败。
3. 添加 `RobotMonitorHook`，在 `AgentLoop` 初次启动时把它加入 `hooks` 列表。
4. 写一个 demo 指令脚本（调用 `nanobot agent` 或 `AgentRunner`）来演示一次完整从指令到执行的回路。

---

文档已生成并保存在 `docs/robot-agent-architecture.md`，需要我现在为你生成 `robot_tools.py` 与 `robot_ctrl_sim.py` 的最小可运行样例吗？

可以，完全可以把“真实运动控制”替换成可控的仿真层，你们只验证 agentic 闭环是否正确。

推荐一个最实用的临时方案：假机器人环境 + 假工具执行器 + 场景回放测试。

1. 做一个 Fake Robot Service（最关键）
- 提供和真实团队约定一致的接口：move_to、pick、place、get_pose、get_gripper_state。
- 但不连硬件，只维护内存里的状态：
  - 机器人位姿
  - 箱子位置与是否被抓取
  - 障碍物/不可达区域
- 返回结构化结果：ok、error_code、reason、final_state。

2. 让工具层调用 Fake Service（而不是底层控制）
- 你们的 MoveTool/PickTool/PlaceTool 保持不变，只把底层调用地址切到 fake endpoint。
- 这样以后切到真实服务只需要改配置，不改 agentic 逻辑。

3. 做“可注入失败”的仿真
- 支持配置故障场景：
  - 第 1 次 pick 必失败（GRIP_FAIL）
  - 某坐标 move 不可达（NOT_REACHABLE）
  - 随机超时（TIMEOUT）
- 目的：验证你们的监控、重规划、重试策略是否生效。

4. 用场景脚本做回归测试
- 每个测试用例固定输入：
  - 初始世界状态
  - 用户指令
  - 故障注入规则
- 断言输出：
  - 是否最终完成任务
  - 调用了哪些工具（顺序是否合理）
  - 失败后是否触发重规划
  - 是否在阈值内完成（如 2 次以内重规划）

5. 定义“agentic 成功”指标（建议先定 4 个）
- Task Success Rate：任务完成率
- Replan Recovery Rate：失败后恢复率
- Safety Violation Count：安全违规次数（应为 0）
- Mean Decision Latency：平均决策耗时

6. 先做三类最小测试集
- Happy Path：一次成功搬运
- Recoverable Failure：中途失败后重规划成功
- Unrecoverable Failure：连续失败后正确上报并停止

你们这个阶段最重要的是“决策闭环正确”，不是“控制精度”。  
所以只要 Fake Service 的接口契约和真实团队一致，你们的 agentic 验证就是有效的。

如果你愿意，我下一步可以直接给你一份“可执行的最小测试蓝图”，包括：
1. fake world 状态结构
2. 三个工具的统一返回协议
3. 6 条验收用例模板（可直接抄到测试代码里）

## 参考文件具体位置

### 核心 Agent 运行链路
- Agent 总循环: [nanobot/agent/loop.py](nanobot/agent/loop.py)
- 规划与工具执行循环: [nanobot/agent/runner.py](nanobot/agent/runner.py)
- 生命周期 Hook 抽象: [nanobot/agent/hook.py](nanobot/agent/hook.py)
- 上下文构建: [nanobot/agent/context.py](nanobot/agent/context.py)

### 工具系统
- 工具注册与执行入口: [nanobot/agent/tools/registry.py](nanobot/agent/tools/registry.py)
- 工具基类: [nanobot/agent/tools/base.py](nanobot/agent/tools/base.py)
- Shell 工具参考实现: [nanobot/agent/tools/shell.py](nanobot/agent/tools/shell.py)
- 文件读写工具参考实现: [nanobot/agent/tools/filesystem.py](nanobot/agent/tools/filesystem.py)

### 记忆与会话
- MemoryStore / Dream / Consolidator: [nanobot/agent/memory.py](nanobot/agent/memory.py)
- 会话管理: [nanobot/session/manager.py](nanobot/session/manager.py)

### 重规划与周期检查
- 心跳服务（周期评估）: [nanobot/heartbeat/service.py](nanobot/heartbeat/service.py)
- Cron 服务（定时任务）: [nanobot/cron/service.py](nanobot/cron/service.py)

### 消息与通道层
- 事件定义: [nanobot/bus/events.py](nanobot/bus/events.py)
- 消息队列总线: [nanobot/bus/queue.py](nanobot/bus/queue.py)
- 通道管理: [nanobot/channels/manager.py](nanobot/channels/manager.py)
- WebSocket 通道（可作外部接入参考）: [nanobot/channels/websocket.py](nanobot/channels/websocket.py)

### 配置与启动入口
- CLI 命令入口: [nanobot/cli/commands.py](nanobot/cli/commands.py)
- 配置 Schema: [nanobot/config/schema.py](nanobot/config/schema.py)
- 配置加载: [nanobot/config/loader.py](nanobot/config/loader.py)

### 与本方案配套文档
- 本架构文档: [phi_robot/robot-agent-architecture.md](phi_robot/robot-agent-architecture.md)
- 实施计划: [phi_robot/plan.md](phi_robot/plan.md)
- 项目总览: [README.md](README.md)
- 配置说明: [docs/configuration.md](docs/configuration.md)
