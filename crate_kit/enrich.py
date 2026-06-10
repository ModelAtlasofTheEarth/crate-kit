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


def _datacite_orcid(a):
    for ni in (a.get("nameIdentifiers") or []):
        v = ni.get("nameIdentifier", "")
        if "orcid" in v.lower():
            return v
    return None


def _t_people(v, doc):
    """An array of author objects -> minted Person entities + @id references. Tolerant of BOTH
    Crossref (`given`/`family`/`ORCID`) and DataCite (`givenName`/`familyName`/`name` +
    `nameIdentifiers`) shapes, so the same transform serves either resolver. Anonymous inline objects
    don't round-trip through editors, so every author gets a real @id."""
    refs = []
    for a in (v or []):
        if not isinstance(a, dict):
            continue
        given = a.get("given") or a.get("givenName")
        family = a.get("family") or a.get("familyName")
        nm = " ".join(x for x in (given, family) if x) or a.get("name")
        if not nm:
            continue
        pid = a.get("ORCID") or _datacite_orcid(a) or ("#author-" + _slug(nm))
        if not any(e.get("@id") == pid for e in doc["@graph"]):
            doc["@graph"].append({"@id": pid, "@type": "Person", "name": nm})
        refs.append({"@id": pid})
    return refs or None


def _t_id_ref(v, doc):
    if not v or v == "NOASSERTION":          # GitHub's "no SPDX license detected" — not a license
        return None
    return {"@id": v}


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


def _apply_map(entity, doc, data, mapping):
    """Apply a resolver's `map` (JMESPath paths + optional named transforms) to an entity, in place.
    `@type` is REFINED (append, never overwrite) so a resolver can add a discovered type
    (SoftwareSourceCode…) without clobbering; other props are GAP-FILL (never overwrite authored).
    Pure (no network) — the shared core of the primary resolver AND any fallback, and unit-testable
    on a sample response. Returns True if anything was applied."""
    applied = False
    for prop, spec in (mapping or {}).items():
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
    return applied


def _enrich_entity(entity, doc, enrich_cfg):
    """Resolve one entity in place. Tries the resolver; if it yields nothing AND the resolver declares
    a `fallback` (e.g. publication: Crossref → DataCite for preprint/dataset DOIs), tries that too.
    Returns the resolver kind if anything was applied, else None."""
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
    applied = _apply_map(entity, doc, data, cfg.get("map")) if data else False

    fb = cfg.get("fallback")
    if not applied and fb and fb.get("url"):
        fb_pid = (_pid(eid, fb) or pid) if fb.get("id_pattern") else pid
        fdata = _get_json(fb["url"].format(id=fb_pid), fb.get("accept", "application/json"))
        if fdata:
            applied = _apply_map(entity, doc, fdata, fb.get("map"))
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
