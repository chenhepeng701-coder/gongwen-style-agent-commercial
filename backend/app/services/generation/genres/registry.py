"""Single genre identity/capability owner and explicit composition root.

Registration is trusted application configuration, never request data. Retrieval
for the existing non-full-draft route retains its prior low-level behavior;
formal full-draft requests must resolve a supported adapter and fail closed.
"""
from .contracts import GenreAdapter


class GenreResolutionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class GenreRegistry:
    def __init__(self):
        self._identities: dict[str, GenreAdapter] = {}

    def register(self, adapter: GenreAdapter) -> None:
        identities = tuple(dict.fromkeys((adapter.genre_id, adapter.document_type, *adapter.aliases)))
        if any(not value or value != value.strip() for value in identities):
            raise GenreResolutionError("GENRE_IDENTITY_INVALID")
        if any(value in self._identities for value in identities):
            raise GenreResolutionError("GENRE_IDENTITY_ALREADY_REGISTERED")
        self._identities.update(dict.fromkeys(identities, adapter))

    def all(self) -> tuple[GenreAdapter, ...]:
        """Return each registered adapter once, in registration order.

        The snapshot is immutable and aliases do not create extra genres.
        """
        return tuple({adapter.genre_id: adapter for adapter in self._identities.values()}.values())

    def resolve(self, document_type: str | None, *, purpose: str = "inspection") -> GenreAdapter:
        if purpose not in {"inspection", "production", "offline"}:
            raise GenreResolutionError("GENRE_RESOLUTION_PURPOSE_INVALID")
        adapter = self._identities.get(str(document_type or "").strip())
        if adapter is None:
            raise GenreResolutionError("GENRE_NOT_REGISTERED")
        if purpose == "production":
            from .contracts import GenreCapabilities
            from .commercial_gate import CommercialAdmissionError, require_commercial_production
            if (isinstance(adapter.capabilities, GenreCapabilities)
                    and adapter.capabilities.production_ready is False):
                raise GenreResolutionError("GENRE_OFFLINE_ONLY")
            try:
                require_commercial_production(adapter)
            except CommercialAdmissionError as exc:
                raise GenreResolutionError(exc.code) from exc
        if purpose == "offline" and not adapter.capabilities.offline_architecture_proof:
            raise GenreResolutionError("GENRE_OFFLINE_UNSUPPORTED")
        return adapter


GENRE_REGISTRY = GenreRegistry()


def resolve_genre(document_type: str | None, *, purpose: str = "inspection") -> GenreAdapter:
    return GENRE_REGISTRY.resolve(document_type, purpose=purpose)


def default_genre() -> GenreAdapter:
    """Compatibility default for existing internal no-argument helpers only."""
    return resolve_genre("work_summary")


def retrieval_profile_for(document_type: str | None):
    """Default live retrieval uses verified profiles only.

    Explicit offline callers may inject an inspected experimental profile into
    retrieve_documents. Merely naming that genre on the legacy current route
    must retain the previous relevance-only retrieval behavior.
    """
    try:
        return resolve_genre(document_type, purpose="production").retrieval_profile
    except GenreResolutionError as exc:
        if exc.code not in {"GENRE_NOT_REGISTERED", "GENRE_OFFLINE_ONLY"}:
            raise
        from .work_summary_retrieval import LEGACY_RETRIEVAL_PROFILE
        return LEGACY_RETRIEVAL_PROFILE


# Only this composition root imports concrete implementations. Adding an
# adapter requires registration here, without editing the shared pipeline.
from .work_summary import WORK_SUMMARY_ADAPTER
from .upstream_adapters import UPSTREAM_ADAPTERS

GENRE_REGISTRY.register(WORK_SUMMARY_ADAPTER)
for _adapter in UPSTREAM_ADAPTERS:
    GENRE_REGISTRY.register(_adapter)
