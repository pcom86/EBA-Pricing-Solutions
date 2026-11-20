from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if not os.environ.get("GAP_GUI_TEST_CTR"):
        print("GAP_GUI_TEST_CTR is required; mount approved CTR inputs and snapshots.", file=sys.stderr)
        return 2
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", GAP_DB_ENABLED="false")
    environment.pop("GAP_RUN_PRICING_SMOKE", None)
    for arguments in (
        ("discover", "-s", "tests", "-t", ".", "-v"),
        ("tests.gui.gui_window_lifecycle", "-v"),
        ("tests.integration.integration_gui", "-v"),
    ):
        try:
            result = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", *arguments],
                cwd=ROOT, env=environment, timeout=1800,
            )
        except subprocess.TimeoutExpired:
            print("Container test suite exceeded its 30-minute timeout.", file=sys.stderr)
            return 1
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
