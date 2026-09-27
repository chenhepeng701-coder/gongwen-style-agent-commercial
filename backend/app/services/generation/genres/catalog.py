"""Read-only UI projection of the Registry and legacy task compatibility.

This does not register genres or grant production capability. The same Registry
production resolution used by requests independently evaluates every entry.
Legacy mappings only normalize known UI labels; they supply no factual content.
"""
from . import registry


# These six old UI values have unambiguous task meaning. Other legacy values
# remain displayable, but are not silently interpreted as a canonical genre.
# Targets are resolved through the Registry on every catalog construction.
_LEGACY_UI_VALUES = (
    ("领导讲话稿", "speech", None),
    ("工作汇报", "report", None),
    ("实施方案", "work-plan", None),
    ("党课讲稿", "speech", "党课讲稿"),
    ("民主生活会对照检查材料", "speech", "民主生活会对照检查"),
    ("组织生活会个人发言材料", "speech", "组织生活会"),
)


def genre_catalog() -> dict:
    """Expose only names and capabilities, without prompts or source paths."""
    from app.services.retrieval.scene_classifier import get_scene_taxonomy

    genres = []
    for adapter in registry.GENRE_REGISTRY.all():
        try:
            registry.resolve_genre(adapter.genre_id, purpose="production")
            production_ready = True
        except registry.GenreResolutionError:
            production_ready = False
        genres.append({
            "genre_id": adapter.genre_id,
            "document_type": adapter.document_type,
            "display_name": adapter.document_type,
            "aliases": list(adapter.aliases),
            "capability_status": adapter.capabilities.status,
            "production_ready": production_ready,
            "commercial_maturity": adapter.commercial.maturity.value if adapter.commercial else None,
            "export_formats": list(adapter.capabilities.export_formats),
            "generation_route": "full_article_drafting" if production_ready else None,
        })
    scene_names = {row["name"] for row in get_scene_taxonomy()}
    legacy_mappings = []
    for label, identity, scene in _LEGACY_UI_VALUES:
        try:
            adapter = registry.resolve_genre(identity)
        except registry.GenreResolutionError as exc:
            if exc.code != "GENRE_NOT_REGISTERED":
                raise
            continue
        if scene is not None and scene not in scene_names:
            continue
        legacy_mappings.append({"legacy_value": label, "document_type": adapter.document_type, "scene": scene})
    return {"genres": genres, "legacy_mappings": legacy_mappings}
