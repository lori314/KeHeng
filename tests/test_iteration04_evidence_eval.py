from __future__ import annotations

import unittest
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from evaluation.run_retrieval_comparison import CONTEXT_CHAR_BUDGET, _measure, normalize
from evaluation import run_paired_model_experiment as paired
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.analysis_service import _terminal_provider_response


class Iteration04EvidenceEvaluationTests(unittest.TestCase):
    def _chunk(self, chunk_id: str, text: str, page: int = 15):
        return SimpleNamespace(chunk_id=chunk_id, text=text, page_number=page)

    def _anchor(self, ids: list[str], specified_id: str = "expected-id"):
        quote = "形成以自主研发为主、外部合作为辅的研发模式"
        return {
            "anchor_id": "sha-page-span-anchor",
            "excerpt": quote,
            "page_number": 15,
            "group": "rd_model",
            "all_containing_chunk_ids": ids,
            "legacy_referenced_chunk_id": specified_id,
            "old_evaluator_first_matching_chunk_id": specified_id,
        }

    def test_equivalent_chunk_with_full_source_quote_counts_as_support_hit(self):
        quote = "形成以自主研发为主、外部合作为辅的研发模式"
        chunks = [self._chunk("different-id", f"前文{quote}后文")]
        anchor = self._anchor(["expected-id", "different-id"])
        result = _measure([(0, 1.0)], chunks, [anchor, anchor], True, 1.0)
        self.assertEqual(result["support_text_recall_at_3"], 1.0)
        self.assertEqual(result["old_evaluator_first_chunk_id_recall_at_3"], 0.0)
        self.assertEqual(result["support_text_recall_final_context"], 1.0)
        self.assertEqual(len(result["support_hit_details"]), 1)

    def test_support_truncated_out_of_actual_context_does_not_count(self):
        quote = "形成以自主研发为主、外部合作为辅的研发模式"
        chunks = [
            self._chunk("prefix", "前" * CONTEXT_CHAR_BUDGET),
            self._chunk("support", quote),
        ]
        result = _measure([(0, 2.0), (1, 1.0)], chunks, [self._anchor(["support"])], True, 1.0)
        self.assertEqual(result["support_text_recall_at_3"], 1.0)
        self.assertEqual(result["support_text_recall_final_context"], 0.0)

    def test_normalization_keeps_numbers_negation_and_units(self):
        self.assertEqual(normalize(" 1,194 万元 "), normalize("１,１９４ 万元"))
        self.assertNotEqual(normalize("不低于 10%"), normalize("低于 10%"))
        self.assertNotEqual(normalize("10%"), normalize("10 万元"))

    def test_versioned_case_review_does_not_create_human_gold_or_change_v1_question(self):
        import json
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        old = json.loads((root / "evaluation" / "retrieval_eval_cases.json").read_text(encoding="utf-8"))
        new = json.loads((root / "evaluation" / "retrieval_eval_cases_v2.json").read_text(encoding="utf-8"))
        old_cases = {row["case_id"]: row for row in old["cases"]}
        for row in new["cases"]:
            self.assertEqual(row["question"], old_cases[row["case_id"]]["question"])
            self.assertFalse(row["evidence_assessment"]["human_confirmed"])
            self.assertIsNone(row["evidence_assessment"]["reviewer"])
            self.assertEqual(row["ai_text_review"]["source"], "AI text review")

    def test_missing_model_configuration_is_a_zero_request_preflight_record(self):
        import asyncio
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "fresh-run"
            unavailable = SimpleNamespace(llm_endpoint=None, llm_model=None, llm_api_key=None)
            with patch.object(paired, "get_settings", return_value=unavailable), patch.object(
                paired.OpenAICompatibleProvider, "from_http", side_effect=AssertionError("provider must not be created")
            ):
                result = asyncio.run(paired.execute(["nio", "estun"], output, Path(tmp) / "absent-confirmations.json"))
            self.assertEqual(result["status"], "not_started_missing_configuration")
            self.assertEqual(result["actual_http_requests"], 0)
            self.assertCountEqual(result["missing_configuration_names"], ["KEHENG_LLM_ENDPOINT", "KEHENG_LLM_MODEL", "KEHENG_LLM_API_KEY"])

    def test_auth_error_is_terminal_for_second_module(self):
        self.assertTrue(_terminal_provider_response(SimpleNamespace(observation_history=[{"http_status_code": 401}])))
        self.assertTrue(_terminal_provider_response(SimpleNamespace(observation_history=[{"billing_or_quota_error": True}])))
        self.assertFalse(_terminal_provider_response(SimpleNamespace(observation_history=[{"http_status_code": 500}])))


if __name__ == "__main__":
    unittest.main()
