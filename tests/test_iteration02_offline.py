from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import sys
import unittest
import gc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))


class Iteration02OfflineTests(unittest.TestCase):
    def test_existing_run_directory_is_refused_without_touching_contents(self) -> None:
        from evaluation.run_safety import require_fresh_output_directory

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "old-run"
            target.mkdir()
            old = target / "human_review.csv"
            old.write_text("company,notes\nExample,keep me\n", encoding="utf-8")
            before = old.read_bytes()
            with self.assertRaises(FileExistsError):
                require_fresh_output_directory(target)
            self.assertEqual(old.read_bytes(), before)

    def test_real_case_review_suggestions_never_write_the_human_label_file(self) -> None:
        from evaluation.run_real_cases import _write_human_review

        human_file = ROOT / "data" / "real_cases" / "human_review_v10.csv"
        before = human_file.read_bytes() if human_file.exists() else None
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "fresh-run"
            output.mkdir()
            _write_human_review(output, [{"case_id": "case-x", "enterprise_name": "synthetic"}])
            self.assertTrue((output / "human_review_suggestions.csv").exists())
        after = human_file.read_bytes() if human_file.exists() else None
        self.assertEqual(before, after)


    def test_recompute_counts_missing_and_unsupported_scores_offline(self) -> None:
        from evaluation.recompute_history import recompute

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_path = root / "raw.json"
            raw_path.write_text(json.dumps({"cases": [
                {"case_id": "case_001", "llm_runs": [{"run": 1, "final_status": "first_pass_success", "json_contract_success": True, "artifacts": {"technology_analysis": {"technology_indicators": {}}}}]},
                {"case_id": "case_002", "llm_runs": [{"run": 1, "final_status": "first_pass_success", "json_contract_success": True, "indicator_scores": {"technical_autonomy": 50}, "artifacts": {"technology_analysis": {"technology_indicators": {"technical_autonomy": {"score": 50, "evidence": []}}}}}]}
            ]}), encoding="utf-8")
            output = root / "new-output"
            result = recompute(raw_path, output)
            tech = result["metrics"]["domains"]["technology"]
            self.assertEqual(tech["score_coverage"]["denominator"], 4)
            self.assertEqual(tech["gold_scored_but_model_empty"]["n"], 4)
            self.assertEqual(tech["explicit_insufficient_gold_but_model_scored"]["n"], 1)
            self.assertTrue((output / "legacy_metrics_v1_1_copy.json").exists())
            self.assertTrue((output / "per_case_indicator_run.csv").exists())


    def test_chroma_query_is_task_scoped_and_orphan_refs_are_detectable(self) -> None:
        from types import SimpleNamespace
        from app.rag.document_parser import DocumentChunk
        from app.rag.embedding import LocalHashingEmbeddingProvider
        from app.rag.knowledge_base import ChromaKnowledgeBase
        from app.rag.retrieval import RetrievalQuery
        from evaluation.recompute_history import citation_audit

        with tempfile.TemporaryDirectory() as temporary:
            async def check_isolation() -> None:
                store = ChromaKnowledgeBase(Path(temporary) / "db", LocalHashingEmbeddingProvider(), collection_name="keheng_task_evidence")
                await store.upsert([
                    DocumentChunk(task_id="task-a", document_id="a", document_name="a.pdf", page_number=1, chunk_id="a1", text="patent portfolio and intellectual property", locator="p1", metadata={}),
                    DocumentChunk(task_id="task-b", document_id="b", document_name="b.pdf", page_number=1, chunk_id="b1", text="patent portfolio and intellectual property secret", locator="p1", metadata={}),
                ])
                result = await store.query(RetrievalQuery(task_id="task-a", query="patent portfolio", top_k=10))
                self.assertTrue(result)
                self.assertEqual({item.task_id for item in result}, {"task-a"})
                store._collection = None
                store._client._system.stop()
                store._client = None
                gc.collect()

            asyncio.run(check_isolation())
        case = SimpleNamespace(pdf_path=ROOT / "data" / "evaluation_cases" / "case_001" / "company_profile.pdf")
        audit = citation_audit(case, {"artifacts": {"technology_analysis": {"technology_summary": "orphan [E99]", "technology_indicators": {"technical_autonomy": {"score": 10, "evidence": ["E99"]}}, "evidence": []}}})
        self.assertEqual(audit["structured_reference_ids"]["n"], 1)
        self.assertEqual(audit["structured_reference_ids"]["resolvable_in_domain_evidence"], 0)
        self.assertEqual(audit["free_text_ids"]["n"], 1)
        self.assertEqual(audit["free_text_ids"]["belong_to_task_domain_evidence"], 0)


    def test_bm25_handles_english_and_chinese_regression_terms(self) -> None:
        from evaluation.run_retrieval_comparison import BM25

        corpus = [
            "SMIC ranks the second globally among pure-play foundries.",
            "中芯国际拥有授权发明专利和软件著作权。",
            "其他企业主营业务为机器人制造。",
        ]
        bm25 = BM25(corpus)
        self.assertEqual(bm25.rank("pure-play foundries global ranking", 1)[0][0], 0)
        self.assertEqual(bm25.rank("授权发明专利 知识产权", 1)[0][0], 1)
