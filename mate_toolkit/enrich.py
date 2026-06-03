"""enrich: resolve the PIDs already in the crate (ORCID, publication DOI) and fold in a
profile-bounded subset of what comes back. PID-first and BEST-EFFORT — a network failure or a
missing field is skipped, never fatal. Resolved values are written into the crate and, being
non-derived, are preserved across rebuilds (build-as-merge). Raw responses are not persisted
here (kept minimal for MVP).

What to keep from each response is the profile's `enrich.<kind>.keep` list — config, not code.
"""
import json
import re
import urllib.request
from pathlib import Path

from .build_crate import _root_entity
from .profile import load_profile


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "person").lower()).strip("-")


def _get_json(url, accept="application/json"):
    try:
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "mate-toolkit"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _enrich_person(entity, keep):
    oid = entity.get("@id", "")
    if "orcid.org" not in oid:
        return False
    orcid = oid.rstrip("/").split("/")[-1]
    data = _get_json(f"https://pub.orcid.org/v3.0/{orcid}/person")
    if not data:
        return False
    name = data.get("name") or {}
    given = (name.get("given-names") or {}).get("value")
    family = (name.get("family-name") or {}).get("value")
    changed = False
    if "givenName" in keep and given and not entity.get("givenName"):
        entity["givenName"] = given; changed = True
    if "familyName" in keep and family and not entity.get("familyName"):
        entity["familyName"] = family; changed = True
    full = " ".join(x for x in (given, family) if x)
    if full and not entity.get("name"):
        entity["name"] = full; changed = True
    return changed


def _doi_of(ref):
    cid = ref.get("@id", "") if isinstance(ref, dict) else ""
    if "doi.org/" in cid:
        return cid.split("doi.org/")[-1]
    if cid.startswith("10."):
        return cid
    return None


def _enrich_publication(doc, root, keep):
    cit = root.get("citation")
    doi = _doi_of(cit)
    if not doi:
        return False
    cid = cit["@id"]
    if any(e.get("@id") == cid and e.get("@type") == "ScholarlyArticle" for e in doc["@graph"]):
        return False  # already resolved
    data = _get_json(f"https://api.crossref.org/works/{doi}")
    if not data:
        return False
    m = data.get("message", {})
    ent = {"@id": cid, "@type": "ScholarlyArticle"}
    if "name" in keep and m.get("title"):
        ent["name"] = m["title"][0]
    if "datePublished" in keep:
        parts = (m.get("published") or {}).get("date-parts") or []
        if parts and parts[0]:
            ent["datePublished"] = "-".join(str(x) for x in parts[0])
    if "url" in keep and m.get("URL"):
        ent["url"] = m["URL"]
    if "author" in keep and m.get("author"):
        refs = []
        for a in m["author"]:
            nm = " ".join(x for x in (a.get("given"), a.get("family")) if x)
            if not nm:
                continue
            # give every author a real @id (ORCID if present, else a blank node) — anonymous
            # inline objects don't round-trip safely through editors like Crate-O.
            pid = a.get("ORCID") or ("#author-" + _slug(nm))
            if not any(e.get("@id") == pid for e in doc["@graph"]):
                doc["@graph"].append({"@id": pid, "@type": "Person", "name": nm})
            refs.append({"@id": pid})
        if refs:
            ent["author"] = refs
    doc["@graph"].append(ent)
    return True


def enrich(repo_dir, out_path=None):
    repo_dir = Path(repo_dir).resolve()
    crate_path = repo_dir / "ro-crate-metadata.json"
    if not crate_path.exists():
        return {"error": "no crate to enrich (run build first)"}

    keep = (load_profile(repo_dir).get("enrich", {}) or {})
    person_keep = set((keep.get("author") or {}).get("keep", []))
    pub_keep = set((keep.get("publication") or {}).get("keep", []))

    doc = json.loads(crate_path.read_text())
    root = _root_entity(doc)
    resolved = []

    for e in list(doc["@graph"]):
        if e.get("@type") == "Person" and not e.get("name"):
            if _enrich_person(e, person_keep):
                resolved.append(e.get("@id"))
    if _enrich_publication(doc, root, pub_keep):
        resolved.append(_doi_of(root.get("citation")))

    Path(out_path or crate_path).write_text(json.dumps(doc, indent=2))
    return {"resolved": resolved}
