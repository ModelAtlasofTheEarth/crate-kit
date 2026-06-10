"""enrich: resolve the PIDs in the crate and fold in metadata, driven entirely by the profile's
declarative `enrich` CROSSWALK (no per-source code). PID-first and BEST-EFFORT — a network failure
or a missing field is skipped, never fatal. GAP-FILL only: a property already set (authored) is
never overwritten. Resolved values are non-derived, so build-as-merge preserves them.

The engine is generic: for each entity whose @id matches a resolver's `match`, fetch the resolver's
`url`, then for each `map` entry pull a JMESPath path out of the response (optionally via a named
transform) and set the target property. SCOPE = exactly what the profile maps. Adding a niche
resolver is a profile edit, not a code change.
"""
import json
import re
import urllib.request
from pathlib import Path

import jmespath

from .profile import load_profile


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "person").lower()).strip("-")


def _get_json(url, accept="application/json"):
    try:
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "crate-kit"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


# ── named transforms (the toolkit's generic post-processors; profiles reference them by name) ──

def _t_join(v, doc):
    return " ".join(str(x) for x in v if x) if isinstance(v, list) else v


def _t_join_date(v, doc):
    return "-".join(f"{int(x):02d}" if i else str(x) for i, x in enumerate(v)) if isinstance(v, list) and v else v


def _t_first(v, doc):
    return v[0] if isinstance(v, list) and v else v


def _t_people(v, doc):
    """An array of author objects (Crossref shape) -> minted Person entities + @id references.
    Anonymous inline objects don't round-trip through editors, so every author gets a real @id."""
    refs = []
    for a in (v or []):
        nm = " ".join(x for x in (a.get("given"), a.get("family")) if x) if isinstance(a, dict) else None
        if not nm:
            continue
        pid = a.get("ORCID") or ("#author-" + _slug(nm))
        if not any(e.get("@id") == pid for e in doc["@graph"]):
            doc["@graph"].append({"@id": pid, "@type": "Person", "name": nm})
        refs.append({"@id": pid})
    return refs or None


def _t_id_ref(v, doc):
    return {"@id": v} if v else None


def _t_striptags(v, doc):
    if not v:
        return None
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", v)).strip()   # drop HTML/JATS tags
    return text or None


TRANSFORMS = {"join": _t_join, "join_date": _t_join_date, "first": _t_first, "people": _t_people,
              "id_ref": _t_id_ref, "striptags": _t_striptags}


def _pid(entity_id, cfg):
    """Extract the API {id} from an entity @id, per the resolver config."""
    pat = cfg.get("id_pattern")
    if pat:
        m = re.search(pat, entity_id)
        return m.group(1) if m else None
    marker = cfg["match"] + "/"
    return entity_id.split(marker, 1)[1] if marker in entity_id else None


def _resolver_for(entity_id, enrich_cfg):
    for kind, cfg in enrich_cfg.items():
        m = cfg.get("match")
        if m and m in entity_id and cfg.get("url") and cfg.get("map"):
            return kind, cfg
    return None, None


def _enrich_entity(entity, doc, enrich_cfg):
    """Resolve one entity in place. Returns the resolver kind if anything was applied, else None."""
    eid = entity.get("@id", "")
    if not eid.startswith("http"):
        return None
    kind, cfg = _resolver_for(eid, enrich_cfg)
    if not cfg:
        return None
    pid = _pid(eid, cfg)
    if not pid:
        return None
    data = _get_json(cfg["url"].format(id=pid), cfg.get("accept", "application/json"))
    if not data:
        return None

    applied = False
    for prop, spec in (cfg.get("map") or {}).items():
        path, tname = (spec, None) if isinstance(spec, str) else (spec.get("path"), spec.get("transform"))
        try:
            val = jmespath.search(path, data)
        except Exception:
            val = None
        if tname:
            val = TRANSFORMS.get(tname, lambda v, d: v)(val, doc)
        if val in (None, "", []):
            continue
        if prop == "@type":
            # REFINE (append-not-overwrite): a resolver may add a structural type it discovered
            # (e.g. SoftwareSourceCode) without clobbering existing types. Generic — the WHICH type
            # comes from the profile's map, not the engine.
            cur = entity.get("@type")
            cur = [cur] if isinstance(cur, str) else list(cur or [])
            for t in ([val] if isinstance(val, str) else val):
                if t and t not in cur:
                    cur.append(t)
            entity["@type"] = cur[0] if len(cur) == 1 else cur
            applied = True
            continue
        if entity.get(prop):
            continue                                   # gap-fill only — never overwrite authored
        entity[prop] = val
        applied = True
    return kind if applied else None


def enrich(repo_dir, out_path=None):
    repo_dir = Path(repo_dir).resolve()
    crate_path = repo_dir / "ro-crate-metadata.json"
    if not crate_path.exists():
        return {"error": "no crate to enrich (run build first)"}

    enrich_cfg = load_profile(repo_dir).get("enrich", {}) or {}
    doc = json.loads(crate_path.read_text())
    resolved = []
    for e in list(doc["@graph"]):                      # snapshot: entities minted mid-run wait for next pass
        if _enrich_entity(e, doc, enrich_cfg):
            resolved.append(e.get("@id"))

    Path(out_path or crate_path).write_text(json.dumps(doc, indent=2))
    return {"resolved": resolved}
