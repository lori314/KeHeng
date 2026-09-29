import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.rag.context_assembly import assemble_context
from app.rag.retrieval import RetrievedEvidence


def evidence(chunk_id, text, score, group, page=1):
    return RetrievedEvidence(
        task_id="task-one", document_id="doc", document_name="profile.pdf",
        page_number=page, chunk_id=chunk_id, text=text, locator=f"p.{page}",
        score=score, metadata={"retrieval_query_groups": str(group)},
    )


class Iteration05ContextAssemblyTests(unittest.TestCase):
    def test_round_robin_keeps_lower_ranked_indicator_and_full_support_sentence(self):
        candidates = [
            evidence(f"g0-{i}", f"高分材料{i}。" + "補" * 480, 1 - i / 100, 0)
            for i in range(8)
        ]
        candidates.append(evidence("g1-support", "形成以自主研发为主、外部合作为辅的研发模式。" + "说明" * 50, .5, 1, 15))
        selected, audit = assemble_context(candidates, max_chunks=8, max_characters=4160)
        self.assertIn("g1-support", [item.chunk_id for item in selected])
        context = "".join(item.text for item in selected)
        self.assertIn("形成以自主研发为主、外部合作为辅的研发模式。", context)
        self.assertLessEqual(len(context), 4160)
        self.assertEqual(audit["budget_unit"], "characters")

    def test_overlap_dedupe_preserves_location_and_budget_excludes_whole_sentence(self):
        first = evidence("first", "共同证据句。第一份材料另有完整说明。", .9, 0, 4)
        second = evidence("second", "共同证据句。第二句。", .8, 1, 5)
        third = evidence("third", "预算之外的一整条超长证据句。", .7, 2, 6)
        selected, audit = assemble_context([first, second, third], max_chunks=8, max_characters=25)
        self.assertEqual([item.page_number for item in selected], [4, 5])
        text = "".join(item.text for item in selected)
        self.assertEqual(text.count("共同证据句。"), 1)
        self.assertIn("第二句。", text)
        self.assertLessEqual(len(text), 25)
        self.assertTrue(audit["budget_exclusions"])

    def test_semicolon_joined_metrics_stay_together_when_overlapping_chunks_are_deduplicated(self):
        statement = "截至2024年12月31日，新增授权专利69件；累计授权专利590件，其中发明专利252件。"
        first = evidence("p15-a", "披露说明。" + statement, .9, 0, 15)
        second = evidence("p15-b", statement + "未授权申请156件。", .8, 1, 15)
        selected, _ = assemble_context([first, second], max_chunks=8, max_characters=4160)
        combined = "\n".join(item.text for item in selected)
        self.assertIn(statement, combined)


if __name__ == "__main__":
    unittest.main()
