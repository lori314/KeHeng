"""Safe output-directory helpers shared by command-line runners."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import uuid


def new_run_directory(base: Path) -> Path:
    """Reserve a unique run directory below *base* without touching old runs."""
    base = base.expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    while True:
        name = f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        candidate = base / name
        if not candidate.exists():
            return candidate


def require_fresh_output_directory(path: Path) -> None:
    """Reject a direct output target that already contains any prior artifact."""
    path = path.expanduser().resolve()
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing run output: {path}")
    path.mkdir(parents=True, exist_ok=False)
