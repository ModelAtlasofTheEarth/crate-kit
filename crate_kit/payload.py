"""link-payload: represent data that does NOT live in the repo as an RO-Crate external entity.

The motivating case: output data too big for git lives on an archive (NCI/Zenodo). The crate
points at it (the external/remote-entity feature that motivated RO-Crate) instead of holding it.

Driven by a `payload:` block in the authored `mate:` metadata, e.g.:
    payload: { backend: zenodo, record: "99999" }
    payload: { backend: nci, url: "https://thredds.nci.org.au/.../catalog.html", path: model_output_data/ }
    payload: { backend: url, href: "https://example.org/data.zip", name: "Outputs" }

Backends are small adapters — new ones (figshare, S3, …) plug in here. This runs as a
post-processing step on the generated crate dict, so it stays independent of rocrate internals.
"""
from .meta import read_mate_block


def _adapter(p, default_backs=None):
    """Return (entity_dict, backing_path, backend) for a payload block."""
    backend = (p.get("backend") or "url").lower()

    if backend == "zenodo":
        rec = str(p.get("record", "")).strip()
        url = p.get("url") or (f"https://zenodo.org/records/{rec}" if rec else "")
        name = p.get("name") or (f"Zenodo record {rec}" if rec else "Zenodo dataset")
    elif backend == "nci":
        url = p.get("url") or p.get("thredds") or p.get("path") or ""
        name = p.get("name") or "NCI/Gadi dataset"
    else:  # generic url
        backend = "url"
        url = p.get("href") or p.get("url") or ""
        name = p.get("name") or url

    entity = {
        "@id": url,
        "@type": "Dataset",
        "additionalType": "ExternalPayload",
        "name": name,
        "description": f"External data payload (backend: {backend}). Not stored in this repository.",
    }
    if p.get("size"):
        entity["contentSize"] = str(p["size"])
    if p.get("license"):
        entity["license"] = {"@id": str(p["license"])}

    # which local directory entity this payload backs — a profile knob (e.g. the geoscience
    # profile's `payload.default_backs`), NOT an engine default. None → link from the root only.
    backing = p.get("backs") or p.get("path") or default_backs
    return entity, backing, backend


def add_payload(doc, repo_dir, profile=None):
    """Append external-payload entities to the crate graph and link them. Returns added @ids."""
    block = read_mate_block(repo_dir)
    payloads = block.get("payload") if block else None
    if not payloads:
        return []
    if isinstance(payloads, dict):
        payloads = [payloads]

    default_backs = ((profile or {}).get("payload") or {}).get("default_backs")
    added = []
    graph = doc["@graph"]
    by_id = {e.get("@id"): e for e in graph}

    for p in payloads:
        entity, backing, _ = _adapter(p, default_backs)
        if not entity["@id"]:
            continue
        if backing:
            entity["about"] = {"@id": backing}
        graph.append(entity)
        added.append(entity["@id"])

        # link from the backing directory entity (distribution) and the root (hasPart)
        for target_id in (backing, "./"):
            tgt = by_id.get(target_id)
            if not tgt:
                continue
            key = "distribution" if target_id == backing else "hasPart"
            cur = tgt.get(key, [])
            if isinstance(cur, dict):
                cur = [cur]
            cur.append({"@id": entity["@id"]})
            tgt[key] = cur

    return added
