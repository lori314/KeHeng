"""Conservative deterministic normalization used only for assertion grouping."""

from __future__ import annotations

import re
import unicodedata


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" \t\r\n.,，。;；:：!?！？、()（）[]【】{}《》<>\"'‘’“”")


_GENERIC_COMPANY_SUBJECTS = frozenset({"公司", "本公司", "该公司", "企业", "本企业", "该企业"})


def normalize_subject(
    subject: str | None,
    canonical_name: str,
    aliases: list[str] | tuple[str, ...],
) -> str:
    """Canonicalize only exact, previously validated company identity strings."""
    normalized = normalize_text(subject)
    identity_names = {normalize_text(canonical_name)}
    identity_names.update(normalize_text(alias) for alias in aliases)
    identity_names.discard("")
    if normalized in identity_names or normalized in _GENERIC_COMPANY_SUBJECTS:
        return "__company__"
    return normalized


def normalize_period(value: str | None) -> str:
    """Canonicalize a small set of explicit year/half-year/quarter forms.

    Unrecognized non-empty values are returned as normalized raw text; no year
    or period is inferred from phrases such as “报告期内” or “近年来”.
    """
    raw = normalize_text(value)
    if not raw:
        return ""
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw).casefold())
    match = re.fullmatch(r"(?:fy)?((?:19|20)\d{2})(?:年)?(?:度)?", compact)
    if match:
        return match.group(1)
    match = re.fullmatch(r"((?:19|20)\d{2})(?:年)?(?:h([12])|([12])半年|上半年|下半年)", compact)
    if match:
        half = match.group(2) or match.group(3)
        if not half:
            half = "1" if compact.endswith("上半年") else "2"
        return f"{match.group(1)}-H{half}"
    match = re.fullmatch(r"((?:19|20)\d{2})(?:年)?q([1-4])", compact)
    if match:
        return f"{match.group(1)}-Q{match.group(2)}"
    match = re.fullmatch(r"((?:19|20)\d{2})(?:年)?(?:第)?([一二三四1234])季度", compact)
    if match:
        quarter = {"一": "1", "二": "2", "三": "3", "四": "4"}.get(match.group(2), match.group(2))
        return f"{match.group(1)}-Q{quarter}"
    return raw


def ngram_jaccard(left: str, right: str, n: int = 3) -> float:
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    def grams(value: str) -> set[str]:
        compact = value.replace(" ", "")
        if len(compact) < n:
            return {compact}
        return {compact[index:index + n] for index in range(len(compact) - n + 1)}
    a, b = grams(left), grams(right)
    return len(a & b) / len(a | b) if a or b else 1.0


_CLAIM_PUNCTUATION = re.compile(r"[\s\.,，。;；:：!?！？、()（）\[\]【】{}《》<>\"'‘’“”]+")
_NUMBER_TOKEN = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?(?:%|％)?")
_MODEL_ANCHOR = re.compile(r"[A-Za-z]+[-_]?\d+[A-Za-z0-9._-]*")


def normalize_claim_text(value: str | None) -> str:
    """Normalize claim punctuation to spaces without applying synonyms."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = _CLAIM_PUNCTUATION.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def claim_tokens(value: str | None) -> tuple[frozenset[str], frozenset[str]]:
    text = unicodedata.normalize("NFKC", value or "")
    anchors_found = list(_MODEL_ANCHOR.finditer(text))
    anchors = frozenset(match.group(0).casefold() for match in anchors_found)
    without_anchors = list(text)
    for match in anchors_found:
        without_anchors[match.start():match.end()] = " " * (match.end() - match.start())
    numbers = frozenset(token.casefold() for token in _NUMBER_TOKEN.findall("".join(without_anchors)))
    return numbers, anchors


def claim_equivalent(
    left_object: str,
    right_object: str,
    left_predicate: str,
    right_predicate: str,
    *,
    threshold: float = 0.72,
    industry_or_core_business: bool = False,
    financing_without_period: bool = False,
) -> tuple[str | None, float, bool, bool]:
    """Conservative claim comparison with numeric and product-anchor guards.

    Returns (match mode, object similarity, numeric guard rejected,
    model-anchor guard rejected). A match mode is exact, containment, or
    fuzzy_text; empty mode means the facts stay separate.
    """
    left = normalize_claim_text(left_object)
    right = normalize_claim_text(right_object)
    if not left or not right:
        return None, 0.0, False, False
    left_numbers, left_models = claim_tokens(left_object)
    right_numbers, right_models = claim_tokens(right_object)
    if left_numbers and right_numbers and left_numbers != right_numbers:
        return None, ngram_jaccard(left, right), True, False
    if left_models and right_models and left_models != right_models:
        return None, ngram_jaccard(left, right), False, True
    if left == right:
        return "exact", 1.0, False, False

    similarity = ngram_jaccard(left, right)
    if financing_without_period:
        # Unknown-period financing may deduplicate virtually identical text only.
        if similarity >= 0.95 and normalize_claim_text(left_predicate) == normalize_claim_text(right_predicate):
            return "fuzzy_text", similarity, False, False
        return None, similarity, False, False
    compact_left, compact_right = left.replace(" ", ""), right.replace(" ", "")
    shorter, longer = sorted((compact_left, compact_right), key=len)
    ratio = len(shorter) / len(longer) if longer else 0.0
    containment_threshold = 0.75 if industry_or_core_business else 0.60
    if len(shorter) >= 8 and shorter in longer and ratio >= containment_threshold:
        return "containment", similarity, False, False

    if industry_or_core_business:
        return ("fuzzy_text", similarity, False, False) if similarity >= 0.85 else (None, similarity, False, False)
    if similarity >= threshold:
        return "fuzzy_text", similarity, False, False
    if similarity >= 0.65:
        left_predicate_norm = normalize_claim_text(left_predicate)
        right_predicate_norm = normalize_claim_text(right_predicate)
        predicate_similarity = ngram_jaccard(left_predicate_norm, right_predicate_norm)
        if left_predicate_norm == right_predicate_norm or predicate_similarity >= 0.70:
            return "fuzzy_text", similarity, False, False
    return None, similarity, False, False


_QUALITY_RANK = {
    "authoritative_public_record": 0,
    "first_party": 1,
    "academic_or_patent": 2,
    "third_party": 3,
    "weak_web": 4,
    "snippet_only": 5,
}


def quality_rank(category: str) -> int:
    return _QUALITY_RANK.get(category, len(_QUALITY_RANK))


_CURRENCY = {"cny": "CNY", "rmb": "CNY", "人民币": "CNY", "元": "CNY", "¥": "CNY"}
_UNIT = {
    "元": ("CNY", 1.0), "人民币元": ("CNY", 1.0), "yuan": ("CNY", 1.0),
    "万元": ("CNY", 10000.0), "万人民币": ("CNY", 10000.0),
    "亿元": ("CNY", 100000000.0), "亿人民币": ("CNY", 100000000.0),
}


def normalize_financial_amount(value: float, unit: str | None, currency: str | None) -> tuple[str, float | str, str] | None:
    """Return comparable amount; unknown units/currencies retain raw identity and fail closed."""
    unit_norm = normalize_text(unit)
    currency_norm = _CURRENCY.get(normalize_text(currency), normalize_text(currency).upper()) if currency else ""
    if unit_norm in _UNIT:
        _, multiplier = _UNIT[unit_norm]
        if currency_norm and currency_norm not in {"CNY", ""}:
            return (f"raw:{unit_norm}", value, f"raw:{currency_norm}")
        return ("CNY", round(value * multiplier, 8), "CNY")
    if unit_norm in {"%", "percent", "percentage", "百分比", "百分数"}:
        return ("ratio", round(value / 100.0, 8), "fraction")
    if unit_norm in {"ratio", "比例", "比率", "fraction", "decimal", "小数"}:
        return ("ratio", round(value, 8), "fraction")
    if unit_norm:
        return (f"raw:{unit_norm}", round(value, 8), f"raw:{currency_norm}")
    if currency_norm:
        return (f"raw-currency:{currency_norm}", round(value, 8), f"raw:{currency_norm}")
    return None


def ratio_equivalent(left_unit: str | None, left: float, right_unit: str | None, right: float) -> bool:
    left_u, right_u = normalize_text(left_unit), normalize_text(right_unit)
    ratio_units = {"ratio", "比例", "比率", "fraction", "decimal", "小数"}
    percent_units = {"%", "percent", "percentage", "百分比", "百分数"}
    if left_u in ratio_units and right_u in percent_units:
        return abs(left * 100.0 - right) <= 1e-8
    if right_u in ratio_units and left_u in percent_units:
        return abs(right * 100.0 - left) <= 1e-8
    return False
