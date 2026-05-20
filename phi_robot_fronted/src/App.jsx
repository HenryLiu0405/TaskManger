import { useEffect, useState, useRef } from 'react';
import gsap from 'gsap';
import PhiRobotAPIClient from './api.js';

const GRID_VALUES = [20, 50, 80];
const HIT_RADIUS = 7.5;

const ZONE_MAP = [
  ['nw', 'n', 'ne'],
  ['w',  'c', 'e'],
  ['sw', 's', 'se'],
];

const NODE_LAYOUT = GRID_VALUES.flatMap((y, row) =>
  GRID_VALUES.map((x, col) => ({
    id: String(row * 3 + col + 1),
    label: String(row * 3 + col + 1),
    zoneId: ZONE_MAP[row][col],
    x,
    y,
  })),
);

const ZONE_ID_BY_NODE = Object.fromEntries(
  NODE_LAYOUT.map((n) => [n.id, n.zoneId]),
);

function pointFromEvent(event) {
  const rect = event.currentTarget.getBoundingClientRect();
  return {
    x: ((event.clientX - rect.left) / rect.width) * 100,
    y: ((event.clientY - rect.top) / rect.height) * 100,
  };
}

function resolveNodeAtPoint(point, lockedIds) {
  let nearestNode = null;
  let nearestDistance = Number.POSITIVE_INFINITY;

  for (const node of NODE_LAYOUT) {
    if (lockedIds.has(node.id)) continue;
    const dx = point.x - node.x;
    const dy = point.y - node.y;
    const distance = dx * dx + dy * dy;
    if (distance <= HIT_RADIUS * HIT_RADIUS && distance < nearestDistance) {
      nearestNode = node;
      nearestDistance = distance;
    }
  }

  return nearestNode;
}

function buildSegments(path) {
  return path.slice(1).map((nodeId, index) => ({
    id: `${path[index]}-${nodeId}-${index}`,
    from: path[index],
    to: nodeId,
  }));
}

const api = new PhiRobotAPIClient();

function App() {
  const [isRunning, setIsRunning] = useState(false);
  const [isDrawing, setIsDrawing] = useState(false);
  const [path, setPath] = useState([]);
  const [previewPoint, setPreviewPoint] = useState(null);
  const [missionStatus, setMissionStatus] = useState(null);
  const [logLines, setLogLines] = useState([]);
  const terminalLogRef = useRef(null);
  const prevStepRef = useRef(null);
  const [lastError, setLastError] = useState(null);
  // removed isTerminalOpen

  useEffect(() => {
    document.title = '九宫格锁屏式 Demo';
  }, []);

  // GSAP-based background and state interactions
  useEffect(() => {
    document.body.classList.toggle('mission-running', isRunning);
    document.body.classList.toggle('is-drawing', isDrawing);

    const bgVideo = document.getElementById('bg-video');
    if (!bgVideo) return;

    if (isRunning && missionStatus) {
      if (missionStatus.status === 'completed') {
        // Force completely clear background
        gsap.to(bgVideo, {
          filter: `blur(0px)`,
          scale: 1,
          duration: 1.2,
          ease: "power2.out"
        });
        gsap.to(".terminal-window", { 
          boxShadow: "0 10px 40px rgba(0,0,0,0.9), 0 0 30px rgba(57,255,20,0.15) inset", duration: 0.8 
        });
      } else {
        const total = missionStatus.total_steps || 1;
        const current = missionStatus.current_step_index ?? 0;
        const progressRatio = Math.min(1, current / total);

        const currentBlur = 12 * (1 - progressRatio); // Start at 12px, down to 0px
        const currentScale = 1.05 - (0.05 * progressRatio);

        gsap.to(bgVideo, {
          filter: `blur(${currentBlur}px)`,
          scale: currentScale,
          duration: 1.2,
          ease: "power2.out"
        });

        gsap.fromTo(".terminal-window", 
          { boxShadow: "0 10px 50px rgba(57,255,20,0.4)" },
          { boxShadow: "0 10px 40px rgba(0,0,0,0.9), 0 0 30px rgba(57,255,20,0.15) inset", duration: 0.8 }
        );
      }
    } else if (isDrawing) {
      gsap.to(bgVideo, {
        filter: `blur(6px)`, // Slightly clearer when interacting with lock
        scale: 1.06,
        duration: 0.3,
        ease: "power2.out"
      });
      // Micro interaction for drawing
      gsap.to(".terminal-window", { scale: 1.01, duration: 0.2, ease: "back.out(1.7)" });
    } else {
      // Idle / Default Lock Screen
      gsap.to(bgVideo, {
        filter: `blur(12px)`, // Default very blurred state
        scale: 1.05,
        duration: 0.7,
        ease: "power2.out"
      });
      gsap.to(".terminal-window", { scale: 1, duration: 0.4, ease: "power2.out" });
    }
  }, [isRunning, isDrawing, missionStatus?.current_step_index, missionStatus?.status]);

  const lockedIds = new Set(path);
  const latestNodeId = path[path.length - 1] ?? null;
  const latestNode = latestNodeId ? NODE_LAYOUT.find((node) => node.id === latestNodeId) : null;
  const segments = buildSegments(path);
  const hasPreview = !isRunning && isDrawing && previewPoint && latestNode;

  function appendNode(nodeId) {
    setPath((currentPath) => {
      if (currentPath.includes(nodeId)) return currentPath;
      return [...currentPath, nodeId];
    });
  }

  function endDrawing() {
    setIsDrawing(false);
    setPreviewPoint(null);
  }

  function handlePointerDown(event) {
    if (isRunning) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setIsDrawing(true);
    const point = pointFromEvent(event);
    setPreviewPoint(point);
    const node = resolveNodeAtPoint(point, lockedIds);
    if (node) appendNode(node.id);
  }

  function handlePointerMove(event) {
    if (isRunning || !isDrawing) return;
    event.preventDefault();
    const point = pointFromEvent(event);
    setPreviewPoint(point);
    const node = resolveNodeAtPoint(point, lockedIds);
    if (node) appendNode(node.id);
  }

  function handlePointerUp(event) {
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    endDrawing();
  }

  function handlePointerCancel(event) {
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    endDrawing();
  }

  async function startMission() {
    if (path.length === 0) {
      setLastError('请先在九宫格上画出路径');
      return;
    }

    setLastError(null);
    const destinationOrder = path.map((nodeId) => ZONE_ID_BY_NODE[nodeId]);

    try {
      const submitted = await api.submitMission(destinationOrder);
      setMissionStatus({ ...submitted, current_step_index: 0, total_steps: destinationOrder.length });
      // reset UI-only terminal log when a new mission starts
      setLogLines([]);
      prevStepRef.current = 0;

      try {
        const runResult = await api.runMission();
        setMissionStatus((prev) => ({ ...prev, ...runResult }));

        api.startPolling((status) => {
          setMissionStatus(status);
          if (status.status === "paused") {
            setIsRunning(false);
            api.stopPolling();
          }
        }, 500);

        setIsRunning(true);
        endDrawing();
      } catch (err) {
        // Special handling for 409 / conflict: mission already running
        if (err && err.status === 409) {
          const wantView = window.confirm(`${err.message}\n\n该任务当前已在运行。按“确定”以查看并跟踪任务，按“取消”以尝试中止它。`);
          if (wantView) {
            try {
              const status = await api.getMissionStatus();
              setMissionStatus(status);
              api.startPolling((status) => {
                setMissionStatus(status);
                if (status.status === "paused") {
                  setIsRunning(false);
                  api.stopPolling();
                }
              }, 500);
              setIsRunning(status.status !== 'paused');
            } catch (e) {
              setLastError(e.message || '无法获取任务状态');
            }
          } else {
            try {
              await api.abortMission();
              setLastError('已发送终止请求');
              setIsRunning(false);
              setMissionStatus(null);
            } catch (e) {
              setLastError(e.message || '终止任务失败');
            }
          }
        } else {
          setLastError(err.message || '启动任务失败');
        }
      }
    } catch (err) {
      setLastError(err.message || '启动任务失败');
    }
  }

  async function stopMission() {
    try {
      await api.abortMission();
    } catch (_) {
      // ignore abort errors — server may have already finished
    }
    api.stopPolling();
    setIsRunning(false);
    setMissionStatus(null);
  }

  function handleReset() {
    setIsRunning(false);
    setMissionStatus(null);
    setLogLines([]);
    setPath([]);
    setLastError(null);
  }

  async function handleRunToggle() {
    if (isRunning) {
      // 运行中 → 暂停
      try {
        await api.pauseMission();
      } catch (err) {
        setLastError(err.message || "暂停任务失败");
      }
    } else if (missionStatus && missionStatus.status === "paused") {
      // 已暂停 → 恢复
      try {
        await api.resumeMission();
        setIsRunning(true);
        api.startPolling((status) => {
          setMissionStatus(status);
          if (status.status === "paused") {
            setIsRunning(false);
            api.stopPolling();
          }
        }, 500);
      } catch (err) {
        setLastError(err.message || "恢复任务失败");
      }
    } else {
      // 初始 → 提交并运行
      startMission();
    }
  }

  async function handleAbort() {
    stopMission();
  }

  const progress =
    missionStatus && missionStatus.total_steps > 0
      ? Math.round((missionStatus.current_step_index / missionStatus.total_steps) * 100)
      : 0;

  // Watch missionStatus changes to append terminal-style log lines (UI-only)
  useEffect(() => {
    if (!missionStatus) {
      prevStepRef.current = null;
      return;
    }

    const total = missionStatus.total_steps || 0;
    const current = missionStatus.current_step_index ?? 0;

    if (prevStepRef.current === null) {
      // initial set - seed ref but don't print a completion line yet
      prevStepRef.current = current;
    } else if (current > prevStepRef.current) {
      // one or more steps completed since last update
      const newLines = [];
      for (let i = prevStepRef.current + 1; i <= current; i++) {
        const pct = total > 0 ? Math.round((i / total) * 100) : 100;
        const blocks = 20;
        const filled = Math.round((pct / 100) * blocks);
        const bar = '█'.repeat(filled) + ' '.repeat(blocks - filled);
        newLines.push(`步骤 ${i}/${total} [${bar}] ${pct}% 完成`);
      }
      setLogLines((prev) => [...prev, ...newLines]);
      prevStepRef.current = current;
    }

    if (missionStatus.status === 'completed') {
      setLogLines((prev) => [...prev, '已全部完成']);
    }
  }, [missionStatus]);

  // Auto-scroll terminal log to bottom when new lines appear
  useEffect(() => {
    if (terminalLogRef.current) {
      terminalLogRef.current.scrollTop = terminalLogRef.current.scrollHeight;
    }
  }, [logLines]);

  return (
    <div className="terminal-stage">
      <div className="terminal-modal" role="dialog" aria-modal="true" aria-label="terminal window">
        <div className="terminal-window">
          <div className="terminal-window__chrome">
            <div className="terminal-window__title">
              <span className="terminal-window__prompt">[ phi_robot@terminal ]</span>
              <span className="terminal-window__subtitle">16:9 session</span>
            </div>
            {missionStatus && ['completed', 'failed', 'aborted'].includes(missionStatus.status) && (
              <button
                className="terminal-window__reset"
                type="button"
                onClick={handleReset}
                aria-label="Return to full console"
              >
                返回控制台
              </button>
            )}
          </div>

          <div className="terminal-window__body">
            <div className="app-shell">
              <section
                className={`lock-board ${isRunning ? 'lock-board--running' : 'lock-board--idle'} ${isDrawing ? 'lock-board--drawing' : ''}`}
              >
                  <svg className="lock-board__svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
                    {segments.map((segment, index) => {
                      const fromNode = NODE_LAYOUT.find((node) => node.id === segment.from);
                      const toNode = NODE_LAYOUT.find((node) => node.id === segment.to);
                      if (!fromNode || !toNode) return null;
                      return (
                        <line
                          key={segment.id}
                          className="lock-line"
                          x1={fromNode.x}
                          y1={fromNode.y}
                          x2={toNode.x}
                          y2={toNode.y}
                          style={{ opacity: 0.38 + Math.min(index * 0.08, 0.3) }}
                        />
                      );
                    })}
                    {hasPreview ? (
                      <line
                        className="lock-line lock-line--preview"
                        x1={latestNode.x}
                        y1={latestNode.y}
                        x2={previewPoint.x}
                        y2={previewPoint.y}
                      />
                    ) : null}
                  </svg>

                  <div
                    className="lock-board__grid"
                    onPointerDown={handlePointerDown}
                    onPointerMove={handlePointerMove}
                    onPointerUp={handlePointerUp}
                    onPointerCancel={handlePointerCancel}
                  >
                    {NODE_LAYOUT.map((node) => {
                      const isLocked = lockedIds.has(node.id);
                      const isLatest = node.id === latestNodeId;
                      return (
                        <div
                          key={node.id}
                          className={`lock-node ${isLocked ? 'lock-node--locked' : 'lock-node--idle'} ${isLatest ? 'lock-node--latest' : ''}`}
                          style={{ left: `${node.x}%`, top: `${node.y}%` }}
                          aria-hidden="true"
                        >
                          <span>{node.label}</span>
                        </div>
                      );
                    })}
                  </div>
                </section>

                <div className="lock-controls">
                  <button
                    className={`lock-button lock-button--run ${isRunning ? 'is-running' : ''}`}
                    type="button"
                    onClick={handleRunToggle}
                  >
                    {isRunning ? '暂停' : missionStatus?.status === 'paused' ? '恢复' : '运行'}
                  </button>
                  <button
                    className="lock-button lock-button--abort"
                    type="button"
                    onClick={handleAbort}
                    disabled={!isRunning && (!missionStatus || missionStatus.status !== 'paused')}
                  >
                    终止任务
                  </button>
                </div>

                {lastError && <div className="status-bar status-bar--error">{lastError}</div>}

                {missionStatus && (
                  <div className="status-bar status-bar--terminal">
                    <div className="terminal-log" ref={terminalLogRef} role="log" aria-live="polite">
                      {logLines.length === 0 ? (
                        <div className="terminal-line terminal-line--muted">等待任务开始...</div>
                      ) : (
                        logLines.map((l, i) => (
                          <div key={i} className={`terminal-line ${l === '已全部完成' ? 'terminal-line--done' : ''}`}>
                            {l}
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
    </div>
  );
}

export default App;
