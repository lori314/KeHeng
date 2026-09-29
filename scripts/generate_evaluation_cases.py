"""Generate the three synthetic, text-extractable v0.5.5 evaluation PDFs."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "data" / "evaluation_cases"
FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simsun.ttc"),
)


CASES = {
    "case_001": {
        "company": "卓越感知科技有限公司",
        "subtitle": "技术优势明显案例",
        "pages": [
            {
                "section": "一、核心技术与创新能力",
                "paragraphs": [
                    "案例标识：KH-EVAL-001。本资料完全合成，不对应真实企业。",
                    "公司自主研发工业视觉核心算法和边缘推理技术方案，已形成可独立部署的软件与硬件协同架构。核心模块由内部研发团队持续维护，关键算法不存在必须依赖第三方闭源服务的情形。",
                    "研发团队围绕小样本学习、模型压缩、数据标注和结果追溯开展持续创新，并完成边缘推理性能优化。内部对照测试记录显示，在本评测材料限定的数据集上，识别准确率和推理时延均达到既定验收目标。",
                ],
            },
            {
                "section": "二、知识产权",
                "paragraphs": [
                    "案例标识：KH-EVAL-001。以下权属信息仅用于验证规则，不构成真实权利证明。",
                    "公司围绕工业视觉缺陷识别方法获得授权专利，并已取得与边缘推理软件相关的软件著作权。材料说明授权专利由公司持有，覆盖核心算法的数据处理流程；正式尽调仍需核验证书、权属和有效状态。",
                ],
            },
            {
                "section": "三、技术成熟度",
                "paragraphs": [
                    "案例标识：KH-EVAL-001。本页部署记录为完全合成的评测事实。",
                    "系统已完成客户现场验证，并已进入规模化部署阶段。公司正在三个生产基地进行批量交付，设备在生产线上连续运行，形成了版本发布、故障跟踪和回归测试记录。",
                    "上述材料只用于说明技术验证和交付状态，不代表商业收益、授信能力或投资价值。",
                ],
            },
        ],
    },
    "case_002": {
        "company": "简页企业管理有限公司",
        "subtitle": "材料不足案例",
        "pages": [
            {
                "section": "一、现有材料清单",
                "paragraphs": [
                    "案例标识：KH-EVAL-002。本资料完全合成，不对应真实企业。",
                    "本次提交材料仅包含企业基本登记信息、办公地址、联系人、成立日期和人员数量。公司现有办公场地一处，已建立一般行政管理制度。",
                    "材料未提供可核验的产品原理说明、核心方案、自主权属记录、测试数据、客户验证记录或交付记录。因此，本资料只能用于登记核对，不能支持完整的技术价值评价。",
                ],
            }
        ],
    },
    "case_003": {
        "company": "前瞻装备研发有限公司",
        "subtitle": "否定与未来语义案例",
        "pages": [
            {
                "section": "一、研发状态说明",
                "paragraphs": [
                    "案例标识：KH-EVAL-003。本资料完全合成，不对应真实企业。",
                    "企业当前仅形成早期技术方案，产品正在研发，尚无定型成果。团队计划量产，并预计在后续阶段扩大交付，但该计划尚未规模化，也没有已完成商业部署的证明。",
                    "上述表述是研发计划和未来目标，不应被解释为已经完成量产、规模化部署、客户现场验证或批量交付。现有材料只能支持早期技术概念判断。",
                ],
            }
        ],
    },
}


def register_cjk_font() -> str:
    for path in FONT_CANDIDATES:
        if path.is_file():
            pdfmetrics.registerFont(TTFont("KeHengCJK", str(path), subfontIndex=0))
            return "KeHengCJK"
    raise FileNotFoundError("No supported CJK font found for PDF generation")


def page_footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D7DDD8"))
    canvas.line(24 * mm, 16 * mm, A4[0] - 24 * mm, 16 * mm)
    canvas.setFillColor(colors.HexColor("#66736C"))
    canvas.setFont("KeHengCJK", 8)
    canvas.drawString(24 * mm, 10 * mm, "KeHeng v0.5.5 合成评测资料")
    canvas.drawRightString(A4[0] - 24 * mm, 10 * mm, f"第 {document.page} 页")
    canvas.restoreState()


def build_case(case_id: str, payload: dict[str, object], font_name: str) -> Path:
    output_path = OUTPUT_ROOT / case_id / "company_profile.pdf"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=24 * mm,
        leftMargin=24 * mm,
        topMargin=23 * mm,
        bottomMargin=24 * mm,
        title=f"{payload['company']} - {payload['subtitle']}",
        author="KeHeng synthetic evaluation fixture",
        subject="KeHeng v0.5.5 pipeline evaluation",
    )
    base = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CaseTitle",
        parent=base["Title"],
        fontName=font_name,
        fontSize=22,
        leading=32,
        textColor=colors.HexColor("#173F32"),
        alignment=TA_CENTER,
        spaceAfter=8 * mm,
    )
    subtitle_style = ParagraphStyle(
        "CaseSubtitle",
        parent=base["Normal"],
        fontName=font_name,
        fontSize=10,
        leading=16,
        textColor=colors.HexColor("#8A7136"),
        alignment=TA_CENTER,
        spaceAfter=12 * mm,
    )
    heading_style = ParagraphStyle(
        "SectionHeading",
        parent=base["Heading2"],
        fontName=font_name,
        fontSize=15,
        leading=22,
        textColor=colors.HexColor("#24513F"),
        spaceAfter=6 * mm,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=base["BodyText"],
        fontName=font_name,
        fontSize=11,
        leading=21,
        textColor=colors.HexColor("#263B32"),
        firstLineIndent=22,
        spaceAfter=5 * mm,
    )
    note_style = ParagraphStyle(
        "Note",
        parent=base["BodyText"],
        fontName=font_name,
        fontSize=9,
        leading=15,
        textColor=colors.HexColor("#65736B"),
    )

    story = []
    pages = payload["pages"]
    assert isinstance(pages, list)
    for index, page in enumerate(pages):
        assert isinstance(page, dict)
        story.append(Paragraph(str(payload["company"]), title_style))
        story.append(Paragraph(f"{case_id.upper()} · {payload['subtitle']}", subtitle_style))
        story.append(Paragraph(str(page["section"]), heading_style))
        for paragraph in page["paragraphs"]:
            story.append(Paragraph(str(paragraph), body_style))
        story.append(Spacer(1, 7 * mm))
        notice = Table(
            [[Paragraph("使用边界", note_style), Paragraph("本资料仅用于本地工程评测，不构成真实企业陈述、融资建议或投资结论。", note_style)]],
            colWidths=[24 * mm, 116 * mm],
        )
        notice.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EDF2EE")),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#B6C4BA")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CDD7CF")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story.append(notice)
        if index < len(pages) - 1:
            story.append(PageBreak())

    document.build(story, onFirstPage=page_footer, onLaterPages=page_footer)
    return output_path


def main() -> None:
    font_name = register_cjk_font()
    outputs = [build_case(case_id, payload, font_name) for case_id, payload in CASES.items()]
    for output in outputs:
        print(f"Generated: {output}")


if __name__ == "__main__":
    main()
