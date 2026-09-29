"""Generate the three synthetic v0.8 composite evaluation PDFs."""

from __future__ import annotations

from pathlib import Path

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
CASE_ROOT = ROOT / "data" / "evaluation_cases" / "composite"
FONT_NAME = "china-s"


CASES = {
    "case_tech_industry_high": {
        "title": "综合案例 A：技术强 + 产业强",
        "pages": [
            [
                "企业概况",
                "卓越智检科技有限公司面向制造业表面缺陷检测场景，资料用于 KeHeng v0.8 综合评价实验，企业与数据均为合成案例。",
                "技术事实",
                "公司采用自主研发的轻量化缺陷检测算法，形成小样本增强、模型压缩、数据标注、模型训练、边缘推理和结果追溯等技术方案。研发团队包含算法与软件研发人员。",
                "知识产权与成熟度",
                "企业已获得授权专利一项，并已完成多工厂规模化部署和批量交付，系统进入正式商业运营。",
            ],
            [
                "产业事实",
                "公司已与三家制造业客户签约，取得批量订单并完成首批交付，资料列出明确的工业视觉应用场景。",
                "行业趋势与竞争",
                "行业市场保持高速增长，资料引用市场规模增长率和持续需求扩张信息。企业在细分市场拥有可核验的市场份额，并形成差异化竞争优势和技术壁垒。",
                "政策环境",
                "相关产业规划提供政策支持和专项资金，资料同时提示企业仍需核验政策适用条件。",
            ],
        ],
    },
    "case_tech_high_industry_low": {
        "title": "综合案例 B：技术强 + 产业弱",
        "pages": [
            [
                "企业概况",
                "专注型算法科技有限公司的材料用于综合评价实验，企业与数据均为合成案例。",
                "技术事实",
                "企业自主研发核心算法，形成技术方案、数据标注、模型训练、边缘推理和结果追溯能力，研发团队持续投入。",
                "知识产权与成熟度",
                "企业已获得授权专利，产品已进入规模化部署并完成批量交付。",
            ],
            [
                "产业事实",
                "资料明确说明当前市场需求有限，企业尚无客户、没有订单，尚未形成销售和商业化收入。",
                "行业竞争",
                "所在细分行业需求下滑、规模萎缩。行业竞争激烈且对手众多。企业市场份额仍待验证。",
                "政策环境",
                "资料仅提示监管不确定和合规压力，未提供明确的政策支持或产业补贴。",
            ],
        ],
    },
    "case_tech_low_industry_high": {
        "title": "综合案例 C：技术弱 + 产业强",
        "pages": [
            [
                "企业概况",
                "成长赛道服务有限公司的材料用于综合评价实验，企业与数据均为合成案例。",
                "技术事实",
                "企业当前依赖第三方组件，技术方案仍处于早期概念阶段，尚未完成验证。材料未提供专利权属材料或独立技术鉴定。",
            ],
            [
                "产业事实",
                "企业已与多家客户签约并取得订单，明确覆盖快速扩张的应用场景。",
                "行业趋势与政策",
                "资料显示所在行业高速增长，市场规模增长率较高，企业在细分市场拥有市场份额并形成差异化竞争优势。",
                "政策支持",
                "国家产业规划提供政策支持、专项资金和补贴，企业需进一步核验具体适用条件。",
            ],
        ],
    },
}


def build_pdf(path: Path, title: str, pages: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    page_width, page_height = pymupdf.paper_size("a4")
    for page_index, sections in enumerate(pages):
        page = document.new_page(width=page_width, height=page_height)
        page.insert_textbox(
            pymupdf.Rect(45, 38, page_width - 45, 72),
            title,
            fontname=FONT_NAME,
            fontsize=16,
            align=1,
        )
        page.insert_text(
            (48, 92),
            "KeHeng v0.8 综合评价实验样例（合成资料）",
            fontname=FONT_NAME,
            fontsize=10,
        )
        y = 132
        for index in range(0, len(sections), 2):
            page.insert_text(
                (48, y), sections[index], fontname=FONT_NAME, fontsize=13
            )
            y += 26
            for line in _wrap_text(sections[index + 1], 42):
                page.insert_text(
                    (48, y), line, fontname=FONT_NAME, fontsize=10.5
                )
                y += 19
            y += 13
        page.insert_text(
            (48, page_height - 38),
            f"第 {page_index + 1} 页 / 共 {len(pages)} 页",
            fontname=FONT_NAME,
            fontsize=9,
        )
    document.set_metadata({"title": title, "author": "KeHeng v0.8"})
    document.save(str(path))
    document.close()


def _wrap_text(text: str, width: int) -> list[str]:
    return [text[index : index + width] for index in range(0, len(text), width)] or [""]


def main() -> None:
    for case_id, config in CASES.items():
        build_pdf(CASE_ROOT / case_id / "company_profile.pdf", config["title"], config["pages"])


if __name__ == "__main__":
    main()
