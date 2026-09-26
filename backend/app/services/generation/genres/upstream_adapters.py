"""Single composition of the engineering-integrated pinned upstream genres."""
from .upstream_suite import build_suite_adapters

UPSTREAM_ADAPTERS = build_suite_adapters()
PRESENTATION_ADAPTER = next(adapter for adapter in UPSTREAM_ADAPTERS if adapter.genre_id == "report")
