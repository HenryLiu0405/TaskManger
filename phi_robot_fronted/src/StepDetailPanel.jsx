import { useEffect, useRef, useState } from 'react';

const STEPS_PER_BLOCK = 4;

const ZONE_ID_BY_NODE = {
  '1': 'nw', '2': 'n', '3': 'ne',
  '4': 'w',  '5': 'c', '6': 'e',
  '7': 'sw', '8': 's', '9': 'se',
};

/* ── helpers ── */
function fmtCoord(args) {
  const t = args?.target || args;
  const x = t.x?.toFixed(1) ?? '?';
  const y = t.y?.toFixed(1) ?? '?';
  return `(${x}, ${y})`;
}

function callText(step, posInBlock) {
  const tool = (step.tool || '?').padEnd(7);
  if (step.tool === 'move_to') {
    const dest = posInBlock === 0 ? '备货槽位' : '目标位置';
    return `▶ ${tool} 移动到${dest} ${fmtCoord(step.args)}`;
  }
  if (step.tool === 'pick') return `▶ ${tool} 抓取方块 ${step.args?.object_id || '?'}`;
  if (step.tool === 'place') return `▶ ${tool} 放置方块 ${fmtCoord(step.args)}`;
  return `▶ ${tool} ${step.tool}`;
}

function runningLabel(step) {
  if (step.tool === 'move_to') return '移动中';
  if (step.tool === 'pick') return '抓取中';
  if (step.tool === 'place') return '放置中';
  return '执行中';
}

function doneLabel(step, posInBlock) {
  if (step.tool === 'move_to') return posInBlock === 0 ? '到达备货槽位' : '到达目标位置';
  if (step.tool === 'pick') return '抓取完成';
  if (step.tool === 'place') return '放置完成';
  return '完成';
}

/* ── Breathing dots — CSS-only running indicator (no character mutation) ── */
function RunDots() {
  return (
    <span className="step-dots" aria-hidden="true">
      <i /><i /><i />
    </span>
  );
}

/* ── Result line — rendered once, no typewriter ── */
function ResultLine({ step, posInBlock, elapsed, status }) {
  if (status === 'completed') {
    return <>{`  ✓ ${doneLabel(step, posInBlock)} · ${elapsed}`}</>;
  }
  if (status === 'running') {
    return (
      <>
        {`  ◇ ${runningLabel(step)} `}
        <RunDots />
        {` ${elapsed}`}
      </>
    );
  }
  if (status === 'failed') return <>{'  ✗ 失败'}</>;
  return <>{'  ○ 等待执行'}</>;
}

/* ── Thinking phase — staged fade reveals, no per-character typing ── */
function ThinkingPhase({ nodeCount, stepCount, pathStr, onDone }) {
  const [stage, setStage] = useState(0); // 0=analyzing · 1=result · 2=planned
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    const t1 = setTimeout(() => setStage(1), 500);
    const t2 = setTimeout(() => setStage(2), 1000);
    const t3 = setTimeout(() => doneRef.current?.(), 1500);
    return () => { clearTimeout(t1); clearTimeout(t2); clearTimeout(t3); };
  }, []);

  return (
    <div className="step-detail-panel__thinking">
      <div className="step-detail-panel__think-line">
        {stage === 0 ? (
          <span>▶ 分析任务需求 <RunDots /></span>
        ) : (
          <span className="step-detail-panel__think-done">
            ▶ 分析任务需求 <span className="text-done">✓</span>
          </span>
        )}
      </div>
      {stage >= 1 && (
        <div className="step-detail-panel__think-result reveal">
          {`  ${nodeCount} 节点 · ${stepCount} 步骤 · 路径 ${pathStr}`}
        </div>
      )}
      {stage >= 2 && (
        <div className="step-detail-panel__think-done-line reveal">
          {'  ✓ 规划完成 · 0.3s'}
        </div>
      )}
    </div>
  );
}

/* ── Step row (call line + result line) ── */
function StepRow({ step, posInBlock, status, timing }) {
  const now = performance.now() / 1000;
  const elapsed = timing?.start
    ? `${Math.max(0, (timing.end || now) - timing.start).toFixed(1)}s`
    : '0.0s';

  const isRunning = status === 'running';

  return (
    <div className={`step-detail-panel__step${isRunning ? ' step-detail-panel__step--active' : ''}`}>
      <div className={`step-detail-panel__call step-detail-panel__call--${status}`}>
        {callText(step, posInBlock)}
      </div>
      <div className={`step-detail-panel__result step-detail-panel__result--${status}`}>
        <ResultLine step={step} posInBlock={posInBlock} elapsed={elapsed} status={status} />
      </div>
    </div>
  );
}

/* ── Main Panel ── */
function StepDetailPanel({ steps, path, currentStepIndex }) {
  const containerRef = useRef(null);
  const bottomRef = useRef(null);
  const timingsRef = useRef({});
  const [tick, setTick] = useState(0);
  const [thinkingDone, setThinkingDone] = useState(false);

  // Live timer tick — runs ONLY while a step is executing, so the panel
  // isn't re-rendering 10×/s during the thinking phase or after completion.
  const hasRunning = !!steps && steps.some((s) => s.status === 'running');
  useEffect(() => {
    if (!hasRunning) return undefined;
    const iv = setInterval(() => setTick((t) => t + 1), 100);
    return () => clearInterval(iv);
  }, [hasRunning]);
  void tick;

  // Track step timings
  const now = performance.now() / 1000;
  if (steps) {
    steps.forEach((step) => {
      if (!timingsRef.current[step.step_id]) {
        timingsRef.current[step.step_id] = { start: null, end: null };
      }
      const t = timingsRef.current[step.step_id];
      if (step.status === 'running' && t.start === null) t.start = now;
      if ((step.status === 'completed' || step.status === 'failed') && t.end === null) {
        t.end = now;
        if (t.start === null) t.start = now;
      }
    });
  }

  // Auto-scroll only on step advance, and only when user is near bottom
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let p = el.parentElement;
    while (p) {
      const s = window.getComputedStyle(p);
      if (s.overflowY === 'auto' || s.overflowY === 'scroll') break;
      p = p.parentElement;
    }
    const scroller = p || el;
    const atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 50;
    if (atBottom) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [currentStepIndex]);

  // Group into blocks
  const blocks = [];
  if (steps && steps.length > 0) {
    for (let i = 0; i < steps.length; i += STEPS_PER_BLOCK) {
      const bi = Math.floor(i / STEPS_PER_BLOCK);
      const nodeId = path?.[bi] || '?';
      const zoneId = ZONE_ID_BY_NODE[nodeId] || '?';
      const blockSteps = steps.slice(i, i + STEPS_PER_BLOCK);
      const allDone = blockSteps.every((s) => s.status === 'completed');
      const blockNow = performance.now() / 1000;
      const totalS = blockSteps.reduce((sum, s) => {
        const t = timingsRef.current[s.step_id];
        if (!t) return sum;
        return sum + Math.max(0, (t.end || blockNow) - (t.start || blockNow));
      }, 0);
      blocks.push({ index: bi, nodeId, zoneId, steps: blockSteps, allDone, totalS });
    }
  }

  const pathStr = (path || []).join(' → ');
  let globalIdx = 0;

  return (
    <div className="step-detail-panel" ref={containerRef}>
      <div className="step-detail-panel__inner">
        {/* Thinking phase */}
        {!thinkingDone && (
          <ThinkingPhase
            nodeCount={path?.length || 0}
            stepCount={steps?.length || 0}
            pathStr={pathStr}
            onDone={() => setThinkingDone(true)}
          />
        )}

        {/* Blocks */}
        {thinkingDone && blocks.map((block) => (
          <div
            key={block.index}
            className="step-detail-panel__block reveal"
            style={{ animationDelay: `${Math.min(block.index * 0.08, 0.4)}s` }}
          >
            {/* Block separator */}
            <div className={`step-detail-panel__block-sep${block.allDone ? ' step-detail-panel__block-sep--done' : ''}`}>
              ═══ Block {block.index + 1}/{blocks.length} · Node {block.nodeId} · {block.zoneId} ═══
            </div>

            {block.steps.map((step, si) => {
              const idx = globalIdx++;
              void idx;
              const status = step.status || 'pending';
              const timing = timingsRef.current[step.step_id] || { start: null, end: null };

              return (
                <StepRow
                  key={step.step_id}
                  step={step}
                  posInBlock={si}
                  status={status}
                  timing={timing}
                />
              );
            })}

            {/* Block summary */}
            {block.allDone && (
              <div className="step-detail-panel__block-done">
                ✓ Block {block.index + 1} 完成 · {Math.max(0, block.totalS).toFixed(1)}s
              </div>
            )}
          </div>
        ))}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}

export default StepDetailPanel;
