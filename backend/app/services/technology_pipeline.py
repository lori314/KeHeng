"""Technology vertical slice: PDF evidence to structured indicators."""

from app.agents.technology_agent import (
    TechnologyAgent,
    TechnologyAgentRequest,
    TechnologyAnalysis,
)
from app.rag.document_parser import DocumentParser, DocumentSource
from app.rag.knowledge_base import KnowledgeBase


class TechnologyAnalysisPipeline:
    """Compose ingestion and analysis without exposing storage internals."""

    def __init__(
        self,
        parser: DocumentParser,
        knowledge_base: KnowledgeBase,
        technology_agent: TechnologyAgent,
    ) -> None:
        self._parser = parser
        self._knowledge_base = knowledge_base
        self._technology_agent = technology_agent

    async def run(
        self, source: DocumentSource, enterprise_name: str | None = None
    ) -> TechnologyAnalysis:
        await self._knowledge_base.delete_document(source.task_id, source.document_id)
        await self._knowledge_base.add_document(source, self._parser)
        return await self._technology_agent.analyze(
            TechnologyAgentRequest(
                task_id=source.task_id,
                enterprise_name=enterprise_name or "未命名企业",
            )
        )
