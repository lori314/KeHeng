"""Run the v0.5 example through the same application service used by the API."""

import asyncio
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.analysis_service import (  # noqa: E402
    TechnologyAnalysisInput,
    TechnologyAssessmentService,
)
from evaluation.run_safety import new_run_directory, require_fresh_output_directory  # noqa: E402


async def main(output_dir: Path) -> None:
    require_fresh_output_directory(output_dir)
    source_pdf = ROOT / "data" / "examples" / "public_test_company_technology_profile.pdf"
    output_json = output_dir / "technology_analysis.json"
    evaluation_json = output_dir / "evaluation_result.json"
    report_json = output_dir / "technology_report.json"
    service = TechnologyAssessmentService(
        project_root=ROOT,
        runtime_root=output_dir / "work",
    )
    artifacts = await service.run_with_artifacts(
        TechnologyAnalysisInput(
            task_id="public-test-company-v05",
            enterprise_name="启衡智造技术有限公司（完全虚构测试企业）",
            pdf_path=source_pdf,
            original_file_name=source_pdf.name,
        )
    )
    analysis = artifacts.technology_analysis
    evaluation = artifacts.evaluation_result
    report = artifacts.report

    output_json.write_text(
        json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    evaluation_json.write_text(
        json.dumps(evaluation.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    report_payload = json.dumps(
        report.model_dump(mode="json"), ensure_ascii=False, indent=2
    ) + "\n"
    report_json.write_text(report_payload, encoding="utf-8")
    print(f"Analysis written to: {output_json}")
    print(f"Evaluation written to: {evaluation_json}")
    print(f"Report written to: {report_json}")
    print(f"Technology score: {evaluation.technology_score}")
    print(f"Evidence count: {len(analysis.evidence)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=None, help="必须是新目录；默认在 runtime/demo_runs/run-* 下创建")
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir is not None else new_run_directory(ROOT / "runtime" / "demo_runs")
    asyncio.run(main(output_dir))
