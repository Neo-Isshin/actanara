#!/usr/bin/env python3
"""Run the scheduled Dashboard Foundation snapshot refresh once."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from app.services.scheduler import run_due_snapshot_refresh


def main() -> int:
    result = run_due_snapshot_refresh()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
