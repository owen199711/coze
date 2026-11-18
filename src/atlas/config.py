from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(RuntimeError):
    pass


VALID_PERMISSION_MODES = {"default", "accept_edits", "explore", "dont_ask"}
VALID_CAPTURE_MODES = {"off", "candidate"}


@dataclass
class ModuleConfig:
    id: str
    path: str


@dataclass
class AtlasConfig:
    root: Path
    name: str = "atlas-project"
    modules: list[ModuleConfig] = field(default_factory=list)
    setup_command: str | None = None
    test_command: str = "python -m pytest -q"
    lint_command: str | None = None
    typecheck_command: str | None = None
    format_command: str | None = None
    style_profile: str = "pep8-ruff"
    learning_enabled: bool = True
    capture_mode: str = "candidate"
    idle_minutes: int = 15
    learning_budget_usd: float = 2.0
    permission_mode: str = "accept_edits"
    model_provider: str = "openai-compatible"
    model_name: str = ""
    api_base: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    max_turns: int = 10
    max_tool_calls: int = 40
    max_cost_usd: float = 5.0
    input_cost_per_1m: float = 0.0
    output_cost_per_1m: float = 0.0

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Best-effort cost estimate from optional per-million-token prices."""
        return (
            max(0, int(input_tokens)) * self.input_cost_per_1m
            + max(0, int(output_tokens)) * self.output_cost_per_1m
        ) / 1_000_000.0

    @property
    def data_dir(self) -> Path:
        return self.root / ".atlas"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "atlas.db"

    @property
    def plans_dir(self) -> Path:
        return self.data_dir / "plans"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.plans_dir, self.artifacts_dir):
            path.mkdir(parents=True, exist_ok=True)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ConfigError(
            "PyYAML is required for atlas.yaml. Install it with `pip install pyyaml`, "
            "or use atlas.json for a zero-dependency config."
        ) from exc
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"failed to parse {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return data


def load_config(root: str | Path = ".") -> AtlasConfig:
    root_path = Path(root).resolve()
    candidates = [
        root_path / "atlas.yaml",
        root_path / "atlas.yml",
        root_path / "atlas.json",
    ]
    config_path = next((p for p in candidates if p.exists()), None)
    if config_path is None:
        return AtlasConfig(root=root_path)

    if config_path.suffix == ".json":
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ConfigError(f"failed to parse {config_path}: {exc}") from exc
    else:
        data = _read_yaml(config_path)

    project = data.get("project") or {}
    configured_root = project.get("root")
    if configured_root:
        candidate = Path(str(configured_root))
        if not candidate.is_absolute():
            candidate = root_path / candidate
        root_path = candidate.resolve()
    commands = project.get("commands") or {}
    style = data.get("style") or {}
    learning = data.get("learning") or {}
    learning_budget = learning.get("budget") or {}
    permissions = data.get("permissions") or {}
    model = data.get("model") or {}

    modules: list[ModuleConfig] = []
    for module in project.get("modules") or []:
        if isinstance(module, dict) and module.get("id"):
            modules.append(ModuleConfig(id=str(module["id"]), path=str(module.get("path", ""))))

    permission_mode = str(permissions.get("mode") or "accept_edits")
    if permission_mode not in VALID_PERMISSION_MODES:
        raise ConfigError(
            f"invalid permissions.mode: {permission_mode!r}; "
            f"expected one of {', '.join(sorted(VALID_PERMISSION_MODES))}"
        )
    capture_mode = str(learning.get("capture") or "candidate")
    if capture_mode not in VALID_CAPTURE_MODES:
        raise ConfigError(
            f"invalid learning.capture: {capture_mode!r}; "
            f"expected one of {', '.join(sorted(VALID_CAPTURE_MODES))}"
        )

    return AtlasConfig(
        root=root_path,
        name=str(project.get("name") or root_path.name),
        modules=modules,
        setup_command=commands.get("setup"),
        test_command=commands.get("test") or "python -m pytest -q",
        lint_command=commands.get("lint"),
        typecheck_command=commands.get("typecheck"),
        format_command=commands.get("format"),
        style_profile=str(style.get("profile") or "pep8-ruff"),
        learning_enabled=bool(learning.get("enabled", True)),
        capture_mode=capture_mode,
        idle_minutes=int(learning.get("idle_minutes", 15)),
        learning_budget_usd=float(learning_budget.get("max_cost_usd_per_night", 2.0)),
        permission_mode=permission_mode,
        model_provider=str(model.get("provider") or "openai-compatible"),
        model_name=str(model.get("name") or ""),
        api_base=str(model.get("api_base") or "https://api.openai.com/v1"),
        api_key_env=str(model.get("api_key_env") or "OPENAI_API_KEY"),
        max_turns=int(model.get("max_turns", 10)),
        max_tool_calls=int(model.get("max_tool_calls", 40)),
        max_cost_usd=float(model.get("max_cost_usd", 5.0)),
        input_cost_per_1m=float(model.get("input_cost_per_1m", 0.0)),
        output_cost_per_1m=float(model.get("output_cost_per_1m", 0.0)),
    )


DEFAULT_ATLAS_YAML = """\
version: 1
project:
  name: {name}
  root: .
  languages: [python]
  commands:
    test: python -m pytest -q
    lint: ruff check .
    typecheck: mypy src
    format: ruff format .
  modules:
    - id: app
      path: .

style:
  profile: pep8-ruff

learning:
  enabled: true
  capture: candidate
  idle_minutes: 15
  budget:
    max_cost_usd_per_night: 2.0

permissions:
  mode: accept_edits

model:
  provider: openai-compatible
  name: ""
  api_base: https://api.openai.com/v1
  api_key_env: OPENAI_API_KEY
  max_turns: 10
  max_tool_calls: 40
  max_cost_usd: 5.0
  input_cost_per_1m: 0.0
  output_cost_per_1m: 0.0
"""

DEFAULT_AGENTS_MD = """\
# AGENTS.md

## Commands
- Install: `pip install -e .`
- Test: `python -m pytest -q`
- Lint: `ruff check .`
- Format: `ruff format .`

## Rules
- Do not commit secrets, `.env`, or generated artifacts.
- Do not force-push or modify `main` directly.
- Prefer small, test-first changes.
- Use the project's existing style and module boundaries.

## Definition of Done
- Tests pass and new behaviour is covered.
- Lint/typecheck pass where configured.
- The change is described with evidence: commands, outputs, and files.
"""


def write_default_config(root: str | Path, name: str | None = None) -> tuple[Path, Path]:
    root_path = Path(root).resolve()
    root_path.mkdir(parents=True, exist_ok=True)
    config_path = root_path / "atlas.yaml"
    agents_path = root_path / "AGENTS.md"
    project_name = name or root_path.name or "atlas-project"

    existing = next(
        (
            root_path / candidate
            for candidate in ("atlas.yaml", "atlas.yml", "atlas.json")
            if (root_path / candidate).exists()
        ),
        None,
    )
    if existing is None:
        config_path.write_text(DEFAULT_ATLAS_YAML.format(name=project_name), encoding="utf-8")
    if not agents_path.exists():
        agents_path.write_text(DEFAULT_AGENTS_MD, encoding="utf-8")
    return existing or config_path, agents_path
