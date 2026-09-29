"""Create an editable human-review pack from existing PDFs and candidate results."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.rag.document_parser import DocumentSource, PdfDocumentParser  # noqa: E402


def norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def printed_page(text: str) -> str:
    first_lines = [line.strip() for line in text[:240].splitlines() if line.strip()]
    for line in first_lines[:5]:
        if re.fullmatch(r"\d{1,4}", line):
            return line
    tail = text[-420:]
    last_lines = [line.strip() for line in tail.splitlines() if line.strip()]
    for line in reversed(last_lines[-4:]):
        if re.fullmatch(r"\d{1,4}", line):
            return line
        branded = re.match(r"^(\d{1,4})\s+[A-Za-z]", line)
        if branded:
            return branded.group(1)
    matches = re.findall(r"(?:第\s*(\d{1,4})\s*页|页\s*(\d{1,4})|page\s*(\d{1,4}))", tail, re.IGNORECASE)
    if matches:
        return next((part for part in matches[-1] if part), "未识别")
    return "未从可提取页脚文字识别；需人工核对"


def source_context(text: str, quote: str, radius: int = 170) -> str:
    normalized: list[str] = []
    offsets: list[int] = []
    for offset, char in enumerate(text):
        value = unicodedata.normalize("NFKC", char).casefold()
        if char.isspace():
            if normalized and normalized[-1] != " ":
                normalized.append(" "); offsets.append(offset)
            continue
        normalized.extend(value)
        offsets.extend([offset] * len(value))
    target = " ".join(unicodedata.normalize("NFKC", quote).casefold().split())
    joined = "".join(normalized)
    position = joined.find(target)
    if position < 0 or not offsets:
        return " ".join(text.split())[:420]
    start_pos, end_pos = max(0, position - radius), min(len(offsets) - 1, position + len(target) + radius)
    return " ".join(text[offsets[start_pos]:offsets[end_pos] + 1].split())


async def create(comparison_path: Path, output_path: Path) -> None:
    cases = json.loads((ROOT / "evaluation" / "retrieval_eval_cases.json").read_text(encoding="utf-8"))["cases"]
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    retrieval = {row["case_id"]: row for row in comparison["cases"]}
    parser = PdfDocumentParser(chunk_size=520, chunk_overlap=80)
    cache: dict[str, tuple[list, list[str]]] = {}
    lines = [
        "# Iteration 03：8 题证据人工复核包",
        "",
        "> 所有下列文字均来自仓库已保存 PDF 和离线检索候选。当前标签均为自动候选、**待人工确认**；本包不是 gold，也不证明结论语义正确。此对照运行前未用留出组调查询或 RRF 参数；本轮已经查看留出结果并据此撰写配置建议，后续不得再把它称为完全未见测试集。",
        "",
        "页码分开记录：PDF 实际页序号按从 1 开始；印刷页码只用可提取页脚规则尝试识别，无法读取时标明待核对。不同方法的同页片段仅供人工判断，不因页码一致自动视为目标原文已召回。",
        "",
    ]
    for case in cases:
        company = case["company_id"]
        pdf = ROOT / "data" / "real_cases" / company / "sources" / "annual_report.pdf"
        source = DocumentSource(task_id=f"review-{company}", document_id=company, file_name="annual_report.pdf", content_type="application/pdf", local_path=str(pdf))
        if company not in cache:
            doc = await parser.load(source)
            chunks = await parser.split(doc)
            with pymupdf.open(pdf) as original_pdf:
                source_pages = [page.get_text("text", sort=True) for page in original_pdf]
            cache[company] = (chunks, source_pages)
        chunks, source_pages = cache[company]
        row = retrieval.get(case["case_id"], {})
        lines += [f"## {case['case_id']} · {company} · {case['indicator']}", "", f"- 问题：{case['question']}", f"- 指标：`{case['domain']}.{case['indicator']}`；语言：{case['language']}；分组：{case['split']}", f"- 源文件：`data/real_cases/{company}/sources/annual_report.pdf`；SHA-256：`{hashlib.sha256(pdf.read_bytes()).hexdigest()}`", f"- 原标注状态：`{case['label_status']}`；来源：`evaluation/retrieval_eval_cases.json` 候选标注；人工确认：**待确认**。", f"- 待确认原因：{case['pending_reason']}", f"- 是否需要多个片段共同支持：{'是' if case['needs_multiple_chunks'] else '当前标注认为否；人工可更正'}", ""]
        for index, support in enumerate(case["candidate_supports"], start=1):
            page_number = int(support["page_number"])
            page_text = source_pages[page_number - 1] if page_number <= len(source_pages) else ""
            quote = support["excerpt"]
            compact_page, compact_quote = norm(page_text), norm(quote)
            exact_on_page = bool(compact_quote and compact_quote in compact_page)
            found_chunk = next((chunk for chunk in chunks if chunk.page_number == page_number and compact_quote in norm(chunk.text)), None)
            if exact_on_page:
                excerpt = source_context(page_text, quote)
            else:
                excerpt = page_text[:420].strip().replace("\n", " ")
            lines += [f"### 候选片段 {index}（分组 `{support.get('group', 'default')}`）", "", f"- PDF 实际页序号：**{page_number}**；可识别的印刷页码：**{printed_page(page_text)}**", f"- 候选原文（匹配规则：NFKC/大小写折叠/移除空白；命中源页：{'是' if exact_on_page else '否'}）：", f"  > {quote}", f"- 必要上下文（空白归一后的源页片段，约前后各 170 字）：", f"  > {excerpt if excerpt else '未提取到上下文'}", f"- 与当前解析 chunk 的关系：{('完整原文包含于 chunk `'+found_chunk.chunk_id+'`。') if found_chunk else '未找到完整包含目标原文的同页 chunk；需复查解析/切分。'}", f"- 机器初审关系：**支持候选 / 待人工确认**。初审理由：原句可在 PDF 页文本定位；事实范围、主体、时间、单位和是否足以支持该指标尚未人工核定。", "- 人工复核关系（选一）：`[ ] 支持`　`[ ] 部分支持`　`[ ] 矛盾`　`[ ] 不足`。", ""]
        # Show other methods' same-page candidates so human reviewers can judge equivalent support.
        for method, label in (("A", "原向量+原 query"), ("B", "原向量+通用双语 query"), ("C", "BM25+通用双语 query"), ("D", "RRF+通用双语 query")):
            method_candidates = []
            for support in case["candidate_supports"]:
                page_no = int(support["page_number"])
                method_candidates.extend(item for item in row.get("ranked_top_10", {}).get(method, []) if item.get("page_number") == page_no)
            unique = {item.get("chunk_id"): item for item in method_candidates}
            display = list(unique.values())[:2]
            if display:
                lines.append(f"- {label} 的候选片段：" + "；".join(f"`{it['chunk_id']}` p{it['page_number']}：{it['text'][:220].replace(chr(10), ' ')}" for it in display))
            else:
                lines.append(f"- {label}：top-10 未返回标注候选所在页的片段。该结论不等于原文不存在。")
        lines += ["", "- 其他等价证据/矛盾证据：**未穷尽；需复核年报相邻页和其他页面。**", "- 建议标签：`支持`（仅为自动初审建议，不能当作人工标注）；人工标签：`待确认`；待确认说明：核对上下文、事实主体与指标相关性，必要时增加共同支持片段。", ""]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "review" / "iteration_03_evidence_review.md")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"review pack already exists: {args.output}")
    asyncio.run(create(args.comparison, args.output))
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
