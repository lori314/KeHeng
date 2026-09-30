"""Offline coverage for strict JSON Schema requests and typed repair calls."""

import asyncio
import json
import sys
import unittest
import os
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.knowledge.semantic.structured_call import complete_contract
from app.core.config import Settings
from app.llm import OpenAICompatibleStructuredModel, StructuredModelError
from app.research.contracts import InitialRetrievalPlan


def valid_plan():
    return {
        "company_identity_queries": [{"query": "企业 注册信息", "category": "identity"}],
        "business_queries": [],
        "technology_queries": [],
        "product_queries": [],
        "people_queries": [],
        "reason": "先核验主体。",
        "target_categories": ["identity"],
        "information_gaps": [],
    }


class StructuredJSONSchemaTests(unittest.TestCase):
    def test_thinking_option_is_optional_and_preserves_explicit_boolean_values(self):
        response_body = json.dumps(
            {"choices": [{"message": {"content": json.dumps(valid_plan(), ensure_ascii=False)}}]}
        ).encode()

        for mode in (None, True, False):
            with self.subTest(enable_thinking=mode):
                captured = {}

                def fake_urlopen(request, timeout):
                    captured["payload"] = json.loads(request.data)
                    captured["timeout"] = timeout
                    return BytesIO(response_body)

                kwargs = {} if mode is None else {"enable_thinking": mode}
                model = OpenAICompatibleStructuredModel(
                    "https://provider.invalid/v1", "offline-test", "test-secret", timeout=17, **kwargs
                )
                with patch("app.llm.structured.urlopen", side_effect=fake_urlopen):
                    asyncio.run(model.complete_json("system", {"mode": "test"}))
                if mode is None:
                    self.assertNotIn("enable_thinking", captured["payload"])
                else:
                    self.assertIs(captured["payload"]["enable_thinking"], mode)
                self.assertEqual(captured["timeout"], 17)

    def test_settings_parse_thinking_environment_boolean(self):
        with patch.dict(os.environ, {"KEHENG_LLM_ENABLE_THINKING": "false"}):
            settings = Settings(_env_file=None)
        self.assertIs(settings.llm_enable_thinking, False)
        with patch.dict(os.environ, {"KEHENG_LLM_ENABLE_THINKING": "true"}):
            settings = Settings(_env_file=None)
        self.assertIs(settings.llm_enable_thinking, True)

    def test_typed_contract_accepts_valid_initial_plan_on_first_call(self):
        class SchemaModel:
            def __init__(self):
                self.calls = 0
                self.schema = None

            async def complete_json_schema(
                self, _prompt, _payload, *, schema_name, schema
            ):
                self.calls += 1
                self.schema = (schema_name, schema)
                return valid_plan()

        model = SchemaModel()
        result = asyncio.run(
            complete_contract(
                model, "planner prompt", {"mode": "initial"},
                InitialRetrievalPlan, stage="initial retrieval plan",
            )
        )
        self.assertEqual(result.queries()[0].query, "企业 注册信息")
        self.assertEqual(model.calls, 1)
        self.assertEqual(model.schema[0], "InitialRetrievalPlan")
        self.assertFalse(model.schema[1]["additionalProperties"])

    def test_adapter_sends_strict_json_schema_and_parses_response(self):
        captured = {}
        response_body = json.dumps(
            {"choices": [{"message": {"content": json.dumps(valid_plan(), ensure_ascii=False)}}]}
        ).encode()

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data)
            captured["timeout"] = timeout
            return BytesIO(response_body)

        model = OpenAICompatibleStructuredModel(
            "https://provider.invalid/v1", "offline-test", "test-secret", timeout=17
        )
        with patch("app.llm.structured.urlopen", side_effect=fake_urlopen):
            result = asyncio.run(
                model.complete_json_schema(
                    "system",
                    {"mode": "initial"},
                    schema_name="InitialRetrievalPlan",
                    schema=InitialRetrievalPlan.model_json_schema(),
                )
            )

        response_format = captured["payload"]["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])
        schema = response_format["json_schema"]["schema"]
        self.assertEqual(schema["type"], "object")
        self.assertIn("properties", schema)
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("$defs", schema)  # nested Pydantic definitions stay intact
        self.assertEqual(result["reason"], "先核验主体。")
        self.assertEqual(captured["timeout"], 17)

    def test_initial_retrieval_plan_uses_fake_model_fallback_and_one_repair(self):
        class FakeModel:
            def __init__(self):
                self.calls = []

            async def complete_json(self, _prompt, payload):
                self.calls.append(payload)
                if len(self.calls) == 1:
                    return {"company_identity_queries": [], "reason": ""}
                return valid_plan()

        model = FakeModel()
        result = asyncio.run(
            complete_contract(
                model, "planner prompt", {"mode": "initial"},
                InitialRetrievalPlan, stage="initial retrieval plan",
            )
        )
        self.assertEqual(result.queries()[0].query, "企业 注册信息")
        self.assertEqual(len(model.calls), 2)
        self.assertIn("validation_errors", model.calls[1])
        self.assertTrue(model.calls[1]["validation_errors"][0]["location"])

    def test_failed_repair_has_readable_redacted_diagnostics(self):
        secret = "sk-testsecretvalue123456789"

        class BadModel:
            def __init__(self):
                self.calls = 0

            async def complete_json(self, _prompt, _payload):
                self.calls += 1
                return {secret: "ignored", "company_identity_queries": [], "reason": ""}

        model = BadModel()
        with self.assertRaises(StructuredModelError) as caught:
            asyncio.run(
                complete_contract(
                    model, "planner prompt", {"mode": "initial"},
                    InitialRetrievalPlan, stage="initial retrieval plan",
                )
            )
        error = caught.exception
        self.assertEqual(error.category, "schema_failure")
        self.assertEqual(model.calls, 2)
        self.assertTrue(error.diagnostics)
        rendered = json.dumps(error.diagnostics)
        self.assertIn("extra_forbidden", rendered)
        self.assertNotIn(secret, rendered)
        self.assertIsNone(error.raw_output)


if __name__ == "__main__":
    unittest.main()
