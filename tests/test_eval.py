from __future__ import annotations

from pathlib import Path

from atlas import eval as eval_mod
from atlas.cli import build_parser, cmd_eval
from atlas.eval import default_tasks, run_suite


def test_default_suite_has_ten_unique_tasks() -> None:
    tasks = default_tasks()
    assert len(tasks) == 10
    assert len({task.id for task in tasks}) == 10
    assert all(task.files and task.test_file for task in tasks)


def test_default_suite_starts_red() -> None:
    for task in default_tasks():
        result = run_suite(tasks=[task], solver=lambda _root, _prompt: None)
        assert result["results"][0]["passed"] is False, task.id


def test_eval_harness_with_solver() -> None:
    task = default_tasks()[0]  # calc_add

    def solver(root: Path, _prompt: str) -> None:
        path = root / "calc.py"
        path.write_text(
            path.read_text(encoding="utf-8").replace("a - b", "a + b"), encoding="utf-8"
        )

    result = run_suite(tasks=[task], solver=solver)
    assert result["tasks"] == 1
    assert result["failed"] == 0
    assert result["results"][0]["passed"] is True


def test_eval_harness_repeat_reports_attempts() -> None:
    task = default_tasks()[0]

    def solver(root: Path, _prompt: str) -> None:
        path = root / "calc.py"
        path.write_text(
            path.read_text(encoding="utf-8").replace("a - b", "a + b"), encoding="utf-8"
        )

    result = run_suite(tasks=[task], solver=solver, repeat=2)
    assert result["tasks"] == 1
    assert result["repeat"] == 2
    assert result["attempts"] == 2
    assert result["passed"] == 2
    assert [item["repeat"] for item in result["results"]] == [1, 2]


def test_eval_harness_applies_config_overrides(monkeypatch) -> None:
    seen = []
    real_atlas = eval_mod.Atlas

    class RecordingAtlas(real_atlas):
        def __init__(self, root):
            super().__init__(root)
            seen.append(self.config)

    monkeypatch.setattr(eval_mod, "Atlas", RecordingAtlas)
    task = default_tasks()[0]

    def solver(root: Path, _prompt: str) -> None:
        path = root / "calc.py"
        path.write_text(
            path.read_text(encoding="utf-8").replace("a - b", "a + b"), encoding="utf-8"
        )

    result = run_suite(
        tasks=[task],
        solver=solver,
        config_overrides={
            "model_name": "deepseek-chat",
            "api_base": "https://api.deepseek.com",
            "api_key_env": "DEEPSEEK_API_KEY",
            "max_turns": 8,
        },
    )

    assert result["failed"] == 0
    config = seen[0]
    assert config.model_name == "deepseek-chat"
    assert config.api_base == "https://api.deepseek.com"
    assert config.api_key_env == "DEEPSEEK_API_KEY"
    assert config.max_turns == 8


def test_eval_cli_passes_model_overrides(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_run_suite(**kwargs):
        captured.update(kwargs)
        return {"tasks": 0, "passed": 0, "failed": 0, "results": []}

    monkeypatch.setattr("atlas.cli.run_suite", fake_run_suite)
    args = build_parser().parse_args(
        [
            "eval",
            "--path",
            str(tmp_path),
            "--model",
            "deepseek-chat",
            "--api-base",
            "https://api.deepseek.com",
            "--api-key-env",
            "DEEPSEEK_API_KEY",
        ]
    )

    assert cmd_eval(args) == 0
    assert captured["config_overrides"] == {
        "model_name": "deepseek-chat",
        "api_base": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "max_turns": 10,
    }
