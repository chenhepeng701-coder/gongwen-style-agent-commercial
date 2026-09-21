"""Local article edits and stale delivery metadata; never runs a safety check."""
from app.models.full_article import FullArticleProjection, article_blocks, article_revision_digest, text_sha
from app.models.generation import ArticleGenResult, GenerationArticleSnapshot


def history_article_view(article: dict) -> dict:
    """Derive history display state without treating portable hashes as trust.

    History snapshots contain no authenticated evaluator receipt or complete
    current evidence inputs. Client/stored reports cannot authorize PASS here.
    Keep the canonical body and revision; only a trusted recheck can grant PASS.
    This projection neither evaluates facts nor changes the delivery gate.
    """
    if article.get("article_markdown") is None:
        return article
    # STALE is already the editor contract for changed bodies. An untouched
    # version needs review, without falsely claiming the user edited its text.
    check_state = "STALE" if article.get("article_version", 1) > 1 else "REVIEW_REQUIRED"
    return {**article,
        "full_article_check_status": {
            "fact": check_state, "quality": check_state, "review": "REVIEW_REQUIRED",
            "required_checks_complete": False,
            "article_revision_id": article.get("article_revision_id", ""),
            "reason": "HISTORY_CHECK_TRUST_UNAVAILABLE",
        },
        "length_gate_status": check_state, "structure_gate_status": check_state,
        "final_pipeline_stage_completed": False,
        "delivery_decision": None,
        "delivery_status": "SUCCESS_WITH_REVIEW", "official_success": False,
        "article_qualified": False, "user_confirmation_required": True,
    }


def history_entry_view(entry: dict) -> dict:
    """The displayed entry status follows its current full-article snapshots."""
    articles = [history_article_view(article) for article in entry.get("articles", [])]
    if not any(article.get("article_markdown") is not None for article in articles):
        return entry
    return {**entry, "articles": articles, "status": "SUCCESS_WITH_REVIEW"}


def revise_full_article(previous: GenerationArticleSnapshot, article_markdown: str) -> GenerationArticleSnapshot:
    previous.require_current_body()
    if previous.article_markdown is None:
        raise ValueError("FULL_ARTICLE_REQUIRED")
    if article_markdown == previous.article_markdown:
        return previous
    blocks = article_blocks(article_markdown)
    if not blocks or blocks[0].level != 1 or not any(b.level == 0 for b in blocks):
        raise ValueError("FULL_ARTICLE_BODY_OR_TITLE_MISSING")
    version = previous.article_version + 1
    sha = text_sha(article_markdown)
    revision = article_revision_digest(sha, version, previous.article_revision_id)
    data = previous.model_dump()
    data.update(
        article_markdown=article_markdown, article_version=version,
        article_sha256=sha, article_revision_id=revision,
        parent_revision_id=previous.article_revision_id,
        previous_revision={"revision_id": previous.article_revision_id,
            "version": previous.article_version, "article_sha256": previous.article_sha256,
            "candidate_digest": previous.content_projection.candidate_digest,
            "check_status": previous.full_article_check_status},
        # An editor revision is not a newly evaluated FullArticleCandidate.
        content_projection=FullArticleProjection(content_sha256=sha, candidate_digest="", blocks=blocks),
        full_article_check_status={"fact": "STALE", "quality": "STALE",
            "review": "REVIEW_REQUIRED", "required_checks_complete": False,
            "article_revision_id": revision},
        title=article_markdown[blocks[0].text_start:blocks[0].end], public_title="",
        save_path="", docx_path="", reference_sources=[],
        draft_full_article={}, wrapped_preview="", appreciation={},
        length_gate_status="STALE", structure_gate_status="STALE",
        final_pipeline_stage_completed=False, selected_candidate_stage="USER_EDIT", delivery_decision=None,
        risk_items=[{"stage": "ARTICLE_EDIT", "issue_type": "REVIEW_REQUIRED",
            "description": "正文已修改，上一版本检查及风险位置已失效，请重新人工复核。"}],
        delivery_status="SUCCESS_WITH_REVIEW", official_success=False,
        article_qualified=False, user_confirmation_required=True,
        word_count=len("".join(article_markdown.split())),
        reason="正文已修改，旧检查失效。", review_suggestion="请复核当前版本全文。",
        review_notes=[], delivery_warnings=["上一版本检查不适用于当前正文。"],
    )
    # Reuse the existing public delivery invariant before persisting.
    current = ArticleGenResult.model_validate(data)
    return GenerationArticleSnapshot.model_validate(current.model_dump())
