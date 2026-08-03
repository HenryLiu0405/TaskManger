import { useEffect, useMemo, useState } from 'react';

const TERMINAL = new Set([
  'completed', 'failed', 'reconciliation_required', 'intervention_required',
]);

function AutonomyConsole({ api }) {
  const [instruction, setInstruction] = useState('把左边的红色箱子放到东北格');
  const [missionId, setMissionId] = useState(null);
  const [snapshot, setSnapshot] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const mission = snapshot?.mission;
  const goal = mission?.metadata?.goal_spec
    || snapshot?.active_revision?.graph?.metadata?.goal_spec;
  const revision = snapshot?.active_revision;
  const events = useMemo(
    () => (snapshot?.events || []).slice(-18).reverse(),
    [snapshot?.events],
  );

  useEffect(() => {
    if (!missionId) return undefined;
    let cancelled = false;
    let timer = null;

    async function refresh() {
      try {
        const next = await api.getAutonomyTask(missionId);
        if (cancelled) return;
        setSnapshot(next);
        setError(null);
        if (TERMINAL.has(next?.mission?.status)) {
          const report = await api.getAutonomyMetrics(missionId);
          if (!cancelled) setMetrics(report);
          return;
        }
        timer = window.setTimeout(refresh, 900);
      } catch (err) {
        if (!cancelled) {
          setError(err.message || 'Unable to refresh autonomy task');
          timer = window.setTimeout(refresh, 1800);
        }
      }
    }

    refresh();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [api, missionId]);

  async function submit(event) {
    event.preventDefault();
    const value = instruction.trim();
    if (!value) return;
    setSubmitting(true);
    setError(null);
    setSnapshot(null);
    setMetrics(null);
    try {
      const result = await api.submitAutonomyTask(value);
      const id = result?.task?.mission_id;
      if (!id) throw new Error('Server returned no mission ID');
      setMissionId(id);
      setSnapshot(result.snapshot || null);
    } catch (err) {
      setError(err.message || 'Autonomy task submission failed');
    } finally {
      setSubmitting(false);
    }
  }

  async function pause() {
    if (!missionId) return;
    try {
      await api.pauseAutonomyTask(missionId);
    } catch (err) {
      setError(err.message || 'Trusted operator authorization is required');
    }
  }

  async function stop() {
    if (!missionId) return;
    try {
      await api.stopAutonomyTask(missionId, 'Web console emergency stop');
    } catch (err) {
      setError(err.message || 'Trusted operator authorization is required');
    }
  }

  return (
    <main className="autonomy-console">
      <header className="autonomy-console__header">
        <div>
          <div className="autonomy-console__eyebrow">GEMINI ROBOTICS · AUTONOMY</div>
          <h1>任务指令与执行监控</h1>
        </div>
        <div className={`autonomy-status autonomy-status--${mission?.status || 'idle'}`}>
          {mission?.status || 'idle'}
        </div>
      </header>

      <form className="autonomy-command" onSubmit={submit}>
        <label htmlFor="autonomy-instruction">自然语言指令</label>
        <div className="autonomy-command__row">
          <textarea
            id="autonomy-instruction"
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            disabled={submitting}
            rows={2}
          />
          <button type="submit" disabled={submitting || !instruction.trim()}>
            {submitting ? 'GROUNDING…' : 'SUBMIT'}
          </button>
        </div>
        <p>提交后任务由 Supervisor 在后台持续执行；关闭或刷新页面不会停止任务。</p>
      </form>

      {error && <div className="autonomy-error">{error}</div>}

      {missionId && (
        <div className="autonomy-grid">
          <section className="autonomy-card autonomy-card--goal">
            <h2>Grounded Goal</h2>
            <dl>
              <div><dt>Mission</dt><dd>{missionId}</dd></div>
              <div><dt>Instruction</dt><dd>{goal?.original_instruction || instruction}</dd></div>
              <div><dt>Object</dt><dd>{goal?.object_ref?.logical_object_id || 'grounding…'}</dd></div>
              <div><dt>Perception ID</dt><dd>{String(goal?.object_ref?.perception_ref?.object_id ?? '—')}</dd></div>
              <div><dt>Tracker</dt><dd>{goal?.object_ref?.perception_ref?.tracker_session_id || '—'}</dd></div>
              <div><dt>Destination</dt><dd>{goal ? `${goal.destination.namespace}/${goal.destination.location_id}` : '—'}</dd></div>
            </dl>
          </section>

          <section className="autonomy-card autonomy-card--plan">
            <h2>Plan · Revision {revision?.revision_number || '—'}</h2>
            <div className="autonomy-plan">
              {(revision?.graph?.nodes || []).map((node) => {
                const invocation = snapshot?.invocations?.find((item) => item.node_id === node.node_id);
                const superseded = revision?.graph?.metadata?.superseded_node_ids?.includes(node.node_id);
                return (
                  <div className="autonomy-plan__node" key={node.node_id}>
                    <span className={`node-state node-state--${superseded ? 'superseded' : (invocation?.status || 'pending')}`} />
                    <div>
                      <strong>{node.skill_name}</strong>
                      <small>{node.node_id}</small>
                    </div>
                    <em>{superseded ? 'superseded' : (invocation?.status || 'pending')}</em>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="autonomy-card autonomy-card--world">
            <h2>Authoritative World State</h2>
            <div className="world-facts">
              {Object.entries(snapshot?.world_state?.dimensions || {}).map(([name, fact]) => (
                <div key={name}>
                  <span>{name}</span>
                  <strong>{fact.value ?? 'unknown'}</strong>
                  <small>{fact.source || 'no source'}</small>
                </div>
              ))}
            </div>
            <div className="autonomy-actions">
              <button type="button" onClick={pause} disabled={!mission || mission.status !== 'running'}>PAUSE</button>
              <button className="danger" type="button" onClick={stop} disabled={!mission || TERMINAL.has(mission.status)}>EMERGENCY STOP</button>
            </div>
            <small className="operator-note">控制操作必须由后端的 trusted-local authorizer 验证。</small>
          </section>

          <section className="autonomy-card autonomy-card--model">
            <h2>VLM Decisions</h2>
            <div className="model-calls">
              {(snapshot?.model_calls || []).slice().reverse().map((call) => (
                <div key={call.call_id}>
                  <strong>{call.task}</strong>
                  <span>{call.status}</span>
                  <small>{call.observation_id} · {call.model}</small>
                </div>
              ))}
              {!snapshot?.model_calls?.length && <p>等待模型决策记录…</p>}
            </div>
          </section>

          <section className="autonomy-card autonomy-card--timeline">
            <h2>Mission / Drop / Recovery Timeline</h2>
            <div className="autonomy-timeline">
              {events.map((item) => (
                <div key={item.event_id}>
                  <time>{new Date(item.occurred_at * 1000).toLocaleTimeString()}</time>
                  <strong>{item.event_type}</strong>
                  <small>{item.node_id || item.payload?.state || ''}</small>
                </div>
              ))}
            </div>
          </section>

          <section className="autonomy-card autonomy-card--metrics">
            <h2>Evaluation</h2>
            <dl>
              <div><dt>VLM calls</dt><dd>{metrics?.vlm?.call_count ?? snapshot?.model_calls?.length ?? 0}</dd></div>
              <div><dt>P95 latency</dt><dd>{metrics?.vlm?.latency_s_p95 != null ? `${metrics.vlm.latency_s_p95.toFixed(2)}s` : '—'}</dd></div>
              <div><dt>Replans</dt><dd>{metrics?.replan_count ?? Math.max((snapshot?.revisions?.length || 1) - 1, 0)}</dd></div>
              <div><dt>ID switches</dt><dd>{metrics?.object_id_switch_count ?? '—'}</dd></div>
              <div><dt>Duplicate actions</dt><dd>{metrics?.duplicate_physical_actions_after_restart ?? 0}</dd></div>
              <div><dt>Estimated cost</dt><dd>{metrics?.vlm?.estimated_cost_usd != null ? `$${metrics.vlm.estimated_cost_usd.toFixed(4)}` : '—'}</dd></div>
            </dl>
          </section>
        </div>
      )}

      <footer className="autonomy-console__footer">
        <a href="?developer=1">Open legacy developer tools</a>
      </footer>
    </main>
  );
}

export default AutonomyConsole;
