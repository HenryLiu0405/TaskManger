import { useMemo } from 'react';
import StepDetailPanel from './StepDetailPanel.jsx';

const ZONE_ID_BY_NODE = {
  '1': 'nw', '2': 'n', '3': 'ne',
  '4': 'w',  '5': 'c', '6': 'e',
  '7': 'sw', '8': 's', '9': 'se',
};

const STEPS_PER_BLOCK = 4;

function UnifiedConsole({ path, missionStatus, isRunning, onRunToggle, onAbort, onReset }) {
  const blocks = useMemo(() => {
    if (!path || path.length === 0) return [];
    return path.map((nodeId, i) => ({
      nodeId,
      zoneId: ZONE_ID_BY_NODE[nodeId] || '?',
      completed: missionStatus?.status === 'completed'
        ? true
        : (missionStatus?.current_step_index || 0) >= (i + 1) * STEPS_PER_BLOCK,
      active: missionStatus?.status === 'running' &&
        Math.floor((missionStatus?.current_step_index || 0) / STEPS_PER_BLOCK) === i,
    }));
  }, [path, missionStatus?.current_step_index, missionStatus?.status]);

  const status = missionStatus?.status;
  const isCompleted = status === 'completed';
  const isPaused = status === 'paused';

  let btnLabel = 'RUN';
  if (isCompleted) btnLabel = 'BACK';
  else if (isRunning) btnLabel = 'PAUSE';
  else if (isPaused) btnLabel = 'RESUME';

  return (
    <div className="unified-console">
      {/* Mini progress bar */}
      {blocks.length > 0 && (
        <div className="unified-console__progress">
          {blocks.map((block, i) => (
            <div key={block.nodeId} className="unified-console__progress-item">
              <div
                className={`unified-console__dot${block.completed ? ' unified-console__dot--done' : ''}${block.active ? ' unified-console__dot--active' : ''}`}
              >
                {block.completed ? '✓' : i + 1}
              </div>
              <div className="unified-console__dot-label">
                {block.zoneId}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Terminal log */}
      <div className="unified-console__terminal">
        {missionStatus?.steps ? (
          <StepDetailPanel
            steps={missionStatus.steps}
            path={path}
            currentStepIndex={missionStatus.current_step_index}
          />
        ) : (
          <div className="unified-console__placeholder">
            {path.length > 0
              ? '▸ Ready · Click RUN to start'
              : '▸ Draw a path on the grid above'}
          </div>
        )}
      </div>

      {/* Action buttons */}
      <div className="unified-console__actions">
        <button
          className={`lock-button lock-button--run${isRunning ? ' is-running' : ''}`}
          type="button"
          onClick={isCompleted ? onReset : onRunToggle}
        >
          {btnLabel}
        </button>
        {isRunning && (
          <button
            className="lock-button lock-button--abort"
            type="button"
            onClick={onAbort}
          >
            ABORT
          </button>
        )}
      </div>
    </div>
  );
}

export default UnifiedConsole;
