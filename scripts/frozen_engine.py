"""Standalone engine entry point; the GUI supplies JSON through stdin."""

import os

from aside_patcher.protocol import main

if __name__ == "__main__":
    # The UAC worker must extract under its own account, not inherit a parent's
    # temporary PyInstaller directory across an elevation boundary.
    os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    raise SystemExit(main())
