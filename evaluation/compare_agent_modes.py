"""Compare offline rule extraction with the v0.6 deterministic LLM mock."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.llm.mock import MockLLMProvider  # noqa: E402
from evaluation.evaluate_pipeline import DEFAULT_CASE_ROOT, evaluate_cases  # noqa: E402


async def compare(
    case_root: str | Path = DEFAULT_CASE_ROOT,
    runtime_root: str | Path | None = None,
) -> dict[str, Any]:
    """Run both modes against identical cases and return comparable metrics."""

    if runtime_root is not None:
        root = Path(runtime_root).resolve()
        rule = await evaluate_cases(case_root, root / "rule")
        llm = await evaluate_cases(
            case_root,
            root / "llm",
            agent_mode="llm",
            llm_provider=MockLLMProvider(),
        )
    else:
        with tempfile.TemporaryDirectory(
            prefix="keheng-v06-compare-", ignore_cleanup_errors=True
        ) as temporary:
            root = Path(temporary)
            rule = await evaluate_cases(case_root, root / "rule")
            llm = await evaluate_cases(
                case_root,
                root / "llm",
                agent_mode="llm",
                llm_provider=MockLLMProvider(),
            )

    metric_names = (
        "indicator_expectation_match_rate",
        "evidence_reference_completeness_rate",
        "report_generation_success_rate",
        "task_id_isolation_accuracy",
    )
    comparison = {
        name: {
            "rule": rule["metrics"][name],
            "llm": llm["metrics"][name],
            "delta_percentage_points": round(
                llm["metrics"][name] - rule["metrics"][name], 2
            ),
        }
        for name in metric_names
    }
    return {
        "schema_version": "1.0",
        "case_count": rule["case_count"],
        "rule": _mode_summary(rule),
        "llm": _mode_summary(llm),
        "comparison": comparison,
    }


def _mode_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "metrics": result["metrics"],
        "failed_cases": [
            {"case_id": item["case_id"], "errors": item["errors"]}
            for item in result["cases"]
            if not item["passed"]
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare KeHeng rule and LLM modes.")
    parser.add_argument("--case-root", type=Path, default=DEFAULT_CASE_ROOT)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = asyncio.run(compare(args.case_root, args.runtime_root))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if not result["rule"]["failed_cases"] and not result["llm"]["failed_cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
