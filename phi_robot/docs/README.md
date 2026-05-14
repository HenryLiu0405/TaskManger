# phi_robot Phase 1

This folder contains a standalone, standard-library-only Phase 1 implementation:

- `fake_robot_service.py`: in-memory robot backend with deterministic fault injection
- `phase1_runner.py`: scenario replay and acceptance report generator

## Run

```bash
python3 -m phi_robot.phase1_runner
```

To write the report to a file:

```bash
python3 -m phi_robot.phase1_runner --write-report /home/rootroot/nanobot/phi_robot/phase1-report.md
```
