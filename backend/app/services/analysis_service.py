"""Unified PDF-to-report application service for the technology workflow."""

from __future__ import annotations

import sys
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.industry_agent import (
    IndustryAgent,
    IndustryAgentMode,
    IndustryAgentRequest,
    IndustryAnalysis,
)
from app.agents.technology_agent import TechnologyAgent, TechnologyAnalysis, TechnologyAgentRequest
from app.agents.technology_agent import TechnologyAgentMode
from app.core.config import get_settings
from app.llm.provider import LLMProvider, LLMProviderError
from app.rag.document_parser import DocumentSource, PdfDocumentParser
from app.rag.embedding import LocalHashingEmbeddingProvider
from app.rag.knowledge_base import ChromaKnowledgeBase
from app.rag.bm25 import BM25KnowledgeBase
from app.rag.retrieval import KnowledgeBaseRetriever
from app.report import (
    ComprehensiveReport,
    ComprehensiveReportGenerator,
    ComprehensiveReportRequest,
    EvaluationResultInput,
    ReportGenerator,
    ReportRequest,
    TechnologyReport,
)
from app.services.technology_pipeline import TechnologyAnalysisPipeline


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    # The deterministic engine remains a repository-level, framework-neutral package.
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.scoring import TechnologyEvaluationEngine  # noqa: E402
from evaluation.composite_scoring import ComprehensiveEvaluationEngine  # noqa: E402
from evaluation.industry_scoring import IndustryEvaluationEngine  # noqa: E402


class AnalysisServiceError(RuntimeError):
    """Safe task failure that can be returned to an API client."""

    def __init__(self, code: str, message: str, *, category: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.category = category


class TechnologyAnalysisInput(BaseModel):
    """Validated local input consumed by the unified application service."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    enterprise_name: str = Field(min_length=1, max_length=120)
    pdf_path: Path
    original_file_name: str = Field(min_length=1, max_length=255)

    @field_validator("enterprise_name", "original_file_name")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class TechnologyAnalysisArtifacts(BaseModel):
    """All deterministic artifacts produced by one analysis run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    technology_analysis: TechnologyAnalysis
    evaluation_result: EvaluationResultInput
    report: TechnologyReport


class ComprehensiveAssessmentArtifacts(BaseModel):
    """Artifacts for the opt-in technology plus industry workflow."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    technology_analysis: TechnologyAnalysis
    industry_analysis: IndustryAnalysis
    technology_evaluation: EvaluationResultInput
    industry_evaluation: dict
    comprehensive_evaluation: dict
    report: ComprehensiveReport


class TechnologyAssessmentService:
    """Run the v0.5 technology workflow and opt-in v0.7 comprehensive workflow."""

    def __init__(
        self,
        project_root: str | Path = PROJECT_ROOT,
        runtime_root: str | Path | None = None,
        agent_mode: TechnologyAgentMode | str | None = None,
        industry_mode: IndustryAgentMode | str | None = None,
        llm_provider: LLMProvider | None = None,
        retrieval_mode: str | None = None,
        query_mode: str | None = None,
    ) -> None:
        self._project_root = Path(project_root).resolve()
        self._runtime_root = Path(
            runtime_root or self._project_root / "runtime" / "analysis"
        ).resolve()
        self._evaluation_engine = TechnologyEvaluationEngine.from_yaml(
            self._project_root / "evaluation" / "indicators.yaml",
            self._project_root / "evaluation" / "weights.yaml",
        )
        self._report_generator = ReportGenerator()
        configured_mode = agent_mode or get_settings().agent_mode
        self._agent_mode = TechnologyAgentMode(configured_mode)
        self._industry_mode = IndustryAgentMode(industry_mode or IndustryAgentMode.RULE)
        # A production ``llm`` run must receive an explicitly configured provider.
        # Mock providers are injected by tests/evaluation scripts only.
        self._llm_provider = llm_provider
        self._retrieval_mode = retrieval_mode or get_settings().retrieval_mode
        self._query_mode = query_mode

    async def run(
        self, request: TechnologyAnalysisInput
    ) -> TechnologyReport:
        """Run the complete workflow and expose its unified report result."""

        return (await self.run_with_artifacts(request)).report

    async def run_product(self, request: TechnologyAnalysisInput, mode: str) -> dict:
        """Shared upload/experiment entry point. Keeps module failures distinct."""
        if mode not in {"rule_demo", "real_model"}:
            raise AnalysisServiceError("invalid_analysis_mode", "分析模式无效。")
        if mode == "real_model" and self._llm_provider is None:
            settings = get_settings()
            if not (settings.llm_endpoint and settings.llm_model and settings.llm_api_key):
                raise AnalysisServiceError(
                    "model_not_configured",
                    "真实模型分析尚未配置。请在后端设置 KEHENG_LLM_ENDPOINT、KEHENG_LLM_MODEL 和 KEHENG_LLM_API_KEY。",
                    category="configuration",
                )
            from app.llm.api_model import OpenAICompatibleProvider
            self._llm_provider = OpenAICompatibleProvider.from_http(
                settings.llm_endpoint, settings.llm_model, settings.llm_api_key,
                timeout=settings.llm_timeout_seconds, max_retries=0, repair_enabled=True,
            )
        if mode == "rule_demo":
            rule_service = TechnologyAssessmentService(project_root=self._project_root, runtime_root=self._runtime_root, agent_mode=TechnologyAgentMode.RULE, retrieval_mode=self._retrieval_mode)
            artifacts = await rule_service.run_with_artifacts(request)
            return {
                "report": artifacts.report,
                "modules": {"technology": {"status": "completed", "result": artifacts.technology_analysis.model_dump(mode="json")}, "industry": {"status": "not_run", "reason": "规则演示模式当前仅支持技术分析。"}},
                "run_info": self._with_input_snapshot(self._run_info(mode, "rule_demo"), request),
                "model_io": {},
            }

        source_path = request.pdf_path.resolve()
        task_runtime = self._runtime_root / request.task_id / "product"
        retrieval_mode = self._retrieval_mode
        if retrieval_mode == "bm25":
            knowledge_base = BM25KnowledgeBase()
        else:
            knowledge_base = ChromaKnowledgeBase(
                persistence_path=task_runtime / "chroma",
                embedding_provider=LocalHashingEmbeddingProvider(dimensions=1024),
                collection_name="keheng_task_evidence",
            )
        query_mode = self._query_mode or ("bilingual" if retrieval_mode == "bm25" else "original")
        parser = PdfDocumentParser(chunk_size=520, chunk_overlap=80)
        source = DocumentSource(task_id=request.task_id, document_id="technology-profile", file_name=request.original_file_name, content_type="application/pdf", local_path=str(source_path))
        try:
            await knowledge_base.delete_document(request.task_id, source.document_id)
            await knowledge_base.add_document(source, parser)
        except pymupdf.FileDataError as exc:
            raise AnalysisServiceError("damaged_pdf", "PDF 文件损坏或格式无效，无法解析。") from exc
        except ValueError as exc:
            if "No extractable text found" in str(exc):
                raise AnalysisServiceError("no_extractable_text", "PDF 中未检测到可提取文本；当前版本暂不支持扫描件 OCR。") from exc
            raise AnalysisServiceError("analysis_validation_failed", "资料解析失败，请检查 PDF 内容。") from exc
        except Exception as exc:
            raise AnalysisServiceError("analysis_failed", "资料解析失败，分析模块尚未运行。") from exc
        retriever = KnowledgeBaseRetriever(knowledge_base)
        transport = getattr(self._llm_provider, "_transport", None)
        request_start = len(getattr(transport, "request_history", []))
        response_start = len(getattr(transport, "response_history", []))
        observation_start = len(getattr(transport, "observation_history", []))
        technology: TechnologyAnalysis | None = None
        industry: IndustryAnalysis | None = None
        failures: dict[str, dict[str, str]] = {}
        module_mode = TechnologyAgentMode.LLM
        try:
            technology = await TechnologyAgent(retriever, mode=module_mode, llm_provider=self._llm_provider, query_mode=query_mode).analyze(
                TechnologyAgentRequest(task_id=request.task_id, enterprise_name=request.enterprise_name)
            )
        except Exception as exc:
            failures["technology"] = _safe_module_failure(exc)
        if technology is None and _terminal_provider_response(transport):
            failures["industry"] = {
                "code": "skipped_after_terminal_provider_error",
                "category": "provider_auth_or_billing_error",
                "message": "技术模块遇到鉴权、余额或限流错误，已停止后续模型请求；这不是企业证据不足。",
            }
        else:
            try:
                industry = await IndustryAgent(retriever, mode=IndustryAgentMode.LLM, llm_provider=self._llm_provider, query_mode=query_mode).analyze(
                    IndustryAgentRequest(task_id=request.task_id, enterprise_name=request.enterprise_name, evidence_id_start=_next_evidence_id(technology) if technology else 1)
                )
            except Exception as exc:
                failures["industry"] = _safe_module_failure(exc)

        technology_eval = None
        industry_eval = None
        composite_eval = None
        report = None
        if technology is not None:
            try:
                technology_eval = EvaluationResultInput.model_validate(self._evaluation_engine.evaluate(technology).to_dict())
                report = self._report_generator.generate(ReportRequest(task_id=request.task_id, enterprise_name=request.enterprise_name, technology_analysis=technology, evaluation_result=technology_eval))
            except Exception as exc:
                failures["technology_evaluation"] = _safe_processing_failure(exc, "技术评分或报告装配失败")
        if industry is not None:
            try:
                engine = IndustryEvaluationEngine.from_yaml(self._project_root / "evaluation" / "industry" / "indicators.yaml", self._project_root / "evaluation" / "industry" / "weights.yaml")
                industry_eval = engine.evaluate(industry).to_dict()
            except Exception as exc:
                failures["industry_evaluation"] = _safe_processing_failure(exc, "产业评分失败")
        if technology is not None and industry is not None and technology_eval is not None and industry_eval is not None:
            try:
                composite_eval = ComprehensiveEvaluationEngine.from_yaml(self._project_root / "evaluation" / "composite_weights.yaml").evaluate(technology_eval, industry_eval)
                report = ComprehensiveReportGenerator().generate(ComprehensiveReportRequest(task_id=request.task_id, enterprise_name=request.enterprise_name, technology_analysis=technology, industry_analysis=industry, technology_evaluation=technology_eval, industry_evaluation=industry_eval, comprehensive_evaluation=composite_eval.to_dict()))
            except Exception as exc:
                failures["comprehensive_report"] = _safe_processing_failure(exc, "综合评分或报告装配失败")
        module_results = {
            "technology": {"status": "failed", "failure": failures["technology"]} if technology is None else {"status": "partial" if "technology_evaluation" in failures else "completed", "failure": failures.get("technology_evaluation"), "analysis": technology.model_dump(mode="json"), "evaluation": technology_eval.model_dump(mode="json") if technology_eval else None},
            "industry": {"status": "failed", "failure": failures["industry"]} if industry is None else {"status": "partial" if "industry_evaluation" in failures else "completed", "failure": failures.get("industry_evaluation"), "analysis": industry.model_dump(mode="json"), "evaluation": industry_eval},
        }
        result_status = "failed" if not technology and not industry else "partial" if failures else "completed"
        model_io = {} if transport is None else {
            "actual_model_requests": transport.request_history[request_start:],
            "raw_responses": transport.response_history[response_start:],
            "request_observations": transport.observation_history[observation_start:],
        }
        return {"report": report, "modules": module_results, "module_failures": failures, "result_status": result_status, "run_info": self._with_input_snapshot(self._run_info(mode, "real_model"), request), "model_io": model_io}

    def _run_info(self, requested_mode: str, actual_mode: str) -> dict:
        settings = get_settings()
        provider_name = getattr(self._llm_provider, "name", None) if self._llm_provider is not None else None
        if actual_mode == "real_model" and provider_name in {"mock", "test-double", "test_substitute"}:
            actual_mode = "test_substitute"
        config = {"retriever": self._retrieval_mode, "query_mode": self._query_mode or ("bilingual" if self._retrieval_mode == "bm25" else "original"), "embedding": "local-hash-1024" if self._retrieval_mode == "hash" else None, "top_k_per_query": 3, "query_count": 4, "max_context_chunks": 8, "max_context_characters": 4160, "context_assembly_strategy": "balanced_sentences_v1", "context_budget_unit": "characters", "chunk_size": 520, "chunk_overlap": 80}
        files = [Path(__file__), Path(__file__).parents[1] / "agents" / "technology_agent.py", Path(__file__).parents[1] / "agents" / "industry_agent.py", Path(__file__).parents[1] / "rag" / "context_assembly.py", Path(__file__).parents[1] / "rag" / "query_definitions.py", Path(__file__).parents[1] / "rag" / "bm25.py", Path(__file__).parents[1] / "llm" / "api_model.py", self._project_root / "evaluation" / "retrieval_indicator_definitions.json", self._project_root / "evaluation" / "indicators.yaml", self._project_root / "evaluation" / "weights.yaml", self._project_root / "evaluation" / "industry" / "indicators.yaml", self._project_root / "evaluation" / "industry" / "weights.yaml", self._project_root / "evaluation" / "composite_weights.yaml", self._project_root / "prompts" / "technology_agent_prompt.md", self._project_root / "prompts" / "industry_agent_prompt.md"]
        snapshots = {str(path.resolve().relative_to(self._project_root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files if path.is_file()}
        code_hash = hashlib.sha256(json.dumps(snapshots, sort_keys=True).encode()).hexdigest()
        model_config = None if actual_mode == "rule_demo" else {"provider": provider_name or "openai-compatible", "model": settings.llm_model if provider_name is None else None, "temperature": 0, "http_retries": 0, "format_repairs_per_module": 1, "max_context_chunks": 8, "max_context_characters": 4160, "context_budget_unit": "characters"}
        return {"requested_mode": requested_mode, "actual_mode": actual_mode, "execution_kind": "model" if actual_mode == "real_model" else "test_substitute" if actual_mode == "test_substitute" else "rule", "retrieval": config, "model_request_config": model_config, "run_snapshot_files_sha256": snapshots, "source_fingerprint_sha256": code_hash, "started_at_utc": datetime.now(timezone.utc).isoformat(), "git_commit": None}

    @staticmethod
    def _with_input_snapshot(run_info: dict, request: TechnologyAnalysisInput) -> dict:
        run_info["input_snapshot"] = {"file_name": request.original_file_name, "sha256": hashlib.sha256(request.pdf_path.read_bytes()).hexdigest()}
        return run_info

    async def run_comprehensive(
        self, request: TechnologyAnalysisInput
    ) -> ComprehensiveReport:
        """Run the opt-in v0.7 technology plus industry value workflow."""

        return (await self.run_comprehensive_with_artifacts(request)).report

    async def run_comprehensive_with_artifacts(
        self, request: TechnologyAnalysisInput
    ) -> ComprehensiveAssessmentArtifacts:
        """Return both domain analyses and the configuration-driven composite result."""

        source_path = request.pdf_path.resolve()
        task_runtime = self._runtime_root / request.task_id / "comprehensive"
        knowledge_base = self._make_knowledge_base(task_runtime)
        retriever = KnowledgeBaseRetriever(knowledge_base)
        source = DocumentSource(
            task_id=request.task_id,
            document_id="technology-profile",
            file_name=request.original_file_name,
            content_type="application/pdf",
            local_path=str(source_path),
        )
        try:
            technology_analysis = await TechnologyAnalysisPipeline(
                parser=PdfDocumentParser(chunk_size=520, chunk_overlap=80),
                knowledge_base=knowledge_base,
                technology_agent=TechnologyAgent(
                    retriever,
                    mode=self._agent_mode,
                    llm_provider=self._llm_provider,
                    query_mode=self._query_mode or ("bilingual" if self._retrieval_mode == "bm25" else "original"),
                ),
            ).run(source, enterprise_name=request.enterprise_name)
            industry_analysis = await IndustryAgent(
                retriever,
                mode=self._industry_mode,
                llm_provider=self._llm_provider,
                query_mode=self._query_mode or ("bilingual" if self._retrieval_mode == "bm25" else "original"),
            ).analyze(
                IndustryAgentRequest(
                    task_id=request.task_id,
                    enterprise_name=request.enterprise_name,
                    evidence_id_start=_next_evidence_id(technology_analysis),
                )
            )
            technology_evaluation = EvaluationResultInput.model_validate(
                self._evaluation_engine.evaluate(technology_analysis).to_dict()
            )
            industry_engine = IndustryEvaluationEngine.from_yaml(
                self._project_root / "evaluation" / "industry" / "indicators.yaml",
                self._project_root / "evaluation" / "industry" / "weights.yaml",
            )
            industry_evaluation = industry_engine.evaluate(industry_analysis).to_dict()
            comprehensive = ComprehensiveEvaluationEngine.from_yaml(
                self._project_root / "evaluation" / "composite_weights.yaml"
            ).evaluate(technology_evaluation, industry_evaluation)
            report = ComprehensiveReportGenerator().generate(
                ComprehensiveReportRequest(
                    task_id=request.task_id,
                    enterprise_name=request.enterprise_name,
                    technology_analysis=technology_analysis,
                    industry_analysis=industry_analysis,
                    technology_evaluation=technology_evaluation,
                    industry_evaluation=industry_evaluation,
                    comprehensive_evaluation=comprehensive.to_dict(),
                )
            )
        except pymupdf.FileDataError as exc:
            raise AnalysisServiceError(
                "damaged_pdf", "PDF 文件损坏或格式无效，无法解析。"
            ) from exc
        except FileNotFoundError as exc:
            raise AnalysisServiceError(
                "file_not_found", "上传文件已不存在，请重新提交。"
            ) from exc
        except ValueError as exc:
            message = str(exc)
            if "LLM extraction" in message or "Industry LLM" in message:
                category = "invalid_evidence_reference" if "evidence" in message else "schema_failure"
                raise AnalysisServiceError(
                    "llm_extraction_failed",
                    f"LLM 抽取失败（{category}）；当前为 llm 模式，未自动切换到 rule 模式。",
                    category=category,
                ) from exc
            if "No extractable text found" in message:
                raise AnalysisServiceError(
                    "no_extractable_text",
                    "PDF 中未检测到可提取文本；当前版本暂不支持扫描件 OCR。",
                ) from exc
            raise AnalysisServiceError(
                "analysis_validation_failed", "综合分析结果校验失败，请检查输入资料。"
            ) from exc
        except LLMProviderError as exc:
            raise AnalysisServiceError(
                "llm_extraction_failed",
                f"LLM 抽取失败（{exc.category}）；当前为 llm 模式，未自动切换到 rule 模式。",
                category=exc.category,
            ) from exc
        except Exception as exc:
            raise AnalysisServiceError(
                "analysis_failed", "综合分析流程执行失败，请稍后重试。"
            ) from exc

        return ComprehensiveAssessmentArtifacts(
            technology_analysis=technology_analysis,
            industry_analysis=industry_analysis,
            technology_evaluation=technology_evaluation,
            industry_evaluation=industry_evaluation,
            comprehensive_evaluation=comprehensive.to_dict(),
            report=report,
        )

    async def run_with_artifacts(
        self, request: TechnologyAnalysisInput
    ) -> TechnologyAnalysisArtifacts:
        """Return intermediate artifacts for the CLI demo and regression tests."""

        source_path = request.pdf_path.resolve()
        task_runtime = self._runtime_root / request.task_id
        knowledge_base = self._make_knowledge_base(task_runtime)
        retriever = KnowledgeBaseRetriever(knowledge_base)
        pipeline = TechnologyAnalysisPipeline(
            parser=PdfDocumentParser(chunk_size=520, chunk_overlap=80),
            knowledge_base=knowledge_base,
            technology_agent=TechnologyAgent(
                retriever,
                mode=self._agent_mode,
                llm_provider=self._llm_provider,
                query_mode=self._query_mode or ("bilingual" if self._retrieval_mode == "bm25" else "original"),
            ),
        )
        source = DocumentSource(
            task_id=request.task_id,
            document_id="technology-profile",
            file_name=request.original_file_name,
            content_type="application/pdf",
            local_path=str(source_path),
        )

        try:
            analysis = await pipeline.run(
                source, enterprise_name=request.enterprise_name
            )
            evaluation = EvaluationResultInput.model_validate(
                self._evaluation_engine.evaluate(analysis).to_dict()
            )
            report = self._report_generator.generate(
                ReportRequest(
                    task_id=request.task_id,
                    enterprise_name=request.enterprise_name,
                    technology_analysis=analysis,
                    evaluation_result=evaluation,
                )
            )
        except pymupdf.FileDataError as exc:
            raise AnalysisServiceError(
                "damaged_pdf", "PDF 文件损坏或格式无效，无法解析。"
            ) from exc
        except FileNotFoundError as exc:
            raise AnalysisServiceError(
                "file_not_found", "上传文件已不存在，请重新提交。"
            ) from exc
        except ValueError as exc:
            message = str(exc)
            if "LLM extraction" in message or "Industry LLM" in message:
                category = "invalid_evidence_reference" if "evidence" in message else "schema_failure"
                raise AnalysisServiceError(
                    "llm_extraction_failed",
                    f"LLM 抽取失败（{category}）；当前为 llm 模式，未自动切换到 rule 模式。",
                    category=category,
                ) from exc
            if "No extractable text found" in message:
                raise AnalysisServiceError(
                    "no_extractable_text",
                    "PDF 中未检测到可提取文本；当前版本暂不支持扫描件 OCR。",
                ) from exc
            if "Password-protected" in message:
                raise AnalysisServiceError(
                    "password_protected_pdf", "当前版本不支持加密 PDF。"
                ) from exc
            raise AnalysisServiceError(
                "analysis_validation_failed", "分析结果校验失败，请检查输入资料。"
            ) from exc
        except LLMProviderError as exc:
            raise AnalysisServiceError(
                "llm_extraction_failed",
                f"LLM 抽取失败（{exc.category}）；当前为 llm 模式，未自动切换到 rule 模式。",
                category=exc.category,
            ) from exc
        except Exception as exc:
            raise AnalysisServiceError(
                "analysis_failed", "分析流程执行失败，请稍后重试。"
            ) from exc

        return TechnologyAnalysisArtifacts(
            technology_analysis=analysis,
            evaluation_result=evaluation,
            report=report,
        )

    def _make_knowledge_base(self, task_runtime: Path):
        if self._retrieval_mode == "bm25":
            return BM25KnowledgeBase()
        return ChromaKnowledgeBase(
            persistence_path=task_runtime / "chroma",
            embedding_provider=LocalHashingEmbeddingProvider(dimensions=1024),
            collection_name="keheng_task_evidence",
        )


def _next_evidence_id(analysis: TechnologyAnalysis) -> int:
    """Reserve a non-overlapping E-number range for the second Agent."""

    highest = 0
    for item in analysis.evidence:
        try:
            highest = max(highest, int(item.evidence_id.lstrip("E")))
        except ValueError:
            continue
    return highest + 1


def _safe_module_failure(exc: Exception) -> dict[str, object]:
    category = getattr(exc, "category", None) or ("schema_failure" if isinstance(exc, ValueError) else "module_failure")
    code = "llm_extraction_failed" if category != "module_failure" else "module_failed"
    failure: dict[str, object] = {"code": code, "category": str(category), "message": "分析模块处理失败；该状态不代表企业资料不足。"}
    diagnostics = getattr(exc, "diagnostics", None)
    if diagnostics:
        failure["validation_errors"] = diagnostics
    elif isinstance(exc, ValueError):
        failure["validation_errors"] = [{"field": "evidence_reference", "type": "contract_violation", "message": "A cited evidence ID is not present in the current task context."}]
    return failure


def _safe_processing_failure(exc: Exception, label: str) -> dict[str, str]:
    category = getattr(exc, "category", None) or type(exc).__name__
    return {"code": "analysis_processing_failed", "category": str(category), "message": f"{label}；该系统状态不代表企业资料不足。"}


def _terminal_provider_response(transport: Any) -> bool:
    """Stop a two-module run immediately on auth, billing, quota, or rate-limit errors."""
    observations = getattr(transport, "observation_history", []) if transport is not None else []
    if not observations:
        return False
    status = observations[-1].get("http_status_code")
    return status in {401, 402, 403, 429} or bool(observations[-1].get("billing_or_quota_error"))
