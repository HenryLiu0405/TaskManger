# 资源索引（仅针对 phi_robot）：各层对应实现与 Phase1 流程

本文件仅列出 `phi_robot` 仓库内与机器人演示（Phase1..Phase4）相关的实现位置，便于快速定位与回放测试。

---

## 各层与对应文件（phi_robot 内）

- **输入层 (Ingest)**：接收外部指令并进入示例驱动或回放流程。
	- 实现文件（phi_robot）：示例驱动与入口位于 [phi_robot/phase1_runner.py](phi_robot/phase1_runner.py#L1)、[phi_robot/phase3_runner.py](phi_robot/phase3_runner.py#L1) 与 [phi_robot/phase4_runner.py](phi_robot/phase4_runner.py#L1)。兼容层在 [phi_robot/nanobot_compat.py](phi_robot/nanobot_compat.py#L1)。

- **规划层 (Planner)**：构建上下文并由示例驱动请求计划（phi_robot 使用兼容层与 runner 驱动演示）。
	- 实现文件（phi_robot）：示例驱动和上下文整合位于 [phi_robot/phase1_runner.py](phi_robot/phase1_runner.py#L1) 与 [phi_robot/phase4_runner.py](phi_robot/phase4_runner.py#L1)。兼容/桥接逻辑在 [phi_robot/nanobot_compat.py](phi_robot/nanobot_compat.py#L1)。

- **记忆 (Memory)**：当前在演示中由兼容层或上游模块提供（如已 vendored则位于 vendor 下）。查看兼容入口：[phi_robot/nanobot_compat.py](phi_robot/nanobot_compat.py#L1)。

- **工具子系统 (Tool Subsystem)**：工具抽象、参数校验、超时、重试与结构化日志。
	- 实现文件（phi_robot）：工具基类与注册已 vendored 为 [phi_robot/vendor/nanobot/agent/tools/base.py](phi_robot/vendor/nanobot/agent/tools/base.py#L1) 与 [phi_robot/vendor/nanobot/agent/tools/registry.py](phi_robot/vendor/nanobot/agent/tools/registry.py#L1)。具体机器人工具与后端适配实现： [phi_robot/robot_tools.py](phi_robot/robot_tools.py#L1)。

- **执行器 / Runner (Executor)**：按计划执行工具调用并做重试/重规划决策（phi_robot 提供回放/验证 runner）。
	- 实现文件（phi_robot）：回放与场景驱动见 [phi_robot/phase1_runner.py](phi_robot/phase1_runner.py#L1)、[phi_robot/phase3_runner.py](phi_robot/phase3_runner.py#L1)、[phi_robot/phase4_runner.py](phi_robot/phase4_runner.py#L1)。

- **监控 Hook (Monitor / Hooks)**：在生命周期点拦截并注入事件（安全/优先级等）。
	- 实现文件（phi_robot）：监控实现见 [phi_robot/phase3_monitor.py](phi_robot/phase3_monitor.py#L1)。兼容/抽象入口见 [phi_robot/nanobot_compat.py](phi_robot/nanobot_compat.py#L1)。

- **重规划 / 心跳 (Replanning / Heartbeat)**：周期或事件触发的重新规划与限次策略（phi_robot 使用兼容层或本地实现协同）。
	- 实现文件（phi_robot）：心跳与监控协同见 [phi_robot/phase3_monitor.py](phi_robot/phase3_monitor.py#L1)。如已 vendored，心跳相关模块位于 `phi_robot/vendor/nanobot/...`。

- **安全与控制服务 (RobotCtrl / Fake Robot Service)**：低层执行后端、世界状态与故障注入。
	- 实现文件（phi_robot）：仿真后端与适配器位于 [phi_robot/fake_robot_service.py](phi_robot/fake_robot_service.py#L1) 与 [phi_robot/robot_tools.py](phi_robot/robot_tools.py#L1)。

参考（phi_robot 文档与报告）：
- [phi_robot/docs/robot-agent-architecture.md](phi_robot/docs/robot-agent-architecture.md#L1)
- [phi_robot/docs/plan.md](phi_robot/docs/plan.md#L1)
- Phase 验收报告： [phi_robot/docs/phase1.md](phi_robot/docs/phase1.md#L1)、[phi_robot/docs/phase4.md](phi_robot/docs/phase4.md#L1)

---

## Phase1（三个动作 `move_to` / `pick` / `place`）—— 每层工作流程示例（phi_robot 视角）

示例场景：把 `box-42` 从 A 点移到 B 点，以下以 `phi_robot` 中的组件为参考：

1) 输入层
- 场景开始：在 phi_robot 中，通过运行回放/示例 runner（例如 [phi_robot/phase1_runner.py](phi_robot/phase1_runner.py#L1)）触发一次任务执行。

2) 规划层
- `ContextBuilder`（通过 runner 汇总世界状态）向 Planner/LLM 请求 Plan，返回步骤序列（由示例驱动触发）。相关实现和入口在 [phi_robot/phase1_runner.py](phi_robot/phase1_runner.py#L1) 与 [phi_robot/nanobot_compat.py](phi_robot/nanobot_compat.py#L1)。

3) 记忆层
- 演示/回放中，历史摘要与记忆由兼容层或 vendored 模块提供；兼容入口：[phi_robot/nanobot_compat.py](phi_robot/nanobot_compat.py#L1)。

4) 工具子系统
- Runner 将每个步骤映射到工具（由 `ToolRegistry` 提供），工具实现与注册位于 [phi_robot/vendor/nanobot/agent/tools/registry.py](phi_robot/vendor/nanobot/agent/tools/registry.py#L1) 与 [phi_robot/robot_tools.py](phi_robot/robot_tools.py#L1)。

5) 执行器 / Runner
- 顺序执行 Plan 步骤：`move_to(A)` -> `pick(box-42)` -> `place(B)`，错误码与重试/重规划策略由 runner/工具层共同处理（runner 入口见 [phi_robot/phase*_runner.py](phi_robot/phase1_runner.py#L1)）。

6) 监控 Hook
- 在 `before_execute_tools` / `after_iteration` 等挂接点，phi_robot 的监控实现（[phi_robot/phase3_monitor.py](phi_robot/phase3_monitor.py#L1)）会检查安全约束并可注入中断或替代步骤。

7) 重规划 / 心跳
- 心跳与监控协同负责触发 replanning；phi_robot 中的监控/心跳协作点位于 [phi_robot/phase3_monitor.py](phi_robot/phase3_monitor.py#L1)。

8) 安全与控制服务（Fake Robot）
- 工具层最终调用 `FakeRobotService` 执行动作、维护世界状态并提供故障注入；实现见 [phi_robot/fake_robot_service.py](phi_robot/fake_robot_service.py#L1)。

示例场景：`recoverable_grip_fail`，夹爪首次失败后通过重试恢复。

1) 输入层
- 场景开始：在 phi_robot 中，通过运行回放/示例 runner（例如 [phi_robot/phase1_runner.py](phi_robot/phase1_runner.py#L1)）触发一次任务执行。

2) 规划层
- `ContextBuilder` 读取当前状态并生成初始计划，通常仍是 `move_to(A)` -> `pick(box-42)` -> `place(B)`，但这次会预期 `pick` 可能需要重试。相关入口在 [phi_robot/phase1_runner.py](phi_robot/phase1_runner.py#L1) 与 [phi_robot/nanobot_compat.py](phi_robot/nanobot_compat.py#L1)。

3) 记忆层
- 记忆中可包含“上一次抓取失败”的历史摘要，用于解释为何本轮会出现恢复路径；兼容入口：[phi_robot/nanobot_compat.py](phi_robot/nanobot_compat.py#L1)。

4) 工具子系统
- `PickTool` 第一次调用返回抓取失败类错误，Runner 依据重试策略再次调用同一工具；相关工具注册与实现位于 [phi_robot/vendor/nanobot/agent/tools/registry.py](phi_robot/vendor/nanobot/agent/tools/registry.py#L1) 与 [phi_robot/robot_tools.py](phi_robot/robot_tools.py#L1)。

5) 执行器 / Runner
- 执行顺序为 `move_to(A)` -> `pick(box-42)`（失败）-> `pick(box-42)`（重试成功）-> `place(B)`；这一轮的重点是验证重试而不是重规划。回放入口见 [phi_robot/phase1_runner.py](phi_robot/phase1_runner.py#L1)。

6) 监控 Hook
- 监控层可记录本次失败与重试次数，便于在验收报告里输出“有重规划恢复的成功场景数”。实现见 [phi_robot/phase3_monitor.py](phi_robot/phase3_monitor.py#L1)。

7) 重规划 / 心跳
- 该场景通常只需要一次重试，不一定触发重规划；若重试策略耗尽，才会进入重规划分支。协同逻辑仍由 [phi_robot/phase3_monitor.py](phi_robot/phase3_monitor.py#L1) 体现。

8) 安全与控制服务（Fake Robot）
- `FakeRobotService` 在第一次抓取时注入失败，再在重试时恢复为成功，以验证恢复路径；实现见 [phi_robot/fake_robot_service.py](phi_robot/fake_robot_service.py#L1)。

示例场景：`blocked_place_replan`，放置目标被阻塞后重新规划到安全备用点。

1) 输入层
- 场景开始：由 [phi_robot/phase1_runner.py](phi_robot/phase1_runner.py#L1) 发起回放，进入与正常任务相同的执行链路。

2) 规划层
- `ContextBuilder` 会先生成标准三步计划，但在 `place(B)` 阶段会根据环境阻塞情况请求替代放置点；相关入口在 [phi_robot/phase1_runner.py](phi_robot/phase1_runner.py#L1) 与 [phi_robot/nanobot_compat.py](phi_robot/nanobot_compat.py#L1)。

3) 记忆层
- 记忆可保留“目标点阻塞”的历史摘要，帮助后续轮次直接规避同类目标；兼容入口：[phi_robot/nanobot_compat.py](phi_robot/nanobot_compat.py#L1)。

4) 工具子系统
- `MoveTool` 与 `PickTool` 正常完成后，`PlaceTool` 首次返回阻塞/不可达类错误；工具实现与注册位于 [phi_robot/vendor/nanobot/agent/tools/registry.py](phi_robot/vendor/nanobot/agent/tools/registry.py#L1) 与 [phi_robot/robot_tools.py](phi_robot/robot_tools.py#L1)。

5) 执行器 / Runner
- 执行顺序为 `move_to(A)` -> `pick(box-42)` -> `place(B)`（失败）-> 触发 replan -> `place(safe_drop_point)`；这一轮重点是验证重规划。回放入口见 [phi_robot/phase1_runner.py](phi_robot/phase1_runner.py#L1)。

6) 监控 Hook
- 监控层记录阻塞原因与重规划触发时机，以便验收报告展示“重规划次数”。实现见 [phi_robot/phase3_monitor.py](phi_robot/phase3_monitor.py#L1)。

7) 重规划 / 心跳
- 该场景会触发一次重规划，替换为安全的备用放置点；协同逻辑见 [phi_robot/phase3_monitor.py](phi_robot/phase3_monitor.py#L1)。

8) 安全与控制服务（Fake Robot）
- `FakeRobotService` 在放置时注入阻塞错误，并在重规划后接受新的安全放置点，验证恢复路径；实现见 [phi_robot/fake_robot_service.py](phi_robot/fake_robot_service.py#L1)。

---

## 快速引用（运行/检查）

运行 Phase1 回放：
```bash
python3 -m phi_robot.phase1_runner
```

生成 Phase4 日常回归报告：
```bash
python3 -m phi_robot.phase4_daily
```

---

## Phase1 验收场景（phi_robot）

这三个场景对应 [phi_robot/docs/phase1.md](phi_robot/docs/phase1.md#L1) 中的验收结果，便于快速对照回放行为与报告内容。

- **happy_path**：`Move box-42 from pickup pose to drop pose without failures.`
	- 预期结果：success
	- 实际结果：success
	- 任务状态：SUCCESS
	- 重规划次数：0
	- 重试次数：0
	- 工具轨迹：4 次调用

- **recoverable_grip_fail**：夹爪首次失败后通过重试恢复。
	- 预期结果：success
	- 实际结果：success
	- 任务状态：SUCCESS
	- 重规划次数：1
	- 重试次数：1
	- 工具轨迹：5 次调用

- **blocked_place_replan**：放置目标被阻塞后，agent 重新规划到安全的备用放置点。
	- 预期结果：success
	- 实际结果：success
	- 任务状态：SUCCESS
	- 重规划次数：1
	- 重试次数：0
	- 工具轨迹：5 次调用

---

如需我把该文件合并到主 README、或生成更详细的架构图（Mermaid），我可以继续处理。
