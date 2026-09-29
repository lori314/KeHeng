"""Generate the public, fully fictional v0.2 technology PDF fixture."""

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
OUTPUT = ROOT / "data" / "examples" / "public_test_company_technology_profile.pdf"


def register_chinese_fonts() -> tuple[str, str]:
    candidates = (
        (
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/msyhbd.ttc"),
        ),
        (
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        ),
    )
    for regular_path, bold_path in candidates:
        if regular_path.exists() and bold_path.exists():
            pdfmetrics.registerFont(TTFont("KeHengCN", str(regular_path)))
            pdfmetrics.registerFont(TTFont("KeHengCNBold", str(bold_path)))
            return "KeHengCN", "KeHengCNBold"
    raise RuntimeError("No supported Chinese font was found for PDF generation")


def build_pdf() -> None:
    regular, bold = register_chinese_fonts()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleCN",
        parent=styles["Title"],
        fontName=bold,
        fontSize=23,
        leading=32,
        textColor=colors.HexColor("#173F32"),
        spaceAfter=14,
    )
    subtitle = ParagraphStyle(
        "SubtitleCN",
        parent=styles["Normal"],
        fontName=regular,
        fontSize=10,
        leading=16,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#65736D"),
        spaceAfter=22,
    )
    heading = ParagraphStyle(
        "HeadingCN",
        parent=styles["Heading2"],
        fontName=bold,
        fontSize=15,
        leading=22,
        textColor=colors.HexColor("#21654D"),
        spaceBefore=12,
        spaceAfter=9,
    )
    body = ParagraphStyle(
        "BodyCN",
        parent=styles["BodyText"],
        fontName=regular,
        fontSize=10.5,
        leading=19,
        textColor=colors.HexColor("#24352F"),
        spaceAfter=9,
    )
    note = ParagraphStyle(
        "NoteCN",
        parent=body,
        fontSize=9,
        leading=15,
        textColor=colors.HexColor("#725E30"),
        backColor=colors.HexColor("#F6F0DE"),
        borderPadding=8,
    )

    def page_decorator(canvas, document) -> None:
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(colors.HexColor("#D5DDD8"))
        canvas.line(22 * mm, height - 18 * mm, width - 22 * mm, height - 18 * mm)
        canvas.setFont(regular, 8)
        canvas.setFillColor(colors.HexColor("#73817A"))
        canvas.drawString(22 * mm, height - 14 * mm, "KEHENG PUBLIC TEST MATERIAL")
        canvas.drawRightString(width - 22 * mm, 14 * mm, f"第 {document.page} 页")
        canvas.setFillColor(colors.HexColor("#D5DDD8"))
        canvas.setFont(bold, 34)
        canvas.translate(width / 2, height / 2)
        canvas.rotate(35)
        canvas.drawCentredString(0, 0, "完全虚构 - 公开测试")
        canvas.restoreState()

    document = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=24 * mm,
        leftMargin=24 * mm,
        topMargin=27 * mm,
        bottomMargin=22 * mm,
        title="启衡智造技术资料（公开测试）",
        author="KeHeng Project",
    )

    story = [
        Paragraph("启衡智造技术有限公司", title),
        Paragraph("技术资料摘要 - 公开测试企业（完全虚构）", subtitle),
        Paragraph(
            "重要声明：本文档为 KeHeng v0.2 软件测试而创建。企业、产品、人员、专利、客户和测试数据均为虚构，不对应任何真实主体。",
            note,
        ),
        Spacer(1, 8 * mm),
        Paragraph("1. 企业与技术概况", heading),
        Paragraph(
            "启衡智造技术有限公司是一家完全虚构的工业人工智能测试企业，研发方向为制造业表面缺陷检测。公司技术团队共 18 人，其中算法与软件研发人员 12 人。",
            body,
        ),
        Paragraph(
            "核心产品 EdgeVision-X 是一套工业视觉缺陷检测原型平台。材料称平台采用自主研发的轻量化缺陷检测算法，包含数据标注、模型训练、边缘推理和结果追溯模块，可部署于普通工业计算机。",
            body,
        ),
        Paragraph("2. 研发与知识产权", heading),
        Paragraph(
            "研发团队形成了小样本缺陷增强、边缘模型压缩和异常样本回流三项技术方案。企业已提交 3 项发明专利申请，其中 1 项进入实质审查阶段；截至本文档生成时，相关专利均未获得授权。",
            body,
        ),
        Paragraph(
            "现有资料仅包含企业自述和内部测试摘要，未提供源代码审计、专利权利要求对比或独立第三方技术鉴定。",
            body,
        ),
        PageBreak(),
        Paragraph("3. 原型与试点验证", heading),
        Paragraph(
            "2025 年第四季度，EdgeVision-X 原型系统在两家虚构制造企业的两条生产线上开展试点。试点材料记录了连续 12 周运行情况，覆盖金属表面划痕、凹点和污染三类缺陷。",
            body,
        ),
        Table(
            [
                ["测试项目", "企业内部记录", "证据限制"],
                ["缺陷召回率", "95.2%", "仅针对企业提供的固定样本集"],
                ["单图推理时间", "35 ms", "工业计算机与固定相机条件"],
                ["连续运行时间", "12 周", "仅两条同类生产线"],
            ],
            colWidths=[38 * mm, 48 * mm, 68 * mm],
            repeatRows=1,
            style=TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), bold),
                    ("FONTNAME", (0, 1), (-1, -1), regular),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("LEADING", (0, 0), (-1, -1), 14),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173F32")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B8C5BE")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            ),
        ),
        Spacer(1, 7 * mm),
        Paragraph("4. 工程化状态", heading),
        Paragraph(
            "原型已支持模型版本记录、缺陷图片回溯和离线更新，但尚未完成多工厂规模化复制，也未形成标准化交付工具链。试点结果未经独立第三方复测，不能直接推断其他行业、材料或光照条件下的表现。",
            body,
        ),
        Paragraph(
            "团队计划补充不同工件、相机和光源条件下的验证，并建立上线后的模型漂移监控。该计划属于未来安排，不代表相关能力已经完成。",
            body,
        ),
        PageBreak(),
        Paragraph("5. 已识别的技术风险", heading),
        Paragraph(
            "验证范围风险：当前样本和试点范围有限，仅覆盖两条同类生产线。缺陷召回率由企业内部记录，尚未经过第三方验证，规模化效果待验证。",
            body,
        ),
        Paragraph(
            "外部依赖风险：系统效果依赖第三方工业相机、光源和传感器的一致性。环境变化可能导致图像分布偏移，现有材料未给出完整的自动校准方案。",
            body,
        ),
        Paragraph(
            "知识产权风险：3 项发明专利均为专利申请，其中 1 项处于实质审查，均未获得授权。现阶段不能据此判断权利稳定性或自由实施空间。",
            body,
        ),
        Paragraph(
            "工程治理风险：原型尚未提供独立安全测评、生产环境高可用报告和完整灾难恢复演练记录。关键研发人员集中度及持续维护能力需要进一步核验。",
            body,
        ),
        Paragraph("6. 建议补充材料", heading),
        Paragraph(
            "建议后续提供：原始测试集与测试脚本、第三方复测报告、专利检索与权利要求分析、不同硬件环境兼容性记录、模型漂移监控方案，以及规模化部署的资源与成本数据。",
            body,
        ),
        Spacer(1, 10 * mm),
        Paragraph(
            "本资料仅用于软件测试。任何自动分析结果都必须由具备相应专业能力的人员复核。",
            note,
        ),
    ]
    document.build(story, onFirstPage=page_decorator, onLaterPages=page_decorator)


if __name__ == "__main__":
    build_pdf()
    print(f"Generated: {OUTPUT}")
