"""edit: attach authored metadata to ANY entity in the crate, by path.

This is the one editing operation behind three surfaces — the `mate describe`/`mate seed` CLI,
the issue form (`from-issue`), and (separately) Crate-O. They all produce the same edit-intent:

    (target path, optional @type, {property: value})

The target defaults to the root (`./`) — so editing the root ("seed") and describing a
sub-folder/file are the *same* code path, differing only in which entity the path resolves to.
The crate is the single source of truth, so this writes straight into ro-crate-metadata.json;
build-as-merge preserves it (including authored properties on File entities).

Field shaping (people -> Person refs, license -> {@id}, list-valued fields) comes from the
PROFILE: the root's `root.fields` for the root, a type's `component_types[type].fields` for a
sub-entity. The escape hatch is `--set <property>=<value>` — any schema.org property.

  mate seed --name "My dataset" --description "…" --license CC-BY-4.0 --author 0000-0002-…
  mate describe recordings/ --type SoftwareSourceCode --set programmingLanguage=Python
"""
import json
import re
import shlex
from pathlib import Path

from .build_crate import build_crate, _person
from .profile import load_profile

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


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "person").lower()).strip("-")


def _ensure_person(doc, spec):
    """Mint a Person entity for an authored creator spec (if absent) and return an @id reference."""
    pid = spec.get("@id") or "#person-" + _slug(spec.get("name"))
    if not any(e.get("@id") == pid for e in doc["@graph"]):
        ent = {"@id": pid, "@type": "Person"}
        if spec.get("name"):
            ent["name"] = spec["name"]
        doc["@graph"].append(ent)
    return {"@id": pid}


def _prop_defs(profile, tid, type_):
    """property -> field-def map used to shape `--set` values, drawn from the profile.
    Root uses root.fields (re-keyed by property); a sub-entity uses its type's component_types
    fields (already keyed by property)."""
    if tid == ROOT:
        return {fdef.get("property", ""): fdef
                for fdef in ((profile.get("root", {}) or {}).get("fields", {}) or {}).values()}
    if type_:
        return (profile.get("component_types", {}) or {}).get(type_, {}).get("fields", {}) or {}
    return {}


def _apply_set(entity, key, val, prop_defs):
    fdef = prop_defs.get(key, {}) or {}
    if key == "license":
        entity[key] = {"@id": str(val)}
    elif fdef.get("input") == "list" or fdef.get("many"):
        entity[key] = [x.strip() for x in str(val).split(",") if x.strip()]
    else:
        entity[key] = val


def edit_entity(repo_dir, target=".", type_=None, name=None, description=None,
                authors=None, sets=None, list_fields=False):
    """Apply an edit-intent to one entity in the crate. Returns a result dict (incl. warnings)."""
    repo_dir = Path(repo_dir).resolve()
    profile = load_profile(repo_dir)

    if list_fields:
        types = profile.get("component_types", {}) or {}
        if type_:
            fields = (types.get(type_, {}) or {}).get("fields", {})
            return {"type": type_, "fields": {k: v.get("label", k) for k, v in fields.items()}}
        return {"types": {k: v.get("label", k) for k, v in types.items()}}

    crate_path = repo_dir / "ro-crate-metadata.json"
    build_crate(repo_dir, out_path=str(crate_path), merge=True)   # ensure the entity exists
    doc = json.loads(crate_path.read_text())

    tid = _resolve_id(doc, target)
    if tid is None:
        return {"error": f"no entity for path '{target}' in the crate — does it exist in the repo?"}
    entity = next(e for e in doc["@graph"] if e.get("@id") == tid)

    prop_defs = _prop_defs(profile, tid, type_)
    applied, warnings = [], []

    if name:
        entity["name"] = name; applied.append("name")
    if description:
        entity["description"] = description; applied.append("description")
    if type_:
        cur = entity.get("@type", "Dataset")
        cur = [cur] if isinstance(cur, str) else list(cur)
        if type_ not in cur:
            cur.append(type_)
        entity["@type"] = cur; applied.append("@type")
    if authors:
        refs = [_ensure_person(doc, _person(a)) for a in authors]
        prop = "creator" if tid == ROOT else "author"   # root authorship is `creator`
        entity[prop] = refs; applied.append(prop)

    for kv in (sets or []):
        key, _, val = kv.partition("=")
        key, val = key.strip(), val.strip()
        if not key:
            continue
        _apply_set(entity, key, val, prop_defs)
        applied.append(key)
        if type_ and prop_defs and key not in prop_defs:
            warnings.append(f"`{key}` is not a curated field for {type_} (allowed, but check the term)")

    crate_path.write_text(json.dumps(doc, indent=2))
    return {"edited": tid, "type": type_, "applied": applied,
            "curated_fields": list(prop_defs), "warnings": warnings}


# Back-compat alias: `describe` IS `edit_entity` (the CLI exposes both `describe` and `seed`).
def describe(repo_dir, target, **kw):
    return edit_entity(repo_dir, target, **kw)


def command_for(target, type_=None, name=None, description=None, authors=None, sets=None):
    """Render an edit-intent as the equivalent `mate` command — the CLI-teaching string shown in
    the issue confirmation comment. Faithful to the CLI flags so it can be copy-pasted."""
    is_root = target in (".", "./", "", None)
    parts = ["crate", "seed"] if is_root else ["crate", "describe", shlex.quote(target)]
    if type_:
        parts += ["--type", type_]
    if name:
        parts += ["--name", shlex.quote(name)]
    if description:
        parts += ["--description", shlex.quote(description)]
    for a in (authors or []):
        parts += ["--author", shlex.quote(a)]
    for kv in (sets or []):
        parts += ["--set", shlex.quote(kv)]
    return " ".join(parts)
