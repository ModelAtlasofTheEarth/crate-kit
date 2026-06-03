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


def load_root_metadata(repo_dir, reverse_engineer=False):
    """Return (root_props, source_label)."""
    repo_dir = Path(repo_dir)

    mate_yml = repo_dir / ".mate" / "metadata.yml"
    if mate_yml.exists():
        data = yaml.safe_load(mate_yml.read_text()) or {}
        data = data.get("mate", data) if isinstance(data, dict) else {}
        if data:
            return _root_from_authored(data), str(mate_yml.relative_to(repo_dir))

    fm = _load_yaml_frontmatter(repo_dir / "README.md")
    if fm:
        return _root_from_authored(fm), "README.md front-matter"

    if reverse_engineer:
        issue_dict = repo_dir / ".metadata_trail" / "issue_dict.json"
        if issue_dict.exists():
            data = json.loads(issue_dict.read_text())
            return _root_from_issue_dict(data), ".metadata_trail/issue_dict.json (old engine)"

    return {}, "none (no authored metadata found)"


def _root_from_authored(data):
    props = {}
    for k, target in ROOT_SCALARS.items():
        if data.get(k):
            props[target] = data[k]
    if data.get("keywords"):
        props["keywords"] = data["keywords"]
    if data.get("license"):
        props["license"] = {"@id": str(data["license"])}
    creators = data.get("creators") or []
    if creators:
        props["creator"] = [_person(c) for c in creators]
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


def _root_entity(doc):
    return next((e for e in doc.get("@graph", []) if e.get("@id") == "./"), {})


def _is_derived_entity(e):
    """Derived = regenerated from the filesystem/payload each build (so replace, don't preserve).
    Authored/enriched entities (Person, SoftwareApplication, ScholarlyArticle, …) are preserved."""
    if e.get("@id") == "./":
        return False
    if e.get("@type") == "File":
        return True
    # NB: ExternalPayload is preserved (authored, set once via front-matter/issue/CLI), not derived.
    i = e.get("@id", "")
    return isinstance(i, str) and i.endswith("/")          # a local directory Dataset


def _merge(existing, fresh):
    """Preserve authored + enriched (from `existing`); refresh the derived layer (from `fresh`).
    This is what makes the crate the editable single source of truth: a rebuild never clobbers
    a human-set or enriched value, only the file manifest / git provenance / payload."""
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

    graph, seen = [root], {"./"}
    for e in fresh.get("@graph", []):                      # fresh derived entities win
        if e.get("@id") not in seen and _is_derived_entity(e):
            graph.append(e); seen.add(e.get("@id"))
    for src in (existing, fresh):                          # preserved entities: existing first
        for e in src.get("@graph", []):
            i = e.get("@id")
            if i not in seen and not _is_derived_entity(e):
                graph.append(e); seen.add(i)

    out = dict(existing)
    out["@graph"] = graph
    if "@context" not in out and "@context" in fresh:
        out["@context"] = fresh["@context"]
    return out


def _build_fresh(repo_dir, reverse_engineer, git_opts, seed_authored=True):
    crate = ROCrate()

    # 1) authored root metadata. People become proper Person entities referenced by @id.
    # seed_authored=False skips the front-matter seed entirely — used by `from-issue`, where
    # the issue (not front-matter) is the authored source.
    root_props, source = ({}, "none (authored externally)")
    if seed_authored:
        root_props, source = load_root_metadata(repo_dir, reverse_engineer)
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

    # 4) external data payloads (NCI/Zenodo/…) as remote entities (front-matter seed only)
    payload_ids = add_payload(doc, repo_dir) if seed_authored else []

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


def build_crate(repo_dir, out_path=None, reverse_engineer=False, merge=True,
                seed_authored=True, git_opts=None):
    """Build (or update) a repo's crate.

    merge=True (default): if a crate already exists, preserve its authored/enriched content and
    only refresh the derived layer. reverse_engineer forces a clean migration (no merge), since
    it is seeding from an old-engine source. seed_authored=False skips the front-matter seed
    (used by `from-issue`).
    """
    repo_dir = Path(repo_dir).resolve()
    fresh, summary = _build_fresh(repo_dir, reverse_engineer, git_opts, seed_authored=seed_authored)

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

    summary["graph_size"] = len(doc["@graph"])
    if out_path:
        Path(out_path).write_text(json.dumps(doc, indent=2))
    return doc, summary
