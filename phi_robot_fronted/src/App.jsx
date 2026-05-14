import { useEffect, useState } from 'react';
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
  const [lastError, setLastError] = useState(null);

  useEffect(() => {
    document.title = '九宫格锁屏式 Demo';
  }, []);

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

  async function handleRunToggle() {
    if (isRunning) {
      // 运行中 → 中止
      stopMission();
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

  async function handlePause() {
    if (!isRunning) return;
    try {
      await api.pauseMission();
    } catch (err) {
      setLastError(err.message || "暂停任务失败");
    }
  }

  const progress =
    missionStatus && missionStatus.total_steps > 0
      ? Math.round((missionStatus.current_step_index / missionStatus.total_steps) * 100)
      : 0;

  return (
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
          {isRunning ? '运行中' : missionStatus?.status === 'paused' ? '恢复' : '运行'}
        </button>
        <button
          className="lock-button lock-button--pause"
          type="button"
          onClick={handlePause}
          disabled={!isRunning}
        >
          强制暂停
        </button>
      </div>

      {lastError && <div className="status-bar status-bar--error">{lastError}</div>}

      {missionStatus && (
        <div className="status-bar">
          <span className="status-bar__label">
            {missionStatus.status === 'running' && '执行中'}
            {missionStatus.status === 'completed' && '已完成'}
            {missionStatus.status === 'failed' && '失败'}
            {missionStatus.status === 'aborted' && '已中止'}
            {missionStatus.status === 'paused' && '已暂停'}
            {missionStatus.status === 'pending' && '等待中'}
          </span>
          <span className="status-bar__steps">
            步骤 {missionStatus.current_step_index ?? 0} / {missionStatus.total_steps ?? 0}
          </span>
          <div className="status-bar__track">
            <div className="status-bar__fill" style={{ width: `${progress}%` }} />
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
