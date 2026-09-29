"""Explicit SHA-256 snapshots for experiment code, prompts and input data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_run_provenance(
    entrypoint: Path,
    cases: list[Any],
    prompt_paths: list[Path],
    request_config: dict[str, Any],
    additional_data_paths: list[Path] | None = None,
) -> dict[str, Any]:
    code_paths = [
        entrypoint,
        Path(__file__),
        ROOT / "evaluation" / "run_v09_experiments.py",
        ROOT / "evaluation" / "run_safety.py",
        ROOT / "backend" / "app" / "rag" / "document_parser.py",
        ROOT / "backend" / "app" / "rag" / "embedding.py",
        ROOT / "backend" / "app" / "rag" / "knowledge_base.py",
        ROOT / "backend" / "app" / "agents" / "technology_agent.py",
        ROOT / "backend" / "app" / "agents" / "industry_agent.py",
    ]
    data_paths: list[Path] = []
    for case in cases:
        data_paths.append(Path(case.pdf_path))
        data_paths.extend(ROOT / "data" / "evaluation_cases" / case.case_id / name for name in ("expected_indicators.json", "expected_technology.json", "expected_industry.json"))
        data_paths.extend((ROOT / "data" / "evaluation_cases" / "composite" / case.case_id).glob("expected_*.json"))
    data_paths.extend(additional_data_paths or [])
    code = _fingerprint(code_paths)
    prompts = _fingerprint(prompt_paths)
    data = _fingerprint(data_paths)
    commit = _current_commit()
    return {
        "schema_version": "1.0.0",
        "git_commit": commit,
        "git_commit_note": "Current HEAD when available; no historical revision is inferred.",
        "code_snapshot": code,
        "prompt_snapshot": prompts,
        "data_snapshot": data,
        "data_snapshot_id": data["digest"],
        "request_config": request_config,
    }


def _current_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=ROOT,
            check=True, capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _fingerprint(paths: list[Path]) -> dict[str, Any]:
    unique = sorted({path.resolve() for path in paths if path.is_file()})
    files = [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in unique]
    payload = json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {"algorithm": "sha256", "files": files, "digest": hashlib.sha256(payload).hexdigest()}
