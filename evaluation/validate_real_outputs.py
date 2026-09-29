"""Validate evidence and semantic guardrails in saved public-company outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BRACKET = re.compile(r"\[\s*([A-Za-z]+\d*)\s*\]")


def validate(root: Path) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for case_dir in sorted(root.iterdir()):
        if not case_dir.is_dir():
            continue
        tech_path = case_dir / "technology_analysis.json"
        if not tech_path.exists():
            metadata = case_dir / "run_metadata.json"
            if metadata.exists():
                data = json.loads(metadata.read_text(encoding="utf-8"))
                if data.get("status") in {"failed", "pipeline_failed"}:
                    items.append({"case_id": case_dir.name, "error": data.get("error")})
            continue
        tech = json.loads(tech_path.read_text(encoding="utf-8"))
        industry = json.loads((case_dir / "industry_analysis.json").read_text(encoding="utf-8"))
        report = json.loads((case_dir / "comprehensive_report.json").read_text(encoding="utf-8"))
        valid = {str(x.get("evidence_id")) for x in tech.get("evidence") or []}
        valid |= {str(x.get("evidence_id")) for x in industry.get("evidence") or []}
        errors: list[str] = []
        for label, value in (("technology", tech), ("industry", industry), ("report", report)):
            refs = set(BRACKET.findall(json.dumps(value, ensure_ascii=False)))
            invalid = sorted(ref for ref in refs if not re.fullmatch(r"E\d+", ref) or f"{ref}" not in valid)
            if invalid:
                errors.append(f"{label} invalid evidence references: {invalid}")
        for key, item in (tech.get("technology_indicators") or {}).items():
            if item.get("score") is not None and not item.get("evidence"):
                errors.append(f"technology indicator without evidence: {key}")
        maturity = (tech.get("technology_indicators") or {}).get("technical_maturity") or {}
        evidence_text = " ".join(str(x.get("excerpt", "")) for x in tech.get("evidence") or [])
        if maturity.get("score") is not None and maturity.get("score", 0) > 25 and any(x in evidence_text for x in ("计划量产", "正在研发", "尚未规模化", "尚未完成验证")):
            errors.append("future/negative semantic may be over-inferred")
        items.append({"case_id": case_dir.name, "error_count": len(errors), "errors": errors})
    result = {"case_count": len(items), "items": items}
    (root / "error_cases.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "runtime" / "real_cases")
    args = parser.parse_args()
    print(json.dumps(validate(args.root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
