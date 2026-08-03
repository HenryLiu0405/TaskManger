# Phase 0/1 risk register

| ID | Risk | Control | Residual status |
|---|---|---|---|
| R-01 | Concurrent physical commands | Per-robot non-blocking writer lock | Covered offline |
| R-02 | Duplicate carry after response loss | Stable idempotency key and draining lock | HIL confirmation required |
| R-03 | Stale/wrong FP pose | Selection epoch, object match, 1.5 s freshness window | External FP snapshot confirmation required |
| R-04 | Wrong body frame | Require exact configured `pelvis` frame; never relabel pose | HIL blocked on owner confirmation |
| R-05 | Navigation service success mistaken for arrival | Require fresh `/nav_reached=true` | HIL confirmation required |
| R-06 | Timeout triggers successor/retry | `unknown` outcome and retained lock | Covered offline |
| R-07 | Fake cancellation | Cancel receipt separates requested from confirmed | Full physical cancel deferred |
| R-08 | Direct adapter bypass reappears | AST/static regression test on production controllers | Covered offline |
| R-09 | Tests touch ROS/hardware | Offline test path plus guarded manual HIL scripts | Covered locally |
| R-10 | Legacy report false-positive | Legacy label and nonzero failure exit | Covered locally |
| R-11 | ROS distro/config drift | Ubuntu 22.04/Humble/Python 3.10 preflight | HIL host check required |
| R-12 | Autonomous recovery makes unsafe decisions | Endpoint returns unsupported in Phase 1 | Deferred to supervisor phase |

