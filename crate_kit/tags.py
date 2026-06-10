"""tags: apply controlled-vocabulary TAGS to an entity — the tag-binding sibling of the role verb
(describe.set_role). See TARGET_ARCHITECTURE §23: roles and tags are two bindings of one mechanism.

A tag is a schema.org `DefinedTerm` (member of a `DefinedTermSet` = a tag set), referenced from the
target entity's `about` (default) or `keywords`. `inDefinedTermSet` names the set so the resolver can
group terms into website categories. The profile's `tag_sets:` declares each set's target, property,
terms, and the website group it feeds — the engine itself is tag-agnostic.
"""
import json
import shlex
from pathlib import Path

from .build_crate import build_crate
from .describe import _add_to_list, _resolve_id
from .profile import load_profile


def set_eid(set_name):
    return f"#tagset-{set_name}"


def _term_eid(set_name, term_id):
    return f"#tag/{set_name}/{term_id}"


def _resolve_term(terms_cfg, raw):
    """Accept either a term id or its display name; return the canonical id (or None)."""
    raw = (raw or "").strip()
    if raw in terms_cfg:
        return raw
    return next((tid for tid, c in terms_cfg.items() if c.get("name") == raw), None)


def apply_tag(repo_dir, set_name, terms, target=None):
    """Apply one or more tags from `set_name` to an entity. Mints the DefinedTermSet + DefinedTerm
    entities (once) and references them from the target's property. Gap-safe + de-duped."""
    repo_dir = Path(repo_dir).resolve()
    profile = load_profile(repo_dir)
    tset = (profile.get("tag_sets") or {}).get(set_name)
    if not tset:
        known = ", ".join((profile.get("tag_sets") or {}).keys()) or "(none)"
        return {"error": f"unknown tag set '{set_name}' (known: {known})"}

    terms_cfg = {t["id"]: t for t in (tset.get("terms") or []) if "id" in t}
    prop = tset.get("property", "about")
    tgt = target or tset.get("target", "root")

    crate_path = repo_dir / "ro-crate-metadata.json"
    build_crate(repo_dir, out_path=str(crate_path), merge=True)
    doc = json.loads(crate_path.read_text())
    by_id = {e.get("@id"): e for e in doc["@graph"]}

    tid = _resolve_id(doc, "." if tgt == "root" else tgt)
    if tid is None:
        return {"error": f"no entity for target '{tgt}' in the crate"}
    entity = by_id[tid]

    seid = set_eid(set_name)
    if seid not in by_id:
        ent = {"@id": seid, "@type": "DefinedTermSet", "name": tset.get("label", set_name)}
        doc["@graph"].append(ent); by_id[seid] = ent

    applied, unknown = [], []
    for raw in (terms or []):
        term_id = _resolve_term(terms_cfg, raw)
        if not term_id:
            if (raw or "").strip():
                unknown.append(raw)
            continue
        teid = _term_eid(set_name, term_id)
        if teid not in by_id:
            ent = {"@id": teid, "@type": "DefinedTerm", "name": terms_cfg[term_id].get("name", term_id),
                   "termCode": term_id, "inDefinedTermSet": {"@id": seid}}
            doc["@graph"].append(ent); by_id[teid] = ent
        _add_to_list(entity, prop, {"@id": teid})
        applied.append(term_id)

    crate_path.write_text(json.dumps(doc, indent=2))
    return {"tagged": tid, "set": set_name, "property": prop, "applied": applied, "unknown": unknown}


def command_for_tag(set_name, term_ids, target=None):
    parts = ["crate", "tag", set_name] + [shlex.quote(t) for t in term_ids]
    if target and target != "root":
        parts += ["--target", shlex.quote(target)]
    return " ".join(parts)
