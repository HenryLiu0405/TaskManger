import { useEffect, useState } from 'react';
import gsap from 'gsap';
import PhiRobotAPIClient from './api.js';
import UnifiedConsole from './UnifiedConsole.jsx';
import LockBoard2D from './LockBoard2D.jsx';

const ZONE_ID_BY_NODE = {
  '1': 'nw', '2': 'n', '3': 'ne',
  '4': 'w',  '5': 'c', '6': 'e',
  '7': 'sw', '8': 's', '9': 'se',
};

const api = new PhiRobotAPIClient();

function App() {
  const [isRunning, setIsRunning] = useState(false);
  const [isDrawing, setIsDrawing] = useState(false);
  const [path, setPath] = useState([]);
  const [missionStatus, setMissionStatus] = useState(null);
  const [lastError, setLastError] = useState(null);

  useEffect(() => {
    document.title = 'phi_robot 九宫格锁屏式 Demo';
  }, []);

  useEffect(() => {
    const bgImage = document.getElementById('bg-image');
    if (!bgImage) return;

    if (isRunning && missionStatus) {
      gsap.to(bgImage, { opacity: 1, duration: 1.2, ease: 'power2.out' });
    } else if (isDrawing) {
      gsap.to(bgImage, { opacity: 0.5, duration: 0.3, ease: 'power2.out' });
    } else {
      gsap.to(bgImage, { opacity: 0.7, duration: 0.7, ease: 'power2.out' });
    }
  }, [isRunning, isDrawing]);

  function appendNode(nodeId) {
    setPath((currentPath) => {
      if (currentPath.includes(nodeId)) return currentPath;
      return [...currentPath, nodeId];
    });
  }

  function handleDrawStart() {
    if (isRunning) return;
    setIsDrawing(true);
  }

  function handleDrawEnd() {
    setIsDrawing(false);
  }

  async function startMission() {
    if (path.length === 0) {
      setLastError('Please draw a path on the grid first');
      return;
    }

    setLastError(null);
    const destinationOrder = path.map((nodeId) => ZONE_ID_BY_NODE[nodeId]);

    try {
      const submitted = await api.submitMission(destinationOrder);
      setMissionStatus({ ...submitted, current_step_index: 0, total_steps: destinationOrder.length });

      try {
        const runResult = await api.runMission(submitted.mission_id);
        setMissionStatus((prev) => ({ ...prev, ...runResult }));

        api.startPolling((status) => {
          setMissionStatus(status);
          if (status.status === 'paused') {
            setIsRunning(false);
            api.stopPolling();
          }
        }, 500);

        setIsRunning(true);
        setIsDrawing(false);
      } catch (err) {
        if (err && err.status === 409) {
          const wantView = window.confirm(`${err.message}\n\nMission already running. OK to view, Cancel to abort.`);
          if (wantView) {
            try {
              const status = await api.getMissionStatus(submitted.mission_id);
              setMissionStatus(status);
              api.startPolling((status) => {
                setMissionStatus(status);
                if (status.status === 'paused') {
                  setIsRunning(false);
                  api.stopPolling();
                }
              }, 500);
              setIsRunning(status.status !== 'paused');
            } catch (e) {
              setLastError(e.message || 'Failed to get mission status');
            }
          } else {
            try {
              await api.abortMission(submitted.mission_id);
              setLastError('Abort request sent');
              setIsRunning(false);
              setMissionStatus(null);
            } catch (e) {
              setLastError(e.message || 'Failed to abort');
            }
          }
        } else {
          setLastError(err.message || 'Failed to start mission');
        }
      }
    } catch (err) {
      setLastError(err.message || 'Failed to start mission');
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

        {/* LockBoard — always visible, shrinks during running */}
        <LockBoard2D
          path={path}
          isRunning={isRunning}
          isDrawing={isDrawing}
          onDrawStart={handleDrawStart}
          onDrawEnd={handleDrawEnd}
          onAppendNode={appendNode}
        />

        {/* Unified Console — always visible */}
        <UnifiedConsole
          path={path}
          missionStatus={missionStatus}
          isRunning={isRunning}
          onRunToggle={handleRunToggle}
          onAbort={handleAbort}
          onReset={handleReset}
        />

        {/* Error bar */}
        {lastError && (
          <div className="error-bar">{lastError}</div>
        )}
      </div>
    </div>
  );
}

export default App;
