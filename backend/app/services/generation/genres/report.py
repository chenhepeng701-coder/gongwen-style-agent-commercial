"""Compatibility name for the single pinned presentation / 汇报材料 adapter.

The Foundation's local `report` identity continues to mean 汇报材料. Its old
host-authored proof is superseded by upstream presentation, not a second genre.
Upstream statutory report / 报告 uses the local identity `statutory_report`.
"""
from .upstream_adapters import PRESENTATION_ADAPTER

REPORT_ADAPTER = PRESENTATION_ADAPTER
RULESET_HASH = REPORT_ADAPTER.drafting_rules.ruleset_hash
