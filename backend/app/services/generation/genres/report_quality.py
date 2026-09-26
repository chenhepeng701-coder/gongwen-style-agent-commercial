"""Compatibility call into the single pinned presentation quality evaluator."""


def evaluate_report_offline_quality(candidate, sidecar, fact_report):
    from .report import REPORT_ADAPTER
    return REPORT_ADAPTER.quality_evaluator(candidate, sidecar, fact_report)
