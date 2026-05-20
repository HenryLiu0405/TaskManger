# P0/P1 整改清单与解决方案

说明：
- P0 = 不修就无法形成稳定闭环，或会直接引入运行/安全/审计风险。
- P1 = 不修会明显削弱可靠性、可回放性或可维护性，但不一定立刻阻断 Demo。

---

## P0

### P0-1: 统一任务输入契约，收敛为单一真源

**现状问题**:
- 后端 `api_server.py` 接收 `scene_version` / `stock_layout_version` 但从未用于实际查表，始终用 `mission_planner.py` 里硬编码的 `GRID_CELLS` 和 `STOCK_SLOTS`。版本参数是假参数。
- `api_server.py:81` 每次都生成新的 `request_id`（`req-{uuid}`），导致 `MissionService.submit()` 里的幂等检查永远不会触发。

**解决方案**:

1. `mission_planner.py` — 将硬编码坐标改为版本查表
```python
# 新增函数
def get_grid_cells(version: str = "scene-v1") -> dict:
    # 当前只有 v1，后续版本在此扩展
    return GRID_CELLS  # 实际查表

def get_stock_slots(version: str = "stock-v1") -> list:
    return STOCK_SLOTS
```
`plan()` 方法改为接收 `scene_version` / `stock_layout_version` 参数并调用上述查表函数。

2. `api_server.py` — 修复幂等逻辑
- `api_server.py:81` 不再自补 `request_id`，改为要求前端传入。
- 前端 `api.js` 在 `submitMission` 里用 `destination_order` 做确定性 hash 生成 `request_id`。

3. `mission_service.py` — 去掉 `board_mapping` 参数（已无实际用途），提交接口只接受 `destination_order` + `scene_version` + `stock_layout_version`。

**涉及文件**: `api_server.py`, `mission_planner.py`, `mission_service.py`, `phi_robot_fronted/src/api.js`

---

### P0-2: 统一前端 Demo 场景和后端任务规模 — ✅ 已解决

前端 ZONE_MAP 完整覆盖 9 宫格（nw/n/ne/w/c/e/sw/s/se），后端 GRID_CELLS 9 个放置位，STOCK_SLOTS 9 个备货槽位，DEFAULT_FAKE_SPEC 9 个 box。旧 data.js/state.js（6宫格）已是死代码。

---

### P0-3: 补齐任务级后端 API — pause / resume / reset

**现状问题**:
- `POST /api/missions/<id>/pause` 端点存在但不调用 `MissionService.pause()`，只返回 JSON（空操作）。
- `POST /api/missions/<id>/resume` 端点不存在（MissionService 有 `resume()` 但未暴露 HTTP）。
- `POST /api/missions/<id>/reset` 端点不存在。
- 无 WebSocket/SSE 实时推送，前端只能用轮询。

**解决方案**:

1. `api_server.py` — 修复 pause，新增 resume 和 reset
```python
# pause — 改为实际调用 service
def pause_mission(mission_id):
    self.service.pause(mission_id)
    return jsonify({"mission_id": mission_id, "status": "paused", ...})

# resume — 新增，重新创建 runner 继续执行
def resume_mission(mission_id):
    self.service.resume(mission_id)
    hook = APIHook(mission_id)
    runner = MissionRunner(self.service, self.adapter, hook=hook)
    self.mission_hooks[mission_id] = hook
    self.active_runners[mission_id] = runner
    thread = threading.Thread(target=self._run_mission_sync, args=(mission_id, runner), daemon=True)
    thread.start()
    return jsonify({"mission_id": mission_id, "status": "running", ...})

# reset — 新增
def reset_mission(mission_id):
    self.active_runners.pop(mission_id, None)
    self.service.reset(mission_id)
    return jsonify({"mission_id": mission_id, "status": "ready", ...})
```

2. `api_server.py` — 新增 SSE 端点替代轮询
```python
@app.route("/api/missions/<mission_id>/stream", methods=["GET"])
def stream_mission(mission_id):
    def event_stream():
        q = self.event_hub.subscribe_sync(mission_id)
        while True:
            event = q.get()  # 阻塞等待
            if event is None:
                break
            yield f"data: {json.dumps(event.to_dict())}\n\n"
    return Response(event_stream(), mimetype="text/event-stream")
```

3. `phi_robot_fronted/src/api.js` — 新增 `resumeMission()` / `resetMission()` 方法，新增 SSE 订阅方法 `subscribeEvents()`。

**涉及文件**: `api_server.py`, `mission_event_hub.py`, `phi_robot_fronted/src/api.js`

---

### P0-4: 暂停/中止的执行层语义

**现状问题**:
- 暂停端点不调用 service，实际是空操作。
- 前端"强制暂停"实际调的是 API 的 abort，不是 pause。
- `MissionRecord` 定义了 `pause_requested` / `abort_requested` 字段，但没有任何代码设置或检查它们。
- `MissionRunner` 在线程中执行 `loop.run_until_complete()`，abort 只能等当前步骤结束后在循环顶部检查状态。如果工具调用卡住，无法中断。

**解决方案**:

1. `mission_runner.py` — 在每次迭代顶部检查标志
```python
# 循环内第一步：重新读 store，检查标志
record = self._service.get_mission(mission_id)
if record.abort_requested:
    self._service.mark_aborted(mission_id, "abort requested")
    break
if record.pause_requested:
    # 当前步骤完成后暂停
    self._service.pause(mission_id)
    break
```

2. `mission_service.py` — `pause()` 设置 `pause_requested=True`，`abort()` 设置 `abort_requested=True`。

3. `api_server.py` — abort 端点使用 `threading.Event` 实现可中断机制
```python
# run_mission 时创建 cancel_event
cancel_event = threading.Event()
self._cancel_events[mission_id] = cancel_event

# MissionRunner._execute_tool 检查 cancel_event
def _execute_tool(self, ...):
    if self._cancel_event.is_set():
        raise InterruptedError("cancelled")
    ...
```

4. `phi_robot_fronted/src/App.jsx` — 区分按钮行为：
- 强制暂停 → `api.pauseMission()`
- 运行中再次点击"运行中" → `api.abortMission()`

**涉及文件**: `mission_runner.py`, `mission_service.py`, `api_server.py`, `models.py`, `phi_robot_fronted/src/App.jsx`

---

### P0-5: 事件落库与推送的一致性边界

**现状问题**:
- 纯内存存储，服务重启后所有数据丢失。
- WebSocket 未接入 HTTP 层，前端只能用轮询。
- 事件无持久化，审计和回放不可靠。

**解决方案**:

1. `store.py` — 增加 JSON 文件持久化（零外部依赖）
```python
import json
from pathlib import Path

class MissionStore:
    def __init__(self, data_dir: str = None):
        self._data_dir = Path(data_dir or os.path.expanduser("~/.phi_robot/data"))
        self._data_dir.mkdir(parents=True, exist_ok=True)
        ...
    
    def _persist(self, mission_id: str) -> None:
        record = self._missions[mission_id]
        events = self._events.get(mission_id, [])
        filepath = self._data_dir / f"{mission_id}.json"
        tmp = filepath.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({
                "record": record.to_dict(),
                "events": [e.to_dict() for e in events]
            }, f, indent=2, default=str)
        tmp.rename(filepath)  # 原子写入
    
    # 在 create / update / put_event 后调用 _persist
```

2. `mission_event_hub.py` — 增加线程安全版事件中心用于 Flask SSE
```python
import queue

class SyncEventHub:
    """同步版事件中心，用于 Flask 线程环境"""
    def __init__(self, store):
        self._store = store
        self._queues: dict[str, list[queue.Queue]] = {}
    
    def publish(self, mission_id: str, event: MissionEvent) -> None:
        self._store.put_event(mission_id, event)  # 先落库
        for q in self._queues.get(mission_id, []):
            q.put(event)  # 再推送
    
    def subscribe(self, mission_id: str) -> queue.Queue:
        q = queue.Queue()
        self._queues.setdefault(mission_id, []).append(q)
        return q
```

3. `api_server.py` — 在 SSE 端点中使用 `SyncEventHub`，在 MissionRunner 每步执行后 publish 事件。

**涉及文件**: `store.py`, `api_server.py`, `mission_event_hub.py`

---

## P1

### P1-1: 补强仿真环境的可达性判断

**现状问题**: `FakeRobotService._move_to()` 和 `_place()` 只检查终点是否落在障碍矩形内，不检查整段轨迹。

**解决方案**:

`fake_robot_service.py` — 增加线段采样碰撞检测
```python
def _segment_intersects_obstacle(self, start: Pose, end: Pose, steps: int = 20) -> bool:
    """沿 start→end 线段均匀采样 steps 个点，任一落入障碍物即返回 True"""
    for i in range(1, steps):
        t = i / steps
        x = start.x + (end.x - start.x) * t
        y = start.y + (end.y - start.y) * t
        if self._inside_obstacle(Pose(x, y, 0, 0)):
            return True
    return False

def _move_to(self, ...):
    ...
    if self._segment_intersects_obstacle(self.world.robot_pose, target):
        return self._error_response(..., "NOT_REACHABLE", "path blocked by obstacle")
    ...
```

**涉及文件**: `fake_robot_service.py`

---

### P1-2: 细化重规划预算

**现状问题**: `APIHook.apply_replan_policy()` 只处理 `safety_alert` → abort，其他错误一律 continue。没有重规划计数器，没有预算限制。

**解决方案**:

1. `mission_runner.py` — 增加重规划状态跟踪
```python
class MissionRunner:
    def __init__(self, ...):
        self._replan_counts: dict[int, int] = {}   # task_index -> count
        self._max_replans_per_task = 2
    
    async def run(self, mission_id):
        ...
        if tool_result.status != "ok":
            task_idx = step.task_index
            current_replans = self._replan_counts.get(task_idx, 0)
            
            if error_code == "safety_alert":
                action = "abort"
            elif error_code in ("GRIP_FAIL", "TIMEOUT"):
                action = "retry"   # 同步骤重试，不消耗预算
            elif current_replans < self._max_replans_per_task:
                action = "replan"
                self._replan_counts[task_idx] = current_replans + 1
            else:
                action = "fail"    # 预算耗尽
```

2. `api_server.py` `APIHook.apply_replan_policy()` — 补充完整分类
```python
def apply_replan_policy(self, record, result, events=None):
    error_code = result.get("error_code", "")
    if error_code == "safety_alert":
        return ("abort", None)
    elif error_code in ("GRIP_FAIL", "TIMEOUT"):
        return ("retry", None)       # 同步骤内重试
    elif error_code in ("NOT_REACHABLE", "OBSTRUCTED"):
        return ("replan", None)      # 触发重规划
    elif error_code in ("INVALID_ARGS", "PRECONDITION_FAILED"):
        return ("abort", None)       # 配置错误，不可恢复
    return ("continue", None)
```

**涉及文件**: `mission_runner.py`, `api_server.py`

---

### P1-3: 补齐真实机器人 adapter 的协议收敛

**现状问题**: Protocol 已定义（`adapter_base.py`），仿真已实现（`unitree_sim.py`），但真机 adapter 是 `NotImplementedError` 桩。

**解决方案**:

1. 新建 `phi_robot/adapters/unitree_real.py`
```python
class UnitreeRealBackend:
    """真实 Unitree 机器人适配器，协议与 UnitreeSimBackend 完全一致"""
    def __init__(self, robot_host="192.168.123.10", port=29090):
        self._host = robot_host
        self._port = port
    
    def execute(self, tool, args, *, request_id, goal_id, step_id):
        # 将统一协议翻译为机器人 SDK 调用
        # 返回格式与 UnitreeSimBackend 完全一致
        ...
    
    def snapshot(self):
        # 从机器人读取当前位姿、夹爪状态
        ...
```

2. `adapters/__init__.py` — 完善工厂函数
```python
if adapter == "unitree_real":
    from .unitree_real import UnitreeRealBackend
    return UnitreeRealBackend(
        robot_host=os.environ.get("UNITREE_HOST", "192.168.123.10"),
        port=int(os.environ.get("UNITREE_PORT", "29090")),
    )
```

3. 上层代码（MissionRunner、api_server.py）完全不变 — 这就是协议收敛的目的。

**涉及文件**: `adapters/unitree_real.py`（新建）, `adapters/__init__.py`

---

### P1-4: 验收指标与实现约束绑定

**现状问题**: `MissionRecord.metrics` 是自由 dict，测试里手动计算 KPI，没有结构化绑定。

**解决方案**:

1. `models.py` — 新增结构化 metrics 数据类
```python
@dataclass
class MissionMetrics:
    total_tasks: int = 0
    succeeded_tasks: int = 0
    failed_tasks: int = 0
    total_steps: int = 0
    succeeded_steps: int = 0
    failed_steps: int = 0
    replan_count: int = 0
    retry_count: int = 0
    safety_alerts: int = 0
    total_latency_ms: float = 0.0
    
    @property
    def task_success_rate(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return self.succeeded_tasks / self.total_tasks
    
    @property
    def replan_recovery_rate(self) -> float:
        denom = self.failed_steps + self.replan_count
        if denom == 0:
            return 1.0
        return self.replan_count / denom
```

2. `mission_runner.py` — 在每次步骤执行后更新结构化 metrics，并将当前 metrics 快照写入事件 payload。

**涉及文件**: `models.py`, `mission_runner.py`

---

## 修复优先级与顺序

```
第1轮（闭环必须 — 立即）:
  P0-4  暂停/中止执行层语义    ← 影响安全和用户体验
  P0-3  补齐 resume/reset/SSE  ← 补完 API 缺口
  P0-1  输入契约收敛           ← 保证幂等和版本可回放

第2轮（稳定必须 — 短期）:
  P0-5  事件持久化             ← 重启不丢数据
  P1-2  重规划预算             ← 错误恢复逻辑完善

第3轮（质量提升 — 中期）:
  P1-1  段级碰撞检测           ← 仿真精度
  P1-3  真机 adapter           ← 协议已冻结，按需实现
  P1-4  指标结构化             ← 测试与验收自动化
```
