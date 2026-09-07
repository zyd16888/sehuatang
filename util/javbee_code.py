import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote


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


# x1080x 标题番号在括号里，如「(杏吧傳媒)(xb-5441)(20260828)标题」；
# 要求字母开头 + 数字结尾，纯数字括号（日期）与纯文字括号（厂牌）不会命中。
_BRACKET_CODE_RE = re.compile(
    r"[（(]\s*([A-Z]{2,15}[-_ ]?\d{2,9})\s*[）)]",
    re.IGNORECASE,
)
_MAGNET_DN_RE = re.compile(r"[?&]dn=([^&\s]+)", re.IGNORECASE)
_DN_CODE_RE = re.compile(r"^([A-Z]{2,15})[-_]?(\d{2,9})$", re.IGNORECASE)


def _format_code(letters: str, digits: str) -> str:
    return f"{letters.upper()}-{digits}"


def resolve_x1080x_code(title, magnets=()) -> JavbeeCodeResolution:
    """x1080x 番号识别：标题括号 → 磁链 dn 参数 → 复用 javbee 开头规则。"""
    normalized_title = normalize_text(title)
    match = _BRACKET_CODE_RE.search(normalized_title)
    if match:
        candidate = match.group(1)
        parts = _DN_CODE_RE.match(candidate.replace(" ", "-"))
        code = (
            _format_code(parts.group(1), parts.group(2))
            if parts
            else candidate.upper().replace("_", "-").replace(" ", "-")
        )
        return JavbeeCodeResolution(
            code=code,
            source="title",
            confidence="high",
            rule="bracket",
            title_kind="descriptive",
        )

    for magnet in magnets or ():
        dn_match = _MAGNET_DN_RE.search(str(magnet or ""))
        if not dn_match:
            continue
        dn_value = unquote(dn_match.group(1)).strip()
        parts = _DN_CODE_RE.match(dn_value)
        if parts:
            return JavbeeCodeResolution(
                code=_format_code(parts.group(1), parts.group(2)),
                source="magnet_dn",
                confidence="high",
                rule="magnet_dn",
                title_kind="descriptive" if normalized_title else "missing",
            )

    return resolve_javbee_code(None, title)
