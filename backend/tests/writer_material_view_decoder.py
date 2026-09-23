"""Independent wire decoder: accepts ONLY the actual sent materials/prompt text.

Uses stdlib alone. No packet, production projection helper or out-of-band table
is available here. Kept separate so integration tests use the same wire check.
"""
from copy import deepcopy
import json
import re


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _object(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, "duplicate wire key"
        result[key] = value
    return result


def read_projection(sent_text):
    projections = []
    for body in re.findall(r"^## [^\n]+\n\n```json\n([^\n]*)\n```", sent_text, re.M):
        value = json.loads(body, object_pairs_hook=_object)
        if isinstance(value, dict) and value.get("version") == "writer-material-view-1":
            projections.append(value)
    assert len(projections) == 1, "exactly one sent Writer view required"
    return projections[0]


def decode_materials(sent_text):
    data = read_projection(sent_text)
    assert set(data) == {"version", "instructions", "evidence_columns", "rule_catalogue",
                         "source_groups", "reading_index", "projection_contract", "assembly_exceptions"}
    rules = {"semantic_constraints": "C", "writability": "W", "forbidden_detail_types": "F",
             "limitations": "L", "source_constraints": "S"}
    source_fields = {"section_id", "source", "text", "source_span"}
    direct = {"evidence_id", "semantic_state", "exact_values"}
    columns = data["evidence_columns"]
    expected = direct | {"literal_signals", "missing_fields", "extensions"} | {f + "_ref" for f in rules}
    assert len(columns) == len(expected) and set(columns) == expected
    pools = data["rule_catalogue"]
    assert set(pools) == set(rules)
    used = {field: set() for field in rules}
    for field, prefix in rules.items():
        pool = pools[field]
        assert type(pool) is dict
        assert all(re.fullmatch(prefix + r"[1-9][0-9]*", ref) for ref in pool)
        assert len({_json(v) for v in pool.values()}) == len(pool), "duplicate rules not shared"

    originals, reading = {}, {}
    seen_sources = set()
    for group in data["source_groups"]:
        assert set(group) <= source_fields | {"evidence"}
        base = {key: value for key, value in group.items() if key != "evidence"}
        assert _json(base) not in seen_sources and group["evidence"]
        seen_sources.add(_json(base))
        for cells in group["evidence"]:
            assert type(cells) is list and len(cells) == len(columns)
            row = dict(zip(columns, cells, strict=True))
            missing = row.pop("missing_fields")
            extensions = row.pop("extensions")
            assert type(missing) is list and len(missing) == len(set(missing))
            assert set(missing) <= (direct | set(rules) | {"literal_signals"}) - {"evidence_id"}
            assert type(extensions) is dict and not set(extensions) & (direct | set(rules) | source_fields)
            eid = row["evidence_id"]
            assert type(eid) is str and eid and eid not in originals
            original = dict(base)
            for field in direct:
                if field in missing:
                    assert row[field] is None
                else:
                    original[field] = row[field]
            for field in rules:
                ref = row[field + "_ref"]
                if field in missing:
                    assert ref is None
                else:
                    assert type(ref) is str and ref in pools[field], "invalid/cross-field/cyclic reference"
                    used[field].add(ref)
                    original[field] = deepcopy(pools[field][ref])  # terminal; never recursively resolve
            original.update(extensions)
            originals[eid] = original
            reading[eid] = dict(original)
            if "literal_signals" in missing:
                assert row["literal_signals"] is None
            else:
                reading[eid]["literal_signals"] = row["literal_signals"]
    contract = data["projection_contract"]
    assert set(contract) == {"evidence_order", "brief_limit_fields"}
    order, limit_fields = contract["evidence_order"], contract["brief_limit_fields"]
    assert len(order) == len(originals) and set(order) == originals.keys(), "order/ID loss"
    assert type(limit_fields) is list and len(limit_fields) == len(set(limit_fields))

    def ids_valid(ids, allowed):
        assert type(ids) is list and all(type(eid) is str and eid in allowed for eid in ids)

    def quotes(ids):
        ids_valid(ids, originals)
        assert all(type(originals[eid]["text"]) is str for eid in ids)
        return "\n".join(dict.fromkeys(originals[eid]["text"] for eid in ids))

    navigation = deepcopy(data["reading_index"])
    changes = data["assembly_exceptions"]
    assert set(changes) <= {str(i) for i in range(len(navigation["matters"]))}, "orphan matter"
    assigned = []
    for index, matter in enumerate(navigation["matters"]):
        assert "summary" not in matter and "limits" not in matter
        ids = matter["evidence_ids"]
        ids_valid(ids, originals)
        assigned.extend(ids)
        change = changes.get(str(index), {})
        assert set(change) <= {"summary", "limits"}
        if "summary" not in change:
            summary = {"evidence_ids": list(ids), "text": quotes(ids)}
        else:
            descriptor = change["summary"]
            if "value" in descriptor:
                assert set(descriptor) == {"value"}
                summary = deepcopy(descriptor["value"])
            else:
                assert set(descriptor) == {"fields", "text_from_evidence_ids"}
                assert "text" not in descriptor["fields"]
                ids_valid(descriptor["text_from_evidence_ids"], ids)
                summary = {**descriptor["fields"], "text": quotes(descriptor["text_from_evidence_ids"])}
        ids_valid(summary["evidence_ids"], ids)
        matter["summary"] = summary
        limits = []
        descriptors = change.get("limits", [{"evidence_id": eid} for eid in ids])
        for descriptor in descriptors:
            if "value" in descriptor:
                assert set(descriptor) == {"value"}
                limit = deepcopy(descriptor["value"])
            else:
                assert set(descriptor) <= {"evidence_id", "fields", "values", "rule_refs", "extra"}
                eid = descriptor["evidence_id"]
                assert eid in ids
                fields = descriptor.get("fields", limit_fields)
                values = dict(descriptor.get("values", {}))
                refs = descriptor.get("rule_refs", {})
                assert set(refs) <= rules.keys() and not set(refs) & values.keys()
                for field, ref in refs.items():
                    assert type(ref) is str and ref in pools[field]
                    used[field].add(ref)
                    values[field] = deepcopy(pools[field][ref])
                extra = descriptor.get("extra", {})
                assert len(fields) == len(set(fields)) and set(values) <= set(fields)
                assert not set(extra) & {"evidence_ids", "text"}
                details = {field: values[field] if field in values else reading[eid][field] for field in fields}
                limit = {**extra, "evidence_ids": [eid], "text": _json(details)}
            ids_valid(limit["evidence_ids"], ids)
            limits.append(limit)
        matter["limits"] = limits
    assert len(assigned) == len(originals) and set(assigned) == originals.keys()
    assert all(used[field] == pools[field].keys() for field in rules), "orphan rule"
    return [deepcopy(originals[eid]) for eid in order], navigation
