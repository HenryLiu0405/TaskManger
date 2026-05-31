import { useEffect, useRef, useState, useCallback } from 'react';

const STEPS_PER_BLOCK = 4;
const BRAILLE = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

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

function resultLine(step, posInBlock, elapsed, spinner) {
  const s = step.status;
  if (s === 'completed') {
    let action;
    if (step.tool === 'move_to') action = posInBlock === 0 ? '到达备货槽位' : '到达目标位置';
    else if (step.tool === 'pick') action = '抓取完成';
    else if (step.tool === 'place') action = '放置完成';
    else action = '完成';
    return `  ✓ ${action} · ${elapsed}`;
  }
  if (s === 'running') {
    let action;
    if (step.tool === 'move_to') action = '移动中';
    else if (step.tool === 'pick') action = '抓取中';
    else if (step.tool === 'place') action = '放置中';
    else action = '执行中';
    return `  ◇ ${action} · ${spinner} ${elapsed}`;
  }
  if (s === 'failed') return `  ✗ 失败`;
  return `  ○ 等待执行`;
}

/* ── Typewriter ── */
function Typewriter({ text, speed = 22, delay = 0, onDone }) {
  const [n, setN] = useState(0);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    setN(0);
    let ok = true;
    let iv;
    const to = setTimeout(() => {
      if (!ok) return;
      let i = 0;
      iv = setInterval(() => { i++; setN(i); if (i >= text.length) { clearInterval(iv); doneRef.current?.(); } }, speed);
    }, delay);
    return () => { ok = false; clearTimeout(to); clearInterval(iv); };
  }, [text, speed, delay]);

  return <>{text.slice(0, n)}</>;
}

/* ── Thinking Phase ── */
function ThinkingPhase({ nodeCount, stepCount, pathStr, onDone }) {
  const [stage, setStage] = useState(0); // 0=analyzing, 1=plan, 2=done

  // Stage 0 → 1 after analyze line types out
  const handleAnalyzeDone = useCallback(() => {
    setTimeout(() => setStage(1), 400);
  }, []);

  // Stage 1 → 2 after plan line appears
  useEffect(() => {
    if (stage === 1) {
      const t = setTimeout(() => setStage(2), 600);
      return () => clearTimeout(t);
    }
  }, [stage]);

  // Notify parent when thinking is complete
  const handlePlanDone = useCallback(() => {
    setTimeout(() => onDone?.(), 300);
  }, [onDone]);

  return (
    <div className="step-detail-panel__thinking">
      <div className="step-detail-panel__think-line">
        {stage === 0 && (
          <Typewriter text="▶ 分析任务需求..." speed={35} onDone={handleAnalyzeDone} />
        )}
        {stage >= 1 && <span className="step-detail-panel__think-done">▶ 分析任务需求... <span className="text-done">✓</span></span>}
      </div>
      {stage >= 1 && (
        <div className="step-detail-panel__think-result">
          <Typewriter text={`  ${nodeCount} 节点 · ${stepCount} 步骤 · 路径 ${pathStr}`} speed={18} />
        </div>
      )}
      {stage >= 2 && (
        <div className="step-detail-panel__think-done-line">
          <Typewriter text="  ✓ 规划完成 · 0.3s" speed={25} onDone={handlePlanDone} />
        </div>
      )}
    </div>
  );
}

/* ── Step row (call line + result line) ── */
function StepRow({ step, posInBlock, status, isPast, timing, tick, showCallTypewriter }) {
  const [callDone, setCallDone] = useState(!showCallTypewriter);
  const [resultDone, setResultDone] = useState(false);
  const prevStatusRef = useRef(status);

  // Reset resultDone when status changes
  useEffect(() => {
    if (status !== prevStatusRef.current) {
      prevStatusRef.current = status;
      setResultDone(false);
    }
  }, [status]);

  const now = performance.now() / 1000;
  const elapsed = timing?.start
    ? `${Math.max(0, (timing.end || now) - timing.start).toFixed(1)}s`
    : '0.0s';
  const spinner = BRAILLE[tick % BRAILLE.length];

  const call = callText(step, posInBlock);
  const result = resultLine(step, posInBlock, elapsed, spinner);
  const isRunning = status === 'running';
  const isCompleted = status === 'completed';
  const isPending = status === 'pending';
  const isFailed = status === 'failed';
  const hasResult = isCompleted || isRunning || isFailed;

  return (
    <div className={`step-detail-panel__step${isRunning ? ' step-detail-panel__step--active' : ''}`}>
      {/* Call line */}
      <div className={`step-detail-panel__call step-detail-panel__call--${status}`}>
        {showCallTypewriter && !callDone ? (
          <>
            <Typewriter key={`call-${step.step_id}`} text={call} speed={18} onDone={() => setCallDone(true)} />
            <span className="step-detail-panel__cursor" />
          </>
        ) : (
          call
        )}
      </div>
      {/* Result line */}
      <div className={`step-detail-panel__result step-detail-panel__result--${status}`}>
        {hasResult ? (
          resultDone ? (
            // After typewriter completes, show live-updating text (needed for running timer)
            result
          ) : (
            <>
              <Typewriter
                key={`res-${step.step_id}-${status}`}
                text={result}
                speed={isRunning ? 18 : 12}
                onDone={() => setResultDone(true)}
              />
              {isRunning && <span className="step-detail-panel__cursor" />}
            </>
          )
        ) : (
          <span>{result}</span>
        )}
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

  // 100ms tick
  useEffect(() => {
    const iv = setInterval(() => setTick(t => t + 1), 100);
    return () => clearInterval(iv);
  }, []);

  // Track step timings
  const now = performance.now() / 1000;
  if (steps) {
    steps.forEach(step => {
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
    // Find nearest scrollable ancestor (the wrapper now handles scrolling)
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
      const allDone = blockSteps.every(s => s.status === 'completed');
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
        {thinkingDone && blocks.map(block => (
          <div key={block.index} className="step-detail-panel__block">
            {/* Block separator */}
            <div className={`step-detail-panel__block-sep${block.allDone ? ' step-detail-panel__block-sep--done' : ''}`}>
              ═══ Block {block.index + 1}/{blocks.length} · Node {block.nodeId} · {block.zoneId} ═══
            </div>

            {block.steps.map((step, si) => {
              const idx = globalIdx++;
              const status = step.status || 'pending';
              const isPast = status === 'completed' || status === 'failed' || idx < currentStepIndex;
              const isCurrent = idx === currentStepIndex;
              const timing = timingsRef.current[step.step_id] || { start: null, end: null };

              // Typewriter for call line: only the first time this step becomes non-pending
              const showTypewriter = isCurrent;

              return (
                <StepRow
                  key={step.step_id}
                  step={step}
                  posInBlock={si}
                  status={status}
                  isPast={isPast}
                  timing={timing}
                  tick={tick}
                  showCallTypewriter={showTypewriter}
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
