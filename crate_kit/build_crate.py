"""build-crate: the filesystem-pure spine of the toolkit.

Walks a repository directory and emits an RO-Crate (`ro-crate-metadata.json`) that
*mirrors what is physically there*, with authored root metadata layered on top and
optional git provenance folded in. No network. Works on any directory (git or not).

Authored root metadata is read, in priority order, from:
  1. `.mate/metadata.yml`            (a `mate:` mapping, or top-level keys)
  2. README.md front-matter          (YAML between the first pair of `---` lines)
  3. (prototype convenience) `--reverse-engineer` reads `.metadata_trail/issue_dict.json`
     produced by the OLD engine, so the back-catalogue can be migrated and diffed.
"""
import json
import os
import re
from pathlib import Path

import yaml

from rocrate.rocrate import ROCrate
from rocrate.model.file import File
from rocrate.model.dataset import Dataset
from rocrate.model.person import Person

from .gitprov import git_provenance
from .ignore import IgnorePolicy
from .payload import add_payload

# map of simple authored keys -> schema.org root properties
ROOT_SCALARS = {
    "title": "name",
    "description": "description",
    "abstract": "abstract",
    "status": "creativeWorkStatus",
}


def _load_yaml_frontmatter(readme_path):
    if not readme_path.exists():
        return {}
    text = readme_path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    return data.get("mate", data) if isinstance(data, dict) else {}


def load_root_metadata(repo_dir, reverse_engineer=False, profile=None):
    """Return (root_props, source_label). `profile` drives which authored keys map to which
    schema.org properties — so the engine carries no domain-specific field list."""
    repo_dir = Path(repo_dir)

    mate_yml = repo_dir / ".mate" / "metadata.yml"
    if mate_yml.exists():
        data = yaml.safe_load(mate_yml.read_text()) or {}
        data = data.get("mate", data) if isinstance(data, dict) else {}
        if data:
            return _root_from_authored(data, profile), str(mate_yml.relative_to(repo_dir))

    fm = _load_yaml_frontmatter(repo_dir / "README.md")
    if fm:
        return _root_from_authored(fm, profile), "README.md front-matter"

    if reverse_engineer:
        issue_dict = repo_dir / ".metadata_trail" / "issue_dict.json"
        if issue_dict.exists():
            data = json.loads(issue_dict.read_text())
            return _root_from_issue_dict(data), ".metadata_trail/issue_dict.json (old engine)"

    return {}, "none (no authored metadata found)"


def _root_from_authored(data, profile=None):
    """Map authored keys -> schema.org root properties, DRIVEN BY THE PROFILE.

    Each `root.fields` entry declares `property` (target) and `input` (shape). The engine
    knows no field names itself, so a profile from any discipline maps its own fields.
    Shapes: people -> minted Person refs; license -> {@id}; list/many -> list; else scalar.
    """
    if profile is None:
        from .profile import load_profile
        profile = load_profile()
    fields = (profile.get("root", {}) or {}).get("fields", {}) or {}
    props = {}
    for key, fdef in fields.items():
        val = data.get(key)
        if val in (None, "", [], {}):
            continue
        prop = fdef.get("property", key)
        inp = fdef.get("input")
        if inp == "people":
            props[prop] = [_person(c) for c in (val if isinstance(val, list) else [val])]
        elif prop == "license":
            props[prop] = {"@id": str(val)}
        elif inp == "list" or fdef.get("many"):
            props[prop] = val if isinstance(val, list) else [val]
        else:
            props[prop] = val
    return props


def _root_from_issue_dict(d):
    """Best-effort lift of the OLD engine's parsed issue dict into root properties."""
    props = {}
    if d.get("title"):
        props["name"] = d["title"]
    if d.get("description"):
        props["description"] = d["description"]
    if d.get("abstract"):
        props["abstract"] = d["abstract"]
    if d.get("scientific_keywords"):
        props["keywords"] = d["scientific_keywords"]
    lic = (d.get("license") or {})
    if lic.get("url"):
        props["license"] = {"@id": lic["url"]}
    creators = d.get("creators") or []
    refs = []
    for c in creators:
        ref = {}
        if c.get("@id"):
            ref["@id"] = c["@id"]
        nm = f"{c.get('givenName','')} {c.get('familyName','')}".strip()
        if nm:
            ref["name"] = nm
        if ref:
            refs.append(ref)
    if refs:
        props["creator"] = refs
    return props


def _person(c):
    """Turn an authored creator (ORCID string or 'Family, Given') into a spec dict.

    Returns {"@id": orcid} for ORCID-shaped input, else {"name": "..."}.
    """
    s = str(c).strip()
    if "orcid.org" in s or (s.replace("-", "").isalnum() and s.count("-") == 3):
        oid = s if s.startswith("http") else f"https://orcid.org/{s}"
        return {"@id": oid}
    return {"name": s}


def _add_people(crate, specs):
    """Add Person entities to the crate and return @id references for the root's creator list.

    ORCID specs use the ORCID URL as @id; name-only specs get a stable slug @id so they are
    real graph entities (rocrate requires creators to be referenced entities, not bare dicts).
    """
    refs = []
    for spec in specs:
        pid = spec.get("@id")
        props = {}
        if spec.get("name"):
            props["name"] = spec["name"]
        if not pid:
            slug = re.sub(r"[^a-z0-9]+", "-", (spec.get("name") or "person").lower()).strip("-")
            pid = f"#person-{slug}"
        crate.add(Person(crate, pid, properties=props))
        refs.append({"@id": pid})
    return refs


def _walk(repo_dir, policy):
    """Yield (relpath, is_dir), applying the layered ignore policy and pruning ignored dirs."""
    repo_dir = Path(repo_dir)
    for root, dirs, files in os.walk(repo_dir):
        rel_root = Path(root).relative_to(repo_dir)
        kept_dirs = []
        for d in sorted(dirs):
            rel = (rel_root / d).as_posix() + "/"
            if policy.skip_dir(d, rel):
                continue
            kept_dirs.append(d)
            yield rel, True
        dirs[:] = kept_dirs  # prune so os.walk does not descend into ignored dirs
        for f in sorted(files):
            rel = (rel_root / f).as_posix()
            if policy.skip_file(f, rel):
                continue
            yield rel, False


# Root properties that are DERIVED (refreshed every build); everything else on the root is
# authored/enriched and preserved across builds.
DERIVED_ROOT_FIELDS = {"version", "dateCreated", "dateModified", "codeRepository",
                       "hasPart", "distribution"}

# File-entity properties DERIVED from the filesystem (refreshed every build); everything else
# on a File is authored (mate describe / Crate-O) and must survive rebuilds.
DERIVED_FILE_FIELDS = {"contentSize"}


def _root_entity(doc):
    return next((e for e in doc.get("@graph", []) if e.get("@id") == "./"), {})


def _merge(existing, fresh):
    """Preserve authored + enriched (from `existing`); refresh the derived layer (from `fresh`).
    This is what makes the crate the editable single source of truth: a rebuild never clobbers
    a human-set or enriched value, only the file manifest / git provenance / payload.

    Entity handling:
    - root ("./"): merge — refresh DERIVED_ROOT_FIELDS, preserve everything else authored.
    - File: existence follows the fresh build; DERIVED_FILE_FIELDS (contentSize) are refreshed
      from it, but authored properties (description, additionalType, author, …) are preserved.
    - directory Dataset (@id ends "/"): existence follows the fresh build, but the EXISTING
      entity is kept so an authored description (`mate describe`) / type survives rebuilds.
    - everything else (Person, ScholarlyArticle, ExternalPayload, descriptor): preserved.
    Existing files/dirs absent from the fresh build are dropped (they no longer exist on disk).
    """
    eroot, froot = _root_entity(existing), _root_entity(fresh)
    root = dict(eroot)
    for k in DERIVED_ROOT_FIELDS:                          # refresh derived root fields
        if k in froot:
            root[k] = froot[k]
        else:
            root.pop(k, None)
    for k, v in froot.items():                             # first-seed: fill authored gaps only
        if k not in DERIVED_ROOT_FIELDS and k not in root:
            root[k] = v

    existing_by_id = {e.get("@id"): e for e in existing.get("@graph", [])}
    graph, seen = [root], {"./"}

    # filesystem-tracked entities: existence follows `fresh`
    for e in fresh.get("@graph", []):
        i = e.get("@id")
        if i in seen:
            continue
        if e.get("@type") == "File":
            ex = existing_by_id.get(i)
            if ex:
                merged = dict(ex)                          # keep authored props (+ any richer @type)
                for k in DERIVED_FILE_FIELDS:              # refresh only the derived bits
                    if k in e:
                        merged[k] = e[k]
                    else:
                        merged.pop(k, None)
                graph.append(merged)
            else:
                graph.append(e)                            # new file: take fresh
            seen.add(i)
        elif isinstance(i, str) and i.endswith("/"):
            graph.append(existing_by_id.get(i, e)); seen.add(i)   # dir: keep authored desc

    # non-filesystem entities: preserved (existing wins, fresh fills gaps)
    for src in (existing, fresh):
        for e in src.get("@graph", []):
            i = e.get("@id")
            if i in seen or e.get("@type") == "File" or (isinstance(i, str) and i.endswith("/")):
                continue
            graph.append(e); seen.add(i)

    # RO-Crate structural rule: every data entity must be linked from root.hasPart. hasPart is
    # derived (refreshed from the filesystem), so re-link ALL data entities incl. external
    # payloads here, or a rebuild orphans them (the bug ro-crate-py caught).
    root["hasPart"] = [{"@id": e["@id"]} for e in graph
                       if e.get("@id") != "./" and (
                           e.get("@type") == "File"
                           or e.get("additionalType") == "ExternalPayload"
                           or (isinstance(e.get("@id"), str) and e.get("@id").endswith("/")))]

    out = dict(existing)
    out["@graph"] = graph
    if "@context" not in out and "@context" in fresh:
        out["@context"] = fresh["@context"]
    return out


def _build_fresh(repo_dir, reverse_engineer, git_opts):
    crate = ROCrate()

    # 1) authored root metadata. The CRATE is the single authored source of truth: build never
    # reads a sidecar seed (no `.mate/metadata.yml`, no README front-matter). Authoring lands in
    # the crate via `mate seed`/`describe`, the issue form (`from-issue`), or Crate-O — and merge
    # preserves it. The ONE exception is migrating an OLD-engine repo (`--reverse-engineer`),
    # which lifts root metadata from its `.metadata_trail` issue dict.
    root_props, source = ({}, "none (crate is the authored source)")
    if reverse_engineer:
        from .profile import load_profile
        root_props, source = load_root_metadata(repo_dir, reverse_engineer=True,
                                                 profile=load_profile(repo_dir))
    for k, v in root_props.items():
        if k == "creator":
            v = _add_people(crate, v)
        crate.root_dataset[k] = v

    # 2) git provenance (references + summarises; never duplicates history)
    gitprops = git_provenance(repo_dir, git_opts)
    for k, v in gitprops.items():
        crate.root_dataset[k] = v

    # 3) data entities mirroring the actual filesystem (minus ignored paths)
    policy = IgnorePolicy(repo_dir)
    n_files = n_dirs = 0
    for rel, is_dir in _walk(repo_dir, policy):
        if is_dir:
            crate.add(Dataset(crate, dest_path=rel))
            n_dirs += 1
        else:
            size = (repo_dir / rel).stat().st_size
            crate.add(File(crate, dest_path=rel, properties={"contentSize": size}))
            n_files += 1

    doc = crate.metadata.generate()

    # 4) external data payloads (NCI/Zenodo/…) as remote entities. In normal operation these
    #    live IN the crate (added by the issue form / editor) and are merge-preserved; only the
    #    old-engine migration still lifts them from a front-matter seed.
    payload_ids = add_payload(doc, repo_dir) if reverse_engineer else []

    summary = {
        "repo": str(repo_dir),
        "root_metadata_source": source,
        "root_properties": sorted(root_props),
        "git_provenance": {k: v for k, v in gitprops.items() if not k.startswith("_")},
        "data_entities": {"files": n_files, "directories": n_dirs},
        "external_payloads": payload_ids,
        "ignored": {
            "source": policy.source,
            "count": len(policy.ignored),
            "payload_candidates": policy.payload_candidates,
        },
    }
    return doc, summary


def build_crate(repo_dir, out_path=None, reverse_engineer=False, merge=True, git_opts=None):
    """Build (or update) a repo's crate — a PURE projection of the filesystem + git, merged onto
    whatever is already authored in the crate.

    merge=True (default): if a crate already exists, preserve its authored/enriched content and
    only refresh the derived layer (manifest, git provenance). reverse_engineer forces a clean
    migration (no merge) from an old-engine source.
    """
    repo_dir = Path(repo_dir).resolve()
    fresh, summary = _build_fresh(repo_dir, reverse_engineer, git_opts)

    crate_path = repo_dir / "ro-crate-metadata.json"
    if merge and not reverse_engineer and crate_path.exists():
        try:
            doc = _merge(json.loads(crate_path.read_text()), fresh)
            summary["mode"] = "merge"
        except Exception as err:
            doc, summary["mode"] = fresh, f"rebuild (merge failed: {err})"
    else:
        doc = fresh
        summary["mode"] = ("migrate" if reverse_engineer
                           else "create" if not crate_path.exists() else "rebuild")

    # The crate's @context is the profile's PINNED context list (ro-crate + schema.org + any
    # discipline contexts, e.g. codemeta), so terms beyond schema.org resolve to real URIs. Generic:
    # the engine just applies whatever the profile declares; a discipline adds its own contexts.
    from .profile import load_profile
    prof = load_profile(repo_dir)
    ctx = prof.get("context") or None
    if ctx:
        doc["@context"] = ctx

    # Version stamp so the website can branch legacy↔new (Julian's redesign doc). Prototype keys
    # (underscore = provenance note, like _git_commit) — the resolver surfaces them in website.json.
    root = next((e for e in doc["@graph"] if e.get("@id") == "./"), None)
    if root is not None:
        root["_crate_profile"] = prof.get("profile") or "base"
        if prof.get("version") is not None:
            root["_crate_profile_version"] = prof.get("version")

    summary["graph_size"] = len(doc["@graph"])
    if out_path:
        Path(out_path).write_text(json.dumps(doc, indent=2))
    return doc, summary


def write_preview(repo_dir):
    """Write `ro-crate-preview.html` next to the crate (RO-Crate's standard preview convention) via
    ro-crate-py — a faithful, human-readable dump of the WHOLE crate graph, for authors/debugging.
    NOT the website page (that's the resolver's curated projection); deliberately plain. BEST-EFFORT:
    a nicety, never fatal. `crate.preview.write(dir)` emits ONLY the HTML (no data copy)."""
    try:
        from rocrate.rocrate import ROCrate
        ROCrate(str(repo_dir), gen_preview=True).preview.write(str(repo_dir))
        return True
    except Exception:
        return False
