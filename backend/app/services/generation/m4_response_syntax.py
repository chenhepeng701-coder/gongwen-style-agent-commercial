"""Conservative M4 wire syntax recovery; never repairs a business contract.

Only whole-response fences and paired, unescaped prose quotes are supported.
No punctuation guessing, field completion, reserialization or model calls.
"""
from __future__ import annotations

import hashlib
import json
import re

from app.services.generation.flow3_v3_trace import parse_structured_draft


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plain(char: str) -> bool:
    return bool(char) and not char.isspace() and ord(char) >= 32 and char not in '"\\{}[]:,'


def _reject_constant(_):
    raise ValueError("M4_NON_JSON_CONSTANT")


def recover_m4_response_syntax(raw: str, audit: dict) -> str:
    """Return exact wire text plus permitted syntax edits, or fail closed.

    Locations use zero-based Unicode offsets and one-based line/column in raw.
    Edits are staged locally and committed only after the entire object parses.
    This intentionally rejects repairable-looking forms outside the whitelist.
    """
    audit.update(raw_response_digest=_sha(raw), repair_applied=False,
                 repair_locations=[], syntax_diff=[], repaired_response_digest=_sha(raw),
                 json_parse="FAIL", schema_validation="NOT_REACHED",
                 binding_validation="NOT_REACHED", m4_contract_validation="NOT_REACHED",
                 final_contract_result="FAIL_CLOSED")
    text = raw
    origins = list(range(len(raw)))
    edits = []
    # Same whole-object fence boundary as parse_structured_draft. Never extract
    # a plausible object out of surrounding commentary or multiple responses.
    fence = re.fullmatch(r'\s*```(?:json)?\s*\n([\s\S]*?)\n```\s*', text, re.IGNORECASE)
    if fence:
        start, end = fence.span(1)
        edits.extend([{"offset": 0, "before": text[:start], "after": ""},
                      {"offset": end, "before": text[end:], "after": ""}])
        text, origins = text[start:end], origins[start:end]
    for round_index in range(33):
        try:
            # Standard JSON parse first; duplicate members must not silently
            # erase business values before the unchanged schema sees them.
            def unique_members(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("M4_DUPLICATE_JSON_MEMBER")
                    result[key] = value
                return result

            json.loads(text, object_pairs_hook=unique_members, parse_constant=_reject_constant)
            parse_structured_draft(text)  # reuse the existing one-object owner
            break
        except json.JSONDecodeError as exc:
            left = exc.pos - 1
            right = text.find('"', exc.pos)
            # A valid value string has closed too early. Only an adjacent
            # prose token followed by another quote AND prose continuation is
            # unambiguous within this escape-only grammar. Structural JSON
            # punctuation, whitespace, escapes, keys and truncation are refused.
            if (round_index == 32 or exc.msg != "Expecting ',' delimiter"
                    or left <= 0 or text[left] != '"' or right <= exc.pos
                    or right + 1 >= len(text) or not _plain(text[left - 1])
                    or not _plain(text[right + 1])
                    or not all(_plain(c) for c in text[exc.pos:right])):
                raise ValueError("M4_JSON_SYNTAX_NOT_UNIQUELY_RECOVERABLE") from exc
            try:
                json.loads(text[exc.pos:right])
            except ValueError:
                pass
            else:
                # A JSON scalar could instead be a separate value with missing
                # separators. Do not choose the prose interpretation for it.
                raise ValueError("M4_JSON_SYNTAX_AMBIGUOUS_SCALAR") from exc
            for offset in (left, right):
                edits.append({"offset": origins[offset], "before": '"', "after": '\\"'})
            text = text[:left] + '\\' + text[left:right] + '\\' + text[right:]
            origins = origins[:left] + [None] + origins[left:right] + [None] + origins[right:]
    edits.sort(key=lambda edit: edit["offset"])
    for edit in edits:
        offset = edit["offset"]
        edit.update(line=raw.count("\n", 0, offset) + 1,
                    column=offset - raw.rfind("\n", 0, offset))
    audit.update(repair_applied=bool(edits), syntax_diff=edits,
                 repair_locations=[{k: e[k] for k in ("offset", "line", "column")} for e in edits],
                 repaired_response_digest=_sha(text), json_parse="PASS")
    return text
