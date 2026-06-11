"""terms: apply a controlled-vocabulary term to an entity — ONE mechanism, two bindings (§23).

Every "stamp a vocab term on an entity" edit shares the same spine: build+merge the crate,
resolve the target path to an entity, resolve the term through the vocabulary loader
(`vocab.load_vocab` / `vocab.load_tag_terms` — the one reader), write the term's JSON-LD shape,
write the crate back. Only the WRITE SHAPE differs, and that is the binding:

  role — the term lands in `additionalType` (+ structural `@type` via `refines`, caption routed
         to the term's text field, `single` cardinality moves the tag off any other holder).
         Surface: `crate role`, the content form.
  tag  — the term lands as a `DefinedTerm` entity (member of a minted `DefinedTermSet`)
         referenced from a property (default `about`), grouped by `inDefinedTermSet` so the
         website resolver can bucket terms into categories.
         Surface: `crate tag`, the configure form's tag dropdowns.

`describe.set_role` and `tags.apply_tag` are thin re-exports of these — kept for their import
sites, not as second implementations.
"""
import json
import shlex
from pathlib import Path

from .build_crate import build_crate
from .profile import load_profile
from .vocab import load_vocab, load_tag_terms

ROOT = "./"


def _resolve_id(doc, target):
    """Map a user-supplied path to an existing entity @id (root, a dir Dataset, or a File)."""
    if target in (".", "./", "", None):
        return ROOT
    ids = {e.get("@id") for e in doc["@graph"]}
    for cand in (target, target.rstrip("/") + "/"):   # file path, or directory (trailing slash)
        if cand in ids:
            return cand
    return None


def _add_to_list(entity, key, val):
    cur = entity.get(key)
    lst = cur if isinstance(cur, list) else ([cur] if cur else [])
    if val not in lst:
        lst.append(val)
    entity[key] = lst[0] if len(lst) == 1 else lst


def _open_crate(repo_dir):
    """The shared preamble of every term edit: profile, build-as-merge, parsed graph."""
    repo_dir = Path(repo_dir).resolve()
    profile = load_profile(repo_dir)
    crate_path = repo_dir / "ro-crate-metadata.json"
    build_crate(repo_dir, out_path=str(crate_path), merge=True)
    doc = json.loads(crate_path.read_text())
    return profile, crate_path, doc


# ── binding: role ─────────────────────────────────────────────────────────────

def apply_role(repo_dir, target, role, type_=None, caption=None):
    """Tag an entity with a website ROLE (`additionalType`) — e.g. graphical-abstract.

    Vocab-driven (vocab.load_vocab): when the role is a known term, the term decides everything —
    its `refines` sets the structural @type (Graphical abstract ⇒ ImageObject), its `text` field
    routes the caption text (caption | description), its `type_value` is what lands in
    additionalType (a loadable URI like doco:Figure, else the local term name), and `single`
    cardinality MOVES the tag off any other holder. An unknown role still works (the escape
    hatch): applied verbatim, @type only if `type_` is given, text always to `caption`."""
    profile, crate_path, doc = _open_crate(repo_dir)
    term = load_vocab(profile).get(role)

    tid = _resolve_id(doc, target)
    if tid is None:
        return {"error": f"no entity for path '{target}' in the crate"}
    entity = next(e for e in doc["@graph"] if e.get("@id") == tid)

    # structural @type: explicit --type wins; else the term's `refines`.
    struct_type = type_ or (term.refines if term else None)
    if struct_type:
        _add_to_list(entity, "@type", struct_type)

    # text → the field the term carries (caption | description); default caption.
    if caption:
        entity[(term.text if term and term.text else "caption")] = caption

    # the value that actually goes in additionalType (URI for loadable terms, else the name).
    type_value = term.type_value if term else role

    single = term.single if term else False
    if single:                                     # move the tag off any other holder
        for e in doc["@graph"]:
            if e is entity:
                continue
            at = e.get("additionalType")
            if at and type_value in (at if isinstance(at, list) else [at]):
                rest = [x for x in (at if isinstance(at, list) else [at]) if x != type_value]
                if rest:
                    e["additionalType"] = rest[0] if len(rest) == 1 else rest
                else:
                    e.pop("additionalType", None)
    _add_to_list(entity, "additionalType", type_value)

    crate_path.write_text(json.dumps(doc, indent=2))
    return {"roled": tid, "role": role, "additionalType": type_value, "known": term is not None,
            "single": bool(single), "type": entity.get("@type"),
            "text_field": (term.text if term and term.text else "caption") if caption else None}


def command_for_role(target, role, caption=None):
    """The CLI-teaching string for a role edit — shown in the issue confirmation comment."""
    parts = ["crate", "role", shlex.quote(target), "--as", role]
    if caption:
        parts += ["--caption", shlex.quote(caption)]
    return " ".join(parts)


# ── binding: tag ──────────────────────────────────────────────────────────────

def set_eid(set_name):
    return f"#tagset-{set_name}"


def _term_eid(set_name, term_id):
    return f"#tag/{set_name}/{term_id}"


def apply_tags(repo_dir, set_name, terms, target=None):
    """Apply one or more tags from `set_name` to an entity. Mints the DefinedTermSet + DefinedTerm
    entities (once) and references them from the target's property. Gap-safe + de-duped.
    Term ids or display names both resolve (the dropdown shows names; the CLI teaches ids)."""
    profile, crate_path, doc = _open_crate(repo_dir)
    tset = (profile.get("tag_sets") or {}).get(set_name)
    if not tset:
        known = ", ".join((profile.get("tag_sets") or {}).keys()) or "(none)"
        return {"error": f"unknown tag set '{set_name}' (known: {known})"}
    vocab = load_tag_terms(profile, set_name)
    prop = tset.get("property", "about")
    tgt = target or tset.get("target", "root")

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
        raw = (raw or "").strip()
        term = vocab.get(raw) or next((t for t in vocab.values() if t.label == raw), None)
        if term is None:
            if raw:
                unknown.append(raw)
            continue
        teid = _term_eid(set_name, term.name)
        if teid not in by_id:
            ent = {"@id": teid, "@type": "DefinedTerm", "name": term.label,
                   "termCode": term.name, "inDefinedTermSet": {"@id": seid}}
            doc["@graph"].append(ent); by_id[teid] = ent
        _add_to_list(entity, prop, {"@id": teid})
        applied.append(term.name)

    crate_path.write_text(json.dumps(doc, indent=2))
    return {"tagged": tid, "set": set_name, "property": prop, "applied": applied, "unknown": unknown}


def command_for_tag(set_name, term_ids, target=None):
    parts = ["crate", "tag", set_name] + [shlex.quote(t) for t in term_ids]
    if target and target != "root":
        parts += ["--target", shlex.quote(target)]
    return " ".join(parts)
