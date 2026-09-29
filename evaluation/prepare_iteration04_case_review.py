"""Create a versioned AI text-review overlay without changing v1 labels."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASES = ROOT / "evaluation" / "retrieval_eval_cases.json"
SOURCE_REVIEW = ROOT / "docs" / "review" / "iteration_03_evidence_review.md"
OUTPUT = ROOT / "evaluation" / "retrieval_eval_cases_v2.json"

AI_REVIEW = {
    "smic_competitive_position_en": "AI文本复核：保留2024年已公布销售额以及纯晶圆代工企业排名口径；自动定位不等于人工确认。",
    "smic_intellectual_property_en": "AI文本复核：直接核验PDF表格页的表头和数据列，区分本年新增/累计、申请/授权；不要仅按一行数字下结论。",
    "nio_intellectual_property_en": "AI文本复核：‘投入大量资源’本身不证明知识产权资产规模；周围关于保密、保密协议及许可安排的保护措施是相关证据。",
    "estun_intellectual_property_zh": "AI文本复核：数量事实可定位；需要区分授权、发明、未授权申请，并保留对应日期。",
    "cambricon_market_potential_zh": "AI文本复核：第三方预测不能表述为已实现的公司事实；核对完整预测句是否进入最终上下文。",
    "siasun_competitive_position_zh": "AI文本复核：定性份额表述不能推出数值；其他客户应用片段也可能回答现有问题，开放问题下未标注命中应待审。",
    "catl_policy_environment_zh": "AI文本复核：保留原开放问题；不要求必须同时命中特定两项政策。分别报告已标注政策证据覆盖率与至少一项有效政策证据命中。未标注命中列待审，不当作错误。",
    "catl_technical_autonomy_zh": "AI文本复核：包含‘形成以自主研发为主、外部合作为辅的研发模式’的任一等价chunk应接受原文命中；该句不支持‘完全无外部依赖’。",
}


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite versioned case file: {OUTPUT}")
    source = json.loads(SOURCE_CASES.read_text(encoding="utf-8"))
    review_text = SOURCE_REVIEW.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^## ([^\s]+) ·", review_text)
    section_by_case = {sections[i]: sections[i + 1] for i in range(1, len(sections) - 1, 2)}
    for case in source["cases"]:
        case_id = case["case_id"]
        section = section_by_case.get(case_id, "")
        ids = re.findall(r"完整原文包含于 chunk `([^`]+)`", section)
        for i, support in enumerate(case["candidate_supports"]):
            if i < len(ids):
                support["legacy_chunk_id"] = ids[i]
            support["anchor_semantics"] = "exact_source_quote_anchor"
        case["evidence_label_version"] = "v1-preserved-candidate-unconfirmed"
        case["ai_text_review"] = {"source": "AI text review", "status": "awaiting_human_review", "note": AI_REVIEW[case_id]}
        case["evidence_assessment"] = {
            "confirmed_gold": False,
            "human_confirmed": False,
            "reviewer": None,
            "note": "No human confirmation supplied; AI review is not expert gold.",
        }
        if case_id == "catl_policy_environment_zh":
            # Preserve v1's two-anchor candidate and question verbatim. The v2
            # protocol makes one relevant policy sufficient for the open question.
            case["protocol_v2"] = {
                "question_version": "v1 unchanged",
                "minimum_relevant_policy_evidence": 1,
                "separate_labeled_anchor_coverage": True,
                "unlabeled_returned_evidence_status": "pending_review",
            }
        else:
            case["protocol_v2"] = {"question_version": "v1 unchanged"}
    output = {
        "schema_version": "2.0.0",
        "source_file": str(SOURCE_CASES.relative_to(ROOT)),
        "source_sha256": __import__("hashlib").sha256(SOURCE_CASES.read_bytes()).hexdigest(),
        "review_source": str(SOURCE_REVIEW.relative_to(ROOT)),
        "review_type": "AI text review only; no human-confirmed labels added",
        "cases": source["cases"],
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT)
    print("cases", len(output["cases"]), "anchors", sum(len(x["candidate_supports"]) for x in output["cases"]))
    print("legacy_chunk_ids", sum("legacy_chunk_id" in support for x in output["cases"] for support in x["candidate_supports"]))
    print("human_confirmed", sum(x["evidence_assessment"]["human_confirmed"] for x in output["cases"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
