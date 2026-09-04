import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional


_QUALITY_PREFIX_RE = re.compile(
    r"^\s*(?:\[(?:FHD|FHDC|HD|UHD|4K)\]\s*)+",
    re.IGNORECASE,
)
_CODE_PATTERNS = (
    (
        "fc2",
        "high",
        re.compile(
            r"^(FC2[-_ ]?(?:PPV[-_ ]?)?\d{5,9})(?=\s|$|[^A-Z0-9])",
            re.IGNORECASE,
        ),
    ),
    (
        "date_source",
        "medium",
        re.compile(
            r"^(\d{6}[_-]\d{1,3}(?:[-_][A-Z0-9]+)+)(?=\s|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "numeric_site_prefix",
        "medium",
        re.compile(
            r"^\d{3}([A-Z]+)[-_](\d+)(?=\s|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "hyphen",
        "medium",
        re.compile(
            r"^([A-Z0-9]{1,15}(?:[-_][A-Z0-9]{1,15})+)(?=\s|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "compact",
        "high",
        re.compile(r"^([A-Z]{1,15}\d{2,9})(?=\s|$)", re.IGNORECASE),
    ),
)


@dataclass(frozen=True)
class JavbeeCodeResolution:
    code: Optional[str]
    source: Optional[str]
    confidence: str
    rule: str
    title_kind: str


def normalize_text(value) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", html.unescape(str(value or "")))
        .replace("\u00a0", " ")
        .split()
    )


def normalize_code_key(value) -> Optional[str]:
    normalized = re.sub(r"[^A-Z0-9]", "", normalize_text(value).upper())
    return normalized or None


def resolve_javbee_code(existing_code, title) -> JavbeeCodeResolution:
    """保留已有 code；缺失时从标题开头提取带置信度的候选番号。"""
    normalized_title = normalize_text(title)
    title_without_quality = _QUALITY_PREFIX_RE.sub("", normalized_title)

    existing = str(existing_code or "").strip()
    if existing:
        return JavbeeCodeResolution(
            code=existing,
            source="legacy_mysql",
            confidence="high",
            rule="existing",
            title_kind=_title_kind(title_without_quality, existing),
        )

    had_quality_prefix = title_without_quality != normalized_title
    for rule, confidence, pattern in _CODE_PATTERNS:
        match = pattern.search(title_without_quality)
        if not match:
            continue

        if rule == "numeric_site_prefix":
            candidate = f"{match.group(1)}-{match.group(2)}"
        else:
            candidate = match.group(1)
        candidate = candidate.upper().replace("_", "-").replace(" ", "-")

        if rule == "hyphen" and had_quality_prefix:
            confidence = "high"
        return JavbeeCodeResolution(
            code=candidate,
            source="title",
            confidence=confidence,
            rule=rule,
            title_kind=_title_kind(title_without_quality, match.group(0)),
        )

    return JavbeeCodeResolution(
        code=None,
        source=None,
        confidence="unknown",
        rule="unresolved",
        title_kind="descriptive" if normalized_title else "missing",
    )


def _title_kind(title_without_quality: str, matched_code: str) -> str:
    remainder = title_without_quality[len(matched_code):].strip()
    return "catalog_only" if not remainder else "descriptive"
