"""Business domain names for writing tasks, not evidence or admission authority.

Existing UI choices plus the classifier's distinct inspection domain are
catalogued. The old mixed name "纪检/巡察" is deliberately NOT an alias of
either domain: unknown/legacy request and history values preserve their meaning.
Document classification rules and stored library metadata remain unchanged.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class BusinessDomain:
    value: str
    display_name: str
    aliases: tuple[str, ...] = ()


BUSINESS_DOMAINS = (
    BusinessDomain("党建", "党建"),
    BusinessDomain("纪检 / 廉政", "纪检 / 廉政"),
    BusinessDomain("巡察整改", "巡察整改"),
    BusinessDomain("意识形态", "意识形态"),
    BusinessDomain("基层治理", "基层治理"),
    BusinessDomain("乡村振兴", "乡村振兴"),
    BusinessDomain("安全生产", "安全生产"),
    BusinessDomain("营商环境", "营商环境"),
    BusinessDomain("生态环保", "生态环保"),
    BusinessDomain("教育医疗", "教育医疗"),
    BusinessDomain("经济发展", "经济发展"),
    BusinessDomain("作风建设", "作风建设"),
    BusinessDomain("办公室综合", "办公室综合"),
    BusinessDomain("其他", "其他"),
)


def _identity(value: str) -> str:
    # Same harmless whitespace/fullwidth-slash variants accepted by old Excel.
    return "".join(value.split()).replace("／", "/")


def _build_lookup(items: tuple[BusinessDomain, ...]) -> dict[str, str]:
    if not items:
        raise ValueError("Business domain catalog must not be empty")
    lookup = {}
    for item in items:
        if not item.value.strip() or not item.display_name.strip():
            raise ValueError("Business domain value/display_name must not be empty")
        for name in (item.value, *item.aliases):
            key = _identity(name)
            if not key or key in lookup:
                raise ValueError("Duplicate business domain identity: " + name)
            lookup[key] = item.value
    return lookup


_LOOKUP = _build_lookup(BUSINESS_DOMAINS)


def normalize_business_domain(value: str | None) -> str | None:
    """Resolve known names only; preserve None, empty and unknown input."""
    if value is None:
        return None
    return _LOOKUP.get(_identity(value), value)


def business_domain_catalog() -> dict:
    """Return a fresh, read-only API projection without mutable shared objects."""
    return {"items": [
        {"value": item.value, "display_name": item.display_name, "aliases": list(item.aliases)}
        for item in BUSINESS_DOMAINS
    ]}
