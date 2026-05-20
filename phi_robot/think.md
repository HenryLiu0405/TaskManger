# 状态机驱动方案（phi_robot）

本方案面向“前端滑动编排 -> 后端状态机 -> 仿真/真实机器人执行”的闭环，不依赖 LLM。
前端只负责收集用户滑动顺序和目标方位，后端负责任务标准化、路径规划、工具执行、重规划和结果回传。

## 1. 输入

### 1.1 用户输入
- 用户在前端九宫格锁屏组件中完成滑动，得到一条有顺序的节点路径。
- 每个节点对应一个放置目标位，节点顺序就是任务执行顺序。
- 用户点击“运行”按钮后，前端将当前路径、已选目标和场景版本提交给后端。
- “强制暂停”保留入口，后端可据此中止或挂起当前状态机。

### 1.2 场景输入
- 放置区：3 x 3 米，9 个固定放置位，对应九宫格 9 个目标方位。
- 备货区：1.5 x 1.5 米，9 个待搬运方块。备货区的 9 个方块坐标由后端场景配置固定维护，前端不直接上报这些坐标。
- 机器人初始位姿、夹爪状态、障碍物、可达性约束、允许的安全距离。
- 场景配置应可版本化，保证同一输入可以重复回放。

### 1.3 提交包
前端提交给后端的输入应是一个结构化任务包，而不是散乱的 UI 状态。建议至少包含以下字段：

```json
{
	"request_id": "req-uuid",
	"scene_id": "scene-001",
	"goal_id": "goal-uuid",
	"scene_version": "scene-v1",
	"stock_layout_version": "stock-v1",
	"path": ["nw", "n", "ne"],
	"destination_order": ["nw", "n", "ne"],
	"board_mapping": {
		"nw": {"x": 0.5, "y": 2.5},
		"n": {"x": 1.5, "y": 2.5}
	},
	"options": {
		"max_replans": 2,
		"timeout_s": 20,
		"safety_first": true
	}
}
```

## 2. 数据模型

### 2.1 场景模型
- `SceneConfig`：一次任务运行所需的完整场景配置。
- `GridCell`：放置区九宫格中的一个位置，包含编号、坐标、朝向和可达性信息。
- `StockSlot`：后端固定维护的备货槽位，包含编号、坐标、顺序和是否已消耗。
- `StockItem`：备货区中的一个方块实体，包含编号、所属槽位、是否已取走、是否可抓取。备货区槽位表由后端 SceneConfig 提供。

### 2.2 任务模型
- `MissionTask`：一次“取一个方块 -> 放到一个目标位”的最小任务单元。
- `MissionPlan`：由多个 `MissionTask` 按顺序组成的执行计划。
- `PlanStep`：具体的工具执行步骤，例如 `move_to`、`pick`、`place`。

### 2.3 运行态模型
- `RobotState`：机器人当前位姿、夹爪状态、是否持物、当前所处阶段。
- `ExecutionContext`：当前 goal、当前步骤、失败次数、重规划次数、暂停状态、备货槽位指针。
- `ExecutionRecord`：每一步执行后的结构化记录，用于日志、回放和验收。
- `MissionResult`：一次任务运行的最终结果，包括成功/失败、原因、轨迹和最终状态。

### 2.4 状态机定义
推荐使用确定性的任务状态机，而不是基于 LLM 的自由规划。

- `IDLE`：未接收任务。
- `READY`：任务已提交，等待运行。
- `PLANNING`：根据输入生成任务计划。
- `EXECUTING_PICK`：移动到备货区并执行抓取。
- `EXECUTING_PLACE`：移动到放置区并执行放置。
- `REPLANNING`：因失败或外部事件重算剩余任务。
- `PAUSED`：收到暂停指令，等待恢复或终止。
- `COMPLETED`：所有任务完成。
- `FAILED`：任务失败且无法恢复。
- `ABORTED`：安全事件或强制终止。

状态迁移应由规则驱动，例如：
- `READY -> PLANNING -> EXECUTING_*`
- `EXECUTING_* -> REPLANNING`
- `EXECUTING_* -> COMPLETED`
- `ANY -> ABORTED`

## 3. 执行流程

### 3.1 前端采集
1. 用户在九宫格锁屏组件中完成滑动。
2. 前端记录滑动顺序、目标点集合和当前草稿状态。
3. 用户点击“运行”，前端把任务包提交给后端。

### 3.2 后端标准化
1. 后端校验提交包是否合法，检查目标点是否完整、是否存在重复或缺失。
2. 将前端路径转换成标准 `MissionPlan`。
3. 根据场景配置加载放置区、备货区和机器人初始状态，其中备货区坐标表由后端固定提供。
4. 写入本次运行的 `goal_id`、`request_id` 和场景版本。

### 3.3 计划生成
1. 状态机按用户给定的目标顺序逐个展开任务。
2. 对每一个放置目标，按后端维护的备货位队列选择“当前该取哪个方块”，默认按槽位顺序消费第 1 个、第 2 个……第 9 个备货位。
3. 代价函数建议至少包含：
	 - 机器人当前位姿到备货区的距离。
	 - 备货区到放置区目标位的距离。
	 - 障碍物/不可达惩罚。
	 - 重规划惩罚。
4. 如果业务要求“顺序优先”，则只在当前目标下做局部最优，不改变用户给定的目标顺序。
5. 备货区坐标不参与前端决策，后端只根据固定槽位表和当前消耗游标生成抓取目标。

### 3.4 执行循环
对每个 `MissionTask`，状态机执行固定动作链：
1. 根据当前备货槽位指针读取后端固定坐标，`move_to` 对应备货槽位。
2. `pick` 抓取方块。
3. `move_to` 放置区对应方位。
4. `place` 放下方块。
5. 标记当前备货槽位已消耗，并在放置成功后推进备货槽位指针，刷新 `RobotState` 与剩余任务列表。
6. 进入下一任务，直到队列耗尽。

### 3.5 结果回传
- 每一步执行都要返回结构化结果，供前端展示当前进度。
- 前端至少应能看到：当前步骤、已完成数、失败原因、剩余任务和最终状态。
- 任务结束后返回完整轨迹，便于回放和验收。

## 4. 重规划

### 4.1 触发条件
- `move_to` 返回 `NOT_REACHABLE`。
- `place` 返回 `OBSTRUCTED`。
- `pick` 返回 `GRIP_FAIL` 或 `TIMEOUT`。
- 输入任务发生变更，例如用户重新滑动、目标顺序调整、优先级改变。
- 监控器检测到外部事件，例如新指令、心跳异常或安全告警。

### 4.2 处理策略
- `safety_alert`：直接进入 `ABORTED`，不继续重规划。
- `GRIP_FAIL` / `TIMEOUT`：允许在当前步骤内有限重试，重试仍失败则失败退出。
- `NOT_REACHABLE`：插入中间安全点或换一条可达路径，再重试原步骤。
- `OBSTRUCTED`：优先尝试备用放置位；如果没有备用位，则重算当前任务。
- `new_instruction` / `priority_change`：只重算剩余任务，不回滚已完成部分。

### 4.3 限制条件
- 重规划次数必须受限，建议默认上限为 2 次。
- 每次重规划都要记录触发原因、改写前后的步骤差异和最终结果。
- 如果连续重规划仍无法收敛，则进入 `FAILED` 并上报原因。

## 5. 接口

### 5.1 前端到后端
建议新增或收敛为以下业务接口：
- `POST /api/mission/submit`：提交九宫格滑动结果和场景信息。
- `POST /api/mission/:id/run`：开始执行状态机。
- `POST /api/mission/:id/pause`：暂停当前任务。
- `POST /api/mission/:id/resume`：恢复执行。
- `POST /api/mission/:id/reset`：重置本次任务。
- `GET /api/mission/:id`：查询当前状态、日志和进度。

建议返回统一结构：

```json
{
	"request_id": "req-uuid",
	"status": "running",
	"current_step": "pick",
	"completed": 3,
	"total": 9,
	"error_code": null,
	"message": "executing",
	"state": {
		"robot_pose": {"x": 1.2, "y": 0.3, "z": 0.0, "theta": 0.0},
		"holding": "box-02"
	}
}
```

### 5.2 后端到工具层
当前工具层已经具备以下原语：
- `move_to`
- `pick`
- `place`
- `get_pose`
- `get_gripper_state`

这些工具应继续作为唯一执行边界，状态机不能直接操作底层仿真或硬件。
备货区坐标表属于后端场景配置，不应作为前端工具输入的一部分。

### 5.3 后端到模拟环境
当前仿真环境可复用以下接口：
- `POST /api/command`：执行一个工具请求。
- `GET /api/snapshot`：读取当前仿真状态。

这层接口已经适合做确定性回放、故障注入和回归测试。

### 5.4 需要补齐的接口
- 任务级 API 目前还缺真正的业务入口。
- 前端到后端还缺数据同步层和执行状态推送层。
- 如果后续要接真实机器人，还需要真实 adapter 的统一协议实现。
- 后端还需要明确暴露场景版本与备货槽位版本，保证固定槽位表可回放、可审计。

### 5.5 接口实现方案
#### 5.5.1 服务拆分
- `MissionApiService`：对外暴露 HTTP 接口，负责参数校验、鉴权、幂等和返回包装。
- `MissionService`：承接任务提交、启动、暂停、恢复、重置、查询等业务动作。
- `MissionPlanner`：基于 `scene_version` 和 `stock_layout_version` 生成 `MissionPlan`，负责把九宫格路径展开成可执行步骤。
- `MissionRunner`：后台执行器，按状态机逐步调用工具层，并在每一步后写入状态和事件。
- `MissionStore`：保存任务主记录、步骤记录、事件流、最终结果和审计信息。
- `MissionEventHub`：向前端推送执行事件，优先使用 WebSocket，必要时可退化为 SSE 或轮询。
- `RobotAdapter`：统一适配器接口，必须实现 `execute` 和 `snapshot`。
- `SimRobotAdapter`：对接 `UnitreeSimBackend` 或 `FakeRobotService`，用于仿真和回放。
- `RealRobotAdapter`：对接真实机器人控制层，但保持和仿真层一致的输入输出协议。

#### 5.5.2 核心数据结构
建议把接口实现收敛为以下四类对象，避免 API、状态和执行逻辑相互耦合：

```json
{
	"mission_id": "mission-uuid",
	"request_id": "req-uuid",
	"scene_id": "scene-001",
	"scene_version": "scene-v1",
	"stock_layout_version": "stock-v1",
	"status": "ready",
	"current_step": null,
	"current_task_index": 0,
	"current_stock_slot_index": 0,
	"pause_requested": false,
	"abort_requested": false,
	"plan": [],
	"state": {},
	"metrics": {},
	"last_error": null
}
```

- `MissionRecord`：任务主记录，表示一个任务从提交到完成的全生命周期。
- `MissionEvent`：事件记录，包含 `event_id`、`type`、`timestamp`、`step_id`、`payload`。
- `ToolCommand`：工具调用命令，字段至少包括 `tool`、`args`、`request_id`、`goal_id`、`step_id`。
- `ToolResult`：工具返回结果，字段至少包括 `status`、`error_code`、`message`、`state`、`metrics`。

事件类型建议固定为以下几类：`mission.created`、`mission.started`、`step.started`、`step.succeeded`、`step.failed`、`replan.started`、`replan.completed`、`mission.paused`、`mission.resumed`、`mission.completed`、`mission.failed`、`mission.aborted`。

#### 5.5.3 HTTP 接口契约
实现时建议前端只通过 HTTP 发起控制动作，通过推送通道订阅状态，不要直接调用工具层。

- `POST /api/mission/submit`
	- 作用：提交九宫格路径和场景版本，创建一个新的任务记录。
	- 输入：`request_id`、`scene_id`、`goal_id`、`scene_version`、`stock_layout_version`、`path`、`destination_order`、`board_mapping`、`options`。
	- 行为：校验字段、检查版本、生成任务主记录、生成初始计划草案、返回 `mission_id`。
	- 幂等：如果相同 `request_id` 已存在，直接返回已有任务记录，不重复建单。

- `POST /api/mission/:id/run`
	- 作用：把 `READY` 状态的任务切换到执行态。
	- 行为：创建后台执行协程或 worker，按状态机驱动 `MissionRunner`。
	- 返回：立即返回 `running` 和当前快照，不等待整个任务结束。
	- 约束：如果任务已经在运行，返回当前状态；如果状态非法，返回冲突错误。

- `POST /api/mission/:id/pause`
	- 作用：请求暂停当前任务。
	- 行为：写入 `pause_requested=true`，runner 在每个步骤边界检查该标志。
	- 语义：暂停不是立刻中断正在进行的底层工具调用，而是保证当前步骤结束后停下。

- `POST /api/mission/:id/resume`
	- 作用：恢复暂停的任务。
	- 行为：清除 `pause_requested`，唤醒 runner 继续执行未完成步骤。

- `POST /api/mission/:id/reset`
	- 作用：清空当前运行态并回到可重新执行的初始状态。
	- 行为：保留提交记录和审计记录，清掉运行中的 worker、临时状态和步进指针。
	- 建议：只允许在 `paused`、`completed`、`failed` 或 `aborted` 状态下重置。

- `GET /api/mission/:id`
	- 作用：查询任务当前快照。
	- 返回：`status`、`current_step`、`current_task_index`、`remaining_tasks`、`state`、`metrics`、`last_error`、`updated_at`。
	- 使用场景：页面首次加载、断线重连、调试回放。

- `GET /api/mission/:id/events`
	- 作用：获取增量事件流。
	- 推荐实现：优先 WebSocket `/api/mission/:id/stream`，如果当前基础设施不方便，也可以先用 SSE。
	- 客户端恢复：前端保存 `last_event_id`，重连后从该点继续拉取。

#### 5.5.4 推送和查询策略
- 状态查询采用“快照 + 事件流”双通道模式：`GET /api/mission/:id` 返回完整快照，`/stream` 推送增量事件。
- 每次状态变化都要先落库再推送，避免前端看到的进度和后端审计记录不一致。
- 推送消息建议统一包含 `mission_id`、`event_id`、`type`、`step_id`、`status`、`timestamp`、`payload`。
- 前端页面先拉快照，再订阅事件流；若事件流断开，再回到快照补齐状态。

#### 5.5.5 工具层和适配器对接
- `MissionRunner` 不能直接操作机器人 SDK，只能通过 `RobotAdapter.execute` 调用工具层。
- `RobotAdapter.execute` 的统一入参为 `tool`、`args`、`request_id`、`goal_id`、`step_id`。
- `RobotAdapter.snapshot` 用于查询当前位姿、夹爪状态、持物状态和环境状态。
- 仿真环境继续复用 `POST /api/command` 和 `GET /api/snapshot`，因此 `SimRobotAdapter` 只需把统一命令翻译成这两个接口即可。
- 真实机器人适配器也必须保持相同协议，这样上层任务流程无需知道底层是仿真、真机还是回放。

#### 5.5.6 执行时序
1. 前端提交任务包，后端创建 `MissionRecord` 并返回 `mission_id`。
2. 用户点击运行后，后端把任务放入 `MissionRunner`。
3. `MissionPlanner` 依据 `destination_order` 和固定备货槽位生成执行计划。
4. `MissionRunner` 逐步执行 `move_to -> pick -> move_to -> place`。
5. 每一步执行后更新 `MissionRecord`，并向 `MissionEventHub` 发出事件。
6. 如果出现 `NOT_REACHABLE`、`GRIP_FAIL`、`OBSTRUCTED`、`TIMEOUT` 或安全事件，先写事件，再按规则决定重试、重规划、暂停或终止。
7. 任务结束后写入最终结果、完整轨迹和审计信息，供回放与验收使用。

#### 5.5.7 错误处理和状态迁移
- `READY -> RUNNING`：运行启动成功。
- `RUNNING -> PAUSED`：收到暂停请求并在步骤边界停下。
- `RUNNING -> REPLANNING`：发生可恢复错误，需要重算剩余任务。
- `RUNNING -> ABORTED`：发生安全告警或强制终止。
- `RUNNING -> COMPLETED`：所有计划步骤成功执行完毕。
- `RUNNING -> FAILED`：错误超过重规划次数上限，或底层适配器返回不可恢复错误。
- `move_to` 不可达时优先重规划路径，`pick` 失败时优先有限重试，`place` 阻挡时优先尝试备选位或局部重算。
- 每个失败事件都要附带 `error_code`、`message`、`step_id`、`latency_ms`、`retry_count`，这样前端和审计都能看懂问题。

#### 5.5.8 实现顺序
建议按下面顺序落地，减少一次性改动带来的耦合：
1. 先实现 `MissionRecord`、`MissionEvent` 和 `MissionStore`，把任务状态落下来。
2. 再实现 `POST /api/mission/submit` 和 `GET /api/mission/:id`，先让前端能提交和查看快照。
3. 接着实现 `POST /api/mission/:id/run`、`pause`、`resume`、`reset` 和后台 `MissionRunner`。
4. 然后接入 `MissionEventHub`，把执行事件实时推到前端。
5. 再把 `SimRobotAdapter` 对接到 `UnitreeSimBackend` / `FakeRobotService`。
6. 最后补 `RealRobotAdapter`，保持和仿真层完全一致的接口和事件格式。

## 6. 验收

### 6.1 功能验收
- 用户完成九宫格滑动后，后端能准确收到顺序和目标方位。
- 状态机可以按顺序完成“取件 -> 搬运 -> 放置 -> 下一步”的闭环。
- 所有任务完成后，结果可回传到前端并可回放。
- 暂停、恢复、重置能够正确改变运行状态。

### 6.2 场景验收
- Happy Path：9 个方块按序全部搬运完成。
- 抓取失败：可重试或在限制内重规划成功。
- 放置阻挡：可切换备用目标位或失败退出。
- 不可达：可插入中间点再执行。
- 安全告警：必须立即中止，不允许继续执行。
- 固定输入回放：同样输入必须得到同样轨迹。

### 6.3 指标验收
- Task Success Rate 达到可接受阈值，建议首版不低于 90%。
- Replan Recovery Rate 达到可接受阈值，建议首版不低于 70%。
- Safety Violation Count 必须为 0。
- Mean Decision Latency 满足交互要求，建议低于 1500 ms。
- Deterministic Replay Pass Rate 必须为 100%。

### 6.4 当前项目结构判断
- 已具备：工具层、仿真后端、监控与重规划骨架、回归测试文档。
- 还缺少：统一的任务级后端 API、前后端联通层、状态推送机制、真实机器人 adapter、九宫格输入与机器人放置区坐标映射表。
- 前端当前更像独立交互原型，后端当前更像执行与验证底座，两者还没有形成完整产品链路。