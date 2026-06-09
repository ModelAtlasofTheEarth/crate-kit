"""website: project a crate onto the profile's `website:` content contract → a flat website.json.

This is the stable, presentation-agnostic seam (TARGET_ARCHITECTURE.md §21): a "dumb" static site
(Quarto today, anything later) consumes the flat result; all graph traversal lives here. It is a
PURE projection of the crate — re-derivable, never a stored source of truth.

Per slot the schema declares a source — `from:` (a root property), `type:` (all entities of a
schema.org type), or `role:` (all entities tagged with that `additionalType`) — and an optional
`project:` to shape each resolved entity, plus `single: true` to take the first.
"""
import json
from pathlib import Path

from .build_crate import build_crate
from .profile import load_profile


def _as_list(v):
    return v if isinstance(v, list) else ([v] if v else [])


def _types(e):
    return _as_list(e.get("@type"))


def _atypes(e):
    return _as_list(e.get("additionalType"))


def _raw_base(root):
    """raw.githubusercontent base for resolving in-repo asset paths to absolute URLs."""
    code = (root.get("codeRepository") or "").rstrip("/")
    if code.endswith(".git"):
        code = code[:-4]
    if "github.com/" in code:
        return code.replace("https://github.com/", "https://raw.githubusercontent.com/") + "/main/"
    return None


def _abs(v, raw_base):
    if isinstance(v, str) and raw_base and not v.startswith(("http", "#", "./", "/")):
        return raw_base + v
    return v


def _val(entity, prop, raw_base):
    v = entity.get(prop)
    if prop == "name" and not v:                    # fall back to given+family (older Person shape)
        v = " ".join(x for x in (entity.get("givenName"), entity.get("familyName")) if x) or None
    return _abs(v, raw_base) if prop == "@id" else v


def _project(entity, proj, raw_base):
    """Shape one resolved entity per `project`: None→name/@id; a string→that property; a dict→a map.
    A `@id` value that is an in-repo path is absolutised to a raw GitHub URL."""
    if proj is None:
        return entity.get("name") or _abs(entity.get("@id"), raw_base)
    if isinstance(proj, str):
        return _val(entity, proj, raw_base)
    return {k: _val(entity, p, raw_base) for k, p in proj.items()}


def _slot(doc, by_id, root, spec, raw_base):
    proj = spec.get("project")
    single = spec.get("single")

    if "from" in spec:
        v = root.get(spec["from"])
        if v in (None, "", []):
            return None
        was_list = isinstance(v, list)
        out = []
        for it in _as_list(v):
            if isinstance(it, dict) and "@id" in it:
                ent = by_id.get(it["@id"])
                out.append(_project(ent, proj, raw_base) if ent is not None else it["@id"])
            else:
                out.append(it)                      # plain scalar (keyword, license id, …)
        if single or not was_list:
            return out[0] if out else None
        return out

    if "type" in spec:
        want = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        ents = [e for e in doc["@graph"] if any(t in _types(e) for t in want)]
    elif "role" in spec:
        ents = [e for e in doc["@graph"] if spec["role"] in _atypes(e)]
    else:
        return None
    out = [_project(e, proj, raw_base) for e in ents]
    if single:
        return out[0] if out else None
    return out


def resolve_website(repo_dir, out_path=None, build=True):
    """Resolve the crate against the profile's `website:` schema → a flat dict (website.json)."""
    repo_dir = Path(repo_dir).resolve()
    if build:
        build_crate(repo_dir, out_path=str(repo_dir / "ro-crate-metadata.json"), merge=True)
    crate_path = repo_dir / "ro-crate-metadata.json"
    if not crate_path.exists():
        return {"error": "no crate (run build first)"}

    doc = json.loads(crate_path.read_text())
    by_id = {e.get("@id"): e for e in doc["@graph"]}
    root = by_id.get("./", {})
    schema = (load_profile(repo_dir).get("website", {}) or {})
    raw_base = _raw_base(root)

    site = {}
    for slot, spec in schema.items():
        val = _slot(doc, by_id, root, spec, raw_base)
        if val not in (None, "", []):
            site[slot] = val

    if out_path:
        Path(out_path).write_text(json.dumps(site, indent=2))
    return site
