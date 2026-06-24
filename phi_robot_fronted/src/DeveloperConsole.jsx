import { useState, useEffect, useCallback, useRef } from 'react';
import { InputNumber, Button, Space, Tag, Card, Segmented } from 'antd';
import { SendOutlined, PushpinOutlined } from '@ant-design/icons';
import RobotPanel from './RobotPanel.jsx';
import BoxPanel from './BoxPanel.jsx';
import ServicePanel from './ServicePanel.jsx';
import ControlButtons from './ControlButtons.jsx';
import LogPanel from './LogPanel.jsx';
import FpVideoPanel from './FpVideoPanel.jsx';

const API = '/api/dev';

const PRESETS = [
  { label: 'nw', x: 2.5, y: 2.5 },
  { label: 'n',  x: 3.5, y: 2.5 },
  { label: 'ne', x: 4.5, y: 2.5 },
  { label: 'w',  x: 2.5, y: 1.5 },
  { label: 'c',  x: 3.5, y: 1.5 },
  { label: 'e',  x: 4.5, y: 1.5 },
  { label: 'sw', x: 2.5, y: 0.5 },
  { label: 's',  x: 3.5, y: 0.5 },
  { label: 'se', x: 4.5, y: 0.5 },
  { label: '顶右', x: 2.0, y: 1.5 },
  { label: '顶中', x: 1.25, y: 1.5 },
  { label: '顶左', x: 0.5, y: 1.5 },
  { label: '中右', x: 2.0, y: 1.0 },
  { label: '中中', x: 1.25, y: 1.0 },
  { label: '中左', x: 0.5, y: 1.0 },
  { label: '底右', x: 2.0, y: 0.5 },
  { label: '底中', x: 1.25, y: 0.5 },
  { label: '底左', x: 0.5, y: 0.5 },
  { label: '原点', x: 0.5, y: 2.5 },
];

const GRID_PRESETS = PRESETS.filter((p) => /^[a-z]+$/.test(p.label));
const STOCK_PRESETS = PRESETS.filter((p) => !/^[a-z]+$/.test(p.label) && p.label !== '原点');

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
  const [logViewMode, setLogViewMode] = useState('log');  // 'log' | 'camera'

  // ── 机器人安全状态轮询 ──────────────────────────
  useEffect(() => {
    let active = true;
    async function poll() {
      try {
        const res = await fetch('/api/robot/state');
        const data = await res.json();
        if (active) setRobotState(data);
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
          if (s.mode) setMode(s.mode);
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
    setMode(val);
    call('/mode', { mode: val });
  }

  function handleNext() {
    const body = {};
    if (targetX != null && targetY != null) {
      body.target = { x: targetX, y: targetY };
    }
    call('/manual/next', body).then((r) => {
      if (!r.ok) console.warn(r.message);
    });
  }

  function handlePause() { call('/manual/pause'); }
  function handleStop() { call('/manual/stop'); }
  function handlePick() { call('/manual/pick'); }
  function handlePlace() { call('/manual/place'); }

  function handleAutoStart() {
    if (autoDests.length === 0) return;
    call('/auto/start', { destinations: autoDests });
  }
  function handleAutoPause() { call('/auto/pause'); }
  function handleAutoResume() { call('/auto/resume'); }
  function handleAutoStop() { call('/auto/stop'); }

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
            options={[
              { label: '手动模式', value: 'manual' },
              { label: '自动模式', value: 'auto' },
            ]}
          />
        </div>
        <div className="flex items-center gap-3">
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

      {/* ── 主体: 左侧栏 + 右侧主区域 ─────────────── */}
      <div className="flex-1 flex min-h-0">
        {/* 左侧边栏 */}
        <aside className="w-72 shrink-0 border-r border-[#2a2a2a] overflow-y-auto p-3 flex flex-col gap-3" style={{ background: '#0b0b10' }}>
          <RobotPanel manual={manual} robot={robot} services={services} />
          <BoxPanel box={box} holdingBox={manual?.holding_box} />
          <ServicePanel />
        </aside>

        {/* 右侧主区域 */}
        <main className="flex-1 flex flex-col min-h-0 p-3 gap-3">
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
                    {GRID_PRESETS.map((p) => (
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
                {/* 取货点 — 系统自动分配 */}
                <div className="mb-2">
                  <div className="text-sm text-[#8a8a8a] mb-1">取货点（系统按顺序自动分配）</div>
                  <Space wrap size={4}>
                    {STOCK_PRESETS.map((p) => (
                      <Button key={p.label} size="small" disabled>
                        {p.label}
                      </Button>
                    ))}
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
                    {PRESETS.map((p) => (
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
                  <Button size="small" type="primary" icon={<SendOutlined />} onClick={handleNext} disabled={targetX == null || targetY == null || robotState?.can_walk === false} loading={loading}>
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
        </main>
      </div>
    </div>
  );
}
