# Manual hardware tests

These scripts are intentionally excluded from the default test discovery path.
They require both `PHI_ALLOW_HARDWARE_TESTS=1` and an exact match between
`PHI_HARDWARE_ROBOT_ID` and `--robot-id` before importing ROS or creating a
client. Run them only during an approved HIL session with an emergency-stop
operator present.

`navigation_hil.py` requires a fresh `/nav_reached` false→true sequence after
the accepted target. `locomotion_hil.py` is only a low-level service probe; a
successful service response is not the Phase 1 skill completion proof. Use the
reviewed `move_to`/`pick`/`place` API and its verification evidence for the
staged release checklist.
