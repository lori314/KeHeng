"""Offline tests for end-to-end smoke review audit helpers."""

from __future__ import annotations

import contextlib
import asyncio
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"
for item in (str(BACKEND), str(SCRIPTS)):
    if item not in sys.path:
        sys.path.insert(0, item)

from app.core.config import Settings
from app.research.contracts import SearchResult
from run_end_to_end_review import (
    _scope_for,
    choose_run_dir,
    execute_review,
    missing_provider_categories,
    render_review_markdown,
    run_quality_checks,
)


class EndToEndReviewRunnerTest(unittest.TestCase):
    def test_provider_preflight_reports_categories_without_secrets(self):
        settings = Settings(llm_api_key="must-never-be-printed", tavily_api_key="also-secret")
        self.assertEqual(missing_provider_categories(settings), ["LLM", "Tavily"])

    def test_complete_provider_configuration_passes_preflight(self):
        settings = Settings(
            llm_endpoint="https://llm.example/v1",
            llm_model="example-model",
            llm_api_key="opaque-secret",
            web_search_provider="tavily",
            tavily_api_key="opaque-tavily-secret",
        )
        self.assertEqual(missing_provider_categories(settings), [])

    def test_missing_provider_cli_does_not_create_output_or_print_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "run"
            settings = Settings(llm_api_key="private-llm-key", tavily_api_key="private-search-key")
            output = io.StringIO()
            with patch("run_end_to_end_review.get_settings", return_value=settings):
                with contextlib.redirect_stdout(output):
                    from run_end_to_end_review import main

                    status = main(["示例企业", "--output-dir", str(target)])
            self.assertEqual(status, 2)
            self.assertIn("REAL_SMOKE_NOT_RUN", output.getvalue())
            self.assertIn("LLM", output.getvalue())
            self.assertIn("Tavily", output.getvalue())
            self.assertNotIn("private-llm-key", output.getvalue())
            self.assertNotIn("private-search-key", output.getvalue())
            self.assertFalse(target.exists())

    def test_run_directory_default_is_scoped_by_run_id_and_override_is_exact(self):
        default = choose_run_dir(None, "run-123")
        self.assertEqual(default, (ROOT / "runtime" / "review" / "end_to_end" / "run-123").resolve())
        with tempfile.TemporaryDirectory() as temp:
            override = Path(temp) / "custom-run"
            self.assertEqual(choose_run_dir(override, "unused"), override.resolve())

    def test_scope_uses_source_version_then_chunk_metadata(self):
        self.assertEqual(_scope_for({"metadata": {"content_scope": "search_snippet"}}, {"metadata": {"content_scope": "full_content"}}), "full_content")
        self.assertEqual(_scope_for({"metadata": {"content_scope": "search_snippet"}}, None), "search_snippet")

    def test_quality_review_flags_provenance_snippet_overuse_and_source_mix(self):
        technology = {
            "technology_facts": [{
                "fact_id": "tech-1", "citation": None,
                "source_quality": {"category": "snippet_only"},
            }],
            "milestone_observations": [{
                "template_id": "template-a", "milestone_id": "milestone-a",
                "status": "supported", "supporting_fact_ids": ["tech-1"],
            }, {
                "template_id": "template-a", "milestone_id": "milestone-b",
                "status": "supported", "supporting_fact_ids": [],
            }],
        }
        finance = {
            "financial_facts": [{
                "fact_id": "fin-1", "citation": None, "source_quality": {"category": "snippet_only"},
            }],
            "applicable_rule_ids": ["rule-known"],
            "funding_activities": [{
                "status": "supported",
                "evidence_bundle": {
                    "technology_fact_ids": ["tech-1"],
                    "financial_fact_ids": ["fin-1"],
                    "milestone_refs": ["template-a:milestone-a"],
                    "rule_ids": ["rule-known"],
                },
            }],
            "risk_observations": [{
                "status": "insufficient_evidence",
                "evidence_bundle": {
                    "technology_fact_ids": ["missing-tech"],
                    "financial_fact_ids": [],
                    "milestone_refs": ["template-a:missing-milestone"],
                    "rule_ids": ["rule-unknown"],
                },
            }],
            "risk_score": 0.7,
        }
        sources = {"sources": [
            {"url": f"https://one.example/page/{index}", "source_type": "web", "company_ids": ["company-1"]}
            for index in range(3)
        ]}
        research = {"relevant_source_count": 3, "sources_ingested": 3}

        result = run_quality_checks(technology, finance, sources, research, "company-1")

        self.assertFalse(result["checks"]["provenance"]["passed"])
        snippet_supported = result["checks"]["snippet_overuse"]["supported_observations_using_only_snippet_facts"]
        self.assertEqual({item["kind"] for item in snippet_supported}, {"finance_observation", "technology_milestone"})
        self.assertEqual(result["checks"]["source_mixture"]["unknown_web_ratio"], 1.0)
        self.assertEqual(result["checks"]["source_mixture"]["most_repeated_host"], "one.example")
        self.assertFalse(result["checks"]["over_inference"]["passed"])
        self.assertIn("risk_score", result["checks"]["finance_boundary"]["prohibited_fields_found"][0])
        self.assertTrue(any("snippet_only" in warning for warning in result["warnings"]))

    def test_resolved_entity_without_website_is_reported(self):
        result = run_quality_checks(
            {}, {}, {"sources": []}, {}, "company-1",
            {"resolution_status": "resolved", "official_website": None},
        )
        self.assertTrue(result["checks"]["official_website"]["resolved_entity_without_official_website"])
        self.assertIn("主体已解析，但没有可验证的官方官网", result["warnings"])

    def test_finance_evidence_strength_check_flags_unsupported_milestone_and_weak_support(self):
        technology = {
            "technology_facts": [{"fact_id": "tech-1", "source_quality": {"category": "weak_web"}, "citation": {}}],
            "milestone_observations": [{
                "template_id": "semiconductor_design", "milestone_id": "tapeout",
                "status": "limited_support", "supporting_fact_ids": ["tech-1"],
                "contradicting_fact_ids": [],
            }],
        }
        finance = {
            "financial_facts": [{"fact_id": "fin-1", "source_quality": {"category": "weak_web"}, "citation": {}}],
            "applicable_rule_ids": ["semiconductor_tapeout_validation"],
            "risk_observations": [{
                "status": "supported",
                "evidence_bundle": {
                    "technology_fact_ids": ["tech-1"], "financial_fact_ids": ["fin-1"],
                    "milestone_refs": ["semiconductor_design:tapeout"],
                    "rule_ids": ["semiconductor_tapeout_validation"],
                },
            }],
        }
        check = run_quality_checks(technology, finance, {"sources": []}, {}, "company-1")["checks"]["finance_evidence_strength"]
        self.assertFalse(check["passed"])
        self.assertEqual(len(check["supported_outputs_with_non_supported_milestones"]), 1)
        self.assertEqual(check["milestone_conditioned_outputs_without_milestone_refs"], [])
        self.assertEqual(check["milestone_evidence_missing_fact_ids"], [])
        self.assertEqual(len(check["supported_outputs_using_only_weak_evidence"]), 1)

        finance["risk_observations"][0]["status"] = "limited_support"
        check = run_quality_checks(technology, finance, {"sources": []}, {}, "company-1")["checks"]["finance_evidence_strength"]
        self.assertTrue(check["passed"])

    def test_markdown_shows_summary_without_source_body(self):
        markdown = render_review_markdown({
            "run": {"run_id": "run-1", "status": "completed"},
            "entity": {"input_name": "企业", "resolution_status": "resolved", "identity_evidence": {}},
            "research": {"source_samples": [{"source_type": "web", "title": "页面标题", "url": "https://example.test", "content_scope": "full_content"}]},
            "quality_checks": {"warnings": []},
        })
        self.assertIn("# KeHeng End-to-End Review", markdown)
        self.assertIn("https://example.test", markdown)
        self.assertNotIn("raw_content", markdown)

    def test_markdown_includes_finance_evidence_strength_check(self):
        markdown = render_review_markdown({
            "run": {"run_id": "run-finance"},
            "quality_checks": {"checks": {"finance_evidence_strength": {"passed": True}}},
        })
        self.assertIn('"finance_evidence_strength"', markdown)

    def test_markdown_includes_financial_extraction_batch_audit(self):
        markdown = render_review_markdown({
            "run": {"run_id": "run-finance-batches"},
            "technology_finance": {
                "financial_fact_count": 3,
                "financial_dimension_distribution": {"revenue": 2, "rd_expense": 1},
                "financial_fact_extraction_report": {
                    "batch_count": 4, "successful_batch_count": 3,
                    "failed_batch_count": 1, "input_chunk_count": 15,
                    "successful_chunk_count": 11, "failed_chunk_count": 4,
                    "source_quality_distribution": {"weak_web": 3},
                    "error_categories": {"timeout": 1},
                    "rejected_evidence_reference_count": 2,
                    "rejected_dimension_count": 1,
                },
            },
        })
        self.assertIn("3/4 succeeded; 1 failed", markdown)
        self.assertIn('"timeout": 1', markdown)
        self.assertIn("Rejected references / dimensions: 2 / 1", markdown)

    def test_markdown_includes_deterministic_finance_mapping_audit(self):
        markdown = render_review_markdown({
            "technology_finance": {
                "financial_fact_count": 0,
                "mapping_audit": {
                    "mapping_mode": "deterministic_registry",
                    "candidate_rule_count": 2,
                    "candidate_rule_ids": ["rule-a", "rule-b"],
                    "selected_rule_count": 2,
                    "selected_rule_ids": ["rule-a", "rule-b"],
                    "milestone_conditioned_candidate_count": 1,
                    "general_candidate_count": 1,
                    "llm_mapping_call": "none",
                },
            },
        })
        self.assertIn("Mapping mode/status: `deterministic_registry`", markdown)
        self.assertIn('"rule-a", "rule-b"', markdown)
        self.assertIn("Milestone-conditioned / general candidates: 1 / 1", markdown)
        self.assertIn("LLM mapping call: `none`", markdown)

    def test_markdown_includes_finance_failure_stage_trace(self):
        markdown = render_review_markdown({
            "run": {"run_id": "run-finance-failed", "status": "failed"},
            "finance_execution": {
                "finance_stage": "failed", "finance_substage": "financial_fact_extraction",
                "financial_fact_batch_count": 15, "financial_fact_failed_batches": 15,
                "financial_fact_error_categories": {"timeout": 15},
            },
            "errors": [{"stage": "technology_finance", "finance_substage": "financial_fact_extraction", "category": "timeout"}],
        })
        self.assertIn("Finance Execution Trace", markdown)
        self.assertIn("financial_fact_extraction", markdown)

    def test_markdown_reports_semantic_selection_and_failure_substage(self):
        markdown = render_review_markdown({
            "run": {"run_id": "run-2", "status": "failed"},
            "entity": {"input_name": "企业", "resolution_status": "resolved", "identity_evidence": {}},
            "research": {"source_samples": []},
            "semantic_execution": {
                "semantic_stage": "classifier",
                "semantic_substage": "domain_template_classifier",
                "available_chunk_count": 356,
                "selected_chunk_count": 18,
                "selected_source_count": 15,
                "selected_char_count": 44000,
                "truncated_chunk_count": 3,
                "quality_distribution": {"first_party": 3, "weak_web": 15},
                "content_scope_distribution": {"full_content": 18},
                "source_type_distribution": {"web": 18},
                "dropped_due_to_budget": 10,
                "dropped_due_to_source_cap": 300,
                "dropped_as_duplicate": 28,
                "fact_extraction_batch_count": 6,
                "fact_extraction_completed_batches": 5,
                "fact_extraction_failed_batches": 1,
                "fact_extraction_input_chunks": 18,
                "fact_extraction_successful_chunks": 15,
                "fact_extraction_failed_chunks": 3,
                "fact_extraction_fact_count": 24,
                "fact_extraction_error_categories": {"timeout": 1},
                "technology_fact_type_registry_version": "technology-fact-types.v1",
                "fact_extraction_rejected_fact_type_count": 3,
                "fact_extraction_rejected_fact_type_distribution": {"corporate_info": 2, "revenue": 1},
            },
            "errors": [{"stage": "technology_semantic", "semantic_substage": "domain_template_classifier", "category": "timeout"}],
            "quality_checks": {"warnings": []},
        })
        self.assertIn("Classifier evidence: 356 → 18 chunks; 15 sources; 44000 chars", markdown)
        self.assertIn("Classifier truncated chunks: 3", markdown)
        self.assertIn("Classifier quality / content scope / source types", markdown)
        self.assertIn("Technology fact evidence: 0 → 0 chunks", markdown)
        self.assertIn("Fact extraction batches: 6", markdown)
        self.assertIn("Succeeded / failed batches: 5 / 1", markdown)
        self.assertIn("Input chunks covered (successful / total; failed): 15 / 18; 3", markdown)
        self.assertIn('Fact extraction batch errors: `{"timeout": 1}`', markdown)
        self.assertIn("Technology fact type registry version: `technology-fact-types.v1`", markdown)
        self.assertIn('Rejected technology fact types: 3; distribution `{"corporate_info": 2, "revenue": 1}`', markdown)
        self.assertIn("technology_semantic at domain_template_classifier", markdown)
        self.assertIn("`timeout`", markdown)

    def test_failed_identity_validation_writes_safe_partial_research_snapshot(self):
        class FakeModel:
            def __init__(self):
                self.responses = [
                    {
                        "company_identity_queries": [{
                            "query": "测试企业 主体信息",
                            "category": "company_identity",
                        }],
                        "business_queries": [], "technology_queries": [],
                        "product_queries": [], "people_queries": [],
                        "reason": "先核验主体信息",
                        "target_categories": ["company_identity"],
                        "information_gaps": [],
                    },
                    {
                        "status": "resolved",
                        "canonical_name": "未出现在检索结果中的企业名称",
                        "aliases": [], "official_website": None,
                        "unified_social_credit_code": None,
                        "evidence_urls": ["https://identity.example.test/0"],
                        "identity_candidates": [],
                        "reason": "模型声称的主体",
                    },
                ]

            async def complete_json(self, _prompt, _payload):
                return self.responses.pop(0)

        class FakeSearch:
            name = "tavily"

            async def search(self, _request):
                return [
                    SearchResult(
                        title=f"登记页面 {index}",
                        url=f"https://identity.example.test/{index}",
                        content=f"登记页面摘要 {index}",
                        raw_content=(f"登记主体页面 {index}。" * 100),
                        provider="tavily",
                    )
                    for index in range(3)
                ]

            async def extract(self, _urls):
                return {}

        settings = Settings(
            llm_endpoint="https://llm.example.test/v1",
            llm_model="test-model",
            llm_api_key="never-print-llm-secret",
            web_search_provider="tavily",
            tavily_api_key="never-print-search-secret",
        )
        args = SimpleNamespace(
            enterprise_name="测试企业",
            max_rounds=3,
            max_queries_per_round=5,
            max_results_per_query=5,
        )
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "partial-review"
            with (
                patch("app.services.evidence_analysis_service.OpenAICompatibleStructuredModel", return_value=FakeModel()),
                patch("app.services.evidence_analysis_service.TavilySearchProvider", return_value=FakeSearch()),
            ):
                review = asyncio.run(execute_review(args, settings, run_dir, "partial-run"))

            self.assertEqual(review["run"]["status"], "failed")
            self.assertEqual(review["errors"][0]["category"], "unsupported_identity_claim")
            research = review["research"]
            self.assertEqual(research["queries_executed"], ["测试企业 主体信息"])
            self.assertEqual(research["identity_results_count"], 3)
            self.assertEqual(research["sources_found"], 3)
            self.assertEqual(research["sources_ingested"], 0)
            self.assertEqual(research["rounds"], 0)
            self.assertEqual(research["current_stage"], "entity_validation_failed")
            self.assertEqual(review["entity"]["input_name"], "测试企业")

            markdown = (run_dir / "review.md").read_text(encoding="utf-8")
            self.assertIn("- Input: 测试企业", markdown)
            self.assertIn("- Current stage: `entity_validation_failed`", markdown)
            self.assertIn("- Identity search results: 3", markdown)
            self.assertIn("- Queries: 1", markdown)
            self.assertIn("- Sources found / ingested: 3 / 0", markdown)
            self.assertIn("unsupported_canonical_name", markdown)
            self.assertNotIn("never-print-llm-secret", markdown)
            self.assertNotIn("never-print-search-secret", markdown)

            serialized = json.loads((run_dir / "review.json").read_text(encoding="utf-8"))
            snapshot_text = json.dumps(serialized["research"], ensure_ascii=False)
            self.assertNotIn("identity.example.test", snapshot_text)
            self.assertNotIn("登记主体页面", snapshot_text)


if __name__ == "__main__":
    unittest.main()
