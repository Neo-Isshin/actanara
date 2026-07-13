#!/usr/bin/env python3
"""Print read-only Nova settings status for onboarding and operator checks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.paths import load_paths, runtime_paths_for_home
from data_foundation.settings_status import (
    dump_nova_settings_status_json,
    format_nova_settings_status,
    nova_settings_status,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", help="Inspect a candidate NOVA_HOME without selecting it.")
    parser.add_argument("--legacy-diary-root", help="Legacy diary root used when --runtime initializes a path object.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    paths = None
    if args.runtime:
        current = load_paths()
        paths = runtime_paths_for_home(
            Path(args.runtime).expanduser(),
            legacy_diary_root=Path(args.legacy_diary_root).expanduser() if args.legacy_diary_root else current.legacy_diary_root,
        )

    payload = nova_settings_status(paths)
    output = dump_nova_settings_status_json(payload) if args.json else format_nova_settings_status(payload)
    sys.stdout.write(output)
    return 1 if payload.get("summary", {}).get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
