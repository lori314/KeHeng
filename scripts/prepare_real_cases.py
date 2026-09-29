"""Download six openly published company reports and write provenance manifests.

The URLs are official company, exchange or statutory disclosure pages. The
script is intentionally explicit: a failed download is recorded in the
manifest and never replaced with synthetic content.
"""

from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "data" / "real_cases"

CASES = {
    "cambricon": {
        "enterprise_name": "中科寒武纪科技股份有限公司",
        "industry": "半导体 / AI 芯片",
        "report_title": "2024年年度报告",
        "published_at": "2025-04-19",
        "source_url": "https://static.cninfo.com.cn/finalpage/2025-04-19/1223155683.PDF",
        "source_organization": "巨潮资讯网（法定信息披露平台）",
    },
    "smic": {
        "enterprise_name": "中芯国际集成电路制造有限公司",
        "industry": "半导体 / 晶圆制造",
        "report_title": "2024 Annual Report",
        "published_at": "2025-03-27",
        "source_url": "https://www.smics.com/uploads/67f6423d/e00981.pdf",
        "source_organization": "中芯国际官方网站",
    },
    "siasun": {
        "enterprise_name": "沈阳新松机器人自动化股份有限公司",
        "industry": "机器人 / 智能制造",
        "report_title": "2024年年度报告",
        "published_at": "2025-04-18",
        "source_url": "https://static.cninfo.com.cn/finalpage/2025-04-19/1223152070.PDF",
        "source_organization": "巨潮资讯网（新松法定信息披露）",
    },
    "estun": {
        "enterprise_name": "南京埃斯顿自动化股份有限公司",
        "industry": "机器人 / 工业自动化",
        "report_title": "2024年年度报告",
        "published_at": "2025-04-29",
        "source_url": "https://static.cninfo.com.cn/finalpage/2025-04-29/1223370517.PDF",
        "source_organization": "巨潮资讯网（埃斯顿法定信息披露）",
    },
    "catl": {
        "enterprise_name": "宁德时代新能源科技股份有限公司",
        "industry": "新能源 / 动力电池",
        "report_title": "2024年年度报告",
        "published_at": "2025-03-17",
        "source_url": "https://www.catl.com/uploads/1/file/public/202503/20250317094543_6ig9e0mwng.pdf",
        "source_organization": "宁德时代官方网站",
    },
    "nio": {
        "enterprise_name": "蔚来集团（NIO Inc.）",
        "industry": "新能源 / 智能电动汽车",
        "report_title": "2024 Form 20-F Annual Report",
        "published_at": "2025-04-08",
        "source_url": "https://www.hkexnews.hk/listedco/listconews/sehk/2025/0409/2025040900011.pdf",
        "source_organization": "香港交易所披露易（NIO 年报）",
    },
}


def download(url: str, destination: Path) -> tuple[str | None, str | None]:
    try:
        request = Request(url, headers={"User-Agent": "KeHeng-research/0.9"})
        with urlopen(request, timeout=60) as response:  # noqa: S310
            content = response.read()
        if not content.startswith(b"%PDF"):
            return None, "response did not start with PDF signature"
        destination.write_bytes(content)
        return hashlib.sha256(content).hexdigest(), None
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def main() -> int:
    fetched_at = date.today().isoformat()
    failures = 0
    for company_id, case in CASES.items():
        case_root = OUTPUT_ROOT / company_id
        sources = case_root / "sources"
        sources.mkdir(parents=True, exist_ok=True)
        pdf_path = sources / "annual_report.pdf"
        sha256, error = download(case["source_url"], pdf_path)
        status = "downloaded" if sha256 else "missing"
        if error:
            failures += 1
        manifest = {
            "schema_version": "0.9.0",
            "company_id": company_id,
            "enterprise_name": case["enterprise_name"],
            "industry": case["industry"],
            "documents": [
                {
                    "file_name": "sources/annual_report.pdf",
                    "source": case["source_url"],
                    "source_organization": case["source_organization"],
                    "published_at": case["published_at"],
                    "retrieved_at": fetched_at,
                    "file_type": "PDF",
                    "purpose": "Technology/Industry Agent 公开资料案例研究",
                    "status": status,
                    "sha256": sha256,
                    "error": error,
                }
            ],
        }
        (case_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        readme = (
            f"# {case['enterprise_name']}\n\n"
            f"- 行业：{case['industry']}\n"
            f"- 资料：{case['report_title']}\n"
            f"- 来源：{case['source_url']}\n"
            f"- 获取时间：{fetched_at}\n\n"
            "本案例用于验证系统面对公开复杂企业资料时的可运行性、证据链和人工复核接口。"
            "系统输出不代表企业真实融资价值或投资建议。若 PDF 获取失败，详见 manifest.json，"
            "不得用合成资料替代。\n"
        )
        (case_root / "README.md").write_text(readme, encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
