# Phase 8 console, replay, evaluation, and deployment status

Status date: 2026-08-18. Offline console/replay slice; HIL and deployment gates
were not run.

## Implemented

- The default React page accepts one natural-language instruction and monitors
  grounded object/location, plan and revisions, invocation states, authoritative
  World State, VLM calls, mission/drop/recovery timeline, and available metrics.
  Legacy grid/stage tooling remains reachable only with `?developer=1`.
- Page refresh/closure affects only polling; mission execution lives in the
  Supervisor process.
- The instruction area polls `/api/autonomy/readiness` and displays VLM,
  camera-frame, FoundationPose-state, ROS-service, robot-odometry/control, and
  Supervisor evidence. Missing or stale evidence is never rendered as ready;
  VLM remains `configured` until a real model call succeeds.
- `/api/autonomy/tasks` exposes submission and monitoring. Replay and metrics
  endpoints are read-only. Pause/stop endpoints fail closed unless the host
  application injects a trusted operator authorizer.
- `ReplayBundle` exports mission, every plan revision, action/result,
  interruption, event, World State, structured model decisions, and exact frame
  hashes. It excludes image bytes, prompt text, and API keys.
- Per-mission metrics include duration, VLM calls/P50/P95/cost fields, replans,
  recovery, ID switches, stale rejection, confirmed-stop latency, and duplicate
  physical action count. Accuracy/false-recovery fields explicitly remain null
  until a labeled evaluation set exists.
- `run_autonomy_api.py` is the guarded production composition. It reads the
  ignored root `.env`, active robot profile, and scene coordinates, serves the
  built React console on the API port, and has a non-ROS `--check-config` mode.
  The existing systemd script selects it only when
  `PHI_AUTONOMY_ENABLED=1`; otherwise the approved legacy entry remains active.
- Every mutating production API (task submission, intervention, developer,
  service, robot switch, and safety controls) accepts only a configured token
  or a same-origin client in configured trusted CIDRs. No credential is placed
  in the frontend build.

## Offline evidence and remaining gates

Tests link two replayed decisions to five persisted semantic invocations and
verify privacy fields, zero duplicate actions, HTTP monitor/replay/metrics
shapes, and trusted-authorizer denial/allow behavior. Frontend unit tests and a
production Vite build are part of the offline gate.

Raw keyframe retention/serving, labeled evaluation datasets, simulation,
production TLS/retention, real provider cost tables, static
hardware checks, motion/manipulation HIL, natural-language HIL, injected-drop
HIL, repeated operation, and long-duration operation remain. Phase 8 and the
overall roadmap are therefore not complete.
