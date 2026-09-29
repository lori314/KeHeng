"""Deterministic SourceType classification, kept separate from source quality."""

from pathlib import Path
from urllib.parse import urlsplit

import yaml

from app.knowledge.contracts import Company, SourceType
from app.knowledge.identity import canonicalize_url
from app.research.contracts import SearchResult


class SourceTypeClassifier:
    def __init__(self, registry_path: str | Path | None = None):
        path = Path(registry_path) if registry_path else Path(__file__).resolve().parents[3] / "configs" / "research" / "source_host_registry.yaml"
        with path.open(encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
        self.registry_version = config["registry_version"]
        self.regulatory_hosts = set(config["regulatory_hosts"])
        self.registry_hosts = set(config["registry_hosts"])
        self.patent_hosts = set(config["patent_hosts"])
        self.paper_hosts = set(config["paper_hosts"])

    def classify(self, result: SearchResult, company: Company | None = None) -> SourceType:
        host = (urlsplit(canonicalize_url(result.url)).hostname or "").lower()
        if company and _same_or_subdomain(host, _host(company.official_website)):
            return SourceType.COMPANY_OFFICIAL
        if _matches(host, self.registry_hosts):
            return SourceType.REGISTRY
        if _matches(host, self.regulatory_hosts):
            return SourceType.REGULATORY
        if _matches(host, self.patent_hosts):
            return SourceType.PATENT
        if _matches(host, self.paper_hosts) or host == "doi.org" or host.endswith(".doi.org"):
            return SourceType.PAPER
        if result.topic == "news":
            return SourceType.NEWS
        if host.endswith(".gov.cn") or host.endswith(".gov"):
            return SourceType.GOVERNMENT
        return SourceType.WEB


def _host(url: str | None) -> str:
    if not url:
        return ""
    try:
        return (urlsplit(canonicalize_url(url)).hostname or "").lower()
    except ValueError:
        return ""


def _matches(host: str, registered: set[str]) -> bool:
    return any(_same_or_subdomain(host, root) for root in registered)


def _same_or_subdomain(host: str, root: str) -> bool:
    return bool(root and (host == root or host.endswith("." + root)))
