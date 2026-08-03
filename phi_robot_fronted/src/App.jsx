import { useEffect, useState, useCallback } from 'react';
import gsap from 'gsap';
import PhiRobotAPIClient from './api.js';
import UnifiedConsole from './UnifiedConsole.jsx';
import LockBoard2D from './LockBoard2D.jsx';
import AutonomyConsole from './AutonomyConsole.jsx';

const ZONE_ID_BY_NODE = {
  '1': 'se', '2': 's', '3': 'sw',
  '4': 'e',  '5': 'c', '6': 'w',
  '7': 'ne', '8': 'n', '9': 'nw',
};

const api = new PhiRobotAPIClient();

function App() {
  const developerMode = new URLSearchParams(window.location.search).get('developer') === '1';
  const [isRunning, setIsRunning] = useState(false);
  const [path, setPath] = useState([]);
  const [missionStatus, setMissionStatus] = useState(null);
  const [lastError, setLastError] = useState(null);
  const [planId, setPlanId] = useState(0);

  useEffect(() => {
    document.title = developerMode
      ? 'phi_robot Developer Console'
      : 'phi_robot Gemini Autonomy';
  }, [developerMode]);

  useEffect(() => {
    const bgImage = document.getElementById('bg-image');
    if (!bgImage) return;

    if (isRunning && missionStatus) {
      gsap.to(bgImage, { opacity: 1, duration: 1.2, ease: 'power2.out' });
    } else {
      gsap.to(bgImage, { opacity: 0.7, duration: 0.7, ease: 'power2.out' });
    }
  }, [isRunning]);

  const appendNode = useCallback((nodeId) => {
    setPath((currentPath) => {
      if (currentPath.includes(nodeId)) return currentPath;
      return [...currentPath, nodeId];
    });
  }, []);

  // SSE 订阅：监听 StepDebugController 状态变更，同步到主页 UI
  useEffect(() => {
    if (planId === 0) return; // 尚未加载计划

    const es = new EventSource('/api/dev/step_debug/stream');

    es.addEventListener('state', (msg) => {
      try {
        const payload = JSON.parse(msg.data);

        // 映射 step_debug state → mission status
        const stateStatusMap = {
          step_ready: 'ready',
          executing: 'running',
          step_done: 'running',
          paused: 'paused',
          step_failed: 'failed',
          idle: 'completed',
          aborted: 'aborted',
        };

        setMissionStatus((prev) => {
          if (!prev || !prev.steps) return prev;
          const ci = payload.current_index;
          const updatedSteps = prev.steps.map((step, i) => {
            let status = 'pending';
            if (i < ci) {
              status = 'completed';
            } else if (i === ci) {
              if (payload.state === 'step_failed') status = 'failed';
              else if (payload.state === 'executing') status = 'running';
              else status = 'current';
            }
            return { ...step, status };
          });

          return {
            ...prev,
            status: stateStatusMap[payload.state] || prev.status,
            current_step_index: ci,
            steps: updatedSteps,
          };
        });

        setIsRunning(
          payload.state === 'executing' || payload.state === 'step_done'
        );
      } catch (_) { /* ignore malformed SSE */ }
    });

    return () => es.close();
  }, [planId]);

  async function startMission() {
    if (path.length === 0) {
      setLastError('Please draw a path on the grid first');
      return;
    }

    setLastError(null);
    const destinations = path.map((nodeId) => ZONE_ID_BY_NODE[nodeId]);

    try {
      const res = await fetch('/api/dev/step_debug/load', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ destinations }),
      });
      const data = await res.json();

      if (!data.ok) {
        setLastError(data.message || 'Failed to load plan');
        return;
      }

      // 计划已加载到 StepDebugController，等待操作员在 debug.html 执行
      setMissionStatus({
        status: 'ready',
        current_step_index: 0,
        total_steps: data.total_steps,
        steps: data.raw_steps,
      });
      setIsRunning(false);
      // 触发 SSE 连接
      setPlanId((prev) => prev + 1);
    } catch (err) {
      setLastError(err.message || 'Failed to load plan');
    }
  }

  async function stopMission() {
    try {
      await api.abortMission(missionStatus?.mission_id);
    } catch (_) {
      // ignore abort errors
    }
    api.stopPolling();
    setIsRunning(false);
    setMissionStatus(null);
  }

  function handleReset() {
    setIsRunning(false);
    setMissionStatus(null);
    setPath([]);
    setLastError(null);
  }

  async function handleRunToggle() {
    if (isRunning) {
      try {
        await api.pauseMission(missionStatus?.mission_id);
      } catch (err) {
        setLastError(err.message || 'Failed to pause');
      }
    } else if (missionStatus && missionStatus.status === 'paused') {
      try {
        await api.resumeMission(missionStatus.mission_id);
        setIsRunning(true);
        api.startPolling((status) => {
          setMissionStatus(status);
          if (status.status === 'paused') {
            setIsRunning(false);
            api.stopPolling();
          }
        }, 500);
      } catch (err) {
        setLastError(err.message || 'Failed to resume');
      }
    } else {
      startMission();
    }
  }

  async function handleAbort() {
    stopMission();
  }

  return (
    <div className="terminal-stage">
      <div className="terminal-modal">
        {/* Brand header */}
        <div className="waic-brand" aria-hidden="true">
          <div className="waic-brand__main">waic2026</div>
          <div className="waic-brand__sub">phi</div>
        </div>

        {!developerMode && <AutonomyConsole api={api} />}

        {developerMode && (
          <>
            <LockBoard2D
              path={path}
              isRunning={isRunning}
              onAppendNode={appendNode}
            />

            <UnifiedConsole
              path={path}
              missionStatus={missionStatus}
              isRunning={isRunning}
              onRunToggle={handleRunToggle}
              onAbort={handleAbort}
              onReset={handleReset}
            />
          </>
        )}

        {/* Error bar */}
        {lastError && (
          <div className="error-bar">{lastError}</div>
        )}
      </div>
    </div>
  );
}

export default App;
