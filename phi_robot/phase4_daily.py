"""Daily regression entrypoint for Phase 4.

This wrapper exists so operators can run the regression suite with a single
stable command.  By default it writes the markdown report to
``phi_robot/docs/phase4.md``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from phi_robot.phase4_runner import build_report, run_regression_suite


DEFAULT_REPORT_PATH = Path(__file__).resolve().parent / "docs" / "phase4.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 4 daily regression suite.")
    parser.add_argument("--write-report", type=Path, default=DEFAULT_REPORT_PATH, help="Where to write the markdown report.")
    args = parser.parse_args()

    results = run_regression_suite()
    report = build_report(results)
    print(report)
    args.write_report.write_text(report, encoding="utf-8")

    if not all(result.passed for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
