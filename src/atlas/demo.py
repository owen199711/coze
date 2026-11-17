from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from .config import write_default_config
from .orchestrator import Atlas
from .tools import AtlasTools

CALC = """def add(a, b):
    # BUG: subtraction instead of addition
    return a - b
"""

TEST = """import unittest
from calc import add


class TestCalc(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)


if __name__ == "__main__":
    unittest.main()
"""


def run_demo(base: str | Path | None = None, keep: bool = False) -> dict[str, Any]:
    root = Path(base).resolve() if base else Path(tempfile.mkdtemp(prefix="atlas-demo-"))
    root.mkdir(parents=True, exist_ok=True)
    created_temp = base is None
    if base is not None:
        for name in ("calc.py", "test_calc.py"):
            if (root / name).exists():
                raise FileExistsError(
                    f"demo refuses to overwrite {root / name}; use an empty directory"
                )

    write_default_config(root, name="atlas-demo")
    (root / "calc.py").write_text(CALC, encoding="utf-8")
    (root / "test_calc.py").write_text(TEST, encoding="utf-8")

    atlas = Atlas(root)
    # Use the interpreter running the demo, not a PATH-dependent `python`.
    atlas.config.test_command = f'"{sys.executable}" -m unittest test_calc -q'
    session = atlas.create_session("demo: fix the calculator")
    tools = AtlasTools(atlas.workspace, atlas.knowledge, atlas.config, session["id"])

    read_result = tools.call("read", {"path": "calc.py"})
    red = tools.call("check", {"kind": "test"})
    if red.data.get("exit_code") is not None:
        atlas.sessions.record_verification(
            session["id"],
            "test",
            red.data.get("command", "python -m unittest -q"),
            red.data.get("cwd", str(root)),
            int(red.data.get("exit_code", 1)),
            red.data.get("stdout", ""),
            red.data.get("stderr", ""),
            int(red.data.get("duration_ms", 0)),
        )

    edit_result = tools.call(
        "edit",
        {
            "path": "calc.py",
            "mode": "replace_text",
            "old": "return a - b",
            "new": "return a + b",
        },
    )
    green = tools.call("check", {"kind": "test"})
    if green.data.get("exit_code") is not None:
        atlas.sessions.record_verification(
            session["id"],
            "test",
            green.data.get("command", "python -m unittest -q"),
            green.data.get("cwd", str(root)),
            int(green.data.get("exit_code", 1)),
            green.data.get("stdout", ""),
            green.data.get("stderr", ""),
            int(green.data.get("duration_ms", 0)),
        )

    knowledge_result = tools.call(
        "knowledge",
        {
            "action": "write",
            "title": "Demo test command",
            "body": "Run `python -m unittest -q` from the repository root to execute the demo tests.",
            "kind": "command",
            "scope": "project",
            "source": "atlas demo",
        },
    )
    hits = tools.call("knowledge", {"action": "search", "query": "unittest", "limit": 3})

    summary = {
        "root": str(root),
        "session_id": session["id"],
        "read": read_result.status,
        "red_exit": red.data.get("exit_code"),
        "edit": edit_result.status,
        "green_exit": green.data.get("exit_code"),
        "green_stdout": green.data.get("stdout", "")[-500:],
        "green_stderr": green.data.get("stderr", "")[-500:],
        "knowledge": knowledge_result.data.get("id"),
        "knowledge_hits": len(hits.data.get("hits", [])),
        "kept": keep or not created_temp,
    }
    summary["ok"] = (
        summary["red_exit"] not in (None, 0)
        and summary["green_exit"] == 0
        and summary["knowledge_hits"] >= 1
    )
    atlas.close()

    if created_temp and not keep:
        shutil.rmtree(root, ignore_errors=True)
        summary["root"] = "(temporary directory removed; use --keep to inspect)"
    return summary
