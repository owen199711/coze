from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .models import KnowledgeItem, SourceRef, new_id, utcnow
from .orchestrator import Atlas


class CuratorJob:
    """The M0 '留守线程': consolidate knowledge, age out stale items, write a report.

    The failure miner is deterministic by default. `run_once(use_llm=True)` asks
    the configured model for a root-cause/prevention analysis and falls back to
    the deterministic template if the model is missing or returns unusable JSON.
    Lessons stay `candidate`; promotion still requires a replay/validation step.
    """

    def __init__(self, atlas: Atlas, model: Any | None = None):
        self.atlas = atlas
        self._model = model
        self._llm_error: str | None = None

    def run_once(self, use_llm: bool = False) -> dict:
        self._llm_error = None
        if not self.atlas.config.learning_enabled:
            return {
                "ok": True,
                "skipped": "learning is disabled in atlas.yaml",
                "new_candidates": 0,
                "llm_analyzed": 0,
                "deterministic_fallback": 0,
                "llm_error": None,
            }
        result = self.atlas.knowledge.consolidate()
        pruned = self.atlas.sessions.prune_checkpoints()
        mined = self._mine_failed_verifications(use_llm=use_llm)
        stats = self.atlas.knowledge.stats()
        reports_dir = self.atlas.config.data_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        report_path = reports_dir / f"curator-{stamp}.md"
        lines = [
            "# Curator report",
            "",
            f"- project: `{self.atlas.project_id}`",
            f"- generated: `{utcnow()}`",
            "",
            "## Consolidation",
            "",
            f"- expired retired: {result['expired']}",
            f"- stale docs: {result['stale_docs']}",
            f"- old candidates retired: {result['old_candidates']}",
            f"- new lesson candidates from failures: {mined['total']}",
            f"- RCA: llm={mined['llm']}, deterministic={mined['fallback']}",
            "",
            "## Knowledge by kind/status",
            "",
        ]
        for kind in sorted(stats):
            parts = ", ".join(f"{status}={n}" for status, n in sorted(stats[kind].items()))
            lines.append(f"- `{kind}`: {parts}")
        if not stats:
            lines.append("- (empty)")
        lines += [
            "",
            "## Next",
            "",
            "- Review candidate lessons in the Review inbox (later UI).",
            "- Validate lessons with a replay/test before they become active.",
            "- Refresh docs whose freshness is older than 90 days.",
            "",
        ]
        if self._llm_error:
            lines += ["## RCA note", "", self._llm_error[:500], ""]
        report_path.write_text("\n".join(lines), encoding="utf-8")

        job_id = new_id("job")
        now = utcnow()
        self.atlas.db.execute(
            "INSERT INTO jobs(id, type, status, payload_json, created_at, updated_at) "
            "VALUES (?, 'curator', 'done', ?, ?, ?)",
            (
                job_id,
                json.dumps(
                    {
                        "consolidation": result,
                        "mining": mined,
                        "llm_error": self._llm_error,
                        "report": str(report_path),
                    },
                    ensure_ascii=False,
                ),
                now,
                now,
            ),
        )
        self.atlas.db.commit()
        return {
            "ok": True,
            "job_id": job_id,
            "report": str(report_path),
            "new_candidates": mined["total"],
            "llm_analyzed": mined["llm"],
            "deterministic_fallback": mined["fallback"],
            "llm_error": self._llm_error,
            "pruned_checkpoints": pruned,
            **result,
        }

    def _resolve_model(self) -> Any | None:
        if self._model is not None:
            return self._model
        try:
            self._model = self.atlas.model
        except Exception as exc:
            self._llm_error = f"{type(exc).__name__}: {exc}"
            return None
        if self._model is None:
            self._llm_error = "no model configured; using deterministic RCA"
        return self._model

    def _mine_failed_verifications(self, limit: int = 20, use_llm: bool = False) -> dict[str, int]:
        """Turn recent failed checks into candidate lessons.

        They stay `candidate` and are not recalled until a human/validation step
        promotes them. This is the smallest useful version of 'learn from errors'.
        """
        rows = self.atlas.db.fetchall(
            "SELECT id, kind, command, cwd, exit_code, stdout, stderr, created_at "
            "FROM verifications WHERE exit_code != 0 ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        model = self._resolve_model() if use_llm else None
        created = 0
        analyzed = 0
        fallback = 0
        for row in rows:
            ref = f"verification:{row['id']}"
            if self.atlas.knowledge.find_by_source("lesson", ref):
                continue
            analysis = self._llm_rca(model, row) if model is not None else None
            if analysis is not None:
                analyzed += 1
            else:
                fallback += 1
            item = KnowledgeItem(
                title=f"Failed check: {row['kind']} ({str(row['command'] or '')[:80]})",
                body=self._lesson_body(row, analysis),
                kind="lesson",
                scope="project",
                status="candidate",
                project_id=self.atlas.project_id,
                severity="high",
                confidence=analysis["confidence"] if analysis else 0.6,
                validation=(
                    {
                        "method": "llm-rca",
                        "status": "unvalidated",
                        "model": str(getattr(model, "model", "unknown")),
                        "confidence": analysis["confidence"],
                    }
                    if analysis
                    else {"method": "deterministic", "status": "unvalidated"}
                ),
                source=SourceRef(kind="command", ref=ref),
            )
            self.atlas.knowledge.write(item)
            created += 1
        return {"total": created, "llm": analyzed, "fallback": fallback}

    def _llm_rca(self, model: Any, row: Any) -> dict[str, Any] | None:
        prompt = (
            f"kind: {row['kind']}\n"
            f"command: {row['command']}\n"
            f"cwd: {row['cwd']}\n"
            f"exit_code: {row['exit_code']}\n"
            f"stdout:\n{str(row['stdout'] or '')[-1500:]}\n"
            f"stderr:\n{str(row['stderr'] or '')[-1500:]}"
        )
        try:
            reply = model.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are Atlas Curator, a root-cause analyst for a coding agent. "
                            "Given one failed verification, return ONLY a JSON object with keys "
                            "root_cause, prevention, confidence (0..1), and evidence. "
                            "Be concrete. Do not invent file contents. If the evidence is "
                            "insufficient, say so and lower the confidence."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
            )
            data = self._parse_rca_json(getattr(reply, "content", "") or "")
            if data is None:
                self._llm_error = "LLM RCA returned no JSON object"
                return None
            root_cause = str(data.get("root_cause") or "").strip()[:1200]
            prevention = str(data.get("prevention") or "").strip()[:1200]
            if not root_cause or not prevention:
                self._llm_error = "LLM RCA missing root_cause/prevention"
                return None
            try:
                confidence = float(data.get("confidence", 0.6))
            except (TypeError, ValueError):
                confidence = 0.6
            confidence = min(1.0, max(0.0, confidence))
            evidence = str(data.get("evidence") or "").strip()[:800]
            self._llm_error = None
            return {
                "root_cause": root_cause,
                "prevention": prevention,
                "confidence": confidence,
                "evidence": evidence,
            }
        except Exception as exc:
            self._llm_error = f"{type(exc).__name__}: {exc}"
            return None

    @staticmethod
    def _parse_rca_json(text: str) -> dict[str, Any] | None:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _lesson_body(row: Any, analysis: dict[str, Any] | None) -> str:
        command = str(row["command"] or "")
        stderr = str(row["stderr"] or "").strip()
        header = f"Command `{command}` exited {row['exit_code']} in `{row['cwd']}`."
        if analysis is None:
            return (
                f"{header}\n\n"
                f"```\n{stderr[-1500:] or '(no stderr captured)'}\n```\n\n"
                "Next: reproduce with the same command, inspect the first real error, "
                "add a regression test, then implement the smallest fix."
            )
        lines = [
            header,
            "",
            "## Root cause",
            analysis["root_cause"],
            "",
            "## Prevention",
            analysis["prevention"],
        ]
        if stderr:
            lines += ["", "## Failure output", f"```\n{stderr[-800:]}\n```"]
        if analysis["evidence"]:
            lines += ["", "## Evidence", analysis["evidence"]]
        lines += [
            "",
            f"Confidence: {analysis['confidence']:.2f}",
            "",
            "Status: candidate. Replay the fix and run the relevant test before promotion.",
        ]
        return "\n".join(lines)
