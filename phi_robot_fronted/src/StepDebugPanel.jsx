import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Button, Space, Tag, Card, message, Tooltip, Modal } from 'antd';
import {
  PlayCircleOutlined, ReloadOutlined, StepForwardOutlined,
  DoubleRightOutlined, PauseCircleOutlined, CaretRightOutlined,
  StopOutlined, PushpinOutlined, CameraOutlined,
  AimOutlined,
} from '@ant-design/icons';

const API = '/api/dev/step_debug';

// 目标点坐标从后端 /api/scene_coords 拉取（Nav2 map 世界坐标）。
// 改坐标只需编辑 TaskManger/phi_robot/scene_coords.json + 重启后端。

// ── 状态对应的按钮启用逻辑 ──────────────────────────
const STATE_BUTTONS = {
  idle:           { exec: false, retry: false, skip: false, fskip: false, pause: false, resume: false, abort: true },
  plan_loaded:    { exec: true,  retry: false, skip: false, fskip: false, pause: false, resume: false, abort: true },
  step_ready:     { exec: true,  retry: false, skip: true,  fskip: false, pause: false, resume: false, abort: true },
  executing:      { exec: false, retry: false, skip: false, fskip: false, pause: true,  resume: false, abort: true },
  paused:         { exec: false, retry: false, skip: false, fskip: false, pause: false, resume: true,  abort: true },
  step_done:      { exec: true,  retry: true,  skip: true,  fskip: false, pause: false, resume: false, abort: true },
  step_failed:    { exec: false, retry: true,  skip: false, fskip: true,  pause: false, resume: false, abort: true },
  aborted:        { exec: false, retry: false, skip: false, fskip: false, pause: false, resume: false, abort: false },
};

// ── 步骤状态渲染 ────────────────────────────────────
const STEP_ICONS = {
  pending:        { color: '#555',    icon: '○', label: '待执行' },
  current:        { color: '#1677ff', icon: '▶', label: '当前' },
  running:        { color: '#faad14', icon: '◉', label: '执行中' },
  completed:      { color: '#52c41a', icon: '✓', label: '完成' },
  failed:         { color: '#ff4d4f', icon: '✗', label: '失败' },
  skipped:        { color: '#888',    icon: '⏭', label: '已跳过' },
  force_skipped:  { color: '#faad14', icon: '⏭⚠', label: '强制跳过' },
};

// ── 诊断事件渲染 ────────────────────────────────────
function DiagEventRow({ ev }) {
  const { ts, phase, type, data } = ev;

  // fp_snapshot
  if (type === 'fp_snapshot') {
    return (
      <div className="flex gap-2 py-0.5 text-xs">
        <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
        <Tag color="purple" className="text-[10px] leading-none">FP</Tag>
        <span className="text-[#c8c8c8]">
          {data.available
            ? `${data.tracker_count} objects tracking`
            : 'FP unavailable'}
        </span>
        {data.trackers?.map((t, i) => (
          <span key={i} className="text-[#8a8a8a] ml-2">
            #{i+1} id={t.id} state={t.state} frames={t.frames}
            {t.mask_area ? ` area=${t.mask_area}` : ''}
            {' '}({t.x},{t.y},{t.z})
          </span>
        ))}
      </div>
    );
  }

  // fp_stable_progress
  if (type === 'fp_stable_progress') {
    return (
      <div className="flex gap-2 py-0.5 text-xs">
        <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
        <Tag color="purple" className="text-[10px] leading-none">FP</Tag>
        <span className="text-[#c8c8c8]">
          等待稳定: {data.elapsed_s}s, max_frames={data.max_frames}
          {data.best_obj_id != null ? `, best=#${data.best_obj_id}` : ''}
        </span>
      </div>
    );
  }

  // gate_result
  if (type === 'gate_result') {
    const replan = data.should_replan;
    return (
      <div className="flex gap-2 py-0.5 text-xs">
        <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
        <Tag color={replan ? 'orange' : 'green'} className="text-[10px] leading-none">GATE</Tag>
        <span className="text-[#c8c8c8]">
          {data.reason_code} — {data.reason}
          {replan && ` → replan #${data.replan_count}`}
        </span>
        {data.selected_id != null && (
          <span className="text-[#8a8a8a]">
            sel=#{data.selected_id} score={data.selected_score} pose={JSON.stringify(data.selected_pose)}
          </span>
        )}
        {data.new_approach && (
          <span className="text-[#d4a853]">
            new={JSON.stringify(data.new_approach)}
          </span>
        )}
      </div>
    );
  }

  // scan_step
  if (type === 'scan_step') {
    return (
      <div className="flex gap-2 py-0.5 text-xs">
        <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
        <Tag color="cyan" className="text-[10px] leading-none">SCAN</Tag>
        <span className="text-[#c8c8c8]">
          ∠{data.angle_deg}° (odom {data.odom_yaw_deg}°)
          {data.found ? <Tag color="success" className="text-[10px] ml-1">FOUND</Tag> : ''}
        </span>
      </div>
    );
  }

  // scan_result
  if (type === 'scan_result') {
    return (
      <div className="flex gap-2 py-0.5 text-xs">
        <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
        <Tag color="cyan" className="text-[10px] leading-none">SCAN</Tag>
        <span className="text-[#c8c8c8]">
          扫描完成: {data.status} ({data.total_angles} 个角度)
          {data.pose && ` → ${JSON.stringify(data.pose)}`}
        </span>
      </div>
    );
  }

  // replan_variant
  if (type === 'replan_variant') {
    return (
      <div className="flex gap-2 py-0.5 text-xs">
        <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
        <Tag color="orange" className="text-[10px] leading-none">VAR</Tag>
        <span className="text-[#c8c8c8]">
          attempt={data.attempt} offset={data.offset_m}m yaw={data.yaw_offset_deg}°
          → {JSON.stringify(data.result_approach)}
        </span>
      </div>
    );
  }

  // action
  if (type === 'action') {
    return (
      <div className="flex gap-2 py-0.5 text-xs">
        <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
        <Tag color="blue" className="text-[10px] leading-none">ACT</Tag>
        <span className="text-[#c8c8c8]">
          → {data.action}
          {data.reason && ` (${data.reason})`}
          {data.deviation != null && ` dev=${data.deviation}m`}
        </span>
        {data.new_approach && (
          <span className="text-[#d4a853]">
            approach={JSON.stringify(data.new_approach)}
          </span>
        )}
      </div>
    );
  }

  // step_result
  if (type === 'step_result') {
    return (
      <div className="flex gap-2 py-0.5 text-xs">
        <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
        <Tag color={data.status === 'ok' ? 'success' : 'error'} className="text-[10px] leading-none">RESULT</Tag>
        <span className={data.status === 'ok' ? 'text-[#52c41a]' : 'text-[#ff4d4f]'}>
          {data.status === 'ok' ? '✓ 成功' : '✗ 失败'}
          {' '}({data.elapsed_s}s)
          {data.error_code && ` [${data.error_code}]`}
          {data.message && ` ${data.message}`}
        </span>
      </div>
    );
  }

  // odom_snapshot, rotate_cmd, step_start — compact
  if (type === 'odom_snapshot') {
    return (
      <div className="flex gap-2 py-0.5 text-xs">
        <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
        <Tag className="text-[10px] leading-none">ODOM</Tag>
        <span className="text-[#8a8a8a]">
          x={data.x} y={data.y} yaw={data.yaw_deg}°
        </span>
      </div>
    );
  }

  if (type === 'rotate_cmd') {
    return (
      <div className="flex gap-2 py-0.5 text-xs">
        <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
        <Tag color="geekblue" className="text-[10px] leading-none">ROT</Tag>
        <span className="text-[#8a8a8a]">
          target={data.target_yaw_deg}° curr={data.current_yaw_deg}°
          err={data.error_deg}° ω={data.angular_z}
        </span>
      </div>
    );
  }

  if (type === 'step_start') {
    return (
      <div className="flex gap-2 py-0.5 text-xs">
        <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
        <Tag color="green" className="text-[10px] leading-none">START</Tag>
        <span className="text-[#c8c8c8]">
          {data.tool}
          {data.args_summary && <span className="text-[#8a8a8a]"> {data.args_summary}</span>}
        </span>
      </div>
    );
  }

  // 🆕 v3: select_target
  if (type === 'select_target') {
    return (
      <div className="flex gap-2 py-0.5 text-xs">
        <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
        <Tag color="blue" className="text-[10px] leading-none">SELECT</Tag>
        <span className="text-[#c8c8c8]">
          select={String(data.select)}
          {data.select && <> → object_id={data.matched_object_id} (pick=({data.pick_x?.toFixed(2)}, {data.pick_y?.toFixed(2)}))</>}
          {!data.select && <> → 掉箱检测恢复</>}
        </span>
      </div>
    );
  }

  // 🆕 v3: drop_status
  if (type === 'drop_status') {
    const dropped = !data.box_present;
    return (
      <div className="flex gap-2 py-0.5 text-xs">
        <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
        <Tag color={dropped ? 'red' : 'green'} className="text-[10px] leading-none">
          {dropped ? '掉落!' : '箱在'}
        </Tag>
        <span className={dropped ? 'text-[#ff4d4f] font-bold' : 'text-[#c8c8c8]'}>
          {dropped
            ? `箱子掉落！odom=(${data.odom_x?.toFixed(2)}, ${data.odom_y?.toFixed(2)})`
            : '箱子在画面中'}
        </span>
      </div>
    );
  }

  // 🆕 v3: drop_detector_state
  if (type === 'drop_detector_state') {
    return (
      <div className="flex gap-2 py-0.5 text-xs">
        <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
        <Tag color={data.enabled ? 'green' : 'orange'} className="text-[10px] leading-none">
          {data.enabled ? '启用' : '禁用'}
        </Tag>
        <span className="text-[#8a8a8a]">
          掉箱检测{data.enabled ? '已启用' : '已禁用'}
          {data.trigger_source && <> ({data.trigger_source})</>}
        </span>
      </div>
    );
  }

  // fallback
  return (
    <div className="flex gap-2 py-0.5 text-xs">
      <span className="text-[#6a6a6a] shrink-0 w-[72px]">{ts}</span>
      <Tag className="text-[10px] leading-none">{type}</Tag>
      <span className="text-[#8a8a8a]">{JSON.stringify(data)}</span>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// StepDebugPanel
// ═══════════════════════════════════════════════════════════════

export default function StepDebugPanel({ robotState }) {
  const [dests, setDests] = useState([]);
  const [gridPresets, setGridPresets] = useState([]);   // [{label,x,y,theta}] 从 /api/scene_coords
  const [loading, setLoading] = useState(false);
  const [state, setState] = useState('idle');
  const [plan, setPlan] = useState([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [diagEvents, setDiagEvents] = useState([]);
  const [stepLogs, setStepLogs] = useState([]);
  const [connected, setConnected] = useState(false);
  const [showFpVideo, setShowFpVideo] = useState(false);
  const [showDropVis, setShowDropVis] = useState(false);
  const [selectTargetActive, setSelectTargetActive] = useState(false);
  const [dropDetectorEnabled, setDropDetectorEnabled] = useState(false);
  const [loadTaskCollapsed, setLoadTaskCollapsed] = useState(false);
  const [planCollapsed, setPlanCollapsed] = useState(false);
  const [controlCollapsed, setControlCollapsed] = useState(false);
  const [dropCollapsed, setDropCollapsed] = useState(false);
  const [diagCollapsed, setDiagCollapsed] = useState(false);
  const [logCollapsed, setLogCollapsed] = useState(false);
  const [droppedBoxId, setDroppedBoxId] = useState(-1);
  const [droppedBoxPose, setDroppedBoxPose] = useState(null);
  const [dropPhase, setDropPhase] = useState('');  // idle | detecting | paused | standing | identifying | ready
  const [navReached, setNavReached] = useState(null);  // 🆕 Nav2 /nav_reached 状态: true=到达, false=导航中
  const [robotPaused, setRobotPaused] = useState(false);  // 🆕 /nav_pause 暂停状态，控制按钮 toggle
  const [autoMode, setAutoMode] = useState(false);  // 自动执行模式

  const diagEndRef = useRef(null);
  const poseTimerRef = useRef(null);

  // 🆕 v3: 从 diag 事件中提取最新掉箱状态
  const dropStatus = useMemo(() => {
    for (let i = diagEvents.length - 1; i >= 0; i--) {
      if (diagEvents[i].type === 'drop_status') return diagEvents[i].data;
    }
    return null;
  }, [diagEvents]);

  // ★ 当前步是否为 pick 且未进入单目标模式
  const currentStep = plan[currentIndex];
  const isPickStep = currentStep?.tool === 'pick';
  const pickDisabled = isPickStep && !selectTargetActive;

  // ── 场景坐标（一次拉取）─────────────────────────────
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
      })
      .catch(() => {});
  }, []);

  // ── SSE ────────────────────────────────────────────
  useEffect(() => {
    let es;
    let reconnectTimer;

    function connect() {
      es = new EventSource(`${API}/stream`);
      es.onopen = () => setConnected(true);

      es.addEventListener('diag', (msg) => {
        try {
          const payload = JSON.parse(msg.data);
          if (payload.events?.length) {
            setDiagEvents((prev) => [...prev, ...payload.events]);
          }
        } catch (_) {}
      });

      es.addEventListener('state', (msg) => {
        try {
          const payload = JSON.parse(msg.data);
          if (payload.state) setState(payload.state);
          if (payload.current_index != null) setCurrentIndex(payload.current_index);
          if (payload.plan) setPlan(payload.plan);
          if (payload.step_logs) setStepLogs(payload.step_logs);
          if (payload.select_target_active != null) setSelectTargetActive(payload.select_target_active);
          if (payload.nav_reached != null) setNavReached(payload.nav_reached);  // 🆕 Nav2 导航到达状态
        } catch (_) {}
      });

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

  // ── 自动执行完成检测 ─────────────────────────────
  useEffect(() => {
    if (autoMode && (state === 'idle' || state === 'step_failed' || state === 'aborted')) {
      setAutoMode(false);
      if (state === 'idle') {
        message.success(`自动执行完成！共 ${plan.length} 步全部成功`);
      } else if (state === 'step_failed') {
        message.error(`自动执行中断：第 ${currentIndex + 1} 步失败`);
      }
    }
  }, [state, autoMode]);

  // ── 自动滚屏 ───────────────────────────────────────
  useEffect(() => {
    diagEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [diagEvents.length]);

  // ── API call ───────────────────────────────────────
  const call = useCallback(async (path, body) => {
    setLoading(true);
    try {
      const res = await fetch(`${API}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
      const data = await res.json();
      if (!data.ok) message.warning(data.message);
      return data;
    } catch (e) {
      message.error(`请求失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  // ── 按钮 handlers ──────────────────────────────────
  const btns = STATE_BUTTONS[state] || STATE_BUTTONS.idle;

  function handleLoad() {
    if (dests.length === 0) return;
    call('/load', { destinations: dests }).then((r) => {
      if (r?.ok) {
        // 不乐观设置状态——SSE 会在下一次轮询时推送正确的 state/plan/stepLogs
        setDiagEvents([]);
      }
    });
  }

  function handleExecute() { call('/execute'); }
  function handleAutoRun() {
    call('/auto_run').then((r) => {
      if (r?.ok) {
        setAutoMode(true);
        message.info(`自动执行已启动 (${r.current_index + 1}/${r.total_steps} 步)`);
      }
    });
  }
  function handleRetry() { call('/retry'); }
  function handleSkip() { call('/skip'); }
  function handleForceSkip() {
    // 前端二次确认
    message.info('正在强制跳过当前步骤...');
    call('/force_skip');
  }
  function handlePause() { call('/pause'); }
  function handleResume() { call('/resume'); }
  function handleAbort() { call('/abort'); }
  function handleToggleSelectTarget() {
    call('/select_target_toggle').then((r) => {
      if (r?.ok) {
        setSelectTargetActive(r.active);
        if (r.active) {
          message.success(`已锁定物体 #${r.object_id}`);
        } else {
          message.info('已退出单目标模式');
        }
      } else {
        message.warning(r?.message || '切换失败');
      }
    });
  }
  function handleResetFp() {
    message.info('正在重新标定物体...');
    call('/reset_fp').then((r) => {
      if (r?.ok) {
        message.success('FP 重新标定完成');
      } else {
        message.warning(r?.message || 'Reset 失败');
      }
    });
  }

  // ── 掉箱处理 ──────────────────────────────────────
  function handleEnableDropDetector() {
    call('/drop_detector/enable').then((r) => {
      if (r?.ok) {
        setDropDetectorEnabled(true);
        setDropPhase('detecting');
        setShowDropVis(true);
        message.success('掉箱检测已开启');
      } else {
        message.warning(r?.message || '开启失败');
      }
    });
  }

  function handleDisableDropDetector() {
    call('/drop_detector/disable').then((r) => {
      if (r?.ok) {
        setDropDetectorEnabled(false);
        setDropPhase('');
        setShowDropVis(false);
        message.info('掉箱检测已关闭');
      } else {
        message.warning(r?.message || '关闭失败');
      }
    });
  }

  function handlePauseRobot() {
    call('/robot/pause').then((r) => {
      if (r?.ok) {
        setDropPhase('paused');
        setRobotPaused(true);
        // 🆕 使用 /nav_reached 状态确认暂停效果
        if (r.nav_reached != null) {
          setNavReached(r.nav_reached);
        }
        const navMsg = r.nav_reached != null
          ? (r.nav_reached ? ' (nav: 已到达)' : ' (nav: 仍导航中)')
          : '';
        message.success(`机器人已暂停${navMsg}`);
      } else {
        message.warning(r?.message || '暂停失败');
      }
    });
  }

  function handleResumeRobot() {
    call('/robot/resume').then((r) => {
      if (r?.ok) {
        setRobotPaused(false);
        setDropPhase('detecting');
        message.success('机器人已恢复导航');
      } else {
        message.warning(r?.message || '恢复失败');
      }
    });
  }

  function handleStandRobot() {
    setDropPhase('standing');
    message.info('正在切换站立模式...');
    call('/robot/stand').then((r) => {
      if (r?.ok) {
        setDropPhase('stood');
        message.success('已切换站立模式');
      } else {
        setDropPhase('detecting');
        message.warning(r?.message || '站立失败');
      }
    });
  }

  function handleIdentifyDropped() {
    setDropPhase('identifying');
    message.info('正在识别掉落箱子...');
    call('/dropped/identify').then((r) => {
      if (r?.ok && r.matched_object_id >= 0) {
        setDroppedBoxId(r.matched_object_id);
        setDroppedBoxPose(r.pose);
        setDropPhase('ready');
        message.success(`识别成功: 箱子 #${r.matched_object_id} ${r.pose ? `(${r.pose.x.toFixed(2)}, ${r.pose.y.toFixed(2)})` : ''}`);
      } else {
        setDropPhase('detecting');
        message.warning(r?.message || '未识别到掉落箱子');
      }
    });
  }

  function handleReplanPick() {
    message.info('正在执行重规划搬起...');
    call('/dropped/replan_pick').then((r) => {
      if (r?.ok) {
        setDropPhase('');
        setDroppedBoxId(-1);
        setDroppedBoxPose(null);
        message.success('重规划搬起完成');
      } else {
        message.warning(r?.message || '搬起失败');
      }
    });
  }

  // ── render ─────────────────────────────────────────
  const canWalk = robotState?.can_walk !== false;

  return (
    <div className="flex flex-col gap-3 h-full min-h-0">
      {/* ── 目标选择 ──────────────────────────────── */}
      <Card size="small" title={
        <div className="flex items-center gap-2 cursor-pointer select-none"
             onClick={() => setLoadTaskCollapsed(!loadTaskCollapsed)}>
          <span className="text-xs text-[#8a8a8a]">{loadTaskCollapsed ? '▶' : '▼'}</span>
          <span>加载任务</span>
          {dests.length > 0 && (
            <Tag className="text-[10px] leading-none">{dests.length} 个目标</Tag>
          )}
        </div>
      } className="shrink-0">
        {!loadTaskCollapsed && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span className="text-sm text-[#8a8a8a] shrink-0">
              <PushpinOutlined className="mr-1" />目标位置
            </span>
            <Space wrap size={4}>
              {gridPresets.map((p) => (
                <Button
                  key={p.label}
                  size="small"
                  onClick={() => setDests((prev) =>
                    prev.includes(p.label) ? prev.filter((d) => d !== p.label) : [...prev, p.label]
                  )}
                  type={dests.includes(p.label) ? 'primary' : 'default'}
                  disabled={state === 'executing' || state === 'paused'}
                >
                  {p.label}
                  <span className="text-sm text-[#8a8a8a] ml-1">({p.x},{p.y})</span>
                </Button>
              ))}
            </Space>
          </div>
          {/* 目标序列 */}
          {dests.length > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-[#8a8a8a]">序列:</span>
              {dests.map((d, i) => (
                <Tag key={`${d}-${i}`} color="processing" closable
                  onClose={() => setDests((prev) => prev.filter((_, j) => j !== i))}>
                  {i+1}. {d}
                </Tag>
              ))}
              <Button size="small" type="text" danger onClick={() => setDests([])}
                disabled={state === 'executing' || state === 'paused'}>
                清空
              </Button>
            </div>
          )}
          <Button
            type="primary"
            disabled={dests.length === 0 || state === 'executing' || state === 'paused'}
            loading={loading}
            onClick={handleLoad}
          >
            加载 Plan
          </Button>
        </div>
        )}
      </Card>

      {/* ── FP 单目标切换 ──────────────────────────── */}
      <Card size="small" className="shrink-0" style={
        isPickStep && !selectTargetActive
          ? { borderColor: '#ff4d4f', boxShadow: '0 0 8px rgba(255,77,79,0.25)' }
          : {}
      }>
        <Button
          type={selectTargetActive ? 'primary' : (isPickStep ? 'default' : 'default')}
          danger={isPickStep && !selectTargetActive}
          icon={<AimOutlined />}
          onClick={handleToggleSelectTarget}
          disabled={state === 'idle' || state === 'executing' || state === 'paused'}
          loading={loading}
          block
        >
          {selectTargetActive
            ? '退出单目标模式'
            : (isPickStep ? '⚠ 请先锁定单目标再搬箱' : '锁定单目标')
          }
        </Button>
        {selectTargetActive && (
          <div className="text-xs text-[#52c41a] mt-1">
            ● 单目标模式已激活 — 可执行搬箱子 (pick)
          </div>
        )}
        {isPickStep && !selectTargetActive && (
          <div className="text-xs text-[#ff4d4f] mt-1">
            ⚠ 当前步骤为搬箱子，必须先锁定单目标物体
          </div>
        )}
      </Card>

      {/* ── 步骤列表 ──────────────────────────────── */}
      <Card
        size="small"
        title={
          <div className="flex items-center gap-2 cursor-pointer select-none"
               onClick={() => setPlanCollapsed(!planCollapsed)}>
            <span className="text-xs text-[#8a8a8a]">{planCollapsed ? '▶' : '▼'}</span>
            <span>任务计划 ({plan.length} 步)</span>
            <Tag color={connected ? 'success' : 'error'} className="text-xs">
              {connected ? '● SSE' : '○ 断开'}
            </Tag>
            <Tag color={
              state === 'executing' ? 'processing' :
              state === 'paused' ? 'warning' :
              state === 'step_failed' ? 'error' :
              state === 'aborted' ? 'default' :
              state === 'idle' ? 'default' :
              'blue'
            }>
              {state}
            </Tag>
          </div>
        }
        className="shrink-0"
      >
        {!planCollapsed && (plan.length === 0 ? (
          <div className="text-sm text-[#8a8a8a]">请先加载目标位置并点击"加载 Plan"</div>
        ) : (
          <div className="overflow-y-auto min-h-0" style={{ maxHeight: 112 }}>
            {plan.map((step, i) => {
              const st = STEP_ICONS[step.status] || STEP_ICONS.pending;
              const isCurrent = i === currentIndex;
              return (
                <div
                  key={step.step_id}
                  className="flex items-center gap-2 py-1 px-2 rounded text-sm"
                  style={{
                    background: isCurrent ? 'rgba(22,119,255,0.08)' : 'transparent',
                    borderLeft: isCurrent ? '3px solid #1677ff' : '3px solid transparent',
                  }}
                >
                  <span style={{ color: st.color, fontWeight: 'bold', width: 24 }}>
                    {st.icon}
                  </span>
                  <span className="text-[#c8c8c8] w-[100px] shrink-0">
                    {step.step_id}
                  </span>
                  <Tag className="text-xs shrink-0">{step.tool}</Tag>
                  <span className="text-[#8a8a8a] text-xs">{step.args_summary}</span>
                  {isCurrent && (
                    <Tag color="processing" className="text-[10px] ml-auto">当前</Tag>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </Card>

      {/* ── 控制按钮 ──────────────────────────────── */}
      <Card size="small" title={
        <div className="flex items-center gap-2 cursor-pointer select-none"
             onClick={() => setControlCollapsed(!controlCollapsed)}>
          <span className="text-xs text-[#8a8a8a]">{controlCollapsed ? '▶' : '▼'}</span>
          <span>控制</span>
          {autoMode && <Tag color="processing" className="text-[10px] leading-none">自动中</Tag>}
        </div>
      } className="shrink-0">
        {!controlCollapsed && (
        <Space wrap size={8}>
          <Tooltip title={
            pickDisabled
              ? '请先点击「锁定单目标」进入单物体识别模式'
              : (state === 'step_done' ? '执行下一步' : '执行当前步')
          }>
            <Button
              size="small" type="primary"
              icon={<PlayCircleOutlined />}
              disabled={!btns.exec || !canWalk || pickDisabled || autoMode}
              loading={loading && btns.exec}
              onClick={handleExecute}
            >
              {state === 'step_done' ? '下一步' : '执行'}
            </Button>
            <Tooltip title={pickDisabled ? '请先锁定单目标' : '自动执行所有剩余步骤，失败即停'}>
              <Button
                size="small"
                icon={<DoubleRightOutlined />}
                disabled={(!btns.exec || !canWalk || pickDisabled) && !autoMode}
                loading={autoMode}
                onClick={handleAutoRun}
                style={autoMode ? {} : { color: '#52c41a', borderColor: '#52c41a' }}
              >
                {autoMode
                  ? `自动中… ${currentIndex + 1}/${plan.length}`
                  : '自动执行'}
              </Button>
            </Tooltip>
          </Tooltip>
          <Tooltip title={pickDisabled ? '请先锁定单目标' : '重试当前步'}>
            <Button
              size="small"
              icon={<ReloadOutlined />}
              disabled={!btns.retry || pickDisabled || autoMode}
              loading={loading && btns.retry}
              onClick={handleRetry}
            >
              重试
            </Button>
          </Tooltip>
          <Button
            size="small"
            icon={<StepForwardOutlined />}
            disabled={!btns.skip || autoMode}
            onClick={handleSkip}
          >
            跳过
          </Button>
          <Button
            size="small"
            icon={<DoubleRightOutlined />}
            disabled={!btns.fskip || autoMode}
            danger
            onClick={handleForceSkip}
          >
            强制跳过
          </Button>
          <Button
            size="small"
            icon={state === 'paused' ? <CaretRightOutlined /> : <PauseCircleOutlined />}
            disabled={!btns.pause && !btns.resume}
            onClick={state === 'paused' ? handleResume : handlePause}
          >
            {state === 'paused' ? '恢复' : '暂停'}
          </Button>
          <Button
            size="small" danger
            icon={<StopOutlined />}
            disabled={!btns.abort}
            onClick={handleAbort}
          >
            终止
          </Button>
          <Button
            size="small"
            icon={<CameraOutlined />}
            onClick={() => setShowFpVideo(true)}
          >
            FP 画面
          </Button>
          <Button
            size="small"
            icon={<CameraOutlined />}
            onClick={() => setShowDropVis(true)}
            style={{ color: '#faad14', borderColor: '#faad14' }}
          >
            掉箱画面
          </Button>
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={handleResetFp}
            style={{ color: '#52c41a', borderColor: '#52c41a' }}
          >
            重新标定
          </Button>
        </Space>
        )}
      </Card>

      {/* ── 掉箱处理 ──────────────────────────────── */}
      <Card size="small" title={
        <div className="flex items-center gap-2 cursor-pointer select-none"
             onClick={() => setDropCollapsed(!dropCollapsed)}>
          <span className="text-xs text-[#8a8a8a]">{dropCollapsed ? '▶' : '▼'}</span>
          <span>掉箱处理{dropPhase ? ' · ' + dropPhase : ''}</span>
        </div>
      } className="shrink-0"
        style={{ borderColor: dropPhase === 'ready' ? '#52c41a' : '#faad14' }}>
        {!dropCollapsed && (
        <Space wrap size={8}>
          <Tooltip title={dropDetectorEnabled ? '关闭掉箱检测并隐藏可视化画面' : '开启掉箱检测并弹出可视化画面'}>
            <Button
              size="small"
              icon={dropDetectorEnabled ? <StopOutlined /> : <CameraOutlined />}
              onClick={dropDetectorEnabled ? handleDisableDropDetector : handleEnableDropDetector}
              type={dropDetectorEnabled ? 'primary' : 'default'}
              danger={dropDetectorEnabled}
              style={dropDetectorEnabled ? undefined : { color: '#faad14', borderColor: '#faad14' }}
            >
              {dropDetectorEnabled ? '关闭掉箱检测' : '1.开启掉箱检测'}
            </Button>
          </Tooltip>
          <Tooltip title={robotPaused
            ? '发 /nav_pause=false 恢复机器人导航'
            : (navReached != null
              ? (navReached ? '🟢 /nav_reached: 到达锁定，可做后续动作' : '🟡 /nav_reached: 导航中')
              : '发 /nav_pause=true 暂停机器人（速度归零，状态保持）')}>
            <Button
              size="small"
              icon={robotPaused ? <CaretRightOutlined /> : <PauseCircleOutlined />}
              onClick={robotPaused ? handleResumeRobot : handlePauseRobot}
              disabled={!robotPaused && dropPhase !== 'detecting'}
              type={robotPaused ? 'primary' : 'default'}
              style={navReached === true ? { color: '#52c41a', borderColor: '#52c41a' } : undefined}
            >
              {robotPaused ? '2.恢复机器人' : `2.暂停机器人${navReached != null ? (navReached ? ' ✓已到' : ' …导航中') : ''}`}
            </Button>
          </Tooltip>
          <Tooltip title="调用 /set_stand 让机器人放下手臂">
            <Button
              size="small"
              icon={<span>🧍</span>}
              onClick={handleStandRobot}
              disabled={dropPhase !== 'paused'}
              loading={dropPhase === 'standing'}
            >
              3.站立模式
            </Button>
          </Tooltip>
          <Tooltip title="FP MODE_DROPPED 识别掉落箱子位姿">
            <Button
              size="small"
              icon={<span>🔍</span>}
              onClick={handleIdentifyDropped}
              disabled={dropPhase !== 'stood' && dropPhase !== 'detecting'}
              loading={dropPhase === 'identifying'}
              type={dropPhase === 'ready' ? 'primary' : 'default'}
            >
              4.识别掉落箱
            </Button>
          </Tooltip>
          <Tooltip title="用掉落箱子位姿调用 Gateway 搬起">
            <Button
              size="small" danger
              icon={<PlayCircleOutlined />}
              onClick={handleReplanPick}
              disabled={dropPhase !== 'ready'}
            >
              5.重规划搬起
            </Button>
          </Tooltip>
          {droppedBoxId >= 0 && (
            <Tag color="green">箱子 #{droppedBoxId}{droppedBoxPose ? ` (${droppedBoxPose.x.toFixed(2)}, ${droppedBoxPose.y.toFixed(2)})` : ''}</Tag>
          )}
        </Space>
        )}
      </Card>

      {/* ── 当前步骤诊断事件 ────────────────────────── */}
      <Card
        size="small"
        title={
          <div className="flex items-center gap-2 cursor-pointer select-none"
               onClick={() => setDiagCollapsed(!diagCollapsed)}>
            <span className="text-xs text-[#8a8a8a]">{diagCollapsed ? '▶' : '▼'}</span>
            <span>诊断事件 ({diagEvents.length} 条)</span>
            {dropStatus && (
              <Tag color={dropStatus.box_present ? 'green' : 'red'} className="text-[10px] leading-none">
                {dropStatus.box_present ? '🟢 箱子在' : '🔴 箱子掉落!'}
              </Tag>
            )}
          </div>
        }
        className="flex-1 min-h-0 flex flex-col"
        styles={{ body: { flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' } }}
      >
        {!diagCollapsed && (
        <div className="flex-1 overflow-y-auto font-mono min-h-0">
          {diagEvents.length === 0 ? (
            <div className="text-sm text-[#8a8a8a]">等待步骤执行...</div>
          ) : (
            diagEvents.map((ev, i) => (
              <DiagEventRow key={i} ev={ev} />
            ))
          )}
          <div ref={diagEndRef} />
        </div>
        )}
      </Card>

      {/* ── 步骤日志摘要 ────────────────────────────── */}
      <Card size="small" title={
        <div className="flex items-center gap-2 cursor-pointer select-none"
             onClick={() => setLogCollapsed(!logCollapsed)}>
          <span className="text-xs text-[#8a8a8a]">{logCollapsed ? '▶' : '▼'}</span>
          <span>操作日志 ({stepLogs.length} 条)</span>
        </div>
      } className="shrink-0">
        {!logCollapsed && (
        <div className="max-h-40 overflow-y-auto font-mono text-xs">
          {stepLogs.length === 0 ? (
            <div className="text-[#8a8a8a]">暂无记录</div>
          ) : (
            stepLogs.map((log, i) => (
              <div key={i} className="flex gap-2 py-0.5 border-b border-[#2a2a2a] last:border-0">
                <span className="text-[#8a8a8a] shrink-0 w-[60px]">{log.timestamp}</span>
                <Tag
                  color={
                    log.status === 'ok' ? 'success' :
                    log.status === 'error' ? 'error' :
                    log.status === 'skipped' ? 'default' :
                    log.status === 'force_skipped' ? 'warning' :
                    'default'
                  }
                  className="text-[10px] leading-none"
                >
                  {log.status}
                </Tag>
                <span className="text-[#c8c8c8] shrink-0 w-[90px]">{log.step_id}</span>
                <span className="text-[#d4a853] shrink-0 w-[70px]">{log.tool}</span>
                {log.error_code && (
                  <span className="text-[#ff4d4f]">[{log.error_code}]</span>
                )}
                <span className="text-[#8a8a8a]">{log.message}</span>
                <span className="text-[#6a6a6a] ml-auto">{log.elapsed_s}s</span>
              </div>
            ))
          )}
        </div>
        )}
      </Card>

      {/* ── FP 实时画面弹窗 ──────────────────────────── */}
      <Modal
        title="FoundationPose RGB 实时画面"
        open={showFpVideo}
        onCancel={() => setShowFpVideo(false)}
        footer={null}
        width={720}
        styles={{ body: { padding: 8, background: '#141414' } }}
      >
        <img
          src={`/api/fp/video/rgb/stream?t=${Date.now()}`}
          alt="FP RGB"
          style={{ width: '100%', borderRadius: 4, background: '#1a1a1a' }}
        />
      </Modal>

      {/* ── 掉箱检测画面弹窗 ──────────────────────────── */}
      <Modal
        title="掉箱检测 实时画面"
        open={showDropVis}
        onCancel={() => setShowDropVis(false)}
        footer={null}
        width={720}
        styles={{ body: { padding: 8, background: '#141414' } }}
      >
        <img
          src={`/api/fp/video/drop/stream?t=${Date.now()}`}
          alt="Drop Detector"
          style={{ width: '100%', borderRadius: 4, background: '#1a1a1a' }}
        />
      </Modal>
    </div>
  );
}
