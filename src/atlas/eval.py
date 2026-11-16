from __future__ import annotations

import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import write_default_config
from .orchestrator import Atlas


@dataclass
class EvalTask:
    id: str
    prompt: str
    files: dict[str, str]
    test_file: str
    test_command: str
    name: str = ""


CALC_FILES = {
    "calc.py": "def add(a, b):\n    return a - b\n",
    "test_calc.py": (
        "import unittest\n"
        "from calc import add\n\n"
        "class TestCalc(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}

REVERSE_FILES = {
    "text.py": "def reverse(s):\n    return s\n",
    "test_text.py": (
        "import unittest\n"
        "from text import reverse\n\n"
        "class TestText(unittest.TestCase):\n"
        "    def test_reverse(self):\n"
        "        self.assertEqual(reverse('abc'), 'cba')\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}

MAX_FILES = {
    "maxnums.py": "def max_value(items):\n    return items[0]\n",
    "test_maxnums.py": (
        "import unittest\n"
        "from maxnums import max_value\n\n"
        "class TestNumbers(unittest.TestCase):\n"
        "    def test_max(self):\n"
        "        self.assertEqual(max_value([1, 9, 3]), 9)\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}

PALINDROME_FILES = {
    "palindrome.py": "def is_palindrome(value):\n    return value == value\n",
    "test_palindrome.py": (
        "import unittest\n"
        "from palindrome import is_palindrome\n\n"
        "class TestPalindrome(unittest.TestCase):\n"
        "    def test_true(self):\n"
        "        self.assertTrue(is_palindrome('racecar'))\n\n"
        "    def test_false(self):\n"
        "        self.assertFalse(is_palindrome('agent'))\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}

FIZZBUZZ_FILES = {
    "fizzbuzz.py": "def fizzbuzz(n):\n    return [str(i) for i in range(1, n + 1)]\n",
    "test_fizzbuzz.py": (
        "import unittest\n"
        "from fizzbuzz import fizzbuzz\n\n"
        "class TestFizzBuzz(unittest.TestCase):\n"
        "    def test_first_five(self):\n"
        "        self.assertEqual(fizzbuzz(5), ['1', '2', 'Fizz', '4', 'Buzz'])\n\n"
        "    def test_fifteen(self):\n"
        "        self.assertEqual(fizzbuzz(15)[-1], 'FizzBuzz')\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}

SUM_FILES = {
    "totals.py": "def total(values):\n    return 0\n",
    "test_totals.py": (
        "import unittest\n"
        "from totals import total\n\n"
        "class TestTotal(unittest.TestCase):\n"
        "    def test_list(self):\n"
        "        self.assertEqual(total([1, 2, 3]), 6)\n\n"
        "    def test_empty(self):\n"
        "        self.assertEqual(total([]), 0)\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}

SLUGIFY_FILES = {
    "slug.py": "def slugify(text):\n    return text.lower()\n",
    "test_slug.py": (
        "import unittest\n"
        "from slug import slugify\n\n"
        "class TestSlugify(unittest.TestCase):\n"
        "    def test_punctuation(self):\n"
        "        self.assertEqual(slugify('Hello, World!'), 'hello-world')\n\n"
        "    def test_whitespace(self):\n"
        "        self.assertEqual(slugify('  Atlas  Agent  '), 'atlas-agent')\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}

CAPITALIZE_FILES = {
    "wording.py": "def capitalize(text):\n    return text.upper()\n",
    "test_wording.py": (
        "import unittest\n"
        "from wording import capitalize\n\n"
        "class TestCapitalize(unittest.TestCase):\n"
        "    def test_sentence(self):\n"
        "        self.assertEqual(capitalize('hello world'), 'Hello world')\n\n"
        "    def test_loud_rest(self):\n"
        "        self.assertEqual(capitalize('hELLO'), 'Hello')\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}

UNIQUE_FILES = {
    "uniq.py": "def unique(values):\n    return values\n",
    "test_uniq.py": (
        "import unittest\n"
        "from uniq import unique\n\n"
        "class TestUnique(unittest.TestCase):\n"
        "    def test_order(self):\n"
        "        self.assertEqual(unique([1, 2, 1, 3, 2]), [1, 2, 3])\n\n"
        "    def test_empty(self):\n"
        "        self.assertEqual(unique([]), [])\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}

PARSE_INT_FILES = {
    "parseint.py": "def parse_int(text):\n    return int(text)\n",
    "test_parseint.py": (
        "import unittest\n"
        "from parseint import parse_int\n\n"
        "class TestParseInt(unittest.TestCase):\n"
        "    def test_valid(self):\n"
        "        self.assertEqual(parse_int('42'), 42)\n\n"
        "    def test_invalid_message(self):\n"
        "        with self.assertRaises(ValueError) as caught:\n"
        "            parse_int('abc')\n"
        "        self.assertIn('not a valid integer', str(caught.exception))\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}


def default_tasks() -> list[EvalTask]:
    py = f'"{sys.executable}" -m unittest'
    return [
        EvalTask(
            id="calc_add",
            name="Fix off-by-operator",
            prompt="Run the tests, find the bug in calc.py, fix it, and make the tests pass.",
            files=CALC_FILES,
            test_file="test_calc.py",
            test_command=f"{py} test_calc -q",
        ),
        EvalTask(
            id="reverse_string",
            name="Implement reverse",
            prompt="Run the tests, implement reverse in text.py, and make the tests pass.",
            files=REVERSE_FILES,
            test_file="test_text.py",
            test_command=f"{py} test_text -q",
        ),
        EvalTask(
            id="max_value",
            name="Fix boundary bug",
            prompt="Run the tests, fix max_value in maxnums.py, and make the tests pass.",
            files=MAX_FILES,
            test_file="test_maxnums.py",
            test_command=f"{py} test_maxnums -q",
        ),
        EvalTask(
            id="palindrome",
            name="Implement palindrome check",
            prompt="Run the tests, fix is_palindrome in palindrome.py, and make the tests pass.",
            files=PALINDROME_FILES,
            test_file="test_palindrome.py",
            test_command=f"{py} test_palindrome -q",
        ),
        EvalTask(
            id="fizzbuzz",
            name="Implement FizzBuzz",
            prompt="Run the tests, implement fizzbuzz in fizzbuzz.py, and make the tests pass.",
            files=FIZZBUZZ_FILES,
            test_file="test_fizzbuzz.py",
            test_command=f"{py} test_fizzbuzz -q",
        ),
        EvalTask(
            id="sum_list",
            name="Sum a list",
            prompt="Run the tests, fix total in totals.py, and make the tests pass.",
            files=SUM_FILES,
            test_file="test_totals.py",
            test_command=f"{py} test_totals -q",
        ),
        EvalTask(
            id="slugify",
            name="Slugify text",
            prompt="Run the tests, implement slugify in slug.py, and make the tests pass.",
            files=SLUGIFY_FILES,
            test_file="test_slug.py",
            test_command=f"{py} test_slug -q",
        ),
        EvalTask(
            id="capitalize",
            name="Normalize capitalization",
            prompt="Run the tests, fix capitalize in wording.py, and make the tests pass.",
            files=CAPITALIZE_FILES,
            test_file="test_wording.py",
            test_command=f"{py} test_wording -q",
        ),
        EvalTask(
            id="unique",
            name="Deduplicate preserving order",
            prompt="Run the tests, fix unique in uniq.py, and make the tests pass.",
            files=UNIQUE_FILES,
            test_file="test_uniq.py",
            test_command=f"{py} test_uniq -q",
        ),
        EvalTask(
            id="parse_int",
            name="Boundary-aware parse_int",
            prompt="Run the tests, fix parse_int in parseint.py, and make the tests pass.",
            files=PARSE_INT_FILES,
            test_file="test_parseint.py",
            test_command=f"{py} test_parseint -q",
        ),
    ]


def run_suite(
    tasks: list[EvalTask] | None = None,
    solver: Callable[[Path, str], None] | None = None,
    keep: bool = False,
    config_overrides: dict[str, Any] | None = None,
) -> dict:
    """Run each task in an isolated temp repo.

    `solver` is an optional callable(root, prompt); when omitted, Atlas.chat is
    used and a configured model is required. This keeps the harness testable
    without an API key.

    `config_overrides` is applied to every temp Atlas config, so the CLI can
    evaluate the model configured in the caller's project without copying a
    whole atlas.yaml into each task repo.
    """
    results = []
    for task in tasks or default_tasks():
        root = Path(tempfile.mkdtemp(prefix=f"atlas-eval-{task.id}-"))
        atlas = None
        try:
            write_default_config(root, name=f"eval-{task.id}")
            for relative, content in task.files.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            atlas = Atlas(root)
            if config_overrides:
                for key, value in config_overrides.items():
                    if hasattr(atlas.config, key):
                        setattr(atlas.config, key, value)
            atlas.config.test_command = task.test_command
            if solver:
                solver(root, task.prompt)
            else:
                session = atlas.create_session(task.name)
                atlas.chat(session["id"], task.prompt)
            check = atlas.workspace.check("test", task.test_command)
            exit_code = int(check.data.get("exit_code", 1))
            results.append(
                {
                    "id": task.id,
                    "name": task.name,
                    "passed": exit_code == 0,
                    "exit_code": exit_code,
                    "stdout": check.data.get("stdout", "")[-2000:],
                    "stderr": check.data.get("stderr", "")[-2000:],
                }
            )
        except Exception as exc:
            results.append(
                {
                    "id": task.id,
                    "name": task.name,
                    "passed": False,
                    "exit_code": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        finally:
            if atlas is not None:
                atlas.close()
            if not keep:
                shutil.rmtree(root, ignore_errors=True)
    passed = sum(1 for r in results if r["passed"])
    return {
        "suite": "m0",
        "tasks": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": (passed / len(results)) if results else 0.0,
        "results": results,
    }
