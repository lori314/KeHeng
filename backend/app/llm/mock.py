"""Deterministic mock extraction provider for offline v0.6 regression tests."""

from __future__ import annotations

import re

from app.llm.provider import (
    LLMContextChunk,
    LLMExtractionRequest,
    LLMExtractionResult,
    LLMIndustryExtractionResult,
    LLMFinding,
    LLMIndicatorExtraction,
    LLMProvider,
)


class MockLLMProvider(LLMProvider):
    """Simulate structured model extraction without network access or a model file."""

    name = "mock"
    implementation_version = "deterministic-evidence-mock-v0.6"

    async def extract(self, request: LLMExtractionRequest) -> LLMExtractionResult:
        chunks = request.chunks
        summary_chunks = chunks[: min(3, len(chunks))]
        summary_ids = [chunk.evidence_id for chunk in summary_chunks]
        indicators = {
            "technical_autonomy": self._autonomy(chunks),
            "innovation_capability": self._innovation(chunks),
            "intellectual_property": self._ip(chunks),
            "technical_maturity": self._maturity(chunks),
        }
        strengths = self._strengths(chunks)
        risks = self._risks(chunks)
        return LLMExtractionResult(
            technology_summary=(
                "Mock LLM 根据检索证据提取技术研发、知识产权和验证事实；"
                "最终技术分由独立评价引擎计算。"
            ) if summary_ids else "证据不足：当前提供的检索片段无法支持技术事实摘要。",
            summary_status="supported" if summary_ids else "insufficient_evidence",
            summary_evidence_ids=summary_ids,
            technology_indicators=indicators,
            strengths=strengths,
            risks=risks,
        )

    async def extract_industry(self, request: LLMExtractionRequest) -> LLMIndustryExtractionResult:
        chunks = request.chunks
        return LLMIndustryExtractionResult(
            industry_indicators={
                "market_potential": _industry_scored(chunks, ("客户", "订单", "交付", "市场需求"), 75),
                "industry_growth": _industry_scored(chunks, ("增长", "增速", "CAGR", "市场规模"), 75),
                "competitive_position": _industry_scored(chunks, ("市场份额", "竞争优势", "差异化", "壁垒", "核心客户"), 70),
                "policy_environment": _industry_scored(chunks, ("政策", "产业规划", "专项资金", "补贴", "监管"), 75),
            }
        )

    @staticmethod
    def _autonomy(chunks: list[LLMContextChunk]) -> LLMIndicatorExtraction:
        item = _first_match(chunks, ("自主研发", "自研"))
        if item:
            return _scored(80, item, "材料描述自主研发核心能力；权属仍需核验。")
        item = _first_match(chunks, ("核心技术", "核心算法"))
        if item:
            return _scored(60, item, "材料描述核心技术，但自主性证据有限。")
        item = _first_match(chunks, ("依赖第三方", "外部依赖"))
        if item:
            return _scored(35, item, "材料显示存在外部依赖，未确认自主核心能力。")
        return _missing("未检索到技术自主性有效证据。")

    @staticmethod
    def _innovation(chunks: list[LLMContextChunk]) -> LLMIndicatorExtraction:
        groups = (
            ("技术方案", "算法", "模型压缩", "小样本"),
            ("研发团队", "研发人员", "研发投入"),
            ("数据标注", "模型训练", "边缘推理", "结果追溯"),
        )
        matched: list[LLMContextChunk] = []
        group_hits = 0
        for group in groups:
            item = _first_match(chunks, group)
            if item:
                group_hits += 1
                if item not in matched:
                    matched.append(item)
        if not matched:
            return _missing("未检索到可核验的创新活动证据。")
        score = min(100, 40 + group_hits * 15)
        return LLMIndicatorExtraction(
            score=score,
            evidence_ids=[item.evidence_id for item in matched],
            rationale=f"材料命中 {group_hits} 类创新活动证据；该分值仅为指标输入。",
            confidence=0.8,
        )

    @staticmethod
    def _ip(chunks: list[LLMContextChunk]) -> LLMIndicatorExtraction:
        item = _first_affirmed_match(chunks, ("获得授权", "授权专利"))
        if item:
            return _scored(80, item, "材料包含已授权知识产权记录，权属仍需核验。")
        item = _first_match(chunks, ("实质审查", "未获得授权"))
        if item:
            return _scored(55, item, "专利布局已开展，但权利稳定性待核验。")
        item = _first_match(chunks, ("专利申请", "软件著作权"))
        if item:
            return _scored(40, item, "材料确认知识产权申请或登记活动，质量证据有限。")
        return _missing("未检索到知识产权有效证据。")

    @staticmethod
    def _maturity(chunks: list[LLMContextChunk]) -> LLMIndicatorExtraction:
        item = _first_affirmed_match(
            chunks,
            ("规模化部署", "批量交付", "量产", "正式商业运营"),
            required_markers=("已进入", "已完成", "已实现", "正在", "实现量产"),
        )
        if item:
            return _scored(85, item, "材料显示已进入规模化部署或批量交付。")
        item = _first_affirmed_match(
            chunks,
            ("开展试点", "中试验证", "客户现场验证", "在生产线上", "连续运行"),
        )
        if item:
            return _scored(65, item, "材料显示原型已在相关环境完成有限验证。")
        item = _first_affirmed_match(chunks, ("原型", "样机", "实验室验证"))
        if item:
            return _scored(45, item, "材料显示已完成实验室原型或样机验证。")
        item = _first_match(chunks, ("技术方案", "算法方案", "研发方向"))
        if item:
            return _scored(25, item, "资料仅支持早期技术概念判断。")
        return _missing("未检索到技术成熟度有效证据。")

    @staticmethod
    def _strengths(chunks: list[LLMContextChunk]) -> list[LLMFinding]:
        definitions = (
            (("自主研发", "自研", "核心技术"), "材料显示已形成自主研发或核心技术积累"),
            (("专利", "知识产权", "软著"), "材料显示已开展知识产权布局"),
            (("试点", "客户现场", "生产线", "连续运行"), "材料显示技术原型已进入真实或近真实环境验证"),
        )
        findings: list[LLMFinding] = []
        for keywords, conclusion in definitions:
            item = _first_match(chunks, keywords)
            if item:
                findings.append(
                    LLMFinding(content=conclusion, evidence_ids=[item.evidence_id])
                )
        return findings

    @staticmethod
    def _risks(chunks: list[LLMContextChunk]) -> list[LLMFinding]:
        definitions = (
            (("范围有限", "仅覆盖", "规模化效果待验证"), "现有验证范围有限，规模化效果需要更多样本确认"),
            (("依赖第三方", "外部依赖"), "技术效果或交付可能依赖第三方组件及外部环境"),
            (("实质审查", "未授权", "未获得授权", "专利申请"), "知识产权仍处于申请或审查阶段，权利稳定性需要持续核验"),
        )
        findings: list[LLMFinding] = []
        for keywords, conclusion in definitions:
            item = _first_match(chunks, keywords)
            if item:
                findings.append(
                    LLMFinding(content=conclusion, evidence_ids=[item.evidence_id])
                )
        return findings


def _scored(
    score: int,
    item: LLMContextChunk,
    rationale: str,
) -> LLMIndicatorExtraction:
    return LLMIndicatorExtraction(
        score=score,
        evidence_ids=[item.evidence_id],
        rationale=rationale,
        confidence=0.8,
    )


def _missing(rationale: str) -> LLMIndicatorExtraction:
    return LLMIndicatorExtraction(score=None, rationale=rationale, confidence=0.2)


def _industry_scored(
    chunks: list[LLMContextChunk], keywords: tuple[str, ...], score: int
) -> LLMIndicatorExtraction:
    item = _first_match(chunks, keywords)
    if item is None:
        return _missing("未检索到产业指标有效证据。")
    return LLMIndicatorExtraction(
        score=score,
        evidence_ids=[item.evidence_id],
        rationale="材料提供了与该产业指标相关的可定位证据；行业趋势不等同企业成功。",
        confidence=0.8,
    )


def _first_match(
    chunks: list[LLMContextChunk], keywords: tuple[str, ...]
) -> LLMContextChunk | None:
    return next(
        (chunk for chunk in chunks if any(keyword in chunk.excerpt for keyword in keywords)),
        None,
    )


def _first_affirmed_match(
    chunks: list[LLMContextChunk],
    keywords: tuple[str, ...],
    required_markers: tuple[str, ...] = (),
) -> LLMContextChunk | None:
    non_factual_markers = (
        "尚未",
        "未完成",
        "未获得",
        "待验证",
        "建议",
        "计划",
        "预计",
        "拟",
        "目标",
        "需要",
        "需提供",
        "不能",
    )
    for chunk in chunks:
        for clause in re.split(r"[。！？；]", chunk.excerpt):
            if not any(keyword in clause for keyword in keywords):
                continue
            if any(marker in clause for marker in non_factual_markers):
                continue
            if required_markers and not any(marker in clause for marker in required_markers):
                continue
            return chunk
    return None
