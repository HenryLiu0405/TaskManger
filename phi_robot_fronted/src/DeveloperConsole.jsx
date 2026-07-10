import { useState, useEffect, useCallback, useRef } from 'react';
import { InputNumber, Button, Space, Tag, Card, Segmented, Popconfirm, message } from 'antd';
import { SendOutlined, PushpinOutlined, RobotOutlined } from '@ant-design/icons';
import RobotPanel from './RobotPanel.jsx';
import BoxPanel from './BoxPanel.jsx';
import ServicePanel from './ServicePanel.jsx';
import ControlButtons from './ControlButtons.jsx';
import LogPanel from './LogPanel.jsx';
import FpVideoPanel from './FpVideoPanel.jsx';
import StepDebugPanel from './StepDebugPanel.jsx';

const API = '/api/dev';

// 坐标从后端 /api/scene_coords 拉取（Nav2 map 世界坐标）。
// 改坐标只需编辑 TaskManger/phi_robot/scene_coords.json + 重启后端，前端自动跟随。

export default function DeveloperConsole() {
  const [mode, setMode] = useState('manual');
  const [state, setState] = useState({});
  const [logs, setLogs] = useState([]);
  const logsRef = useRef([]);
  const skipCountRef = useRef(0);
  const [displayLogs, setDisplayLogs] = useState([]);
  const [targetX, setTargetX] = useState(null);
  const [targetY, setTargetY] = useState(null);
  const [autoDests, setAutoDests] = useState([]);
  const [loading, setLoading] = useState(false);
  const [connected, setConnected] = useState(false);
  const [robotState, setRobotState] = useState({ state: 'standing', can_walk: true, can_pick: false, can_place: false });
  const [sonicSource, setSonicSource] = useState('ROS2');
  const [sonicLoading, setSonicLoading] = useState(false);
  const [logViewMode, setLogViewMode] = useState('log');  // 'log' | 'camera'

  // ── 多机器人切换状态 ─────────────────────────────
  const [robotList, setRobotList] = useState([]);          // [{id,label,ip,domain_id}]
  const [activeRobot, setActiveRobot] = useState(null);    // {active_id,label,ip,domain_id,runtime_domain,domain_synced,switching,...}
  const [switching, setSwitching] = useState(false);
  // 场景坐标（从 /api/scene_coords 拉）：gridPresets=9 目标点，stockPreset=单物料点
  const [gridPresets, setGridPresets] = useState([]);      // [{label,x,y,theta}]
  const [stockPreset, setStockPreset] = useState(null);    // {x,y,theta}

  // 拉取场景坐标（一次）
  useEffect(() => {
    fetch('/api/scene_coords')
      .then((r) => r.json())
      .then((d) => {
        if (d.grid_cells) {
          setGridPresets(
            Object.entries(d.grid_cells).map(([label, c]) => ({
              label, x: c.x, y: c.y, theta: c.theta,
            }))
          );
        }
        if (d.stock_point) setStockPreset(d.stock_point);
      })
      .catch(() => {});
  }, []);

  // 拉取可选机器人列表（一次）
  useEffect(() => {
    fetch('/api/robot/list')
      .then((r) => r.json())
      .then((d) => { if (d.robots) setRobotList(d.robots); })
      .catch(() => {});
  }, []);

  // 轮询当前受控机器人 + 切换状态
  useEffect(() => {
    let active = true;
    async function pollActive() {
      try {
        const res = await fetch('/api/robot/active');
        const data = await res.json();
        if (!active) return;
        setActiveRobot(data);
        setSwitching(!!data.switching);
      } catch (_) {
        // 切换重启期间后端会短暂 500/断连 → 视为切换中
        if (active) setSwitching(true);
      }
    }
    pollActive();
    const timer = setInterval(pollActive, 1000);
    return () => { active = false; clearInterval(timer); };
  }, []);

  async function handleSwitchRobot(robotId) {
    if (switching) return;
    if (activeRobot && activeRobot.active_id === robotId && activeRobot.domain_synced) {
      message.info('已在控制该机器人');
      return;
    }
    setSwitching(true);
    try {
      const res = await fetch('/api/robot/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ robot_id: robotId }),
      });
      const data = await res.json();
      if (data.ok) {
        message.warning(data.message || '正在切换，服务重启中…', 4);
      } else {
        setSwitching(false);
        message.error(data.message || '切换失败');
      }
    } catch (_) {
      // 请求本身可能因重启中断 → 保持切换中，由轮询恢复
      message.warning('切换请求已发出，服务重启中…', 4);
    }
  }


  // ── 机器人安全状态轮询 ──────────────────────────
  useEffect(() => {
    let active = true;
    async function poll() {
      try {
        const res = await fetch('/api/robot/state');
        const data = await res.json();
        if (active) {
          setRobotState(data);
          if (data.sonic_input_source) setSonicSource(data.sonic_input_source);
        }
      } catch (_) {}
    }
    poll();
    const timer = setInterval(poll, 500);
    return () => { active = false; clearInterval(timer); };
  }, []);

  // ── SSE ────────────────────────────────────────────
  useEffect(() => {
    let es;
    let reconnectTimer;

    function connect() {
      es = new EventSource(`${API}/stream`);
      es.onopen = () => setConnected(true);
      es.onmessage = (msg) => {
        try {
          const s = JSON.parse(msg.data);
          setState(s);
          if (s.mode) setMode((prev) => prev === 'step_debug' ? prev : s.mode);
          if (s.logs) {
            logsRef.current = s.logs;
            setLogs(s.logs);
            setDisplayLogs(s.logs.slice(skipCountRef.current));
          }
        } catch (_) {}
      };
      es.onerror = () => {
        setConnected(false);
        es.close();
        reconnectTimer = setTimeout(connect, 2000);
      };
    }

    connect();
    return () => {
      if (es) es.close();
      clearTimeout(reconnectTimer);
    };
  }, []);

  // ── API call ───────────────────────────────────────
  const call = useCallback(async (path, body) => {
    setLoading(true);
    try {
      const res = await fetch(`${API}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
      return await res.json();
    } finally {
      setLoading(false);
    }
  }, []);

  function applyPreset(preset) {
    setTargetX(preset.x);
    setTargetY(preset.y);
  }

  function handleModeChange(val) {
    if (switching) return;
    setMode(val);
    call('/mode', { mode: val });
  }

  function handleNext() {
    if (switching) return;
    const body = {};
    if (targetX != null && targetY != null) {
      body.target = { x: targetX, y: targetY };
    }
    call('/manual/next', body).then((r) => {
      if (!r.ok) console.warn(r.message);
    });
  }

  function handlePause() { if (switching) return; call('/manual/pause'); }
  function handleStop() { if (switching) return; call('/manual/stop'); }
  function handlePick() { if (switching) return; call('/manual/pick'); }
  function handlePlace() { if (switching) return; call('/manual/place'); }

  async function toggleSonicSource(gamepad) {
    setSonicLoading(true);
    try {
      const res = await fetch('/api/dev/sonic/input_source', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gamepad }),
      });
      const data = await res.json();
      if (data.ok) {
        setSonicSource(data.active_source || (gamepad ? 'GAMEPAD' : 'ROS2'));
      }
    } catch (_) {
    } finally {
      setSonicLoading(false);
    }
  }

  function handleAutoStart() {
    if (switching) return;
    if (autoDests.length === 0) return;
    call('/auto/start', { destinations: autoDests });
  }
  function handleAutoPause() { if (switching) return; call('/auto/pause'); }
  function handleAutoResume() { if (switching) return; call('/auto/resume'); }
  function handleAutoStop() { if (switching) return; call('/auto/stop'); }

  function handleClearLogs() {
    skipCountRef.current = logsRef.current.length;
    setDisplayLogs([]);
  }

  // ── derived ────────────────────────────────────────
  const manual = state.manual || {};
  const robot = state.robot || {};
  const box = state.box || {};
  const auto = state.auto || {};
  const services = state.services || {};
  const autoRunning = auto.mission_status === 'running' || auto.mission_status === 'paused';
  const autoPaused = auto.mission_status === 'paused';

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      {/* ── 顶栏 ──────────────────────────────────── */}
      <header className="flex items-center justify-between px-4 py-2 border-b border-[#2a2a2a] shrink-0" style={{ background: '#111118' }}>
        <div className="flex items-center gap-4">
          <h1 className="text-xl font-bold tracking-wider text-white m-0 tracking-wide">
            HGPT CONSOLE
          </h1>
          <Segmented
            value={mode}
            onChange={handleModeChange}
            disabled={switching}
            options={[
              { label: '手动模式', value: 'manual' },
              { label: '分步调试', value: 'step_debug' },
              { label: '自动模式', value: 'auto' },
            ]}
          />
        </div>
        <div className="flex items-center gap-3">
          {/* 机器人切换器 */}
          <Space size={4}>
            <RobotOutlined style={{ color: '#d4a853' }} />
            {robotList.map((r) => {
              const isActive = activeRobot?.active_id === r.id && activeRobot?.domain_synced;
              return (
                <Popconfirm
                  key={r.id}
                  title={`切换到 ${r.label}?`}
                  description={`将重启工作站服务（domain ${r.domain_id} · ${r.ip}），约 10-20 秒`}
                  okText="确认切换"
                  cancelText="取消"
                  disabled={switching || isActive}
                  onConfirm={() => handleSwitchRobot(r.id)}
                >
                  <Button
                    size="small"
                    type={isActive ? 'primary' : 'default'}
                    disabled={switching}
                    title={`${r.ip} · domain ${r.domain_id}`}
                  >
                    {r.label}
                    <span className="text-[10px] opacity-70 ml-1">d{r.domain_id}</span>
                  </Button>
                </Popconfirm>
              );
            })}
          </Space>
          {/* 当前受控机器人 */}
          <Tag color={switching ? 'warning' : activeRobot?.domain_synced ? 'success' : 'error'}>
            {switching
              ? `⚠ 切换到 ${activeRobot?.switch_target || '…'} 中…`
              : activeRobot
                ? `控制中: ${activeRobot.label} (${activeRobot.ip}·d${activeRobot.runtime_domain})`
                : '机器人未知'}
          </Tag>
          <Tag color={connected ? 'success' : 'error'}>
            {connected ? '● 已连接' : '○ 断开'}
          </Tag>
          <Tag
            color={
              manual?.state === 'stopped' ? 'error'
              : manual?.state === 'paused' ? 'warning'
              : manual?.is_paused ? 'warning'
              : autoRunning ? 'processing'
              : 'default'
            }
          >
            {mode === 'manual'
              ? (manual?.state || 'idle')
              : (auto?.mission_status || 'idle')}
          </Tag>
          <Tag
            color={
              robotState?.state === 'emergency' ? 'error'
              : robotState?.state === 'error' ? 'warning'
              : robotState?.state === 'moving' ? 'processing'
              : robotState?.state === 'picking' || robotState?.state === 'placing' ? 'processing'
              : robotState?.state === 'holding' ? 'success'
              : 'default'
            }
          >
            🤖 {robotState?.state || 'standing'}
          </Tag>
        </div>
      </header>

      {/* ── 切换中横幅 ─────────────────────────────── */}
      {switching && (
        <div
          className="shrink-0 text-center py-1.5 text-sm font-bold"
          style={{ background: '#4a3a10', color: '#d4a853', borderBottom: '1px solid #d4a853' }}
        >
          ⚠ 正在切换到 {activeRobot?.switch_target || '目标机器人'}，工作站服务重启中，请勿操作（约 10-20 秒）…
        </div>
      )}
      {!switching && activeRobot && !activeRobot.domain_synced && (
        <div
          className="shrink-0 text-center py-1.5 text-sm font-bold"
          style={{ background: '#4a1010', color: '#ff7875', borderBottom: '1px solid #d44a4a' }}
        >
          ⚠ 工作站 domain ({activeRobot.runtime_domain}) 与目标机器人 {activeRobot.label} (d{activeRobot.domain_id}) 不一致 —
          {activeRobot.switch_error ? ' 上次切换失败，请检查 systemctl 状态' : ' 请重新切换'}
        </div>
      )}

      {/* ── 主体: 左侧栏 + 右侧主区域 ─────────────── */}
      <div className="flex-1 flex min-h-0">
        {/* 左侧边栏 */}
        <aside className="w-72 shrink-0 border-r border-[#2a2a2a] overflow-y-auto p-3 flex flex-col gap-3" style={{ background: '#0b0b10' }}>
          <RobotPanel
            manual={manual} robot={robot} services={services}
            sonicSource={sonicSource} sonicLoading={sonicLoading}
            onToggleSonicSource={toggleSonicSource}
          />
          <BoxPanel box={box} holdingBox={manual?.holding_box} />
          <ServicePanel />
        </aside>

        {/* 右侧主区域 */}
        <main className="flex-1 flex flex-col min-h-0 p-3 gap-3">
          {/* 分步调试模式 — 独立面板 */}
          {mode === 'step_debug' ? (
            <StepDebugPanel robotState={robotState} />
          ) : (
          <>
          {/* 任务状态 + 预设位置 / 自动模式 */}
          <Card size="small" title="任务状态" className="shrink-0">
            {mode === 'auto' ? (
              <div>
                <div className="flex gap-4 text-base mb-2">
                  <span className="text-[#8a8a8a]">Mission:</span>
                  <code className="text-sm">{auto?.mission_id || '--'}</code>
                </div>
                <div className="flex gap-4 text-base mb-2">
                  <span className="text-[#8a8a8a]">进度:</span>
                  <span>{auto?.current_step != null ? `${auto.current_step + 1}/${auto.total_steps}` : '--'}</span>
                </div>
                <div className="flex gap-4 text-base mb-3">
                  <span className="text-[#8a8a8a]">状态:</span>
                  <Tag>{auto?.mission_status || 'idle'}</Tag>
                </div>
                {/* 目标位置 — 点击加入序列 */}
                <div className="mb-2">
                  <div className="text-sm text-[#8a8a8a] mb-1">
                    <PushpinOutlined className="mr-1" />目标位置（点击加入序列）
                  </div>
                  <Space wrap size={4}>
                    {gridPresets.map((p) => (
                      <Button
                        key={p.label}
                        size="small"
                        onClick={() => setAutoDests((prev) => [...prev, p.label])}
                        disabled={!!autoRunning || autoDests.includes(p.label)}
                      >
                        {p.label}
                        <span className="text-sm text-[#8a8a8a] ml-1">({p.x},{p.y})</span>
                      </Button>
                    ))}
                  </Space>
                </div>
                {/* 取货点 — 固定单点，人工补料 */}
                <div className="mb-2">
                  <div className="text-sm text-[#8a8a8a] mb-1">取货点（固定，人工补料）</div>
                  <Space wrap size={4}>
                    {stockPreset && (
                      <Button size="small" disabled>
                        物料
                        <span className="text-sm text-[#8a8a8a] ml-1">({stockPreset.x},{stockPreset.y})</span>
                      </Button>
                    )}
                  </Space>
                </div>
                {/* 目标序列展示 */}
                <div>
                  <div className="text-sm text-[#8a8a8a] mb-1">目标序列:</div>
                  {autoDests.length === 0 ? (
                    <div className="text-sm text-[#8a8a8a] italic">点击上方目标位置添加</div>
                  ) : (
                    <div className="flex items-center gap-1 flex-wrap">
                      {autoDests.map((dest, i) => (
                        <Tag
                          key={`${dest}-${i}`}
                          closable
                          onClose={() => setAutoDests((prev) => prev.filter((_, j) => j !== i))}
                          color="processing"
                        >
                          {i + 1}. {dest}
                        </Tag>
                      ))}
                      <Button
                        size="small"
                        type="text"
                        danger
                        onClick={() => setAutoDests([])}
                        disabled={!!autoRunning}
                      >
                        清空序列
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div>
                {/* 预设位置 */}
                <div className="mb-3">
                  <div className="text-sm text-[#8a8a8a] mb-1">
                    <PushpinOutlined className="mr-1" />预设位置
                  </div>
                  <Space wrap size={4}>
                    {[...gridPresets, ...(stockPreset ? [{ label: '物料', x: stockPreset.x, y: stockPreset.y }] : [])].map((p) => (
                      <Button
                        key={p.label}
                        size="small"
                        onClick={() => applyPreset(p)}
                        type={targetX === p.x && targetY === p.y ? 'primary' : 'default'}
                      >
                        {p.label}
                        <span className="text-sm text-[#8a8a8a] ml-1">({p.x},{p.y})</span>
                      </Button>
                    ))}
                  </Space>
                </div>

                {/* 导航输入 */}
                <div className="flex items-center gap-2">
                  <span className="text-sm text-[#8a8a8a]">导航目标:</span>
                  <span className="text-sm text-[#8a8a8a]">X</span>
                  <InputNumber size="small" style={{ width: 80 }} placeholder="0.5" step={0.5} value={targetX} onChange={setTargetX} />
                  <span className="text-sm text-[#8a8a8a]">Y</span>
                  <InputNumber size="small" style={{ width: 80 }} placeholder="3.5" step={0.5} value={targetY} onChange={setTargetY} />
                  <Button size="small" type="primary" icon={<SendOutlined />} onClick={handleNext} disabled={switching || targetX == null || targetY == null || robotState?.can_walk === false} loading={loading}>
                    导航
                  </Button>
                </div>

                {manual?.can_undo && (
                  <div className="text-sm text-[#8a8a8a] mt-2">
                    可撤销 ({manual?.undo_count || 0}): {manual?.undo_description}
                  </div>
                )}
              </div>
            )}
          </Card>

          {/* 控制按钮 */}
          <Card size="small" title="控制" className="shrink-0">
            <ControlButtons
              mode={mode}
              manualState={manual?.state}
              robotState={robotState}
              autoRunning={autoRunning}
              autoPaused={autoPaused}
              onPause={mode === 'manual' ? handlePause : handleAutoPause}
              onStop={mode === 'manual' ? handleStop : handleAutoStop}
              onPick={handlePick}
              onPlace={handlePlace}
              onStart={handleAutoStart}
              onResume={handleAutoResume}
              autoEnabled={autoDests.length > 0}
              autoDestCount={autoDests.length}
            />
          </Card>

          {/* 日志 / FP 视频切换 */}
          <div style={{ display: 'flex', justifyContent: 'center', gap: 0 }}>
            <button
              type="button"
              onClick={() => setLogViewMode('log')}
              style={{
                padding: '4px 16px',
                border: '2px solid #d4a853',
                borderRight: 'none',
                background: logViewMode === 'log' ? '#d4a853' : 'transparent',
                color: logViewMode === 'log' ? '#0b0b10' : '#d4a853',
                fontWeight: 'bold',
                fontFamily: 'monospace',
                fontSize: 13,
                borderRadius: '4px 0 0 4px',
                cursor: 'pointer',
              }}
            >
              ▶ LOG
            </button>
            <button
              type="button"
              onClick={() => setLogViewMode('camera')}
              style={{
                padding: '4px 16px',
                border: '2px solid #d4a853',
                background: logViewMode === 'camera' ? '#d4a853' : 'transparent',
                color: logViewMode === 'camera' ? '#0b0b10' : '#d4a853',
                fontWeight: 'bold',
                fontFamily: 'monospace',
                fontSize: 13,
                borderRadius: '0 4px 4px 0',
                cursor: 'pointer',
              }}
            >
              📷 CAMERA
            </button>
          </div>

          {logViewMode === 'log' ? (
            <LogPanel logs={displayLogs} onClear={handleClearLogs} />
          ) : (
            <FpVideoPanel />
          )}
          </>
          )}
        </main>
      </div>
    </div>
  );
}
