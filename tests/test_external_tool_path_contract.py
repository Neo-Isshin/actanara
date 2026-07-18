from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ExternalToolPathContractTests(unittest.TestCase):
    def test_production_code_does_not_hardcode_developer_machine_paths(self):
        forbidden = re.compile(
            r"/Users/[^/\s]+/(?:Desktop/)?DEV\b"
            r"|/Volumes/[^/\s]+/DEV\b"
            r"|/private/tmp/actanara\b"
            r"|/tmp/actanara\b"
        )
        offenders: list[str] = []
        for base in (ROOT / "src", ROOT / "advanced"):
            for path in sorted(base.rglob("*.py")):
                rel = path.relative_to(ROOT)
                text = path.read_text(encoding="utf-8")
                for match in forbidden.finditer(text):
                    line_no = text.count("\n", 0, match.start()) + 1
                    offenders.append(f"{rel}:{line_no}: {match.group(0)}")

        self.assertEqual(offenders, [], "Production code must not contain developer-machine absolute paths.")

    def test_production_code_does_not_hardcode_external_tool_home_paths(self):
        forbidden = re.compile(
            r"(?:~/(?:\.openclaw|\.codex|\.claude|\.gemini|\.hermes))|"
            r"(?:/Users/[^\"' )]+/\.(?:openclaw|codex|claude|gemini|hermes))|"
            r"(?:(?:Path\.home\(\)|HOME)\s*/\s*[\"']\.(?:openclaw|codex|claude|gemini|hermes)[\"'])"
        )
        allowed = {
            Path("src/data_foundation/external_tool_definitions.py"),
            Path("src/data_foundation/settings.py"),
        }
        offenders: list[str] = []
        for base in (ROOT / "src", ROOT / "advanced"):
            for path in sorted(base.rglob("*.py")):
                rel = path.relative_to(ROOT)
                if rel in allowed:
                    continue
                text = path.read_text(encoding="utf-8")
                for match in forbidden.finditer(text):
                    line_no = text.count("\n", 0, match.start()) + 1
                    offenders.append(f"{rel}:{line_no}: {match.group(0)}")

        self.assertEqual(
            offenders,
            [],
            "Production code must read external tool paths through external_tool_path() "
            "or resolve_external_tool_paths(); defaults belong in the external tool catalog.",
        )
