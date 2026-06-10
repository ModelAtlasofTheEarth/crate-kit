"""add a contextual entity ("remote" thing) to the crate BY REFERENCE.

People, publications, software, funders, remote data payloads — none of them live in the repo;
each is added by a PID/URL, linked to the root via the property the profile's `contextual:` block
declares, and later fleshed out by `enrich`. This is form 3 of the entity-role taxonomy
(TARGET_ARCHITECTURE.md §18) and the CLI's `mate add`.

  mate add creator 0000-0002-1825-0097
  mate add publication 10.1038/s41586-020-2649-2
  mate add remote_data https://zenodo.org/records/123 --name "Model outputs"
"""
import json
import re
import shlex
from pathlib import Path

from .build_crate import build_crate
from .payload import _adapter
from .profile import load_profile

ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
ROR_RE = re.compile(r"^0[a-z0-9]{8}$")
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>?#]+")   # a DOI, even embedded in a publisher URL


def _normalize_id(ref):
    """Turn a typed reference into a canonical @id URL (so it round-trips, dedupes, and enrich can
    match). Crucially, a DOI is canonicalised to `https://doi.org/<doi>` EVEN when pasted as a
    publisher URL (e.g. essopenarchive.org/doi/full/10.x/…) — so the same paper added two ways gets
    one @id and dedup works. Non-DOI URLs (a GitHub repo, a homepage) pass through unchanged."""
    r = (ref or "").strip()
    m = DOI_RE.search(r)
    if m:
        return "https://doi.org/" + m.group(0).rstrip(".")
    if r.startswith("http"):
        return r
    if ORCID_RE.match(r):
        return f"https://orcid.org/{r}"
    if ROR_RE.match(r):
        return f"https://ror.org/{r}"
    return r


def _detect_type(cdef, reference, eid):
    """Refine a contextual entity's @type from the profile's declarative `detect_type` rules
    (generic: the engine just matches; the rules are the profile's). First rule whose `match`
    appears in the reference/@id wins; otherwise the kind's default `type`."""
    for rule in (cdef.get("detect_type") or []):
        m = rule.get("match")
        if m and (m in (reference or "") or m in (eid or "")):
            return rule.get("type", cdef.get("type"))
    return cdef.get("type")


def _link_root(root, prop, ref_obj):
    """Append a reference onto a root property, normalising to a list and de-duping."""
    cur = root.get(prop)
    items = cur if isinstance(cur, list) else ([cur] if cur else [])
    if not any(isinstance(x, dict) and x.get("@id") == ref_obj["@id"] for x in items):
        items.append(ref_obj)
    root[prop] = items


def add_contextual(repo_dir, kind, reference, name=None):
    repo_dir = Path(repo_dir).resolve()
    profile = load_profile(repo_dir)
    cdef = (profile.get("contextual", {}) or {}).get(kind)
    if not cdef:
        kinds = ", ".join((profile.get("contextual", {}) or {}).keys())
        return {"error": f"unknown contextual kind '{kind}' (known: {kinds})"}
    if not (reference or "").strip():
        return {"error": "a reference (DOI / ORCID / ROR / URL) is required"}

    crate_path = repo_dir / "ro-crate-metadata.json"
    build_crate(repo_dir, out_path=str(crate_path), merge=True)   # ensure the root exists
    doc = json.loads(crate_path.read_text())
    root = next(e for e in doc["@graph"] if e.get("@id") == "./")
    by_id = {e.get("@id"): e for e in doc["@graph"]}

    if kind == "remote_data":
        # reuse the payload adapter: builds a Dataset + additionalType=ExternalPayload entity.
        # A bare number is a Zenodo record id; anything else is treated as a URL.
        r = reference.strip()
        spec = ({"backend": "zenodo", "record": r} if r.isdigit()
                else {"backend": "url", "url": r})
        if name:
            spec["name"] = name
        ent, _backing, _ = _adapter(spec)
        eid = ent["@id"]
        if eid not in by_id:
            doc["@graph"].append(ent)
    else:
        eid = _normalize_id(reference)
        ent = by_id.get(eid) or {"@id": eid, "@type": _detect_type(cdef, reference, eid)}
        if name:
            ent["name"] = name
        if eid not in by_id:
            doc["@graph"].append(ent)

    _link_root(root, cdef["link"], {"@id": eid})
    crate_path.write_text(json.dumps(doc, indent=2))
    return {"added": eid, "kind": kind, "type": by_id.get(eid, ent).get("@type") if eid in by_id else ent.get("@type"),
            "link": cdef["link"], "enrich": cdef.get("enrich"),
            "command": command_for_contextual(kind, reference, name)}


def command_for_contextual(kind, reference, name=None):
    parts = ["crate", "add", kind, shlex.quote(reference)]
    if name:
        parts += ["--name", shlex.quote(name)]
    return " ".join(parts)
