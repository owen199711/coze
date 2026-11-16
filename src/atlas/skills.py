from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    path: Path


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip().lower()] = value.strip().strip("\"'")
    return meta, parts[2].lstrip("\r\n")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    return slug[:64]


def _read_source(source: str) -> tuple[str, str]:
    if source.startswith(("http://", "https://")):
        request = urllib.request.Request(source, headers={"User-Agent": "atlas-skill/0.1"})
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(100_001)
        if len(raw) > 100_000:
            raise ValueError("skill source is larger than 100KB")
        return raw.decode("utf-8", errors="replace"), Path(source.split("?", 1)[0]).stem
    path = Path(source).expanduser()
    if path.is_dir():
        path = path / "SKILL.md"
    if not path.is_file():
        raise ValueError(f"skill not found: {source}")
    raw = path.read_bytes()
    if len(raw) > 100_000:
        raise ValueError("skill source is larger than 100KB")
    return raw.decode("utf-8", errors="replace"), path.stem


def install_skill(data_dir: Path, source: str) -> Skill:
    text, fallback_name = _read_source(source)
    meta, _body = _frontmatter(text)
    name = _slug(meta.get("name") or fallback_name)
    if not name:
        raise ValueError("skill name is empty")
    description = meta.get("description") or ""
    skills_dir = data_dir / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    target = skills_dir / f"{name}.md"
    target.write_text(text, encoding="utf-8")
    return Skill(name=name, description=description, path=target)


def list_skills(data_dir: Path) -> list[Skill]:
    skills_dir = data_dir / "skills"
    if not skills_dir.is_dir():
        return []
    result: list[Skill] = []
    for path in sorted(skills_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, _body = _frontmatter(text)
        name = _slug(meta.get("name") or path.stem)
        if name:
            result.append(
                Skill(name=name, description=meta.get("description") or "", path=path)
            )
    return result


def remove_skill(data_dir: Path, name: str) -> bool:
    name = _slug(name)
    if not name:
        return False
    path = data_dir / "skills" / f"{name}.md"
    if not path.is_file():
        return False
    path.unlink()
    return True


def skills_prompt(data_dir: Path) -> str:
    skills = list_skills(data_dir)
    if not skills:
        return ""
    lines = ["## Installed skills", ""]
    for skill in skills:
        lines.append(
            f"- `{skill.name}`: {skill.description or '(no description)'} "
            f"(read `.atlas/skills/{skill.name}.md` when it applies)"
        )
    return "\n".join(lines)
