#!/usr/bin/env python3
"""Source-tree entrypoint for the packaged Actanara operator CLI."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
